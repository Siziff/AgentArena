"""A thread-safe sliding-window rate limiter.

Each key (e.g. a side) may make at most `limit` calls within any trailing
`window_seconds`. Uses a monotonic clock so wall-clock changes do not affect
limiting. The clock is injectable for deterministic tests.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    remaining: int  # calls still allowed in the current window after this one
    retry_after_seconds: float  # 0.0 if allowed


class SlidingWindowRateLimiter:
    def __init__(
        self,
        limit: int,
        window_seconds: float,
        clock=time.monotonic,
    ) -> None:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        self.limit = limit
        self.window_seconds = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _evict(self, dq: deque[float], now: float) -> None:
        cutoff = now - self.window_seconds
        while dq and dq[0] <= cutoff:
            dq.popleft()

    def check(self, key: str) -> RateDecision:
        """Return the decision for `key` without consuming a slot."""
        with self._lock:
            now = self._clock()
            dq = self._hits.setdefault(key, deque())
            self._evict(dq, now)
            return self._decide(dq, now, consume=False)

    def allow(self, key: str) -> RateDecision:
        """Attempt to consume a slot for `key`."""
        with self._lock:
            now = self._clock()
            dq = self._hits.setdefault(key, deque())
            self._evict(dq, now)
            return self._decide(dq, now, consume=True)

    def _decide(self, dq: deque[float], now: float, consume: bool) -> RateDecision:
        if len(dq) < self.limit:
            if consume:
                dq.append(now)
            # len(dq) already reflects the consumed slot (if any).
            remaining = self.limit - len(dq)
            return RateDecision(True, max(0, remaining), 0.0)
        # Window full: the oldest hit determines when a slot frees up.
        retry_after = (dq[0] + self.window_seconds) - now
        return RateDecision(False, 0, max(0.0, retry_after))

    def usage(self, key: str) -> int:
        """Current number of hits in the window for `key` (does not consume)."""
        with self._lock:
            now = self._clock()
            dq = self._hits.setdefault(key, deque())
            self._evict(dq, now)
            return len(dq)

    def reset(self, key: str | None = None) -> None:
        """Clear state for one key or for all keys."""
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)
