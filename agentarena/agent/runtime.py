"""The phase-aware agent loop.

Drives an LLM with tools through the fortify and battle phases, emitting
commentary after every step. The loop is identical for both agents; only the
side, treasure, judge target, and prompts differ.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from ..core.types import Side
from ..judge.client import JudgeClient
from .commentary import CommentaryEmitter
from .prompts import DefensePolicy, battle_prompt, fortify_prompt
from .provider import LLMProvider, Message, ToolCall
from .tools import TOOL_SPECS, SideContext, execute_tool

MAX_HISTORY = 40
_STUCK_WINDOW = 3  # consecutive identical / idle iterations that trigger a nudge


def _stuck_nudge(phase: str) -> str:
    if phase == "fortify":
        return (
            "You are repeating yourself or idling. Your treasure is likely protected "
            "enough already. Do ONE final, DIFFERENT hardening step (or just verify it) "
            "and then finish. Act now — do not wait."
        )
    return (
        "You are stuck or idling. STOP waiting and change approach. Pick the single most "
        "promising file and crack it NOW: try short common passwords (password123, qwerty, "
        "letmein) or a 4-6 digit PIN via a quick loop. If the Judge replied 429, STOP "
        "submitting for ~60s and analyze files instead. Act now with a concrete, different step."
    )


@dataclass
class AgentConfig:
    side: Side
    workspace: Path
    judge: JudgeClient
    commentary: CommentaryEmitter
    provider: LLMProvider
    opponent_exposed: Path | None = None
    opponent_hint: str = "the opponent's exposed network services and files"
    rate_limit_per_minute: int = 10
    treasure_name: str = "treasure.txt"
    policy: DefensePolicy = field(default_factory=DefensePolicy)
    max_iterations: int = 50


@dataclass
class PhaseResult:
    phase: str
    iterations: int = 0
    submissions: int = 0
    won: bool = False
    stopped: bool = False


class AgentRuntime:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.ctx = SideContext(
            side=config.side,
            workspace=config.workspace,
            judge=config.judge,
            commentary=config.commentary,
            opponent_exposed=config.opponent_exposed,
        )

    # ---- public API --------------------------------------------------------

    def run_fortify(self, stop: threading.Event | None = None) -> PhaseResult:
        prompt = fortify_prompt(
            self.config.side, self.config.treasure_name, self.config.policy
        )
        return self._run_phase("fortify", prompt, stop)

    def run_battle(self, stop: threading.Event | None = None) -> PhaseResult:
        prompt = battle_prompt(
            self.config.side, self.config.opponent_hint, self.config.rate_limit_per_minute
        )
        return self._run_phase("battle", prompt, stop)

    # ---- core loop ---------------------------------------------------------

    def _run_phase(
        self, phase: str, system_prompt: str, stop: threading.Event | None
    ) -> PhaseResult:
        stop = stop or threading.Event()
        result = PhaseResult(phase=phase)
        messages: list[Message] = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=f"Begin the {phase} phase now."),
        ]
        consecutive_errors = 0
        recent: deque[tuple[tuple, bool]] = deque(maxlen=_STUCK_WINDOW * 2)

        while not stop.is_set() and result.iterations < self.config.max_iterations:
            result.iterations += 1
            try:
                response = self.config.provider.complete(messages, TOOL_SPECS)
                consecutive_errors = 0
            except Exception as exc:  # LLM backend hiccup
                consecutive_errors += 1
                self.ctx.commentary.emit(f"Model backend error: {exc}", phase=phase)
                if consecutive_errors >= 3:
                    break
                continue

            # ---- stuck / idle detection -----------------------------------
            made_progress = any(c.name not in ("comment", "finish") for c in response.tool_calls)
            sig = tuple(
                sorted((c.name, json.dumps(c.arguments, sort_keys=True)) for c in response.tool_calls)
            ) or (("talk", (response.content or "")[:80]),)
            recent.append((sig, made_progress))
            if len(recent) >= _STUCK_WINDOW:
                last = list(recent)[-_STUCK_WINDOW:]
                identical = all(item[0] == last[0][0] for item in last)
                idle = not any(item[1] for item in last)
                if identical or idle:
                    messages.append(Message(role="user", content=_stuck_nudge(phase)))
                    recent.clear()

            if response.tool_calls:
                messages.append(
                    Message(role="assistant", content=response.content, tool_calls=response.tool_calls)
                )
                for call in response.tool_calls:
                    output = execute_tool(self.ctx, call, phase=phase)
                    messages.append(
                        Message(role="tool", tool_call_id=call.id, content=output)
                    )
                    if call.name == "submit_code":
                        result.submissions += 1
                        if '"verdict": "correct"' in output or '"verdict":"correct"' in output:
                            result.won = True
                # Share the model's reasoning as a "thought". The emitter strips
                # JSON/commands and skips empties and consecutive duplicates.
                if response.content:
                    self.ctx.commentary.emit(response.content, phase=phase)
            else:
                # No tool calls: share the thought and nudge the model to act.
                if response.content:
                    self.ctx.commentary.emit(response.content, phase=phase)
                messages.append(Message(role="assistant", content=response.content or ""))
                messages.append(
                    Message(role="user", content="Continue. Use tools to act.")
                )

            self._trim_history(messages)

            if result.won or self._match_finished():
                break

        result.stopped = stop.is_set()
        return result

    # ---- helpers -----------------------------------------------------------

    def _match_finished(self) -> bool:
        try:
            return self.ctx.judge.status().get("match_status") == "finished"
        except Exception:
            return False

    @staticmethod
    def _trim_history(messages: list[Message]) -> None:
        """Keep the system prompt + most recent messages to bound context size."""
        if len(messages) <= MAX_HISTORY:
            return
        system = messages[0]
        del messages[1 : len(messages) - (MAX_HISTORY - 1)]
        messages[0] = system
