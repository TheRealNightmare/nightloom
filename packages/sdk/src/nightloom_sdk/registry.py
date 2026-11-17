"""Finding installed Shuttles.

Shuttles register under the entry-point group ``nightloom.shuttles``; the
entry point's name must be the Shuttle's ``meta.name``::

    [project.entry-points."nightloom.shuttles"]
    registrable-domain = "nightloom_shuttle_registrable_domain:RegistrableDomain"

Loading is strict: an entry point that does not load, is not a concrete
:class:`~nightloom_sdk.shuttle.Shuttle` subclass, has invalid metadata, is
registered under another name, or shares its name with another Shuttle makes
discovery fail with :class:`RegistryError` instead of being skipped quietly.

List what is installed with ``python -m nightloom_sdk.registry list``.
"""

import argparse
import inspect
import sys
from collections.abc import Iterable, Sequence
from functools import lru_cache
from importlib.metadata import EntryPoint, entry_points

from pydantic import ValidationError

from nightloom_core.entities import EntityType
from nightloom_sdk.meta import ShuttleMeta
from nightloom_sdk.shuttle import Shuttle

ENTRY_POINT_GROUP = "nightloom.shuttles"


class RegistryError(Exception):
    """A Shuttle could not be registered. The message names the entry point or Shuttle."""


def validate_shuttle_class(candidate: object, *, where: str) -> type[Shuttle]:
    """Return ``candidate`` if it is a usable Shuttle class, else raise :class:`RegistryError`."""
    if not (inspect.isclass(candidate) and issubclass(candidate, Shuttle)):
        raise RegistryError(f"{where} is not a Shuttle subclass.")
    if inspect.isabstract(candidate):
        raise RegistryError(f"{where} is abstract (it does not implement run()).")
    meta = getattr(candidate, "meta", None)
    if not isinstance(meta, ShuttleMeta):
        raise RegistryError(f"{where} has no ShuttleMeta in `meta`.")
    try:
        # Re-validate: model_construct() or later mutation could bypass the checks.
        ShuttleMeta.model_validate(meta.model_dump())
    except ValidationError as exc:
        raise RegistryError(f"{where} has invalid metadata: {exc.error_count()} error(s).") from exc
    return candidate


class ShuttleRegistry:
    """The installed Shuttles, by name."""

    def __init__(self, shuttles: Iterable[type[Shuttle]] = ()) -> None:
        self._shuttles: dict[str, type[Shuttle]] = {}
        for shuttle in shuttles:
            self.add(shuttle)

    def add(self, shuttle: type[Shuttle], *, where: str | None = None) -> None:
        where = where or f"{shuttle.__module__}.{shuttle.__qualname__}"
        shuttle = validate_shuttle_class(shuttle, where=where)
        name = shuttle.meta.name
        if name in self._shuttles:
            raise RegistryError(f"Two Shuttles are named {name!r} ({where}).")
        self._shuttles[name] = shuttle

    @classmethod
    def discover(cls, *, found: Sequence[EntryPoint] | None = None) -> "ShuttleRegistry":
        """Load every Shuttle registered under :data:`ENTRY_POINT_GROUP`."""
        registry = cls()
        points = list(found) if found is not None else list(entry_points(group=ENTRY_POINT_GROUP))
        for point in sorted(points, key=lambda p: (p.name, p.value)):
            where = f"entry point {point.name!r} ({point.value})"
            try:
                loaded = point.load()
            except Exception as exc:
                raise RegistryError(f"{where} failed to load: {type(exc).__name__}.") from exc
            shuttle = validate_shuttle_class(loaded, where=where)
            if shuttle.meta.name != point.name:
                raise RegistryError(
                    f"{where} must be named after its Shuttle ({shuttle.meta.name!r})."
                )
            registry.add(shuttle, where=where)
        return registry

    def get(self, name: str) -> type[Shuttle] | None:
        return self._shuttles.get(name)

    def list(self, input_type: EntityType | None = None) -> list[type[Shuttle]]:
        """Shuttles by name; with ``input_type``, only those that accept it."""
        return [
            shuttle
            for name, shuttle in sorted(self._shuttles.items())
            if input_type is None or shuttle.meta.accepts(input_type)
        ]

    def __len__(self) -> int:
        return len(self._shuttles)

    def __contains__(self, name: object) -> bool:
        return name in self._shuttles


@lru_cache(maxsize=1)
def default_registry() -> ShuttleRegistry:
    """The installed Shuttles, discovered once per process."""
    return ShuttleRegistry.discover()


def _describe(meta: ShuttleMeta) -> str:
    inputs = ",".join(sorted(t.value for t in meta.input_types))
    return (
        f"{meta.name:<28} {meta.version:<10} {meta.mode.value:<8} {meta.sensitivity.value:<15} "
        f"{'yes' if meta.network else 'no':<8} {inputs}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m nightloom_sdk.registry", description="Installed Nightloom Shuttles."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="list the installed Shuttles")
    parser.parse_args(argv)
    try:
        registry = ShuttleRegistry.discover()
    except RegistryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{'NAME':<28} {'VERSION':<10} {'MODE':<8} {'SENSITIVITY':<15} {'NETWORK':<8} INPUTS")
    for shuttle in registry.list():
        print(_describe(shuttle.meta))
    print(f"{len(registry)} Shuttle(s) installed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
