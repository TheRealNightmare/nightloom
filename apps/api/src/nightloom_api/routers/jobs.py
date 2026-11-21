"""Running Shuttles on a case's entities: jobs.

Access works like every other case route (see :mod:`nightloom_api.cases.access`):
non-members get 404, members without the needed capability 403, both audited.
Listing and reading jobs needs ``read``; creating and cancelling them needs
``run_shuttles`` (editors and owners).

A job is queued only after its row and its ``job.create`` event have
committed, so the worker never sees a job that does not exist. The worker
checks every guardrail again when the job starts.
"""

import logging
import uuid
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from neo4j.exceptions import DriverError, Neo4jError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nightloom_api.audit import service as audit
from nightloom_api.audit.actions import AuditAction, AuditOutcome, TargetType
from nightloom_api.audit.listing import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from nightloom_api.cases.access import Capability, CaseAccess, case_access, record_denial
from nightloom_api.cases.service import InvalidCursorError
from nightloom_api.config import Settings, get_settings
from nightloom_api.graph.repository import GraphRepository
from nightloom_api.infra.db import get_session, get_sessionmaker
from nightloom_api.infra.graph import get_graph_repository
from nightloom_api.jobs import service
from nightloom_api.jobs.models import JobStatus
from nightloom_api.jobs.queue import JobQueue, get_job_queue
from nightloom_api.jobs.schemas import JobCreate, JobOut, JobPage
from nightloom_api.routers.graph import GRAPH_UNAVAILABLE, canonical_key, ensure_open
from nightloom_sdk.registry import ShuttleRegistry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cases/{case_id}/jobs", tags=["jobs"])

DbSession = Annotated[AsyncSession, Depends(get_session)]
Sessionmaker = Annotated[async_sessionmaker[AsyncSession], Depends(get_sessionmaker)]
Readable = Annotated[CaseAccess, Depends(case_access(Capability.READ))]
RunnerLocked = Annotated[CaseAccess, Depends(case_access(Capability.RUN_SHUTTLES, lock=True))]
Runner = Annotated[CaseAccess, Depends(case_access(Capability.RUN_SHUTTLES))]
Registry = Annotated[ShuttleRegistry, Depends(service.get_shuttle_registry)]
Queue = Annotated[JobQueue, Depends(get_job_queue)]

_ERRORS: dict[int | str, dict[str, object]] = {
    status.HTTP_403_FORBIDDEN: {"description": "Your case role does not allow this"},
    status.HTTP_404_NOT_FOUND: {"description": "No such case or job, or you are not a member"},
}


async def _refuse(
    db: AsyncSession,
    make_session: async_sessionmaker[AsyncSession],
    request: Request,
    access: CaseAccess,
    error: service.JobError,
    *,
    shuttle: str,
) -> NoReturn:
    """Answer a refused job request; 403s are recorded as authz.denied, 429s as job.create."""
    global_role, case_id, actor = access.user.role, access.case.id, access.actor
    await db.rollback()  # release the case lock before recording
    if error.status_code == status.HTTP_403_FORBIDDEN:
        await record_denial(
            make_session,
            request,
            actor=actor,
            case_id=case_id,
            global_role=global_role,
            reason=error.reason,
            case_role=access.role,
            extra={"shuttle": shuttle},
        )
    elif error.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
        await audit.record_standalone(
            make_session,
            action=AuditAction.JOB_CREATE,
            outcome=AuditOutcome.FAILURE,
            actor=actor,
            target_type=TargetType.JOB,
            case_id=case_id,
            details={"shuttle": shuttle, "reason": error.reason},
        )
    raise HTTPException(error.status_code, str(error)) from error


@router.post(
    "",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        **_ERRORS,
        status.HTTP_409_CONFLICT: {"description": "The case is not open"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "Unknown Shuttle, input type not accepted, or no such entity"
        },
        status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many queued or running jobs"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Neo4j or the queue is unavailable"},
    },
)
async def create_job(
    body: JobCreate,
    request: Request,
    access: RunnerLocked,
    db: DbSession,
    make_session: Sessionmaker,
    graph: Annotated[GraphRepository, Depends(get_graph_repository)],
    registry: Registry,
    queue: Queue,
    settings: Annotated[Settings, Depends(get_settings)],
) -> JobOut:
    """Run a Shuttle on an entity of this case (editors and owners; the case must be open).

    The Shuttle must accept the entity's type and be passive (active Shuttles
    are refused with 403 until ownership verification exists); a
    person-related Shuttle needs a case that allows person entities (403).
    Each user may have at most five queued or running jobs (429).

    With ``fresh: true`` the Shuttle runs even if its recent results are
    cached for this entity (see :mod:`nightloom_api.jobs.result_cache`); the
    run refreshes the cache.
    """
    await ensure_open(db, access)
    entity_type = canonical_key(body.entity_key, "entity_key")
    shuttle = registry.get(body.shuttle)
    if shuttle is None:
        await db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown Shuttle.")
    meta = shuttle.meta
    try:
        service.check_shuttle(
            meta, input_type=entity_type, allow_person_entities=access.case.allow_person_entities
        )
    except service.GuardrailError as error:
        await _refuse(db, make_session, request, access, error, shuttle=meta.name)

    try:
        detail = await graph.get_entity(access.case.id, body.entity_key)
    except (Neo4jError, DriverError) as error:
        await db.rollback()
        logger.error("Graph read failed: %s", type(error).__name__)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, GRAPH_UNAVAILABLE) from error
    if detail is None:
        await db.rollback()
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, str(service.EntityNotFoundError())
        )

    try:
        job = await service.create_job(
            db,
            case=access.case,
            meta=meta,
            input_key=body.entity_key,
            user=access.user,
            actor=access.actor,
            limit=settings.max_active_jobs_per_user,
            use_cache=not body.fresh,
        )
    except service.TooManyJobsError as error:
        await _refuse(db, make_session, request, access, error, shuttle=meta.name)

    # Only now, with the job committed, may the worker see it.
    try:
        await queue.enqueue(job.id, timeout_seconds=meta.timeout_seconds)
    except Exception as error:
        logger.error("Could not queue job %s: %s", job.id, type(error).__name__)
        await service.finish_job(
            db,
            job,
            status=JobStatus.FAILED,
            actor=access.actor,
            error=service.ERROR_QUEUE,
            reason="queue_unavailable",
        )
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, service.ERROR_QUEUE) from error
    return JobOut.build(job, registry)


@router.get("", response_model=JobPage, responses=_ERRORS)
async def list_jobs(
    access: Readable,
    db: DbSession,
    registry: Registry,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    status_filter: Annotated[JobStatus | None, Query(alias="status")] = None,
) -> JobPage:
    """The case's jobs (any member), newest first; pass ``next_cursor`` back as ``cursor``."""
    try:
        listing = await service.list_jobs(
            db, access.case.id, limit=limit, cursor=cursor, status=status_filter
        )
    except InvalidCursorError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    return JobPage(
        items=[JobOut.build(job, registry) for job in listing.items],
        next_cursor=listing.next_cursor,
    )


@router.get("/{job_id}", response_model=JobOut, responses=_ERRORS)
async def get_job(job_id: uuid.UUID, access: Readable, db: DbSession, registry: Registry) -> JobOut:
    """One job of the case (any member)."""
    try:
        job = await service.get_job(db, access.case.id, job_id)
    except service.JobNotFoundError as error:
        raise HTTPException(error.status_code, str(error)) from error
    return JobOut.build(job, registry)


@router.post(
    "/{job_id}/cancel",
    response_model=JobOut,
    responses={**_ERRORS, status.HTTP_409_CONFLICT: {"description": "The job already finished"}},
)
async def cancel_job(
    job_id: uuid.UUID, access: Runner, db: DbSession, registry: Registry
) -> JobOut:
    """Cancel a job (editors and owners), also in a closed case.

    A queued job is cancelled at once; a running one is asked to stop and
    becomes ``cancelled`` when the worker notices (between findings).
    """
    try:
        job = await service.get_job(db, access.case.id, job_id, lock=True)
        job = await service.cancel_job(db, job, actor=access.actor)
    except service.JobError as error:
        await db.rollback()
        raise HTTPException(error.status_code, str(error)) from error
    return JobOut.build(job, registry)
