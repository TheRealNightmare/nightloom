"""Ownership verifications of a case (``/cases/{id}/verifications``).

Members list them; owners add a domain (open case), ask for a check (open
case) and remove one (any time: removing only takes permission away). The
API never looks for the proof itself: *Check now* queues the worker's check
and answers at once with ``checking: true``. See
:mod:`nightloom_api.ownership.service`.
"""

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nightloom_api.auth import clock
from nightloom_api.cases.access import Capability, CaseAccess, case_access
from nightloom_api.infra.db import get_session
from nightloom_api.jobs.queue import JobQueue, get_job_queue
from nightloom_api.ownership import service
from nightloom_api.ownership.schemas import VerificationCreate, VerificationList, VerificationOut
from nightloom_api.routers.graph import ensure_open

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cases/{case_id}/verifications", tags=["ownership"])

DbSession = Annotated[AsyncSession, Depends(get_session)]
Queue = Annotated[JobQueue, Depends(get_job_queue)]
Readable = Annotated[CaseAccess, Depends(case_access(Capability.READ))]
OwnerLocked = Annotated[CaseAccess, Depends(case_access(Capability.MANAGE, lock=True))]
Owner = Annotated[CaseAccess, Depends(case_access(Capability.MANAGE))]

NOT_FOUND = "No such verification in this case."


@router.get("", response_model=VerificationList)
async def list_verifications(access: Readable, db: DbSession) -> VerificationList:
    """The case's domains and whether each is verified (members)."""
    rows = await service.list_for_case(db, access.case.id)
    return VerificationList(items=[VerificationOut.build(row) for row in rows])


@router.post(
    "",
    response_model=VerificationOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_409_CONFLICT: {"description": "Already listed, or the case is not open"},
    },
)
async def create_verification(
    body: VerificationCreate, access: OwnerLocked, db: DbSession
) -> VerificationOut:
    """Add a domain to verify (owners, open case). Publish its ``proof``, then check it.

    Verifying a domain covers its subdomains. At most 50 per case; public
    suffixes (``co.uk``) and wildcards are refused.
    """
    await ensure_open(db, access)
    try:
        domain = service.clean_domain(body.domain)
        row = await service.create(
            db,
            case_id=access.case.id,
            domain=domain,
            method=body.method,
            user_id=access.user.id,
            actor=access.actor,
        )
        await db.commit()
    except service.OwnershipError as error:
        await db.rollback()
        raise HTTPException(error.status_code, str(error)) from error
    except IntegrityError as error:  # a concurrent add of the same domain
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This domain is already in the list."
        ) from error
    return VerificationOut.build(row)


@router.post(
    "/{verification_id}/check",
    response_model=VerificationOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def check_verification(
    verification_id: uuid.UUID, access: OwnerLocked, db: DbSession, queue: Queue
) -> VerificationOut:
    """Ask the worker to look for the proof now (owners, open case).

    A found proof verifies the domain for 30 days from now (renewing it if it
    was verified). Answers at once; poll the verification for the result.
    """
    await ensure_open(db, access)
    row = await service.get(db, access.case.id, verification_id, lock=True)
    if row is None:
        await db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    send = service.request_check(row, clock.utcnow())
    await db.commit()
    if send:
        try:
            await queue.enqueue_check(row.id)
        except Exception as error:
            # The worker's sweep sends checks that never reported again.
            logger.error("Could not queue check %s: %s", row.id, type(error).__name__)
    return VerificationOut.build(row)


@router.delete(
    "/{verification_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
async def delete_verification(verification_id: uuid.UUID, access: Owner, db: DbSession) -> None:
    """Remove a domain (owners; also in a closed case). Active Shuttles stop covering it."""
    row = await service.get(db, access.case.id, verification_id, lock=True)
    if row is None:
        await db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    await service.delete(db, row, actor=access.actor)
    await db.commit()
