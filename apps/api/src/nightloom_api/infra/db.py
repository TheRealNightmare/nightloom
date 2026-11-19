"""PostgreSQL access through async SQLAlchemy (asyncpg driver)."""

from collections.abc import AsyncIterator
from typing import Any, cast

from fastapi import Request
from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from nightloom_api.config import Settings

# Deterministic constraint and index names, so Alembic autogenerate produces
# stable migrations that can later drop or alter the same objects.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Seconds asyncpg may spend opening a single connection.
CONNECT_TIMEOUT_SECONDS = 5.0


class Base(DeclarativeBase):
    """Declarative base for every ORM model; Alembic's ``target_metadata``."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def create_engine(
    settings: Settings, *, statement_timeout_seconds: float | None = None
) -> AsyncEngine:
    """Create the async engine. No connection is opened until first use.

    ``statement_timeout_seconds`` makes PostgreSQL cancel any statement, or
    wait for a lock, that takes longer (``statement_timeout`` and
    ``lock_timeout``), so a session using it cannot hang on the database.
    """
    connect_args: dict[str, Any] = {"timeout": CONNECT_TIMEOUT_SECONDS}
    if statement_timeout_seconds is not None:
        milliseconds = str(max(1, round(statement_timeout_seconds * 1000)))
        connect_args["server_settings"] = {
            "statement_timeout": milliseconds,
            "lock_timeout": milliseconds,
        }
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        connect_args=connect_args,
    )


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory; objects stay usable after commit."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def ping(engine: AsyncEngine) -> None:
    """Run ``SELECT 1``; raises if PostgreSQL is unreachable."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


def get_sessionmaker(request: Request) -> async_sessionmaker[AsyncSession]:
    """FastAPI dependency: the session factory, for work in a transaction of its own."""
    return cast(async_sessionmaker[AsyncSession], request.app.state.infra.sessionmaker)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, closed afterwards."""
    async with request.app.state.infra.sessionmaker() as session:
        yield session
