"""AgentArena command-line interface.

Subcommands:
    gen-treasure   print a fresh treasure string
    judge          run the Judge HTTP API
    feed           run the Arena feed HTTP server
    run-match      run a match from a TOML config
    demo           run a fast, deterministic scripted match (offline, no model)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agent.provider import LLMResponse, MockProvider, ToolCall
from .config import build_provider_factory, load_config
from .core.treasure import DEFAULT_ALPHABET, DEFAULT_LENGTH, generate_treasure
from .core.types import Side
from .orchestrator.runner import MatchRunner, RunnerConfig


def _cmd_gen_treasure(args: argparse.Namespace) -> int:
    print(generate_treasure(args.length, args.alphabet))
    return 0


def _cmd_judge(args: argparse.Namespace) -> int:
    import uvicorn

    from .judge.app import create_default_app

    uvicorn.run(create_default_app(), host=args.host, port=args.port, log_level="info")
    return 0


def _cmd_feed(args: argparse.Namespace) -> int:
    import uvicorn

    from .arena.feed import FeedStore
    from .arena.server import create_app

    store = FeedStore([Path(p) for p in args.files])
    uvicorn.run(create_app(store), host=args.host, port=args.port, log_level="info")
    return 0


def _build_runner(cfg_path: str, output: str, provider_factory) -> MatchRunner:
    cfg = load_config(cfg_path)
    return MatchRunner(
        RunnerConfig(
            root=Path(output),
            provider_factory=provider_factory,
            fortify_seconds=cfg.match.fortify_seconds,
            max_match_seconds=cfg.match.max_match_seconds,
            rate_limit_per_minute=cfg.match.rate_limit_per_minute,
            treasure_length=cfg.match.treasure_length,
            alphabet=cfg.match.alphabet,
            policy=cfg.policy,
            max_agent_iterations=cfg.match.max_agent_iterations,
        )
    )


def _cmd_run_match(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    if cfg.llm.provider == "mock" and not args.mock:
        print(
            "warning: llm.provider is 'mock'; agents will not play intelligently. "
            "Pass --mock to acknowledge, or set llm.provider='openai_compatible'.",
            file=sys.stderr,
        )
        return 2
    factory = build_provider_factory(cfg.llm)
    runner = _build_runner(args.config, args.output, factory)
    report = runner.run()
    print(json.dumps(report.match, indent=2))
    print(f"report written under {args.output}/reports/")
    return 0


# ---- demo (deterministic, offline) -----------------------------------------

_ALPHA_TREASURE = "a" * DEFAULT_LENGTH
_BRAVO_TREASURE = "b" * DEFAULT_LENGTH


def _tool(name: str, **arguments) -> ToolCall:
    return ToolCall(id=f"demo-{name}", name=name, arguments=arguments)


def _demo_provider_factory(side: Side, phase: str) -> MockProvider:
    """Scripted providers: Alpha plays to win, Bravo defends weakly."""
    if phase == "fortify":
        if side is Side.ALPHA:
            return MockProvider([
                LLMResponse(tool_calls=[
                    _tool("run_shell", command="openssl version && mkdir -p vault"),
                    _tool("comment", text="Setting up an encrypted vault and checking tools."),
                ]),
            ])
        return MockProvider([
            LLMResponse(tool_calls=[
                _tool("comment", text="Leaving the treasure as-is and planting a decoy file."),
            ]),
        ])
    # battle
    if side is Side.ALPHA:
        return MockProvider([
            LLMResponse(tool_calls=[
                _tool("list_dir", path="."),
                _tool("comment", text="Probing Bravo's exposed workspace for candidates."),
            ]),
            LLMResponse(tool_calls=[
                _tool("submit_code", guess=_BRAVO_TREASURE),
                _tool("comment", text="Submitting a confident candidate code to the Judge."),
            ]),
        ])
    return MockProvider([
        LLMResponse(tool_calls=[
            _tool("submit_code", guess="c" * DEFAULT_LENGTH),  # wrong on purpose
            _tool("comment", text="Guessing a candidate code for Alpha."),
        ]),
    ])


def _cmd_demo(args: argparse.Namespace) -> int:
    runner = MatchRunner(
        RunnerConfig(
            root=Path(args.output),
            provider_factory=_demo_provider_factory,
            fortify_seconds=1,
            max_match_seconds=30,
            rate_limit_per_minute=10,
            treasures={Side.ALPHA: _ALPHA_TREASURE, Side.BRAVO: _BRAVO_TREASURE},
            max_agent_iterations=10,
        )
    )
    report = runner.run()
    print("=== AgentArena demo match ===")
    print(json.dumps(report.match, indent=2))
    print("\n=== Live commentary transcript ===")
    for e in report.commentary:
        print(f"[{e['side']:>5}/{e['phase']:<7}] {e['text']}")
    print(f"\nreport written under {args.output}/reports/")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agentarena", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    g = sub.add_parser("gen-treasure", help="print a fresh treasure string")
    g.add_argument("--length", type=int, default=DEFAULT_LENGTH)
    g.add_argument("--alphabet", type=str, default=DEFAULT_ALPHABET)
    g.set_defaults(func=_cmd_gen_treasure)

    j = sub.add_parser("judge", help="run the Judge HTTP API")
    j.add_argument("--host", default="127.0.0.1")
    j.add_argument("--port", type=int, default=8000)
    j.set_defaults(func=_cmd_judge)

    f = sub.add_parser("feed", help="run the Arena feed HTTP server")
    f.add_argument("--host", default="127.0.0.1")
    f.add_argument("--port", type=int, default=8001)
    f.add_argument("--files", nargs="*", default=[], help="JSONL commentary files to serve")
    f.set_defaults(func=_cmd_feed)

    r = sub.add_parser("run-match", help="run a match from a TOML config")
    r.add_argument("--config", required=True)
    r.add_argument("--output", default="./arena_run")
    r.add_argument("--mock", action="store_true", help="allow running with the mock provider")
    r.set_defaults(func=_cmd_run_match)

    d = sub.add_parser("demo", help="run a fast deterministic scripted match (offline)")
    d.add_argument("--output", default="./arena_demo")
    d.set_defaults(func=_cmd_demo)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
