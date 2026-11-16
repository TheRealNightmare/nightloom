"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from nightloom_api import __version__
from nightloom_api.config import Settings, get_settings
from nightloom_api.infra import create_infra
from nightloom_api.logging import configure_logging
from nightloom_api.routers import health, readiness


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API application.

    When ``settings`` is given it replaces the environment-derived settings for
    this app, including inside request dependencies.
    """
    resolved = settings if settings is not None else get_settings()
    configure_logging(resolved.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Clients are lazy: nothing connects here, so startup never depends on
        # PostgreSQL, Neo4j, Valkey or storage being up.
        infra = create_infra(resolved)
        app.state.infra = infra
        try:
            yield
        finally:
            await infra.aclose()

    app = FastAPI(
        title="Nightloom API",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.dependency_overrides[get_settings] = lambda: resolved

    app.include_router(health.router, prefix=resolved.api_prefix)
    app.include_router(readiness.router, prefix=resolved.api_prefix)
    return app


app = create_app()
