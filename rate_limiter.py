from __future__ import annotations

import asyncio
import time
from collections import defaultdict


class RateLimiter:
    """Per-server async token bucket rate limiter.

    Each server gets its own bucket. Tokens replenish at `rate` per second.
    """

    def __init__(self, rate: float = 1.0):
        """
        Args:
            rate: Maximum queries per second per server.
        """
        self.rate = rate
        self._min_interval = 1.0 / rate if rate > 0 else 0
        self._last_request: dict[str, float] = defaultdict(float)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def acquire(self, server: str) -> float:
        """Wait until a request to `server` is allowed.

        Returns the number of seconds waited.
        """
        if self._min_interval <= 0:
            return 0.0

        async with self._locks[server]:
            now = time.monotonic()
            elapsed = now - self._last_request[server]
            wait_time = self._min_interval - elapsed

            if wait_time > 0:
                await asyncio.sleep(wait_time)
                self._last_request[server] = time.monotonic()
                return wait_time
            else:
                self._last_request[server] = now
                return 0.0
