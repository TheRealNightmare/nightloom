"""The only code that reads an RDAP answer's contacts (its ``entities`` and their vCards).

RDAP answers name people: registrants, administrative and technical
contacts, abuse mailboxes, with names, email addresses, phone numbers and
postal addresses in jCard (vCard as JSON, RFC 7095). This Shuttle is not a
person Shuttle, so :func:`extract_contacts` keeps **only an organization's
display name** (the vCard ``fn``) for the roles the Shuttle uses, plus the
registrar's IANA ID (a public identifier, not a vCard field). Everything
else in a contact is never read into a finding, a note or a log:

- no names of individuals: a registrant or network owner is kept only when
  its vCard says ``kind: org``; a registrar (always a company) unless its
  vCard says ``kind: individual``;
- no email addresses, phone numbers, postal addresses or any other vCard
  field, including abuse and role mailboxes;
- no value that looks redacted (:data:`REDACTION_MARKERS`, case-insensitive,
  shared with other Shuttles in :mod:`nightloom_sdk.redaction`), or that
  looks like an address or a number rather than a name.

What was skipped is counted by :class:`SkipReason`, never kept.
"""

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from nightloom_sdk.redaction import REDACTION_MARKERS, is_redacted

# The roles whose organization this Shuttle uses (RFC 9083, section 10.2.4).
REGISTRAR = "registrar"
REGISTRANT = "registrant"
USED_ROLES = frozenset({REGISTRAR, REGISTRANT})

# Contacts looked at per answer; registries nest a few, never hundreds.
MAX_CONTACTS = 50
MAX_DEPTH = 3
NAME_MAX_LENGTH = 200

# Something that is not an organization's name: an email address, a URL, or
# a value made mostly of digits and phone punctuation.
_NOT_A_NAME = re.compile(r"@|https?://|^[\d\s()+.\-/]+$", re.IGNORECASE)


class SkipReason(StrEnum):
    """Why a contact was not used. Only these counts are reported."""

    NOT_AN_ORGANIZATION = "not_an_organization"
    REDACTED = "redacted"
    NO_NAME = "no_name"
    UNUSED_ROLE = "unused_role"


SKIP_LABELS: dict[SkipReason, str] = {
    SkipReason.NOT_AN_ORGANIZATION: "not an organization",
    SkipReason.REDACTED: "redacted",
    SkipReason.NO_NAME: "without a usable name",
    SkipReason.UNUSED_ROLE: "other roles (not used)",
}


@dataclass
class Contacts:
    """What :func:`extract_contacts` kept: organization names by role, and the skip counts."""

    organizations: dict[str, str] = field(default_factory=dict)
    registrar_iana_id: int | None = None
    skipped: Counter[SkipReason] = field(default_factory=Counter)


def _roles(entity: Mapping[str, Any]) -> set[str]:
    roles = entity.get("roles")
    if not isinstance(roles, list):
        return set()
    return {role.strip().lower() for role in roles if isinstance(role, str)}


def _vcard_value(entity: Mapping[str, Any], name: str) -> str | None:
    """The first text value of the vCard property ``name`` (only ``kind`` and ``fn`` are read)."""
    vcard = entity.get("vcardArray")
    if not (isinstance(vcard, list) and len(vcard) == 2 and isinstance(vcard[1], list)):
        return None
    for item in vcard[1]:
        if (
            isinstance(item, list)
            and len(item) >= 4
            and isinstance(item[0], str)
            and item[0].lower() == name
            and isinstance(item[3], str)
        ):
            return item[3]
    return None


def _organization_name(entity: Mapping[str, Any], role: str) -> str | SkipReason:
    kind = (_vcard_value(entity, "kind") or "").strip().lower()
    if role == REGISTRAR:
        # A registrar is an accredited company; only an explicit "individual" is refused.
        if kind == "individual":
            return SkipReason.NOT_AN_ORGANIZATION
    elif kind != "org":
        return SkipReason.NOT_AN_ORGANIZATION
    name = " ".join((_vcard_value(entity, "fn") or "").split())
    if not name or len(name) > NAME_MAX_LENGTH or _NOT_A_NAME.search(name):
        return SkipReason.NO_NAME
    if is_redacted(name):
        return SkipReason.REDACTED
    return name


def _iana_registrar_id(entity: Mapping[str, Any]) -> int | None:
    public_ids = entity.get("publicIds")
    if not isinstance(public_ids, list):
        return None
    for public_id in public_ids:
        if not isinstance(public_id, Mapping):
            continue
        kind, identifier = public_id.get("type"), public_id.get("identifier")
        if (
            isinstance(kind, str)
            and kind.strip().lower() == "iana registrar id"
            and isinstance(identifier, str | int)
        ):
            text = str(identifier).strip()
            if text.isdigit() and 0 < int(text) < 2**31:
                return int(text)
    return None


def _walk(entities: object, depth: int = 0) -> Iterable[tuple[Mapping[str, Any], int]]:
    if not isinstance(entities, list) or depth >= MAX_DEPTH:
        return
    for entity in entities:
        if isinstance(entity, Mapping):
            yield entity, depth
            yield from _walk(entity.get("entities"), depth + 1)


def extract_contacts(rdap_object: Mapping[str, Any]) -> Contacts:
    """The organization names (by role) in ``rdap_object``'s contacts, and what was skipped.

    Only top-level contacts with the role ``registrar`` or ``registrant``
    are used, the first usable one per role. Nested contacts (a registrar's
    abuse desk) and every other role are counted as unused.
    """
    contacts = Contacts()
    for count, (entity, depth) in enumerate(_walk(rdap_object.get("entities"))):
        if count >= MAX_CONTACTS:
            break
        roles = _roles(entity) & USED_ROLES if depth == 0 else set()
        if not roles:
            contacts.skipped[SkipReason.UNUSED_ROLE] += 1
            continue
        for role in sorted(roles):
            kept = _organization_name(entity, role)
            if isinstance(kept, SkipReason):
                contacts.skipped[kept] += 1
                continue
            contacts.organizations.setdefault(role, kept)
            if role == REGISTRAR and contacts.registrar_iana_id is None:
                contacts.registrar_iana_id = _iana_registrar_id(entity)
    return contacts


__all__ = [
    "REDACTION_MARKERS",
    "REGISTRANT",
    "REGISTRAR",
    "SKIP_LABELS",
    "Contacts",
    "SkipReason",
    "extract_contacts",
    "is_redacted",
]
