"""ORM models for users and their server-side sessions."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nightloom_api.auth import clock
from nightloom_api.infra.db import Base

EMAIL_MAX_LENGTH = 320
DISPLAY_NAME_MAX_LENGTH = 100
USER_AGENT_MAX_LENGTH = 256
# Long enough for any IPv6 address in text form.
IP_MAX_LENGTH = 45


class Role(StrEnum):
    """What a user may do. Admins manage users; analysts and viewers do not."""

    ADMIN = "admin"
    ANALYST = "analyst"
    VIEWER = "viewer"


def _now() -> datetime:
    return clock.utcnow()


class User(Base):
    """A person who can sign in. Emails are stored normalized (stripped, lower-case)."""

    __tablename__ = "users"
    __table_args__ = (CheckConstraint("email = lower(email)", name="email_lowercase"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(EMAIL_MAX_LENGTH), unique=True)
    display_name: Mapped[str] = mapped_column(String(DISPLAY_NAME_MAX_LENGTH))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(
        Enum(
            Role,
            name="user_role",
            values_callable=lambda roles: [role.value for role in roles],
            validate_strings=True,
        )
    )
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    sessions: Mapped[list["Session"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"User(id={self.id!s}, role={self.role.value}, is_active={self.is_active})"


class Session(Base):
    """A signed-in browser. Only the SHA-256 of the session token is stored."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str | None] = mapped_column(String(IP_MAX_LENGTH))
    user_agent: Mapped[str | None] = mapped_column(String(USER_AGENT_MAX_LENGTH))

    user: Mapped[User] = relationship(back_populates="sessions")

    def __repr__(self) -> str:
        return f"Session(id={self.id!s}, user_id={self.user_id!s})"
