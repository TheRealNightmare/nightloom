"""Reading a Have I Been Pwned breach into what the Shuttles keep.

HIBP describes a breach as (API v3, shortened)::

    {"Name": "Adobe", "Title": "Adobe", "Domain": "adobe.com",
     "BreachDate": "2013-10-04", "AddedDate": "2013-12-04T00:00:00Z",
     "ModifiedDate": "...", "PwnCount": 152445165,
     "Description": "<p>In October 2013, ...</p>", "LogoPath": "...",
     "DataClasses": ["Email addresses", "Password hints", "Passwords", "Usernames"],
     "IsVerified": true, "IsFabricated": false, "IsSensitive": false,
     "IsRetired": false, "IsSpamList": false, "IsMalware": false,
     "IsSubscriptionFree": false, "IsStealerLog": false}

Kept: the name (the Breach's value: HIBP's unique, stable name), title,
site domain, breach date, the date HIBP added it, the account count, the
kinds of data and the verified, fabricated, spam-list and malware flags.
Never kept: the description (HIBP's own prose, with links) and the logo.
``IsSensitive`` and ``IsRetired`` are read only to leave such breaches out
where that matters. A breach without a usable name is
:class:`InvalidBreachError`; an optional field in an unexpected form is
left out.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from nightloom_core.entities import EntityType
from nightloom_core.normalize import NormalizationError, normalize, normalize_text

MAX_DATA_CLASSES = 50
MAX_TEXT_LENGTH = 200


class InvalidBreachError(ValueError):
    """An item is not a usable HIBP breach (no usable name)."""


@dataclass(frozen=True)
class HibpBreach:
    """The parts of an HIBP breach the Shuttles keep (and the flags they act on)."""

    name: str
    title: str | None = None
    domain: str | None = None
    breach_date: date | None = None
    added_at: datetime | None = None
    record_count: int | None = None
    data_classes: tuple[str, ...] = ()
    verified: bool | None = None
    fabricated: bool | None = None
    spam_list: bool | None = None
    malware: bool | None = None
    sensitive: bool = False
    retired: bool = False

    def properties(self) -> dict[str, Any]:
        """The breach as Breach properties (only those present)."""
        values: dict[str, Any] = {
            "title": self.title,
            "breach_date": self.breach_date,
            "added_at": self.added_at,
            "record_count": self.record_count,
            "data_classes": list(self.data_classes) or None,
            "verified": self.verified,
            "fabricated": self.fabricated,
            "spam_list": self.spam_list,
            "malware": self.malware,
        }
        return {key: value for key, value in values.items() if value is not None}


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        text = normalize_text(value)
    except NormalizationError:
        return None
    return text if len(text) <= MAX_TEXT_LENGTH else None


def _flag(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


def _count(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _domain(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return normalize(EntityType.DOMAIN, value).value
    except NormalizationError:
        return None


def _data_classes(values: Any) -> tuple[str, ...]:
    kept: list[str] = []
    for value in values if isinstance(values, list) else []:
        text = _text(value)
        if text is not None and text not in kept:
            kept.append(text)
        if len(kept) == MAX_DATA_CLASSES:
            break
    return tuple(kept)


def parse_breach(item: Any) -> HibpBreach:
    """The :class:`HibpBreach` of one item of an HIBP breach list."""
    if not isinstance(item, dict):
        raise InvalidBreachError("the breach is not an object")
    name = _text(item.get("Name"))
    if name is None:
        raise InvalidBreachError("the breach has no usable name")
    return HibpBreach(
        name=name,
        title=_text(item.get("Title")),
        domain=_domain(item.get("Domain")),
        breach_date=_date(item.get("BreachDate")),
        added_at=_datetime(item.get("AddedDate")),
        record_count=_count(item.get("PwnCount")),
        data_classes=_data_classes(item.get("DataClasses")),
        verified=_flag(item.get("IsVerified")),
        fabricated=_flag(item.get("IsFabricated")),
        spam_list=_flag(item.get("IsSpamList")),
        malware=_flag(item.get("IsMalware")),
        sensitive=item.get("IsSensitive") is True,
        retired=item.get("IsRetired") is True,
    )


__all__ = ["HibpBreach", "InvalidBreachError", "parse_breach"]
