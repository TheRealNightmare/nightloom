"""Brute-force protection for sign-in, with counters in Valkey.

Failed attempts are counted per email and per client IP in fixed windows of
:data:`WINDOW_SECONDS`. Once either counter reaches its limit, further
attempts are refused (HTTP 429) until the window ends, without checking the
password. Emails are hashed before they become part of a key, so Valkey never
holds addresses.
"""

import hashlib
from dataclasses import dataclass

from redis.asyncio import Redis

WINDOW_SECONDS = 15 * 60
MAX_FAILURES_PER_EMAIL = 5
MAX_FAILURES_PER_IP = 20

KEY_PREFIX = "nightloom:auth:login-failures"


@dataclass(frozen=True)
class Limits:
    """Failure limits per window; overridable for tests."""

    per_email: int = MAX_FAILURES_PER_EMAIL
    per_ip: int = MAX_FAILURES_PER_IP
    window_seconds: int = WINDOW_SECONDS


def _email_key(email: str) -> str:
    return f"{KEY_PREFIX}:email:{hashlib.sha256(email.encode()).hexdigest()}"


def _ip_key(ip: str) -> str:
    return f"{KEY_PREFIX}:ip:{ip}"


class LoginRateLimiter:
    """Counts failed sign-ins per email and per IP."""

    def __init__(self, cache: Redis, limits: Limits | None = None) -> None:
        self._cache = cache
        self._limits = limits or Limits()

    def _keys(self, email: str, ip: str | None) -> list[tuple[str, int]]:
        keys = [(_email_key(email), self._limits.per_email)]
        if ip:
            keys.append((_ip_key(ip), self._limits.per_ip))
        return keys

    async def retry_after(self, email: str, ip: str | None) -> int | None:
        """Seconds until another attempt is allowed, or ``None`` if not limited."""
        keys = self._keys(email, ip)
        async with self._cache.pipeline(transaction=False) as pipe:
            for key, _limit in keys:
                pipe.get(key)
                pipe.ttl(key)
            replies = await pipe.execute()

        waits: list[int] = []
        for index, (_key, limit) in enumerate(keys):
            count, ttl = replies[2 * index], replies[2 * index + 1]
            if count is not None and int(count) >= limit:
                # TTL is -1 only if the expiry was somehow lost; wait a full window.
                waits.append(int(ttl) if ttl > 0 else self._limits.window_seconds)
        return max(waits) if waits else None

    async def record_failure(self, email: str, ip: str | None) -> None:
        """Count one failed attempt; the window starts with the first failure."""
        async with self._cache.pipeline(transaction=True) as pipe:
            for key, _limit in self._keys(email, ip):
                pipe.incr(key)
                pipe.expire(key, self._limits.window_seconds, nx=True)
            await pipe.execute()

    async def reset_email(self, email: str) -> None:
        """Forget the email's failures after a successful sign-in (the IP counter stays)."""
        await self._cache.delete(_email_key(email))
