"""ORM model for personal graph layouts."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nightloom_api.auth import clock
from nightloom_api.infra.db import Base


def _now() -> datetime:
    return clock.utcnow()


class GraphLayout(Base):
    """One user's pinned positions in one case. Deleted with the case or the user."""

    __tablename__ = "graph_layouts"

    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    # Entity key -> [x, y] in graph coordinates.
    positions: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"GraphLayout(case_id={self.case_id!s}, user_id={self.user_id!s})"
