# Judge API

The Judge is a neutral HTTP service that verifies candidate treasure codes submitted by agents and enforces per-side rate limits. It is the single source of truth for the match outcome.

- Base URL (reference runner): `http://127.0.0.1:<port>`
- All bodies are JSON (`application/json`).
- The Judge stores only `sha256(treasure)` digests — never plaintext secrets.
- Verdicts are boolean only: **no hints, no partial credit**.

---

## Data types

### Side
A string identifier: `"alpha"` or `"bravo"`.

### SubmitRequest
```json
{ "side": "alpha", "guess": "<128-char string>" }
```
- `side` (string, required): which agent is submitting. The guess is checked against the **opponent's** remaining (not yet stolen) treasures.
- `guess` (string, required): the candidate code. Should be exactly 128 characters, but the Judge validates rather than assumes.

### SubmitResponse
```json
{
  "verdict": "correct",
  "match_status": "ongoing",
  "winner": null,
  "remaining_in_window": 9,
  "attempts_total": 1,
  "detail": "side alpha stole a treasure of bravo (1/2)"
}
```
- `verdict`: `"correct"` | `"incorrect"`. A `correct` verdict steals exactly one of the opponent's treasures; re-submitting an already-stolen code yields `incorrect` with detail `"treasure already stolen"`.
- `match_status`: `"ongoing"` | `"finished"`. Becomes `"finished"` when a side has stolen **all** of the opponent's treasures.
- `winner`: `"alpha"` | `"bravo"` | `null`.
- `remaining_in_window`: how many submissions this side may still make in the current rate-limit window **after** this request.
- `attempts_total`: total accepted submissions by this side this match.
- `detail`: short human-readable note (includes steal progress, e.g. `(1/2)`).

### StatusResponse
```json
{
  "match_id": "m-20260917-001",
  "state": "battling",
  "result": "ongoing",
  "winner": null,
  "attempts": { "alpha": 3, "bravo": 5 },
  "limits": { "per_window": 10, "window_seconds": 60 },
  "progress": {
    "alpha": { "stolen": 1, "total": 2 },
    "bravo": { "stolen": 0, "total": 2 }
  }
}
```
- `progress`: per side, how many of the **opponent's** treasures that side has stolen (`stolen`) out of how many (`total`).

### HealthResponse
```json
{ "status": "ok" }
```

---

## Endpoints

### `POST /submit`
Submit a candidate code for verification.

- **200 OK** — request accepted and evaluated (body is `SubmitResponse`). This includes both `correct` and `incorrect` verdicts.
- **400 Bad Request** — malformed body, unknown `side`, or `guess` fails validation (e.g., wrong length/charset).
- **409 Conflict** — the match is already finished; further submissions are not evaluated.
- **429 Too Many Requests** — the side exceeded its rate limit. Body includes `retry_after_seconds`. The guess is **not** evaluated and does not count as an attempt.

Rate limiting is a **sliding window**: at most `per_window` (default 10) submissions per side within any trailing `window_seconds` (default 60).

Example (curl):
```bash
curl -s -X POST http://127.0.0.1:8000/submit \
  -H 'Content-Type: application/json' \
  -d '{"side":"alpha","guess":"'"$(printf 'a%.0s' {1..128})"'"}'
```

### `GET /status`
Return the current match status (`StatusResponse`). Used by the orchestrator and agents to detect match end.

### `GET /health`
Liveness probe → `{"status":"ok"}`.

---

## Semantics & guarantees

- **Steal all to win.** Each `correct` verdict steals one of the opponent's treasures. The first side whose opponent has no treasures left latches `winner` and `match_status=finished`. All later submissions return `409` and are not evaluated.
- **Digest-only verification.** Guesses are hashed with SHA-256 and matched against the stored digests; plaintext treasures are never stored.
- **Per-side independence.** Each side has its own rate-limit window; one side's activity never throttles the other.
- **Idempotent finish.** Once finished, the result is immutable.

---

## Error body
Errors use a consistent envelope:
```json
{ "error": "rate_limited", "message": "side alpha exceeded 10 requests per 60s", "retry_after_seconds": 12.4 }
```

| HTTP | `error` | When |
|---|---|---|
| 400 | `invalid_request` | bad JSON / missing fields / unknown side / invalid guess format |
| 409 | `match_finished` | submitting after a winner is decided |
| 429 | `rate_limited` | over the per-side rate limit |
