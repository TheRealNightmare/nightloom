"""The API records the real client IP behind the `web` proxy, and cannot be spoofed.

Runs against the Compose stack (``make up && make test-integration``). Signs in
with a throwaway user through nginx and directly on the API port, each time
with a forged ``X-Forwarded-For``, and reads the IP stored with the session
from PostgreSQL.
"""

import hashlib
import ipaddress
import os
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import delete, select

from nightloom_api.audit.service import Actor
from nightloom_api.auth import service, sessions
from nightloom_api.auth.models import Role, Session, User
from nightloom_api.config import Settings
from nightloom_api.infra import Infra, create_infra

pytestmark = pytest.mark.integration

API_URL = os.environ.get("NIGHTLOOM_TEST_API_URL", "http://127.0.0.1:8000")
WEB_URL = os.environ.get("NIGHTLOOM_TEST_WEB_URL", "http://127.0.0.1:8080")
WEB_IP = os.environ.get("NIGHTLOOM_WEB_IP", "172.30.0.10")
SPOOFED_IP = "203.0.113.99"
PASSWORD = "integration test password"
USER_EMAIL = "client-ip-test@example.org"


@pytest.fixture
async def infra() -> AsyncIterator[Infra]:
    infra = create_infra(Settings(env="test"))
    try:
        yield infra
    finally:
        await infra.aclose()


@pytest.fixture
async def user_email(infra: Infra) -> AsyncIterator[str]:
    """A viewer in the stack's database, created on the first run and reused after that.

    Users with audit history cannot be deleted (the audit log is append-only),
    so the user stays; its sessions are deleted afterwards.
    """
    async with infra.sessionmaker() as db:
        user = (await db.execute(select(User).where(User.email == USER_EMAIL))).scalar_one_or_none()
        if user is None:
            await service.create_user(
                db,
                email=USER_EMAIL,
                display_name="Client IP test",
                role=Role.VIEWER,
                password=PASSWORD,
                actor=Actor.system("integration-test"),
            )
    try:
        yield USER_EMAIL
    finally:
        async with infra.sessionmaker() as db:
            await db.execute(
                delete(Session).where(
                    Session.user_id
                    == select(User.id).where(User.email == USER_EMAIL).scalar_subquery()
                )
            )
            await db.commit()


async def login_and_read_session_ip(infra: Infra, base_url: str, email: str) -> str | None:
    """Sign in with a forged X-Forwarded-For; return the IP stored with the new session."""
    async with httpx.AsyncClient(base_url=base_url, timeout=15) as client:
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": PASSWORD},
            headers={"X-Forwarded-For": SPOOFED_IP},
        )
        assert response.status_code == 200, response.text
        token = client.cookies[sessions.SESSION_COOKIE]

    token_hash = hashlib.sha256(token.encode()).hexdigest()
    async with infra.sessionmaker() as db:
        ip: str | None = (
            await db.execute(select(Session.ip).where(Session.token_hash == token_hash))
        ).scalar_one()
    return ip


async def test_login_through_web_proxy_records_the_real_client_ip(
    infra: Infra, user_email: str
) -> None:
    ip = await login_and_read_session_ip(infra, WEB_URL, user_email)

    assert ip is not None
    ipaddress.ip_address(ip)
    assert ip != SPOOFED_IP, "a client-sent X-Forwarded-For was trusted"
    assert ip != WEB_IP, "the API recorded the proxy's address, not the client's"


async def test_direct_login_ignores_a_spoofed_forwarded_for(infra: Infra, user_email: str) -> None:
    ip = await login_and_read_session_ip(infra, API_URL, user_email)

    assert ip is not None
    assert ip != SPOOFED_IP, "X-Forwarded-For from an untrusted peer was trusted"
