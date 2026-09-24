"""End-to-end test: a full match driven by MatchRunner with scripted mock
providers (no network, no real LLM). Alpha plays to win; Bravo guesses wrong.
"""

import tempfile
import unittest
from pathlib import Path

from agentarena.agent.provider import LLMResponse, MockProvider, ToolCall
from agentarena.cli import _ALPHA_TREASURE, _BRAVO_TREASURE, _demo_provider_factory
from agentarena.core.types import Side
from agentarena.orchestrator.runner import MatchRunner, RunnerConfig


class TestMatchRunner(unittest.TestCase):
    def test_alpha_wins_deterministically(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = MatchRunner(
                RunnerConfig(
                    root=Path(tmp),
                    provider_factory=_demo_provider_factory,
                    fortify_seconds=0,          # skip fortification for speed
                    max_match_seconds=15,       # backstop
                    rate_limit_per_minute=10,
                    treasures={Side.ALPHA: _ALPHA_TREASURE, Side.BRAVO: _BRAVO_TREASURE},
                    max_agent_iterations=10,
                )
            )
            report = runner.run()

            # Alpha submitted Bravo's treasure correctly and won.
            self.assertEqual(report.match["result"], "alpha_win")
            self.assertEqual(report.match["winner"], "alpha")
            self.assertEqual(report.match["state"], "finished")

            # Alpha made at least one (successful) submission.
            self.assertGreaterEqual(report.attempts["alpha"], 1)

            # Both sides produced commentary for the live feed.
            sides = {e["side"] for e in report.commentary}
            self.assertIn("alpha", sides)
            self.assertIn("bravo", sides)

            # The report was persisted to disk.
            saved = list(Path(tmp).glob("reports/*.json"))
            self.assertEqual(len(saved), 1)

    def test_draw_when_nobody_wins(self):
        def passive_factory(side, phase):
            return MockProvider()  # immediately finishes; never submits

        with tempfile.TemporaryDirectory() as tmp:
            runner = MatchRunner(
                RunnerConfig(
                    root=Path(tmp),
                    provider_factory=passive_factory,
                    fortify_seconds=0,
                    max_match_seconds=0.3,  # short cap -> draw
                    rate_limit_per_minute=10,
                    treasures={Side.ALPHA: _ALPHA_TREASURE, Side.BRAVO: _BRAVO_TREASURE},
                    max_agent_iterations=3,
                )
            )
            report = runner.run()
            self.assertEqual(report.match["result"], "draw")
            self.assertIsNone(report.match["winner"])

    def test_multi_treasure_alpha_must_steal_all(self):
        """Two treasures per side: Alpha wins only after stealing BOTH of Bravo's."""
        bravo_treasures = ["b" * 128, "2" * 128]
        alpha_treasures = ["a" * 128, "1" * 128]

        def factory(side, phase):
            if phase == "fortify":
                return MockProvider()  # skip hardening; finish immediately
            if side is Side.ALPHA:
                return MockProvider([
                    LLMResponse(tool_calls=[
                        ToolCall(id="a1", name="submit_code", arguments={"guess": bravo_treasures[0]}),
                    ]),
                    LLMResponse(tool_calls=[
                        ToolCall(id="a2", name="submit_code", arguments={"guess": bravo_treasures[1]}),
                    ]),
                ])
            return MockProvider()  # bravo never attacks

        with tempfile.TemporaryDirectory() as tmp:
            runner = MatchRunner(
                RunnerConfig(
                    root=Path(tmp),
                    provider_factory=factory,
                    fortify_seconds=0,
                    max_match_seconds=15,
                    rate_limit_per_minute=10,
                    treasures={Side.ALPHA: alpha_treasures, Side.BRAVO: bravo_treasures},
                    max_agent_iterations=10,
                )
            )
            report = runner.run()

            self.assertEqual(report.match["result"], "alpha_win")
            self.assertEqual(report.match["winner"], "alpha")
            # both of alpha's submissions were needed and counted
            self.assertEqual(report.attempts["alpha"], 2)
            # two treasure files were provisioned per side
            for side in ("alpha", "bravo"):
                paths = report.sides[side]["treasure_paths"]
                self.assertEqual(len(paths), 2)
                for p in paths:
                    self.assertTrue(Path(p).is_file())

    def test_treasures_per_side_generates_files(self):
        """Without pre-seeded treasures, `treasures_per_side` files are created."""
        def passive_factory(side, phase):
            return MockProvider()

        with tempfile.TemporaryDirectory() as tmp:
            runner = MatchRunner(
                RunnerConfig(
                    root=Path(tmp),
                    provider_factory=passive_factory,
                    fortify_seconds=0,
                    max_match_seconds=0.3,
                    rate_limit_per_minute=10,
                    treasures_per_side=3,
                    max_agent_iterations=2,
                )
            )
            report = runner.run()
            self.assertEqual(report.match["result"], "draw")  # nobody attacked
            for side in ("alpha", "bravo"):
                paths = report.sides[side]["treasure_paths"]
                self.assertEqual(len(paths), 3)
                self.assertTrue(paths[0].endswith("treasure.txt"))
                self.assertTrue(paths[1].endswith("treasure_2.txt"))
                self.assertTrue(paths[2].endswith("treasure_3.txt"))


if __name__ == "__main__":
    unittest.main()
