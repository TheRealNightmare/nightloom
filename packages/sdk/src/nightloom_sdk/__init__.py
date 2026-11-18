"""Nightloom Shuttle SDK.

A Shuttle subclasses :class:`Shuttle`, declares a :class:`ShuttleMeta` and
yields :class:`EntityFinding` and :class:`RelationshipFinding` objects from
``run()``. See CONTRIBUTING.md ("Writing a Shuttle").
"""

from nightloom_sdk.context import JobCancelledError, ShuttleContext
from nightloom_sdk.findings import EntityFinding, EntityRef, Finding, RelationshipFinding
from nightloom_sdk.meta import Mode, RateLimit, ShuttleMeta
from nightloom_sdk.shuttle import (
    EntityInput,
    InputRefusedError,
    RefusalReason,
    Shuttle,
    ShuttleConfigurationError,
)

__all__ = [
    "EntityFinding",
    "EntityInput",
    "EntityRef",
    "Finding",
    "InputRefusedError",
    "JobCancelledError",
    "Mode",
    "RateLimit",
    "RefusalReason",
    "RelationshipFinding",
    "Shuttle",
    "ShuttleConfigurationError",
    "ShuttleContext",
    "ShuttleMeta",
]
