"""Valkey access through redis-py's asyncio client (Valkey speaks the Redis protocol)."""

from typing import cast

from fastapi import Request
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialWithJitterBackoff

from nightloom_api.config import Settings

# Seconds the client may spend opening a single connection.
CONNECT_TIMEOUT_SECONDS = 5.0


def create_client(settings: Settings) -> Redis:
    """Create the client. Its pool opens connections lazily, on first command."""
    return Redis.from_url(
        settings.valkey_url.get_secret_value(),
        socket_connect_timeout=CONNECT_TIMEOUT_SECONDS,
        health_check_interval=30,
        # A few quick retries. redis-py's default (10 retries, backoff capped
        # at 1 s) can outlast the readiness timeout when Valkey is down.
        retry=Retry(ExponentialWithJitterBackoff(cap=0.5, base=0.05), retries=2),
    )


async def ping(client: Redis) -> None:
    """Send ``PING``; raises if Valkey is unreachable or rejects the password."""
    await client.ping()


def get_cache(request: Request) -> Redis:
    """FastAPI dependency: the shared Valkey client."""
    return cast(Redis, request.app.state.infra.cache)
