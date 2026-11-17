"""A token bucket per destination host.

Each host starts with a full bucket of ``requests`` tokens, refilled at
``requests / period_seconds`` tokens per second. A request takes one token,
waiting for it when the bucket is empty, so bursts up to ``requests`` go out
at once and the long-run rate never exceeds the declared limit.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from nightloom_sdk.meta import RateLimit

Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


@dataclass
class _Bucket:
    tokens: float
    updated: float


class HostRateLimiter:
    """Per-host token buckets for one :class:`~nightloom_sdk.meta.RateLimit`.

    ``clock`` and ``sleep`` are injectable for tests.
    """

    def __init__(
        self,
        limit: RateLimit,
        *,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._limit = limit
        self._clock = clock
        self._sleep = sleep
        self._buckets: dict[str, _Bucket] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def limit(self) -> RateLimit:
        return self._limit

    def _refill(self, host: str) -> _Bucket:
        now = self._clock()
        bucket = self._buckets.get(host)
        if bucket is None:
            bucket = self._buckets[host] = _Bucket(float(self._limit.requests), now)
            return bucket
        elapsed = max(0.0, now - bucket.updated)
        bucket.tokens = min(
            float(self._limit.requests), bucket.tokens + elapsed * self._limit.per_second
        )
        bucket.updated = now
        return bucket

    async def acquire(self, host: str) -> float:
        """Take one token for ``host``, waiting if needed. Returns the seconds waited."""
        host = host.lower()
        lock = self._locks.setdefault(host, asyncio.Lock())
        waited = 0.0
        # One waiter per host at a time keeps the order fair.
        async with lock:
            bucket = self._refill(host)
            while bucket.tokens < 1:
                delay = (1 - bucket.tokens) / self._limit.per_second
                await self._sleep(delay)
                waited += delay
                bucket = self._refill(host)
            bucket.tokens -= 1
        return waited


__all__ = ["HostRateLimiter"]
