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
    "Write all commentary and reasoning strictly in ENGLISH. "
    "Never reveal your own secret code in commentary or anywhere an opponent could read it.\n\n"
    "GAME RULES (they bind you — read carefully):\n"
    "- The match has TWO phases. Phase 1 FORTIFICATION: you only DEFEND your own treasure(s); "
    "you have NO access to the opponent — all ports/paths to them are CLOSED. Phase 2 BATTLE: "
    "access to the opponent OPENS; you attack their treasures while still defending yours.\n"
    "- Each treasure is a 128-character code. Its value is IMMUTABLE: you may not change or "
    "delete it, and it must stay obtainable on your side (no destroying it to force a draw).\n"
    "- To WIN you must submit ALL of the OPPONENT's exact 128-char codes to the Judge "
    "(each correct submission steals one of their treasures). The Judge only "
    "answers 'correct'/'incorrect' and enforces a strict per-minute rate limit (see below). "
    "The FIRST side to steal every opponent treasure wins; if time runs out first, it is a draw.\n"
    "- FORBIDDEN: altering/destroying a treasure value, attacking before the battle phase, "
    "exceeding the Judge's rate limit, or tampering with the Judge / the opponent's compute."
)

FORTIFY_TEMPLATE = (
    _COMMON
    + """

PHASE: FORTIFICATION. Your side holds {treasure_count} 128-character code(s) (your
treasure(s)), stored in your workspace at: {treasure_files}.
You have limited time to protect them before the battle phase begins.

Rules:
- You may NOT change or delete any code's value, and each must remain obtainable on your
  side if an attacker defeats your defenses (no destroying it to force a draw).
- Otherwise you have maximum freedom: move/copy/wrap/encrypt/encode them, gate access behind
  passwords or a local service, set permissions, and plant decoy 128-character files.
- Protect EVERY treasure — the opponent wins by stealing ALL of them, so your
  weakest-protected code is what decides the match.

{defense_policy}

A good fortify plan (do it ONCE, well — do not over-engineer):
1. Encrypt each treasure ONCE with a policy-compliant password.
2. Plant 2-3 convincing decoy 128-character files.
3. Restrict permissions (chmod 600) on the key and treasure files.
Then STOP. Done is better than more: do NOT repeatedly re-encrypt or create many files."""
)

BATTLE_TEMPLATE = (
    _COMMON
    + """

PHASE: BATTLE. The opponent hides {treasure_count} 128-character code(s). Steal ALL of
them: discover each code and submit it with `submit_code` before the opponent steals
all of yours. You must ALSO keep your own defenses up.

- Opponent exposed locations/services: {opponent_hint}
- The Judge allows only {rate_limit} submissions per minute. Blind guessing is hopeless.
- Each correct submission steals ONE treasure; the Judge's detail tells you how many
  remain (e.g. "stole a treasure ... (1/3)"). An already-stolen code is wasted budget.

A good attack plan (follow it — do not wander off-task):
1. List the opponent's files ONCE. Pick the single most likely real treasure
   (ignore decoys: wrong length, random names, README hints).
2. Find its weakest protection (short password, small PIN, weak archive) and crack THAT
   with a quick dictionary or PIN loop. Do not waste time on anything else.
3. When you extract a 128-char candidate, check its format, then submit ONCE.
4. Move on to the next treasure until you have stolen them all.
- If the Judge replies 429 (rate limit), STOP submitting for ~60s and keep cracking —
  do NOT just wait idly and do NOT re-submit right away.
- NEVER create dummy files or re-encrypt your own findings; that never reveals the code."""
)


def fortify_prompt(side: Side, treasure_names: list[str], policy: DefensePolicy) -> str:
    files = ", ".join(f"`{name}`" for name in treasure_names)
    return FORTIFY_TEMPLATE.format(
        side=side.value,
        treasure_count=len(treasure_names),
        treasure_files=files,
        defense_policy=policy.render(),
    )


def battle_prompt(
    side: Side, opponent_hint: str, rate_limit: int, treasure_count: int = 1
) -> str:
    return BATTLE_TEMPLATE.format(
        side=side.value,
        opponent_hint=opponent_hint,
        rate_limit=rate_limit,
        treasure_count=treasure_count,
    )
