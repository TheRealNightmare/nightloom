"""A case's investigation graph: entities and relationships in Neo4j.

Access works like every other case route (see :mod:`nightloom_api.cases.access`):
non-members get 404, members without the needed capability 403, both audited.
Reading needs ``read``; changing the graph needs ``edit`` and an open case.

Writes take the case row lock for the whole request, so a case cannot be
closed or deleted half-way through a graph change. The graph change and its
audit event are made in two databases, ordered so that an event is never
missing for a change that happened: the Neo4j transaction does the work, the
audit event commits in PostgreSQL, then Neo4j commits. If that last commit
fails, a ``failure`` event follows the ``success`` one and the client gets 503.
"""

import hashlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from neo4j.exceptions import DriverError, Neo4jError
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nightloom_api.audit import service as audit
from nightloom_api.audit.actions import AuditAction, AuditOutcome, TargetType
from nightloom_api.auth import clock
from nightloom_api.cases.access import Capability, CaseAccess, case_access, record_denial
from nightloom_api.cases.models import CaseStatus
from nightloom_api.graph.repository import (
    DEFAULT_GRAPH_LIMIT,
    EndpointNotFoundError,
    GraphCommitError,
    GraphRepository,
    PersonEntitiesDisabledError,
    RelationshipNotAllowedError,
)
from nightloom_api.graph.schemas import (
    EntityCreate,
    EntityDetailOut,
    EntityOut,
    GraphOut,
    RelationshipCreate,
    RelationshipOut,
)
from nightloom_api.infra.db import get_session, get_sessionmaker
from nightloom_api.infra.graph import get_graph_repository
from nightloom_core.entities import EntityType, Sensitivity, build_entity, sensitivity_of
from nightloom_core.normalize import NormalizationError, parse_key
from nightloom_core.provenance import manual_observation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cases/{case_id}", tags=["graph"])

DbSession = Annotated[AsyncSession, Depends(get_session)]
Sessionmaker = Annotated[async_sessionmaker[AsyncSession], Depends(get_sessionmaker)]
Graph = Annotated[GraphRepository, Depends(get_graph_repository)]
Readable = Annotated[CaseAccess, Depends(case_access(Capability.READ))]
EditableLocked = Annotated[CaseAccess, Depends(case_access(Capability.EDIT, lock=True))]

MAX_GRAPH_LIMIT = 10_000

GRAPH_UNAVAILABLE = "The graph database is unavailable. Try again later."
CASE_NOT_OPEN = "This case is not open. Reopen it to change its graph."
PERSON_ENTITIES_DISABLED = "This case does not allow person-related entities."
ENTITY_NOT_FOUND = "Entity not found."

_ERRORS: dict[int | str, dict[str, object]] = {
    status.HTTP_403_FORBIDDEN: {"description": "Your case role does not allow this"},
    status.HTTP_404_NOT_FOUND: {"description": "No such case, or you are not a member"},
    status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Neo4j is unavailable"},
}
_WRITE_ERRORS: dict[int | str, dict[str, object]] = {
    **_ERRORS,
    status.HTTP_409_CONFLICT: {"description": "The case is not open"},
}


# --- helpers ------------------------------------------------------------------------


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def key_details(key: str, sensitivity: Sensitivity, name: str = "key") -> dict[str, str]:
    """How an entity key appears in an audit event.

    Audit events outlive the case, so person-related keys (an email address,
    a name, a phone number) are recorded only as their SHA-256: enough to
    correlate events, without keeping the personal data after the case's
    graph is deleted.
    """
    if sensitivity == Sensitivity.PERSON:
        return {f"{name}_sha256": _digest(key)}
    return {name: key}


def audit_target(text: str, *, personal: bool) -> str:
    return f"sha256:{_digest(text)}" if personal else text


def canonical_key(key: str, field: str = "key") -> EntityType:
    """The key's entity type; 422 unless ``key`` is a canonical entity key."""
    try:
        return parse_key(key)[0]
    except NormalizationError as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"{field} is not a canonical entity key."
        ) from error


async def ensure_open(db: AsyncSession, access: CaseAccess) -> None:
    if access.case.status != CaseStatus.OPEN:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, CASE_NOT_OPEN)


async def deny_person_entities(
    db: AsyncSession,
    make_session: async_sessionmaker[AsyncSession],
    request: Request,
    access: CaseAccess,
    entity_type: EntityType | None = None,
) -> NoReturn:
    """403 for a person-related entity in a case that does not allow them, audited."""
    global_role, case_id = access.user.role, access.case.id
    await db.rollback()
    await record_denial(
        make_session,
        request,
        actor=access.actor,
        case_id=case_id,
        global_role=global_role,
        reason="person_entities_disabled",
        case_role=access.role,
        extra={"entity_type": entity_type.value} if entity_type else None,
    )
    raise HTTPException(status.HTTP_403_FORBIDDEN, PERSON_ENTITIES_DISABLED)


@asynccontextmanager
async def graph_errors(
    db: AsyncSession,
    make_session: async_sessionmaker[AsyncSession],
    *,
    action: AuditAction,
    access: CaseAccess,
    target_type: TargetType | None = None,
) -> AsyncIterator[None]:
    """Turn Neo4j failures into 503.

    Only the exception type and Neo4j's status code are logged: driver
    messages can quote parameter values, such as an entity key.
    """
    case_id, actor = access.case.id, access.actor
    try:
        yield
    except GraphCommitError as error:
        # The success event is already committed; record that the change did not happen.
        logger.error("Graph commit failed for %s: %s", action.value, _cause(error))
        await audit.record_standalone(
            make_session,
            action=action,
            outcome=AuditOutcome.FAILURE,
            actor=actor,
            target_type=target_type,
            case_id=case_id,
            details={"reason": "graph_commit_failed"},
        )
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, GRAPH_UNAVAILABLE) from error
    except (Neo4jError, DriverError) as error:
        await db.rollback()
        logger.error("Graph operation %s failed: %s", action.value, _cause(error))
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, GRAPH_UNAVAILABLE) from error


def _cause(error: BaseException) -> str:
    cause = error.__cause__ or error
    code = getattr(cause, "code", None)
    return f"{type(cause).__name__}{f' ({code})' if code else ''}"


def properties_error(error: ValidationError) -> RequestValidationError:
    """A 422 for invalid ``properties``, located under ``body``, without the input."""
    return RequestValidationError(
        [
            {"type": item["type"], "loc": ("body", *item["loc"]), "msg": item["msg"]}
            for item in error.errors()
        ]
    )


# --- entities -------------------------------------------------------------------------


@router.post(
    "/entities",
    response_model=EntityOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        **_WRITE_ERRORS,
        status.HTTP_200_OK: {"model": EntityOut, "description": "Merged into an existing entity"},
    },
)
async def create_entity(
    body: EntityCreate,
    request: Request,
    response: Response,
    access: EditableLocked,
    db: DbSession,
    make_session: Sessionmaker,
    graph: Graph,
) -> EntityOut:
    """Add an entity by hand (editors and owners; the case must be open).

    The value is normalized to the type's canonical form (422 with the reason
    when it cannot be). Adding something the case already has merges a new
    observation into it (200) instead of creating a node (201). The
    observation is recorded as ``source: manual`` with the manual Admiralty
    defaults (B2, confidence 0.8). Person-related types need a case that
    allows person entities (403 otherwise).
    """
    await ensure_open(db, access)
    if sensitivity_of(body.type) == Sensitivity.PERSON and not access.case.allow_person_entities:
        await deny_person_entities(db, make_session, request, access, body.type)
    try:
        entity = build_entity(
            body.type, body.value, body.properties, phone_region=body.phone_region
        )
    except NormalizationError as error:
        await db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    except ValidationError as error:
        await db.rollback()
        raise properties_error(error) from error

    observation = manual_observation(clock.utcnow(), body.note)
    personal = entity.sensitivity == Sensitivity.PERSON
    async with (
        graph_errors(
            db,
            make_session,
            action=AuditAction.ENTITY_CREATE,
            access=access,
            target_type=TargetType.ENTITY,
        ),
        graph.write() as ops,
    ):
        record = await ops.upsert_entity(access.case.id, entity, observation)
        await audit.record(
            db,
            action=AuditAction.ENTITY_CREATE,
            outcome=AuditOutcome.SUCCESS,
            actor=access.actor,
            target_type=TargetType.ENTITY,
            target_id=audit_target(entity.key, personal=personal),
            case_id=access.case.id,
            details={
                "type": entity.entity_type.value,
                **key_details(entity.key, entity.sensitivity),
            },
        )
        await db.commit()
    if not record.created:
        response.status_code = status.HTTP_200_OK
    return EntityOut.build(record)


@router.get("/entities/{key:path}", response_model=EntityDetailOut, responses=_ERRORS)
async def get_entity(key: str, access: Readable, graph: Graph) -> EntityDetailOut:
    """One entity with its number of neighbours (any member).

    ``key`` is the canonical key; percent-encode it in the URL. A key that is
    not canonical gets 422, an unknown one 404.
    """
    canonical_key(key)
    try:
        detail = await graph.get_entity(access.case.id, key)
    except (Neo4jError, DriverError) as error:
        logger.error("Graph read failed: %s", _cause(error))
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, GRAPH_UNAVAILABLE) from error
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, ENTITY_NOT_FOUND)
    return EntityDetailOut.from_detail(detail)


@router.delete(
    "/entities/{key:path}", status_code=status.HTTP_204_NO_CONTENT, responses=_WRITE_ERRORS
)
async def delete_entity(
    key: str, access: EditableLocked, db: DbSession, make_session: Sessionmaker, graph: Graph
) -> None:
    """Delete an entity and its relationships (editors and owners; the case must be open)."""
    await ensure_open(db, access)
    canonical_key(key)
    async with (
        graph_errors(
            db,
            make_session,
            action=AuditAction.ENTITY_DELETE,
            access=access,
            target_type=TargetType.ENTITY,
        ),
        graph.write() as ops,
    ):
        deleted = await ops.delete_entity(access.case.id, key)
        if deleted is None:
            await db.rollback()
            raise HTTPException(status.HTTP_404_NOT_FOUND, ENTITY_NOT_FOUND)
        personal = deleted.sensitivity == Sensitivity.PERSON
        await audit.record(
            db,
            action=AuditAction.ENTITY_DELETE,
            outcome=AuditOutcome.SUCCESS,
            actor=access.actor,
            target_type=TargetType.ENTITY,
            target_id=audit_target(key, personal=personal),
            case_id=access.case.id,
            details={"type": deleted.type.value, **key_details(key, deleted.sensitivity)},
        )
        await db.commit()


# --- relationships ------------------------------------------------------------------------


@router.post(
    "/relationships",
    response_model=RelationshipOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        **_WRITE_ERRORS,
        status.HTTP_200_OK: {
            "model": RelationshipOut,
            "description": "Merged into an existing one",
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "Unknown endpoint, or a pair of types the relationship does not allow"
        },
    },
)
async def create_relationship(
    body: RelationshipCreate,
    request: Request,
    response: Response,
    access: EditableLocked,
    db: DbSession,
    make_session: Sessionmaker,
    graph: Graph,
) -> RelationshipOut:
    """Connect two entities of this case (editors and owners; the case must be open).

    The pair of entity types must be allowed for the relationship type, and
    ``RELATED_TO`` needs a ``note``. Repeating an existing relationship merges
    a new observation into it (200).
    """
    await ensure_open(db, access)
    canonical_key(body.source_key, "source_key")
    canonical_key(body.target_key, "target_key")
    observation = manual_observation(clock.utcnow(), body.note)
    case_id = access.case.id
    try:
        async with (
            graph_errors(
                db,
                make_session,
                action=AuditAction.RELATIONSHIP_CREATE,
                access=access,
                target_type=TargetType.RELATIONSHIP,
            ),
            graph.write() as ops,
        ):
            record = await ops.upsert_relationship(
                case_id,
                body.type,
                body.source_key,
                body.target_key,
                observation,
                allow_person_entities=access.case.allow_person_entities,
            )
            # Unknown sensitivity is treated as personal (hashed), never logged in clear.
            source_sensitivity = record.source_sensitivity or Sensitivity.PERSON
            target_sensitivity = record.target_sensitivity or Sensitivity.PERSON
            personal = Sensitivity.PERSON in (source_sensitivity, target_sensitivity)
            await audit.record(
                db,
                action=AuditAction.RELATIONSHIP_CREATE,
                outcome=AuditOutcome.SUCCESS,
                actor=access.actor,
                target_type=TargetType.RELATIONSHIP,
                target_id=audit_target(record.id, personal=personal),
                case_id=case_id,
                details={
                    "type": record.type.value,
                    **key_details(record.source_key, source_sensitivity, "source_key"),
                    **key_details(record.target_key, target_sensitivity, "target_key"),
                },
            )
            await db.commit()
    except PersonEntitiesDisabledError:
        await deny_person_entities(db, make_session, request, access)
    except (EndpointNotFoundError, RelationshipNotAllowedError) as error:
        await db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    if not record.created:
        response.status_code = status.HTTP_200_OK
    return RelationshipOut.build(record)


# --- the whole graph ------------------------------------------------------------------------


@router.get("/graph", response_model=GraphOut, responses=_ERRORS)
async def get_graph(
    access: Readable,
    graph: Graph,
    types: Annotated[list[EntityType] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_GRAPH_LIMIT)] = DEFAULT_GRAPH_LIMIT,
) -> GraphOut:
    """The case's graph (any member): up to ``limit`` nodes, ordered by key.

    ``types`` (repeatable) keeps only those entity types. Edges are those
    between the returned nodes, at most five per node. ``truncated`` tells
    when either limit cut something off.
    """
    case_id = access.case.id
    try:
        snapshot = await graph.get_graph(case_id, types, limit)
    except (Neo4jError, DriverError) as error:
        logger.error("Graph read failed: %s", _cause(error))
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, GRAPH_UNAVAILABLE) from error
    return GraphOut.build(snapshot)
