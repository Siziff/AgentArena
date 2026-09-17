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


class TestStatus(unittest.TestCase):
    def test_status_shape(self):
        core = make_core()
        status = core.status()
        self.assertEqual(status["match_status"], "ongoing")
        self.assertIn("alpha", status["attempts"])
        self.assertEqual(status["limits"]["per_window"], 10)


if __name__ == "__main__":
    unittest.main()
