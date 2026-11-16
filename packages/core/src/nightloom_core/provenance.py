"""Where a fact came from: one :class:`Observation` per sighting of an entity or relationship.

The Admiralty (NATO) system rates a sighting on two independent scales:
the reliability of the *source* (A completely reliable … E unreliable,
F cannot be judged) and the credibility of the *information* (1 confirmed by
other sources … 5 improbable, 6 cannot be judged).
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator

Reliability = Literal["A", "B", "C", "D", "E", "F"]

# "manual" (typed in by an analyst) or "shuttle:<name>@<version>".
SOURCE_PATTERN = r"^(manual|shuttle:[a-z0-9][a-z0-9_.-]{0,63}@[0-9A-Za-z][0-9A-Za-z.+-]{0,31})$"
MANUAL_SOURCE = "manual"
NOTE_MAX_LENGTH = 500
EVIDENCE_REF_MAX_LENGTH = 512

# Defaults for entities and relationships an analyst adds by hand. The analyst
# is a usually reliable source (B) for their own case, but a hand-made entry is
# not independently confirmed, so it is "probably true" (2) rather than
# "confirmed" (1), with a matching confidence. Collectors that later observe
# the same entity add their own observations; ``max_confidence`` keeps the best.
MANUAL_RELIABILITY: Reliability = "B"
MANUAL_CREDIBILITY = 2
MANUAL_CONFIDENCE = 0.8


class Observation(BaseModel):
    """One sighting of an entity or relationship."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: Annotated[str, StringConstraints(pattern=SOURCE_PATTERN)]
    observed_at: AwareDatetime
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    admiralty_reliability: Reliability
    admiralty_credibility: int = Field(ge=1, le=6)
    evidence_ref: (
        Annotated[
            str,
            StringConstraints(
                strip_whitespace=True, min_length=1, max_length=EVIDENCE_REF_MAX_LENGTH
            ),
        ]
        | None
    ) = None
    note: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=NOTE_MAX_LENGTH)] | None
    ) = None

    @field_validator("note")
    @classmethod
    def _blank_note(cls, value: str | None) -> str | None:
        return value or None

    @property
    def admiralty(self) -> str:
        """The combined rating, e.g. ``"B2"``."""
        return f"{self.admiralty_reliability}{self.admiralty_credibility}"


def manual_observation(observed_at: datetime, note: str | None = None) -> Observation:
    """An observation for something an analyst added by hand (see the ``MANUAL_*`` defaults)."""
    return Observation(
        source=MANUAL_SOURCE,
        observed_at=observed_at,
        confidence=MANUAL_CONFIDENCE,
        admiralty_reliability=MANUAL_RELIABILITY,
        admiralty_credibility=MANUAL_CREDIBILITY,
        note=note,
    )


__all__ = [
    "MANUAL_CONFIDENCE",
    "MANUAL_CREDIBILITY",
    "MANUAL_RELIABILITY",
    "MANUAL_SOURCE",
    "Observation",
    "Reliability",
    "manual_observation",
]
