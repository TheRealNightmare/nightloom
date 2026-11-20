"""How often one user may save layouts, counted in Valkey.

Saving is cheap but triggered by dragging, so each user may save at most
:data:`MAX_WRITES` layouts per window of :data:`WINDOW_SECONDS` (fixed window,
starting with the first save), across cases and sessions. The web client
debounces its saves, so it stays far below this.
"""

import uuid

from redis.asyncio import Redis

MAX_WRITES = 60
WINDOW_SECONDS = 60
KEY_PREFIX = "nightloom:layout:user"


class LayoutRateLimiter:
    def __init__(
        self, cache: Redis, *, max_writes: int = MAX_WRITES, window_seconds: int = WINDOW_SECONDS
    ) -> None:
        self._cache = cache
        self.max_writes = max_writes
        self.window_seconds = window_seconds

    async def hit(self, user_id: uuid.UUID) -> int | None:
        """Count one save; seconds to wait if it is over the limit (then it is not counted)."""
        key = f"{KEY_PREFIX}:{user_id}"
        async with self._cache.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, self.window_seconds, nx=True)
            pipe.ttl(key)
            count, _set, ttl = await pipe.execute()
        if int(count) <= self.max_writes:
            return None
        await self._cache.decr(key)
        # TTL is -1 only if the expiry was somehow lost; wait a full window.
        return int(ttl) if int(ttl) > 0 else self.window_seconds
