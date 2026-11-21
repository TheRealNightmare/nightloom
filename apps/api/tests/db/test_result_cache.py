"""The result cache's storage and its API, against PostgreSQL and Valkey."""

import uuid
from collections.abc import Callable, Coroutine
from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from nightloom_api.audit.models import AuditEvent
from nightloom_api.auth import clock
from nightloom_api.auth.models import Role, User
from nightloom_api.infra import Infra
from nightloom_api.jobs import result_cache
from nightloom_api.jobs.models import Job, JobStatus, empty_stats
from nightloom_api.jobs.result_cache import CacheScope, ResultCacheEntry
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
KEY = "domain:www.example.org"
FINDINGS = [{"kind": "entity", "type": "domain", "value": "example.org"}]


async def signed_in(new_client: ClientFactory, user: User) -> AsyncClient:
    client = new_client()
    assert (await login(client, email=user.email)).status_code == 200
    return client


async def new_case(client: AsyncClient) -> uuid.UUID:
    response = await client.post(f"{PREFIX}/cases", json=CASE, headers=csrf(client))
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


async def put(
    infra: Infra,
    case_id: uuid.UUID,
    *,
    scope: CacheScope = CacheScope.CASE,
    shuttle: str = "dns",
    version: str = "1.0.0",
    entity_key: str = KEY,
    fetched_ago: timedelta = timedelta(0),
    ttl: int = 3600,
    findings: list[dict[str, Any]] | None = None,
    max_entry_bytes: int = 1_000_000,
) -> tuple[str, bool]:
    key = result_cache.cache_key(
        scope=scope,
        case_id=case_id,
        shuttle_name=shuttle,
        shuttle_version=version,
        entity_key=entity_key,
    )
    async with infra.sessionmaker() as db, db.begin():
        stored = await result_cache.store(
            db,
            key=key,
            scope=scope,
            case_id=case_id,
            shuttle_name=shuttle,
            shuttle_version=version,
            entity_key=entity_key,
            findings=FINDINGS if findings is None else findings,
            notes=["a note"],
            fetched_at=clock.utcnow() - fetched_ago,
            ttl_seconds=ttl,
            max_entry_bytes=max_entry_bytes,
        )
    return key, stored


async def entries(infra: Infra) -> list[ResultCacheEntry]:
    async with infra.sessionmaker() as db:
        rows = await db.execute(select(ResultCacheEntry).order_by(ResultCacheEntry.fetched_at))
        return list(rows.scalars())


async def lookup(infra: Infra, key: str, scope: CacheScope = CacheScope.CASE) -> Any:
    async with infra.sessionmaker() as db, db.begin():
        return await result_cache.lookup(db, key=key, scope=scope, now=clock.utcnow())


@pytest.fixture
async def owner(make_user: UserFactory) -> User:
    return await make_user("owner@example.org", Role.ANALYST)


async def test_store_lookup_hits_and_expiry(
    infra: Infra, owner: User, new_client: ClientFactory
) -> None:
    case_id = await new_case(await signed_in(new_client, owner))
    key, stored = await put(infra, case_id)
    assert stored
    first = await lookup(infra, key)
    assert first is not None
    assert (first.findings, first.notes) == (FINDINGS, ["a note"])
    assert await lookup(infra, key) is not None
    [entry] = await entries(infra)
    assert (entry.hits, entry.scope, entry.case_id, entry.finding_count) == (2, "case", case_id, 1)
    assert entry.last_hit_at is not None
    assert entry.size_bytes == result_cache.encoded_size(FINDINGS, ["a note"])
    # Another scope never sees it, even under the same key.
    assert await lookup(infra, key, CacheScope.INSTANCE) is None

    # Replacing an entry starts it afresh.
    await put(infra, case_id, findings=[*FINDINGS, *FINDINGS])
    [entry] = await entries(infra)
    assert (entry.hits, entry.finding_count, entry.last_hit_at) == (0, 2, None)

    # Expired entries are not served.
    expired, _ = await put(
        infra, case_id, entity_key="domain:old.example.org", ttl=1, fetched_ago=timedelta(seconds=5)
    )
    assert await lookup(infra, expired) is None


async def test_too_large_entries_are_not_stored(
    infra: Infra, owner: User, new_client: ClientFactory
) -> None:
    case_id = await new_case(await signed_in(new_client, owner))
    _, stored = await put(infra, case_id, max_entry_bytes=10)
    assert not stored
    assert await entries(infra) == []


async def test_scope_rules_are_enforced_by_the_database(
    infra: Infra, owner: User, new_client: ClientFactory
) -> None:
    case_id = await new_case(await signed_in(new_client, owner))
    await put(infra, case_id, scope=CacheScope.INSTANCE)
    [entry] = await entries(infra)
    assert (entry.scope, entry.case_id) == ("instance", None)
    bad_rows = (("case", None), ("instance", case_id), ("other", None))
    for scope, case in bad_rows:
        with pytest.raises(IntegrityError):
            async with infra.sessionmaker() as db, db.begin():
                await db.execute(
                    text(
                        "INSERT INTO result_cache (key, scope, case_id, shuttle_name, "
                        "shuttle_version, entity_key, findings, finding_count, size_bytes, "
                        "fetched_at, expires_at) VALUES ('k', :scope, :case_id, 'dns', '1.0.0', "
                        "'domain:x.org', '[]', 0, 0, now(), now())"
                    ),
                    {"scope": scope, "case_id": case},
                )


async def test_purge_removes_expired_then_least_recently_used(
    infra: Infra, owner: User, new_client: ClientFactory
) -> None:
    case_id = await new_case(await signed_in(new_client, owner))
    await put(
        infra,
        case_id,
        entity_key="domain:gone.example.org",
        ttl=1,
        fetched_ago=timedelta(seconds=10),
    )
    oldest, _ = await put(
        infra, case_id, entity_key="domain:a.example.org", fetched_ago=timedelta(minutes=3)
    )
    middle, _ = await put(
        infra, case_id, entity_key="domain:b.example.org", fetched_ago=timedelta(minutes=2)
    )
    newest, _ = await put(
        infra, case_id, entity_key="domain:c.example.org", fetched_ago=timedelta(minutes=1)
    )
    assert await lookup(infra, oldest) is not None  # a hit makes it the most recently used
    size = result_cache.encoded_size(FINDINGS, ["a note"])
    async with infra.sessionmaker() as db, db.begin():
        purged = await result_cache.purge(db, now=clock.utcnow(), max_total_bytes=2 * size)
    assert purged == result_cache.PurgeResult(expired=1, evicted=1)
    assert {entry.key for entry in await entries(infra)} == {oldest, newest}
    assert middle not in {entry.key for entry in await entries(infra)}
    async with infra.sessionmaker() as db, db.begin():
        assert await result_cache.purge(db, now=clock.utcnow(), max_total_bytes=0) == (
            result_cache.PurgeResult(expired=0, evicted=2)
        )


async def test_case_scoped_entries_go_with_their_case(
    infra: Infra, owner: User, new_client: ClientFactory
) -> None:
    case_id = await new_case(await signed_in(new_client, owner))
    await put(infra, case_id)
    await put(infra, case_id, scope=CacheScope.INSTANCE)
    async with infra.sessionmaker() as db, db.begin():
        await db.execute(text("DELETE FROM case_members WHERE case_id = :id"), {"id": case_id})
        await db.execute(text("DELETE FROM cases WHERE id = :id"), {"id": case_id})
    assert [entry.scope for entry in await entries(infra)] == ["instance"]


async def test_admin_statistics_and_clearing(
    infra: Infra, make_user: UserFactory, owner: User, new_client: ClientFactory
) -> None:
    admin = await make_user("admin@example.org", Role.ADMIN)
    admin_client = await signed_in(new_client, admin)
    owner_client = await signed_in(new_client, owner)
    case_id = await new_case(owner_client)
    await put(infra, case_id, shuttle="dns")
    await put(infra, case_id, shuttle="crtsh", entity_key="domain:example.org")
    await put(infra, case_id, shuttle="crtsh", entity_key="domain:b.example.org")
    await put(infra, case_id, shuttle="retired", entity_key="domain:c.example.org")
    async with infra.sessionmaker() as db:
        now = clock.utcnow()
        for from_cache in (True, False, False):
            db.add(
                Job(
                    case_id=case_id,
                    shuttle_name="crtsh",
                    shuttle_version="1.0.0",
                    input_key=KEY,
                    requested_by=owner.id,
                    status=JobStatus.SUCCEEDED,
                    stats=empty_stats(),
                    started_at=now,
                    finished_at=now,
                    from_cache=from_cache,
                )
            )
        await db.commit()

    # Admins only.
    for client in (owner_client,):
        assert (await client.get(f"{PREFIX}/admin/result-cache")).status_code == 403
        denied = await client.delete(f"{PREFIX}/admin/result-cache", headers=csrf(client))
        assert denied.status_code == 403
    response = await admin_client.get(f"{PREFIX}/admin/result-cache")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["scope"], body["entries"]) == ("case", 4)
    assert body["max_bytes"] == 512 * 1024 * 1024
    assert body["max_entry_bytes"] == 5 * 1024 * 1024
    by_name = {item["shuttle"]: item for item in body["shuttles"]}
    assert set(by_name) == {"crtsh", "dns", "retired"}
    assert by_name["crtsh"]["entries"] == 2
    assert (by_name["crtsh"]["jobs"], by_name["crtsh"]["jobs_from_cache"]) == (3, 1)
    assert by_name["crtsh"]["cache_ttl_seconds"] == 24 * 3600
    assert by_name["retired"]["cache_ttl_seconds"] is None
    assert body["size_bytes"] == sum(item["size_bytes"] for item in body["shuttles"])

    bad = await admin_client.delete(
        f"{PREFIX}/admin/result-cache", params={"shuttle": "Bad Name"}, headers=csrf(admin_client)
    )
    assert bad.status_code == 422
    one = await admin_client.delete(
        f"{PREFIX}/admin/result-cache", params={"shuttle": "crtsh"}, headers=csrf(admin_client)
    )
    assert one.json() == {"deleted": 2}
    everything = await admin_client.delete(
        f"{PREFIX}/admin/result-cache", headers=csrf(admin_client)
    )
    assert everything.json() == {"deleted": 2}
    assert await entries(infra) == []
    async with infra.sessionmaker() as db:
        events = list(
            (
                await db.execute(
                    select(AuditEvent)
                    .where(AuditEvent.action == "result_cache.clear")
                    .order_by(AuditEvent.id)
                )
            ).scalars()
        )
    assert [(e.outcome, e.details) for e in events] == [
        ("success", {"shuttle": "crtsh", "deleted": 2}),
        ("success", {"shuttle": "all", "deleted": 2}),
    ]
    assert all(e.actor_user_id == admin.id and e.case_id is None for e in events)
    # No entity keys in the audit log.
    assert not any("example.org" in str(e.details) for e in events)


async def test_case_owners_clear_their_case(
    infra: Infra, make_user: UserFactory, owner: User, new_client: ClientFactory
) -> None:
    editor = await make_user("editor@example.org", Role.ANALYST)
    outsider = await make_user("outsider@example.org", Role.ANALYST)
    owner_client = await signed_in(new_client, owner)
    case_id = await new_case(owner_client)
    other_case = await new_case(owner_client)
    added = await owner_client.put(
        f"{PREFIX}/cases/{case_id}/members",
        json={"email": editor.email, "role": "editor"},
        headers=csrf(owner_client),
    )
    assert added.status_code == 201
    await put(infra, case_id)
    await put(infra, case_id, entity_key="domain:b.example.org")
    await put(infra, other_case)
    await put(infra, case_id, scope=CacheScope.INSTANCE)
    url = f"{PREFIX}/cases/{case_id}/result-cache"

    editor_client = await signed_in(new_client, editor)
    assert (await editor_client.delete(url, headers=csrf(editor_client))).status_code == 403
    outsider_client = await signed_in(new_client, outsider)
    assert (await outsider_client.delete(url, headers=csrf(outsider_client))).status_code == 404
    assert (await owner_client.delete(url)).status_code == 403  # no CSRF token

    cleared = await owner_client.delete(url, headers=csrf(owner_client))
    assert cleared.status_code == 200, cleared.text
    assert cleared.json() == {"deleted": 2}
    left = await entries(infra)
    assert sorted((e.scope, e.case_id) for e in left) == sorted(
        [("case", other_case), ("instance", None)]
    )
    async with infra.sessionmaker() as db:
        event = (
            await db.execute(
                select(AuditEvent).where(
                    AuditEvent.action == "result_cache.clear", AuditEvent.outcome == "success"
                )
            )
        ).scalar_one()
    assert (event.case_id, event.details) == (case_id, {"shuttle": "all", "deleted": 2})
