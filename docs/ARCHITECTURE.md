# AgentArena Architecture

This document describes the system design of AgentArena: components, their responsibilities, communication protocols, data model, and security assumptions.

---

## 1. High-level overview

AgentArena runs a single **match** between two autonomous LLM agents, **Alpha** and **Bravo**. A match is coordinated by an **Orchestrator**, verified by a **Judge**, and narrated through an **Arena Feed**. Each agent operates on its own **Side** (an isolated workspace + compute allocation) that stores one **Treasure**.

```
                 +-------------------- Arena Feed --------------------+
                 |  (commentary stream, human-readable, English)      |
                 +--------^----------------------------^--------------+
                          | commentary                 | commentary
        +-----------------+------+          +----------+-------------+
        |   Agent Runtime ALPHA  |          |   Agent Runtime BRAVO  |
        |  (LLM + tools + loop)  |          |  (LLM + tools + loop)  |
        +----+------------------+          +-------+----------------+
             | own workspace                       | own workspace
             v                                     v
        +---------+   attacks over network   +----------+
        | SIDE A  | <----------------------> |  SIDE B  |
        | treasure|                          | treasure |
        +---------+                          +----------+
             | submit guess                       | submit guess
             +---------------->+----------------+
                              |
                       +------v-------+
                       |    JUDGE     |  verify + rate-limit + verdict
                       +------^-------+
                              | register secrets / read result
                       +------+--------+
                       | ORCHESTRATOR |  provision, timers, lifecycle
                       +--------------+
```

---

## 2. Components

### 2.1 Orchestrator
Owns the match lifecycle. Responsibilities:

- Load and validate match configuration.
- Generate the two treasures (or accept pre-seeded ones).
- **Provision** both sides (workspace directories, permissions, resource limits).
- Register each treasure with the Judge (as a verification digest).
- Launch the two Agent Runtimes with identical resource budgets.
- Drive the **phase timers** (Fortification → Battle) and broadcast phase transitions to agents.
- Monitor the Judge for a terminal verdict; on match end, stop agents and write a **match report**.
- Expose the current match state for observability.

The Orchestrator is trusted infrastructure. Agents must not be able to tamper with it.

### 2.2 Judge
Neutral verification service. The single source of truth for "who won".

- Stores only a **digest** (SHA-256) of each side's treasure, never the plaintext.
- Accepts candidate codes via `POST /submit` and returns a boolean verdict.
- Enforces a **per-side sliding-window rate limit** (default 10 requests / 60 s).
- Compares digests in constant time (`hmac.compare_digest`).
- Latches the match result: the first `correct` verdict ends the match and is immutable afterward.
- Exposes `GET /status` for the orchestrator/agents and `GET /health`.

The Judge is intentionally dumb: no hints, no partial scores, no "close" feedback. See [JUDGE_API.md](./JUDGE_API.md).

### 2.3 Agent Runtime
Runs one agent. Wraps an LLM in a loop with a set of **tools** and a **phase-aware policy**.

- **LLMProvider**: pluggable interface to any chat-completions-style model (OpenAI-compatible HTTP, local model, or a scripted mock for tests). Ensures both agents can be given *identical* model + budget for fairness.
- **Tool registry**: the actions the agent may take (shell, file ops, HTTP requests, submit-to-judge, comment, etc.).
- **Phase loop**: a Fortify policy (defend) then a Battle policy (attack + keep defending). The loop assembles a prompt from system instructions + current phase + recent observations + tool results, calls the LLM, executes the returned tool calls, and repeats until the phase/match ends.
- **Commentary emitter**: after each step the agent writes a short English description of what it is doing to the Arena Feed.

Both agents run the *same* runtime code and the *same* model with the *same* compute budget; they differ only in their side, treasure, and (optionally) a persona/system-prompt seed.

### 2.4 Arena Feed
Collects and serves human-readable commentary from both agents.

- Append-only store of commentary entries `{timestamp, side, phase, text}`.
- Backed by a JSONL file (durable) and/or in-memory list (fast).
- Served over HTTP for a live view (`GET /feed`, optional `?side=`, `?since=`).
- A minimal HTML page polls the feed to render a running narrative.

### 2.5 Side
A participant's runtime environment.

- **Workspace directory**: where the treasure file lives and where the agent reads/writes.
- **Compute allocation**: CPU/memory (and model token budget) — identical for both sides.
- **Network identity**: host/port from which the agent's services are reachable by the opponent.

In the reference implementation a Side is a local directory + process. See [§6 Security](#6-security-model--isolation) for how to harden this with containers.

---

## 3. Data model

### Treasure
- A string of exactly **128 characters** from a defined alphabet (default: lowercase hex `[0-9a-f]`).
- Initially written as the content of a single file on the side, e.g. `<side_workspace>/treasure.txt`.
- The treasure string **value** is immutable for the whole match. **Handling is maximum-freedom:** the defender may move/copy/wrap/encrypt/split/encode it arbitrarily, provided the exact 128-char string still exists somewhere on their side and remains *theoretically obtainable* by an attacker who defeats the defenses. See [RULES.md](./RULES.md).
- The Judge stores `sha256(treasure)` for verification.

### Match
```
Match {
  id: str
  side_alpha: SideRef
  side_bravo: SideRef
  state: CREATED | PROVISIONING | FORTIFYING | BATTLING | FINISHED | ABORTED
  fortify_seconds: int          # default 300
  max_match_seconds: int        # hard cap; 0/None = no cap
  rate_limit_per_minute: int    # default 10
  winner: "alpha" | "bravo" | None
  result: ONGOING | ALPHA_WIN | BRAVO_WIN | DRAW | ABORTED
  started_at, fortify_ends_at, finished_at: timestamps
}
```

### Commentary entry
```
{ "ts": 1695000000.123, "side": "alpha", "phase": "battle", "text": "..." }
```

### Verdict
```
{ "verdict": "correct" | "incorrect",
  "match_status": "ongoing" | "finished",
  "winner": "alpha" | "bravo" | null,
  "remaining_in_window": int }
```

---

## 4. Communication protocols

| Channel | Producer → Consumer | Transport | Notes |
|---|---|---|---|
| Submit guess | Agent → Judge | HTTP `POST /submit` | rate-limited, returns verdict |
| Read status | Agent/Orch → Judge | HTTP `GET /status` | poll for match end |
| Register secrets | Orchestrator → Judge | in-process or admin call | at provisioning |
| Emit commentary | Agent → Arena Feed | append JSONL / HTTP | one line per action |
| Consume feed | Spectator/UI → Arena | HTTP `GET /feed` | poll or stream |
| Phase control | Orchestrator → Agent | in-process event / env | start/transition signals |
| Attack traffic | Agent ↔ Opponent side | network (TCP/HTTP/fs) | whatever the defender exposes |

In the local reference runner, the Judge and Feed run as in-process services and agents run as threads, but they communicate through the *same logical contracts* so the topology can be split across hosts/containers without code changes.

---

## 5. Match lifecycle (sequence)

1. **Provision.** Orchestrator creates both workspaces, writes each `treasure.txt`, registers digests with the Judge, and starts the Feed.
2. **Start.** Orchestrator records `started_at`, sets state `FORTIFYING`, and launches both agents with the *fortify* policy.
3. **Fortification (T0 → T0+fortify_seconds).** Agents harden their treasure. No submissions are meaningful yet (the Judge accepts them, but an attacker has nothing to guess).
4. **Battle begins.** At `fortify_ends_at`, Orchestrator sets state `BATTLING` and signals agents to switch to the *battle* policy.
5. **Battle.** Agents attack the opponent and keep defending. They submit guesses to the Judge (≤ rate limit). The Judge returns verdicts.
6. **Terminal.** On the first `correct` verdict, the Judge latches `winner`; Orchestrator observes this (via `/status`), sets state `FINISHED`, stops both agents, and writes the match report. If `max_match_seconds` elapses with no correct verdict, the result is `DRAW`.

See [MATCH_LIFECYCLE.md](./MATCH_LIFECYCLE.md) for the full state machine and timing rules.

---

## 6. Security model & isolation

The game is only meaningful if an agent cannot simply read the opponent's plaintext treasure through a channel the framework itself provides.

**Trusted vs untrusted.** The Orchestrator, Judge, and Arena Feed are *trusted*. The two agents are *mutually untrusted* and untrusted by the infrastructure.

**Isolation requirements.**
- Each agent's filesystem and process space must be private to its side, except for explicitly *exposed* surfaces (open ports, served files) that constitute the legitimate attack surface.
- The defending agent protects the treasure by gating access to it (encryption, password-protected services, permissions, decoys). The raw secret remains present and recoverable if the attacker defeats the defenses — this is what makes cracking possible.
- The Judge never reveals the secret; it only confirms/denies a guess.

**Reference backend: Docker.** Each side is provisioned as its **own Docker container** with its own network namespace, identical CPU/memory cgroup limits, and only the ports the defender intentionally exposes. The `DockerSideProvisioner` is the reference backend for real matches.

**Development/test backend: local directories.** A `LocalSideProvisioner` (separate directories with owner-only `0700` permissions) is provided for development and tests only. On a single shared user account this is **not** strong isolation and must not be used for real matches. The `SideProvisioner` interface is the seam that lets the same game logic run on local dirs, Docker, or remote hosts without changes.

**Defense-strength policy.** To keep the game winnable, defense strength is bounded by a configurable policy (password length/alphabet caps, KDF work-factor ceiling, an allow-list of cipher tools, max encryption layers). See [RULES.md](./RULES.md#3-defense-strength-policy-keeps-the-game-winnable).

**Fairness.** Both agents receive identical model, token budget, tool set, and compute allocation. The only asymmetry is the random treasure and (optionally) a persona seed.

---

## 7. Failure & abuse handling

- **Rate-limit abuse:** excess submissions are rejected with HTTP 429 and do not count; they neither help nor harm the guesser beyond wasted time.
- **Judge tampering:** out of scope for agents by isolation; the Judge runs in trusted infrastructure.
- **Agent crash:** the Orchestrator detects a dead agent; the match may continue (the crashed agent simply stops attacking) or be aborted, per config.
- **Timeout:** a global `max_match_seconds` guarantees termination → `DRAW` if nobody wins.
- **Treasure tampering:** deleting/altering the raw treasure string is a rules violation; the framework can detect a missing/changed treasure file at match end and flag it in the report.

---

## 8. Extensibility

- **LLM providers:** implement the `LLMProvider` protocol for any backend.
- **Isolation backends:** implement `SideProvisioner` for containers/VMs/remote hosts.
- **Tools:** register new tools in the agent's tool registry.
- **Verifiers:** the Judge's digest comparison is pluggable (e.g., support per-side salt or keyed hashes).
- **Feed sinks/servers:** swap the JSONL store or add streaming (WebSocket/SSE).
