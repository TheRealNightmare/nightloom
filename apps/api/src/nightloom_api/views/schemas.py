"""Request and response bodies of the saved-view routes, the view state, and their limits.

The state is versioned and validated strictly (unknown fields are refused at
every level), so a client can rely on what it reads back. Version 2 adds the
3D view (``mode``, ``camera3d``, ``depth``); version-1 states (2D only) are
still accepted and stored ones stay readable.
"""

import math
import re
import unicodedata
import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    field_validator,
    model_validator,
)

from nightloom_api.layouts.schemas import MAX_BODY_BYTES, MAX_COORDINATE, Positions
from nightloom_core.entities import EntityType, Sensitivity
from nightloom_core.normalize import NormalizationError, parse_key

__all__ = ["MAX_BODY_BYTES"]

STATE_VERSION = 2
NAME_MAX_LENGTH = 80
# At most this many views per case.
MAX_VIEWS_PER_CASE = 50
MAX_EXPANDED_GROUPS = 500
MAX_HIDDEN_SOURCES = 100
SOURCE_MAX_LENGTH = 100
MAX_FOCUS_HOPS = 3
# The camera's scale in graph units per screen pixel.
MIN_CAMERA_RATIO = 1e-6
MAX_CAMERA_RATIO = 1e4
GROUP_ID_PREFIX = "group:"

NAME_CONTROL = "name must not contain control characters."
NOT_A_GROUP = "expanded must hold group ids (group:<entity key>|<entity type>)."
NOT_A_FOCUS_KEY = "focus.key must be a canonical entity key."
NOT_A_DAY = "Dates must be written YYYY-MM-DD."
ZERO_UP = "camera3d.up must not be the zero vector."
CAMERA_AT_TARGET = "camera3d.position must differ from camera3d.target."
# A 3D camera's up vector: each component within [-1, 1].
MIN_UP_LENGTH = 1e-6
ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")

Finite = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Coordinate = Annotated[Finite, Field(ge=-MAX_COORDINATE, le=MAX_COORDINATE)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _no_control_characters(value: str) -> str:
    # Cc: C0/C1 controls (tabs, newlines, escape...); Zl/Zp: line and paragraph separators.
    if any(unicodedata.category(char) in {"Cc", "Zl", "Zp"} for char in value):
        raise ValueError(NAME_CONTROL)
    return value


ViewName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=NAME_MAX_LENGTH),
    AfterValidator(_no_control_characters),
]


class Camera(_Strict):
    """Where the view looks: the graph point at the center of the screen, the scale, the rotation.

    ``x`` and ``y`` are graph coordinates (like pinned positions), ``ratio``
    is graph units per screen pixel and ``angle`` the rotation in radians.
    """

    x: Coordinate
    y: Coordinate
    ratio: Annotated[Finite, Field(ge=MIN_CAMERA_RATIO, le=MAX_CAMERA_RATIO)]
    angle: Annotated[Finite, Field(ge=-math.tau, le=math.tau)]


UpComponent = Annotated[Finite, Field(ge=-1, le=1)]
Vector = Annotated[list[Coordinate], Field(min_length=3, max_length=3)]
UpVector = Annotated[list[UpComponent], Field(min_length=3, max_length=3)]


class Camera3D(_Strict):
    """A 3D view's camera: where it is, what it looks at, which way is up (graph coordinates)."""

    position: Vector
    target: Vector
    up: UpVector

    @model_validator(mode="after")
    def _usable(self) -> "Camera3D":
        if math.hypot(*self.up) < MIN_UP_LENGTH:
            raise ValueError(ZERO_UP)
        if self.position == self.target:
            raise ValueError(CAMERA_AT_TARGET)
        return self


def _iso_day(value: Any) -> Any:
    if isinstance(value, date) or (isinstance(value, str) and ISO_DAY.fullmatch(value)):
        return value
    raise ValueError(NOT_A_DAY)


# A calendar day written YYYY-MM-DD (not a timestamp).
Day = Annotated[date, BeforeValidator(_iso_day)]


class SeenAll(_Strict):
    kind: Literal["all"]


class SeenPreset(_Strict):
    kind: Literal["preset"]
    preset: Literal["24h", "7d", "30d"]


class SeenRange(_Strict):
    """Whole UTC days; either end may be open."""

    kind: Literal["range"]
    from_: Day | None = Field(default=None, alias="from")
    to: Day | None = None


class Focus(_Strict):
    """Show only what is at most ``hops`` relationships away from this entity."""

    key: Annotated[str, Field(min_length=1, max_length=2048)]
    hops: Annotated[int, Field(strict=True, ge=1, le=MAX_FOCUS_HOPS)] = 1

    @field_validator("key")
    @classmethod
    def _canonical(cls, value: str) -> str:
        try:
            parse_key(value)
        except NormalizationError as error:
            raise ValueError(NOT_A_FOCUS_KEY) from error
        return value


SourceName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=SOURCE_MAX_LENGTH),
    AfterValidator(_no_control_characters),
]


class Filters(_Strict):
    """The graph filters, as the web keeps them in the page's query string (see filters.ts)."""

    hidden_types: Annotated[list[EntityType], Field(max_length=len(EntityType))] = []
    # Source groups: "manual" or a Shuttle's name.
    hidden_sources: Annotated[list[SourceName], Field(max_length=MAX_HIDDEN_SOURCES)] = []
    hidden_sensitivities: Annotated[list[Sensitivity], Field(max_length=len(Sensitivity))] = []
    seen: Annotated[SeenAll | SeenPreset | SeenRange, Field(discriminator="kind")] = SeenAll(
        kind="all"
    )
    min_confidence: Annotated[Finite, Field(ge=0, le=1)] = 0.0
    min_reliability: Literal["A", "B", "C", "D", "E", "F"] | None = None
    min_credibility: Annotated[int, Field(strict=True, ge=1, le=6)] | None = None
    focus: Focus | None = None
    hide_isolated: StrictBool = False

    @field_validator("hidden_types", "hidden_sources", "hidden_sensitivities")
    @classmethod
    def _unique(cls, value: list[Any]) -> list[Any]:
        return list(dict.fromkeys(value))


def _group_ids(value: list[str]) -> list[str]:
    for item in value:
        parent, separator, kind = item.removeprefix(GROUP_ID_PREFIX).rpartition("|")
        if not item.startswith(GROUP_ID_PREFIX) or not separator or kind not in EntityType:
            raise ValueError(NOT_A_GROUP)
        try:
            parse_key(parent)
        except NormalizationError as error:
            raise ValueError(NOT_A_GROUP) from error
    return list(dict.fromkeys(value))


class Groups(_Strict):
    expanded: Annotated[
        list[Annotated[str, Field(max_length=2100)]],
        Field(max_length=MAX_EXPANDED_GROUPS),
        AfterValidator(_group_ids),
    ] = []


class ViewStateV1(_Strict):
    """Everything a saved 2D view restores (version 1, still accepted)."""

    version: Literal[1]
    mode: Literal["2d"]
    camera: Camera
    filters: Filters
    groups: Groups
    # Entity key -> [x, y]: the pins the view applies (same rules as personal layouts).
    positions: Positions

    def stored(self) -> dict[str, Any]:
        """The JSON stored in the database (and sent back): aliases, ISO dates."""
        return self.model_dump(mode="json", by_alias=True)


class ViewStateV2(_Strict):
    """Everything a saved 2D or 3D view restores (version 2).

    ``camera`` is always there (for a 3D view, its camera seen from the
    front), so any client can show the view in 2D; ``camera3d`` only for
    views saved in 3D. ``depth`` is what the 3D view's third axis shows.
    """

    version: Literal[2]
    mode: Literal["2d", "3d"]
    camera: Camera
    camera3d: Camera3D | None = None
    depth: Literal["time", "type", "flat"]
    filters: Filters
    groups: Groups
    positions: Positions

    def stored(self) -> dict[str, Any]:
        """The JSON stored in the database (and sent back); no ``camera3d`` when there is none."""
        return self.model_dump(
            mode="json", by_alias=True, exclude={"camera3d"} if self.camera3d is None else None
        )


# Either version, told apart by ``version``.
ViewState = Annotated[ViewStateV1 | ViewStateV2, Field(discriminator="version")]


class ViewCreate(_Strict):
    name: ViewName
    state: ViewState


class ViewUpdate(_Strict):
    """A new name, a new state, or both."""

    name: ViewName | None = None
    state: ViewState | None = None

    @model_validator(mode="after")
    def _something(self) -> "ViewUpdate":
        if self.name is None and self.state is None:
            raise ValueError("Give a new name, a new state, or both.")
        return self


class Author(BaseModel):
    id: uuid.UUID
    display_name: str


class ViewSummary(BaseModel):
    """A view in the list: who saved it and when, without its state."""

    id: uuid.UUID
    name: str
    created_by: Author | None
    updated_by: Author | None
    created_at: datetime
    updated_at: datetime


class ViewOut(ViewSummary):
    """A view with its state; positions of entities the case no longer has are left out."""

    state: dict[str, Any]
