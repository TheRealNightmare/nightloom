"""Request and response bodies for Shuttles and jobs."""

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from nightloom_api.jobs.models import Job, JobStatus
from nightloom_core.entities import EntityType, Sensitivity
from nightloom_core.normalize import MAX_KEY_LENGTH
from nightloom_core.relationships import RelationType
from nightloom_sdk.meta import NAME_MAX_LENGTH, Mode, ShuttleMeta


class RateLimitOut(BaseModel):
    requests: int
    period_seconds: float


class ShuttleOut(BaseModel):
    """A Shuttle's metadata (never its code, keys or configuration)."""

    name: str
    version: str
    title: str
    description: str
    input_types: list[EntityType]
    output_entity_types: list[EntityType]
    output_relationship_types: list[RelationType]
    mode: Mode
    sensitivity: Sensitivity
    network: bool
    required_keys: list[str]
    rate_limit: RateLimitOut | None
    timeout_seconds: int
    terms_url: str | None
    homepage: str | None
    default_admiralty: str
    default_confidence: float

    @classmethod
    def build(cls, meta: ShuttleMeta) -> "ShuttleOut":
        return cls(
            name=meta.name,
            version=meta.version,
            title=meta.title,
            description=meta.description,
            input_types=sorted(meta.input_types),
            output_entity_types=sorted(meta.output_entity_types),
            output_relationship_types=sorted(meta.output_relationship_types),
            mode=meta.mode,
            sensitivity=meta.sensitivity,
            network=meta.network,
            required_keys=sorted(meta.required_keys),
            rate_limit=(
                RateLimitOut(
                    requests=meta.rate_limit.requests,
                    period_seconds=meta.rate_limit.period_seconds,
                )
                if meta.rate_limit
                else None
            ),
            timeout_seconds=meta.timeout_seconds,
            terms_url=meta.terms_url,
            homepage=meta.homepage,
            default_admiralty=meta.default_admiralty,
            default_confidence=meta.default_confidence,
        )


class JobCreate(BaseModel):
    """Body of ``POST /cases/{id}/jobs``."""

    model_config = ConfigDict(extra="forbid")

    shuttle: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=NAME_MAX_LENGTH)
    ]
    entity_key: Annotated[str, StringConstraints(min_length=1, max_length=MAX_KEY_LENGTH)]


class JobStats(BaseModel):
    entities_created: int = 0
    entities_updated: int = 0
    relationships_created: int = 0
    relationships_updated: int = 0
    findings_rejected: int = 0
    # Rejected findings by reason (e.g. "invalid_value"); never the findings themselves.
    rejected_reasons: dict[str, int] = Field(default_factory=dict)
    # Keys of the entities the job created or updated (at most 200), so a
    # client can highlight them. Never copied into the audit log.
    entities_touched: list[str] = Field(default_factory=list)

    @classmethod
    def parse(cls, raw: dict[str, Any] | None) -> "JobStats":
        return cls.model_validate(raw or {})


class JobOut(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    shuttle_name: str
    shuttle_version: str
    input_key: str
    requested_by: uuid.UUID | None
    status: JobStatus
    cancel_requested: bool
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    stats: JobStats

    @classmethod
    def build(cls, job: Job) -> "JobOut":
        return cls(
            id=job.id,
            case_id=job.case_id,
            shuttle_name=job.shuttle_name,
            shuttle_version=job.shuttle_version,
            input_key=job.input_key,
            requested_by=job.requested_by,
            status=job.status,
            cancel_requested=job.cancel_requested,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            error=job.error,
            stats=JobStats.parse(job.stats),
        )


class JobPage(BaseModel):
    items: list[JobOut]
    next_cursor: str | None
