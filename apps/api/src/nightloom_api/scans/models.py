"""ORM models for scan profiles and scan runs."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nightloom_api.auth import clock
from nightloom_api.infra.db import Base


def _now() -> datetime:
    return clock.utcnow()


class ScanProfile(Base):
    """A profile an admin defined (the built-in ones live in code, see .profiles)."""

    __tablename__ = "scan_profiles"
    __table_args__ = (Index("uq_scan_profiles_lower_name", text("lower(name)"), unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, server_default=text("''"))
    # Shuttle names, in the order they run.
    shuttles: Mapped[list[str]] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"ScanProfile(id={self.id!s})"


class RunStatus(StrEnum):
    # Releasing jobs, or waiting for released ones to finish.
    RUNNING = "running"
    # Every job was released and has finished.
    FINISHED = "finished"
    CANCELLED = "cancelled"
    # Ended early: the case closed, or the requester may no longer run Shuttles.
    STOPPED = "stopped"


class ScanRun(Base):
    """One profile run on one or more entities of a case. Deleted with the case.

    ``plan`` lists every job of the run in order; the first ``released`` of
    them exist as jobs (``jobs.run_id``). The rest are released as the
    requester's earlier jobs finish, so a run never exceeds the per-user
    limit of queued and running jobs.
    """

    __tablename__ = "scan_runs"
    __table_args__ = (
        CheckConstraint("status IN ('running', 'finished', 'cancelled', 'stopped')", name="status"),
        CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)", name="finished_at_matches_status"
        ),
        CheckConstraint("released >= 0 AND skipped >= 0 AND entities >= 1", name="counts"),
        Index("ix_scan_runs_case_id_created_at_id", "case_id", "created_at", "id"),
        Index(
            "ix_scan_runs_requested_by_running",
            "requested_by",
            postgresql_where=text("status = 'running'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    # A built-in profile's slug or a custom profile's UUID, and its name then.
    profile_id: Mapped[str] = mapped_column(Text)
    profile_name: Mapped[str] = mapped_column(Text)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(Text, default=RunStatus.RUNNING.value)
    # [{"shuttle": name, "entity_key": key}, ...]
    plan: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    released: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    # Pairs left out when the run was planned (type not accepted, person gate,
    # not installed) or released (no longer installed).
    skipped: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    entities: Mapped[int] = mapped_column(Integer)
    use_cache: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    stop_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def total(self) -> int:
        return len(self.plan)

    def __repr__(self) -> str:
        return f"ScanRun(id={self.id!s}, status={self.status})"
