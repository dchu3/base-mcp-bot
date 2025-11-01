"""Simple per-user rate limiting."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Deque, Dict


class RateLimiter:
    """Track per-user events within a rolling window."""

    def __init__(self, limit_per_minute: int) -> None:
        self.limit = limit_per_minute
        self._events: Dict[int, Deque[float]] = defaultdict(deque)

    def allow(self, user_id: int) -> bool:
        """Record an event and return whether it stays under limit."""
        now = time.time()
        window_start = now - 60
        events = self._events[user_id]

        while events and events[0] < window_start:
            events.popleft()

        if len(events) >= self.limit:
            return False

        events.append(now)
        return True
