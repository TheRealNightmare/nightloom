"""Request and response bodies for Certificate Transparency monitoring."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from nightloom_api.monitoring import service
from nightloom_api.monitoring.models import CtAlert, CtWatch


class WatchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # A Domain in the case's graph; its subdomains are covered too.
    domain: str = Field(min_length=1, max_length=300)


class WatchOut(BaseModel):
    id: uuid.UUID
    domain: str
    created_at: datetime
    # A check is queued or running.
    checking: bool
    checked_at: datetime | None
    next_check_at: datetime
    # The first successful check recorded what existed; alerts come after it.
    baseline_at: datetime | None
    last_job_id: uuid.UUID | None
    # Why the last check did not run or succeed: a code and its explanation.
    problem: str | None
    problem_message: str | None

    @classmethod
    def build(cls, row: CtWatch) -> "WatchOut":
        return cls(
            id=row.id,
            domain=row.domain,
            created_at=row.created_at,
            checking=row.pending_job_id is not None,
            checked_at=row.checked_at,
            next_check_at=row.next_check_at,
            baseline_at=row.baseline_at,
            last_job_id=row.last_job_id,
            problem=row.last_problem,
            problem_message=service.PROBLEMS.get(row.last_problem or ""),
        )


class WatchList(BaseModel):
    items: list[WatchOut]


class AlertOut(BaseModel):
    id: uuid.UUID
    domain: str
    created_at: datetime
    job_id: uuid.UUID | None
    certificates: int
    # The first new names (at most 20) and how many there were.
    names: list[str]
    names_total: int
    # Graph keys of what is new, to highlight it (at most 200).
    keys: list[str]
    acknowledged_at: datetime | None

    @classmethod
    def build(cls, row: CtAlert) -> "AlertOut":
        return cls(
            id=row.id,
            domain=row.domain,
            created_at=row.created_at,
            job_id=row.job_id,
            certificates=row.certificates,
            names=list(row.names),
            names_total=row.names_total,
            keys=list(row.keys),
            acknowledged_at=row.acknowledged_at,
        )


class AlertList(BaseModel):
    items: list[AlertOut]
    # How many are not seen yet (may exceed the items listed).
    unseen: int


class AcknowledgeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The alerts to mark seen; all unseen ones when left out.
    ids: list[uuid.UUID] | None = Field(default=None, max_length=service.MAX_ALERTS_LISTED)


class AcknowledgeOut(BaseModel):
    acknowledged: int
