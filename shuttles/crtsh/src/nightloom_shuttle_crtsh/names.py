"""Which names from certificates the crt.sh Shuttle keeps.

Certificates name far more than the domain that was searched for: other
customers' names on shared certificates, email addresses (S/MIME and old
certificates put them in the subject alternative names), IP addresses and
junk. :func:`classify` keeps a name only if, after stripping a leading
``*.`` (a wildcard) and normalizing it with ``nightloom_core``, it is the
searched domain itself or a name under it. Everything else is dropped, with
a :class:`DropReason` so the run can report how many it dropped and why.

Email addresses are dropped *before* anything else is done with them: they
are person data, and this is an infrastructure Shuttle.
"""

import ipaddress
from dataclasses import dataclass
from enum import StrEnum

from nightloom_core.normalize import NormalizationError, normalize_domain

WILDCARD_PREFIX = "*."
# Longer raw names are refused without looking further (a domain is at most
# 253 characters; some slack for a wildcard, a trailing dot and whitespace).
MAX_RAW_NAME_LENGTH = 300


class DropReason(StrEnum):
    """Why a name from a certificate was not kept."""

    OUT_OF_SCOPE = "out_of_scope"
    EMAIL = "email"
    IP_ADDRESS = "ip_address"
    INVALID = "invalid"


DROP_LABELS: dict[DropReason, str] = {
    DropReason.OUT_OF_SCOPE: "outside the searched domain",
    DropReason.EMAIL: "email addresses",
    DropReason.IP_ADDRESS: "IP addresses",
    DropReason.INVALID: "not valid domain names",
}


@dataclass(frozen=True)
class Name:
    """A kept name: normalized, without its wildcard label, and whether it had one."""

    value: str
    wildcard: bool


def _is_ip(text: str) -> bool:
    candidate = text.strip("[]").split("%", 1)[0]
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return True


def in_scope(name: str, scope: str) -> bool:
    """Whether the normalized ``name`` is ``scope`` or a name under it."""
    return name == scope or name.endswith(f".{scope}")


def classify(raw: str, scope: str) -> Name | DropReason:
    """Keep ``raw`` as a :class:`Name` under ``scope`` (a normalized domain), or say why not."""
    text = raw.strip()
    if "@" in text:
        return DropReason.EMAIL
    if not text or len(text) > MAX_RAW_NAME_LENGTH:
        return DropReason.INVALID
    if _is_ip(text):
        return DropReason.IP_ADDRESS
    wildcard = text.startswith(WILDCARD_PREFIX)
    if wildcard:
        text = text[len(WILDCARD_PREFIX) :]
    try:
        name = normalize_domain(text.lower())
    except NormalizationError:
        return DropReason.INVALID
    if "*" in name:  # a wildcard anywhere else is not a name
        return DropReason.INVALID
    if not in_scope(name, scope):
        return DropReason.OUT_OF_SCOPE
    return Name(value=name, wildcard=wildcard)


__all__ = [
    "DROP_LABELS",
    "MAX_RAW_NAME_LENGTH",
    "WILDCARD_PREFIX",
    "DropReason",
    "Name",
    "classify",
    "in_scope",
]
