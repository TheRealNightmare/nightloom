"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from nightloom_api import __version__
from nightloom_api.auth import passwords
from nightloom_api.config import Settings, get_settings
from nightloom_api.infra import create_infra
from nightloom_api.logging import configure_logging
from nightloom_api.request_id import RequestIdMiddleware
from nightloom_api.routers import audit, auth, cases, graph, health, readiness, schema, users


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API application.

    When ``settings`` is given it replaces the environment-derived settings for
    this app, including inside request dependencies.
    """
    resolved = settings if settings is not None else get_settings()
    configure_logging(resolved.log_level)
    passwords.set_hash_concurrency(resolved.password_hash_concurrency)

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

    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    # Outermost of the app's own middleware: the ID is set before any handler,
    # dependency or log record needs it, and every response carries it.
    app.add_middleware(RequestIdMiddleware)

    app.include_router(health.router, prefix=resolved.api_prefix)
    app.include_router(readiness.router, prefix=resolved.api_prefix)
    app.include_router(auth.router, prefix=resolved.api_prefix)
    app.include_router(users.router, prefix=resolved.api_prefix)
    app.include_router(audit.router, prefix=resolved.api_prefix)
    app.include_router(cases.router, prefix=resolved.api_prefix)
    app.include_router(graph.router, prefix=resolved.api_prefix)
    app.include_router(schema.router, prefix=resolved.api_prefix)
    return app


async def _validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """422 like FastAPI's default, minus the echoed ``input`` (it may contain a password)."""
    assert isinstance(exc, RequestValidationError)  # noqa: S101 - registered for this type only
    errors = [
        {key: value for key, value in error.items() if key in {"type", "loc", "msg"}}
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=jsonable_encoder({"detail": errors}),
    )


app = create_app()
