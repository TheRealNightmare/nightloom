"""The audit log for admins: list events, verify the hash chain."""

import contextlib
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import AwareDatetime, BaseModel
from redis.asyncio import Redis
from redis.exceptions import LockError, RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from nightloom_api.audit import listing
from nightloom_api.audit import service as audit
from nightloom_api.audit.actions import AuditAction, AuditOutcome
from nightloom_api.audit.listing import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, AuditFilters, AuditPage
from nightloom_api.audit.verify import verify_chain
from nightloom_api.auth.deps import AdminUser, client_ip
from nightloom_api.infra.cache import get_cache
from nightloom_api.infra.db import get_session

router = APIRouter(prefix="/audit", tags=["audit"])

DbSession = Annotated[AsyncSession, Depends(get_session)]

# One verification at a time, across all API processes.
VERIFY_LOCK_KEY = "nightloom:audit:verify-lock"
# Upper bound for one verification; the lock frees itself after this.
VERIFY_LOCK_SECONDS = 15 * 60
VERIFY_RETRY_AFTER_SECONDS = 10
VERIFY_BUSY = "A verification is already running. Try again shortly."
VERIFY_UNAVAILABLE = "Verification is temporarily unavailable. Try again shortly."


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
    filters = AuditFilters(
        action=action,
        outcome=outcome,
        actor_user_id=actor_user_id,
        case_id=case_id,
        since=since,
        until=until,
    )
    return await listing.list_events(
        db, filters, before_id=before_id, limit=limit, include_hashes=include_hashes
    )


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
