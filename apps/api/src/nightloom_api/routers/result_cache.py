"""The result cache: statistics and clearing (see :mod:`nightloom_api.jobs.result_cache`).

Admins see the cache's settings and contents per Shuttle and can clear it
(all of it, or one Shuttle's entries). A case owner can clear the entries of
their own case. Clearing is audited with counts and the Shuttle name only,
never entity keys.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from nightloom_api.audit import service as audit
from nightloom_api.audit.actions import AuditAction, AuditOutcome
from nightloom_api.audit.service import Actor
from nightloom_api.auth import clock
from nightloom_api.auth.deps import AdminUser, client_ip
from nightloom_api.cases.access import Capability, CaseAccess, case_access
from nightloom_api.config import Settings, get_settings
from nightloom_api.infra.db import get_session
from nightloom_api.jobs import result_cache, service
from nightloom_api.jobs.schemas import ResultCacheCleared, ResultCacheOut, ShuttleCacheStatsOut
from nightloom_sdk.meta import NAME_MAX_LENGTH
from nightloom_sdk.registry import ShuttleRegistry

admin_router = APIRouter(prefix="/admin/result-cache", tags=["result cache"])
case_router = APIRouter(prefix="/cases/{case_id}/result-cache", tags=["result cache"])

DbSession = Annotated[AsyncSession, Depends(get_session)]
Registry = Annotated[ShuttleRegistry, Depends(service.get_shuttle_registry)]
Owner = Annotated[CaseAccess, Depends(case_access(Capability.MANAGE))]


@admin_router.get("", response_model=ResultCacheOut)
async def get_result_cache(
    _admin: AdminUser,
    db: DbSession,
    registry: Registry,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ResultCacheOut:
    """The cache's scope and limits, and per Shuttle its entries, size, hits and recent jobs."""
    stats = await result_cache.stats(db, now=clock.utcnow())
    shuttles = []
    for item in stats.shuttles:
        installed = registry.get(item.shuttle)
        shuttles.append(
            ShuttleCacheStatsOut(
                shuttle=item.shuttle,
                cache_ttl_seconds=installed.meta.cache_ttl_seconds if installed else None,
                entries=item.entries,
                size_bytes=item.size_bytes,
                hits=item.hits,
                jobs=item.jobs,
                jobs_from_cache=item.jobs_from_cache,
            )
        )
    return ResultCacheOut(
        scope=settings.result_cache_scope,
        max_bytes=settings.result_cache_max_bytes,
        max_entry_bytes=settings.result_cache_max_entry_bytes,
        entries=stats.entries,
        size_bytes=stats.size_bytes,
        shuttles=shuttles,
    )


@admin_router.delete("", response_model=ResultCacheCleared)
async def clear_result_cache(
    request: Request,
    admin: AdminUser,
    db: DbSession,
    shuttle: Annotated[
        str | None, Query(min_length=1, max_length=NAME_MAX_LENGTH, pattern=r"^[a-z0-9-]+$")
    ] = None,
) -> ResultCacheCleared:
    """Delete every cached result, or one Shuttle's (``?shuttle=name``)."""
    deleted = await result_cache.clear(db, shuttle=shuttle)
    await audit.record(
        db,
        action=AuditAction.RESULT_CACHE_CLEAR,
        outcome=AuditOutcome.SUCCESS,
        actor=Actor.for_user(admin, client_ip(request)),
        details={"shuttle": shuttle or "all", "deleted": deleted},
    )
    await db.commit()
    return ResultCacheCleared(deleted=deleted)


@case_router.delete("", response_model=ResultCacheCleared)
async def clear_case_result_cache(access: Owner, db: DbSession) -> ResultCacheCleared:
    """Delete this case's cached results (case owners; also in a closed case).

    Only entries scoped to this case: instance-wide entries are the admins' to clear.
    """
    deleted = await result_cache.clear(db, case_id=access.case.id)
    await audit.record(
        db,
        action=AuditAction.RESULT_CACHE_CLEAR,
        outcome=AuditOutcome.SUCCESS,
        actor=access.actor,
        case_id=access.case.id,
        details={"shuttle": "all", "deleted": deleted},
    )
    await db.commit()
    return ResultCacheCleared(deleted=deleted)
