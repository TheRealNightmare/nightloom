"""Request and response bodies for scan profiles and scan runs."""

import uuid
from collections import Counter
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from nightloom_api.scans.models import ScanRun
from nightloom_api.scans.profiles import (
    DESCRIPTION_MAX_LENGTH,
    MAX_SHUTTLES,
    NAME_MAX_LENGTH,
    Profile,
    accepted_types,
    missing_shuttles,
)
from nightloom_api.scans.service import MAX_RUN_ENTITIES
from nightloom_core.entities import EntityType
from nightloom_core.normalize import MAX_KEY_LENGTH
from nightloom_sdk.meta import NAME_MAX_LENGTH as SHUTTLE_NAME_MAX_LENGTH
from nightloom_sdk.registry import ShuttleRegistry

ShuttleName = Annotated[str, StringConstraints(min_length=1, max_length=SHUTTLE_NAME_MAX_LENGTH)]
# Checked again (and cleaned) by nightloom_api.scans.profiles.
ProfileName = Annotated[str, StringConstraints(max_length=NAME_MAX_LENGTH * 2)]
ProfileDescription = Annotated[str, StringConstraints(max_length=DESCRIPTION_MAX_LENGTH * 2)]


class ProfileOut(BaseModel):
    id: str
    name: str
    description: str
    # Shuttle names, in the order they run.
    shuttles: list[str]
    built_in: bool
    # Entity types at least one of its installed Shuttles accepts.
    input_types: list[EntityType]
    # Its Shuttles that are not installed (left out of runs).
    missing_shuttles: list[str]
    updated_at: datetime | None

    @classmethod
    def build(cls, profile: Profile, registry: ShuttleRegistry) -> "ProfileOut":
        return cls(
            id=profile.id,
            name=profile.name,
            description=profile.description,
            shuttles=list(profile.shuttles),
            built_in=profile.built_in,
            input_types=accepted_types(profile, registry),
            missing_shuttles=missing_shuttles(profile, registry),
            updated_at=profile.updated_at,
        )


class ProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ProfileName
    description: ProfileDescription = ""
    shuttles: list[ShuttleName] = Field(min_length=1, max_length=MAX_SHUTTLES * 2)


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ProfileName | None = None
    description: ProfileDescription | None = None
    shuttles: list[ShuttleName] | None = Field(
        default=None, min_length=1, max_length=MAX_SHUTTLES * 2
    )


class RunCreate(BaseModel):
    """Body of ``POST /cases/{id}/scan-runs``."""

    model_config = ConfigDict(extra="forbid")

    profile_id: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    entity_keys: list[
        Annotated[str, StringConstraints(min_length=1, max_length=MAX_KEY_LENGTH)]
    ] = Field(min_length=1, max_length=MAX_RUN_ENTITIES)
    # Run every Shuttle even if its recent results are cached.
    fresh: bool = False
    # Also continue from the infrastructure entities it finds (the case must allow it).
    pivot: bool = False


class RunJobs(BaseModel):
    """A run's released jobs by status."""

    queued: int = 0
    running: int = 0
    succeeded: int = 0
    failed: int = 0
    cancelled: int = 0


class RunOut(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    profile_id: str
    profile_name: str
    requested_by: uuid.UUID | None
    # running, finished, cancelled or stopped.
    status: str
    # Why a stopped run stopped: case_not_open or requester_not_allowed.
    stop_reason: str | None
    entities: int
    # Jobs the run planned, how many have been queued so far, and how many
    # wait for a free job slot.
    total: int
    released: int
    waiting: int
    # (Shuttle, entity) pairs left out: not accepted, person gate, not installed.
    skipped: int
    jobs: RunJobs
    use_cache: bool
    # A pivot: its limits, how many new entities it found, the furthest hop
    # released so far, and which limit stopped its expansion ("jobs" or "entities").
    pivot: bool
    max_depth: int
    max_jobs: int
    max_entities: int
    discovered: int
    depth: int
    capped: str | None
    created_at: datetime
    finished_at: datetime | None

    @classmethod
    def build(cls, run: ScanRun, counts: Counter[str]) -> "RunOut":
        return cls(
            id=run.id,
            case_id=run.case_id,
            profile_id=run.profile_id,
            profile_name=run.profile_name,
            requested_by=run.requested_by,
            status=run.status,
            stop_reason=run.stop_reason,
            entities=run.entities,
            total=run.total,
            released=run.released,
            waiting=max(0, run.total - run.released) if run.status == "running" else 0,
            skipped=run.skipped,
            jobs=RunJobs(**{status: counts.get(status, 0) for status in RunJobs.model_fields}),
            use_cache=run.use_cache,
            pivot=run.pivot,
            max_depth=run.max_depth,
            max_jobs=run.max_jobs,
            max_entities=run.max_entities,
            discovered=run.discovered,
            depth=max((int(item.get("depth", 0)) for item in run.plan[: run.released]), default=0),
            capped=run.capped,
            created_at=run.created_at,
            finished_at=run.finished_at,
        )


class RunPage(BaseModel):
    items: list[RunOut]
