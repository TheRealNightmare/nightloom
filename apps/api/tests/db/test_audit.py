"""The audit log against real services: wiring, chain, append-only trigger, admin API."""

import asyncio
import hashlib
import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from nightloom_api import cli
from nightloom_api.audit import service as audit
from nightloom_api.audit.actions import AuditAction, AuditOutcome
from nightloom_api.audit.chain import GENESIS_HASH
from nightloom_api.audit.models import AuditEvent
from nightloom_api.audit.verify import REASON_HASH, REASON_PREV_HASH, verify_chain
from nightloom_api.auth.models import Role, User
from nightloom_api.config import Settings
from nightloom_api.infra import Infra
from nightloom_api.infra.cache import get_cache
from nightloom_api.request_id import REQUEST_ID_HEADER
from nightloom_api.routers.audit import VERIFY_LOCK_KEY
from support import ADMIN_EMAIL, APPEND_ONLY_TRIGGER, PASSWORD, PREFIX, csrf, login

pytestmark = pytest.mark.db

UserFactory = Callable[..., Coroutine[Any, Any, User]]
ClientFactory = Callable[[], AsyncClient]
CLIENT_IP = "127.0.0.1"  # httpx's ASGI transport
NEW_USER = {
    "email": "new.analyst@example.org",
    "display_name": "New Analyst",
    "role": "analyst",
    "password": "a sufficiently long password",
}


# --- helpers ------------------------------------------------------------------------


async def all_events(infra: Infra) -> list[AuditEvent]:
    async with infra.sessionmaker() as db:
        return list((await db.execute(select(AuditEvent).order_by(AuditEvent.id))).scalars())


async def last_event_id(infra: Infra) -> int:
    async with infra.sessionmaker() as db:
        return int(await db.scalar(text("SELECT coalesce(max(id), 0) FROM audit_events")) or 0)


async def events_since(infra: Infra, mark: int) -> list[AuditEvent]:
    return [event for event in await all_events(infra) if event.id > mark]


async def only_event_since(infra: Infra, mark: int) -> AuditEvent:
    events = await events_since(infra, mark)
    assert len(events) == 1, [(event.action, event.outcome) for event in events]
    return events[0]


def ip_of(event: AuditEvent) -> str | None:
    return None if event.actor_ip is None else str(event.actor_ip).split("/")[0]


def assert_event(
    event: AuditEvent,
    *,
    action: AuditAction,
    outcome: AuditOutcome,
    actor: User | None,
    request_id: str | None = None,
    target: object = None,
    details: dict[str, Any] | None = None,
) -> None:
    assert (event.action, event.outcome) == (action.value, outcome.value)
    assert event.actor_user_id == (actor.id if actor else None)
    assert event.actor_label == (actor.email if actor else None)
    assert ip_of(event) == CLIENT_IP
    if request_id is not None:
        assert event.request_id == request_id
    assert event.target_type == ("user" if target is not None else None)
    assert event.target_id == (str(target) if target is not None else None)
    if details is not None:
        assert event.details == details
    assert event.case_id is None


async def set_trigger(infra: Infra, enabled: bool) -> None:
    async with infra.engine.begin() as conn:
        state = "ENABLE" if enabled else "DISABLE"
        await conn.execute(text(f"ALTER TABLE audit_events {state} TRIGGER {APPEND_ONLY_TRIGGER}"))


async def tamper(infra: Infra, statement: str, **params: object) -> None:
    """Change audit rows the way a database owner could: trigger off, edit, trigger on."""
    await set_trigger(infra, enabled=False)
    try:
        async with infra.engine.begin() as conn:
            await conn.execute(text(statement), params)
    finally:
        await set_trigger(infra, enabled=True)


@pytest.fixture
async def admin_user(make_user: UserFactory) -> User:
    return await make_user()


@pytest.fixture
async def admin(db_client: AsyncClient, admin_user: User) -> AsyncClient:
    """A client signed in as an admin."""
    assert (await login(db_client)).status_code == 200
    return db_client


async def signed_in(new_client: ClientFactory, user: User) -> AsyncClient:
    client = new_client()
    assert (await login(client, email=user.email)).status_code == 200
    return client


# --- sign-in, sign-out, password ----------------------------------------------------


async def test_login_success_is_recorded(
    db_client: AsyncClient, admin_user: User, infra: Infra
) -> None:
    mark = await last_event_id(infra)

    response = await login(db_client)

    event = await only_event_since(infra, mark)
    assert_event(
        event,
        action=AuditAction.AUTH_LOGIN,
        outcome=AuditOutcome.SUCCESS,
        actor=admin_user,
        request_id=response.headers[REQUEST_ID_HEADER],
        target=admin_user.id,
        details={},
    )


@pytest.mark.parametrize(
    ("email", "password", "reason"),
    [
        (" Admin@Example.ORG ", "wrong password, long enough", "wrong_password"),
        ("nobody@example.org", PASSWORD, "unknown_email"),
        ("admin@example.org", "x" * 2000, "password_too_long"),
    ],
)
async def test_failed_login_stores_only_a_hash_of_the_email(
    db_client: AsyncClient,
    admin_user: User,
    infra: Infra,
    email: str,
    password: str,
    reason: str,
) -> None:
    mark = await last_event_id(infra)

    response = await login(db_client, email=email, password=password)

    assert response.status_code == 401
    event = await only_event_since(infra, mark)
    digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()
    assert_event(
        event,
        action=AuditAction.AUTH_LOGIN,
        outcome=AuditOutcome.FAILURE,
        actor=None,
        request_id=response.headers[REQUEST_ID_HEADER],
        details={"email_sha256": digest, "reason": reason},
    )
    row_text = " ".join(str(getattr(event, column.key)) for column in AuditEvent.__table__.columns)
    assert email.strip().lower() not in row_text.lower()
    assert password not in row_text


async def test_inactive_user_login_failure(
    db_client: AsyncClient, make_user: UserFactory, infra: Infra
) -> None:
    await make_user("gone@example.org", Role.VIEWER, is_active=False)
    mark = await last_event_id(infra)

    assert (await login(db_client, email="gone@example.org")).status_code == 401

    event = await only_event_since(infra, mark)
    assert event.details["reason"] == "inactive_user"
    assert event.actor_user_id is None


async def test_rate_limited_login_is_recorded(
    db_client: AsyncClient, admin_user: User, infra: Infra
) -> None:
    for _ in range(5):
        await login(db_client, password="wrong password, long enough")
    mark = await last_event_id(infra)

    response = await login(db_client)

    assert response.status_code == 429
    event = await only_event_since(infra, mark)
    assert_event(
        event, action=AuditAction.AUTH_RATE_LIMITED, outcome=AuditOutcome.DENIED, actor=None
    )
    assert event.details["attempted"] == "auth.login"
    assert event.details["email_sha256"] == hashlib.sha256(ADMIN_EMAIL.encode()).hexdigest()
    assert 0 < event.details["retry_after"] <= 900


async def test_logout_is_recorded(admin: AsyncClient, admin_user: User, infra: Infra) -> None:
    mark = await last_event_id(infra)

    response = await admin.post(f"{PREFIX}/auth/logout", headers=csrf(admin))

    assert response.status_code == 204
    assert_event(
        await only_event_since(infra, mark),
        action=AuditAction.AUTH_LOGOUT,
        outcome=AuditOutcome.SUCCESS,
        actor=admin_user,
        request_id=response.headers[REQUEST_ID_HEADER],
        target=admin_user.id,
    )


async def test_password_change_is_recorded(
    admin: AsyncClient, admin_user: User, new_client: ClientFactory, infra: Infra
) -> None:
    await signed_in(new_client, admin_user)  # a second session, revoked by the change
    mark = await last_event_id(infra)

    response = await admin.post(
        f"{PREFIX}/auth/password",
        json={"current_password": PASSWORD, "new_password": "a brand new passphrase"},
        headers=csrf(admin),
    )

    assert response.status_code == 204
    event = await only_event_since(infra, mark)
    assert_event(
        event,
        action=AuditAction.AUTH_PASSWORD_CHANGE,
        outcome=AuditOutcome.SUCCESS,
        actor=admin_user,
        request_id=response.headers[REQUEST_ID_HEADER],
        target=admin_user.id,
        details={"revoked_sessions": 1},
    )
    assert "brand new" not in str(event.details)


async def test_wrong_current_password_is_recorded_as_failure(
    admin: AsyncClient, admin_user: User, infra: Infra
) -> None:
    mark = await last_event_id(infra)

    response = await admin.post(
        f"{PREFIX}/auth/password",
        json={"current_password": "not my password!", "new_password": "a brand new passphrase"},
        headers=csrf(admin),
    )

    assert response.status_code == 400
    assert_event(
        await only_event_since(infra, mark),
        action=AuditAction.AUTH_PASSWORD_CHANGE,
        outcome=AuditOutcome.FAILURE,
        actor=admin_user,
        target=admin_user.id,
        details={"reason": "wrong_current_password"},
    )


async def test_password_policy_rejection_is_not_recorded(admin: AsyncClient, infra: Infra) -> None:
    mark = await last_event_id(infra)

    response = await admin.post(
        f"{PREFIX}/auth/password",
        json={"current_password": PASSWORD, "new_password": "short"},
        headers=csrf(admin),
    )

    assert response.status_code == 422
    assert await events_since(infra, mark) == []


async def test_rate_limited_password_change_is_recorded(
    admin: AsyncClient, admin_user: User, infra: Infra
) -> None:
    for _ in range(5):
        await admin.post(
            f"{PREFIX}/auth/password",
            json={"current_password": "not my password!", "new_password": "x" * 20},
            headers=csrf(admin),
        )
    mark = await last_event_id(infra)

    response = await admin.post(
        f"{PREFIX}/auth/password",
        json={"current_password": PASSWORD, "new_password": "a brand new passphrase"},
        headers=csrf(admin),
    )

    assert response.status_code == 429
    event = await only_event_since(infra, mark)
    assert_event(
        event,
        action=AuditAction.AUTH_RATE_LIMITED,
        outcome=AuditOutcome.DENIED,
        actor=admin_user,
        target=admin_user.id,
    )
    assert event.details["attempted"] == "auth.password_change"


# --- user management ----------------------------------------------------------------


async def test_user_create_is_recorded(admin: AsyncClient, admin_user: User, infra: Infra) -> None:
    mark = await last_event_id(infra)

    response = await admin.post(f"{PREFIX}/users", json=NEW_USER, headers=csrf(admin))

    assert response.status_code == 201
    event = await only_event_since(infra, mark)
    assert_event(
        event,
        action=AuditAction.USER_CREATE,
        outcome=AuditOutcome.SUCCESS,
        actor=admin_user,
        request_id=response.headers[REQUEST_ID_HEADER],
        target=response.json()["id"],
        details={"email": NEW_USER["email"], "role": "analyst"},
    )
    assert NEW_USER["password"] not in str(event.details)


async def test_failed_user_create_is_recorded_without_a_success_event(
    admin: AsyncClient, admin_user: User, infra: Infra
) -> None:
    await admin.post(f"{PREFIX}/users", json=NEW_USER, headers=csrf(admin))
    mark = await last_event_id(infra)

    duplicate = await admin.post(f"{PREFIX}/users", json=NEW_USER, headers=csrf(admin))
    weak = await admin.post(
        f"{PREFIX}/users",
        json={**NEW_USER, "email": "other@example.org", "password": "short"},
        headers=csrf(admin),
    )

    assert (duplicate.status_code, weak.status_code) == (409, 422)
    events = await events_since(infra, mark)
    assert [(e.action, e.outcome, e.details) for e in events] == [
        ("user.create", "failure", {"role": "analyst", "reason": "email_taken"}),
        ("user.create", "failure", {"role": "analyst", "reason": "password_policy"}),
    ]
    assert all(e.actor_user_id == admin_user.id for e in events)


async def test_user_update_is_recorded_with_the_changes(
    admin: AsyncClient,
    admin_user: User,
    make_user: UserFactory,
    new_client: ClientFactory,
    infra: Infra,
) -> None:
    viewer = await make_user("viewer@example.org", Role.VIEWER, display_name="Old Name")
    await signed_in(new_client, viewer)
    mark = await last_event_id(infra)

    response = await admin.patch(
        f"{PREFIX}/users/{viewer.id}",
        json={"display_name": "New Name", "role": "analyst", "is_active": False},
        headers=csrf(admin),
    )

    assert response.status_code == 200
    assert_event(
        await only_event_since(infra, mark),
        action=AuditAction.USER_UPDATE,
        outcome=AuditOutcome.SUCCESS,
        actor=admin_user,
        request_id=response.headers[REQUEST_ID_HEADER],
        target=viewer.id,
        details={
            "changes": {
                "display_name": {"from": "Old Name", "to": "New Name"},
                "role": {"from": "viewer", "to": "analyst"},
                "is_active": {"from": True, "to": False},
            },
            "revoked_sessions": 1,
        },
    )


@pytest.mark.parametrize(
    ("target", "changes", "status", "reason"),
    [
        ("self", {"role": "viewer"}, 409, "conflict"),
        ("missing", {"display_name": "x"}, 404, "not_found"),
    ],
)
async def test_failed_user_update_is_recorded(
    admin: AsyncClient,
    admin_user: User,
    infra: Infra,
    target: str,
    changes: dict[str, str],
    status: int,
    reason: str,
) -> None:
    user_id = admin_user.id if target == "self" else uuid.uuid4()
    mark = await last_event_id(infra)

    response = await admin.patch(f"{PREFIX}/users/{user_id}", json=changes, headers=csrf(admin))

    assert response.status_code == status
    assert_event(
        await only_event_since(infra, mark),
        action=AuditAction.USER_UPDATE,
        outcome=AuditOutcome.FAILURE,
        actor=admin_user,
        target=user_id,
        details={"reason": reason},
    )


async def test_session_revocation_is_recorded(
    admin: AsyncClient,
    admin_user: User,
    make_user: UserFactory,
    new_client: ClientFactory,
    infra: Infra,
) -> None:
    viewer = await make_user("viewer@example.org", Role.VIEWER)
    await signed_in(new_client, viewer)
    await signed_in(new_client, viewer)
    mark = await last_event_id(infra)

    response = await admin.delete(f"{PREFIX}/users/{viewer.id}/sessions", headers=csrf(admin))
    missing = uuid.uuid4()
    not_found = await admin.delete(f"{PREFIX}/users/{missing}/sessions", headers=csrf(admin))

    assert (response.status_code, not_found.status_code) == (204, 404)
    success, failure = await events_since(infra, mark)
    assert_event(
        success,
        action=AuditAction.USER_SESSIONS_REVOKE,
        outcome=AuditOutcome.SUCCESS,
        actor=admin_user,
        request_id=response.headers[REQUEST_ID_HEADER],
        target=viewer.id,
        details={"revoked_sessions": 2},
    )
    assert_event(
        failure,
        action=AuditAction.USER_SESSIONS_REVOKE,
        outcome=AuditOutcome.FAILURE,
        actor=admin_user,
        target=missing,
        details={"reason": "not_found"},
    )


async def test_cli_create_admin_is_recorded(db_settings: Settings, infra: Infra) -> None:
    mark = await last_event_id(infra)

    await cli.create_admin(
        db_settings, email="root@example.org", display_name="Root", password=PASSWORD
    )

    event = await only_event_since(infra, mark)
    assert (event.action, event.outcome) == ("user.create", "success")
    assert (event.actor_user_id, event.actor_label, event.actor_ip) == (None, "system:cli", None)
    assert uuid.UUID(event.request_id).version == 4  # generated: no HTTP request


# --- role checks ----------------------------------------------------------------------


@pytest.mark.parametrize("role", [Role.ANALYST, Role.VIEWER])
@pytest.mark.parametrize(
    ("method", "path", "template"),
    [
        ("GET", "/audit", "/api/v1/audit"),
        ("POST", "/audit/verify", "/api/v1/audit/verify"),
        ("GET", "/users", "/api/v1/users"),
        ("DELETE", f"/users/{uuid.uuid4()}/sessions", "/api/v1/users/{user_id}/sessions"),
    ],
)
async def test_non_admins_are_denied_and_the_denial_is_recorded(
    make_user: UserFactory,
    new_client: ClientFactory,
    infra: Infra,
    role: Role,
    method: str,
    path: str,
    template: str,
) -> None:
    user = await make_user(f"{role.value}@example.org", role)
    client = await signed_in(new_client, user)
    mark = await last_event_id(infra)

    response = await client.request(method, f"{PREFIX}{path}", headers=csrf(client))

    assert response.status_code == 403
    assert_event(
        await only_event_since(infra, mark),
        action=AuditAction.AUTHZ_DENIED,
        outcome=AuditOutcome.DENIED,
        actor=user,
        request_id=response.headers[REQUEST_ID_HEADER],
        details={
            "method": method,
            "path": template,
            "role": role.value,
            "required_roles": ["admin"],
        },
    )


# --- transactions and the chain -------------------------------------------------------


async def test_rolled_back_transaction_leaves_no_event(infra: Infra) -> None:
    mark = await last_event_id(infra)

    async with infra.sessionmaker() as db:
        await audit.record(
            db,
            action=AuditAction.USER_UPDATE,
            outcome=AuditOutcome.SUCCESS,
            actor=audit.Actor.system("test"),
        )
        await db.rollback()

    assert await events_since(infra, mark) == []
    assert (await verify_chain_now(infra)).ok


async def test_change_and_event_roll_back_together(
    admin_user: User, make_user: UserFactory, infra: Infra
) -> None:
    viewer = await make_user("viewer@example.org", Role.VIEWER)
    mark = await last_event_id(infra)

    async with infra.sessionmaker() as db:
        user = await db.get(User, viewer.id)
        assert user is not None
        user.display_name = "Changed"
        await audit.record(
            db,
            action=AuditAction.USER_UPDATE,
            outcome=AuditOutcome.SUCCESS,
            actor=audit.Actor.for_user(admin_user, None),
            target_id=viewer.id,
        )
        await db.rollback()

    async with infra.sessionmaker() as db:
        assert (await db.get(User, viewer.id)).display_name != "Changed"  # type: ignore[union-attr]
    assert await events_since(infra, mark) == []


async def verify_chain_now(infra: Infra, **kwargs: Any) -> Any:
    async with infra.sessionmaker() as db:
        return await verify_chain(db, **kwargs)


async def test_record_links_rows_and_stores_case_and_ip(infra: Infra) -> None:
    case_id = uuid.uuid4()
    async with infra.sessionmaker() as db:
        first = await audit.record(
            db,
            action=AuditAction.AUTHZ_DENIED,
            outcome="denied",
            actor=audit.Actor(None, None, "not-an-ip"),
            case_id=case_id,
            details={"token": "secret", "note": "kept"},
        )
        second = await audit.record(
            db,
            action="audit.verify",
            outcome=AuditOutcome.SUCCESS,
            actor=audit.Actor(None, None, "2001:DB8::1"),
        )
        await db.commit()

    rows = await all_events(infra)
    assert [row.id for row in rows] == [first.id, second.id]
    assert rows[0].prev_hash == GENESIS_HASH
    assert rows[1].prev_hash == rows[0].hash == first.hash
    assert rows[0].case_id == case_id
    assert rows[0].actor_ip is None
    assert ip_of(rows[1]) == "2001:db8::1"
    assert rows[0].details == {"note": "kept"}
    assert rows[0].occurred_at.tzinfo is not None


async def test_concurrent_appends_form_one_unbroken_chain(infra: Infra) -> None:
    count = 20

    async def append(index: int) -> None:
        async with infra.sessionmaker() as db:
            await audit.record(
                db,
                action=AuditAction.AUTH_LOGIN,
                outcome=AuditOutcome.FAILURE,
                actor=audit.Actor.anonymous(f"192.0.2.{index + 1}"),
                details={"n": index},
            )
            await asyncio.sleep(0.01)  # hold the chain lock a little while
            await db.commit()

    await asyncio.gather(*(append(index) for index in range(count)))

    rows = await all_events(infra)
    assert len(rows) == count
    assert sorted(row.details["n"] for row in rows) == list(range(count))
    expected_prev = GENESIS_HASH
    for row in rows:
        assert row.prev_hash == expected_prev
        expected_prev = row.hash
    result = await verify_chain_now(infra, batch_size=7)
    assert (result.ok, result.checked) == (True, count)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_events SET details = '{}'::jsonb",
        "UPDATE audit_events SET action = 'x' WHERE false",  # statement-level: no rows needed
        "DELETE FROM audit_events",
        "TRUNCATE audit_events",
    ],
)
async def test_audit_events_are_append_only(infra: Infra, statement: str) -> None:
    async with infra.sessionmaker() as db:
        await audit.record(
            db,
            action=AuditAction.AUTHZ_DENIED,
            outcome=AuditOutcome.DENIED,
            actor=audit.Actor.anonymous(None),
        )
        await db.commit()

    with pytest.raises(DBAPIError, match="audit_events is append-only"):
        async with infra.engine.begin() as conn:
            await conn.execute(text(statement))

    assert len(await all_events(infra)) == 1


async def test_users_with_audit_history_cannot_be_deleted(
    db_client: AsyncClient, admin_user: User, infra: Infra
) -> None:
    await login(db_client)

    with pytest.raises(IntegrityError, match="fk_audit_events_actor_user_id_users"):
        async with infra.engine.begin() as conn:
            await conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": admin_user.id})


# --- verification ---------------------------------------------------------------------


async def test_verify_an_empty_chain(infra: Infra) -> None:
    result = await verify_chain_now(infra)

    assert result.as_dict() == {"ok": True, "checked": 0, "first_bad_id": None, "reason": None}


async def test_verify_endpoint_on_a_clean_chain(
    admin: AsyncClient, admin_user: User, infra: Infra
) -> None:
    before = len(await all_events(infra))

    response = await admin.post(f"{PREFIX}/audit/verify", headers=csrf(admin))

    assert response.status_code == 200
    result = {"ok": True, "checked": before, "first_bad_id": None, "reason": None}
    assert response.json() == result
    event = (await all_events(infra))[-1]
    assert_event(
        event,
        action=AuditAction.AUDIT_VERIFY,
        outcome=AuditOutcome.SUCCESS,
        actor=admin_user,
        request_id=response.headers[REQUEST_ID_HEADER],
        details=result,
    )
    assert (await verify_chain_now(infra)).checked == before + 1


async def test_verify_detects_edited_details(
    admin: AsyncClient, admin_user: User, infra: Infra
) -> None:
    await admin.post(f"{PREFIX}/users", json=NEW_USER, headers=csrf(admin))
    victim = next(
        e for e in await all_events(infra) if e.action == "user.create" and e.actor_user_id
    )
    await tamper(
        infra,
        'UPDATE audit_events SET details = details || \'{"role": "viewer"}\' WHERE id = :id',
        id=victim.id,
    )

    response = await admin.post(f"{PREFIX}/audit/verify", headers=csrf(admin))

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "ok": False,
        "checked": body["checked"],
        "first_bad_id": victim.id,
        "reason": REASON_HASH,
    }
    event = (await all_events(infra))[-1]
    assert (event.action, event.outcome, event.details["first_bad_id"]) == (
        "audit.verify",
        "failure",
        victim.id,
    )


async def test_verify_detects_a_deleted_event_across_batches(
    admin: AsyncClient, infra: Infra
) -> None:
    for _ in range(4):
        await login(admin)
    rows = await all_events(infra)
    assert len(rows) == 6
    removed, following = rows[2], rows[3]
    await tamper(infra, "DELETE FROM audit_events WHERE id = :id", id=removed.id)

    result = await verify_chain_now(infra, batch_size=2)

    assert (result.ok, result.first_bad_id, result.reason) == (
        False,
        following.id,
        REASON_PREV_HASH,
    )


async def test_only_one_verification_at_a_time(admin: AsyncClient, infra: Infra) -> None:
    await infra.cache.set(VERIFY_LOCK_KEY, "someone-else", ex=60)
    mark = await last_event_id(infra)

    response = await admin.post(f"{PREFIX}/audit/verify", headers=csrf(admin))

    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0
    assert await events_since(infra, mark) == []
    # The other holder's lock is untouched; ours is released after a run.
    assert await infra.cache.get(VERIFY_LOCK_KEY) == b"someone-else"
    await infra.cache.delete(VERIFY_LOCK_KEY)
    assert (await admin.post(f"{PREFIX}/audit/verify", headers=csrf(admin))).status_code == 200
    assert await infra.cache.get(VERIFY_LOCK_KEY) is None


async def test_verify_is_unavailable_without_valkey(admin: AsyncClient, db_app: FastAPI) -> None:
    class DownCache:
        def lock(self, *_args: object, **_kwargs: object) -> "DownCache":
            return self

        async def acquire(self) -> bool:
            raise RedisConnectionError("valkey is down")

    db_app.dependency_overrides[get_cache] = DownCache
    try:
        response = await admin.post(f"{PREFIX}/audit/verify", headers=csrf(admin))
    finally:
        del db_app.dependency_overrides[get_cache]

    assert response.status_code == 503
    assert "valkey" not in response.text.lower()


# --- listing ----------------------------------------------------------------------------


async def test_list_hides_hashes_unless_asked(admin: AsyncClient, infra: Infra) -> None:
    plain = await admin.get(f"{PREFIX}/audit")
    with_hashes = await admin.get(f"{PREFIX}/audit", params={"include_hashes": "true"})

    assert plain.status_code == with_hashes.status_code == 200
    items = plain.json()["items"]
    assert len(items) == 2  # the admin's creation and sign-in
    assert set(items[0]) == {
        "id",
        "occurred_at",
        "actor_user_id",
        "actor_label",
        "actor_ip",
        "request_id",
        "action",
        "outcome",
        "target_type",
        "target_id",
        "case_id",
        "details",
    }
    assert "hash" not in plain.text
    rows = {row.id: row for row in await all_events(infra)}
    for listed in with_hashes.json()["items"]:
        assert listed["hash"] == rows[listed["id"]].hash
        assert listed["prev_hash"] == rows[listed["id"]].prev_hash


async def test_list_returns_the_recorded_fields(
    admin: AsyncClient, admin_user: User, infra: Infra
) -> None:
    created = await admin.post(f"{PREFIX}/users", json=NEW_USER, headers=csrf(admin))

    response = await admin.get(f"{PREFIX}/audit", params={"action": "user.create", "limit": 1})

    [item] = response.json()["items"]
    assert item["action"] == "user.create"
    assert item["outcome"] == "success"
    assert item["actor_user_id"] == str(admin_user.id)
    assert item["actor_label"] == ADMIN_EMAIL
    assert item["actor_ip"] == CLIENT_IP
    assert item["request_id"] == created.headers[REQUEST_ID_HEADER]
    assert item["target_type"] == "user"
    assert item["target_id"] == created.json()["id"]
    assert item["details"] == {"email": NEW_USER["email"], "role": "analyst"}
    assert "hash" not in item


async def test_pagination_walks_every_event_newest_first(admin: AsyncClient, infra: Infra) -> None:
    for _ in range(4):
        await login(admin)
    expected = [row.id for row in reversed(await all_events(infra))]

    seen: list[int] = []
    params: dict[str, Any] = {"limit": 2}
    while True:
        page = (await admin.get(f"{PREFIX}/audit", params=params)).json()
        seen.extend(item["id"] for item in page["items"])
        if page["next_before_id"] is None:
            break
        assert page["next_before_id"] == page["items"][-1]["id"]
        params["before_id"] = page["next_before_id"]

    # Listing is not audited, so nothing was added while paging.
    assert seen == expected


async def test_filters(
    admin: AsyncClient,
    admin_user: User,
    new_client: ClientFactory,
    infra: Infra,
) -> None:
    await login(new_client(), password="wrong password, long enough")
    case_id = uuid.uuid4()
    async with infra.sessionmaker() as db:
        await audit.record(
            db,
            action=AuditAction.AUDIT_VERIFY,
            outcome=AuditOutcome.SUCCESS,
            actor=audit.Actor.system("test"),
            case_id=case_id,
        )
        await db.commit()
    rows = await all_events(infra)

    async def ids(**params: Any) -> list[int]:
        response = await admin.get(f"{PREFIX}/audit", params={"limit": 200, **params})
        assert response.status_code == 200, response.text
        return sorted(item["id"] for item in response.json()["items"])

    def matching(predicate: Callable[[AuditEvent], bool]) -> list[int]:
        return sorted(row.id for row in rows if predicate(row))

    assert await ids(action="auth.login") == matching(lambda r: r.action == "auth.login")
    assert await ids(outcome="failure") == matching(lambda r: r.outcome == "failure")
    assert await ids(actor_user_id=str(admin_user.id)) == matching(
        lambda r: r.actor_user_id == admin_user.id
    )
    assert await ids(case_id=str(case_id)) == matching(lambda r: r.case_id == case_id)
    assert await ids(action="auth.login", outcome="success") == matching(
        lambda r: (r.action, r.outcome) == ("auth.login", "success")
    )
    middle = rows[1].occurred_at
    assert await ids(since=middle.isoformat()) == matching(lambda r: r.occurred_at >= middle)
    assert await ids(until=middle.isoformat()) == matching(lambda r: r.occurred_at < middle)
    later = datetime.now(UTC) + timedelta(days=1)
    assert await ids(since=later.isoformat()) == []


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 201},
        {"before_id": 0},
        {"action": "case.nuke"},
        {"outcome": "maybe"},
        {"since": "2026-01-01T00:00:00"},  # no timezone
        {"actor_user_id": "not-a-uuid"},
    ],
)
async def test_list_validates_parameters(admin: AsyncClient, params: dict[str, Any]) -> None:
    assert (await admin.get(f"{PREFIX}/audit", params=params)).status_code == 422


async def test_audit_needs_a_session(db_client: AsyncClient) -> None:
    assert (await db_client.get(f"{PREFIX}/audit")).status_code == 401
    assert (await db_client.post(f"{PREFIX}/audit/verify")).status_code == 401


async def test_incoming_request_id_ends_up_in_the_event(
    db_client: AsyncClient, admin_user: User, infra: Infra
) -> None:
    mark = await last_event_id(infra)

    response = await db_client.post(
        f"{PREFIX}/auth/login",
        json={"email": ADMIN_EMAIL, "password": PASSWORD},
        headers={REQUEST_ID_HEADER: "trace-from-proxy-42"},
    )

    assert response.headers[REQUEST_ID_HEADER] == "trace-from-proxy-42"
    assert (await only_event_since(infra, mark)).request_id == "trace-from-proxy-42"
