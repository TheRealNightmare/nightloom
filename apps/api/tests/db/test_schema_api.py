"""``GET /schema`` with real sessions: any signed-in user, nobody else."""

from collections.abc import Callable, Coroutine
from typing import Any

import pytest
from httpx import AsyncClient

from nightloom_api.auth.models import Role, User
from support import PREFIX, login

pytestmark = pytest.mark.db

UserFactory = Callable[..., Coroutine[Any, Any, User]]
ClientFactory = Callable[[], AsyncClient]


@pytest.mark.parametrize("role", list(Role))
async def test_every_signed_in_role_reads_the_schema(
    make_user: UserFactory, new_client: ClientFactory, role: Role
) -> None:
    user = await make_user(f"{role.value}@example.org", role)
    client = new_client()
    assert (await login(client, email=user.email)).status_code == 200

    response = await client.get(f"{PREFIX}/schema")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, max-age=300"
    body = response.json()
    assert len(body["entity_types"]) == 19
    assert body["manual_defaults"]["source"] == "manual"


async def test_signed_out_is_401(new_client: ClientFactory) -> None:
    response = await new_client().get(f"{PREFIX}/schema")
    assert response.status_code == 401
    assert "max-age" not in response.headers.get("cache-control", "")
