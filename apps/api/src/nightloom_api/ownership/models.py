"""ORM model for ownership verifications."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
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


class Method(StrEnum):
    # A TXT record at _nightloom-challenge.<domain>.
    DNS_TXT = "dns_txt"
    # A file at https://<domain>/.well-known/nightloom-verification.txt.
    HTTP_FILE = "http_file"


class VerificationStatus(StrEnum):
    # Added; the proof has not been found yet.
    PENDING = "pending"
    # The proof was found; active Shuttles may run until expires_at.
    VERIFIED = "verified"
    # A check by an owner did not find the proof.
    FAILED = "failed"
    # A daily re-check no longer found the proof.
    REVOKED = "revoked"
    # 30 days passed since it was verified.
    EXPIRED = "expired"


class OwnershipVerification(Base):
    """A domain a case's owner claims to control, and whether that was proven.

    One per domain and case; deleted with the case. Verifying a domain covers
    its subdomains, and the IP addresses it resolved to when last checked
    (``addresses``; the worker resolves the domain again before an active
    Shuttle runs on an address).
    """

    __tablename__ = "ownership_verifications"
    __table_args__ = (
        UniqueConstraint("case_id", "domain", name="uq_ownership_verifications_case_domain"),
        CheckConstraint("method IN ('dns_txt', 'http_file')", name="method"),
        CheckConstraint(
            "status IN ('pending', 'verified', 'failed', 'revoked', 'expired')", name="status"
        ),
        CheckConstraint(
            "status <> 'verified' OR (verified_at IS NOT NULL AND expires_at IS NOT NULL)",
            name="verified_has_dates",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), index=True
    )
    domain: Mapped[str] = mapped_column(Text)
    method: Mapped[str] = mapped_column(Text)
    # What the proof must contain: "nightloom-verification=<token>".
    token: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default=VerificationStatus.PENDING)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )
    # A check is queued (an owner asked, or the daily re-check) and has not reported.
    check_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Whether that check renews the 30 days (an owner's) or is the daily re-check.
    check_renews: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Why the last check did not find the proof (a short code, see service.ERRORS).
    last_error: Mapped[str | None] = mapped_column(Text)
    # Re-checks in a row that could not tell (the source was unreachable).
    failures: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    # The domain's A and AAAA addresses at the last successful check.
    addresses: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))

    def __repr__(self) -> str:
        return f"OwnershipVerification(id={self.id!s})"
