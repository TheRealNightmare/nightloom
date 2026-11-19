"""The parts of RIPEstat's answers the Shuttle reads, as Pydantic models.

Every data call answers with an :class:`Envelope` whose ``data`` the
Shuttle validates with the model of that call. Unknown fields are ignored
(RIPEstat adds fields over time). A ``data`` object of the wrong shape
fails as a whole; the items of a list (origin ASNs, announced prefixes) are
validated one by one, so a malformed item is counted and skipped instead.
"""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class Envelope(_Model):
    """What every data call answers: ``status`` is ``ok`` when ``data`` is usable."""

    status: str
    status_code: int | None = None
    data: dict[str, Any] | None = None


class Block(_Model):
    """The IANA registry block a resource falls in (``1.0.0.0/8``, ``APNIC (Status: ...)``)."""

    resource: str | None = None
    desc: str | None = None
    name: str | None = None


class NetworkInfo(_Model):
    """``network-info``: the most specific announced prefix covering an address."""

    # Strings ("13335"); an empty list and prefix "" when nothing announces it.
    asns: list[Any] = Field(default_factory=list)
    prefix: str | None = None


class AsOverview(_Model):
    """``as-overview``: an AS number's holder, whether it is announced, its IANA block."""

    resource: str | None = None
    holder: str | None = None
    announced: bool | None = None
    block: Block | None = None


class PrefixOrigin(_Model):
    """One origin AS in ``prefix-overview``."""

    asn: int = Field(ge=1, le=2**32 - 1)
    holder: str | None = None


class PrefixOverview(_Model):
    """``prefix-overview``: whether a prefix is announced, and by which ASes.

    When the prefix asked about is not announced but a less specific one
    covering it is, ``is_less_specific`` is true and ``resource`` is that
    covering prefix.
    """

    resource: str
    is_less_specific: bool = False
    announced: bool = False
    asns: list[Any] = Field(default_factory=list)
    block: Block | None = None


class Timeline(_Model):
    """A period in which a prefix was seen announced. RIPEstat's times are UTC without a zone."""

    starttime: datetime | None = None
    endtime: datetime | None = None

    @field_validator("starttime", "endtime")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class AnnouncedPrefix(_Model):
    """One prefix in ``announced-prefixes``."""

    prefix: str
    timelines: list[Timeline] = Field(default_factory=list)


class AnnouncedPrefixes(_Model):
    """``announced-prefixes``: the prefixes an AS originated in the period asked about."""

    prefixes: list[Any] = Field(default_factory=list)


__all__ = [
    "AnnouncedPrefix",
    "AnnouncedPrefixes",
    "AsOverview",
    "Block",
    "Envelope",
    "NetworkInfo",
    "PrefixOrigin",
    "PrefixOverview",
    "Timeline",
]
