"""The phase-aware agent loop.

Drives an LLM with tools through the fortify and battle phases, emitting
commentary after every step. The loop is identical for both agents; only the
side, treasure, judge target, and prompts differ.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from ..core.types import Side
from ..judge.client import JudgeClient
from .commentary import CommentaryEmitter
from .prompts import DefensePolicy, battle_prompt, fortify_prompt
from .provider import LLMProvider, Message, ToolCall
from .tools import TOOL_SPECS, SideContext, execute_tool

MAX_HISTORY = 40


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

            if response.tool_calls:
                messages.append(
                    Message(role="assistant", content=response.content, tool_calls=response.tool_calls)
                )
                made_comment = False
                for call in response.tool_calls:
                    output = execute_tool(self.ctx, call, phase=phase)
                    messages.append(
                        Message(role="tool", tool_call_id=call.id, content=output)
                    )
                    if call.name == "comment":
                        made_comment = True
                    elif call.name == "submit_code":
                        result.submissions += 1
                        if '"verdict": "correct"' in output or '"verdict":"correct"' in output:
                            result.won = True
                if not made_comment:
                    note = response.content or f"Working on {phase} tasks."
                    self.ctx.commentary.emit(note, phase=phase)
            else:
                # No tool calls: narrate and nudge the model to act.
                text = response.content or "Assessing the situation."
                self.ctx.commentary.emit(text, phase=phase)
                messages.append(Message(role="assistant", content=text))
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
