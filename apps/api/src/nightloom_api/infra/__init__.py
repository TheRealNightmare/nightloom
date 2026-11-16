"""Clients for the API's backing services: PostgreSQL, Neo4j, Valkey and S3 storage.

Every client is lazy: creating :class:`Infra` opens no connections, so the API
starts (and ``/health`` answers) even when every dependency is down. Use
``/ready`` to check that the dependencies are reachable.
"""

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from nightloom_api.config import Settings
from nightloom_api.infra import cache, db, graph, storage
from nightloom_api.infra.storage import Storage

logger = logging.getLogger(__name__)

DependencyName = Literal["postgres", "neo4j", "valkey", "storage"]
Check = Callable[[], Awaitable[None]]


@dataclass(eq=False)
class Infra:
    """The process-wide clients, created in the app lifespan and kept on ``app.state.infra``."""

    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    graph: AsyncDriver
    cache: Redis
    storage: Storage

    def checks(self) -> Mapping[DependencyName, Check]:
        """One zero-argument ping per dependency, for the readiness endpoint."""
        return {
            "postgres": lambda: db.ping(self.engine),
            "neo4j": lambda: graph.ping(self.graph),
            "valkey": lambda: cache.ping(self.cache),
            "storage": lambda: storage.ping(self.storage),
        }

    async def aclose(self) -> None:
        """Close every client. One failing close does not prevent the others."""
        closers: dict[str, Callable[[], Awaitable[None]]] = {
            "postgres": self.engine.dispose,
            "neo4j": self.graph.close,
            "valkey": self.cache.aclose,
            "storage": self.storage.aclose,
        }
        for name, close in closers.items():
            try:
                await close()
            except Exception as exc:
                logger.warning("Closing the %s client failed: %s", name, type(exc).__name__)


def create_infra(settings: Settings) -> Infra:
    """Create every client without connecting to anything."""
    engine = db.create_engine(settings)
    return Infra(
        engine=engine,
        sessionmaker=db.create_sessionmaker(engine),
        graph=graph.create_driver(settings),
        cache=cache.create_client(settings),
        storage=Storage(settings),
    )


__all__ = ["Check", "DependencyName", "Infra", "create_infra"]
