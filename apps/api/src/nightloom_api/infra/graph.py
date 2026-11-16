"""Neo4j access through the official async driver."""

from typing import TYPE_CHECKING, cast

from fastapi import Request
from neo4j import AsyncDriver, AsyncGraphDatabase

from nightloom_api.config import Settings

if TYPE_CHECKING:
    from nightloom_api.graph.repository import GraphRepository

# Seconds the driver may spend opening a single connection.
CONNECT_TIMEOUT_SECONDS = 5.0


def create_driver(settings: Settings) -> AsyncDriver:
    """Create the driver. Connections are opened lazily, on first use."""
    return AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
        connection_timeout=CONNECT_TIMEOUT_SECONDS,
    )


async def ping(driver: AsyncDriver) -> None:
    """Run ``RETURN 1``; raises if Neo4j is unreachable or rejects the credentials.

    Uses an auto-commit query rather than ``execute_query``, which retries
    unavailable servers for up to 30 seconds.
    """
    async with driver.session() as session:
        result = await session.run("RETURN 1 AS ok")
        record = await result.single(strict=True)
    if record["ok"] != 1:  # pragma: no cover - would mean a broken server
        raise RuntimeError("unexpected response to RETURN 1")


def get_graph(request: Request) -> AsyncDriver:
    """FastAPI dependency: the shared Neo4j driver."""
    return cast(AsyncDriver, request.app.state.infra.graph)


def get_graph_repository(request: Request) -> "GraphRepository":
    """FastAPI dependency: case graphs on the shared Neo4j driver."""
    from nightloom_api.graph.repository import GraphRepository

    return GraphRepository(get_graph(request))
