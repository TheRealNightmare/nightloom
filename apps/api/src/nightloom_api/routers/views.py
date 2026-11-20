"""Saved views: named snapshots of how a case's graph looks, shared with its members.

``/cases/{id}/views``. Every member may list and read them (``read``).
Saving one needs ``edit`` (editors and owners) and an open case, like other
changes to the graph. Renaming, replacing the state of, or deleting a view
also needs ``edit`` and an open case, and is for the view's creator or a case
owner. Non-members get 404 for everything, as for every case route; a view of
another case is 404 too.

The state is validated strictly (see :mod:`nightloom_api.views.schemas`):
bodies over 1 MiB get 413, at most 50 views per case (409), names are unique
per case ignoring case (409). Writes are audited as ``view.create``,
``view.update`` and ``view.delete`` with the case and view IDs only (names
may be person-related, so neither the name nor the state is recorded) and
limited per user (429 with ``Retry-After``). Reading a view leaves out the
positions of entities the case no longer has.
"""

import logging
import uuid
from typing import Annotated, Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from neo4j.exceptions import DriverError, Neo4jError
from pydantic import BaseModel, ValidationError
from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nightloom_api.audit import service as audit
from nightloom_api.audit.actions import AuditAction, AuditOutcome, TargetType
from nightloom_api.auth.deps import FORBIDDEN
from nightloom_api.cases.access import (
    Capability,
    CaseAccess,
    case_access,
    record_denial,
)
from nightloom_api.cases.models import CaseRole, CaseStatus
from nightloom_api.graph.repository import GraphRepository
from nightloom_api.infra.cache import get_cache
from nightloom_api.infra.db import get_session, get_sessionmaker
from nightloom_api.infra.graph import get_graph_repository
from nightloom_api.layouts.ratelimit import LayoutRateLimiter
from nightloom_api.views import service
from nightloom_api.views.models import SavedView
from nightloom_api.views.schemas import (
    MAX_BODY_BYTES,
    MAX_VIEWS_PER_CASE,
    Author,
    ViewCreate,
    ViewOut,
    ViewSummary,
    ViewUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cases/{case_id}/views", tags=["views"])

DbSession = Annotated[AsyncSession, Depends(get_session)]
Sessionmaker = Annotated[async_sessionmaker[AsyncSession], Depends(get_sessionmaker)]
Graph = Annotated[GraphRepository, Depends(get_graph_repository)]
Cache = Annotated[Redis, Depends(get_cache)]
Readable = Annotated[CaseAccess, Depends(case_access(Capability.READ))]
EditableLocked = Annotated[CaseAccess, Depends(case_access(Capability.EDIT, lock=True))]

# Saves, renames and deletes per user per window (a fixed window in Valkey).
MAX_WRITES = 30
WINDOW_SECONDS = 60
RATE_KEY_PREFIX = "nightloom:views:user"

VIEW_NOT_FOUND = "View not found."
CASE_NOT_OPEN = "This case is not open. Reopen it to change its saved views."
GRAPH_UNAVAILABLE = "The graph database is unavailable. Try again later."
BODY_TOO_LARGE = f"The view is larger than {MAX_BODY_BYTES // 1024} KiB."
TOO_MANY_WRITES = "Too many changes to saved views. Try again in a moment."
NAME_TAKEN = "A view with this name already exists in this case. Choose another name."
TOO_MANY_VIEWS = (
    f"This case already has {MAX_VIEWS_PER_CASE} saved views, the most it can have. "
    "Delete one before saving another."
)

_ERRORS: dict[int | str, dict[str, object]] = {
    status.HTTP_404_NOT_FOUND: {"description": "No such case or view, or you are not a member"},
}
_WRITE_ERRORS: dict[int | str, dict[str, object]] = {
    **_ERRORS,
    status.HTTP_403_FORBIDDEN: {"description": "Your case role does not allow this"},
    status.HTTP_409_CONFLICT: {
        "description": "The case is not open, the name is taken, or the case has 50 views"
    },
    status.HTTP_413_CONTENT_TOO_LARGE: {"description": "The body is larger than 1 MiB"},
    status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many changes; see Retry-After"},
}


def _body_schema(model: type[BaseModel]) -> dict[str, Any]:
    return {
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": model.model_json_schema()}},
        }
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


async def _parse[Body: BaseModel](request: Request, db: AsyncSession, model: type[Body]) -> Body:
    """The body as ``model``: 413 when too large, 422 (without the input) when invalid."""
    try:
        raw = await _read_body(request)
        return model.model_validate_json(raw)
    except HTTPException:
        await db.rollback()
        raise
    except ValidationError as error:
        await db.rollback()
        # Names and keys may be person-related: the input is never echoed.
        raise RequestValidationError(
            [
                {"type": item["type"], "loc": ("body", *item["loc"]), "msg": item["msg"]}
                for item in error.errors(include_url=False)
            ]
        ) from error


async def _refuse(db: AsyncSession, code: int, detail: str) -> NoReturn:
    await db.rollback()
    raise HTTPException(code, detail)


async def _ensure_open(db: AsyncSession, access: CaseAccess) -> None:
    if access.case.status != CaseStatus.OPEN:
        await _refuse(db, status.HTTP_409_CONFLICT, CASE_NOT_OPEN)


async def _limit_writes(db: AsyncSession, cache: Redis, access: CaseAccess) -> None:
    limiter = LayoutRateLimiter(
        cache, max_writes=MAX_WRITES, window_seconds=WINDOW_SECONDS, key_prefix=RATE_KEY_PREFIX
    )
    retry_after = await limiter.hit(access.user.id)
    if retry_after is not None:
        await db.rollback()
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            TOO_MANY_WRITES,
            headers={"Retry-After": str(retry_after)},
        )


async def _record(
    db: AsyncSession, action: AuditAction, access: CaseAccess, view_id: uuid.UUID
) -> None:
    await audit.record(
        db,
        action=action,
        outcome=AuditOutcome.SUCCESS,
        actor=access.actor,
        target_type=TargetType.VIEW,
        target_id=view_id,
        case_id=access.case.id,
    )


async def _commit_or_conflict(db: AsyncSession) -> None:
    """Commits; a name taken meanwhile (the unique index) becomes 409."""
    try:
        await db.commit()
    except IntegrityError as error:  # pragma: no cover - the case lock serializes writes
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, NAME_TAKEN) from error


async def _changeable_view(
    request: Request,
    view_id: uuid.UUID,
    access: CaseAccess,
    db: AsyncSession,
    make_session: async_sessionmaker[AsyncSession],
) -> SavedView:
    """The view, if the caller may change it: its creator or a case owner (403 otherwise)."""
    await _ensure_open(db, access)
    found = await service.get_view(db, access.case.id, view_id)
    if found is None:
        await _refuse(db, status.HTTP_404_NOT_FOUND, VIEW_NOT_FOUND)
    view = found[0]
    if access.role != CaseRole.OWNER and view.created_by != access.user.id:
        global_role, case_id = access.user.role, access.case.id
        await db.rollback()
        await record_denial(
            make_session,
            request,
            actor=access.actor,
            case_id=case_id,
            global_role=global_role,
            reason="not_view_creator",
            case_role=access.role,
            extra={"view_id": str(view_id)},
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, FORBIDDEN)
    return view


async def _filtered_state(
    graph: GraphRepository, case_id: uuid.UUID, state: dict[str, Any]
) -> dict[str, Any]:
    """The state without positions of entities the case no longer has."""
    positions: dict[str, Any] = state.get("positions") or {}
    if not positions:
        return state
    try:
        existing = await graph.existing_keys(case_id, list(positions))
    except (Neo4jError, DriverError) as error:
        logger.error("Graph read failed: %s", type(error).__name__)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, GRAPH_UNAVAILABLE) from error
    kept = {key: xy for key, xy in positions.items() if key in existing}
    return {**state, "positions": kept}


@router.get("", response_model=list[ViewSummary], responses=_ERRORS)
async def list_views(access: Readable, db: DbSession) -> list[ViewSummary]:
    """The case's saved views, by name, without their state (any member)."""
    return await service.list_views(db, access.case.id)


@router.get(
    "/{view_id}",
    response_model=ViewOut,
    responses={
        **_ERRORS,
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Neo4j is unavailable"},
    },
)
async def get_view(view_id: uuid.UUID, access: Readable, db: DbSession, graph: Graph) -> ViewOut:
    """One view with its state (any member).

    Positions of entities deleted since the view was saved are left out.
    """
    found = await service.get_view(db, access.case.id, view_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, VIEW_NOT_FOUND)
    view, creator, updater = found
    state = await _filtered_state(graph, access.case.id, view.state)
    return service.full(view, creator, updater, state)


@router.post(
    "",
    response_model=ViewOut,
    status_code=status.HTTP_201_CREATED,
    responses=_WRITE_ERRORS,
    openapi_extra=_body_schema(ViewCreate),
)
async def create_view(
    request: Request, access: EditableLocked, db: DbSession, cache: Cache
) -> ViewOut:
    """Save a view of the case's graph (editors and owners; the case must be open).

    ``name`` is trimmed, 1-80 characters without control characters, and
    unique in the case ignoring case (409). A case has at most 50 views (409).
    """
    body = await _parse(request, db, ViewCreate)
    await _ensure_open(db, access)
    await _limit_writes(db, cache, access)
    if await service.count_views(db, access.case.id) >= MAX_VIEWS_PER_CASE:
        await _refuse(db, status.HTTP_409_CONFLICT, TOO_MANY_VIEWS)
    if await service.name_taken(db, access.case.id, body.name):
        await _refuse(db, status.HTTP_409_CONFLICT, NAME_TAKEN)
    state = body.state.stored()
    view = await service.create_view(
        db, case_id=access.case.id, user_id=access.user.id, name=body.name, state=state
    )
    await _record(db, AuditAction.VIEW_CREATE, access, view.id)
    author = Author(id=access.user.id, display_name=access.user.display_name)
    out = ViewOut(
        id=view.id,
        name=view.name,
        created_by=author,
        updated_by=author,
        created_at=view.created_at,
        updated_at=view.updated_at,
        state=state,
    )
    await _commit_or_conflict(db)
    return out


@router.patch(
    "/{view_id}",
    response_model=ViewOut,
    responses=_WRITE_ERRORS,
    openapi_extra=_body_schema(ViewUpdate),
)
async def update_view(
    view_id: uuid.UUID,
    request: Request,
    access: EditableLocked,
    db: DbSession,
    make_session: Sessionmaker,
    cache: Cache,
    graph: Graph,
) -> ViewOut:
    """Rename a view and/or replace its state (its creator or a case owner; open case only)."""
    body = await _parse(request, db, ViewUpdate)
    view = await _changeable_view(request, view_id, access, db, make_session)
    await _limit_writes(db, cache, access)
    if body.name is not None and await service.name_taken(
        db, access.case.id, body.name, except_id=view.id
    ):
        await _refuse(db, status.HTTP_409_CONFLICT, NAME_TAKEN)
    service.update_view(
        view,
        user_id=access.user.id,
        name=body.name,
        state=body.state.stored() if body.state is not None else None,
    )
    await db.flush()
    await _record(db, AuditAction.VIEW_UPDATE, access, view.id)
    await _commit_or_conflict(db)
    found = await service.get_view(db, access.case.id, view_id)
    if found is None:  # pragma: no cover - deleted between the commit and this read
        raise HTTPException(status.HTTP_404_NOT_FOUND, VIEW_NOT_FOUND)
    saved, creator, updater = found
    state = await _filtered_state(graph, access.case.id, saved.state)
    return service.full(saved, creator, updater, state)


@router.delete("/{view_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_WRITE_ERRORS)
async def delete_view(
    view_id: uuid.UUID,
    request: Request,
    access: EditableLocked,
    db: DbSession,
    make_session: Sessionmaker,
    cache: Cache,
) -> Response:
    """Delete a view (its creator or a case owner; open case only)."""
    view = await _changeable_view(request, view_id, access, db, make_session)
    await _limit_writes(db, cache, access)
    await db.delete(view)
    await _record(db, AuditAction.VIEW_DELETE, access, view_id)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
