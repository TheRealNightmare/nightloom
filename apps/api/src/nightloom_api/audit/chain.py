"""The audit log's hash chain, and the sanitizer every ``details`` value goes through.

Each row's ``hash`` is the SHA-256 of a canonical JSON document holding every
other column of the row, ``prev_hash`` included; ``prev_hash`` is the previous
row's ``hash`` (:data:`GENESIS_HASH` for the first row). Changing, removing or
reordering a row therefore breaks the chain from that row on, which
:mod:`nightloom_api.audit.verify` detects.

Canonical JSON: keys sorted, no whitespace, UTF-8, timestamps as UTC ISO 8601
with microseconds (``2026-11-16T12:00:00.000000Z``), UUIDs and IP addresses in
their standard text form. These functions are pure and cheap; verification runs
them in a worker thread.
"""

import datetime as dt
import hashlib
import ipaddress
import json
import math
import uuid
from collections.abc import Mapping
from typing import Any

GENESIS_HASH = "0" * 64

# Keys whose name contains one of these (case-insensitive) are dropped from
# details, at any depth.
SENSITIVE_KEY_PARTS = ("password", "token", "secret", "cookie", "csrf", "hash")
MAX_STRING_LENGTH = 512
MAX_DETAILS_BYTES = 8 * 1024
MAX_DEPTH = 8
TRUNCATION_MARK = "…"

JsonValue = None | bool | int | str | list["JsonValue"] | dict[str, "JsonValue"]


# --- canonical form and hash ------------------------------------------------------


def canonical_json(value: Any) -> bytes:
    """Deterministic JSON bytes: sorted keys, no whitespace, UTF-8, no NaN/Infinity."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def format_timestamp(value: dt.datetime) -> str:
    """UTC ISO 8601 with microseconds and a ``Z`` suffix. Naive datetimes are refused."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("audit timestamps must be timezone-aware")
    return value.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _uuid_text(value: uuid.UUID | str | None) -> str | None:
    return None if value is None else str(uuid.UUID(str(value)))


def ip_text(value: object) -> str | None:
    if value is None:
        return None
    # PostgreSQL's inet may come back as "192.0.2.1/32" or an ipaddress object.
    if isinstance(value, ipaddress.IPv4Interface | ipaddress.IPv6Interface):
        return str(value.ip)
    return str(ipaddress.ip_address(str(value).split("/", 1)[0]))


def chain_payload(
    *,
    id: int,
    occurred_at: dt.datetime,
    actor_user_id: uuid.UUID | str | None,
    actor_label: str | None,
    actor_ip: object,
    request_id: str,
    action: str,
    outcome: str,
    target_type: str | None,
    target_id: str | None,
    case_id: uuid.UUID | str | None,
    details: Mapping[str, Any],
    prev_hash: str,
) -> dict[str, Any]:
    """Every hashed column of a row, normalized to its canonical JSON value."""
    return {
        "id": int(id),
        "occurred_at": format_timestamp(occurred_at),
        "actor_user_id": _uuid_text(actor_user_id),
        "actor_label": actor_label,
        "actor_ip": ip_text(actor_ip),
        "request_id": request_id,
        "action": str(action),
        "outcome": str(outcome),
        "target_type": target_type,
        "target_id": target_id,
        "case_id": _uuid_text(case_id),
        "details": dict(details),
        "prev_hash": prev_hash,
    }


def compute_hash(payload: Mapping[str, Any]) -> str:
    """Hex SHA-256 of the canonical JSON of ``payload`` (from :func:`chain_payload`)."""
    return hashlib.sha256(canonical_json(payload)).hexdigest()


# --- details sanitizer ------------------------------------------------------------


def is_sensitive_key(key: str) -> bool:
    """Whether a details key looks like it names a secret."""
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def _clean_string(value: str) -> str:
    # PostgreSQL's jsonb rejects NUL; lone surrogates cannot be encoded as UTF-8.
    value = value.replace("\x00", "").encode("utf-8", "replace").decode("utf-8")
    if len(value) > MAX_STRING_LENGTH:
        value = value[: MAX_STRING_LENGTH - len(TRUNCATION_MARK)] + TRUNCATION_MARK
    return value


def _clean(value: object, depth: int) -> JsonValue:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, str):
        return _clean_string(str(value))
    if isinstance(value, float):
        # Stored as text: jsonb may rewrite a float's form (1e100 comes back as
        # an integer), which would change the hash on verification.
        return repr(value) if math.isfinite(value) else None
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dt.datetime):
        return format_timestamp(value) if value.tzinfo is not None else value.isoformat()
    if isinstance(value, ipaddress.IPv4Address | ipaddress.IPv6Address):
        return str(value)
    if depth >= MAX_DEPTH:
        return "<too deep>"
    if isinstance(value, Mapping):
        return _clean_mapping(value, depth + 1)
    if isinstance(value, list | tuple):
        return [_clean(item, depth + 1) for item in value]
    # Never str() an arbitrary object: its text could include anything.
    return f"<{type(value).__name__}>"


def _clean_mapping(value: Mapping[Any, Any], depth: int) -> dict[str, JsonValue]:
    cleaned: dict[str, JsonValue] = {}
    for raw_key, item in value.items():
        key = str(raw_key)
        if is_sensitive_key(key):
            continue
        cleaned[_clean_string(key)] = _clean(item, depth)
    return cleaned


def sanitize_details(details: Mapping[str, Any] | None) -> dict[str, JsonValue]:
    """Make ``details`` safe and small enough to store.

    Drops keys that name secrets (password, token, secret, cookie, csrf, hash;
    case-insensitive, at any depth), cuts strings to :data:`MAX_STRING_LENGTH`
    characters, turns non-JSON values into text, and replaces the whole value
    with a marker if its JSON exceeds :data:`MAX_DETAILS_BYTES`. Callers pass
    the few fields that matter, never a whole request body.
    """
    if not details:
        return {}
    cleaned = _clean_mapping(details, 0)
    size = len(canonical_json(cleaned))
    if size > MAX_DETAILS_BYTES:
        return {"_truncated": True, "_original_bytes": size}
    return cleaned
