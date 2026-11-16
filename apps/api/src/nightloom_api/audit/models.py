"""The ``audit_events`` table.

Rows are append-only: a trigger created by migration ``0003_audit_events``
raises on every UPDATE, DELETE and TRUNCATE. See :mod:`nightloom_api.audit.chain`
for the hash chain over the rows.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nightloom_api.infra.db import Base

HASH_LENGTH = 64


class AuditEvent(Base):
    """One recorded action. Never updated or deleted."""

    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint("outcome IN ('success', 'failure', 'denied')", name="outcome_valid"),
        CheckConstraint("hash ~ '^[0-9a-f]{64}$'", name="hash_hex"),
        CheckConstraint("prev_hash ~ '^[0-9a-f]{64}$'", name="prev_hash_hex"),
        Index("ix_audit_events_actor_user_id_occurred_at", "actor_user_id", "occurred_at"),
        Index("ix_audit_events_case_id_occurred_at", "case_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    # RESTRICT, not SET NULL: the hash covers this column and the row may not
    # change, so a user with audit history can be deactivated but not deleted.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    # The actor's email when the event happened (survives later changes).
    actor_label: Mapped[str | None] = mapped_column(Text)
    actor_ip: Mapped[str | None] = mapped_column(INET)
    request_id: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text, index=True)
    outcome: Mapped[str] = mapped_column(Text)
    target_type: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[str | None] = mapped_column(Text)
    # Deliberately no foreign key to cases: the audit trail outlives a deleted
    # case, and these rows may never change (see migration 0004_cases).
    case_id: Mapped[uuid.UUID | None] = mapped_column()
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    # Unique: every event has at most one successor, so the chain cannot fork.
    prev_hash: Mapped[str] = mapped_column(CHAR(HASH_LENGTH), unique=True)
    hash: Mapped[str] = mapped_column(CHAR(HASH_LENGTH), unique=True)

    def __repr__(self) -> str:
        return f"AuditEvent(id={self.id}, action={self.action}, outcome={self.outcome})"
