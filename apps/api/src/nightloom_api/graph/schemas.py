"""Request and response bodies for a case's graph."""

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from nightloom_api.graph.repository import (
    EntityDetail,
    EntityRecord,
    GraphSnapshot,
    Provenance,
    RelationshipRecord,
)
from nightloom_core.entities import EntityType, Sensitivity
from nightloom_core.normalize import MAX_INPUT_LENGTH, MAX_KEY_LENGTH
from nightloom_core.provenance import NOTE_MAX_LENGTH
from nightloom_core.relationships import RelationType

Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=NOTE_MAX_LENGTH)]
Key = Annotated[str, StringConstraints(min_length=1, max_length=MAX_KEY_LENGTH)]
# Fields an entity's properties may have at most (every model has far fewer).
MAX_PROPERTY_FIELDS = 32


def _blank_to_none(value: str | None) -> str | None:
    return value or None


class EntityCreate(BaseModel):
    """Body of ``POST /cases/{id}/entities``."""

    model_config = ConfigDict(extra="forbid")

    type: EntityType
    value: Annotated[str, StringConstraints(min_length=1, max_length=MAX_INPUT_LENGTH)]
    properties: dict[str, Any] | None = Field(default=None, max_length=MAX_PROPERTY_FIELDS)
    note: Note | None = None
    # Default region (ISO 3166 alpha-2) for a phone number in national format;
    # without it, phone numbers must be in international format (+...).
    phone_region: Annotated[str, StringConstraints(pattern=r"^[A-Za-z]{2}$")] | None = None

    _note = field_validator("note")(_blank_to_none)


class RelationshipCreate(BaseModel):
    """Body of ``POST /cases/{id}/relationships``."""

    model_config = ConfigDict(extra="forbid")

    type: RelationType
    source_key: Key
    target_key: Key
    note: Note | None = None

    _note = field_validator("note")(_blank_to_none)


class ProvenanceOut(BaseModel):
    first_seen: datetime
    last_seen: datetime
    observation_count: int
    sources: list[str]
    max_confidence: float
    # Admiralty rating of the most recent observation, e.g. "B2".
    admiralty: str | None
    evidence_refs: list[str]
    note: str | None

    @classmethod
    def build(cls, provenance: Provenance) -> "ProvenanceOut":
        return cls(**provenance.__dict__)


class EntityOut(BaseModel):
    key: str
    type: EntityType
    value: str
    sensitivity: Sensitivity
    properties: dict[str, Any]
    provenance: ProvenanceOut

    @classmethod
    def build(cls, record: EntityRecord) -> "EntityOut":
        return cls(
            key=record.key,
            type=record.type,
            value=record.value,
            sensitivity=record.sensitivity,
            properties=record.properties,
            provenance=ProvenanceOut.build(record.provenance),
        )


class EntityDetailOut(EntityOut):
    neighbours: int

    @classmethod
    def from_detail(cls, detail: EntityDetail) -> "EntityDetailOut":
        return cls(**EntityOut.build(detail.entity).model_dump(), neighbours=detail.neighbours)


class RelationshipOut(BaseModel):
    id: str
    type: RelationType
    source_key: str
    target_key: str
    provenance: ProvenanceOut

    @classmethod
    def build(cls, record: RelationshipRecord) -> "RelationshipOut":
        return cls(
            id=record.id,
            type=record.type,
            source_key=record.source_key,
            target_key=record.target_key,
            provenance=ProvenanceOut.build(record.provenance),
        )


class GraphOut(BaseModel):
    """Nodes ordered by key; ``truncated`` when the node or edge limit cut something off."""

    nodes: list[EntityOut]
    edges: list[RelationshipOut]
    truncated: bool

    @classmethod
    def build(cls, snapshot: GraphSnapshot) -> "GraphOut":
        return cls(
            nodes=[EntityOut.build(node) for node in snapshot.nodes],
            edges=[RelationshipOut.build(edge) for edge in snapshot.edges],
            truncated=snapshot.truncated,
        )
