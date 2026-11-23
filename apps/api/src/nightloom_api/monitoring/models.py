"""ORM models for Certificate Transparency monitoring: watches and their alerts."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nightloom_api.auth import clock
from nightloom_api.infra.db import Base


def _now() -> datetime:
    return clock.utcnow()


class CtWatch(Base):
    """A domain whose new certificates a case wants to hear about.

    One per domain and case; deleted with the case. Every hour the worker
    queues a crt.sh job for it (requested by ``created_by``, without the
    result cache) and turns the certificates and names that job added to the
    graph into a :class:`CtAlert`. The first check only records what exists
    (``baseline_at``): everything would be new.
    """

    __tablename__ = "ct_watches"
    __table_args__ = (
        UniqueConstraint("case_id", "domain", name="uq_ct_watches_case_domain"),
        Index("ix_ct_watches_next_check_at", "next_check_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), index=True
    )
    domain: Mapped[str] = mapped_column(Text)
    # Whose job limit the checks count against, and in whose name they run.
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )
    # When the sweep should queue the next check.
    next_check_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )
    # The check queued and not yet read (its job may still be running).
    pending_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL")
    )
    # The last check that finished, and when.
    last_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL")
    )
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When the first successful check recorded what already existed.
    baseline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Why the last check did not run or did not succeed (see service.PROBLEMS).
    last_problem: Mapped[str | None] = mapped_column(Text)

    def __repr__(self) -> str:
        return f"CtWatch(id={self.id!s})"


class CtAlert(Base):
    """New certificates or names a watch's check added to the case's graph.

    Seen by the case as a whole: an editor or owner marks it seen
    (``acknowledged_at``). Deleted with the case; kept when the watch is
    removed (``watch_id`` becomes NULL).
    """

    __tablename__ = "ct_alerts"
    __table_args__ = (
        CheckConstraint("certificates >= 0 AND names_total >= 0", name="counts"),
        Index("ix_ct_alerts_case_id_created_at", "case_id", "created_at"),
        Index(
            "ix_ct_alerts_unacknowledged",
            "case_id",
            postgresql_where=text("acknowledged_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    watch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ct_watches.id", ondelete="SET NULL")
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    domain: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )
    # How many new certificates; the new names (at most service.MAX_ALERT_NAMES) and their count.
    certificates: Mapped[int] = mapped_column(Integer)
    names: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    names_total: Mapped[int] = mapped_column(Integer)
    # The graph keys of what is new (for "show in graph"); at most service.MAX_ALERT_KEYS.
    keys: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    def __repr__(self) -> str:
        return f"CtAlert(id={self.id!s})"
