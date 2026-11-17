"""The Shuttle base class and the input it receives."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from nightloom_core.entities import EntityType
from nightloom_sdk.findings import EntityRef, Finding
from nightloom_sdk.meta import ShuttleMeta

if TYPE_CHECKING:
    from nightloom_sdk.context import ShuttleContext


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

    @abstractmethod
    def run(self, ctx: "ShuttleContext", entity: EntityInput) -> AsyncIterator[Finding]:
        """Yield findings for ``entity``. Implement as ``async def`` with ``yield``."""


__all__ = ["EntityInput", "Shuttle"]
