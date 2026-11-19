"""Admin user management and role checks against real services."""

import uuid
from collections.abc import Callable, Coroutine
from typing import Any

import pytest
from httpx import AsyncClient

from nightloom_api.auth import service
from nightloom_api.auth.models import Role, User
from nightloom_api.auth.schemas import UserUpdate
from nightloom_api.infra import Infra
from support import ADMIN_EMAIL, PASSWORD, PREFIX, csrf, login

pytestmark = pytest.mark.db

UserFactory = Callable[..., Coroutine[Any, Any, User]]
ClientFactory = Callable[[], AsyncClient]
NEW_USER: dict[str, Any] = {
    "email": "New.Analyst@Example.org",
    "display_name": "  New Analyst ",
    "role": "analyst",
    "generate_password": False,
    "password": "a sufficiently long password",
    "must_change_password": False,
}


@pytest.fixture
async def admin(db_client: AsyncClient, make_user: UserFactory) -> AsyncClient:
    """A client signed in as an admin."""
    await make_user()
    assert (await login(db_client)).status_code == 200
    return db_client


async def test_admin_creates_and_lists_users(admin: AsyncClient) -> None:
    response = await admin.post(f"{PREFIX}/users", json=NEW_USER, headers=csrf(admin))

    assert response.status_code == 201
    created = response.json()
    assert created["email"] == "new.analyst@example.org"
    assert created["display_name"] == "New Analyst"
    assert created["role"] == "analyst"
    assert created["is_active"] is True
    assert created["last_login_at"] is None
    assert created["must_change_password"] is False
    assert created["temporary_password"] is None  # the admin supplied it
    assert NEW_USER["password"] not in response.text
    assert "password_hash" not in response.text

    listed = await admin.get(f"{PREFIX}/users")
    assert listed.status_code == 200
    assert [user["email"] for user in listed.json()] == [ADMIN_EMAIL, "new.analyst@example.org"]
    assert NEW_USER["password"] not in listed.text
    assert "password_hash" not in listed.text
    assert "temporary_password" not in listed.text


async def test_created_user_can_log_in(admin: AsyncClient, new_client: ClientFactory) -> None:
    await admin.post(f"{PREFIX}/users", json=NEW_USER, headers=csrf(admin))

    response = await login(new_client(), email=NEW_USER["email"], password=NEW_USER["password"])

    assert response.status_code == 200
    assert response.json()["user"]["role"] == "analyst"


async def test_duplicate_email_conflicts(admin: AsyncClient) -> None:
    await admin.post(f"{PREFIX}/users", json=NEW_USER, headers=csrf(admin))

    again = await admin.post(
        f"{PREFIX}/users",
        json={**NEW_USER, "email": "NEW.ANALYST@example.org"},
        headers=csrf(admin),
    )

    assert again.status_code == 409
    assert again.json() == {"detail": "A user with this email already exists."}


@pytest.mark.parametrize(
    ("changes", "fragment"),
    [
        ({"password": "short"}, "at least 12 characters"),
        ({"password": "new.analyst-is-my-password"}, "must not contain your email"),
    ],
)
async def test_create_enforces_password_policy(
    admin: AsyncClient, changes: dict[str, str], fragment: str
) -> None:
    response = await admin.post(
        f"{PREFIX}/users", json={**NEW_USER, **changes}, headers=csrf(admin)
    )

    assert response.status_code == 422
    assert fragment in response.json()["detail"]
    assert changes["password"] not in response.text


@pytest.mark.parametrize(
    "changes",
    [
        {"email": "not-an-email"},
        {"role": "superuser"},
        {"display_name": "   "},
        {"is_admin": True},
    ],
)
async def test_create_validates_the_body(admin: AsyncClient, changes: dict[str, object]) -> None:
    response = await admin.post(
        f"{PREFIX}/users", json={**NEW_USER, **changes}, headers=csrf(admin)
    )

    assert response.status_code == 422
    assert NEW_USER["password"] not in response.text


async def test_admin_updates_a_user(
    admin: AsyncClient, make_user: UserFactory, new_client: ClientFactory
) -> None:
    viewer = await make_user("viewer@example.org", Role.VIEWER)
    viewer_client = new_client()
    await login(viewer_client, email=viewer.email)

    response = await admin.patch(
        f"{PREFIX}/users/{viewer.id}",
        json={"display_name": "Promoted", "role": "analyst"},
        headers=csrf(admin),
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["display_name"], body["role"], body["is_active"]) == ("Promoted", "analyst", True)
    assert body["updated_at"] >= body["created_at"]
    # The role change applies to the existing session immediately.
    me = await viewer_client.get(f"{PREFIX}/auth/me")
    assert me.json()["role"] == "analyst"


async def test_deactivating_signs_the_user_out(
    admin: AsyncClient, make_user: UserFactory, new_client: ClientFactory
) -> None:
    analyst = await make_user("analyst@example.org", Role.ANALYST)
    analyst_client = new_client()
    await login(analyst_client, email=analyst.email)

    response = await admin.patch(
        f"{PREFIX}/users/{analyst.id}", json={"is_active": False}, headers=csrf(admin)
    )

    assert response.status_code == 200
    assert response.json()["is_active"] is False
    assert (await analyst_client.get(f"{PREFIX}/auth/me")).status_code == 401
    assert (await login(new_client(), email=analyst.email)).status_code == 401

    reactivated = await admin.patch(
        f"{PREFIX}/users/{analyst.id}", json={"is_active": True}, headers=csrf(admin)
    )
    assert reactivated.json()["is_active"] is True
    assert (await login(new_client(), email=analyst.email)).status_code == 200


@pytest.mark.parametrize("changes", [{"role": "analyst"}, {"is_active": False}])
async def test_admin_cannot_demote_or_deactivate_themselves(
    admin: AsyncClient, make_user: UserFactory, infra: Infra, changes: dict[str, object]
) -> None:
    await make_user("second.admin@example.org", Role.ADMIN)  # not the last admin
    me = (await admin.get(f"{PREFIX}/auth/me")).json()

    response = await admin.patch(f"{PREFIX}/users/{me['id']}", json=changes, headers=csrf(admin))

    assert response.status_code == 409
    assert "your own admin role" in response.json()["detail"]


async def test_admin_can_rename_themselves(admin: AsyncClient) -> None:
    me = (await admin.get(f"{PREFIX}/auth/me")).json()

    response = await admin.patch(
        f"{PREFIX}/users/{me['id']}",
        json={"display_name": "Renamed", "role": "admin", "is_active": True},
        headers=csrf(admin),
    )

    assert response.status_code == 200
    assert response.json()["display_name"] == "Renamed"


async def test_admin_can_demote_another_admin(admin: AsyncClient, make_user: UserFactory) -> None:
    other = await make_user("second.admin@example.org", Role.ADMIN)

    response = await admin.patch(
        f"{PREFIX}/users/{other.id}", json={"role": "viewer"}, headers=csrf(admin)
    )

    assert response.status_code == 200
    assert response.json()["role"] == "viewer"


async def test_last_active_admin_cannot_be_removed(make_user: UserFactory, infra: Infra) -> None:
    # Through the API an admin can only reach the last admin by targeting
    # themselves; this guards the rule itself (e.g. against concurrent changes).
    last_admin = await make_user()
    inactive_admin = await make_user("old.admin@example.org", Role.ADMIN, is_active=False)
    actor = await make_user("someone@example.org", Role.ANALYST)

    for changes in (UserUpdate(role=Role.VIEWER), UserUpdate(is_active=False)):
        async with infra.sessionmaker() as db:
            with pytest.raises(service.ConflictError, match="At least one active admin"):
                await service.update_user(db, actor, last_admin.id, changes)

    # An inactive admin does not count, and changing it is fine.
    async with infra.sessionmaker() as db:
        updated = await service.update_user(
            db, actor, inactive_admin.id, UserUpdate(role=Role.VIEWER)
        )
    assert updated.role == Role.VIEWER


async def test_unknown_user_is_404(admin: AsyncClient) -> None:
    missing = uuid.uuid4()

    patch = await admin.patch(
        f"{PREFIX}/users/{missing}", json={"display_name": "x"}, headers=csrf(admin)
    )
    revoke = await admin.delete(f"{PREFIX}/users/{missing}/sessions", headers=csrf(admin))

    assert patch.status_code == 404
    assert revoke.status_code == 404
    assert patch.json() == {"detail": "User not found."}


async def test_admin_revokes_sessions(
    admin: AsyncClient, make_user: UserFactory, new_client: ClientFactory
) -> None:
    analyst = await make_user("analyst@example.org", Role.ANALYST)
    first, second = new_client(), new_client()
    await login(first, email=analyst.email)
    await login(second, email=analyst.email)

    response = await admin.delete(f"{PREFIX}/users/{analyst.id}/sessions", headers=csrf(admin))

    assert response.status_code == 204
    assert (await first.get(f"{PREFIX}/auth/me")).status_code == 401
    assert (await second.get(f"{PREFIX}/auth/me")).status_code == 401
    assert (await admin.get(f"{PREFIX}/auth/me")).status_code == 200
    # Revoking is not deactivating: the user can sign in again.
    assert (await login(new_client(), email=analyst.email)).status_code == 200


@pytest.mark.parametrize("role", [Role.VIEWER, Role.ANALYST])
async def test_non_admins_get_403_on_admin_routes(
    db_client: AsyncClient, make_user: UserFactory, role: Role
) -> None:
    target = await make_user()  # an admin to aim at
    await make_user("member@example.org", role)
    await login(db_client, email="member@example.org", password=PASSWORD)
    headers = csrf(db_client)  # valid CSRF: the 403 must come from the role check

    responses = [
        await db_client.get(f"{PREFIX}/users"),
        await db_client.post(f"{PREFIX}/users", json=NEW_USER, headers=headers),
        await db_client.patch(
            f"{PREFIX}/users/{target.id}", json={"role": "viewer"}, headers=headers
        ),
        await db_client.delete(f"{PREFIX}/users/{target.id}/sessions", headers=headers),
    ]

    for response in responses:
        assert response.status_code == 403
        assert response.json() == {"detail": "You do not have permission to do this."}
    # Their own account still works.
    assert (await db_client.get(f"{PREFIX}/auth/me")).json()["role"] == role.value


async def test_admin_routes_need_csrf(admin: AsyncClient) -> None:
    assert (await admin.post(f"{PREFIX}/users", json=NEW_USER)).status_code == 403
