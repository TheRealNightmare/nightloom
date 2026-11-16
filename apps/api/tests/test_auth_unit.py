"""Authentication building blocks that need no services."""

import asyncio
import hashlib
import hmac
import io
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException, Response
from httpx import ASGITransport, AsyncClient
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from pydantic import SecretStr, ValidationError

from nightloom_api import cli
from nightloom_api.auth import clock, deps, passwords, sessions
from nightloom_api.auth.models import Role, Session, User
from nightloom_api.auth.passwords import PasswordPolicyError
from nightloom_api.auth.schemas import UserCreate, UserOut, UserUpdate, normalize_email
from nightloom_api.config import Settings
from nightloom_api.main import create_app
from support import FakeClock

# --- password policy ----------------------------------------------------------


@pytest.mark.parametrize(
    ("password", "message"),
    [
        ("short-pw", "at least 12 characters"),
        ("x" * 11, "at least 12 characters"),
        ("x" * 129, "at most 128 characters"),
        ("alice.smith1", "must not contain your email"),  # equal to the local part
        ("my-ALICE.SMITH1-secret", "must not contain your email"),  # contains, any case
    ],
)
def test_password_policy_rejects(password: str, message: str) -> None:
    with pytest.raises(PasswordPolicyError, match=message) as excinfo:
        passwords.validate_password(password, "Alice.Smith1@Example.org")
    assert password not in str(excinfo.value)


@pytest.mark.parametrize("password", ["x" * 12, "y" * 128, "a long passphrase with spaces"])
def test_password_policy_accepts(password: str) -> None:
    passwords.validate_password(password, "alice.smith1@example.org")


def test_short_local_parts_are_only_checked_for_equality() -> None:
    # "al" appears in many passwords; only an exact match would be rejected,
    # and that is impossible at 12+ characters.
    passwords.validate_password("totally-normal-password", "al@example.org")


# --- hashing --------------------------------------------------------------------


def test_hash_is_argon2id_and_salted() -> None:
    first = passwords.hash_password("correct horse battery")
    second = passwords.hash_password("correct horse battery")

    assert first.startswith("$argon2id$")
    assert first != second


def test_verify_accepts_right_and_rejects_wrong_password() -> None:
    hashed = passwords.hash_password("correct horse battery")

    assert passwords.verify_password("correct horse battery", hashed) == (True, None)
    assert passwords.verify_password("wrong horse battery", hashed) == (False, None)


@pytest.mark.parametrize("bad_hash", ["", "not-a-hash", "$argon2id$v=19$garbage"])
def test_malformed_hash_is_a_mismatch(bad_hash: str) -> None:
    assert passwords.verify_password("anything at all", bad_hash) == (False, None)


def test_outdated_parameters_are_rehashed() -> None:
    weak = PasswordHash((Argon2Hasher(time_cost=1, memory_cost=8192, parallelism=1),))
    old_hash = weak.hash("correct horse battery")

    valid, new_hash = passwords.verify_password("correct horse battery", old_hash)

    assert valid is True
    assert new_hash is not None
    assert new_hash != old_hash
    assert passwords.verify_password("correct horse battery", new_hash) == (True, None)


def test_dummy_verify_runs_a_real_hash_check(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    real_verify = passwords._hasher.verify

    def spy(password: str, hashed: str) -> bool:
        calls.append(hashed)
        return real_verify(password, hashed)

    monkeypatch.setattr(passwords._hasher, "verify", spy)

    passwords.dummy_verify("whatever password")

    assert len(calls) == 1
    assert calls[0].startswith("$argon2id$")


# --- tokens and CSRF -------------------------------------------------------------


def test_tokens_are_random_and_256_bit() -> None:
    tokens = {sessions.new_token() for _ in range(100)}

    assert len(tokens) == 100
    assert all(len(token) == 43 for token in tokens)  # 32 bytes, base64url, no padding


def test_hash_token_is_hex_sha256() -> None:
    assert sessions.hash_token("abc") == hashlib.sha256(b"abc").hexdigest()
    assert len(sessions.hash_token(sessions.new_token())) == 64


def test_csrf_token_is_bound_to_the_session_token() -> None:
    token = sessions.new_token()
    csrf = sessions.csrf_token_for(token)

    assert csrf == sessions.csrf_token_for(token)
    assert csrf != sessions.csrf_token_for(sessions.new_token())
    assert token not in csrf


def test_csrf_matches() -> None:
    token = sessions.new_token()
    good = sessions.csrf_token_for(token)
    other = sessions.csrf_token_for(sessions.new_token())

    assert sessions.csrf_matches(good, good, token) is True
    assert sessions.csrf_matches(None, good, token) is False
    assert sessions.csrf_matches("", good, token) is False
    assert sessions.csrf_matches(good, None, token) is False
    assert sessions.csrf_matches(good, other, token) is False
    # Header and cookie agree, but were not issued for this session.
    assert sessions.csrf_matches(other, other, token) is False


def test_csrf_compare_is_constant_time(monkeypatch: pytest.MonkeyPatch) -> None:
    compared: list[tuple[bytes, bytes]] = []
    real = hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        compared.append((a, b))
        return real(a, b)

    monkeypatch.setattr(hmac, "compare_digest", spy)
    token = sessions.new_token()
    good = sessions.csrf_token_for(token)

    assert sessions.csrf_matches(good, good, token)
    assert len(compared) == 2


# --- cookies ---------------------------------------------------------------------


def cookie_headers(response: Response) -> dict[str, str]:
    headers = [value.decode() for key, value in response.raw_headers if key == b"set-cookie"]
    return {header.split("=", 1)[0]: header for header in headers}


PRODUCTION = {
    "env": "production",
    "database_url": SecretStr("postgresql+asyncpg://u:real@db/x"),
    "neo4j_password": SecretStr("real"),
    "valkey_url": SecretStr("redis://:real@cache/0"),
    "s3_access_key": SecretStr("real-ak"),
    "s3_secret_key": SecretStr("real-sk"),
}


def test_cookie_flags_in_development() -> None:
    response = Response()
    token = sessions.new_token()

    sessions.set_auth_cookies(response, token, Settings(env="development"))

    cookies = cookie_headers(response)
    session_cookie = cookies[sessions.SESSION_COOKIE]
    csrf_cookie = cookies[sessions.CSRF_COOKIE]
    assert session_cookie.startswith(f"{sessions.SESSION_COOKIE}={token};")
    assert "HttpOnly" in session_cookie
    assert "SameSite=strict" in session_cookie
    assert "Path=/" in session_cookie
    assert "Secure" not in session_cookie
    assert f"Max-Age={7 * 24 * 3600}" in session_cookie
    assert csrf_cookie.startswith(f"{sessions.CSRF_COOKIE}={sessions.csrf_token_for(token)};")
    assert "HttpOnly" not in csrf_cookie
    assert "SameSite=strict" in csrf_cookie


@pytest.mark.parametrize(
    "settings",
    [Settings.model_validate(PRODUCTION), Settings(env="development", cookie_secure=True)],
    ids=["production", "cookie_secure"],
)
def test_cookies_are_secure_in_production_or_when_configured(settings: Settings) -> None:
    response = Response()

    sessions.set_auth_cookies(response, sessions.new_token(), settings)
    sessions.clear_auth_cookies(response, settings)

    for header in [value.decode() for key, value in response.raw_headers if key == b"set-cookie"]:
        assert "; Secure" in header


def test_clear_auth_cookies_expires_both() -> None:
    response = Response()

    sessions.clear_auth_cookies(response, Settings(env="test"))

    cookies = cookie_headers(response)
    assert set(cookies) == {sessions.SESSION_COOKIE, sessions.CSRF_COOKIE}
    for header in cookies.values():
        assert "Max-Age=0" in header
        assert "Path=/" in header


# --- expiry ----------------------------------------------------------------------


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(clock, "utcnow", fake)
    return fake


def make_session(now: datetime) -> Session:
    return Session(
        user_id=uuid.uuid4(),
        token_hash="0" * 64,
        created_at=now,
        last_seen_at=now,
        expires_at=now + sessions.ABSOLUTE_LIFETIME,
    )


def test_session_idle_timeout(fake_clock: FakeClock) -> None:
    session = make_session(fake_clock.now)

    fake_clock.advance(hours=23, minutes=59)
    assert sessions.is_expired(session) is False
    fake_clock.advance(minutes=1)
    assert sessions.is_expired(session) is True


def test_session_absolute_lifetime(fake_clock: FakeClock) -> None:
    session = make_session(fake_clock.now)

    fake_clock.advance(days=7)
    session.last_seen_at = fake_clock.now  # active a moment ago
    assert sessions.is_expired(session) is True


def test_clock_is_timezone_aware() -> None:
    now = clock.utcnow()
    assert now.tzinfo is not None
    assert abs(now - datetime.now(UTC)) < timedelta(seconds=5)


# --- roles -----------------------------------------------------------------------


def user_with(role: Role) -> User:
    return User(email="x@example.org", display_name="X", role=role, password_hash="-")


def test_check_role_allows_listed_roles() -> None:
    user = user_with(Role.ANALYST)

    assert deps.check_role(user, frozenset({Role.ADMIN, Role.ANALYST})) is user


@pytest.mark.parametrize("role", [Role.VIEWER, Role.ANALYST])
def test_check_role_forbids_other_roles(role: Role) -> None:
    with pytest.raises(HTTPException) as excinfo:
        deps.check_role(user_with(role), frozenset({Role.ADMIN}))

    assert excinfo.value.status_code == 403


async def test_require_role_dependency() -> None:
    admin_only = deps.require_role(Role.ADMIN)
    admin = user_with(Role.ADMIN)

    assert await admin_only(admin) is admin
    with pytest.raises(HTTPException) as excinfo:
        await admin_only(user_with(Role.VIEWER))
    assert excinfo.value.status_code == 403


def test_require_role_needs_a_role() -> None:
    with pytest.raises(ValueError, match="at least one role"):
        deps.require_role()


def test_user_repr_has_no_secrets() -> None:
    user = user_with(Role.ADMIN)
    user.password_hash = "$argon2id$secret-hash"

    assert "secret-hash" not in repr(user)
    assert "x@example.org" not in repr(user)


# --- schemas ---------------------------------------------------------------------


def test_emails_are_normalized() -> None:
    assert normalize_email("  Alice@Example.ORG ") == "alice@example.org"
    body = UserCreate(email=" Bob@Example.org", display_name=" Bob ", role=Role.VIEWER, password="")
    assert body.email == "bob@example.org"
    assert body.display_name == "Bob"


@pytest.mark.parametrize("email", ["no-at-sign", "a@b", "two@@example.org", "sp ace@example.org"])
def test_invalid_emails_are_rejected(email: str) -> None:
    with pytest.raises(ValidationError):
        UserCreate(email=email, display_name="X", role=Role.VIEWER, password="")


def test_user_update_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        UserUpdate.model_validate({"password_hash": "x"})


def test_user_out_never_has_the_password_hash() -> None:
    assert "password_hash" not in UserOut.model_fields


# --- HTTP without services --------------------------------------------------------


async def test_validation_errors_do_not_echo_the_input(
    settings_for_port: Callable[..., Settings], closed_port: int
) -> None:
    app = create_app(settings_for_port(closed_port))
    secret = "my-secret-password-123"

    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client,
    ):
        response = await client.post("/api/v1/auth/login", json={"password": secret})

    assert response.status_code == 422
    assert secret not in response.text
    assert response.json()["detail"][0]["loc"] == ["body", "email"]


async def test_login_returns_503_when_valkey_is_down(
    settings_for_port: Callable[..., Settings], closed_port: int
) -> None:
    app = create_app(settings_for_port(closed_port))

    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client,
    ):
        response = await client.post(
            "/api/v1/auth/login", json={"email": "a@example.org", "password": "x" * 12}
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Sign-in is temporarily unavailable. Try again shortly."}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/auth/me"),
        ("POST", "/api/v1/auth/logout"),
        ("POST", "/api/v1/auth/password"),
        ("GET", "/api/v1/users"),
        ("POST", "/api/v1/users"),
        ("PATCH", f"/api/v1/users/{uuid.uuid4()}"),
        ("DELETE", f"/api/v1/users/{uuid.uuid4()}/sessions"),
    ],
)
async def test_protected_routes_need_a_session(
    settings_for_port: Callable[..., Settings], closed_port: int, method: str, path: str
) -> None:
    app = create_app(settings_for_port(closed_port))

    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client,
    ):
        response = await client.request(method, path, json={})

    # Authentication runs before body validation.
    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated."}


# --- CLI ---------------------------------------------------------------------------


def test_read_password_from_stdin() -> None:
    assert cli.read_password(from_stdin=True, stdin=io.StringIO("s3cret pass\nignored\n")) == (
        "s3cret pass"
    )


def test_read_password_from_empty_stdin_fails() -> None:
    with pytest.raises(cli.CommandError, match="No password"):
        cli.read_password(from_stdin=True, stdin=io.StringIO(""))


def test_read_password_prompts_twice() -> None:
    answers = iter(["first password", "first password"])

    assert (
        cli.read_password(from_stdin=False, stdin=io.StringIO(), prompt=lambda _p: next(answers))
        == "first password"
    )


def test_read_password_mismatch_fails() -> None:
    answers = iter(["first password", "other password"])

    with pytest.raises(cli.CommandError, match="do not match"):
        cli.read_password(from_stdin=False, stdin=io.StringIO(), prompt=lambda _p: next(answers))


def test_cli_rejects_invalid_email_before_connecting(
    capsys: pytest.CaptureFixture[str], settings_for_port: Callable[..., Settings], closed_port: int
) -> None:
    code = cli.main(
        ["create-admin", "--email", "not-an-email", "--display-name", "X", "--password-stdin"],
        stdin=io.StringIO("a long enough password\n"),
        settings=settings_for_port(closed_port),
    )

    assert code == cli.EXIT_ERROR
    assert "Invalid email address" in capsys.readouterr().err


def test_cli_reports_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], settings: Settings
) -> None:
    seen: dict[str, object] = {}

    async def fake_create_admin(_settings: Settings, **kwargs: str) -> str:
        seen.update(kwargs)
        await asyncio.sleep(0)
        return kwargs["email"].lower()

    monkeypatch.setattr(cli, "create_admin", fake_create_admin)

    code = cli.main(
        ["create-admin", "--email", "Root@Example.org", "--display-name", "Root"],
        stdin=io.StringIO(),
        settings=settings,
        prompt=lambda _p: "a long enough password",
    )

    assert code == cli.EXIT_OK
    assert seen == {
        "email": "Root@Example.org",
        "display_name": "Root",
        "password": "a long enough password",
    }
    assert "Created admin root@example.org." in capsys.readouterr().out


def test_cli_requires_a_command() -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main([])
    assert excinfo.value.code == 2
