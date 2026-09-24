import unittest

from agentarena.core.types import Side, Verdict
from agentarena.judge.core import JudgeCore, OutcomeStatus

ALPHA_T = "a" * 128
BRAVO_T = "b" * 128


def make_core(rate=10) -> JudgeCore:
    core = JudgeCore(rate_limit_per_minute=rate, window_seconds=60)
    core.register_secret(Side.ALPHA, ALPHA_T)
    core.register_secret(Side.BRAVO, BRAVO_T)
    return core


class TestVerdicts(unittest.TestCase):
    def test_correct_submission_wins(self):
        core = make_core()
        out = core.submit("alpha", BRAVO_T)  # alpha guesses bravo's treasure
        self.assertEqual(out.status, OutcomeStatus.EVALUATED)
        self.assertEqual(out.verdict, Verdict.CORRECT)
        self.assertEqual(out.match_status, "finished")
        self.assertEqual(out.winner, Side.ALPHA)
        self.assertTrue(core.is_finished)

    def test_incorrect_submission(self):
        core = make_core()
        out = core.submit("alpha", "c" * 128)
        self.assertEqual(out.verdict, Verdict.INCORRECT)
        self.assertEqual(out.match_status, "ongoing")

    def test_own_treasure_is_not_a_win(self):
        core = make_core()
        # alpha submits its OWN treasure -> checked against bravo's -> incorrect
        out = core.submit("alpha", ALPHA_T)
        self.assertEqual(out.verdict, Verdict.INCORRECT)

    def test_first_correct_wins_then_409(self):
        core = make_core()
        core.submit("alpha", BRAVO_T)
        out = core.submit("bravo", ALPHA_T)
        self.assertEqual(out.status, OutcomeStatus.MATCH_FINISHED)
        self.assertEqual(core.winner, Side.ALPHA)  # unchanged


class TestValidation(unittest.TestCase):
    def test_unknown_side(self):
        core = make_core()
        out = core.submit("gamma", "a" * 128)
        self.assertEqual(out.status, OutcomeStatus.INVALID)

    def test_bad_guess_format(self):
        core = make_core()
        out = core.submit("alpha", "too-short")
        self.assertEqual(out.status, OutcomeStatus.INVALID)
        # invalid guesses are not counted as attempts
        self.assertEqual(core.status()["attempts"]["alpha"], 0)


class TestRateLimit(unittest.TestCase):
    def test_rate_limit_enforced(self):
        core = make_core(rate=2)
        core.submit("alpha", "c" * 128)
        core.submit("alpha", "d" * 128)
        out = core.submit("alpha", "e" * 128)
        self.assertEqual(out.status, OutcomeStatus.RATE_LIMITED)
        self.assertGreater(out.retry_after_seconds, 0)

    def test_attempts_count_only_evaluated(self):
        core = make_core(rate=1)
        core.submit("alpha", "c" * 128)  # evaluated
        core.submit("alpha", "d" * 128)  # rate limited
        self.assertEqual(core.status()["attempts"]["alpha"], 1)

    def test_sides_are_independent(self):
        core = make_core(rate=1)
        core.submit("alpha", "c" * 128)
        out = core.submit("bravo", "d" * 128)
        self.assertEqual(out.status, OutcomeStatus.EVALUATED)


class TestMultiTreasure(unittest.TestCase):
    """With several treasures per side, ALL must be stolen to win."""

    def make_multi(self, rate=10) -> JudgeCore:
        core = JudgeCore(rate_limit_per_minute=rate, window_seconds=60)
        core.register_secret(Side.ALPHA, "a" * 128)
        core.register_secret(Side.ALPHA, "1" * 128)
        core.register_secret(Side.BRAVO, "b" * 128)
        core.register_secret(Side.BRAVO, "2" * 128)
        return core

    def test_first_treasure_stolen_but_match_ongoing(self):
        core = self.make_multi()
        out = core.submit("alpha", "b" * 128)
        self.assertEqual(out.verdict, Verdict.CORRECT)
        self.assertEqual(out.match_status, "ongoing")
        self.assertFalse(core.is_finished)
        self.assertIn("1/2", out.detail)

    def test_stealing_all_treasures_wins(self):
        core = self.make_multi()
        core.submit("alpha", "b" * 128)
        out = core.submit("alpha", "2" * 128)
        self.assertEqual(out.verdict, Verdict.CORRECT)
        self.assertEqual(out.match_status, "finished")
        self.assertEqual(out.winner, Side.ALPHA)
        self.assertIn("2/2", out.detail)
        # later submissions are refused
        out = core.submit("bravo", "a" * 128)
        self.assertEqual(out.status, OutcomeStatus.MATCH_FINISHED)

    def test_opponents_progress_is_independent(self):
        core = self.make_multi()
        core.submit("alpha", "b" * 128)   # alpha steals 1 of bravo's
        core.submit("bravo", "a" * 128)   # bravo steals 1 of alpha's
        core.submit("bravo", "1" * 128)   # bravo steals the last one -> wins
        self.assertTrue(core.is_finished)
        self.assertEqual(core.winner, Side.BRAVO)

    def test_resubmitting_stolen_treasure_is_incorrect(self):
        core = self.make_multi()
        core.submit("alpha", "b" * 128)
        out = core.submit("alpha", "b" * 128)  # already stolen
        self.assertEqual(out.verdict, Verdict.INCORRECT)
        self.assertEqual(out.detail, "treasure already stolen")
        self.assertFalse(core.is_finished)

    def test_status_reports_progress(self):
        core = self.make_multi()
        core.submit("alpha", "b" * 128)
        status = core.status()
        self.assertEqual(status["progress"]["alpha"], {"stolen": 1, "total": 2})
        self.assertEqual(status["progress"]["bravo"], {"stolen": 0, "total": 2})

    def test_duplicate_registration_counts_once(self):
        core = JudgeCore(rate_limit_per_minute=10, window_seconds=60)
        core.register_secret(Side.ALPHA, "a" * 128)
        core.register_secret(Side.ALPHA, "a" * 128)  # same treasure twice
        core.register_secret(Side.BRAVO, "b" * 128)
        self.assertEqual(core.status()["progress"]["alpha"]["total"], 1)
        out = core.submit("alpha", "b" * 128)
        self.assertEqual(out.match_status, "finished")  # single treasure -> instant win


class TestStatus(unittest.TestCase):
    def test_status_shape(self):
        core = make_core()
        status = core.status()
        self.assertEqual(status["match_status"], "ongoing")
        self.assertIn("alpha", status["attempts"])
        self.assertEqual(status["limits"]["per_window"], 10)

    def test_status_window_usage(self):
        core = make_core(rate=10)
        core.submit("alpha", "c" * 128)
        core.submit("alpha", "d" * 128)
        status = core.status()
        self.assertEqual(status["window"]["alpha"]["used"], 2)
        self.assertEqual(status["window"]["alpha"]["limit"], 10)
        self.assertEqual(status["window"]["alpha"]["verdicts"], ["incorrect", "incorrect"])
        self.assertEqual(status["window"]["bravo"]["used"], 0)

    def test_status_window_verdicts_end_at_first_correct(self):
        core = make_core(rate=10)
        core.submit("alpha", "c" * 128)   # incorrect
        core.submit("alpha", BRAVO_T)      # correct -> wins, match ends
        status = core.status()
        self.assertEqual(
            status["window"]["alpha"]["verdicts"], ["incorrect", "correct"]
        )
        self.assertEqual(status["match_status"], "finished")
        self.assertEqual(status["winner"], "alpha")


if __name__ == "__main__":
    unittest.main()
