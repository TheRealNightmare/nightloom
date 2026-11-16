"""FastAPI application factory."""

from fastapi import FastAPI

from nightloom_api import __version__
from nightloom_api.config import Settings, get_settings
from nightloom_api.logging import configure_logging
from nightloom_api.routers import health


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API application.

    When ``settings`` is given it replaces the environment-derived settings for
    this app, including inside request dependencies.
    """
    resolved = settings if settings is not None else get_settings()
    configure_logging(resolved.log_level)

    app = FastAPI(
        title="Nightloom API",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.settings = resolved
    app.dependency_overrides[get_settings] = lambda: resolved

    app.include_router(health.router, prefix=resolved.api_prefix)
    return app


app = create_app()
