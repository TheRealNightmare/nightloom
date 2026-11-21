"""Ownership verifications against PostgreSQL: the API, checks, the sweep."""

import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text, update

from db_fixtures import RecordingJobQueue
from nightloom_api.audit.models import AuditEvent
from nightloom_api.auth import clock
from nightloom_api.auth.models import Role, User
from nightloom_api.cases.models import Case
from nightloom_api.infra import Infra
from nightloom_api.ownership import service
from nightloom_api.ownership.models import OwnershipVerification
from nightloom_api.ownership.service import CheckResult, Proof
from support import PREFIX, csrf, login

pytestmark = pytest.mark.db

UserFactory = Callable[..., Coroutine[Any, Any, User]]
ClientFactory = Callable[[], AsyncClient]

CASE: dict[str, Any] = {
    "title": "Example exposure review",
    "purpose": "Map the public attack surface of example.org for its owner.",
    "legal_basis": "owned_assets",
    "ethics_acknowledged": True,
}


@dataclass
class World:
    infra: Infra
    case_id: uuid.UUID
    clients: dict[str, AsyncClient]
    queue: RecordingJobQueue

    @property
    def url(self) -> str:
        return f"{PREFIX}/cases/{self.case_id}/verifications"

    async def add(self, domain: str = "example.org", method: str = "dns_txt") -> dict[str, Any]:
        owner = self.clients["owner"]
        response = await owner.post(
            self.url, json={"domain": domain, "method": method}, headers=csrf(owner)
        )
        assert response.status_code == 201, response.text
        body: dict[str, Any] = response.json()
        return body

    async def row(self, verification_id: str | uuid.UUID) -> OwnershipVerification:
        async with self.infra.sessionmaker() as db:
            found = await db.get(OwnershipVerification, uuid.UUID(str(verification_id)))
            assert found is not None
            return found

    async def apply(self, verification_id: str, proof: Proof, *, renew: bool = True) -> str:
        async with self.infra.sessionmaker() as db, db.begin():
            row = await db.get(
                OwnershipVerification, uuid.UUID(verification_id), with_for_update=True
            )
            assert row is not None
            row.check_renews = renew
            return await service.apply_check(db, row, proof, clock.utcnow())

    async def events(self, prefix: str = "ownership.") -> list[AuditEvent]:
        async with self.infra.sessionmaker() as db:
            rows = await db.execute(
                select(AuditEvent)
                .where(AuditEvent.action.startswith(prefix))
                .order_by(AuditEvent.id)
            )
            return list(rows.scalars())


@pytest.fixture
async def world(
    infra: Infra, make_user: UserFactory, new_client: ClientFactory, job_queue: RecordingJobQueue
) -> World:
    users = {
        "owner": await make_user("owner@example.org", Role.ANALYST),
        "editor": await make_user("editor@example.org", Role.ANALYST),
        "viewer": await make_user("viewer@example.org", Role.ANALYST),
        "outsider": await make_user("outsider@example.org", Role.ANALYST),
    }
    clients = {}
    for role, user in users.items():
        client = new_client()
        assert (await login(client, email=user.email)).status_code == 200
        clients[role] = client
    owner = clients["owner"]
    created = await owner.post(f"{PREFIX}/cases", json=CASE, headers=csrf(owner))
    assert created.status_code == 201, created.text
    case_id = uuid.UUID(created.json()["id"])
    for role in ("editor", "viewer"):
        added = await owner.put(
            f"{PREFIX}/cases/{case_id}/members",
            json={"email": users[role].email, "role": role},
            headers=csrf(owner),
        )
        assert added.status_code == 201, added.text
    return World(infra, case_id, clients, job_queue)


# --- the API -----------------------------------------------------------------------------------


async def test_owners_add_a_domain_and_get_its_proof(world: World) -> None:
    dns = await world.add(" Example.ORG ")
    assert dns["domain"] == "example.org"
    assert dns["status"] == "pending"
    assert dns["checking"] is False
    token = (await world.row(dns["id"])).token
    assert dns["proof"] == {
        "record_type": "TXT",
        "record_name": "_nightloom-challenge.example.org",
        "url": None,
        "value": f"nightloom-verification={token}",
    }
    web = await world.add("shop.example.net", "http_file")
    assert web["proof"]["url"] == (
        "https://shop.example.net/.well-known/nightloom-verification.txt"
    )
    assert web["proof"]["record_name"] is None
    listed = await world.clients["viewer"].get(world.url)
    assert listed.status_code == 200
    assert [item["domain"] for item in listed.json()["items"]] == [
        "example.org",
        "shop.example.net",
    ]
    [created, _] = await world.events("ownership.create")
    assert created.details == {"domain": "example.org", "method": "dns_txt"}
    assert created.case_id == world.case_id


async def test_only_owners_change_the_list(world: World) -> None:
    item = await world.add()
    for role, expected in (("editor", 403), ("viewer", 403), ("outsider", 404)):
        client = world.clients[role]
        posted = await client.post(
            world.url, json={"domain": "example.net", "method": "dns_txt"}, headers=csrf(client)
        )
        assert posted.status_code == expected, role
        check = await client.post(f"{world.url}/{item['id']}/check", headers=csrf(client))
        assert check.status_code == expected, role
        removed = await client.delete(f"{world.url}/{item['id']}", headers=csrf(client))
        assert removed.status_code == expected, role
    assert (await world.clients["outsider"].get(world.url)).status_code == 404


async def test_bad_domains_and_duplicates_are_refused(world: World) -> None:
    owner = world.clients["owner"]
    await world.add()
    for body, expected in [
        ({"domain": "EXAMPLE.org.", "method": "dns_txt"}, 409),
        ({"domain": "co.uk", "method": "dns_txt"}, 422),
        ({"domain": "*.example.com", "method": "dns_txt"}, 422),
        ({"domain": "example.com", "method": "carrier_pigeon"}, 422),
        ({"domain": "example.com", "method": "dns_txt", "extra": 1}, 422),
    ]:
        response = await owner.post(world.url, json=body, headers=csrf(owner))
        assert response.status_code == expected, (body, response.text)
    async with world.infra.sessionmaker() as db:
        for i in range(service.MAX_PER_CASE - 1):
            db.add(
                OwnershipVerification(
                    case_id=world.case_id, domain=f"d{i}.example.com", method="dns_txt", token="t"
                )
            )
        await db.commit()
    full = await owner.post(
        world.url, json={"domain": "one-more.example.com", "method": "dns_txt"}, headers=csrf(owner)
    )
    assert full.status_code == 422
    assert "at most 50" in full.text


async def test_check_now_queues_one_check(world: World) -> None:
    item = await world.add()
    owner = world.clients["owner"]
    url = f"{world.url}/{item['id']}/check"
    first = await owner.post(url, headers=csrf(owner))
    assert first.status_code == 202, first.text
    assert first.json()["checking"] is True
    # Asked again at once: no second message.
    assert (await owner.post(url, headers=csrf(owner))).status_code == 202
    assert world.queue.checks == [uuid.UUID(item["id"])]
    assert (await world.row(item["id"])).check_renews is True
    # A broken queue still answers; the sweep sends it later.
    async with world.infra.sessionmaker() as db:
        await db.execute(update(OwnershipVerification).values(check_requested_at=None))
        await db.commit()
    world.queue.fail = True
    assert (await owner.post(url, headers=csrf(owner))).status_code == 202
    unknown = await owner.post(f"{world.url}/{uuid.uuid4()}/check", headers=csrf(owner))
    assert unknown.status_code == 404


async def test_closed_cases_cannot_add_or_check_but_can_remove(world: World) -> None:
    item = await world.add()
    async with world.infra.sessionmaker() as db:
        await db.execute(
            update(Case)
            .where(Case.id == world.case_id)
            .values(status="closed", closed_at=text("now()"))
        )
        await db.commit()
    owner = world.clients["owner"]
    added = await owner.post(
        world.url, json={"domain": "example.net", "method": "dns_txt"}, headers=csrf(owner)
    )
    assert added.status_code == 409
    check = await owner.post(f"{world.url}/{item['id']}/check", headers=csrf(owner))
    assert check.status_code == 409
    removed = await owner.delete(f"{world.url}/{item['id']}", headers=csrf(owner))
    assert removed.status_code == 204
    assert (await owner.get(world.url)).json()["items"] == []
    [event] = await world.events("ownership.delete")
    assert event.details == {"domain": "example.org", "status": "pending"}


# --- checks ------------------------------------------------------------------------------------


async def test_a_found_proof_verifies_for_30_days(world: World) -> None:
    item = await world.add()
    status = await world.apply(
        item["id"], Proof(CheckResult.FOUND, addresses=("192.0.2.1", "2001:db8::1"))
    )
    assert status == "verified"
    row = await world.row(item["id"])
    assert row.verified_at is not None and row.expires_at is not None
    assert row.expires_at - row.verified_at == timedelta(days=30)
    assert row.addresses == ["192.0.2.1", "2001:db8::1"]
    response = await world.clients["owner"].get(world.url)
    [out] = response.json()["items"]
    assert (out["status"], out["checking"], out["last_error"]) == ("verified", False, None)
    [event] = await world.events("ownership.check")
    assert (event.outcome, event.actor_label) == ("success", "system:ownership-check")
    assert event.details["status"] == "verified"


async def test_a_missing_proof_fails_with_a_reason(world: World) -> None:
    item = await world.add()
    status = await world.apply(item["id"], Proof(CheckResult.MISSING, "record_not_found"))
    assert status == "failed"
    [out] = (await world.clients["owner"].get(world.url)).json()["items"]
    assert out["last_error"] == "record_not_found"
    assert out["error_message"] == "No TXT record with the token was found."
    [event] = await world.events("ownership.check")
    assert (event.outcome, event.details["error"]) == ("failure", "record_not_found")


async def test_daily_rechecks_keep_revoke_or_tolerate(world: World) -> None:
    item = await world.add()
    await world.apply(item["id"], Proof(CheckResult.FOUND, addresses=("192.0.2.1",)))
    before = await world.row(item["id"])
    # Found again by the daily re-check: same expiry, fresh addresses, no event.
    assert (
        await world.apply(
            item["id"], Proof(CheckResult.FOUND, addresses=("192.0.2.9",)), renew=False
        )
        == "verified"
    )
    after = await world.row(item["id"])
    assert after.expires_at == before.expires_at
    assert after.addresses == ["192.0.2.9"]
    # Unreachable twice: still verified; the third time revokes.
    for _ in range(service.MAX_FAILURES - 1):
        proof = Proof(CheckResult.UNREACHABLE, "dns_unreachable")
        assert await world.apply(item["id"], proof, renew=False) == "verified"
    assert (await world.row(item["id"])).failures == service.MAX_FAILURES - 1
    assert (
        await world.apply(
            item["id"], Proof(CheckResult.UNREACHABLE, "dns_unreachable"), renew=False
        )
        == "revoked"
    )
    revoked = await world.row(item["id"])
    assert (revoked.expires_at, revoked.addresses) == (None, [])
    [event] = await world.events("ownership.revoke")
    assert event.outcome == "failure"
    # An owner's check that finds it again verifies it again.
    assert await world.apply(item["id"], Proof(CheckResult.FOUND)) == "verified"

    # A missing proof on a re-check revokes at once.
    assert (
        await world.apply(item["id"], Proof(CheckResult.MISSING, "token_mismatch"), renew=False)
        == "revoked"
    )


async def test_renewing_restarts_the_30_days(world: World) -> None:
    item = await world.add()
    await world.apply(item["id"], Proof(CheckResult.FOUND))
    async with world.infra.sessionmaker() as db:
        await db.execute(
            update(OwnershipVerification).values(
                verified_at=text("now() - interval '20 days'"),
                expires_at=text("now() + interval '10 days'"),
            )
        )
        await db.commit()
    await world.apply(item["id"], Proof(CheckResult.FOUND))
    row = await world.row(item["id"])
    assert row.expires_at is not None
    assert row.expires_at > clock.utcnow() + timedelta(days=29)


# --- the sweep ---------------------------------------------------------------------------------


async def test_the_sweep_expires_and_picks_due_checks(world: World) -> None:
    old = await world.add("old.example.org")
    stale = await world.add("stale.example.org")
    fresh = await world.add("fresh.example.org")
    lost = await world.add("lost.example.org")
    for item in (old, stale, fresh):
        await world.apply(item["id"], Proof(CheckResult.FOUND))
    async with world.infra.sessionmaker() as db:
        await db.execute(
            update(OwnershipVerification)
            .where(OwnershipVerification.domain == "old.example.org")
            .values(expires_at=text("now() - interval '1 minute'"))
        )
        await db.execute(
            update(OwnershipVerification)
            .where(OwnershipVerification.domain == "stale.example.org")
            .values(checked_at=text("now() - interval '25 hours'"))
        )
        await db.execute(
            update(OwnershipVerification)
            .where(OwnershipVerification.domain == "lost.example.org")
            .values(check_requested_at=text("now() - interval '11 minutes'"), check_renews=True)
        )
        await db.commit()
    async with world.infra.sessionmaker() as db:
        due = await service.sweep(db, clock.utcnow())
    assert set(due) == {uuid.UUID(lost["id"]), uuid.UUID(stale["id"])}
    assert (await world.row(old["id"])).status == "expired"
    assert (await world.row(stale["id"])).check_renews is False
    assert (await world.row(lost["id"])).check_renews is True
    assert (await world.row(fresh["id"])).check_requested_at is None
    [event] = await world.events("ownership.expire")
    assert event.details == {"domain": "old.example.org"}
    # Queued checks are not picked again until they are stale.
    async with world.infra.sessionmaker() as db:
        assert await service.sweep(db, clock.utcnow()) == []


async def test_coverage_in_the_database(world: World) -> None:
    item = await world.add()
    async with world.infra.sessionmaker() as db:
        assert (
            await service.covers(db, world.case_id, "domain:www.example.org", clock.utcnow())
            is None
        )
    await world.apply(item["id"], Proof(CheckResult.FOUND, addresses=("192.0.2.1",)))
    async with world.infra.sessionmaker() as db:
        now = clock.utcnow()
        assert await service.covers(db, world.case_id, "domain:www.example.org", now)
        assert await service.covers(db, world.case_id, "ip_address:192.0.2.1", now)
        assert await service.covers(db, world.case_id, "ip_address:192.0.2.2", now) is None
        # Another case's verification covers nothing here.
        assert await service.covers(db, uuid.uuid4(), "domain:www.example.org", now) is None
