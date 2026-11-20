"""ORM model for saved graph views."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nightloom_api.auth import clock
from nightloom_api.infra.db import Base


def _now() -> datetime:
    return clock.utcnow()


class SavedView(Base):
    """A named view of one case's graph. Deleted with the case; kept when its author is deleted."""

    __tablename__ = "saved_views"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    # The versioned view state (see nightloom_api.views.schemas.ViewState).
    state: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"SavedView(id={self.id!s}, case_id={self.case_id!s})"


# Names are unique per case, ignoring case ("Overview" and "overview" clash).
Index(
    "uq_saved_views_case_id_lower_name",
    SavedView.case_id,
    func.lower(SavedView.name),
    unique=True,
)
