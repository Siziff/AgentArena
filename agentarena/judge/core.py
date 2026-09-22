"""JudgeCore: verification, rate limiting, and winner latching.

Pure standard-library logic, independent of any web framework, so it is easy
to unit-test. The FastAPI layer (judge/app.py) is a thin HTTP wrapper that
maps SubmitOutcome statuses to HTTP status codes.

Design notes:
- The Judge stores only SHA-256 digests of treasures, never plaintext.
- A guess is checked against the *opponent's* treasure.
- The first correct verdict latches the winner; later submissions are refused.
- Submissions are serialized with a lock so "first correct wins" is
  deterministic even under concurrent requests.
"""

from __future__ import annotations

import enum
import threading
import time
from collections import deque
from dataclasses import dataclass, field

from ..core.treasure import (
    DEFAULT_ALPHABET,
    DEFAULT_LENGTH,
    digests_equal,
    treasure_digest,
    validate_treasure,
)
from ..core.types import Side, Verdict
from .ratelimit import SlidingWindowRateLimiter


class OutcomeStatus(str, enum.Enum):
    """Why a submission returned what it did (mapped to HTTP by the app)."""

    EVALUATED = "evaluated"  # guess was checked -> verdict present (HTTP 200)
    INVALID = "invalid"  # bad side / malformed guess (HTTP 400)
    RATE_LIMITED = "rate_limited"  # over the per-side limit (HTTP 429)
    MATCH_FINISHED = "match_finished"  # a winner is already latched (HTTP 409)


@dataclass(frozen=True)
class SubmitOutcome:
    status: OutcomeStatus
    verdict: Verdict | None
    match_status: str  # "ongoing" | "finished"
    winner: Side | None
    remaining_in_window: int
    retry_after_seconds: float
    attempts_total: int
    detail: str


@dataclass
class JudgeCore:
    rate_limit_per_minute: int = 10
    window_seconds: float = 60.0
    treasure_length: int = DEFAULT_LENGTH
    alphabet: str = DEFAULT_ALPHABET
    match_id: str = "match"
    clock: object = None  # optional injectable monotonic clock for tests

    _digests: dict[Side, str] = field(default_factory=dict, init=False)
    _attempts: dict[Side, int] = field(
        default_factory=lambda: {Side.ALPHA: 0, Side.BRAVO: 0}, init=False
    )
    _winner: Side | None = field(default=None, init=False)
    _finished: bool = field(default=False, init=False)
    _limiter: SlidingWindowRateLimiter = field(init=False)
    _clock: object = field(default=None, init=False, repr=False)
    # (monotonic_ts, verdict) for every *evaluated* submission, per side.
    _attempts_log: dict[Side, deque] = field(
        default_factory=lambda: {Side.ALPHA: deque(), Side.BRAVO: deque()}, init=False
    )
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self._clock = self.clock if self.clock is not None else time.monotonic
        self._limiter = SlidingWindowRateLimiter(
            self.rate_limit_per_minute, self.window_seconds, clock=self._clock
        )

    # ---- setup -------------------------------------------------------------

    def register_secret(self, side: Side, treasure: str) -> None:
        """Register a side's treasure (stored as a digest)."""
        if not validate_treasure(treasure, self.treasure_length, self.alphabet):
            raise ValueError("treasure does not match the expected format")
        with self._lock:
            self._digests[side] = treasure_digest(treasure)

    def register_digest(self, side: Side, digest: str) -> None:
        """Register a side's treasure directly by digest (no plaintext)."""
        with self._lock:
            self._digests[side] = digest

    # ---- submissions -------------------------------------------------------

    def submit(self, side_raw: str, guess: str) -> SubmitOutcome:
        """Evaluate a candidate code submitted by `side_raw`."""
        try:
            side = Side.parse(side_raw)
        except ValueError:
            return SubmitOutcome(
                status=OutcomeStatus.INVALID,
                verdict=None,
                match_status="finished" if self._finished else "ongoing",
                winner=self._winner,
                remaining_in_window=0,
                retry_after_seconds=0.0,
                attempts_total=0,
                detail=f"unknown side: {side_raw!r}",
            )

        with self._lock:
            if self._finished:
                return self._make(
                    OutcomeStatus.MATCH_FINISHED, side=side,
                    detail="match already finished",
                )

            if not isinstance(guess, str) or not validate_treasure(
                guess, self.treasure_length, self.alphabet
            ):
                return self._make(
                    OutcomeStatus.INVALID, side=side,
                    detail=(
                        f"guess must be a {self.treasure_length}-char string "
                        "from the allowed alphabet"
                    ),
                )

            decision = self._limiter.allow(side.value)
            if not decision.allowed:
                return self._make(
                    OutcomeStatus.RATE_LIMITED, side=side,
                    retry_after=decision.retry_after_seconds,
                    detail=(
                        f"side {side.value} exceeded {self.rate_limit_per_minute} "
                        f"requests per {int(self.window_seconds)}s"
                    ),
                )

            # Passed validation and rate limiting: this counts as an attempt.
            self._attempts[side] += 1
            opponent = side.opponent
            known = self._digests.get(opponent)
            if known is None:
                return self._make(
                    OutcomeStatus.INVALID, side=side,
                    detail=f"no treasure registered for {opponent.value}",
                )

            if digests_equal(guess, known):
                self._finished = True
                self._winner = side
                self._attempts_log[side].append((self._clock(), Verdict.CORRECT))
                return self._make(
                    OutcomeStatus.EVALUATED, side=side, verdict=Verdict.CORRECT,
                    remaining=decision.remaining,
                    detail=(
                        f"side {side.value} submitted the correct code "
                        f"for {opponent.value}"
                    ),
                )

            self._attempts_log[side].append((self._clock(), Verdict.INCORRECT))
            return self._make(
                OutcomeStatus.EVALUATED, side=side, verdict=Verdict.INCORRECT,
                remaining=decision.remaining, detail="incorrect",
            )

    # ---- status ------------------------------------------------------------

    @property
    def winner(self) -> Side | None:
        return self._winner

    @property
    def is_finished(self) -> bool:
        return self._finished

    def status(self) -> dict:
        with self._lock:
            return {
                "match_id": self.match_id,
                "match_status": "finished" if self._finished else "ongoing",
                "winner": self._winner.value if self._winner else None,
                "attempts": {s.value: n for s, n in self._attempts.items()},
                "limits": {
                    "per_window": self.rate_limit_per_minute,
                    "window_seconds": self.window_seconds,
                },
                "window": {
                    s.value: self._window_status(s) for s in (Side.ALPHA, Side.BRAVO)
                },
            }

    def _window_status(self, side: Side) -> dict:
        """Per-side rate-limit window: count + ordered verdicts in the window."""
        cutoff = self._clock() - self.window_seconds
        verdicts = [v.value for ts, v in self._attempts_log[side] if ts >= cutoff]
        return {
            "used": len(verdicts),
            "limit": self.rate_limit_per_minute,
            "verdicts": verdicts,
        }

    # ---- helpers -----------------------------------------------------------

    def _make(
        self,
        status: OutcomeStatus,
        *,
        side: Side,
        verdict: Verdict | None = None,
        remaining: int = 0,
        retry_after: float = 0.0,
        detail: str = "",
    ) -> SubmitOutcome:
        # Caller holds the lock; safe to read shared state.
        return SubmitOutcome(
            status=status,
            verdict=verdict,
            match_status="finished" if self._finished else "ongoing",
            winner=self._winner,
            remaining_in_window=max(0, remaining),
            retry_after_seconds=round(retry_after, 3),
            attempts_total=self._attempts[side],
            detail=detail,
        )
