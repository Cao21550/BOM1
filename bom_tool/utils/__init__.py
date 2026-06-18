"""Shared utilities."""

from __future__ import annotations

import asyncio
import random


class AsyncRateLimiter:
    """Async-friendly rate limiter with per-instance lock and interval."""

    def __init__(self, min_interval: float = 3.0, jitter: float = 0.3) -> None:
        self._min_interval = min_interval
        self._jitter = jitter
        self._lock = asyncio.Lock()
        self._next_at = 0.0

    async def wait(self) -> None:
        loop = asyncio.get_running_loop()
        now = loop.time()
        async with self._lock:
            wait = max(0.0, self._next_at - now)
            self._next_at = max(now, self._next_at) + (
                self._min_interval + random.uniform(0, self._jitter)
            )
        if wait > 0:
            await asyncio.sleep(wait)

    def reset(self) -> None:
        self._next_at = 0.0

    @property
    def min_interval(self) -> float:
        return self._min_interval
