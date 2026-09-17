# Match Lifecycle & State Machine

States, transitions, timing rules, and termination conditions for an AgentArena match.

---

## States

| State | Meaning |
|---|---|
| `CREATED` | Match object exists; nothing provisioned yet. |
| `PROVISIONING` | Workspaces created, treasures written, secrets registered with the Judge. |
| `FORTIFYING` | Phase 1. Both agents harden their treasure. Duration = `fortify_seconds` (default 300). |
| `BATTLING` | Phase 2. Agents attack the opponent and keep defending. |
| `FINISHED` | A winner is decided, or the match timed out as a draw, or it was aborted. Terminal. |
| `ABORTED` | The match was stopped early by the orchestrator/operator. Terminal. |

---

## State machine

```
            provision_ok
 CREATED ───────────────► PROVISIONING
                              │ ready
                              ▼
                          FORTIFYING ──────────────► ABORTED
                              │ fortify_seconds elapsed   (operator abort)
                              ▼
                           BATTLING ───────────────► ABORTED
                              │
              ┌───────────────┼────────────────────────┐
              │ correct        │ correct                │ max_match_seconds
              │ by alpha       │ by bravo               │ elapsed (no winner)
              ▼                ▼                        ▼
          FINISHED         FINISHED                 FINISHED
        (ALPHA_WIN)      (BRAVO_WIN)                (DRAW)
```

Legal transitions (enforced by the match state machine; illegal transitions raise an error):

- `CREATED → PROVISIONING`
- `PROVISIONING → FORTIFYING`
- `FORTIFYING → BATTLING`
- `FORTIFYING → ABORTED`
- `BATTLING → FINISHED`  (win or draw)
- `BATTLING → ABORTED`
- `PROVISIONING → ABORTED` (setup failure)

`FINISHED` and `ABORTED` are terminal; no further transitions.

---

## Timing rules

Let `t0 = started_at` (when the match enters `FORTIFYING`).

- **Fortification window:** `[t0, t0 + fortify_seconds)`. During this window agents run the *fortify* policy.
- **Battle start:** at `fortify_ends_at = t0 + fortify_seconds` the match enters `BATTLING`.
- **Hard cap:** if `max_match_seconds > 0`, the match must end by `t0 + max_match_seconds`; otherwise it ends as `DRAW`. A value of `0`/unset means "no cap" (battle until a winner).

The orchestrator enforces these with monotonic clocks (immune to wall-clock changes).

---

## Result values

| `result` | Condition |
|---|---|
| `ONGOING` | match not finished |
| `ALPHA_WIN` | alpha submitted bravo's code correctly, first |
| `BRAVO_WIN` | bravo submitted alpha's code correctly, first |
| `DRAW` | `max_match_seconds` elapsed with no correct submission |
| `ABORTED` | match aborted before a natural finish |

`winner` is `"alpha"`, `"bravo"`, or `null` (for `DRAW`/`ABORTED`/`ONGOING`).

---

## Termination & reporting

When the match reaches `FINISHED`:

1. The Judge has already latched the winner (if any) at the moment of the first correct verdict.
2. The orchestrator stops both agent runtimes (cooperative stop signal, then hard kill after a grace period).
3. A **match report** is written, containing: match id, final state/result/winner, per-side attempt counts, duration, and the full commentary transcript from the Arena Feed.

---

## Concurrency notes

- The Judge may receive a `correct` verdict at the exact boundary of a phase transition; the verdict is authoritative regardless of which phase the orchestrator *believes* is active, because a guess is only meaningful once the attacker had a chance to learn the secret. In practice, submissions before `BATTLING` are allowed but almost never correct.
- If both agents submit a correct code in the same instant, the **first one processed by the Judge** wins; the Judge serializes submissions internally to make this deterministic.
