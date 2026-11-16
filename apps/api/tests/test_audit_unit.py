"""Audit log building blocks that need no services: hashing, sanitizing, request IDs."""

import datetime as dt
import hashlib
import ipaddress
import json
import logging
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import OperationalError
from starlette.types import Message, Receive, Scope, Send

from nightloom_api.audit import chain, verify
from nightloom_api.audit import service as audit
from nightloom_api.audit.actions import AuditAction, AuditOutcome
from nightloom_api.auth import service as auth_service
from nightloom_api.auth.models import Role, User
from nightloom_api.request_id import (
    NO_REQUEST_ID,
    REQUEST_ID_HEADER,
    RequestIdLogFilter,
    RequestIdMiddleware,
    current_request_id,
    is_valid_request_id,
)
from nightloom_api.routers.auth import failure_reason

UTC_NOON = dt.datetime(2026, 11, 16, 12, 0, 0, 123456, tzinfo=dt.UTC)
USER_ID = uuid.UUID("12345678-1234-5678-1234-567812345678")

# --- canonical JSON ---------------------------------------------------------------


def test_canonical_json_is_independent_of_key_order() -> None:
    first = {"b": 1, "a": {"y": [1, 2], "x": None}}
    second = {"a": {"x": None, "y": [1, 2]}, "b": 1}

    assert chain.canonical_json(first) == chain.canonical_json(second)
    assert chain.canonical_json(first) == b'{"a":{"x":null,"y":[1,2]},"b":1}'


def test_canonical_json_is_utf8_without_escapes() -> None:
    assert chain.canonical_json({"name": "Nahar Ākāś"}) == '{"name":"Nahar Ākāś"}'.encode()


def test_canonical_json_rejects_nan() -> None:
    with pytest.raises(ValueError, match="not JSON compliant"):
        chain.canonical_json({"x": float("nan")})


def test_timestamps_are_utc_with_microseconds() -> None:
    plus_six = dt.timezone(dt.timedelta(hours=6))

    assert chain.format_timestamp(UTC_NOON) == "2026-11-16T12:00:00.123456Z"
    assert chain.format_timestamp(UTC_NOON.astimezone(plus_six)) == "2026-11-16T12:00:00.123456Z"
    assert chain.format_timestamp(UTC_NOON.replace(microsecond=0)).endswith(":00.000000Z")
    with pytest.raises(ValueError, match="timezone-aware"):
        chain.format_timestamp(UTC_NOON.replace(tzinfo=None))


# --- hash ---------------------------------------------------------------------------


def payload(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": 1,
        "occurred_at": UTC_NOON,
        "actor_user_id": USER_ID,
        "actor_label": "admin@example.org",
        "actor_ip": "192.0.2.1",
        "request_id": "req-12345678",
        "action": AuditAction.AUTH_LOGIN,
        "outcome": AuditOutcome.SUCCESS,
        "target_type": "user",
        "target_id": str(USER_ID),
        "case_id": None,
        "details": {"b": 2, "a": 1},
        "prev_hash": chain.GENESIS_HASH,
    }
    values.update(overrides)
    return chain.chain_payload(**values)


def test_hash_is_sha256_of_the_documented_canonical_form() -> None:
    expected_json = (
        '{"action":"auth.login","actor_ip":"192.0.2.1","actor_label":"admin@example.org",'
        f'"actor_user_id":"{USER_ID}","case_id":null,"details":{{"a":1,"b":2}},"id":1,'
        '"occurred_at":"2026-11-16T12:00:00.123456Z","outcome":"success",'
        f'"prev_hash":"{"0" * 64}","request_id":"req-12345678","target_id":"{USER_ID}",'
        '"target_type":"user"}'
    )

    assert chain.compute_hash(payload()) == hashlib.sha256(expected_json.encode()).hexdigest()


def test_hash_does_not_depend_on_value_representation() -> None:
    # What the database hands back on verification, rather than what was inserted.
    as_read = payload(
        actor_user_id=str(USER_ID).upper(),
        actor_ip=ipaddress.IPv4Interface("192.0.2.1/32"),
        occurred_at=UTC_NOON.astimezone(dt.timezone(dt.timedelta(hours=-5))),
        details={"a": 1, "b": 2},
    )

    assert chain.compute_hash(as_read) == chain.compute_hash(payload())


@pytest.mark.parametrize(
    "change",
    [
        {"id": 2},
        {"details": {"a": 1, "b": 3}},
        {"prev_hash": "f" * 64},
        {"actor_ip": "192.0.2.2"},
        {"outcome": AuditOutcome.FAILURE},
        {"case_id": uuid.uuid4()},
        {"occurred_at": UTC_NOON + dt.timedelta(microseconds=1)},
    ],
)
def test_every_column_is_covered_by_the_hash(change: dict[str, Any]) -> None:
    assert chain.compute_hash(payload(**change)) != chain.compute_hash(payload())


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("2001:DB8::1", "2001:db8::1"),
        ("192.0.2.7/32", "192.0.2.7"),
        (ipaddress.ip_address("192.0.2.8"), "192.0.2.8"),
        (ipaddress.IPv6Interface("2001:db8::2/128"), "2001:db8::2"),
    ],
)
def test_ip_text(value: object, expected: str | None) -> None:
    assert chain.ip_text(value) == expected


# --- details sanitizer --------------------------------------------------------------


def test_sanitizer_drops_secret_keys_at_any_depth() -> None:
    details = {
        "Password": "hunter2",
        "role": "viewer",
        "nested": {
            "csrf_token": "x",
            "SessionCookie": "y",
            "client_secret": "z",
            "password_hash": "$argon2id$...",
            "kept": {"api_TOKEN": "t", "fine": 1},
        },
        "items": [{"new_password": "p", "name": "a"}, "plain"],
    }

    cleaned = chain.sanitize_details(details)

    assert cleaned == {
        "role": "viewer",
        "nested": {"kept": {"fine": 1}},
        "items": [{"name": "a"}, "plain"],
    }
    text = json.dumps(cleaned)
    for secret in ("hunter2", "argon2", '"t"', '"p"'):
        assert secret not in text


def test_sanitizer_truncates_long_strings_and_keys() -> None:
    cleaned = chain.sanitize_details({"long": "x" * 1000, "k" * 600: "v"})

    assert len(cleaned["long"]) == chain.MAX_STRING_LENGTH  # type: ignore[arg-type]
    assert str(cleaned["long"]).endswith(chain.TRUNCATION_MARK)
    assert ("k" * 511 + chain.TRUNCATION_MARK) in cleaned


def test_sanitizer_caps_the_total_size() -> None:
    details = {f"field{i}": "y" * 500 for i in range(40)}

    cleaned = chain.sanitize_details(details)

    assert cleaned["_truncated"] is True
    assert int(cleaned["_original_bytes"]) > chain.MAX_DETAILS_BYTES  # type: ignore[arg-type]
    assert len(chain.canonical_json(cleaned)) < chain.MAX_DETAILS_BYTES


def test_sanitizer_keeps_details_just_under_the_cap() -> None:
    details = {"a": "y" * 500, "b": "z" * 500}

    assert chain.sanitize_details(details) == details


class Opaque:
    def __str__(self) -> str:
        return "secret-repr"


def test_sanitizer_converts_values_to_safe_json() -> None:
    when = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    details: dict[Any, Any] = {
        "flag": True,
        "count": 3,
        "ratio": 0.5,
        "huge": 1e100,
        "nan": float("nan"),
        "id": USER_ID,
        "when": when,
        "naive": when.replace(tzinfo=None),
        "ip": ipaddress.ip_address("192.0.2.1"),
        "nul": "a\x00b",
        "surrogate": "a\ud800b",
        "tuple": (1, "two"),
        "object": Opaque(),
        "bytes": b"raw",
        "none": None,
        7: "int key",
    }

    cleaned = chain.sanitize_details(details)

    assert cleaned == {
        "flag": True,
        "count": 3,
        "ratio": "0.5",
        "huge": "1e+100",
        "nan": None,
        "id": str(USER_ID),
        "when": "2026-01-01T00:00:00.000000Z",
        "naive": "2026-01-01T00:00:00",
        "ip": "192.0.2.1",
        "nul": "ab",
        "surrogate": "a?b",
        "tuple": [1, "two"],
        "object": "<Opaque>",
        "bytes": "<bytes>",
        "none": None,
        "7": "int key",
    }


def test_sanitizer_limits_depth() -> None:
    deep: dict[str, Any] = {"leaf": 1}
    for _ in range(chain.MAX_DEPTH + 2):
        deep = {"d": deep}

    text = json.dumps(chain.sanitize_details(deep))

    assert "<too deep>" in text
    assert "leaf" not in text


@pytest.mark.parametrize("details", [None, {}])
def test_sanitizer_empty(details: dict[str, Any] | None) -> None:
    assert chain.sanitize_details(details) == {}


# --- verification of a batch (pure part) ------------------------------------------


def make_rows(count: int) -> list[SimpleNamespace]:
    rows = []
    prev = chain.GENESIS_HASH
    for index in range(1, count + 1):
        values: dict[str, Any] = {
            "id": index,
            "occurred_at": UTC_NOON,
            "actor_user_id": None,
            "actor_label": None,
            "actor_ip": None,
            "request_id": f"request-{index:04d}",
            "action": "auth.login",
            "outcome": "failure",
            "target_type": None,
            "target_id": None,
            "case_id": None,
            "details": {"n": index},
            "prev_hash": prev,
        }
        values["hash"] = chain.compute_hash(chain.chain_payload(**values))
        prev = values["hash"]
        rows.append(SimpleNamespace(**values))
    return rows


def test_check_batch_accepts_a_valid_chain() -> None:
    rows = make_rows(3)

    result = verify.check_batch(rows, chain.GENESIS_HASH)  # type: ignore[arg-type]

    assert (result.checked, result.first_bad_id, result.last_hash) == (3, None, rows[-1].hash)


def test_check_batch_reports_edited_contents() -> None:
    rows = make_rows(3)
    rows[1].details = {"n": 99}

    result = verify.check_batch(rows, chain.GENESIS_HASH)  # type: ignore[arg-type]

    assert (result.first_bad_id, result.reason) == (2, verify.REASON_HASH)


def test_check_batch_reports_a_broken_link() -> None:
    rows = make_rows(3)
    del rows[1]

    result = verify.check_batch(rows, chain.GENESIS_HASH)  # type: ignore[arg-type]

    assert (result.first_bad_id, result.reason) == (3, verify.REASON_PREV_HASH)


# --- audit service helpers ----------------------------------------------------------


def test_actor_constructors() -> None:
    user = User(id=USER_ID, email="a@example.org", display_name="A", role=Role.ADMIN)

    assert audit.Actor.for_user(user, "192.0.2.1") == audit.Actor(
        USER_ID, "a@example.org", "192.0.2.1"
    )
    assert audit.Actor.anonymous("192.0.2.2") == audit.Actor(None, None, "192.0.2.2")
    assert audit.Actor.system("cli") == audit.Actor(None, "system:cli", None)


def test_email_digest_normalizes_first() -> None:
    expected = hashlib.sha256(b"admin@example.org").hexdigest()

    assert audit.email_digest("  Admin@Example.ORG ") == expected


async def test_record_rejects_unknown_actions_and_outcomes() -> None:
    db: Any = object()  # never reached
    actor = audit.Actor.anonymous(None)

    with pytest.raises(ValueError, match="not a valid AuditAction"):
        await audit.record(db, action="case.nuke", outcome="success", actor=actor)
    with pytest.raises(ValueError, match="not a valid AuditOutcome"):
        await audit.record(db, action="auth.login", outcome="maybe", actor=actor)


async def test_record_standalone_logs_and_swallows_database_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class BrokenSession:
        async def __aenter__(self) -> None:
            raise OperationalError("SELECT 1", {}, Exception("password=hunter2 refused"))

        async def __aexit__(self, *args: object) -> None:
            return None

    caplog.set_level(logging.ERROR)
    result = await audit.record_standalone(
        lambda: BrokenSession(),  # type: ignore[arg-type]
        action=AuditAction.AUTHZ_DENIED,
        outcome=AuditOutcome.DENIED,
        actor=audit.Actor.anonymous(None),
    )

    assert result is None
    assert "Could not record audit event authz.denied: OperationalError" in caplog.text
    assert "hunter2" not in caplog.text


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (auth_service.InvalidCredentialsError("unknown_email"), "unknown_email"),
        (auth_service.WrongPasswordError(), "wrong_current_password"),
        (auth_service.PasswordPolicyError("too short"), "password_policy"),
        (auth_service.EmailTakenError(), "email_taken"),
        (auth_service.ConflictError("no"), "conflict"),
        (auth_service.UserNotFoundError(), "not_found"),
        (auth_service.AuthUnavailableError(), "error"),
    ],
)
def test_failure_reasons_are_fixed_strings(error: Exception, reason: str) -> None:
    assert failure_reason(error) == reason  # type: ignore[arg-type]


# --- request IDs --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("abcd1234", True),
        ("A.b_c-D9" * 8, True),  # 64 characters
        (str(uuid.uuid4()), True),
        ("abc1234", False),  # 7 characters
        ("a" * 65, False),
        ("abcd 1234", False),
        ("abcd1234\n", False),
        ("abcd/1234", False),
        ("ünïcödé-id", False),
        ("", False),
        (None, False),
    ],
)
def test_request_id_validation(value: str | None, valid: bool) -> None:
    assert is_valid_request_id(value) is valid


@pytest.fixture
async def echo_client() -> AsyncIterator[AsyncClient]:
    """An app whose one route returns the request ID it sees and logs a line."""
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/echo")
    async def echo() -> dict[str, str | None]:
        logging.getLogger("echo").warning("handling")
        return {"seen": current_request_id()}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        yield client


async def test_valid_incoming_request_id_is_kept(echo_client: AsyncClient) -> None:
    response = await echo_client.get("/echo", headers={REQUEST_ID_HEADER: "client-id-0001"})

    assert response.headers[REQUEST_ID_HEADER] == "client-id-0001"
    assert response.json() == {"seen": "client-id-0001"}


@pytest.mark.parametrize("incoming", [None, "short", "has spaces in it", "x" * 65])
async def test_invalid_or_missing_request_id_is_replaced(
    echo_client: AsyncClient, incoming: str | None
) -> None:
    headers = {REQUEST_ID_HEADER: incoming} if incoming else {}

    response = await echo_client.get("/echo", headers=headers)

    generated = response.headers[REQUEST_ID_HEADER]
    assert generated != incoming
    assert uuid.UUID(generated).version == 4
    assert response.json() == {"seen": generated}


async def test_request_id_is_only_set_during_the_request(echo_client: AsyncClient) -> None:
    await echo_client.get("/echo")

    assert current_request_id() is None


async def test_log_records_carry_the_request_id(
    echo_client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.handler.addFilter(RequestIdLogFilter())

    await echo_client.get("/echo", headers={REQUEST_ID_HEADER: "log-test-0001"})
    logging.getLogger("outside").warning("no request")

    by_name = {record.name: record for record in caplog.records}
    assert by_name["echo"].request_id == "log-test-0001"  # type: ignore[attr-defined]
    assert by_name["outside"].request_id == NO_REQUEST_ID  # type: ignore[attr-defined]


async def test_non_http_scopes_pass_through() -> None:
    seen: list[str] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope["type"])
        assert current_request_id() is None

    async def receive() -> Message:
        return {"type": "lifespan.startup"}

    async def send(_message: Message) -> None:
        return None

    await RequestIdMiddleware(inner)({"type": "lifespan"}, receive, send)

    assert seen == ["lifespan"]


async def test_api_responses_carry_a_request_id(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health", headers={REQUEST_ID_HEADER: "health-check-1"})

    assert response.headers[REQUEST_ID_HEADER] == "health-check-1"
