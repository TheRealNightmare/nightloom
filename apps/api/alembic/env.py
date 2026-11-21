"""Alembic environment: async migrations against ``NIGHTLOOM_DATABASE_URL``."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

# Registers audit_events, users, sessions, cases, case_members, jobs, graph_layouts and
# saved_views on Base.metadata.
import nightloom_api.audit.models
import nightloom_api.auth.models
import nightloom_api.cases.models
import nightloom_api.jobs.models
import nightloom_api.jobs.result_cache
import nightloom_api.layouts.models
import nightloom_api.scans.models
import nightloom_api.views.models  # noqa: F401
from nightloom_api.config import get_settings
from nightloom_api.infra.db import Base

# Import model modules here as they are added, so autogenerate sees their
# tables on Base.metadata (like nightloom_api.auth.models above).

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    # Callers that drive Alembic from Python (the test suite) can pass a URL
    # explicitly; otherwise it comes from NIGHTLOOM_DATABASE_URL.
    url = config.attributes.get("database_url")
    if isinstance(url, str):
        return url
    return get_settings().database_url.get_secret_value()


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of connecting (``alembic upgrade --sql``)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Connect with the asyncpg driver and run migrations in one transaction."""
    engine = create_async_engine(_database_url(), poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
