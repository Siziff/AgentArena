"""End-to-end test: a full match driven by MatchRunner with scripted mock
providers (no network, no real LLM). Alpha plays to win; Bravo guesses wrong.
"""

import tempfile
import unittest
from pathlib import Path

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
        from agentarena.agent.provider import MockProvider

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


if __name__ == "__main__":
    unittest.main()
