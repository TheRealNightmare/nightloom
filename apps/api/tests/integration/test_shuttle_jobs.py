"""A Shuttle job end to end through the running stack: API, Valkey, worker, Neo4j.

Run with ``make up && make test-integration``. The test creates a throwaway
analyst directly in the stack's database, works only through the API over
HTTP like a client would, and removes its case (and so its graph) at the end.
"""

import asyncio
import os
import secrets
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from nightloom_api.audit.service import Actor
from nightloom_api.auth import service as auth_service
from nightloom_api.auth.models import Role, User
from nightloom_api.config import Settings
from nightloom_api.infra import create_infra

pytestmark = pytest.mark.integration

PREFIX = "/api/v1"
TITLE = "Integration: registrable domain"
SUBDOMAIN = "domain:www.shop.example.co.uk"


@pytest.fixture
async def analyst() -> AsyncIterator[tuple[str, str]]:
    """A fresh active analyst (email, password), deactivated afterwards."""
    infra = create_infra(Settings(env="test"))
    email = f"it-{uuid.uuid4().hex[:12]}@example.org"
    password = secrets.token_urlsafe(24)
    try:
        async with infra.sessionmaker() as db:
            user = await auth_service.create_user(
                db,
                email=email,
                display_name="Integration test",
                role=Role.ANALYST,
                password=password,
                actor=Actor.system("integration-test"),
            )
            user_id = user.id
        yield email, password
    finally:
        async with infra.sessionmaker() as db:
            stored = await db.get(User, user_id)
            if stored is not None:
                stored.is_active = False
                await db.commit()
        await infra.aclose()


async def poll_job(client: httpx.AsyncClient, url: str, attempts: int = 60) -> dict[str, Any]:
    """The job once it is no longer queued or running (up to ~30 s)."""
    for _ in range(attempts):
        job: dict[str, Any] = (await client.get(url)).json()
        if job["status"] not in {"queued", "running"}:
            return job
        await asyncio.sleep(0.5)
    raise AssertionError(f"job still {job['status']} after {attempts} polls")


async def test_registrable_domain_job_through_api_and_worker(analyst: tuple[str, str]) -> None:
    email, password = analyst
    base_url = os.environ.get("NIGHTLOOM_TEST_API_URL", "http://127.0.0.1:8000")
    async with httpx.AsyncClient(base_url=base_url, timeout=10) as client:
        login = await client.post(
            f"{PREFIX}/auth/login", json={"email": email, "password": password}
        )
        assert login.status_code == 200, login.text
        headers = {"X-CSRF-Token": client.cookies["nightloom_csrf"]}

        created = await client.post(
            f"{PREFIX}/cases",
            json={
                "title": TITLE,
                "purpose": "Check that a Shuttle job runs end to end in the stack.",
                "legal_basis": "self_assessment",
                "ethics_acknowledged": True,
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        case_url = f"{PREFIX}/cases/{created.json()['id']}"
        try:
            entity = await client.post(
                f"{case_url}/entities",
                json={"type": "domain", "value": "www.shop.example.co.uk"},
                headers=headers,
            )
            assert entity.status_code == 201, entity.text

            shuttles = await client.get(f"{PREFIX}/shuttles", params={"input_type": "domain"})
            assert "registrable-domain" in [s["name"] for s in shuttles.json()]

            job = await client.post(
                f"{case_url}/jobs",
                json={"shuttle": "registrable-domain", "entity_key": SUBDOMAIN},
                headers=headers,
            )
            assert job.status_code == 201, job.text
            finished = await poll_job(client, f"{case_url}/jobs/{job.json()['id']}")
            assert finished["status"] == "succeeded", finished
            assert finished["stats"]["entities_created"] == 1
            assert finished["stats"]["relationships_created"] == 1

            graph = (await client.get(f"{case_url}/graph")).json()
            keys = {node["key"] for node in graph["nodes"]}
            assert keys == {"domain:example.co.uk", SUBDOMAIN}
            edges = {(e["source_key"], e["type"], e["target_key"]) for e in graph["edges"]}
            assert edges == {("domain:example.co.uk", "HAS_SUBDOMAIN", SUBDOMAIN)}

            # A finished job cannot be cancelled.
            cancel = await client.post(
                f"{case_url}/jobs/{job.json()['id']}/cancel", headers=headers
            )
            assert cancel.status_code == 409
        finally:
            await client.request("DELETE", case_url, json={"confirm_title": TITLE}, headers=headers)
