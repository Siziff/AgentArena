"""System prompts for the fortify and battle phases.

Prompts are templates rendered with the side's context and the match's
defense-strength policy, so agents know the rules they must play under.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.types import Side


@dataclass(frozen=True)
class DefensePolicy:
    """Configurable caps that keep defenses crackable (see docs/RULES.md)."""

    max_password_length: int = 12
    max_password_alphabet: str = "lowercase letters and digits"
    max_kdf_iterations: int = 1000
    allowed_cipher_suites: str = "openssl AES-256-CBC, zip/7z with password, gpg --symmetric"
    max_layers: int = 3

    def render(self) -> str:
        return (
            "Defense-strength policy (you MUST stay within these limits):\n"
            f"- Any single password/key: at most {self.max_password_length} characters, "
            f"using {self.max_password_alphabet}.\n"
            f"- Key-derivation work factor: at most {self.max_kdf_iterations} iterations.\n"
            f"- Allowed cipher tools: {self.allowed_cipher_suites}.\n"
            f"- At most {self.max_layers} nested encryption layers."
        )


_COMMON = (
    "You are an autonomous agent in AgentArena, side {side}. "
    "Your workspace is your private directory. You act ONLY through the provided tools. "
    "After every action, call the `comment` tool with a one-sentence English summary of "
    "what you are doing or just did (this narrates the match for spectators). "
    "Never reveal your own secret code in commentary or anywhere an opponent could read it."
)

FORTIFY_TEMPLATE = (
    _COMMON
    + """

PHASE: FORTIFICATION. A 128-character code (your treasure) is stored in your workspace at
`{treasure_name}`. You have limited time to protect it before the battle phase begins.

Rules:
- You may NOT change or delete the code's value, and it must remain obtainable on your
  side if an attacker defeats your defenses (no destroying it to force a draw).
- Otherwise you have maximum freedom: move/copy/wrap/encrypt/encode it, gate access behind
  passwords or a local service, set permissions, and plant decoy 128-character files.

{defense_policy}

Goal: make your code as hard to discover as possible WITHIN the policy. Prefer layered,
legal defenses plus convincing decoys. Work step by step and verify each defense works."""
)

BATTLE_TEMPLATE = (
    _COMMON
    + """

PHASE: BATTLE. Discover the opponent's 128-character code and submit it with `submit_code`
before they discover yours. You must ALSO keep your own defenses up.

- Opponent exposed locations/services: {opponent_hint}
- The Judge allows only {rate_limit} submissions per minute. Blind guessing is hopeless;
  crack, probe, and reason to narrow down real candidates.
- Distinguish decoys from the real code. Fix any weaknesses you notice in your own defenses.

When you have a confident candidate, submit it. If you get rate-limited (429), slow down."""
)


def fortify_prompt(side: Side, treasure_name: str, policy: DefensePolicy) -> str:
    return FORTIFY_TEMPLATE.format(
        side=side.value, treasure_name=treasure_name, defense_policy=policy.render()
    )


def battle_prompt(side: Side, opponent_hint: str, rate_limit: int) -> str:
    return BATTLE_TEMPLATE.format(
        side=side.value, opponent_hint=opponent_hint, rate_limit=rate_limit
    )
