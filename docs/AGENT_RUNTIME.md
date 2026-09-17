# Agent Runtime

How an AgentArena agent thinks and acts: the LLM provider interface, the tool set, the phase-aware loop, and the commentary contract.

---

## 1. Overview

Each agent is an **LLM in a loop with tools**. The runtime is identical for both agents; only the side, treasure, judge target, and optional persona differ. The loop is **phase-aware**:

- **Fortify** — protect your own treasure.
- **Battle** — discover the opponent's treasure code while keeping yours safe.

Every iteration the runtime:
1. Builds a prompt from system instructions + current phase + recent observations + tool results.
2. Calls the LLM, which returns either tool calls or a final note.
3. Executes the tool calls.
4. Emits a short English **commentary** line describing what it just did.
5. Repeats until the phase/match ends or a stop signal arrives.

---

## 2. LLMProvider interface

The runtime talks to the model through a small protocol so any backend can be plugged in and both agents can be given an *identical* model for fairness.

```python
class LLMProvider(Protocol):
    def complete(self, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse: ...
```

- `messages`: chat history (`system`/`user`/`assistant`/`tool` roles).
- `tools`: the tool schemas the model may invoke.
- Returns `LLMResponse` with either `tool_calls: list[ToolCall]` or final `content: str`.

Implementations:
- `OpenAICompatibleProvider` — calls any OpenAI-style `POST {base_url}/chat/completions` over HTTP using only the standard library (`urllib`). Works with hosted APIs and local servers (vLLM, llama.cpp, Ollama, etc.).
- `MockProvider` — a scripted provider used for tests and demos; deterministically replays a queue of responses so a full match can run with no real model.

Fairness knob: the provider exposes the model name, temperature, and a **token budget**. The orchestrator constructs both agents with identical values.

---

## 3. Tools

Tools are the agent's only way to affect the world. Each tool has a name, a JSON-Schema for its arguments, and a handler that runs **within the agent's side context** (its workspace root and network identity).

Default tool set:

| Tool | Arguments | Effect |
|---|---|---|
| `run_shell` | `command: str` | Run a shell command with `cwd` = side workspace. Returns `{exit_code, stdout, stderr}` (truncated). This is the agent's general-purpose capability (crypto, packaging, networking, etc.). |
| `read_file` | `path: str` | Read a file (relative to side workspace or an explicitly exposed opponent path). Returns content (truncated). |
| `write_file` | `path: str, content: str` | Write/overwrite a file in the side workspace. |
| `list_dir` | `path: str` | List a directory's entries. |
| `http_request` | `method: str, url: str, headers?, body?` | Make an HTTP request (e.g., probe an opponent service, call the Judge). Returns `{status, body}`. |
| `submit_code` | `guess: str` | Submit a candidate opponent code to the Judge. Returns the verdict. This is the **only** way to win. |
| `comment` | `text: str` | Emit a commentary line to the Arena Feed (see §5). |
| `finish` | `note?: str` | Declare the current sub-task done / yield the turn (ends this iteration). |

Notes:
- Paths in `read_file`/`write_file`/`list_dir`/`run_shell` are **sandboxed to the side workspace** in the reference implementation. Reaching the opponent goes through `http_request` (network) or explicitly *exposed* opponent paths — the legitimate attack surface.
- `submit_code` is subject to the Judge's rate limit; the tool surfaces `429` responses so the agent learns to slow down.
- `run_shell` is powerful by design: real defense/attack needs real commands (openssl, zip, curl, etc.). In containerized deployments it runs inside the side's container.

---

## 4. The loop (phase-aware)

```
state = load_side_context()
while not match_over and not stop_signalled:
    phase = orchestrator.current_phase()          # "fortify" | "battle"
    policy = POLICIES[phase]
    messages = build_messages(policy.system, state, recent_observations, tool_results)
    response = llm.complete(messages, tools=TOOL_SPECS)
    if response.tool_calls:
        for call in response.tool_calls:
            result = execute_tool(call, side_context)
            tool_results.append(result)
            if call.name == "comment":
                record_commentary(call.args["text"])
    else:
        record_commentary(summarize(response.content))
```

### Fortify policy (excerpt of system prompt)
> You are the defender of side {side}. A file at {treasure_path} contains a 128-character code you must protect. You may NOT delete or modify the code itself. Build layered defenses around it: encrypt access, gate it behind passwords or services, plant decoy 128-character files, restrict permissions, add monitoring. Think step by step and use the tools. After each action, call `comment` with a one-sentence English summary of what you did. You have limited time; prioritize strong, correct defenses.

### Battle policy (excerpt of system prompt)
> You are the attacker of side {side} and still the defender of your own treasure. Goal: discover the opponent's 128-character code and submit it via `submit_code` before they discover yours. Probe the opponent at {opponent_endpoints}; crack passwords/encryption; distinguish decoys from the real code; exploit misconfigurations. Keep your own defenses up. The Judge allows only {rate_limit} submissions per minute — make each guess count. After each action, call `comment` with a one-sentence English summary. If you obtain a confident candidate, submit it.

The prompts live in `agentarena/agent/prompts.py` and can be tuned per match/persona.

---

## 5. Commentary contract

Commentary is how humans follow the match. Rules enforced by the runtime/feed:

- **Language:** English.
- **Length:** short — ideally one sentence, hard-capped (default 280 chars).
- **Cadence:** at least one entry per loop iteration (the runtime synthesizes one from the model's final content if the model didn't call `comment`).
- **Content:** high-level description of current/just-completed action. Must **not** contain the agent's own 128-char secret or anything that directly reveals it. The feed may redact exact 128-char tokens as a safety net.
- Entries are appended as `{ts, side, phase, text}`.

---

## 6. Fairness & determinism

- Both agents use the same runtime, model, temperature, token budget, and tool set.
- The only intentional asymmetries are the random treasure and an optional persona seed.
- `MockProvider` makes matches reproducible for tests (no network, no cost).

---

## 7. Stop conditions

An agent stops when any of the following holds:
- The orchestrator signals match end (a winner was decided or time expired).
- It receives a cooperative stop signal (and, after a grace period, is killed).
- Its token budget is exhausted (configurable policy: stop or degrade).
