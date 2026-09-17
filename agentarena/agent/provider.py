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
