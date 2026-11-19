"""The production HTTPS edge: Caddy -> nginx -> API, with real client IPs.

Runs against the production stack with the `https` profile and the test
override (Caddy's internal CA, ports on 127.0.0.1):

    make secrets ARGS="--domain nightloom.localhost --acme-email admin@example.org"
    make up EDGE_TEST=1
    make test-edge EDGE_TEST=1

`make test-edge` copies Caddy's internal root certificate out of the
container, so TLS is verified for real. Skipped unless NIGHTLOOM_TEST_EDGE_URL
is set.

The request from the test reaches Caddy through Docker's published port, so
the client Caddy sees is the Compose network's gateway (the host). Every
spoofable header is sent with a fake address; the API must record the
gateway, never the fake address, Caddy's or nginx's.
"""

import hashlib
import ipaddress
import os
import ssl
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from nightloom_api.audit.service import Actor
from nightloom_api.auth import service, sessions
from nightloom_api.auth.models import Role, Session, User
from nightloom_api.config import Settings
from nightloom_api.infra import Infra, create_infra

EDGE_URL = os.environ.get("NIGHTLOOM_TEST_EDGE_URL", "")
EDGE_HOST = os.environ.get("NIGHTLOOM_TEST_EDGE_HOST", "nightloom.localhost")
EDGE_CA = os.environ.get("NIGHTLOOM_TEST_EDGE_CA", "")
# The Compose network's gateway: the address the host's requests arrive from.
# `make test-edge` reads it from Docker (with an ip_range, Docker puts it at
# the range's first address, not the subnet's).
IP_RANGE = ipaddress.ip_network(os.environ.get("NIGHTLOOM_NETWORK_IP_RANGE", "172.30.0.128/25"))
EXPECTED_CLIENT_IP = os.environ.get("NIGHTLOOM_TEST_EDGE_CLIENT_IP") or str(IP_RANGE[0])
WEB_IP = os.environ.get("NIGHTLOOM_WEB_IP", "172.30.0.10")
CADDY_IP = os.environ.get("NIGHTLOOM_CADDY_IP", "172.30.0.11")
SPOOFED_IP = "203.0.113.99"
SPOOFED_HEADERS = {
    "X-Forwarded-For": SPOOFED_IP,
    "X-Real-IP": SPOOFED_IP,
    "CF-Connecting-IP": SPOOFED_IP,
    "Forwarded": f"for={SPOOFED_IP}",
}

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not EDGE_URL,
        reason="needs the production stack with the HTTPS edge (make up EDGE_TEST=1)",
    ),
]


def edge_client() -> httpx.AsyncClient:
    """A client for Caddy on 127.0.0.1 that presents and verifies EDGE_HOST (SNI)."""
    context = ssl.create_default_context(cafile=EDGE_CA or None)
    return httpx.AsyncClient(
        base_url=EDGE_URL, headers={"Host": EDGE_HOST}, verify=context, timeout=15
    )


def sni() -> dict[str, Any]:
    return {"sni_hostname": EDGE_HOST}


@pytest.fixture
async def infra() -> AsyncIterator[Infra]:
    infra = create_infra(Settings(env="test"))
    try:
        yield infra
    finally:
        await infra.aclose()


@pytest.fixture
async def viewer(infra: Infra) -> AsyncIterator[tuple[str, str]]:
    """A fresh viewer (email, password), deactivated afterwards."""
    email = f"edge-{uuid.uuid4().hex[:12]}@example.org"
    password = f"edge test {uuid.uuid4().hex}"
    async with infra.sessionmaker() as db:
        user = await service.create_user(
            db,
            email=email,
            display_name="Edge test",
            role=Role.VIEWER,
            password=password,
            actor=Actor.system("integration-test"),
        )
        user_id = user.id
    try:
        yield email, password
    finally:
        async with infra.sessionmaker() as db:
            stored = await db.get(User, user_id)
            if stored is not None:
                stored.is_active = False
                await db.commit()


async def test_login_through_caddy_records_the_real_client_ip(
    infra: Infra, viewer: tuple[str, str]
) -> None:
    email, password = viewer
    async with edge_client() as client:
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
            headers=SPOOFED_HEADERS,
            extensions=sni(),
        )
        assert response.status_code == 200, response.text
        token = client.cookies[sessions.SESSION_COOKIE]

    # Production cookies: Secure, HttpOnly session, SameSite=Strict.
    cookies = {c.split("=", 1)[0]: c.lower() for c in response.headers.get_list("set-cookie")}
    session_cookie = cookies[sessions.SESSION_COOKIE]
    assert "; secure" in session_cookie
    assert "; httponly" in session_cookie
    assert "samesite=strict" in session_cookie
    assert "; secure" in cookies["nightloom_csrf"]
    assert response.headers["strict-transport-security"] == "max-age=31536000"

    token_hash = hashlib.sha256(token.encode()).hexdigest()
    async with infra.sessionmaker() as db:
        ip = (
            await db.execute(select(Session.ip).where(Session.token_hash == token_hash))
        ).scalar_one()

    assert ip is not None
    assert ip != SPOOFED_IP, "a client-sent forwarding header was trusted"
    assert ip != CADDY_IP, "the API recorded Caddy's address, not the client's"
    assert ip != WEB_IP, "the API recorded nginx's address, not the client's"
    assert ip == EXPECTED_CLIENT_IP


async def test_static_pages_keep_nginx_headers_and_gain_hsts() -> None:
    async with edge_client() as client:
        response = await client.get("/", extensions=sni())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["strict-transport-security"] == "max-age=31536000"
    # Set once, by nginx; Caddy adds no second copy.
    assert len(response.headers.get_list("content-security-policy")) == 1
    assert response.headers.get_list("x-frame-options") == ["DENY"]
    assert "server" not in response.headers
    assert "via" not in response.headers


async def test_request_body_limit() -> None:
    async with edge_client() as client:
        response = await client.post(
            "/api/v1/auth/login",
            content=b"x" * (2 * 1024 * 1024),
            headers={"Content-Type": "application/json"},
            extensions=sni(),
        )

    assert response.status_code == 413


async def test_plain_http_redirects_to_https() -> None:
    http_url = EDGE_URL.replace("https://", "http://").rsplit(":", 1)[0]
    http_port = os.environ.get("EDGE_HTTP_HOST_PORT", "8081")
    async with httpx.AsyncClient(timeout=10, headers={"Host": EDGE_HOST}) as client:
        response = await client.get(f"{http_url}:{http_port}/cases?q=1")

    assert response.status_code in {301, 308}
    assert response.headers["location"].startswith(f"https://{EDGE_HOST}/cases")
