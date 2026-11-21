"""Scan profiles (``/scan-profiles``) and profile runs (``/cases/{id}/scan-runs``).

Any signed-in user lists the profiles; admins create, change and delete
custom ones (built-in profiles cannot be changed). Editors and owners run a
profile on up to 25 entities of an open case; members read runs; editors
and owners cancel them (also in a closed case). See
:mod:`nightloom_api.scans.service` for how a run releases its jobs.
"""

import logging
import uuid
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from neo4j.exceptions import DriverError, Neo4jError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nightloom_api.audit import service as audit
from nightloom_api.audit.actions import AuditAction, AuditOutcome, TargetType
from nightloom_api.audit.service import Actor
from nightloom_api.auth.deps import AdminUser, CurrentUser, client_ip
from nightloom_api.cases.access import Capability, CaseAccess, case_access
from nightloom_api.config import Settings, get_settings
from nightloom_api.graph.repository import GraphRepository
from nightloom_api.infra.db import get_session, get_sessionmaker
from nightloom_api.infra.graph import get_graph_repository
from nightloom_api.jobs.queue import JobQueue, get_job_queue
from nightloom_api.jobs.service import get_shuttle_registry
from nightloom_api.routers.graph import GRAPH_UNAVAILABLE, canonical_key, ensure_open
from nightloom_api.scans import profiles, service
from nightloom_api.scans.models import ScanProfile
from nightloom_api.scans.schemas import (
    ProfileCreate,
    ProfileOut,
    ProfileUpdate,
    RunCreate,
    RunOut,
    RunPage,
)
from nightloom_core.entities import EntityType
from nightloom_sdk.registry import ShuttleRegistry

logger = logging.getLogger(__name__)

profiles_router = APIRouter(prefix="/scan-profiles", tags=["scan profiles"])
runs_router = APIRouter(prefix="/cases/{case_id}/scan-runs", tags=["scan profiles"])

DbSession = Annotated[AsyncSession, Depends(get_session)]
Sessionmaker = Annotated[async_sessionmaker[AsyncSession], Depends(get_sessionmaker)]
Registry = Annotated[ShuttleRegistry, Depends(get_shuttle_registry)]
Readable = Annotated[CaseAccess, Depends(case_access(Capability.READ))]
RunnerLocked = Annotated[CaseAccess, Depends(case_access(Capability.RUN_SHUTTLES, lock=True))]
Runner = Annotated[CaseAccess, Depends(case_access(Capability.RUN_SHUTTLES))]

BUILT_IN_READ_ONLY = "Built-in profiles cannot be changed or deleted."
UNKNOWN_PROFILE = "Unknown profile."
NAME_TAKEN = "A profile with this name already exists."
PIVOT_INFRASTRUCTURE_ONLY = (
    "Auto-pivot starts only from infrastructure entities, such as domains and IP addresses."
)
AUTO_PIVOT_OFF = (
    "Auto-pivot is off for this case. A case owner can turn it on in the case's settings."
)
# pg_advisory_xact_lock key for "scan profile changes" ("nsp").
_PROFILES_LOCK = 0x6E7370


def _raise(error: Exception, status_code: int) -> NoReturn:
    raise HTTPException(status_code, str(error)) from error


# --- profiles --------------------------------------------------------------------------------


@profiles_router.get("", response_model=list[ProfileOut])
async def list_profiles(_user: CurrentUser, db: DbSession, registry: Registry) -> list[ProfileOut]:
    """Every profile: the built-in ones first, then the admins' by name."""
    return [ProfileOut.build(profile, registry) for profile in await profiles.list_profiles(db)]


def _clean_shuttles(names: list[str], registry: ShuttleRegistry) -> tuple[str, ...]:
    try:
        return profiles.check_shuttles(names, registry)
    except profiles.ProfileError as error:
        _raise(error, error.status_code)


@profiles_router.post("", response_model=ProfileOut, status_code=status.HTTP_201_CREATED)
async def create_profile(
    body: ProfileCreate, request: Request, admin: AdminUser, db: DbSession, registry: Registry
) -> ProfileOut:
    """Add a profile every user can run (admins; at most 50 custom profiles)."""
    try:
        name = profiles.clean_name(body.name)
        description = profiles.clean_description(body.description)
    except profiles.ProfileError as error:
        _raise(error, error.status_code)
    shuttles = _clean_shuttles(body.shuttles, registry)
    await _lock_profiles(db)
    if await profiles.name_taken(db, name):
        raise HTTPException(status.HTTP_409_CONFLICT, NAME_TAKEN)
    if await profiles.count_custom(db) >= profiles.MAX_CUSTOM_PROFILES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"There are already {profiles.MAX_CUSTOM_PROFILES} profiles. Delete one first.",
        )
    row = ScanProfile(
        name=name, description=description, shuttles=list(shuttles), created_by=admin.id
    )
    db.add(row)
    await db.flush()
    await audit.record(
        db,
        action=AuditAction.SCAN_PROFILE_CREATE,
        outcome=AuditOutcome.SUCCESS,
        actor=Actor.for_user(admin, client_ip(request)),
        target_type=TargetType.SCAN_PROFILE,
        target_id=row.id,
        details={"name": name, "shuttles": list(shuttles)},
    )
    await _commit_or_conflict(db)
    profile = await profiles.get_profile(db, str(row.id))
    assert profile is not None  # noqa: S101 - just created
    return ProfileOut.build(profile, registry)


async def _lock_profiles(db: AsyncSession) -> None:
    """Serialize profile changes for the rest of the transaction (name and count checks)."""
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _PROFILES_LOCK})


async def _commit_or_conflict(db: AsyncSession) -> None:
    try:
        await db.commit()
    except IntegrityError as error:  # two admins chose the same name at once
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, NAME_TAKEN) from error


async def _custom_row(db: AsyncSession, profile_id: str) -> ScanProfile:
    if profiles.is_built_in(profile_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, BUILT_IN_READ_ONLY)
    row = await profiles.get_row(db, profile_id, lock=True)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Profile not found.")
    return row


@profiles_router.patch("/{profile_id}", response_model=ProfileOut)
async def update_profile(
    profile_id: str,
    body: ProfileUpdate,
    request: Request,
    admin: AdminUser,
    db: DbSession,
    registry: Registry,
) -> ProfileOut:
    """Rename a custom profile, or change its description or Shuttles (admins)."""
    await _lock_profiles(db)
    row = await _custom_row(db, profile_id)
    changed: list[str] = []
    try:
        if body.name is not None:
            name = profiles.clean_name(body.name)
            if name != row.name:
                if await profiles.name_taken(db, name, other_than=row.id):
                    raise HTTPException(status.HTTP_409_CONFLICT, NAME_TAKEN)
                row.name = name
                changed.append("name")
        if body.description is not None:
            description = profiles.clean_description(body.description)
            if description != row.description:
                row.description = description
                changed.append("description")
    except profiles.ProfileError as error:
        _raise(error, error.status_code)
    if body.shuttles is not None:
        shuttles = list(_clean_shuttles(body.shuttles, registry))
        if shuttles != list(row.shuttles):
            row.shuttles = shuttles
            changed.append("shuttles")
    if changed:
        await audit.record(
            db,
            action=AuditAction.SCAN_PROFILE_UPDATE,
            outcome=AuditOutcome.SUCCESS,
            actor=Actor.for_user(admin, client_ip(request)),
            target_type=TargetType.SCAN_PROFILE,
            target_id=row.id,
            details={"name": row.name, "changed": changed, "shuttles": list(row.shuttles)},
        )
    await _commit_or_conflict(db)
    profile = await profiles.get_profile(db, profile_id)
    assert profile is not None  # noqa: S101 - it exists
    return ProfileOut.build(profile, registry)


@profiles_router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(
    profile_id: str, request: Request, admin: AdminUser, db: DbSession
) -> Response:
    """Delete a custom profile (admins). Runs that used it keep its name."""
    row = await _custom_row(db, profile_id)
    await db.delete(row)
    await audit.record(
        db,
        action=AuditAction.SCAN_PROFILE_DELETE,
        outcome=AuditOutcome.SUCCESS,
        actor=Actor.for_user(admin, client_ip(request)),
        target_type=TargetType.SCAN_PROFILE,
        target_id=row.id,
        details={"name": row.name},
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- runs ------------------------------------------------------------------------------------


async def _run_out(db: AsyncSession, run_id: uuid.UUID, case_id: uuid.UUID) -> RunOut:
    # Releasing (another session) may have changed the run since this one loaded it.
    db.expire_all()
    run = await service.get_run(db, case_id, run_id)
    counts = await service.job_counts(db, [run.id])
    return RunOut.build(run, counts[run.id])


@runs_router.post(
    "",
    response_model=RunOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_409_CONFLICT: {"description": "The case is not open"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "Unknown profile, bad or missing entities, nothing to run, too many jobs"
        },
        status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Two runs already in progress"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Neo4j is unavailable"},
    },
)
async def create_run(
    body: RunCreate,
    access: RunnerLocked,
    db: DbSession,
    make_session: Sessionmaker,
    graph: Annotated[GraphRepository, Depends(get_graph_repository)],
    registry: Registry,
    queue: Annotated[JobQueue, Depends(get_job_queue)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RunOut:
    """Run a profile's Shuttles on up to 25 entities of this case (editors and owners).

    Every (Shuttle, entity) pair the guardrails allow is planned; the jobs are
    queued as the caller's job slots free up (at most 100 per run, two runs
    in progress per user). ``fresh: true`` skips the result cache.
    ``pivot: true`` (only where the case allows auto-pivot, else 403) also
    continues from the infrastructure entities the run finds, up to 2 hops,
    200 jobs and 500 new entities.
    """
    await ensure_open(db, access)
    # A rollback expires the loaded case and user: keep what is needed after one.
    case_id, user_id, actor = access.case.id, access.user.id, access.actor
    if body.pivot and not access.case.allow_auto_pivot:
        await db.rollback()
        await audit.record_standalone(
            make_session,
            action=AuditAction.SCAN_RUN_CREATE,
            outcome=AuditOutcome.DENIED,
            actor=actor,
            target_type=TargetType.SCAN_RUN,
            case_id=case_id,
            details={"profile": body.profile_id[:64], "reason": "auto_pivot_disabled"},
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, AUTO_PIVOT_OFF)
    profile = await profiles.get_profile(db, body.profile_id)
    if profile is None:
        await db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, UNKNOWN_PROFILE)
    keys = list(dict.fromkeys(body.entity_keys))
    entities: list[tuple[str, EntityType]] = []
    for key in keys:
        entity_type = canonical_key(key, "entity_keys")
        try:
            detail = await graph.get_entity(access.case.id, key)
        except (Neo4jError, DriverError) as error:
            await db.rollback()
            logger.error("Graph read failed: %s", type(error).__name__)
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, GRAPH_UNAVAILABLE) from error
        if detail is None:
            await db.rollback()
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "entity_keys must name entities in this case.",
            )
        entities.append((key, entity_type))
    if body.pivot and any(not service.pivots_through(t) for _, t in entities):
        await db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, PIVOT_INFRASTRUCTURE_ONLY)
    plan = service.plan_run(
        profile,
        entities,
        registry,
        # Person-related Shuttles never run in a pivot, whatever the case allows.
        allow_person_entities=access.case.allow_person_entities and not body.pivot,
    )
    try:
        run = await service.create_run(
            db,
            case=access.case,
            profile=profile,
            plan=plan,
            entities=len(entities),
            user=access.user,
            actor=access.actor,
            use_cache=not body.fresh,
            pivot=body.pivot,
            seeds=keys,
        )
    except service.RunError as error:
        await db.rollback()
        if isinstance(error, service.TooManyRunsError):
            await audit.record_standalone(
                make_session,
                action=AuditAction.SCAN_RUN_CREATE,
                outcome=AuditOutcome.FAILURE,
                actor=actor,
                target_type=TargetType.SCAN_RUN,
                case_id=case_id,
                details={"profile": profile.id, "reason": error.reason},
            )
        raise HTTPException(error.status_code, str(error)) from error
    await service.release_and_enqueue(
        make_session,
        queue,
        user_id=user_id,
        limit=settings.max_active_jobs_per_user,
        registry=registry,
    )
    return await _run_out(db, run.id, case_id)


@runs_router.get("", response_model=RunPage)
async def list_runs(
    access: Readable,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> RunPage:
    """The case's profile runs (any member), newest first."""
    runs = await service.list_runs(db, access.case.id, limit=limit)
    counts = await service.job_counts(db, [run.id for run in runs])
    return RunPage(items=[RunOut.build(run, counts[run.id]) for run in runs])


@runs_router.get("/{run_id}", response_model=RunOut)
async def get_run(run_id: uuid.UUID, access: Readable, db: DbSession) -> RunOut:
    """One profile run of the case (any member), with its jobs by status."""
    try:
        return await _run_out(db, run_id, access.case.id)
    except service.RunError as error:
        raise HTTPException(error.status_code, str(error)) from error


@runs_router.post(
    "/{run_id}/cancel",
    response_model=RunOut,
    responses={status.HTTP_409_CONFLICT: {"description": "The run already ended"}},
)
async def cancel_run(run_id: uuid.UUID, access: Runner, db: DbSession) -> RunOut:
    """Cancel a run (editors and owners, also in a closed case): nothing more is queued,
    queued jobs are cancelled and running ones asked to stop."""
    try:
        run = await service.get_run(db, access.case.id, run_id, lock=True)
        await service.cancel_run(db, run, actor=access.actor)
    except service.RunError as error:
        await db.rollback()
        raise HTTPException(error.status_code, str(error)) from error
    return await _run_out(db, run_id, access.case.id)
