"""LLM provider abstraction.

The runtime talks to any chat-completions-style model through the
`LLMProvider` protocol so both agents can be given an *identical* model for
fairness. Implementations:

- `OpenAICompatibleProvider`: calls any OpenAI-style `/chat/completions`
  endpoint using only the standard library (works with OpenAI, vLLM,
  llama.cpp, Ollama, etc.).
- `MockProvider`: a scripted provider for tests/demos (no network, no cost).
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


# ---- message / tool data types --------------------------------------------


@dataclass
class ToolSpec:
    """A tool the model may invoke (JSON-Schema for its arguments)."""

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class Message:
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None


@dataclass
class LLMResponse:
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict[str, Any] | None = None


class LLMProvider(Protocol):
    """Anything that can complete a chat with optional tool calls."""

    def complete(self, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        ...


# ---- OpenAI-compatible provider -------------------------------------------


def _message_to_openai(m: Message) -> dict[str, Any]:
    if m.role == "assistant" and m.tool_calls:
        return {
            "role": "assistant",
            "content": m.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for tc in m.tool_calls
            ],
        }
    if m.role == "tool":
        return {
            "role": "tool",
            "tool_call_id": m.tool_call_id,
            "content": m.content or "",
        }
    return {"role": m.role, "content": m.content or ""}


def _toolspec_to_openai(t: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters,
        },
    }


class OpenAICompatibleProvider:
    """Minimal OpenAI-style chat-completions client (stdlib only)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        temperature: float = 0.2,
        max_tokens: int = 1024,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds

    def complete(self, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [_message_to_openai(m) for m in messages],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            payload["tools"] = [_toolspec_to_openai(t) for t in tools]
            payload["tool_choice"] = "auto"

        req = urllib.request.Request(
            url=f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        msg = data["choices"][0]["message"]
        tool_calls = [
            ToolCall(
                id=tc.get("id", ""),
                name=tc["function"]["name"],
                arguments=json.loads(tc["function"].get("arguments") or "{}"),
            )
            for tc in (msg.get("tool_calls") or [])
        ]
        return LLMResponse(content=msg.get("content"), tool_calls=tool_calls, raw=data)


# ---- mock provider ---------------------------------------------------------


class MockProvider:
    """A scripted provider that replays a queue of responses.

    Each item may be an `LLMResponse` or a callable
    `(messages, tools) -> LLMResponse`. When the queue is exhausted it returns
    a harmless `finish` so loops terminate.
    """

    def __init__(self, script: list[LLMResponse | Callable[[list[Message], list[ToolSpec]], LLMResponse]] | None = None) -> None:
        self._script = list(script or [])
        self.calls: list[list[Message]] = []

    def push(self, item: LLMResponse | Callable[[list[Message], list[ToolSpec]], LLMResponse]) -> None:
        self._script.append(item)

    def complete(self, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        self.calls.append(list(messages))
        if not self._script:
            return LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="mock-finish", name="finish", arguments={"note": "mock done"})],
            )
        item = self._script.pop(0)
        if callable(item):
            return item(messages, tools)
        return item


# ---- text-protocol provider (fallback for weak tool-calling models) --------
#
# Many small models cannot emit OpenAI `tool_calls`. TextActionProvider instead
# instructs the model (in the system prompt) to emit actions as plain text
# lines of the form:
#     ACTION <tool_name> <json-args>
# and parses those lines back into ToolCall objects, so the agent runtime works
# unchanged regardless of whether the underlying model supports native tools.

_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9]*\s*$")
_ACTION_RE = re.compile(r"^\s*ACTION\s+([A-Za-z_][\w]*)\s*(.*)$")
_TOOL_NAME_KEYS = ("name", "tool", "tool_name", "function")
_TOOL_ARG_KEYS = ("arguments", "args", "parameters", "params", "input")


def _scan_json_objects(text: str) -> list[tuple[dict, int, int]]:
    """Yield (parsed_dict, start, end) for every balanced top-level {...} in text."""
    out: list[tuple[dict, int, int]] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] == "{":
            depth, j, instr, esc = 0, i, False, False
            while j < n:
                ch = text[j]
                if instr:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        instr = False
                else:
                    if ch == '"':
                        instr = True
                    elif ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            break
                j += 1
            if depth == 0:
                chunk = text[i : j + 1]
                try:
                    obj = json.loads(chunk)
                except json.JSONDecodeError:
                    obj = None
                if isinstance(obj, dict):
                    out.append((obj, i, j + 1))
                i = j + 1
                continue
        i += 1
    return out


def _remove_spans(text: str, spans: list[tuple[int, int]]) -> str:
    if not spans:
        return text
    out, last = [], 0
    for s, e in sorted(spans):
        out.append(text[last:s])
        last = e
    out.append(text[last:])
    return " ".join("".join(out).split())


def strip_json_objects(text: str) -> str:
    """Remove all balanced {...} JSON objects from free text (for clean commentary)."""
    spans = [(s, e) for _, s, e in _scan_json_objects(text)]
    return _remove_spans(text, spans)


def _tool_signature(spec: ToolSpec) -> str:
    props = spec.parameters.get("properties", {}) if spec.parameters else {}
    required = set(spec.parameters.get("required", [])) if spec.parameters else set()
    parts = []
    for name, meta in props.items():
        ptype = meta.get("type", "any") if isinstance(meta, dict) else "any"
        suffix = "" if name in required else "?"
        parts.append(f"{name}{suffix}: {ptype}")
    return f"{spec.name}({', '.join(parts)}) — {spec.description}"


def build_text_protocol_instructions(tools: list[ToolSpec]) -> str:
    lines = [
        "You control the environment by emitting COMMANDS, each on its own line,",
        "in EXACTLY this format (no markdown, no code fences):",
        "",
        "ACTION <tool_name> <json-arguments>",
        "",
        "Available tools:",
    ]
    lines += [f"- {_tool_signature(t)}" for t in tools]
    lines += [
        "",
        "Rules:",
        "- Write ONLY in English. Every ACTION, comment, and line of reasoning must be English.",
        "- Emit a few ACTION lines per reply, each on its own line.",
        "- After acting, emit ONE `comment` ACTION: a single short sentence (under 15 words),",
        "  in plain English, saying what you DID and what you learned. No commands, no JSON,",
        "  no logs. Think like: 'what if I try X' -> then report the result.",
        "- Use `submit_code` only when you have a confident 128-char candidate.",
        "- Use `finish` with {} to end your turn.",
        "- Any line that is not an ACTION line is treated as private reasoning (not shown).",
        "",
        "Example:",
        'ACTION list_dir {"path": "."}',
        'ACTION comment {"text": "I listed my workspace and found the treasure file."}',
    ]
    return "\n".join(lines)


def _parse_json_object(raw: str) -> dict[str, Any]:
    """Best-effort extraction of a single JSON object from a string."""
    raw = raw.strip()
    if not raw:
        return {}
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    try:
        obj = json.loads(raw[start : end + 1])
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        return {}


def parse_action_lines(text: str) -> tuple[list[ToolCall], str]:
    """Split a completion into tool calls and remaining free text.

    Handles two formats small models use:
      - ACTION <tool> <json-args>          (one per line)
      - {"name": "<tool>", "arguments": {...}}   (one or more JSON objects)
    Returns (tool_calls, remaining_free_text) with tool syntax removed from the text.
    """
    tool_calls: list[ToolCall] = []

    # 1) ACTION lines
    rest_lines: list[str] = []
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            continue
        m = _ACTION_RE.match(line)
        if m:
            tool_calls.append(
                ToolCall(id=f"txt-{len(tool_calls)}", name=m.group(1), arguments=_parse_json_object(m.group(2)))
            )
        else:
            rest_lines.append(line)
    rest_text = "\n".join(rest_lines)

    # 2) {"name": ..., "arguments"/"args"/...: {...}} JSON objects
    spans: list[tuple[int, int]] = []
    for obj, s, e in _scan_json_objects(rest_text):
        name = next((obj[k] for k in _TOOL_NAME_KEYS if isinstance(obj.get(k), str)), None)
        args = next((obj[k] for k in _TOOL_ARG_KEYS if isinstance(obj.get(k), dict)), {})
        if name:
            tool_calls.append(ToolCall(id=f"txt-{len(tool_calls)}", name=name, arguments=args))
            spans.append((s, e))

    remaining = _remove_spans(rest_text, spans).strip()
    return tool_calls, remaining


class TextActionProvider:
    """LLMProvider that drives a model through a plain-text action protocol.

    Wraps an OpenAICompatibleProvider. Ignores native tool-calling; injects the
    text protocol into the system prompt and parses ACTION lines from the
    completion into ToolCalls.
    """

    def __init__(self, base: OpenAICompatibleProvider) -> None:
        self.base = base

    def complete(self, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        protocol = build_text_protocol_instructions(tools)
        augmented = list(messages)
        if augmented and augmented[0].role == "system":
            merged = (augmented[0].content or "") + "\n\n" + protocol
            augmented[0] = Message(role="system", content=merged)
        else:
            augmented.insert(0, Message(role="system", content=protocol))

        resp = self.base.complete(augmented, tools=[])
        tool_calls, remaining = parse_action_lines(resp.content or "")
        return LLMResponse(
            content=remaining or None, tool_calls=tool_calls, raw=resp.raw
        )
