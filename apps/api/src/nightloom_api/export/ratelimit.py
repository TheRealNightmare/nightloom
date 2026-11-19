"""How many exports one user may start, counted in Valkey.

Exports read a whole case graph, so each user may start at most
:data:`MAX_EXPORTS` per window of :data:`WINDOW_SECONDS` (fixed window, starting
with the first export). The counter is keyed by user ID, so the limit applies
across cases and sessions.
"""

import uuid
from dataclasses import dataclass

from redis.asyncio import Redis

MAX_EXPORTS = 10
WINDOW_SECONDS = 10 * 60
KEY_PREFIX = "nightloom:export:user"


@dataclass(frozen=True)
class ExportLimits:
    max_exports: int = MAX_EXPORTS
    window_seconds: int = WINDOW_SECONDS


def _key(user_id: uuid.UUID) -> str:
    return f"{KEY_PREFIX}:{user_id}"


class ExportRateLimiter:
    def __init__(self, cache: Redis, limits: ExportLimits | None = None) -> None:
        self._cache = cache
        self.limits = limits or ExportLimits()

    def _wait(self, ttl: int) -> int:
        # TTL is -1 only if the expiry was somehow lost; wait a full window.
        return ttl if ttl > 0 else self.limits.window_seconds

    async def hit(self, user_id: uuid.UUID) -> int | None:
        """Count one export; seconds to wait if it is over the limit (then it is not counted)."""
        key = _key(user_id)
        async with self._cache.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, self.limits.window_seconds, nx=True)
            pipe.ttl(key)
            count, _set, ttl = await pipe.execute()
        if int(count) <= self.limits.max_exports:
            return None
        await self._cache.decr(key)
        return self._wait(int(ttl))

    async def remaining(self, user_id: uuid.UUID) -> tuple[int, int | None]:
        """Exports left in the current window, and seconds until it ends (None if none started)."""
        key = _key(user_id)
        async with self._cache.pipeline(transaction=False) as pipe:
            pipe.get(key)
            pipe.ttl(key)
            count, ttl = await pipe.execute()
        used = int(count) if count is not None else 0
        left = max(self.limits.max_exports - used, 0)
        return left, (self._wait(int(ttl)) if count is not None else None)
