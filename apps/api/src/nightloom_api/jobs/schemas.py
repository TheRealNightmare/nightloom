"""Request and response bodies for Shuttles and jobs."""

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from nightloom_api.jobs import deadlines
from nightloom_api.jobs.models import Job, JobStatus
from nightloom_core.entities import EntityType, Sensitivity
from nightloom_core.normalize import MAX_KEY_LENGTH
from nightloom_core.relationships import RelationType
from nightloom_sdk.meta import NAME_MAX_LENGTH, Mode, ShuttleMeta
from nightloom_sdk.registry import ShuttleRegistry


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
    # How long a successful run's results may be reused (0: never cached).
    cache_ttl_seconds: int
    terms_url: str | None
    homepage: str | None
    # Conditions of use of the source (plain text, at most 300 characters).
    usage_note: str | None
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
            cache_ttl_seconds=meta.cache_ttl_seconds,
            terms_url=meta.terms_url,
            homepage=meta.homepage,
            usage_note=meta.usage_note,
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
    # Run the Shuttle even if recent results are cached (the run refreshes them).
    fresh: bool = False


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
    # Short remarks from the Shuttle (at most 10), e.g. that a result was
    # capped. Never copied into the audit log.
    notes: list[str] = Field(default_factory=list)

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
    # The Shuttle's timeout (the largest allowed one if it is no longer installed).
    timeout_seconds: int
    # When the run must end: started_at + timeout_seconds (null until it starts).
    deadline: datetime | None
    # When the worker's hard limit has passed (null until it starts). A job
    # still running after this has most likely lost its worker; the worker's
    # reaper fails it about a minute later.
    overdue_at: datetime | None
    # Answered from the result cache instead of running the Shuttle, and when
    # the source was asked for those results.
    from_cache: bool
    cached_fetched_at: datetime | None
    # False when the user asked for a fresh run.
    use_cache: bool
    # The profile run that queued this job (null for a single run).
    run_id: uuid.UUID | None

    @classmethod
    def build(cls, job: Job, registry: ShuttleRegistry) -> "JobOut":
        timeout = deadlines.shuttle_timeout(registry, job.shuttle_name)
        started = job.started_at
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
            timeout_seconds=timeout,
            deadline=deadlines.deadline(started, timeout) if started else None,
            overdue_at=deadlines.overdue_at(started, timeout) if started else None,
            from_cache=job.from_cache,
            cached_fetched_at=job.cached_fetched_at,
            use_cache=job.use_cache,
            run_id=job.run_id,
        )


class JobPage(BaseModel):
    items: list[JobOut]
    next_cursor: str | None


class ShuttleCacheStatsOut(BaseModel):
    shuttle: str
    # The installed Shuttle's cache lifetime (null if it is no longer installed).
    cache_ttl_seconds: int | None
    entries: int
    size_bytes: int
    # Times entries were reused.
    hits: int
    # Jobs of this Shuttle that succeeded in the last 30 days, and how many of
    # them were answered from the cache.
    jobs: int
    jobs_from_cache: int


class ResultCacheOut(BaseModel):
    """The result cache's settings and contents (admins only)."""

    scope: str
    max_bytes: int
    max_entry_bytes: int
    entries: int
    size_bytes: int
    shuttles: list[ShuttleCacheStatsOut]


class ResultCacheCleared(BaseModel):
    deleted: int
