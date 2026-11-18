"""The Shuttle base class and the input it receives."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from nightloom_core.entities import EntityType
from nightloom_sdk.findings import EntityRef, Finding
from nightloom_sdk.meta import ShuttleMeta

if TYPE_CHECKING:
    from nightloom_sdk.context import ShuttleContext


class RefusalReason(StrEnum):
    """Why a Shuttle refuses an input. Each has one fixed, user-safe message."""

    INTERNAL_NAME = "internal_name"
    NOT_REGISTRABLE = "not_registrable"


REFUSAL_MESSAGES: dict[RefusalReason, str] = {
    RefusalReason.INTERNAL_NAME: (
        "This Shuttle does not look up internal-only names (single-label names or names "
        "under reserved or private suffixes such as .local, .internal or .test). "
        "Nothing was queried."
    ),
    RefusalReason.NOT_REGISTRABLE: (
        "This Shuttle only looks up domains under a public suffix (such as example.org or "
        "shop.example.co.uk), not public suffixes themselves (such as co.uk) or internal-only "
        "names. Nothing was queried."
    ),
}


class FailureReason(StrEnum):
    """Why a Shuttle gave up during a run. Each has one fixed, user-safe message."""

    SOURCE_UNAVAILABLE = "source_unavailable"
    SOURCE_INVALID_RESPONSE = "source_invalid_response"
    RESULT_TOO_LARGE = "result_too_large"


FAILURE_MESSAGES: dict[FailureReason, str] = {
    FailureReason.SOURCE_UNAVAILABLE: (
        "The data source did not answer after several tries (it may be overloaded or down). "
        "Try again later. Findings stored before the failure were kept."
    ),
    FailureReason.SOURCE_INVALID_RESPONSE: (
        "The data source sent an answer this Shuttle could not read. "
        "Findings stored before the failure were kept."
    ),
    FailureReason.RESULT_TOO_LARGE: (
        "The data source's answer is too large to process, even after narrowing the query. "
        "Run the Shuttle on a narrower input instead (for a domain: one of its subdomains)."
    ),
}


class InputRefusedError(Exception):
    """Raised by :meth:`Shuttle.run` (before doing any work) for an input it will not handle.

    The job fails with the fixed message for ``reason`` (never the input's
    value or other text), and ``reason`` is recorded in the audit log.
    """

    def __init__(self, reason: RefusalReason) -> None:
        self.reason = RefusalReason(reason)
        super().__init__(REFUSAL_MESSAGES[self.reason])

    @property
    def message(self) -> str:
        return REFUSAL_MESSAGES[self.reason]


class ShuttleFailedError(Exception):
    """Raised by :meth:`Shuttle.run` when it cannot finish for a known, explainable reason.

    The job fails with the fixed message for ``reason`` instead of the generic
    one; ``reason`` is recorded in the audit log. Findings already yielded are
    kept. Unexpected errors should still just raise: they end the job with the
    generic message.
    """

    def __init__(self, reason: FailureReason) -> None:
        self.reason = FailureReason(reason)
        super().__init__(FAILURE_MESSAGES[self.reason])

    @property
    def message(self) -> str:
        return FAILURE_MESSAGES[self.reason]


class ShuttleConfigurationError(Exception):
    """A Shuttle's configuration (environment variables) is invalid.

    The message is shown to operators: name the setting and say how to fix it,
    never print secrets.
    """


class EntityInput(BaseModel):
    """The entity a Shuttle runs on, as stored in the case: canonical value and key."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: EntityType
    value: str
    key: str
    properties: dict[str, Any] = Field(default_factory=dict)

    @property
    def ref(self) -> EntityRef:
        return EntityRef(type=self.type, value=self.value)


class Shuttle(ABC):
    """Base class of every Shuttle.

    Subclasses set ``meta`` and implement :meth:`run` as an async generator::

        class Example(Shuttle):
            meta = ShuttleMeta(...)

            async def run(self, ctx, entity):
                yield EntityFinding(type=EntityType.DOMAIN, value="example.org")

    A Shuttle is instantiated once per job and must not keep state between
    jobs. It never writes to the graph itself; it yields findings.
    """

    meta: ClassVar[ShuttleMeta]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        meta = cls.__dict__.get("meta")
        if meta is not None and not isinstance(meta, ShuttleMeta):
            raise TypeError(f"{cls.__name__}.meta must be a ShuttleMeta instance")

    @classmethod  # noqa: B027 - an optional hook, not an abstract method
    def check_configuration(cls) -> None:
        """Check the Shuttle's own configuration; raise :class:`ShuttleConfigurationError`.

        The worker calls this for every installed Shuttle when it starts and
        refuses to start if one fails, so a misconfiguration is reported once,
        clearly, instead of failing every job. The default checks nothing.
        """

    @abstractmethod
    def run(self, ctx: "ShuttleContext", entity: EntityInput) -> AsyncIterator[Finding]:
        """Yield findings for ``entity``. Implement as ``async def`` with ``yield``."""


__all__ = [
    "FAILURE_MESSAGES",
    "REFUSAL_MESSAGES",
    "EntityInput",
    "FailureReason",
    "InputRefusedError",
    "RefusalReason",
    "Shuttle",
    "ShuttleConfigurationError",
    "ShuttleFailedError",
]
