"""What an export says about each entity and relationship, and how person data is redacted.

By default an export redacts every entity whose type is person-related
(``sensitivity: person``; see ETHICS.md, "PII redaction on export"):

- its ``type`` and ``sensitivity`` are kept, so the shape of the case survives;
- its ``value`` becomes :data:`REDACTED_VALUE`;
- its ``key`` becomes ``redacted:<HMAC>``: HMAC-SHA256 of the real key under a
  random secret drawn for this one export and never stored or logged. The same
  entity therefore gets the same pseudonymous key everywhere in the file, so
  relationships still connect, but the key cannot be reversed or matched
  against another export (each export draws a new secret);
- its properties, note and evidence references are dropped (they can describe
  the person), and so are the note and evidence references of every
  relationship touching it.

With ``include_person_data`` (case owners only, with a recorded reason)
nothing is redacted.
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field, replace
from typing import Any

from nightloom_api.graph.repository import EntityRecord, Provenance, RelationshipRecord
from nightloom_core.entities import EntityType, Sensitivity
from nightloom_core.relationships import RelationType

REDACTED_VALUE = "[redacted]"
REDACTED_KEY_PREFIX = "redacted:"
# Hex characters of the HMAC kept in a redacted key (128 bits).
REDACTED_KEY_HEX_LENGTH = 32
SECRET_BYTES = 32


@dataclass(frozen=True)
class ExportEntity:
    key: str
    type: EntityType
    value: str
    sensitivity: Sensitivity
    provenance: Provenance
    properties: dict[str, Any] = field(default_factory=dict)
    redacted: bool = False


@dataclass(frozen=True)
class ExportRelationship:
    type: RelationType
    source_key: str
    target_key: str
    provenance: Provenance

    @property
    def id(self) -> str:
        return f"{self.source_key}|{self.type.value}|{self.target_key}"


def _without_free_text(provenance: Provenance) -> Provenance:
    return replace(provenance, note=None, evidence_refs=[])


class Redactor:
    """Turns graph records into what one export contains."""

    def __init__(self, *, include_person_data: bool, secret: bytes | None = None) -> None:
        self.include_person_data = include_person_data
        # Drawn per export and only ever kept in memory.
        self._secret = secret if secret is not None else secrets.token_bytes(SECRET_BYTES)

    def redacts(self, sensitivity: Sensitivity | None) -> bool:
        """Whether an entity of this sensitivity is redacted (unknown counts as personal)."""
        return not self.include_person_data and sensitivity in (Sensitivity.PERSON, None)

    def key(self, key: str, sensitivity: Sensitivity | None) -> str:
        if not self.redacts(sensitivity):
            return key
        digest = hmac.new(self._secret, key.encode(), hashlib.sha256).hexdigest()
        return REDACTED_KEY_PREFIX + digest[:REDACTED_KEY_HEX_LENGTH]

    def entity(self, record: EntityRecord) -> ExportEntity:
        if not self.redacts(record.sensitivity):
            return ExportEntity(
                key=record.key,
                type=record.type,
                value=record.value,
                sensitivity=record.sensitivity,
                provenance=record.provenance,
                properties=dict(record.properties),
            )
        return ExportEntity(
            key=self.key(record.key, record.sensitivity),
            type=record.type,
            value=REDACTED_VALUE,
            sensitivity=record.sensitivity,
            provenance=_without_free_text(record.provenance),
            redacted=True,
        )

    def relationship(self, record: RelationshipRecord) -> ExportRelationship:
        source_redacted = self.redacts(record.source_sensitivity)
        target_redacted = self.redacts(record.target_sensitivity)
        provenance = record.provenance
        if source_redacted or target_redacted:
            provenance = _without_free_text(provenance)
        return ExportRelationship(
            type=record.type,
            source_key=self.key(record.source_key, record.source_sensitivity),
            target_key=self.key(record.target_key, record.target_sensitivity),
            provenance=provenance,
        )
