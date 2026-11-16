"""Reading the audit log: one page of events, newest first, with filters.

Shared by the admin listing (``GET /audit``) and the per-case listing
(``GET /cases/{id}/audit``), so both page and filter the same way.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator, model_serializer
from pydantic_core.core_schema import SerializerFunctionWrapHandler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nightloom_api.audit.actions import AuditAction, AuditOutcome
from nightloom_api.audit.chain import ip_text
from nightloom_api.audit.models import AuditEvent

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


class AuditEventOut(BaseModel):
    """One audit event. ``prev_hash`` and ``hash`` appear only with ``include_hashes=true``."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    occurred_at: datetime
    actor_user_id: uuid.UUID | None
    actor_label: str | None
    actor_ip: str | None
    request_id: str
    action: str
    outcome: AuditOutcome
    target_type: str | None
    target_id: str | None
    case_id: uuid.UUID | None
    details: dict[str, Any]
    prev_hash: str | None = None
    hash: str | None = None

    @field_validator("actor_ip", mode="before")
    @classmethod
    def _ip_as_text(cls, value: object) -> str | None:
        return ip_text(value)

    @model_serializer(mode="wrap")
    def _omit_absent_hashes(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        for key in ("prev_hash", "hash"):
            if data.get(key) is None:
                data.pop(key, None)
        return data


class AuditPage(BaseModel):
    """Newest first. Pass ``next_before_id`` as ``before_id`` for the next page."""

    items: list[AuditEventOut]
    next_before_id: int | None


@dataclass(frozen=True)
class AuditFilters:
    """Optional filters; ``since`` is inclusive, ``until`` exclusive."""

    action: AuditAction | None = None
    outcome: AuditOutcome | None = None
    actor_user_id: uuid.UUID | None = None
    case_id: uuid.UUID | None = None
    since: datetime | None = None
    until: datetime | None = None


async def list_events(
    db: AsyncSession,
    filters: AuditFilters,
    *,
    before_id: int | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    include_hashes: bool = False,
) -> AuditPage:
    """One page of events, newest first (by ID, which is chain order)."""
    statement = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit + 1)
    if before_id is not None:
        statement = statement.where(AuditEvent.id < before_id)
    if filters.action is not None:
        statement = statement.where(AuditEvent.action == filters.action.value)
    if filters.outcome is not None:
        statement = statement.where(AuditEvent.outcome == filters.outcome.value)
    if filters.actor_user_id is not None:
        statement = statement.where(AuditEvent.actor_user_id == filters.actor_user_id)
    if filters.case_id is not None:
        statement = statement.where(AuditEvent.case_id == filters.case_id)
    if filters.since is not None:
        statement = statement.where(AuditEvent.occurred_at >= filters.since)
    if filters.until is not None:
        statement = statement.where(AuditEvent.occurred_at < filters.until)

    rows = list((await db.execute(statement)).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = []
    for row in rows:
        item = AuditEventOut.model_validate(row)
        if not include_hashes:
            item.prev_hash = item.hash = None
        items.append(item)
    return AuditPage(items=items, next_before_id=rows[-1].id if has_more else None)
