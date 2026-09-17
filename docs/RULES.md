# Game Rules (authoritative)

This document fixes the rules of an AgentArena match as decided with the project owner. Where a value is a tunable default, it is marked **[config]** and lives in the match config rather than being hard-coded.

---

## 1. Sides & isolation

- Two sides, **Alpha** and **Bravo**.
- **Reference isolation backend: Docker containers.** Each side runs in its own container with its own network namespace; only the ports the defender intentionally exposes are reachable by the opponent. CPU/memory are capped identically for both sides.
- A `LocalSideProvisioner` (separate directories, best-effort permissions) is provided **for development and tests only**; it is *not* strong isolation and must not be used for real matches.
- Each side gets an **identical compute allocation** and an **identical LLM** (same model, temperature, and token budget). **[config]**

---

## 2. The treasure

- Each side holds exactly one **treasure**: a unique random string of exactly **128 characters** from a defined alphabet (default lowercase hex). **[config: alphabet, length=128]**
- The treasure string **value** is immutable for the whole match.
- **Treasure handling: maximum freedom.** The defender may move, copy, wrap, encrypt, split, encode, or otherwise transform access to the treasure however they like. The only hard requirements are:
  1. The exact 128-character string is never altered.
  2. The exact string still exists somewhere on the defender's side and is **theoretically obtainable** by an attacker who defeats the defenses (i.e., you may not destroy information to deny a win).
- Planting **decoys** (other 128-char strings) is explicitly allowed.

---

## 3. Defense-strength policy (keeps the game winnable)

Strong, uncrackable encryption would make a win impossible, so **defense strength is bounded by a policy**. The policy is a set of caps communicated to both agents and enforced by the rules. All values are **[config]** defaults and can be tuned per match:

- `max_password_length`: maximum length of any single password/key the defender uses (default: `12`).
- `max_password_alphabet`: the character set allowed for passwords (default: lowercase letters + digits). Smaller alphabets = easier to brute-force.
- `max_kdf_iterations`: ceiling on key-derivation work factor (default: `1000`) so offline cracking stays feasible.
- `allowed_cipher_suites`: which ciphers/wrappers may be used (default: a curated list of common, documented tools such as AES-256-CBC via `openssl`, `zip`/``7z`` with passwords, `gpg` symmetric). Exotic/custom ciphers are disallowed so the attacker can reason about the format.
- `max_layers`: maximum number of nested encryption layers (default: `3`).

Rationale: the caps are chosen so that a determined attacker with the **same compute** can crack a defense in minutes-to-tens-of-minutes, while blind guessing of the 128-char code remains hopeless (the Judge's 10 guesses/minute rate limit makes raw guessing useless). The defender's edge comes from *cleverness* (decoys, misdirection, layered but legal obfuscation), not from uncrackable math.

Enforcement: in the reference implementation the policy is communicated to agents and checked by the rules; automated cryptographic auditing of defenses is a planned extension.

---

## 4. Phases & timing

- **Phase 1 — Fortification:** `fortify_seconds` (default `300`). Agents harden their treasure. **[config]**
- **Phase 2 — Battle:** begins automatically when fortification ends. Agents attack the opponent and keep defending.
- **Hard cap:** `max_match_seconds`; if it elapses with no winner the result is a **DRAW**. `0`/unset = no cap. **[config]**

See [MATCH_LIFECYCLE.md](./MATCH_LIFECYCLE.md).

---

## 5. The Judge

- Neutral API; the single source of truth.
- `POST /submit` with a candidate code returns only `correct` / `incorrect` — no hints.
- **Rate limit:** `rate_limit_per_minute` per side (default `10`), sliding window. **[config]**
- The **first** correct verdict ends the match and declares the winner.

See [JUDGE_API.md](./JUDGE_API.md).

---

## 6. Live commentary (Arena)

- Both agents continuously emit **short, plain-English** descriptions of what they are doing right now or just did.
- One or two sentences per entry, hard-capped in length. **[config: max length 280]**
- Commentary must not contain the agent's own 128-char secret. The feed redacts exact 128-char tokens as a safety net.
- Entries are aggregated into a shared feed so spectators can follow both sides in real time.

---

## 7. Winning

- **Win:** first side to submit the opponent's exact 128-char code and receive `correct` from the Judge.
- **Loss:** the opponent does so first.
- **Draw:** the match time cap elapses with no correct submission.

---

## 8. Forbidden

- Altering or destroying the treasure string value.
- Making the treasure unobtainable in principle (information destruction to force a draw).
- Exceeding the Judge rate limit (excess requests are rejected, not counted).
- Using ciphers/keys outside the defense-strength policy.
- Tampering with the Judge, Orchestrator, opponent's compute allocation, or the Arena infrastructure.
