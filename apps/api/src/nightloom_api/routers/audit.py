"""The audit log for admins: list events, verify the hash chain."""

import contextlib
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import AwareDatetime, BaseModel, ConfigDict, field_validator, model_serializer
from pydantic_core.core_schema import SerializerFunctionWrapHandler
from redis.asyncio import Redis
from redis.exceptions import LockError, RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nightloom_api.audit import service as audit
from nightloom_api.audit.actions import AuditAction, AuditOutcome
from nightloom_api.audit.chain import ip_text
from nightloom_api.audit.models import AuditEvent
from nightloom_api.audit.verify import verify_chain
from nightloom_api.auth.deps import AdminUser, client_ip
from nightloom_api.infra.cache import get_cache
from nightloom_api.infra.db import get_session

router = APIRouter(prefix="/audit", tags=["audit"])

DbSession = Annotated[AsyncSession, Depends(get_session)]

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
# One verification at a time, across all API processes.
VERIFY_LOCK_KEY = "nightloom:audit:verify-lock"
# Upper bound for one verification; the lock frees itself after this.
VERIFY_LOCK_SECONDS = 15 * 60
VERIFY_RETRY_AFTER_SECONDS = 10
VERIFY_BUSY = "A verification is already running. Try again shortly."
VERIFY_UNAVAILABLE = "Verification is temporarily unavailable. Try again shortly."


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


class VerifyOut(BaseModel):
    ok: bool
    checked: int
    first_bad_id: int | None
    reason: str | None


@router.get("", response_model=AuditPage)
async def list_events(
    _admin: AdminUser,
    db: DbSession,
    before_id: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    action: AuditAction | None = None,
    outcome: AuditOutcome | None = None,
    actor_user_id: uuid.UUID | None = None,
    case_id: uuid.UUID | None = None,
    since: Annotated[AwareDatetime | None, Query(description="occurred_at >= since")] = None,
    until: Annotated[AwareDatetime | None, Query(description="occurred_at < until")] = None,
    include_hashes: bool = False,
) -> AuditPage:
    """Audit events, newest first (by ID, which is chain order), with optional filters."""
    statement = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit + 1)
    if before_id is not None:
        statement = statement.where(AuditEvent.id < before_id)
    if action is not None:
        statement = statement.where(AuditEvent.action == action.value)
    if outcome is not None:
        statement = statement.where(AuditEvent.outcome == outcome.value)
    if actor_user_id is not None:
        statement = statement.where(AuditEvent.actor_user_id == actor_user_id)
    if case_id is not None:
        statement = statement.where(AuditEvent.case_id == case_id)
    if since is not None:
        statement = statement.where(AuditEvent.occurred_at >= since)
    if until is not None:
        statement = statement.where(AuditEvent.occurred_at < until)

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


_VERIFY_ERRORS: dict[int | str, dict[str, object]] = {
    status.HTTP_429_TOO_MANY_REQUESTS: {"description": "A verification is already running"},
    status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Valkey is unreachable"},
}


@router.post("/verify", response_model=VerifyOut, responses=_VERIFY_ERRORS)
async def verify(
    request: Request,
    admin: AdminUser,
    db: DbSession,
    cache: Annotated[Redis, Depends(get_cache)],
) -> VerifyOut:
    """Recompute the whole hash chain and report the first event that does not match.

    One verification runs at a time (429 otherwise). The run itself is recorded
    as an ``audit.verify`` event: ``success`` if the chain is intact, else ``failure``.
    """
    # Built now: the user object expires when this session's transaction ends.
    actor = audit.Actor.for_user(admin, client_ip(request))
    lock = cache.lock(VERIFY_LOCK_KEY, timeout=VERIFY_LOCK_SECONDS, blocking=False)
    try:
        acquired = await lock.acquire()
    except RedisError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, VERIFY_UNAVAILABLE) from exc
    if not acquired:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            VERIFY_BUSY,
            headers={"Retry-After": str(VERIFY_RETRY_AFTER_SECONDS)},
        )
    try:
        result = await verify_chain(db)
        await db.rollback()  # end the read-only transaction before appending
        await audit.record(
            db,
            action=AuditAction.AUDIT_VERIFY,
            outcome=AuditOutcome.SUCCESS if result.ok else AuditOutcome.FAILURE,
            actor=actor,
            details=result.as_dict(),
        )
        await db.commit()
    finally:
        # Expired (LockNotOwnedError) or Valkey gone: it frees itself anyway.
        with contextlib.suppress(LockError, RedisError):
            await lock.release()
    return VerifyOut(**result.as_dict())
