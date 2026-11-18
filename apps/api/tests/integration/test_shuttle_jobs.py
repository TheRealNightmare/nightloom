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
DNS_TITLE = "Integration: DNS"
TEST_ZONE = "nightloom-test.example"


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


async def signed_in(client: httpx.AsyncClient, email: str, password: str) -> dict[str, str]:
    login = await client.post(f"{PREFIX}/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    return {"X-CSRF-Token": client.cookies["nightloom_csrf"]}


async def run_and_wait(
    client: httpx.AsyncClient, case_url: str, headers: dict[str, str], shuttle: str, key: str
) -> dict[str, Any]:
    job = await client.post(
        f"{case_url}/jobs", json={"shuttle": shuttle, "entity_key": key}, headers=headers
    )
    assert job.status_code == 201, job.text
    return await poll_job(client, f"{case_url}/jobs/{job.json()['id']}")


@pytest.mark.skipif(
    os.environ.get("NIGHTLOOM_TEST_DNS") != "1",
    reason="needs the test DNS server: make up TEST_DNS=1 && make test-integration TEST_DNS=1",
)
async def test_dns_job_against_the_local_test_dns_server(analyst: tuple[str, str]) -> None:
    """A DNS job end to end; the worker asks only the stack's test-only CoreDNS server."""
    email, password = analyst
    base_url = os.environ.get("NIGHTLOOM_TEST_API_URL", "http://127.0.0.1:8000")
    async with httpx.AsyncClient(base_url=base_url, timeout=10) as client:
        headers = await signed_in(client, email, password)
        created = await client.post(
            f"{PREFIX}/cases",
            json={
                "title": DNS_TITLE,
                "purpose": "Check that the DNS Shuttle runs end to end against a test zone.",
                "legal_basis": "self_assessment",
                "ethics_acknowledged": True,
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        case_url = f"{PREFIX}/cases/{created.json()['id']}"
        try:
            for value in (TEST_ZONE, f"www.{TEST_ZONE}", "printer.local"):
                entity = await client.post(
                    f"{case_url}/entities", json={"type": "domain", "value": value}, headers=headers
                )
                assert entity.status_code == 201, entity.text

            apex = await run_and_wait(client, case_url, headers, "dns", f"domain:{TEST_ZONE}")
            assert apex["status"] == "succeeded", apex
            assert apex["stats"]["findings_rejected"] == 0, apex
            assert f"domain:mail.{TEST_ZONE}" in apex["stats"]["entities_touched"]
            www = await run_and_wait(client, case_url, headers, "dns", f"domain:www.{TEST_ZONE}")
            assert www["status"] == "succeeded", www

            graph = (await client.get(f"{case_url}/graph")).json()
            edges = {(e["source_key"], e["type"], e["target_key"]) for e in graph["edges"]}
            zone = f"domain:{TEST_ZONE}"
            assert {
                (zone, "RESOLVES_TO", "ip_address:192.0.2.10"),
                (zone, "RESOLVES_TO", "ip_address:2001:db8::10"),
                (zone, "HAS_MX", f"domain:mail.{TEST_ZONE}"),
                (zone, "HAS_MX", f"domain:backup-mx.{TEST_ZONE}"),
                (zone, "HAS_NS", f"domain:ns1.{TEST_ZONE}"),
                (zone, "HAS_NS", f"domain:ns2.{TEST_ZONE}"),
                (f"domain:www.{TEST_ZONE}", "ALIAS_OF", f"domain:edge.{TEST_ZONE}"),
                (f"domain:edge.{TEST_ZONE}", "RESOLVES_TO", "ip_address:192.0.2.20"),
            } <= edges
            nodes = {node["key"]: node for node in graph["nodes"]}
            properties = nodes[zone]["properties"]
            assert properties["dns_spf"] == ["v=spf1 mx -all"]
            assert properties["dns_dmarc"] == ["v=DMARC1; p=reject"]
            assert properties["dns_mx"] == [
                f"10 mail.{TEST_ZONE}",
                f"20 backup-mx.{TEST_ZONE}",
            ]
            assert properties["dns_caa"] == ['0 issue "letsencrypt.org"']
            assert properties["dns_soa_serial"] == 2026111801
            # Documentation addresses are not globally routable either.
            assert nodes["ip_address:192.0.2.10"]["properties"] == {"scope": "private"}

            # Internal-only names are refused before anything is sent.
            refused = await run_and_wait(client, case_url, headers, "dns", "domain:printer.local")
            assert refused["status"] == "failed"
            assert "internal-only names" in refused["error"]
        finally:
            await client.request(
                "DELETE", case_url, json={"confirm_title": DNS_TITLE}, headers=headers
            )


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
