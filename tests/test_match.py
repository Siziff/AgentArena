import time
import unittest

from agentarena.core.match import IllegalTransitionError, Match
from agentarena.core.types import MatchResult, MatchState, Side


def battling_match(**kwargs) -> Match:
    m = Match(**kwargs)
    m.transition_to(MatchState.PROVISIONING)
    m.transition_to(MatchState.FORTIFYING)
    m.transition_to(MatchState.BATTLING)
    return m


class TestTransitions(unittest.TestCase):
    def test_happy_path(self):
        m = Match()
        self.assertEqual(m.state, MatchState.CREATED)
        m.transition_to(MatchState.PROVISIONING)
        m.transition_to(MatchState.FORTIFYING)
        m.transition_to(MatchState.BATTLING)
        m.transition_to(MatchState.FINISHED)
        self.assertTrue(m.is_terminal)

    def test_illegal_transition_raises(self):
        m = Match()
        with self.assertRaises(IllegalTransitionError):
            m.transition_to(MatchState.BATTLING)  # skipping states

    def test_terminal_is_sticky(self):
        m = battling_match()
        m.transition_to(MatchState.FINISHED)
        with self.assertRaises(IllegalTransitionError):
            m.transition_to(MatchState.BATTLING)


class TestOutcome(unittest.TestCase):
    def test_declare_winner(self):
        m = battling_match()
        m.declare_winner(Side.ALPHA)
        self.assertEqual(m.result, MatchResult.ALPHA_WIN)
        self.assertEqual(m.winner, Side.ALPHA)
        self.assertEqual(m.state, MatchState.FINISHED)

    def test_declare_winner_requires_battle(self):
        m = Match()
        m.transition_to(MatchState.PROVISIONING)
        m.transition_to(MatchState.FORTIFYING)
        with self.assertRaises(IllegalTransitionError):
            m.declare_winner(Side.ALPHA)

    def test_declare_draw(self):
        m = battling_match()
        m.declare_draw()
        self.assertEqual(m.result, MatchResult.DRAW)
        self.assertIsNone(m.winner)

    def test_abort(self):
        m = Match()
        m.abort()
        self.assertEqual(m.result, MatchResult.ABORTED)
        self.assertEqual(m.state, MatchState.ABORTED)


class TestTiming(unittest.TestCase):
    def test_zero_fortify_starts_battle_immediately(self):
        m = Match(fortify_seconds=0)
        m.transition_to(MatchState.PROVISIONING)
        m.transition_to(MatchState.FORTIFYING)
        self.assertTrue(m.battle_should_start())
        self.assertEqual(m.fortify_remaining_seconds(), 0.0)

    def test_time_cap(self):
        m = Match(max_match_seconds=0.05)
        m.transition_to(MatchState.PROVISIONING)
        m.transition_to(MatchState.FORTIFYING)
        self.assertFalse(m.time_cap_reached())
        time.sleep(0.06)
        self.assertTrue(m.time_cap_reached())

    def test_no_cap(self):
        m = Match(max_match_seconds=0)
        m.transition_to(MatchState.PROVISIONING)
        m.transition_to(MatchState.FORTIFYING)
        self.assertFalse(m.time_cap_reached())


if __name__ == "__main__":
    unittest.main()
