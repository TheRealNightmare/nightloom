"""What a Shuttle yields: entity and relationship findings.

Findings carry raw values. Shuttles do not normalize: the runner normalizes
every value with ``nightloom_core``, checks it against the Shuttle's declared
output types, the case's person gate and the relationship allow-list, and
only then writes it to the graph. A finding that fails a check is counted as
rejected and never stored.
"""

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from nightloom_core.entities import EntityType
from nightloom_core.provenance import NOTE_MAX_LENGTH
from nightloom_core.relationships import RelationType
from nightloom_sdk.meta import Admiralty

# Longer raw values are refused by every normalizer anyway.
MAX_VALUE_LENGTH = 2048

RawValue = Annotated[str, StringConstraints(min_length=1, max_length=MAX_VALUE_LENGTH)]
Confidence = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=NOTE_MAX_LENGTH)]


class EntityRef(BaseModel):
    """An entity named by its type and (raw) value, e.g. a relationship's endpoint."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: EntityType
    value: RawValue


class EntityFinding(BaseModel):
    """An entity the Shuttle found.

    ``confidence`` and ``admiralty`` override the Shuttle's defaults for this
    finding only. ``properties`` are validated against the entity type's
    property model by the runner.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: EntityType
    value: RawValue
    properties: dict[str, Any] | None = None
    confidence: Confidence | None = None
    admiralty: Admiralty | None = None
    note: Note | None = None

    @property
    def ref(self) -> EntityRef:
        return EntityRef(type=self.type, value=self.value)


class RelationshipFinding(BaseModel):
    """A relationship between two entities. Both must exist in the case when it is stored.

    Yield the endpoints as :class:`EntityFinding` first (unless one is the
    input entity), so they exist by the time the relationship arrives.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: RelationType
    source: EntityRef
    target: EntityRef
    confidence: Confidence | None = None
    note: Note | None = None


Finding = EntityFinding | RelationshipFinding

__all__ = ["MAX_VALUE_LENGTH", "EntityFinding", "EntityRef", "Finding", "RelationshipFinding"]
