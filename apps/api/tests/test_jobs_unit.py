"""Jobs without services: Celery configuration, the queue, guardrails and schemas."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from nightloom_api.audit.service import Actor
from nightloom_api.config import Settings
from nightloom_api.jobs import deadlines, service
from nightloom_api.jobs import queue as queue_module
from nightloom_api.jobs.models import Job, JobStatus, empty_stats
from nightloom_api.jobs.queue import (
    QUEUE_NAME,
    TASK_NAME,
    CeleryJobQueue,
    celery_config,
    create_celery,
    time_limits,
)
from nightloom_api.jobs.schemas import JobStats, ShuttleOut
from nightloom_core.entities import EntityType, Sensitivity
from nightloom_core.relationships import RelationType
from nightloom_sdk.meta import MAX_TIMEOUT_SECONDS, Mode, ShuttleMeta
from nightloom_sdk.registry import ShuttleRegistry
from nightloom_shuttle_registrable_domain import RegistrableDomain


def meta(**overrides: Any) -> ShuttleMeta:
    values: dict[str, Any] = {
        "name": "unit",
        "version": "1.0.0",
        "title": "Unit",
        "description": "A Shuttle for the unit tests.",
        "input_types": {EntityType.DOMAIN},
        "output_entity_types": {EntityType.DOMAIN},
        "output_relationship_types": {RelationType.HAS_SUBDOMAIN},
        "sensitivity": Sensitivity.INFRASTRUCTURE,
        "network": False,
    }
    values.update(overrides)
    return ShuttleMeta.model_validate(values)


def test_celery_accepts_only_json_and_acks_late() -> None:
    config = celery_config(Settings(env="test"))
    assert config["accept_content"] == ["json"]
    assert config["result_accept_content"] == ["json"]
    assert config["task_serializer"] == config["result_serializer"] == "json"
    assert config["task_acks_late"] is True
    assert config["task_reject_on_worker_lost"] is True
    assert config["worker_prefetch_multiplier"] == 1
    assert config["task_default_queue"] == QUEUE_NAME
    # Longer than any task may run, or the broker would deliver it twice.
    assert config["broker_transport_options"]["visibility_timeout"] > config["task_time_limit"]


def test_celery_app_uses_the_settings() -> None:
    app = create_celery(Settings(env="test"))
    assert app.conf.task_serializer == "json"
    assert "pickle" not in app.conf.accept_content
    assert app.conf.broker_url.startswith("redis://")


def test_time_limits_follow_the_shuttle_timeout() -> None:
    assert time_limits(60) == (120, 150)
    soft, hard = time_limits(600)
    assert soft < hard


def test_deadline_layers_come_in_order() -> None:
    started = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    # crt.sh: 300 s timeout; soft 360 s, hard 390 s, reaped at 450 s.
    assert deadlines.deadline(started, 300) == started + timedelta(seconds=300)
    assert deadlines.overdue_at(started, 300) == started + timedelta(seconds=390)
    assert deadlines.stale_at(started, 300) == started + timedelta(seconds=450)
    # The runner records the outcome before Celery's soft limit fires.
    assert deadlines.FINISH_GRACE_SECONDS < deadlines.SOFT_LIMIT_MARGIN_SECONDS


def test_shuttle_timeout_falls_back_to_the_largest() -> None:
    registry = ShuttleRegistry([RegistrableDomain])
    assert deadlines.shuttle_timeout(registry, "registrable-domain") == 30
    assert deadlines.shuttle_timeout(registry, "uninstalled") == MAX_TIMEOUT_SECONDS


async def test_finish_job_if_needs_a_final_status() -> None:
    with pytest.raises(ValueError, match="final status"):
        await service.finish_job_if(
            None,  # type: ignore[arg-type]
            uuid.uuid4(),
            only_if={JobStatus.RUNNING},
            status=JobStatus.RUNNING,
            actor=Actor.system("worker"),
        )


async def test_enqueue_sends_only_the_job_id(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, dict[str, Any]]] = []
    queue = CeleryJobQueue(Settings(env="test"))
    monkeypatch.setattr(queue._app, "send_task", lambda name, **kwargs: sent.append((name, kwargs)))
    job_id = uuid.uuid4()
    await queue.enqueue(job_id, timeout_seconds=30)
    queue.close()
    ((name, kwargs),) = sent
    assert name == TASK_NAME
    assert kwargs["args"] == [str(job_id)]
    assert kwargs["task_id"] == str(job_id)
    assert (kwargs["soft_time_limit"], kwargs["time_limit"]) == (90, 120)
    assert kwargs["queue"] == QUEUE_NAME


def test_get_job_queue_reads_app_state() -> None:
    queue = CeleryJobQueue(Settings(env="test"))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(job_queue=queue)))
    assert queue_module.get_job_queue(request) is queue  # type: ignore[arg-type]
    queue.close()


def test_check_shuttle_guardrails() -> None:
    service.check_shuttle(meta(), input_type=EntityType.DOMAIN, allow_person_entities=False)
    with pytest.raises(service.GuardrailError) as wrong_type:
        service.check_shuttle(meta(), input_type=EntityType.IP_ADDRESS, allow_person_entities=True)
    assert (wrong_type.value.status_code, wrong_type.value.reason) == (
        422,
        "input_type_not_accepted",
    )
    with pytest.raises(service.GuardrailError) as active:
        service.check_shuttle(
            meta(mode=Mode.ACTIVE), input_type=EntityType.DOMAIN, allow_person_entities=True
        )
    assert (active.value.status_code, active.value.reason) == (403, "target_not_verified")
    service.check_shuttle(
        meta(mode=Mode.ACTIVE),
        input_type=EntityType.DOMAIN,
        allow_person_entities=False,
        target_verified=True,
    )
    person = meta(sensitivity=Sensitivity.PERSON)
    with pytest.raises(service.GuardrailError) as gated:
        service.check_shuttle(person, input_type=EntityType.DOMAIN, allow_person_entities=False)
    assert (gated.value.status_code, gated.value.reason) == (403, "person_entities_disabled")
    service.check_shuttle(person, input_type=EntityType.DOMAIN, allow_person_entities=True)


def test_job_details_hash_personal_keys_and_survive_bad_keys() -> None:
    job = Job(
        id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        shuttle_name="unit",
        shuttle_version="1.0.0",
        input_key="email:alex@example.org",
        status=JobStatus.QUEUED,
    )
    details = service.job_details(job)
    assert "input_key" not in details and details["entity_type"] == "email"
    job.input_key = "not a key"
    assert "input_key_sha256" in service.job_details(job)
    assert "alex" not in repr(job) and "unit" in repr(job)


def test_errors_and_stats() -> None:
    assert str(service.UnknownShuttleError()) == "Unknown Shuttle."
    assert service.TooManyJobsError(5).status_code == 429
    assert JobStats.parse(None) == JobStats()
    assert JobStats.parse(empty_stats()).model_dump() == empty_stats()
    assert ShuttleOut.build(meta()).rate_limit is None
    assert ShuttleOut.build(meta()).cache_ttl_seconds == 0
    assert ShuttleOut.build(meta(cache_ttl_seconds=3600)).cache_ttl_seconds == 3600


async def test_finish_job_needs_a_final_status() -> None:
    with pytest.raises(ValueError, match="final status"):
        await service.finish_job(
            None,  # type: ignore[arg-type]
            Job(),
            status=JobStatus.RUNNING,
            actor=Actor.system("test"),
        )
