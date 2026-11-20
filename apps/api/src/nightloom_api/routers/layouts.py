"""Personal graph layouts: each member's pinned entity positions in a case.

``GET`` and ``PUT /cases/{id}/layout`` read and replace the caller's own
layout. Both need only ``read`` (a layout arranges one's own view, so viewers
may have one) and work whatever the case's status. Access is otherwise like
every case route: non-members get 404, as if the case did not exist.

Layouts are UI preferences, so they are not audited. Saving is limited per
user (see :mod:`nightloom_api.layouts.ratelimit`) and the body is validated
strictly: canonical entity keys, finite coordinates within ±1e6, at most
10,000 entries and 1 MiB. Pins of entities the case no longer has are left
out when the layout is read.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from neo4j.exceptions import DriverError, Neo4jError
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nightloom_api.cases.access import CASE_NOT_FOUND, Capability, CaseAccess, case_access
from nightloom_api.graph.repository import GraphRepository
from nightloom_api.infra.cache import get_cache
from nightloom_api.infra.db import get_session
from nightloom_api.infra.graph import get_graph_repository
from nightloom_api.layouts import service
from nightloom_api.layouts.ratelimit import LayoutRateLimiter
from nightloom_api.layouts.schemas import MAX_BODY_BYTES, LayoutIn, LayoutOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cases/{case_id}", tags=["layouts"])

DbSession = Annotated[AsyncSession, Depends(get_session)]
Graph = Annotated[GraphRepository, Depends(get_graph_repository)]
Cache = Annotated[Redis, Depends(get_cache)]
Readable = Annotated[CaseAccess, Depends(case_access(Capability.READ))]

GRAPH_UNAVAILABLE = "The graph database is unavailable. Try again later."
BODY_TOO_LARGE = f"The layout is larger than {MAX_BODY_BYTES // 1024} KiB."
TOO_MANY_SAVES = "Too many layout saves. Try again in a moment."

_ERRORS: dict[int | str, dict[str, object]] = {
    status.HTTP_404_NOT_FOUND: {"description": "No such case, or you are not a member"},
}


async def _read_body(request: Request) -> bytes:
    """The request body, refused with 413 as soon as it exceeds MAX_BODY_BYTES."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, BODY_TOO_LARGE)
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, BODY_TOO_LARGE)
        chunks.append(chunk)
    return b"".join(chunks)


def _validation_error(error: ValidationError) -> RequestValidationError:
    """A 422 located under ``body``, without the input (keys may be person-related)."""
    return RequestValidationError(
        [
            {"type": item["type"], "loc": ("body", *item["loc"]), "msg": item["msg"]}
            for item in error.errors(include_url=False)
        ]
    )


@router.get("/layout", response_model=LayoutOut, responses=_ERRORS)
async def get_layout(access: Readable, db: DbSession, graph: Graph) -> LayoutOut:
    """The caller's pinned positions in the case (any member; empty when none).

    Entities deleted since the layout was saved are left out.
    """
    layout = await service.get_layout(db, access.case.id, access.user.id)
    if layout is None or not layout.positions:
        return LayoutOut(positions={}, updated_at=layout.updated_at if layout else None)
    stored: dict[str, list[float]] = layout.positions
    try:
        existing = await graph.existing_keys(access.case.id, list(stored))
    except (Neo4jError, DriverError) as error:
        logger.error("Graph read failed: %s", type(error).__name__)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, GRAPH_UNAVAILABLE) from error
    positions = {key: (xy[0], xy[1]) for key, xy in stored.items() if key in existing}
    return LayoutOut(positions=positions, updated_at=layout.updated_at)


@router.put(
    "/layout",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **_ERRORS,
        status.HTTP_413_CONTENT_TOO_LARGE: {"description": "The body is larger than 1 MiB"},
        status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many saves; see Retry-After"},
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": LayoutIn.model_json_schema()}},
        }
    },
)
async def put_layout(request: Request, access: Readable, db: DbSession, cache: Cache) -> None:
    """Replace the caller's pinned positions in the case (any member, any case status).

    ``positions`` maps canonical entity keys to ``[x, y]`` (finite, within
    ±1e6; at most 10,000 entries; 422 otherwise). Bodies over 1 MiB get 413.
    At most 60 saves per user per minute (429 with ``Retry-After``).
    """
    raw = await _read_body(request)
    try:
        body = LayoutIn.model_validate_json(raw)
    except ValidationError as error:
        await db.rollback()
        raise _validation_error(error) from error
    retry_after = await LayoutRateLimiter(cache).hit(access.user.id)
    if retry_after is not None:
        await db.rollback()
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            TOO_MANY_SAVES,
            headers={"Retry-After": str(retry_after)},
        )
    try:
        await service.replace_layout(db, access.case.id, access.user.id, body.positions)
        await db.commit()
    except IntegrityError as error:
        # The case was deleted after the access check.
        await db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, CASE_NOT_FOUND) from error
