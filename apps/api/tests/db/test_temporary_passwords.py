"""Temporary passwords from admins, and the forced password change, against real services."""

import logging
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from nightloom_api.audit.models import AuditEvent
from nightloom_api.auth import passwords
from nightloom_api.auth.deps import PASSWORD_CHANGE_REQUIRED, PASSWORD_CHANGE_REQUIRED_CODE
from nightloom_api.auth.models import Role, Session, User
from nightloom_api.infra import Infra
from support import ADMIN_EMAIL, PASSWORD, PREFIX, csrf, login

pytestmark = pytest.mark.db

UserFactory = Callable[..., Coroutine[Any, Any, User]]
ClientFactory = Callable[[], AsyncClient]

NEW_USER = {"email": "temp.analyst@example.org", "display_name": "Temp Analyst", "role": "analyst"}
NEW_PASSWORD = "a brand new long passphrase"
REQUIRED = {"detail": PASSWORD_CHANGE_REQUIRED, "code": PASSWORD_CHANGE_REQUIRED_CODE}
VALID_CASE = {
    "title": "Forced change case",
    "purpose": "Checking that a pending password change blocks every case route.",
    "legal_basis": "owned_assets",
    "ethics_acknowledged": True,
}


@pytest.fixture
async def admin_user(make_user: UserFactory) -> User:
    return await make_user()


@pytest.fixture
async def admin(db_client: AsyncClient, admin_user: User) -> AsyncClient:
    assert (await login(db_client)).status_code == 200
    return db_client


async def create(admin: AsyncClient, **body: Any) -> Any:
    response = await admin.post(f"{PREFIX}/users", json={**NEW_USER, **body}, headers=csrf(admin))
    assert response.status_code == 201, response.text
    return response


async def stored_user(infra: Infra, email: str) -> User:
    async with infra.sessionmaker() as db:
        return (await db.execute(select(User).where(User.email == email))).scalar_one()


async def set_flag(infra: Infra, user_id: uuid.UUID, value: bool = True) -> None:
    async with infra.sessionmaker() as db:
        await db.execute(update(User).where(User.id == user_id).values(must_change_password=value))
        await db.commit()


async def events_text(infra: Infra) -> str:
    async with infra.sessionmaker() as db:
        events = (await db.execute(select(AuditEvent))).scalars().all()
    return " ".join(f"{e.action} {e.target_id} {e.details} {e.actor_label}" for e in events)


async def session_count(infra: Infra, user_id: uuid.UUID) -> int:
    async with infra.sessionmaker() as db:
        rows = (await db.execute(select(Session).where(Session.user_id == user_id))).all()
    return len(rows)


# --- Creating users ------------------------------------------------------------


async def test_create_generates_a_temporary_password_returned_once(
    admin: AsyncClient, infra: Infra, new_client: ClientFactory
) -> None:
    response = await create(admin)

    body = response.json()
    temporary = body["temporary_password"]
    assert isinstance(temporary, str)
    assert len(temporary) >= 20
    assert body["must_change_password"] is True
    assert response.headers["cache-control"] == "no-store"

    # Stored only as an Argon2 hash.
    user = await stored_user(infra, NEW_USER["email"])
    assert user.must_change_password is True
    assert temporary not in user.password_hash
    assert passwords.verify_password(temporary, user.password_hash)[0]

    # Never shown again: not in the listing, nor in the user as /auth/me sees it.
    listed = await admin.get(f"{PREFIX}/users")
    assert temporary not in listed.text
    assert "temporary_password" not in listed.text
    client = new_client()
    signed_in = await login(client, email=NEW_USER["email"], password=temporary)
    assert signed_in.status_code == 200
    assert signed_in.json()["user"]["must_change_password"] is True
    assert temporary not in signed_in.text
    me = await client.get(f"{PREFIX}/auth/me")
    assert me.json()["must_change_password"] is True
    assert temporary not in me.text


async def test_temporary_password_never_reaches_audit_or_logs(
    admin: AsyncClient,
    admin_user: User,
    infra: Infra,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    created = (await create(admin)).json()
    reset = await admin.post(f"{PREFIX}/users/{created['id']}/password-reset", headers=csrf(admin))

    secrets = [created["temporary_password"], reset.json()["temporary_password"]]
    audit_text = await events_text(infra)
    for secret in secrets:
        assert secret not in audit_text
        assert secret not in caplog.text
    async with infra.sessionmaker() as db:
        event = (
            await db.execute(
                select(AuditEvent)
                .where(AuditEvent.action == "user.create")
                .order_by(AuditEvent.id.desc())
            )
        ).scalar()
    assert event is not None
    assert event.details == {
        "email": NEW_USER["email"],
        "role": "analyst",
        "credential": "generated",
        "change_required": True,
    }


async def test_create_with_a_supplied_password(admin: AsyncClient, infra: Infra) -> None:
    response = await create(admin, generate_password=False, password=PASSWORD + " x")

    body = response.json()
    assert body["temporary_password"] is None
    # Still required by default...
    assert body["must_change_password"] is True

    # ...unless the admin opts out explicitly.
    other = await create(
        admin,
        email="opted.out@example.org",
        generate_password=False,
        password=PASSWORD + " x",
        must_change_password=False,
    )
    assert other.json()["must_change_password"] is False
    assert (await stored_user(infra, "opted.out@example.org")).must_change_password is False


@pytest.mark.parametrize(
    "body",
    [
        {"generate_password": True, "password": "a sufficiently long password"},
        {"password": "a sufficiently long password"},  # generate_password defaults to true
        {"generate_password": False},
    ],
)
async def test_create_needs_exactly_one_password_source(
    admin: AsyncClient, body: dict[str, Any]
) -> None:
    response = await admin.post(f"{PREFIX}/users", json={**NEW_USER, **body}, headers=csrf(admin))

    assert response.status_code == 422
    assert "a sufficiently long password" not in response.text


# --- Enforcement ---------------------------------------------------------------


@pytest.fixture
async def pending(
    make_user: UserFactory, new_client: ClientFactory, infra: Infra
) -> tuple[AsyncClient, User, str]:
    """An analyst with a case of their own, signed in, who must now change their password."""
    user = await make_user("pending@example.org", Role.ANALYST)
    client = new_client()
    assert (await login(client, email=user.email)).status_code == 200
    case = await client.post(f"{PREFIX}/cases", json=VALID_CASE, headers=csrf(client))
    assert case.status_code == 201
    await set_flag(infra, user.id)
    return client, user, case.json()["id"]


async def test_pending_change_blocks_every_other_route(
    pending: tuple[AsyncClient, User, str],
) -> None:
    client, _user, case_id = pending
    case = f"{PREFIX}/cases/{case_id}"
    calls: list[tuple[str, str, dict[str, Any] | None]] = [
        ("GET", f"{PREFIX}/cases", None),
        ("POST", f"{PREFIX}/cases", VALID_CASE),
        ("GET", case, None),
        ("GET", f"{case}/members", None),
        ("GET", f"{case}/graph", None),
        ("POST", f"{case}/entities", {"type": "domain", "value": "example.org"}),
        ("GET", f"{case}/jobs", None),
        ("POST", f"{case}/jobs", {"shuttle": "dns", "entity_key": "domain:example.org"}),
        ("GET", f"{case}/export?format=json", None),
        ("GET", f"{case}/export/check?format=json", None),
        ("GET", f"{PREFIX}/schema", None),
        ("GET", f"{PREFIX}/shuttles", None),
        ("GET", f"{PREFIX}/users", None),
        ("GET", f"{PREFIX}/audit", None),
    ]
    for method, url, body in calls:
        headers = csrf(client) if method != "GET" else {}
        response = await client.request(method, url, json=body, headers=headers)
        assert (response.status_code, response.json()) == (403, REQUIRED), (method, url)


async def test_pending_change_blocks_admin_routes(
    admin: AsyncClient, admin_user: User, infra: Infra
) -> None:
    await set_flag(infra, admin_user.id)

    for method, url in [
        ("GET", f"{PREFIX}/users"),
        ("POST", f"{PREFIX}/users"),
        ("POST", f"{PREFIX}/users/{uuid.uuid4()}/password-reset"),
        ("POST", f"{PREFIX}/audit/verify"),
    ]:
        response = await admin.request(
            method, url, json=NEW_USER if method == "POST" else None, headers=csrf(admin)
        )
        assert (response.status_code, response.json()) == (403, REQUIRED), (method, url)


async def test_the_three_allowed_routes_answer(
    pending: tuple[AsyncClient, User, str], new_client: ClientFactory, infra: Infra
) -> None:
    client, user, case_id = pending

    me = await client.get(f"{PREFIX}/auth/me")
    assert me.status_code == 200
    assert me.json()["must_change_password"] is True

    changed = await client.post(
        f"{PREFIX}/auth/password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        headers=csrf(client),
    )
    assert changed.status_code == 204
    assert (await stored_user(infra, user.email)).must_change_password is False
    assert (await client.get(f"{PREFIX}/auth/me")).json()["must_change_password"] is False
    assert (await client.get(f"{PREFIX}/cases/{case_id}")).status_code == 200

    # Logout also works while the change is pending.
    await set_flag(infra, user.id)
    other = new_client()
    assert (await login(other, email=user.email, password=NEW_PASSWORD)).status_code == 200
    out = await other.post(f"{PREFIX}/auth/logout", headers=csrf(other))
    assert out.status_code == 204
    assert (await other.get(f"{PREFIX}/auth/me")).status_code == 401


async def test_pending_change_still_needs_csrf_and_a_session(
    pending: tuple[AsyncClient, User, str], new_client: ClientFactory
) -> None:
    client, _user, _case_id = pending

    no_csrf = await client.post(
        f"{PREFIX}/auth/password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert no_csrf.status_code == 403
    assert "code" not in no_csrf.json()
    assert (await new_client().get(f"{PREFIX}/cases")).status_code == 401


async def test_a_rejected_new_password_keeps_the_flag(
    pending: tuple[AsyncClient, User, str], infra: Infra
) -> None:
    client, user, _case_id = pending

    weak = await client.post(
        f"{PREFIX}/auth/password",
        json={"current_password": PASSWORD, "new_password": "short"},
        headers=csrf(client),
    )

    assert weak.status_code == 422
    assert (await stored_user(infra, user.email)).must_change_password is True


async def test_change_is_audited_as_required(
    pending: tuple[AsyncClient, User, str], infra: Infra
) -> None:
    client, user, _case_id = pending

    await client.post(
        f"{PREFIX}/auth/password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        headers=csrf(client),
    )

    async with infra.sessionmaker() as db:
        event = (
            await db.execute(select(AuditEvent).where(AuditEvent.action == "auth.password_change"))
        ).scalar_one()
    assert event.details == {"revoked_sessions": 0, "change_required": True}
    assert event.target_id == str(user.id)


# --- Resetting passwords -------------------------------------------------------


async def test_reset_gives_a_new_temporary_password_and_ends_every_session(
    admin: AsyncClient,
    make_user: UserFactory,
    new_client: ClientFactory,
    infra: Infra,
) -> None:
    analyst = await make_user("reset.me@example.org", Role.ANALYST)
    first, second = new_client(), new_client()
    for client in (first, second):
        assert (await login(client, email=analyst.email)).status_code == 200
    assert await session_count(infra, analyst.id) == 2

    response = await admin.post(f"{PREFIX}/users/{analyst.id}/password-reset", headers=csrf(admin))

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    temporary = body["temporary_password"]
    assert len(temporary) >= 20
    assert body["revoked_sessions"] == 2
    assert body["user"]["must_change_password"] is True
    assert await session_count(infra, analyst.id) == 0
    assert (await first.get(f"{PREFIX}/auth/me")).status_code == 401

    # The old password is gone; the temporary one signs in to the forced change.
    assert (await login(new_client(), email=analyst.email)).status_code == 401
    third = new_client()
    assert (await login(third, email=analyst.email, password=temporary)).status_code == 200
    assert (await third.get(f"{PREFIX}/cases")).status_code == 403

    async with infra.sessionmaker() as db:
        event = (
            await db.execute(select(AuditEvent).where(AuditEvent.action == "user.password_reset"))
        ).scalar_one()
    assert (event.outcome, event.target_id, event.details) == (
        "success",
        str(analyst.id),
        {"revoked_sessions": 2},
    )


async def test_admins_cannot_reset_their_own_password(
    admin: AsyncClient, admin_user: User, infra: Infra
) -> None:
    response = await admin.post(
        f"{PREFIX}/users/{admin_user.id}/password-reset", headers=csrf(admin)
    )

    assert response.status_code == 400
    assert "Change password" in response.json()["detail"]
    assert (await stored_user(infra, ADMIN_EMAIL)).must_change_password is False
    assert (await admin.get(f"{PREFIX}/auth/me")).status_code == 200
    async with infra.sessionmaker() as db:
        event = (
            await db.execute(select(AuditEvent).where(AuditEvent.action == "user.password_reset"))
        ).scalar_one()
    assert (event.outcome, event.details) == ("failure", {"reason": "self_reset"})


async def test_reset_of_an_unknown_user(admin: AsyncClient) -> None:
    response = await admin.post(
        f"{PREFIX}/users/{uuid.uuid4()}/password-reset", headers=csrf(admin)
    )

    assert response.status_code == 404


async def test_only_admins_reset_passwords(
    make_user: UserFactory, new_client: ClientFactory
) -> None:
    analyst = await make_user("analyst@example.org", Role.ANALYST)
    client = new_client()
    assert (await login(client, email=analyst.email)).status_code == 200

    response = await client.post(
        f"{PREFIX}/users/{analyst.id}/password-reset", headers=csrf(client)
    )

    assert response.status_code == 403
    assert "code" not in response.json()
