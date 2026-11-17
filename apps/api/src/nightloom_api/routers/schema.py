"""The entity model as data, so clients never duplicate its rules.

``GET /schema`` describes every entity type (name, sensitivity, what a value
looks like), every relationship type with the (source, target) pairs it
allows, the two Admiralty scales and the defaults for hand-made observations.
Everything is generated from :mod:`nightloom_core`, the single source of
truth that the API itself validates with.
"""

from functools import cache
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from nightloom_api.auth.deps import current_user
from nightloom_api.auth.models import User
from nightloom_core.entities import ENTITY_MODELS, EntityType, Sensitivity
from nightloom_core.provenance import (
    CREDIBILITY_SCALE,
    MANUAL_CONFIDENCE,
    MANUAL_CREDIBILITY,
    MANUAL_RELIABILITY,
    MANUAL_SOURCE,
    NOTE_MAX_LENGTH,
    RELIABILITY_SCALE,
)
from nightloom_core.relationships import ALLOWED, NOTE_REQUIRED, RelationType

router = APIRouter(tags=["schema"])

# The schema only changes with a deployment; let the browser reuse it for a while.
CACHE_CONTROL = "private, max-age=300"


class EntityTypeOut(BaseModel):
    type: EntityType
    # The Neo4j label, e.g. "IPAddress".
    label: str
    # For people, e.g. "IP address".
    display_name: str
    sensitivity: Sensitivity
    hint: str
    example: str


class RelationshipTypeOut(BaseModel):
    type: RelationType
    note_required: bool
    # Allowed (source type, target type) pairs, in entity type order.
    pairs: list[tuple[EntityType, EntityType]]


class Grade(BaseModel):
    code: str
    label: str


class AdmiraltyOut(BaseModel):
    reliability: list[Grade]
    credibility: list[Grade]


class ManualDefaultsOut(BaseModel):
    source: str
    reliability: str
    credibility: int
    admiralty: str
    confidence: float
    note_max_length: int


class SchemaOut(BaseModel):
    entity_types: list[EntityTypeOut]
    relationship_types: list[RelationshipTypeOut]
    admiralty: AdmiraltyOut
    manual_defaults: ManualDefaultsOut


@cache
def build_schema() -> SchemaOut:
    """The schema, built once from nightloom_core."""
    order = {entity_type: index for index, entity_type in enumerate(EntityType)}
    return SchemaOut(
        entity_types=[
            EntityTypeOut(
                type=entity_type,
                label=model.label,
                display_name=model.display_name,
                sensitivity=model.sensitivity,
                hint=model.hint,
                example=model.example,
            )
            for entity_type, model in ENTITY_MODELS.items()
        ],
        relationship_types=[
            RelationshipTypeOut(
                type=relation,
                note_required=relation in NOTE_REQUIRED,
                pairs=sorted(ALLOWED[relation], key=lambda pair: (order[pair[0]], order[pair[1]])),
            )
            for relation in RelationType
        ],
        admiralty=AdmiraltyOut(
            reliability=[Grade(code=code, label=text) for code, text in RELIABILITY_SCALE.items()],
            credibility=[
                Grade(code=str(code), label=text) for code, text in CREDIBILITY_SCALE.items()
            ],
        ),
        manual_defaults=ManualDefaultsOut(
            source=MANUAL_SOURCE,
            reliability=MANUAL_RELIABILITY,
            credibility=MANUAL_CREDIBILITY,
            admiralty=f"{MANUAL_RELIABILITY}{MANUAL_CREDIBILITY}",
            confidence=MANUAL_CONFIDENCE,
            note_max_length=NOTE_MAX_LENGTH,
        ),
    )


@router.get("/schema", response_model=SchemaOut)
async def get_schema(
    _user: Annotated[User, Depends(current_user)], response: Response
) -> SchemaOut:
    """Entity types, relationship rules, Admiralty scales and manual defaults (signed in)."""
    response.headers["Cache-Control"] = CACHE_CONTROL
    return build_schema()


if __name__ == "__main__":
    # Refreshes the web unit tests' copy:
    # uv run python -m nightloom_api.routers.schema > apps/web/src/test/schema.json
    print(build_schema().model_dump_json(indent=2))
