"""Client-side view of the Judge.

The agent's `submit_code` tool talks to the Judge through this interface.
Two implementations:

- `HttpJudgeClient`: the real thing — POSTs to the Judge's HTTP API.
- `InProcessJudgeClient`: calls a JudgeCore directly, for fast tests and the
  local in-process runner (no socket needed).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Protocol

from ..core.types import Side
from .core import JudgeCore, OutcomeStatus


class JudgeClient(Protocol):
    side: Side

    def submit(self, guess: str) -> dict[str, Any]:
        ...

    def status(self) -> dict[str, Any]:
        ...


class HttpJudgeClient:
    def __init__(self, base_url: str, side: Side, timeout_seconds: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.side = side
        self.timeout_seconds = timeout_seconds

    def submit(self, guess: str) -> dict[str, Any]:
        payload = json.dumps({"side": self.side.value, "guess": guess}).encode("utf-8")
        req = urllib.request.Request(
            url=f"{self.base_url}/submit",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                body["http_status"] = resp.status
                return body
        except urllib.error.HTTPError as exc:  # 4xx/5xx still carry JSON bodies
            try:
                body = json.loads(exc.read().decode("utf-8"))
            except Exception:
                body = {"error": "http_error", "message": str(exc)}
            body["http_status"] = exc.code
            return body

    def status(self) -> dict[str, Any]:
        with urllib.request.urlopen(
            f"{self.base_url}/status", timeout=self.timeout_seconds
        ) as resp:
            return json.loads(resp.read().decode("utf-8"))


class InProcessJudgeClient:
    def __init__(self, core: JudgeCore, side: Side) -> None:
        self.core = core
        self.side = side

    def submit(self, guess: str) -> dict[str, Any]:
        outcome = self.core.submit(self.side.value, guess)
        http = {
            OutcomeStatus.EVALUATED: 200,
            OutcomeStatus.INVALID: 400,
            OutcomeStatus.RATE_LIMITED: 429,
            OutcomeStatus.MATCH_FINISHED: 409,
        }[outcome.status]
        return {
            "http_status": http,
            "outcome": outcome.status.value,
            "verdict": outcome.verdict.value if outcome.verdict else None,
            "match_status": outcome.match_status,
            "winner": outcome.winner.value if outcome.winner else None,
            "remaining_in_window": outcome.remaining_in_window,
            "attempts_total": outcome.attempts_total,
            "detail": outcome.detail,
        }

    def status(self) -> dict[str, Any]:
        return self.core.status()
