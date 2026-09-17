"""The match state machine and timing.

Pure standard-library logic, fully unit-testable. The orchestrator drives a
Match through its lifecycle; the Judge latches the winner. See
docs/MATCH_LIFECYCLE.md for the state diagram.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from .types import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    MatchResult,
    MatchState,
    Side,
)


class IllegalTransitionError(RuntimeError):
    """Raised when an illegal match state transition is attempted."""


def _now() -> float:
    """Wall-clock timestamp for reporting."""
    return time.time()


def _monotonic() -> float:
    """Monotonic clock for measuring durations (immune to wall-clock changes)."""
    return time.monotonic()


@dataclass
class Match:
    """One AgentArena match between Alpha and Bravo."""

    fortify_seconds: int = 300
    max_match_seconds: int = 0  # 0/None => no cap
    rate_limit_per_minute: int = 10
    match_id: str = field(default_factory=lambda: f"m-{uuid.uuid4().hex[:12]}")

    state: MatchState = field(default=MatchState.CREATED, init=False)
    result: MatchResult = field(default=MatchResult.ONGOING, init=False)
    winner: Side | None = field(default=None, init=False)

    # Timestamps (wall clock) for reporting; monotonic markers for durations.
    created_at: float = field(default_factory=_now, init=False)
    started_at: float | None = field(default=None, init=False)
    finished_at: float | None = field(default=None, init=False)
    _start_mono: float | None = field(default=None, init=False, repr=False)
    _fortify_deadline_mono: float | None = field(default=None, init=False, repr=False)

    # ---- state transitions -------------------------------------------------

    def transition_to(self, new_state: MatchState) -> None:
        """Move to `new_state`, enforcing the legal-transition table."""
        if new_state not in ALLOWED_TRANSITIONS[self.state]:
            raise IllegalTransitionError(
                f"illegal transition: {self.state.value} -> {new_state.value}"
            )
        self.state = new_state
        if new_state is MatchState.FORTIFYING:
            self._mark_started()
        elif new_state in TERMINAL_STATES:
            self.finished_at = _now()

    # ---- lifecycle helpers -------------------------------------------------

    def _mark_started(self) -> None:
        self.started_at = _now()
        self._start_mono = _monotonic()
        self._fortify_deadline_mono = self._start_mono + self.fortify_seconds

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def elapsed_seconds(self) -> float:
        """Seconds since the match entered FORTIFYING (0 if not started)."""
        if self._start_mono is None:
            return 0.0
        return _monotonic() - self._start_mono

    def fortify_remaining_seconds(self) -> float:
        """Seconds left in the fortification window (0 once battle starts)."""
        if self._fortify_deadline_mono is None:
            return float(self.fortify_seconds)
        return max(0.0, self._fortify_deadline_mono - _monotonic())

    def battle_should_start(self) -> bool:
        """True when the fortification window has elapsed."""
        return (
            self.state is MatchState.FORTIFYING
            and self._fortify_deadline_mono is not None
            and _monotonic() >= self._fortify_deadline_mono
        )

    def time_cap_reached(self) -> bool:
        """True when the global match cap has elapsed (if a cap is set)."""
        if not self.max_match_seconds or self._start_mono is None:
            return False
        return self.elapsed_seconds() >= self.max_match_seconds

    # ---- outcome -----------------------------------------------------------

    def declare_winner(self, side: Side) -> None:
        """Latch a winner. Only valid from BATTLING and only once."""
        if self.is_terminal:
            raise IllegalTransitionError("match already finished")
        if self.state is not MatchState.BATTLING:
            raise IllegalTransitionError(
                f"cannot declare a winner from state {self.state.value}"
            )
        self.winner = side
        self.result = MatchResult.ALPHA_WIN if side is Side.ALPHA else MatchResult.BRAVO_WIN
        self.transition_to(MatchState.FINISHED)

    def declare_draw(self) -> None:
        """End the match as a draw (time cap reached, no winner)."""
        if self.state is MatchState.BATTLING:
            self.result = MatchResult.DRAW
            self.transition_to(MatchState.FINISHED)
        else:
            raise IllegalTransitionError(
                f"cannot declare a draw from state {self.state.value}"
            )

    def abort(self) -> None:
        """Abort the match from any non-terminal state."""
        if self.is_terminal:
            raise IllegalTransitionError("match already finished")
        self.result = MatchResult.ABORTED
        self.transition_to(MatchState.ABORTED)

    # ---- reporting ---------------------------------------------------------

    def summary(self) -> dict:
        return {
            "match_id": self.match_id,
            "state": self.state.value,
            "result": self.result.value,
            "winner": self.winner.value if self.winner else None,
            "fortify_seconds": self.fortify_seconds,
            "max_match_seconds": self.max_match_seconds,
            "rate_limit_per_minute": self.rate_limit_per_minute,
            "elapsed_seconds": round(self.elapsed_seconds(), 3),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }
