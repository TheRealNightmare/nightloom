"""Reusing a Shuttle's recent results: the result cache.

When a passive Shuttle with a ``cache_ttl_seconds`` runs successfully, the
worker stores what it yielded (its **raw** findings and notes, before any
check that depends on the case) under a key made of:

- the **scope**: the case (``NIGHTLOOM_RESULT_CACHE_SCOPE=case``, the
  default) or the whole instance (``instance``);
- the Shuttle's **name and version** (a new version never reuses an older
  one's results);
- the input entity's canonical **key**, and the run's options (none yet).

A later job for the same key, while the entry has not expired, replays those
findings through the normal checks and writes instead of running the
Shuttle (unless the user asked for a fresh run). So everything that depends
on the case, such as the person gate, is applied again on every replay, and
provenance keeps the time the source was actually asked (``fetched_at``).

Why per case by default: a shared entry's ``fetched_at`` would tell one
case's members that someone else on this instance looked the same target up.
``instance`` is for a single user or a trusted team; ``off`` disables it.

Entries larger than ``NIGHTLOOM_RESULT_CACHE_MAX_ENTRY_BYTES`` are not
stored; the worker purges expired entries and keeps the total under
``NIGHTLOOM_RESULT_CACHE_MAX_BYTES`` (least recently used first).
"""

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    delete,
    func,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from nightloom_api.infra.db import Base
from nightloom_api.jobs.models import Job, JobStatus

# How far back the admin statistics count jobs.
STATS_WINDOW = timedelta(days=30)


class CacheScope(StrEnum):
    """Who shares an entry (``NIGHTLOOM_RESULT_CACHE_SCOPE``)."""

    CASE = "case"
    INSTANCE = "instance"
    OFF = "off"


class ResultCacheEntry(Base):
    """A successful run's raw findings, reusable until ``expires_at``."""

    __tablename__ = "result_cache"
    __table_args__ = (
        CheckConstraint("scope IN ('case', 'instance')", name="scope"),
        CheckConstraint("(scope = 'case') = (case_id IS NOT NULL)", name="case_id_matches_scope"),
        CheckConstraint("size_bytes >= 0 AND finding_count >= 0 AND hits >= 0", name="counts"),
        Index("ix_result_cache_expires_at", "expires_at"),
        Index("ix_result_cache_shuttle_name", "shuttle_name"),
    )

    # SHA-256 (hex) of the scope, the Shuttle's name and version, the entity
    # key and the options; see cache_key().
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    scope: Mapped[str] = mapped_column(Text)
    # Set for case-scoped entries, which are deleted with their case.
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), index=True
    )
    shuttle_name: Mapped[str] = mapped_column(Text)
    shuttle_version: Mapped[str] = mapped_column(Text)
    entity_key: Mapped[str] = mapped_column(Text)
    # What the Shuttle yielded, in order, as JSON: {"kind": "entity" | "relationship", ...}.
    findings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    notes: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    finding_count: Mapped[int] = mapped_column(Integer)
    size_bytes: Mapped[int] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    hits: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        # No entity key: it can be personal data, and reprs end up in logs.
        return f"ResultCacheEntry(shuttle={self.shuttle_name}@{self.shuttle_version})"


@dataclass(frozen=True)
class CachedResult:
    findings: list[dict[str, Any]]
    notes: list[str]
    fetched_at: datetime


def cache_key(
    *,
    scope: CacheScope,
    case_id: uuid.UUID,
    shuttle_name: str,
    shuttle_version: str,
    entity_key: str,
    options: Mapping[str, Any] | None = None,
) -> str:
    """The entry's key. Options are normalized (sorted keys), so their order never matters."""
    if scope == CacheScope.OFF:
        raise ValueError("The result cache is off.")
    owner = f"case:{case_id}" if scope == CacheScope.CASE else "instance"
    payload = json.dumps(
        [owner, shuttle_name, shuttle_version, entity_key, dict(options or {})],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def encoded_size(findings: Sequence[Mapping[str, Any]], notes: Sequence[str]) -> int:
    """Bytes of the entry's findings and notes as compact JSON."""
    return len(
        json.dumps(
            [list(findings), list(notes)], separators=(",", ":"), ensure_ascii=False
        ).encode()
    )


async def lookup(
    db: AsyncSession, *, key: str, scope: CacheScope, now: datetime
) -> CachedResult | None:
    """The valid entry for ``key`` (counting the hit), or None. The caller commits."""
    row = (
        await db.execute(
            update(ResultCacheEntry)
            .where(
                ResultCacheEntry.key == key,
                ResultCacheEntry.scope == scope.value,
                ResultCacheEntry.expires_at > now,
            )
            .values(hits=ResultCacheEntry.hits + 1, last_hit_at=now)
            .returning(
                ResultCacheEntry.findings, ResultCacheEntry.notes, ResultCacheEntry.fetched_at
            )
        )
    ).one_or_none()
    if row is None:
        return None
    findings, notes, fetched_at = row
    return CachedResult(findings=list(findings), notes=list(notes or []), fetched_at=fetched_at)


async def store(
    db: AsyncSession,
    *,
    key: str,
    scope: CacheScope,
    case_id: uuid.UUID,
    shuttle_name: str,
    shuttle_version: str,
    entity_key: str,
    findings: Sequence[Mapping[str, Any]],
    notes: Sequence[str],
    fetched_at: datetime,
    ttl_seconds: int,
    max_entry_bytes: int,
) -> bool:
    """Store or replace an entry; False (nothing stored) if it is too large. The caller commits."""
    size = encoded_size(findings, notes)
    if size > max_entry_bytes:
        return False
    values = {
        "key": key,
        "scope": scope.value,
        "case_id": case_id if scope == CacheScope.CASE else None,
        "shuttle_name": shuttle_name,
        "shuttle_version": shuttle_version,
        "entity_key": entity_key,
        "findings": [dict(finding) for finding in findings],
        "notes": list(notes),
        "finding_count": len(findings),
        "size_bytes": size,
        "fetched_at": fetched_at,
        "expires_at": fetched_at + timedelta(seconds=ttl_seconds),
        "hits": 0,
        "last_hit_at": None,
    }
    statement = insert(ResultCacheEntry).values(**values)
    await db.execute(
        statement.on_conflict_do_update(
            index_elements=[ResultCacheEntry.key],
            set_={name: statement.excluded[name] for name in values if name != "key"},
        )
    )
    return True


@dataclass(frozen=True)
class PurgeResult:
    expired: int
    evicted: int


async def purge(db: AsyncSession, *, now: datetime, max_total_bytes: int) -> PurgeResult:
    """Delete expired entries, then the least recently used ones beyond ``max_total_bytes``.

    The caller commits.
    """
    expired = (
        await db.execute(
            delete(ResultCacheEntry)
            .where(ResultCacheEntry.expires_at <= now)
            .returning(ResultCacheEntry.key)
        )
    ).all()
    used = func.coalesce(ResultCacheEntry.last_hit_at, ResultCacheEntry.fetched_at)
    running = (
        select(
            ResultCacheEntry.key,
            func.sum(ResultCacheEntry.size_bytes)
            .over(order_by=(used.desc(), ResultCacheEntry.key))
            .label("running"),
        )
    ).subquery()
    over_cap = select(running.c.key).where(running.c.running > max_total_bytes)
    evicted = (
        await db.execute(
            delete(ResultCacheEntry)
            .where(ResultCacheEntry.key.in_(over_cap))
            .returning(ResultCacheEntry.key)
        )
    ).all()
    return PurgeResult(expired=len(expired), evicted=len(evicted))


@dataclass(frozen=True)
class ShuttleCacheStats:
    shuttle: str
    entries: int
    size_bytes: int
    hits: int
    # Jobs of this Shuttle in the last STATS_WINDOW that finished, and how many
    # of them were answered from the cache.
    jobs: int
    jobs_from_cache: int


@dataclass(frozen=True)
class CacheStats:
    entries: int
    size_bytes: int
    shuttles: list[ShuttleCacheStats]


async def stats(db: AsyncSession, *, now: datetime) -> CacheStats:
    """Entries, size and hits per Shuttle, with recent jobs answered from the cache."""
    entries = {
        name: (count, size, hits)
        for name, count, size, hits in (
            await db.execute(
                select(
                    ResultCacheEntry.shuttle_name,
                    func.count(),
                    func.coalesce(func.sum(ResultCacheEntry.size_bytes), 0),
                    func.coalesce(func.sum(ResultCacheEntry.hits), 0),
                ).group_by(ResultCacheEntry.shuttle_name)
            )
        ).all()
    }
    jobs = {
        name: (total, cached)
        for name, total, cached in (
            await db.execute(
                select(
                    Job.shuttle_name,
                    func.count(),
                    func.count().filter(Job.from_cache.is_(True)),
                )
                .where(
                    Job.status == JobStatus.SUCCEEDED,
                    Job.finished_at >= now - STATS_WINDOW,
                )
                .group_by(Job.shuttle_name)
            )
        ).all()
    }
    shuttles = [
        ShuttleCacheStats(
            shuttle=name,
            entries=int(entries.get(name, (0, 0, 0))[0]),
            size_bytes=int(entries.get(name, (0, 0, 0))[1]),
            hits=int(entries.get(name, (0, 0, 0))[2]),
            jobs=int(jobs.get(name, (0, 0))[0]),
            jobs_from_cache=int(jobs.get(name, (0, 0))[1]),
        )
        for name in sorted(set(entries) | set(jobs))
    ]
    return CacheStats(
        entries=sum(item.entries for item in shuttles),
        size_bytes=sum(item.size_bytes for item in shuttles),
        shuttles=shuttles,
    )


async def clear(
    db: AsyncSession, *, shuttle: str | None = None, case_id: uuid.UUID | None = None
) -> int:
    """Delete entries (of one Shuttle, or one case's), returning how many. The caller commits."""
    statement = delete(ResultCacheEntry)
    if shuttle is not None:
        statement = statement.where(ResultCacheEntry.shuttle_name == shuttle)
    if case_id is not None:
        statement = statement.where(ResultCacheEntry.case_id == case_id)
    return len((await db.execute(statement.returning(ResultCacheEntry.key))).all())


__all__ = [
    "STATS_WINDOW",
    "CacheScope",
    "CacheStats",
    "CachedResult",
    "PurgeResult",
    "ResultCacheEntry",
    "ShuttleCacheStats",
    "cache_key",
    "clear",
    "encoded_size",
    "lookup",
    "purge",
    "stats",
    "store",
]
