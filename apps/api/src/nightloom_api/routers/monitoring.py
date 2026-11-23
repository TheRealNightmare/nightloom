"""Certificate Transparency monitoring of a case.

``/cases/{id}/ct-watches``: members list the watched domains; owners add a
Domain that is in the graph (open case), ask for a check now (open case) and
remove one (any time). ``/cases/{id}/ct-alerts``: members list the alerts;
editors and owners mark them seen. The API never asks crt.sh itself: the
worker's sweep queues ordinary crt.sh jobs. See
:mod:`nightloom_api.monitoring.service`.
"""

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from neo4j.exceptions import DriverError, Neo4jError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nightloom_api.auth import clock
from nightloom_api.cases.access import Capability, CaseAccess, case_access
from nightloom_api.graph.repository import GraphRepository
from nightloom_api.infra.db import get_session
from nightloom_api.infra.graph import get_graph_repository
from nightloom_api.monitoring import service
from nightloom_api.monitoring.schemas import (
    AcknowledgeBody,
    AcknowledgeOut,
    AlertList,
    AlertOut,
    WatchCreate,
    WatchList,
    WatchOut,
)
from nightloom_api.routers.graph import GRAPH_UNAVAILABLE, ensure_open

logger = logging.getLogger(__name__)

watches_router = APIRouter(prefix="/cases/{case_id}/ct-watches", tags=["monitoring"])
alerts_router = APIRouter(prefix="/cases/{case_id}/ct-alerts", tags=["monitoring"])

DbSession = Annotated[AsyncSession, Depends(get_session)]
Graph = Annotated[GraphRepository, Depends(get_graph_repository)]
Readable = Annotated[CaseAccess, Depends(case_access(Capability.READ))]
OwnerLocked = Annotated[CaseAccess, Depends(case_access(Capability.MANAGE, lock=True))]
Owner = Annotated[CaseAccess, Depends(case_access(Capability.MANAGE))]
Runner = Annotated[CaseAccess, Depends(case_access(Capability.RUN_SHUTTLES))]

NOT_FOUND = "No such watched domain in this case."
NOT_IN_GRAPH = "Add {domain} to the graph first, then watch it."


@watches_router.get("", response_model=WatchList)
async def list_watches(access: Readable, db: DbSession) -> WatchList:
    """The case's watched domains and their last check (members)."""
    rows = await service.list_watches(db, access.case.id)
    return WatchList(items=[WatchOut.build(row) for row in rows])


@watches_router.post(
    "",
    response_model=WatchOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_409_CONFLICT: {"description": "Already watched, or the case is not open"},
    },
)
async def create_watch(
    body: WatchCreate, access: OwnerLocked, db: DbSession, graph: Graph
) -> WatchOut:
    """Watch a Domain of the graph for new certificates (owners, open case).

    Checked at once (the baseline: no alert) and then every hour, as a crt.sh
    job requested by you. At most 20 per case.
    """
    await ensure_open(db, access)
    try:
        domain = service.clean_domain(body.domain)
    except service.MonitoringError as error:
        await db.rollback()
        raise HTTPException(error.status_code, str(error)) from error
    try:
        detail = await graph.get_entity(access.case.id, service.domain_key(domain))
    except (Neo4jError, DriverError) as error:
        await db.rollback()
        logger.error("Graph read failed: %s", type(error).__name__)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, GRAPH_UNAVAILABLE) from error
    if detail is None:
        await db.rollback()
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, NOT_IN_GRAPH.format(domain=domain)
        )
    try:
        row = await service.create_watch(
            db,
            case_id=access.case.id,
            domain=domain,
            user_id=access.user.id,
            actor=access.actor,
            now=clock.utcnow(),
        )
        await db.commit()
    except service.MonitoringError as error:
        await db.rollback()
        raise HTTPException(error.status_code, str(error)) from error
    except IntegrityError as error:  # a concurrent add of the same domain
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "This domain is already watched.") from error
    return WatchOut.build(row)


@watches_router.post(
    "/{watch_id}/check", response_model=WatchOut, status_code=status.HTTP_202_ACCEPTED
)
async def check_watch(watch_id: uuid.UUID, access: OwnerLocked, db: DbSession) -> WatchOut:
    """Check this domain at the worker's next sweep (within a minute; owners, open case)."""
    await ensure_open(db, access)
    row = await service.get_watch(db, access.case.id, watch_id, lock=True)
    if row is None:
        await db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    service.request_check(row, clock.utcnow())
    await db.commit()
    return WatchOut.build(row)


@watches_router.delete(
    "/{watch_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
async def delete_watch(watch_id: uuid.UUID, access: Owner, db: DbSession) -> None:
    """Stop watching (owners; also in a closed case). Its alerts stay."""
    row = await service.get_watch(db, access.case.id, watch_id, lock=True)
    if row is None:
        await db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    await service.delete_watch(db, row, actor=access.actor)
    await db.commit()


@alerts_router.get("", response_model=AlertList)
async def list_alerts(
    access: Readable,
    db: DbSession,
    unseen: Annotated[bool, Query(description="Only alerts nobody marked seen")] = False,
) -> AlertList:
    """The case's newest alerts (at most 50), newest first, and how many are unseen (members)."""
    rows = await service.list_alerts(db, access.case.id, unseen_only=unseen)
    return AlertList(
        items=[AlertOut.build(row) for row in rows],
        unseen=await service.count_unseen(db, access.case.id),
    )


@alerts_router.post("/acknowledge", response_model=AcknowledgeOut)
async def acknowledge_alerts(
    access: Runner, db: DbSession, body: AcknowledgeBody | None = None
) -> AcknowledgeOut:
    """Mark alerts seen for the whole case: the given ones, or all (editors and owners)."""
    count = await service.acknowledge(
        db,
        case_id=access.case.id,
        alert_ids=body.ids if body is not None else None,
        user=access.user,
        actor=access.actor,
        now=clock.utcnow(),
    )
    await db.commit()
    return AcknowledgeOut(acknowledged=count)
