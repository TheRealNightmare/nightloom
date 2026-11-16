"""ORM models for cases and their members."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Text,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from nightloom_api.auth import clock
from nightloom_api.infra.db import Base

# The version of ETHICS.md a case's creator acknowledges. Bump it when the
# acceptable-use rules change materially; existing cases keep the version
# their creator agreed to.
ETHICS_VERSION = "2026-11"

TITLE_MIN_LENGTH = 3
TITLE_MAX_LENGTH = 200
DESCRIPTION_MAX_LENGTH = 5000
PURPOSE_MIN_LENGTH = 30
PURPOSE_MAX_LENGTH = 2000
LEGAL_BASIS_NOTE_MAX_LENGTH = 1000
RETENTION_DAYS_MIN = 1
RETENTION_DAYS_MAX = 3650


class LegalBasis(StrEnum):
    """Why the investigation is lawful. See ETHICS.md."""

    OWNED_ASSETS = "owned_assets"
    AUTHORIZED_ENGAGEMENT = "authorized_engagement"
    THREAT_INTELLIGENCE = "threat_intelligence"
    JOURNALISM = "journalism"
    ACADEMIC_RESEARCH = "academic_research"
    SELF_ASSESSMENT = "self_assessment"
    OTHER = "other"


# These bases mean little without specifics (whose engagement, which basis),
# so they need a legal_basis_note.
LEGAL_BASES_NEEDING_NOTE = frozenset({LegalBasis.AUTHORIZED_ENGAGEMENT, LegalBasis.OTHER})


class CaseStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    ARCHIVED = "archived"


class CaseRole(StrEnum):
    """A member's role within one case (independent of their global role)."""

    OWNER = "owner"
    EDITOR = "editor"
    VIEWER = "viewer"


def _now() -> datetime:
    return clock.utcnow()


def _values(enum: type[StrEnum]) -> list[str]:
    return [member.value for member in enum]


class Case(Base):
    """One investigation, with its mandatory purpose and legal basis."""

    __tablename__ = "cases"
    __table_args__ = (
        CheckConstraint(
            f"char_length(title) BETWEEN {TITLE_MIN_LENGTH} AND {TITLE_MAX_LENGTH}",
            name="title_length",
        ),
        CheckConstraint(
            f"description IS NULL OR char_length(description) <= {DESCRIPTION_MAX_LENGTH}",
            name="description_length",
        ),
        CheckConstraint(
            f"char_length(purpose) BETWEEN {PURPOSE_MIN_LENGTH} AND {PURPOSE_MAX_LENGTH}",
            name="purpose_length",
        ),
        CheckConstraint(
            "legal_basis_note IS NULL OR char_length(legal_basis_note) <= "
            f"{LEGAL_BASIS_NOTE_MAX_LENGTH}",
            name="legal_basis_note_length",
        ),
        CheckConstraint(
            "legal_basis NOT IN ('authorized_engagement', 'other') "
            "OR btrim(coalesce(legal_basis_note, '')) <> ''",
            name="legal_basis_note_required",
        ),
        CheckConstraint(
            "retention_days IS NULL OR retention_days BETWEEN "
            f"{RETENTION_DAYS_MIN} AND {RETENTION_DAYS_MAX}",
            name="retention_days_range",
        ),
        CheckConstraint(
            "(status <> 'closed' OR closed_at IS NOT NULL) "
            "AND (status <> 'open' OR closed_at IS NULL)",
            name="closed_at_matches_status",
        ),
        Index("ix_cases_status_updated_at", "status", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    purpose: Mapped[str] = mapped_column(Text)
    legal_basis: Mapped[LegalBasis] = mapped_column(
        Enum(
            LegalBasis,
            name="case_legal_basis",
            values_callable=_values,
            validate_strings=True,
        )
    )
    legal_basis_note: Mapped[str | None] = mapped_column(Text)
    ethics_ack_version: Mapped[str] = mapped_column(Text)
    ethics_ack_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    allow_person_entities: Mapped[bool] = mapped_column(default=False, server_default=false())
    status: Mapped[CaseStatus] = mapped_column(
        Enum(CaseStatus, name="case_status", values_callable=_values, validate_strings=True),
        default=CaseStatus.OPEN,
        server_default=CaseStatus.OPEN.value,
    )
    # Not enforced yet: nothing deletes old case data automatically.
    retention_days: Mapped[int | None] = mapped_column()
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, server_default=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        # No title or purpose: reprs end up in logs.
        return f"Case(id={self.id!s}, status={self.status.value})"


class CaseMember(Base):
    """A user's role in a case. Removed with the case or the user."""

    __tablename__ = "case_members"

    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role: Mapped[CaseRole] = mapped_column(
        Enum(CaseRole, name="case_member_role", values_callable=_values, validate_strings=True)
    )
    added_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"CaseMember(case_id={self.case_id!s}, user_id={self.user_id!s}, role={self.role})"
