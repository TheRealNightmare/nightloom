"""Reading RDAP objects (RFC 9083): everything except contacts.

Contacts (``entities``) are read only by :mod:`nightloom_shuttle_rdap.privacy`.
These helpers read the registration's dates, statuses, DNSSEC flag, name
servers, the link to the registrar's own RDAP server, and an IP network's
extent as CIDR blocks. Every field is optional and checked; a field of the
wrong shape is ignored, never an error.
"""

import ipaddress
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from nightloom_core.entities import RDAP_MAX_STATUSES

Network = ipaddress.IPv4Network | ipaddress.IPv6Network

# At most this many CIDR blocks for one network; a range that needs more is capped.
MAX_CIDRS = 16
MAX_NAMESERVERS = 20
TEXT_MAX_LENGTH = 200
HANDLE_MAX_LENGTH = 100

# RDAP event actions (RFC 9083, section 10.2.3) → property names.
EVENTS: dict[str, str] = {
    "registration": "registered",
    "expiration": "expires",
    "last changed": "last_changed",
}


def text(value: object, limit: int = TEXT_MAX_LENGTH) -> str | None:
    """Trimmed text with whitespace collapsed, or ``None`` if empty, too long or not text."""
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned if 0 < len(cleaned) <= limit else None


def parse_date(value: object) -> datetime | None:
    """An RDAP event date (RFC 3339) as an aware UTC datetime; naive ones are taken as UTC."""
    if not isinstance(value, str):
        return None
    try:
        when = datetime.fromisoformat(value.strip().replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        return None
    return when.replace(tzinfo=UTC) if when.tzinfo is None else when.astimezone(UTC)


def events(rdap_object: Mapping[str, Any]) -> dict[str, datetime]:
    """``registered``, ``expires`` and ``last_changed`` from the object's events."""
    found: dict[str, datetime] = {}
    items = rdap_object.get("events")
    if not isinstance(items, list):
        return found
    for event in items:
        if not isinstance(event, Mapping):
            continue
        action = event.get("eventAction")
        name = EVENTS.get(action.strip().lower()) if isinstance(action, str) else None
        when = parse_date(event.get("eventDate"))
        if name and when and name not in found:
            found[name] = when
    return found


def statuses(rdap_object: Mapping[str, Any]) -> list[str]:
    """The object's status values (``active``, ``client transfer prohibited``, ...)."""
    items = rdap_object.get("status")
    if not isinstance(items, list):
        return []
    kept: list[str] = []
    for item in items:
        value = text(item)
        if value and value.lower() not in kept:
            kept.append(value.lower())
    return kept[:RDAP_MAX_STATUSES]


def dnssec_signed(rdap_object: Mapping[str, Any]) -> bool | None:
    secure = rdap_object.get("secureDNS")
    if isinstance(secure, Mapping) and isinstance(secure.get("delegationSigned"), bool):
        return bool(secure["delegationSigned"])
    return None


def nameservers(rdap_object: Mapping[str, Any]) -> list[str]:
    """The name servers' host names (``ldhName``), lower-cased, deduplicated."""
    items = rdap_object.get("nameservers")
    if not isinstance(items, list):
        return []
    names: list[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        name = text(item.get("ldhName"), 255)
        if name:
            name = name.lower().rstrip(".")
            if name and name not in names:
                names.append(name)
    return names[:MAX_NAMESERVERS]


def related_link(rdap_object: Mapping[str, Any], current_url: str) -> str | None:
    """The first ``related`` RDAP link to another server (the registrar's), if any.

    Registries of "thin" TLDs such as .com and .net point to the registrar's
    RDAP server this way (RFC 9083, section 4.2; ICANN's RDAP profile).
    """
    links = rdap_object.get("links")
    if not isinstance(links, list):
        return None
    current = urlsplit(current_url)
    for link in links:
        if not isinstance(link, Mapping):
            continue
        rel, href, kind = link.get("rel"), link.get("href"), link.get("type")
        if rel != "related" or not isinstance(href, str):
            continue
        if isinstance(kind, str) and "rdap+json" not in kind.lower():
            continue
        target = urlsplit(href.strip())
        if target.scheme not in {"http", "https"} or not target.hostname:
            continue
        if (target.hostname.lower(), target.path) == (
            (current.hostname or "").lower(),
            current.path,
        ):
            continue
        return href.strip()
    return None


def _cidr0(rdap_object: Mapping[str, Any]) -> list[Network]:
    """The ``cidr0_cidrs`` extension: ``[{"v4prefix": "192.0.2.0", "length": 24}, ...]``."""
    items = rdap_object.get("cidr0_cidrs")
    if not isinstance(items, list):
        return []
    found: list[Network] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        prefix = item.get("v4prefix") or item.get("v6prefix")
        length = item.get("length")
        if not isinstance(prefix, str) or not isinstance(length, int) or isinstance(length, bool):
            continue
        try:
            found.append(ipaddress.ip_network(f"{prefix.strip()}/{length}", strict=False))
        except ValueError:
            continue
    return found


def range_to_cidrs(start: str, end: str) -> list[Network]:
    """The smallest list of CIDR blocks covering ``start`` to ``end`` (empty if invalid)."""
    try:
        first, last = ipaddress.ip_address(start.strip()), ipaddress.ip_address(end.strip())
    except ValueError:
        return []
    if isinstance(first, ipaddress.IPv4Address) and isinstance(last, ipaddress.IPv4Address):
        return list(ipaddress.summarize_address_range(first, last)) if first <= last else []
    if isinstance(first, ipaddress.IPv6Address) and isinstance(last, ipaddress.IPv6Address):
        return list(ipaddress.summarize_address_range(first, last)) if first <= last else []
    return []


def networks(rdap_object: Mapping[str, Any]) -> tuple[list[Network], bool]:
    """An ``ip network`` object's CIDR blocks (at most :data:`MAX_CIDRS`) and whether capped.

    The ``cidr0`` extension when the server sends it, else the
    ``startAddress`` to ``endAddress`` range converted to the minimal CIDR list.
    """
    found = _cidr0(rdap_object)
    if not found:
        start, end = rdap_object.get("startAddress"), rdap_object.get("endAddress")
        if isinstance(start, str) and isinstance(end, str):
            found = range_to_cidrs(start, end)
    unique = list(dict.fromkeys(found))
    return unique[:MAX_CIDRS], len(unique) > MAX_CIDRS


def country(rdap_object: Mapping[str, Any]) -> str | None:
    value = text(rdap_object.get("country"), 2)
    return value.upper() if value and len(value) == 2 and value.isalpha() else None


def object_class(rdap_object: Mapping[str, Any]) -> str | None:
    value = rdap_object.get("objectClassName")
    return value.strip().lower() if isinstance(value, str) else None


__all__ = [
    "MAX_CIDRS",
    "MAX_NAMESERVERS",
    "country",
    "dnssec_signed",
    "events",
    "nameservers",
    "networks",
    "object_class",
    "parse_date",
    "range_to_cidrs",
    "related_link",
    "statuses",
    "text",
]
