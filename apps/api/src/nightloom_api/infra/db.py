"""PostgreSQL access through async SQLAlchemy (asyncpg driver)."""

from collections.abc import AsyncIterator

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


def create_engine(settings: Settings) -> AsyncEngine:
    """Create the async engine. No connection is opened until first use."""
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        connect_args={"timeout": CONNECT_TIMEOUT_SECONDS},
    )


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory; objects stay usable after commit."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def ping(engine: AsyncEngine) -> None:
    """Run ``SELECT 1``; raises if PostgreSQL is unreachable."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, closed afterwards."""
    async with request.app.state.infra.sessionmaker() as session:
        yield session
