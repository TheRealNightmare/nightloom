"""S3-compatible object storage through aioboto3 (SeaweedFS by default)."""

import asyncio
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, cast

import aioboto3
from botocore.config import Config
from fastapi import Request

from nightloom_api.config import Settings

if TYPE_CHECKING:
    # Stubs only (dev dependency); not installed in the runtime image.
    from types_aiobotocore_s3 import S3Client

# Seconds botocore may spend connecting / waiting for a response.
CONNECT_TIMEOUT_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 30.0


class Storage:
    """Owns one S3 client for the configured endpoint and bucket.

    The client is created on first use rather than at startup, and
    :meth:`aclose` releases its HTTP connection pool.
    """

    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.s3_bucket
        self._settings = settings
        self._session = aioboto3.Session()
        self._config = Config(
            region_name=settings.s3_region,
            # SeaweedFS (like most self-hosted S3 servers) needs path-style
            # URLs: http://host:8333/<bucket>/<key>, not <bucket>.host.
            s3={"addressing_style": "path"},
            signature_version="s3v4",
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
            read_timeout=READ_TIMEOUT_SECONDS,
            retries={"mode": "standard", "max_attempts": 2},
        )
        self._stack = AsyncExitStack()
        self._client: S3Client | None = None
        self._lock = asyncio.Lock()

    async def client(self) -> "S3Client":
        """Return the shared S3 client, creating it on first call."""
        if self._client is not None:
            return self._client
        async with self._lock:
            # Another task may have created it while this one waited.
            if self._client is not None:
                return self._client
            client: S3Client = await self._stack.enter_async_context(
                self._session.client(
                    "s3",
                    endpoint_url=self._settings.s3_endpoint,
                    aws_access_key_id=self._settings.s3_access_key.get_secret_value(),
                    aws_secret_access_key=self._settings.s3_secret_key.get_secret_value(),
                    config=self._config,
                )
            )
            self._client = client
            return client

    async def aclose(self) -> None:
        """Close the client, if one was created."""
        self._client = None
        await self._stack.aclose()


async def ping(storage: Storage) -> None:
    """``HeadBucket`` on the configured bucket; raises if it is missing or unreachable."""
    client = await storage.client()
    await client.head_bucket(Bucket=storage.bucket)


def get_storage(request: Request) -> Storage:
    """FastAPI dependency: the shared object storage."""
    return cast(Storage, request.app.state.infra.storage)
