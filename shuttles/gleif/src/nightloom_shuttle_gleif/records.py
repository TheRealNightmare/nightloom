"""Reading a GLEIF LEI record (a JSON:API resource) into what the Shuttle keeps.

``GET https://api.gleif.org/api/v1/lei-records/<lei>`` answers (shortened)::

    {"data": {"type": "lei-records", "id": "<lei>", "attributes": {
        "lei": "<lei>",
        "entity": {
            "legalName": {"name": "EXAMPLE HOLDINGS LIMITED", "language": "en"},
            "otherNames": [{"name": "Example Holdings", "type": "TRADING_OR_OPERATING_NAME"}],
            "legalAddress": {"addressLines": ["..."], "city": "...", "country": "GB"},
            "headquartersAddress": {...},
            "registeredAs": "01234567", "jurisdiction": "GB",
            "category": "GENERAL", "legalForm": {"id": "H0PO", "other": null},
            "status": "ACTIVE"},
        "registration": {"status": "ISSUED", ...}}}}

Only the LEI, the legal and other names, the legal address's **country**,
the jurisdiction, legal form, business register number, the entity's and
the LEI's status, and the category are read: never street addresses,
postal codes or anything else. A record without a valid LEI or legal name
is :class:`InvalidRecordError`; an optional field in an unexpected form is
left out.
"""

import re
from dataclasses import dataclass
from typing import Any

from nightloom_core.normalize import NormalizationError, normalize_text, text_key
from nightloom_shuttle_gleif.client import is_valid_lei

# An LEI registered by a natural person in business on their own account.
SOLE_PROPRIETOR = "SOLE_PROPRIETOR"
# ELF codes that mean "a form not in the ISO 20275 list" and "not known".
ELF_OTHER = frozenset({"8888", "9999"})
MAX_OTHER_NAMES = 20
MAX_TEXT_LENGTH = 200

COUNTRY = re.compile(r"^[A-Z]{2}$")
JURISDICTION = re.compile(r"^[A-Z]{2}(-[A-Z0-9]{1,3})?$")
ELF_CODE = re.compile(r"^[A-Z0-9]{4}$")
STATUS = re.compile(r"^[A-Z][A-Z_]{0,39}$")
# GLEIF's placeholders for "no business register number".
NO_NUMBER = frozenset({"N/A", "NA", "NONE", "-"})


class InvalidRecordError(ValueError):
    """A resource is not a usable LEI record (no valid LEI or legal name)."""


@dataclass(frozen=True)
class LeiRecord:
    """The parts of an LEI record the Shuttle keeps."""

    lei: str
    legal_name: str
    other_names: tuple[str, ...] = ()
    country: str | None = None
    jurisdiction: str | None = None
    legal_form: str | None = None
    registered_as: str | None = None
    entity_status: str | None = None
    lei_status: str | None = None
    category: str | None = None

    @property
    def is_sole_proprietor(self) -> bool:
        """Whether the LEI belongs to a person (a sole proprietor), not an organization."""
        return self.category == SOLE_PROPRIETOR

    def is_named(self, name: str) -> bool:
        """Whether ``name`` is this entity's legal name or one of its other names,
        ignoring case and spacing (Unicode-aware)."""
        try:
            wanted = text_key(normalize_text(name))
        except NormalizationError:
            return False
        return any(text_key(known) == wanted for known in (self.legal_name, *self.other_names))

    def properties(self) -> dict[str, Any]:
        """The record as Organization properties (only those present)."""
        values = {
            "lei": self.lei,
            "legal_name": self.legal_name,
            "jurisdiction": self.jurisdiction,
            "legal_form": self.legal_form,
            "registered_as": self.registered_as,
            "entity_status": self.entity_status,
            "lei_status": self.lei_status,
            "country": self.country,
        }
        return {key: value for key, value in values.items() if value is not None}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str | None:
    """Normalized text of at most :data:`MAX_TEXT_LENGTH` characters, else ``None``."""
    if not isinstance(value, str):
        return None
    try:
        text = normalize_text(value)
    except NormalizationError:
        return None
    return text if len(text) <= MAX_TEXT_LENGTH else None


def _code(value: Any, pattern: re.Pattern[str]) -> str | None:
    if not isinstance(value, str):
        return None
    code = value.strip().upper()
    return code if pattern.fullmatch(code) else None


def _legal_form(value: Any) -> str | None:
    form = _dict(value)
    code = _code(form.get("id"), ELF_CODE)
    if code is not None and code not in ELF_OTHER:
        return code
    return _text(form.get("other"))


def _registered_as(value: Any) -> str | None:
    text = _text(value)
    return None if text is None or text.upper() in NO_NUMBER else text


def _other_names(values: Any, legal_name: str) -> tuple[str, ...]:
    names: list[str] = []
    seen = {text_key(legal_name)}
    for item in values if isinstance(values, list) else []:
        name = _text(_dict(item).get("name"))
        if name is not None and text_key(name) not in seen:
            seen.add(text_key(name))
            names.append(name)
        if len(names) == MAX_OTHER_NAMES:
            break
    return tuple(names)


def parse_record(resource: Any) -> LeiRecord:
    """The :class:`LeiRecord` of a JSON:API ``lei-records`` resource."""
    data = _dict(resource)
    attributes = _dict(data.get("attributes"))
    lei = attributes.get("lei")
    if not isinstance(lei, str) or not is_valid_lei(lei):
        raise InvalidRecordError("the record has no valid LEI")
    resource_id = data.get("id")
    if resource_id is not None and resource_id != lei:
        raise InvalidRecordError("the record's id is not its LEI")
    entity = _dict(attributes.get("entity"))
    legal_name = _text(_dict(entity.get("legalName")).get("name"))
    if legal_name is None:
        raise InvalidRecordError("the record has no usable legal name")
    return LeiRecord(
        lei=lei,
        legal_name=legal_name,
        other_names=_other_names(entity.get("otherNames"), legal_name),
        country=_code(_dict(entity.get("legalAddress")).get("country"), COUNTRY),
        jurisdiction=_code(entity.get("jurisdiction"), JURISDICTION),
        legal_form=_legal_form(entity.get("legalForm")),
        registered_as=_registered_as(entity.get("registeredAs")),
        entity_status=_code(entity.get("status"), STATUS),
        lei_status=_code(_dict(attributes.get("registration")).get("status"), STATUS),
        category=_code(entity.get("category"), STATUS),
    )


__all__ = ["SOLE_PROPRIETOR", "InvalidRecordError", "LeiRecord", "parse_record"]
