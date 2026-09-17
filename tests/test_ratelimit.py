import unittest

from agentarena.judge.ratelimit import SlidingWindowRateLimiter


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class TestRateLimiter(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.rl = SlidingWindowRateLimiter(limit=3, window_seconds=60, clock=self.clock)

    def test_allows_up_to_limit(self):
        for expected_remaining in (2, 1, 0):
            d = self.rl.allow("alpha")
            self.assertTrue(d.allowed)
            self.assertEqual(d.remaining, expected_remaining)

    def test_denies_after_limit(self):
        for _ in range(3):
            self.rl.allow("alpha")
        d = self.rl.allow("alpha")
        self.assertFalse(d.allowed)
        self.assertEqual(d.remaining, 0)
        self.assertGreater(d.retry_after_seconds, 0)

    def test_check_does_not_consume(self):
        self.rl.allow("alpha")
        d1 = self.rl.check("alpha")
        d2 = self.rl.check("alpha")
        self.assertTrue(d1.allowed)
        self.assertEqual(d1.remaining, d2.remaining)

    def test_window_slides(self):
        for _ in range(3):
            self.rl.allow("alpha")
        self.assertFalse(self.rl.allow("alpha").allowed)
        self.clock.advance(61)
        self.assertTrue(self.rl.allow("alpha").allowed)

    def test_independent_keys(self):
        for _ in range(3):
            self.rl.allow("alpha")
        self.assertFalse(self.rl.allow("alpha").allowed)
        self.assertTrue(self.rl.allow("bravo").allowed)

    def test_invalid_args(self):
        with self.assertRaises(ValueError):
            SlidingWindowRateLimiter(limit=0, window_seconds=60)
        with self.assertRaises(ValueError):
            SlidingWindowRateLimiter(limit=1, window_seconds=0)


if __name__ == "__main__":
    unittest.main()
