"""Exporting a case's graph: JSON, GraphML, or CSV files in a ZIP.

``GET /cases/{id}/export`` is a plain ``GET`` so the browser can download it
with a normal link (the session cookie authenticates it; a ``GET`` needs no
CSRF token). Any member may export; person-related data is redacted unless a
case owner asks for it with ``include_person_data=true`` and a ``reason``.
Every export is recorded as ``case.export`` (format, counts, whether person
data was included, the reason), never with exported values.

A download link cannot show an error to the page that started it, so
``GET /cases/{id}/export/check`` answers the same checks (403, 413, 429, 422)
without exporting anything; the web UI calls it before following the link.
"""

import logging
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from neo4j.exceptions import DriverError, Neo4jError
from pydantic import BaseModel
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nightloom_api import __version__
from nightloom_api.audit import service as audit
from nightloom_api.audit.actions import AuditAction, AuditOutcome, TargetType
from nightloom_api.auth import clock
from nightloom_api.cases.access import Capability, CaseAccess, can, case_access, deny_capability
from nightloom_api.export import service
from nightloom_api.export.formats import MEDIA_TYPES, ExportFormat, ExportHeader
from nightloom_api.export.ratelimit import MAX_EXPORTS, WINDOW_SECONDS, ExportRateLimiter
from nightloom_api.export.redaction import Redactor
from nightloom_api.graph.repository import GraphCounts, GraphRepository
from nightloom_api.infra.cache import get_cache
from nightloom_api.infra.db import get_session, get_sessionmaker
from nightloom_api.infra.graph import get_graph_repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cases/{case_id}", tags=["export"])

DbSession = Annotated[AsyncSession, Depends(get_session)]
Sessionmaker = Annotated[async_sessionmaker[AsyncSession], Depends(get_sessionmaker)]
Graph = Annotated[GraphRepository, Depends(get_graph_repository)]
Cache = Annotated[Redis, Depends(get_cache)]
Readable = Annotated[CaseAccess, Depends(case_access(Capability.READ))]

FormatParam = Annotated[ExportFormat, Query(alias="format")]
IncludePersonData = Annotated[bool, Query()]
ReasonParam = Annotated[str | None, Query(max_length=service.REASON_MAX_LENGTH)]

GRAPH_UNAVAILABLE = "The graph database is unavailable. Try again later."
EXPORTS_UNAVAILABLE = "Exports are unavailable right now. Try again later."
REASON_REQUIRED = (
    f"Give a reason of at least {service.REASON_MIN_LENGTH} characters "
    "to include person-related data."
)

_ERRORS: dict[int | str, dict[str, object]] = {
    status.HTTP_403_FORBIDDEN: {"description": "Person data was asked for by a non-owner"},
    status.HTTP_404_NOT_FOUND: {"description": "No such case, or you are not a member"},
    status.HTTP_413_CONTENT_TOO_LARGE: {"description": "The case has too many entities"},
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Missing or short reason"},
    status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many exports; see Retry-After"},
    status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Neo4j or Valkey is unavailable"},
}


def too_large_message(entities: int) -> str:
    return (
        f"This case has {entities:,} entities; exports are limited to "
        f"{service.MAX_EXPORT_ENTITIES:,}. Delete what you no longer need, or ask an "
        "operator for a database-level export."
    )


def too_many_message(retry_after: int) -> str:
    minutes = max(1, -(-retry_after // 60))
    return (
        f"You can start at most {MAX_EXPORTS} exports every {WINDOW_SECONDS // 60} minutes. "
        f"Try again in {minutes} minute{'s' if minutes != 1 else ''}."
    )


async def _check_person_data(
    db: AsyncSession,
    make_session: async_sessionmaker[AsyncSession],
    request: Request,
    access: CaseAccess,
    include_person_data: bool,
    reason: str | None,
) -> str | None:
    """The cleaned reason; 403 (audited) for non-owners, 422 without a reason."""
    cleaned = reason.strip() if reason else None
    if not include_person_data:
        return None
    if not can(access.role, Capability.EXPORT_PERSON_DATA):
        await deny_capability(db, make_session, request, access, Capability.EXPORT_PERSON_DATA)
    if cleaned is None or len(cleaned) < service.REASON_MIN_LENGTH:
        await db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, REASON_REQUIRED)
    return cleaned


async def _counts(db: AsyncSession, graph: GraphRepository, access: CaseAccess) -> GraphCounts:
    try:
        return await graph.count_graph(access.case.id)
    except (Neo4jError, DriverError) as error:
        await db.rollback()
        logger.error("Graph read failed: %s", type(error).__name__)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, GRAPH_UNAVAILABLE) from error


async def _refuse(
    db: AsyncSession,
    make_session: async_sessionmaker[AsyncSession],
    access: CaseAccess,
    *,
    code: int,
    message: str,
    details: dict[str, object],
    headers: dict[str, str] | None = None,
) -> NoReturn:
    """Answer ``code`` and record a ``case.export`` failure in its own transaction."""
    case_id, actor = access.case.id, access.actor
    await db.rollback()
    await audit.record_standalone(
        make_session,
        action=AuditAction.CASE_EXPORT,
        outcome=AuditOutcome.FAILURE,
        actor=actor,
        target_type=TargetType.CASE,
        target_id=case_id,
        case_id=case_id,
        details=details,
    )
    raise HTTPException(code, message, headers=headers)


class ExportCheckOut(BaseModel):
    """What an export with these parameters would contain, and how many exports are left."""

    entities: int
    person_entities: int
    relationships: int
    max_entities: int
    include_person_data: bool
    exports_remaining: int


@router.get("/export/check", response_model=ExportCheckOut, responses=_ERRORS)
async def check_export(
    request: Request,
    access: Readable,
    db: DbSession,
    make_session: Sessionmaker,
    graph: Graph,
    cache: Cache,
    export_format: FormatParam = ExportFormat.JSON,
    include_person_data: IncludePersonData = False,
    reason: ReasonParam = None,
) -> ExportCheckOut:
    """Run an export's checks without exporting or counting against the limit (any member)."""
    del export_format  # accepted so the UI can send the same query as the download
    await _check_person_data(db, make_session, request, access, include_person_data, reason)
    counts = await _counts(db, graph, access)
    user_id = access.user.id
    await db.rollback()
    if counts.entities > service.MAX_EXPORT_ENTITIES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, too_large_message(counts.entities))
    try:
        remaining, retry_after = await ExportRateLimiter(cache).remaining(user_id)
    except (RedisError, OSError) as error:
        logger.error("Export rate limit check failed: %s", type(error).__name__)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, EXPORTS_UNAVAILABLE) from error
    if remaining == 0 and retry_after is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            too_many_message(retry_after),
            headers={"Retry-After": str(retry_after)},
        )
    return ExportCheckOut(
        entities=counts.entities,
        person_entities=counts.person_entities,
        relationships=counts.relationships,
        max_entities=service.MAX_EXPORT_ENTITIES,
        include_person_data=include_person_data,
        exports_remaining=remaining,
    )


@router.get(
    "/export",
    response_class=StreamingResponse,
    responses={
        **_ERRORS,
        status.HTTP_200_OK: {
            "description": "The export, as a download",
            "content": {media: {} for media in MEDIA_TYPES.values()},
        },
    },
)
async def export_case(
    request: Request,
    access: Readable,
    db: DbSession,
    make_session: Sessionmaker,
    graph: Graph,
    cache: Cache,
    export_format: FormatParam,
    include_person_data: IncludePersonData = False,
    reason: ReasonParam = None,
) -> StreamingResponse:
    """Download the case's graph (any member), streamed.

    ``format`` is ``json``, ``graphml`` or ``csv`` (a ZIP of two CSV files and
    a README). Person-related entities are redacted unless
    ``include_person_data=true``, which needs the case owner role and a
    ``reason`` of at least 10 characters. At most 100,000 entities (413), and
    at most 10 exports per user every 10 minutes (429).
    """
    cleaned_reason = await _check_person_data(
        db, make_session, request, access, include_person_data, reason
    )
    counts = await _counts(db, graph, access)
    details: dict[str, object] = {
        "format": export_format.value,
        "entities": counts.entities,
        "relationships": counts.relationships,
        "include_person_data": include_person_data,
    }
    if cleaned_reason is not None:
        details["reason"] = cleaned_reason
    if counts.entities > service.MAX_EXPORT_ENTITIES:
        await _refuse(
            db,
            make_session,
            access,
            code=status.HTTP_413_CONTENT_TOO_LARGE,
            message=too_large_message(counts.entities),
            details={**details, "error": "too_large"},
        )
    try:
        retry_after = await ExportRateLimiter(cache).hit(access.user.id)
    except (RedisError, OSError) as error:
        await db.rollback()
        logger.error("Export rate limit check failed: %s", type(error).__name__)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, EXPORTS_UNAVAILABLE) from error
    if retry_after is not None:
        await _refuse(
            db,
            make_session,
            access,
            code=status.HTTP_429_TOO_MANY_REQUESTS,
            message=too_many_message(retry_after),
            details={**details, "error": "rate_limited"},
            headers={"Retry-After": str(retry_after)},
        )

    now = clock.utcnow()
    case = access.case
    case_id = case.id
    header = ExportHeader(
        case_id=str(case.id),
        title=case.title,
        legal_basis=case.legal_basis.value,
        exported_at=now,
        exported_by_label=access.user.display_name,
        nightloom_version=__version__,
        include_person_data=include_person_data,
    )
    redactor = Redactor(include_person_data=include_person_data)
    if not include_person_data:
        details["redacted_entities"] = counts.person_entities
    filename = service.export_filename(case.title, export_format, now)
    await audit.record(
        db,
        action=AuditAction.CASE_EXPORT,
        outcome=AuditOutcome.SUCCESS,
        actor=access.actor,
        target_type=TargetType.CASE,
        target_id=case.id,
        case_id=case.id,
        details=details,
    )
    await db.commit()
    return StreamingResponse(
        service.stream_export(graph, case_id, export_format, header, redactor),
        media_type=MEDIA_TYPES[export_format],
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )
