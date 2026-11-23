"""ORM model for Shuttle jobs."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Text,
    false,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nightloom_api.auth import clock
from nightloom_api.infra.db import Base

# The error shown for a failed job is one of a few fixed, user-safe messages.
ERROR_MAX_LENGTH = 500


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


ACTIVE_STATUSES = frozenset({JobStatus.QUEUED, JobStatus.RUNNING})
FINISHED_STATUSES = frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED})


def _now() -> datetime:
    return clock.utcnow()


def empty_stats() -> dict[str, Any]:
    return {
        "entities_created": 0,
        "entities_updated": 0,
        "relationships_created": 0,
        "relationships_updated": 0,
        "findings_rejected": 0,
        "rejected_reasons": {},
        "entities_touched": [],
        "entities_new": [],
        "notes": [],
    }


class Job(Base):
    """One run of one Shuttle on one entity of a case. Deleted with the case."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            f"error IS NULL OR char_length(error) <= {ERROR_MAX_LENGTH}", name="error_length"
        ),
        CheckConstraint(
            "(status IN ('queued', 'running')) = (finished_at IS NULL)",
            name="finished_at_matches_status",
        ),
        # Keyset pagination of a case's jobs, newest first.
        Index("ix_jobs_case_id_created_at_id", "case_id", "created_at", "id"),
        # The per-user limit counts a user's queued and running jobs.
        Index(
            "ix_jobs_requested_by_active",
            "requested_by",
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    shuttle_name: Mapped[str] = mapped_column(Text)
    shuttle_version: Mapped[str] = mapped_column(Text)
    input_key: Mapped[str] = mapped_column(Text)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(
            JobStatus,
            name="job_status",
            values_callable=lambda enum: [member.value for member in enum],
            validate_strings=True,
        ),
        default=JobStatus.QUEUED,
        server_default=JobStatus.QUEUED.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=empty_stats, server_default="{}")
    cancel_requested: Mapped[bool] = mapped_column(default=False, server_default=false())
    # Whether the run may reuse recent results (False when the user asked for
    # a fresh run); see nightloom_api.jobs.result_cache.
    use_cache: Mapped[bool] = mapped_column(default=True, server_default=true())
    # Answered from the result cache, with the time the source was asked.
    from_cache: Mapped[bool] = mapped_column(default=False, server_default=false())
    cached_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The scan run that released this job (see nightloom_api.scans), if any.
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("scan_runs.id", ondelete="SET NULL"), index=True
    )
    # For a pivot's jobs: hops from the seed entities (0 for the seeds), and
    # whether what the job found has been added to the pivot's plan.
    pivot_depth: Mapped[int | None] = mapped_column()
    pivot_expanded: Mapped[bool] = mapped_column(default=False, server_default=false())

    def __repr__(self) -> str:
        # No input key: it can be personal data, and reprs end up in logs.
        return f"Job(id={self.id!s}, shuttle={self.shuttle_name}, status={self.status.value})"
