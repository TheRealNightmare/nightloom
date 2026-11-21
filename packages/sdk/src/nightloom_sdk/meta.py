"""What a Shuttle declares about itself: :class:`ShuttleMeta`.

The metadata is checked when the Shuttle class is defined and again when the
registry loads it. The runner (and the API, before it queues a job) relies on
it: a Shuttle may only emit the entity and relationship types it declares,
``person`` Shuttles only run in cases that allow person entities, and
``active`` Shuttles are refused until ownership verification exists.
"""

import re
from enum import StrEnum
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    model_validator,
)

from nightloom_core.entities import EntityType, Sensitivity, sensitivity_of
from nightloom_core.provenance import SOURCE_PATTERN
from nightloom_core.relationships import ALLOWED, RelationType

# A lowercase slug: letters, digits and single hyphens, starting with a letter.
NAME_PATTERN = r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$"
NAME_MAX_LENGTH = 63
# Semantic Versioning 2.0.0 (https://semver.org), without leading zeros.
SEMVER_PATTERN = (
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)
VERSION_MAX_LENGTH = 32
DEFAULT_TIMEOUT_SECONDS = 60
MAX_TIMEOUT_SECONDS = 600
# Results of a run may be reused for at most this long (see ``cache_ttl_seconds``).
MAX_CACHE_TTL_SECONDS = 30 * 24 * 3600
# An Admiralty rating: source reliability A-F and information credibility 1-6.
ADMIRALTY_PATTERN = r"^[A-F][1-6]$"
KEY_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"

_SENSITIVITY_ORDER = {
    Sensitivity.INFRASTRUCTURE: 0,
    Sensitivity.ORGANIZATION: 1,
    Sensitivity.PERSON: 2,
}


class Mode(StrEnum):
    """How a Shuttle collects.

    ``passive`` Shuttles only ask third parties (or compute locally);
    ``active`` ones touch the target itself and need verified ownership.
    """

    PASSIVE = "passive"
    ACTIVE = "active"


class RateLimit(BaseModel):
    """At most ``requests`` requests per ``period_seconds``, per destination host."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    requests: int = Field(ge=1, le=10_000)
    period_seconds: float = Field(gt=0, le=86_400, allow_inf_nan=False)

    @property
    def per_second(self) -> float:
        return self.requests / self.period_seconds


Admiralty = Annotated[str, StringConstraints(pattern=ADMIRALTY_PATTERN)]

_HTTP_URL: TypeAdapter[AnyHttpUrl] = TypeAdapter(AnyHttpUrl)


def _http_url(value: str) -> str:
    """An http(s) URL, kept as the text the Shuttle author wrote."""
    _HTTP_URL.validate_python(value)
    return value


HttpUrlText = Annotated[str, StringConstraints(max_length=2048), AfterValidator(_http_url)]

USAGE_NOTE_MAX_LENGTH = 300
# Control characters (line breaks included) and markup brackets.
_NOT_PLAIN_TEXT = re.compile(r"[\x00-\x1f\x7f-\x9f<>]")


def _plain_text(value: str) -> str:
    """One line of plain text: no control characters, line breaks or markup brackets."""
    if _NOT_PLAIN_TEXT.search(value):
        raise ValueError("must be one line of plain text (no control characters, < or >).")
    return value


UsageNote = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=USAGE_NOTE_MAX_LENGTH),
    AfterValidator(_plain_text),
]


class ShuttleMeta(BaseModel):
    """A Shuttle's declaration. Frozen, and validated as a whole.

    Rules beyond the field types:

    - every output relationship type connects at least one pair of the
      Shuttle's input and output entity types under the core allow-list;
    - ``sensitivity`` is at least that of every input and output entity type
      (so any person-related type makes it a ``person`` Shuttle);
    - a Shuttle that uses the network declares a ``rate_limit``.

    ``cache_ttl_seconds`` is how long the results of a successful run on an
    entity may be reused for the same entity instead of running again (0, the
    default: never). The cache is keyed by the Shuttle's name **and version**,
    so a new version never reuses an older one's results, and a Shuttle's
    output must depend only on the input entity's type and value. ``active``
    Shuttles are never cached: what they observe is the target itself, now.

    ``usage_note`` is one line of plain text (at most
    :data:`USAGE_NOTE_MAX_LENGTH` characters) stating conditions the operator
    must respect when using the source, such as a non-commercial licence. The
    API exposes it and the web UI shows it before a run, next to ``terms_url``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Annotated[
        str, StringConstraints(pattern=NAME_PATTERN, min_length=1, max_length=NAME_MAX_LENGTH)
    ]
    version: Annotated[
        str, StringConstraints(pattern=SEMVER_PATTERN, max_length=VERSION_MAX_LENGTH)
    ]
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=100)]
    description: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=10, max_length=2000)
    ]
    input_types: frozenset[EntityType] = Field(min_length=1)
    output_entity_types: frozenset[EntityType] = frozenset()
    output_relationship_types: frozenset[RelationType] = frozenset()
    mode: Mode = Mode.PASSIVE
    sensitivity: Sensitivity
    network: bool
    required_keys: frozenset[Annotated[str, StringConstraints(pattern=KEY_NAME_PATTERN)]] = (
        frozenset()
    )
    rate_limit: RateLimit | None = None
    timeout_seconds: int = Field(default=DEFAULT_TIMEOUT_SECONDS, ge=1, le=MAX_TIMEOUT_SECONDS)
    cache_ttl_seconds: int = Field(default=0, ge=0, le=MAX_CACHE_TTL_SECONDS)
    terms_url: HttpUrlText | None = None
    homepage: HttpUrlText | None = None
    # Conditions an operator must respect when using the source (e.g. "free for
    # non-commercial use only"), shown next to the terms link before a run.
    usage_note: UsageNote | None = None
    # Provenance defaults for every finding; a finding may override them.
    default_admiralty: Admiralty = "F6"
    default_confidence: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not self.output_entity_types and not self.output_relationship_types:
            raise ValueError("A Shuttle must declare at least one output type.")
        if not re.fullmatch(SOURCE_PATTERN, self.source):
            raise ValueError("name and version must form a valid provenance source.")
        endpoints = self.input_types | self.output_entity_types
        for relation in sorted(self.output_relationship_types):
            if not any(
                source in endpoints and target in endpoints for source, target in ALLOWED[relation]
            ):
                raise ValueError(
                    f"{relation.value} cannot connect any of the Shuttle's input and output "
                    "entity types."
                )
        needed = max(
            (sensitivity_of(entity_type) for entity_type in endpoints),
            key=_SENSITIVITY_ORDER.__getitem__,
        )
        if _SENSITIVITY_ORDER[self.sensitivity] < _SENSITIVITY_ORDER[needed]:
            raise ValueError(
                f"sensitivity must be at least {needed.value}: the Shuttle handles "
                f"{needed.value}-related entity types."
            )
        if self.network and self.rate_limit is None:
            raise ValueError("A Shuttle that uses the network must declare a rate_limit.")
        if self.mode == Mode.ACTIVE and self.cache_ttl_seconds > 0:
            raise ValueError("An active Shuttle cannot be cached: cache_ttl_seconds must be 0.")
        return self

    @property
    def source(self) -> str:
        """The provenance source of its observations, ``shuttle:<name>@<version>``."""
        return f"shuttle:{self.name}@{self.version}"

    @property
    def reliability(self) -> str:
        return self.default_admiralty[0]

    @property
    def credibility(self) -> int:
        return int(self.default_admiralty[1])

    def accepts(self, entity_type: EntityType) -> bool:
        return EntityType(entity_type) in self.input_types


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_CACHE_TTL_SECONDS",
    "MAX_TIMEOUT_SECONDS",
    "USAGE_NOTE_MAX_LENGTH",
    "Admiralty",
    "Mode",
    "RateLimit",
    "ShuttleMeta",
]
