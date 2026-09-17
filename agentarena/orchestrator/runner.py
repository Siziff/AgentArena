"""MatchRunner: orchestrates a full match end to end.

Responsibilities (see docs/ARCHITECTURE.md):
- provision both sides and register their treasure digests with the Judge,
- drive the phase timers (fortify -> battle),
- launch both agent runtimes concurrently,
- watch the Judge for a terminal verdict (or the time cap for a draw),
- finalize the match and write a report with the commentary transcript.

The runner talks to a JudgeCore directly for setup/status; agents receive
JudgeClients via `judge_client_factory` (in-process by default, HTTP in real
deployments). LLM providers are injected via `provider_factory` so matches can
run on real models or on the scripted MockProvider for tests/demos.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..agent.commentary import CommentaryEmitter
from ..agent.prompts import DefensePolicy
from ..agent.provider import LLMProvider
from ..agent.runtime import AgentConfig, AgentRuntime
from ..core.match import Match
from ..core.treasure import DEFAULT_ALPHABET, DEFAULT_LENGTH, generate_treasure
from ..core.types import MatchState, Side
from ..judge.client import InProcessJudgeClient, JudgeClient
from ..judge.core import JudgeCore
from .side import LocalSideProvisioner, SideHandle

# Builds an LLM provider for a given side and phase ("fortify" | "battle").
ProviderFactory = Callable[[Side, str], LLMProvider]
JudgeClientFactory = Callable[[Side, JudgeCore], JudgeClient]


@dataclass
class RunnerConfig:
    root: Path
    provider_factory: ProviderFactory
    fortify_seconds: int = 300
    max_match_seconds: int = 0
    rate_limit_per_minute: int = 10
    treasure_length: int = DEFAULT_LENGTH
    alphabet: str = DEFAULT_ALPHABET
    policy: DefensePolicy = field(default_factory=DefensePolicy)
    judge_client_factory: JudgeClientFactory = lambda side, core: InProcessJudgeClient(core, side)
    treasures: dict[Side, str] | None = None  # pre-seeded, else generated
    max_agent_iterations: int = 50
    poll_interval: float = 0.1


@dataclass
class MatchReport:
    match: dict
    attempts: dict
    commentary: list[dict]
    sides: dict

    def to_dict(self) -> dict:
        return {
            "match": self.match,
            "attempts": self.attempts,
            "sides": self.sides,
            "commentary": self.commentary,
        }

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")


class MatchRunner:
    def __init__(self, config: RunnerConfig) -> None:
        self.config = config
        self.match = Match(
            fortify_seconds=config.fortify_seconds,
            max_match_seconds=config.max_match_seconds,
            rate_limit_per_minute=config.rate_limit_per_minute,
        )
        self.judge = JudgeCore(
            rate_limit_per_minute=config.rate_limit_per_minute,
            treasure_length=config.treasure_length,
            alphabet=config.alphabet,
            match_id=self.match.match_id,
        )
        self.provisioner = LocalSideProvisioner(Path(config.root) / "sides")
        self.handles: dict[Side, SideHandle] = {}

    # ---- main entry ---------------------------------------------------------

    def run(self) -> MatchReport:
        cfg = self.config
        self.match.transition_to(MatchState.PROVISIONING)
        self._provision_and_register()

        # Phase 1: fortification.
        self.match.transition_to(MatchState.FORTIFYING)
        self._run_agents_concurrently(phase="fortify", until=self.match.battle_should_start)

        # Phase 2: battle.
        self.match.transition_to(MatchState.BATTLING)
        self._run_agents_concurrently(phase="battle", until=self._battle_over)

        self._finalize()
        report = self._build_report()
        report.save(Path(cfg.root) / "reports" / f"{self.match.match_id}.json")
        return report

    # ---- setup --------------------------------------------------------------

    def _provision_and_register(self) -> None:
        cfg = self.config
        for side in (Side.ALPHA, Side.BRAVO):
            treasure = (
                cfg.treasures.get(side)
                if cfg.treasures
                else generate_treasure(cfg.treasure_length, cfg.alphabet)
            )
            handle = self.provisioner.provision(side, treasure)
            self.handles[side] = handle
            self.judge.register_secret(side, treasure)

    def _make_agent(self, side: Side, phase: str) -> AgentRuntime:
        cfg = self.config
        handle = self.handles[side]
        opponent = self.handles[side.opponent]
        commentary = CommentaryEmitter(
            side=side,
            path=Path(cfg.root) / "arena" / f"{side.value}.jsonl",
            secret=handle.treasure_path.read_text(encoding="utf-8"),
            alphabet=cfg.alphabet,
            treasure_length=cfg.treasure_length,
        )
        agent_cfg = AgentConfig(
            side=side,
            workspace=handle.workspace,
            judge=cfg.judge_client_factory(side, self.judge),
            commentary=commentary,
            provider=cfg.provider_factory(side, phase),
            opponent_exposed=opponent.workspace,
            opponent_hint=opponent.exposure_hint,
            rate_limit_per_minute=cfg.rate_limit_per_minute,
            policy=cfg.policy,
            max_iterations=cfg.max_agent_iterations,
        )
        return AgentRuntime(agent_cfg)

    # ---- phase driving --------------------------------------------------------

    def _run_agents_concurrently(self, phase: str, until: Callable[[], bool]) -> None:
        stop = threading.Event()
        threads = []
        for side in (Side.ALPHA, Side.BRAVO):
            agent = self._make_agent(side, phase)
            target = agent.run_fortify if phase == "fortify" else agent.run_battle
            t = threading.Thread(target=target, args=(stop,), daemon=True, name=f"{phase}-{side.value}")
            t.start()
            threads.append(t)

        # Wait until the phase's end condition, then stop the agents.
        while not until():
            time.sleep(self.config.poll_interval)
        stop.set()
        for t in threads:
            t.join(timeout=5.0)

    def _battle_over(self) -> bool:
        return self.judge.is_finished or self.match.time_cap_reached()

    # ---- finalization ---------------------------------------------------------

    def _finalize(self) -> None:
        if self.judge.is_finished and self.judge.winner is not None:
            self.match.declare_winner(self.judge.winner)
        elif self.match.time_cap_reached():
            self.match.declare_draw()
        else:
            self.match.abort()

    def _build_report(self) -> MatchReport:
        commentary: list[dict] = []
        for side in (Side.ALPHA, Side.BRAVO):
            path = Path(self.config.root) / "arena" / f"{side.value}.jsonl"
            if path.is_file():
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line:
                        commentary.append(json.loads(line))
        commentary.sort(key=lambda e: float(e.get("ts", 0)))
        return MatchReport(
            match=self.match.summary(),
            attempts=self.judge.status()["attempts"],
            sides={
                s.value: {
                    "workspace": str(h.workspace),
                    "treasure_path": str(h.treasure_path),
                    "backend": h.backend,
                }
                for s, h in self.handles.items()
            },
            commentary=commentary,
        )
