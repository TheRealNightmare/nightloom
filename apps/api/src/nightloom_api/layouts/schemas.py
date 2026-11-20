"""Request and response bodies of the personal layout routes, and their limits."""

from datetime import datetime
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field

from nightloom_core.normalize import NormalizationError, parse_key

# At most this many pinned entities per layout.
MAX_POSITIONS = 10_000
# Coordinates are graph units; anything beyond this is a bug, not a layout.
MAX_COORDINATE = 1e6
# The request body is refused (413) above this many bytes.
MAX_BODY_BYTES = 1024 * 1024

Coordinate = Annotated[
    float, Field(strict=True, allow_inf_nan=False, ge=-MAX_COORDINATE, le=MAX_COORDINATE)
]
Position = Annotated[list[Coordinate], Field(min_length=2, max_length=2)]

TOO_MANY = f"positions may hold at most {MAX_POSITIONS:,} entries."
NOT_A_KEY = "positions must be keyed by canonical entity keys."


def not_too_many_positions(value: Any) -> Any:
    """Refuses a map of more than MAX_POSITIONS entries (before any key is parsed)."""
    if isinstance(value, dict) and len(value) > MAX_POSITIONS:
        raise ValueError(TOO_MANY)
    return value


def canonical_position_keys(value: dict[str, list[float]]) -> dict[str, list[float]]:
    """Refuses keys that are not canonical entity keys, without echoing them."""
    for key in value:
        try:
            parse_key(key)
        except NormalizationError as error:
            # The key itself is not echoed: it may be person-related.
            raise ValueError(NOT_A_KEY) from error
    return value


# Entity key -> [x, y]: what a layout, and a saved view, stores.
Positions = Annotated[
    dict[str, Position],
    BeforeValidator(not_too_many_positions),
    AfterValidator(canonical_position_keys),
]


class LayoutIn(BaseModel):
    """The caller's pins in the case, replacing what was stored."""

    model_config = ConfigDict(extra="forbid")

    positions: Positions


class LayoutOut(BaseModel):
    """The caller's pins: only entities the case still has."""

    positions: dict[str, tuple[float, float]]
    updated_at: datetime | None
