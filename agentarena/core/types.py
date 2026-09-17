"""Shared types for AgentArena: sides, states, results, verdicts.

This module is intentionally dependency-free so every other component
(judge, agent, orchestrator, arena) can import it safely.
"""

from __future__ import annotations

import enum


class Side(str, enum.Enum):
    """A participant in a match."""

    ALPHA = "alpha"
    BRAVO = "bravo"

    @property
    def opponent(self) -> "Side":
        """Return the opposing side."""
        return Side.BRAVO if self is Side.ALPHA else Side.ALPHA

    @classmethod
    def parse(cls, value: str) -> "Side":
        """Parse a string into a Side, raising ValueError on unknown values."""
        try:
            return cls(value.strip().lower())
        except ValueError as exc:  # pragma: no cover - defensive
            raise ValueError(f"unknown side: {value!r}") from exc


class Verdict(str, enum.Enum):
    """The Judge's answer to a submission."""

    CORRECT = "correct"
    INCORRECT = "incorrect"


class MatchState(str, enum.Enum):
    """Lifecycle states of a match (see docs/MATCH_LIFECYCLE.md)."""

    CREATED = "created"
    PROVISIONING = "provisioning"
    FORTIFYING = "fortifying"
    BATTLING = "battling"
    FINISHED = "finished"
    ABORTED = "aborted"


class MatchResult(str, enum.Enum):
    """The outcome of a match."""

    ONGOING = "ongoing"
    ALPHA_WIN = "alpha_win"
    BRAVO_WIN = "bravo_win"
    DRAW = "draw"
    ABORTED = "aborted"


# Legal transitions of the match state machine. Anything not listed here is
# rejected by Match.transition_to.
ALLOWED_TRANSITIONS: dict[MatchState, frozenset[MatchState]] = {
    MatchState.CREATED: frozenset({MatchState.PROVISIONING, MatchState.ABORTED}),
    MatchState.PROVISIONING: frozenset({MatchState.FORTIFYING, MatchState.ABORTED}),
    MatchState.FORTIFYING: frozenset({MatchState.BATTLING, MatchState.ABORTED}),
    MatchState.BATTLING: frozenset({MatchState.FINISHED, MatchState.ABORTED}),
    MatchState.FINISHED: frozenset(),
    MatchState.ABORTED: frozenset(),
}

TERMINAL_STATES: frozenset[MatchState] = frozenset(
    {MatchState.FINISHED, MatchState.ABORTED}
)
