"""Discovering Shuttles from entry points, and the registry CLI."""

from collections.abc import AsyncIterator
from importlib.metadata import EntryPoint
from typing import Any

import pytest

from nightloom_core.entities import EntityType, Sensitivity
from nightloom_core.relationships import RelationType
from nightloom_sdk import EntityInput, Finding, Shuttle, ShuttleContext, ShuttleMeta
from nightloom_sdk import registry as registry_module
from nightloom_sdk.registry import RegistryError, ShuttleRegistry, main


def make_meta(name: str, **overrides: Any) -> ShuttleMeta:
    values: dict[str, Any] = {
        "name": name,
        "version": "1.0.0",
        "title": "Test Shuttle",
        "description": "A Shuttle used by the registry tests.",
        "input_types": {EntityType.DOMAIN},
        "output_entity_types": {EntityType.DOMAIN},
        "output_relationship_types": {RelationType.HAS_SUBDOMAIN},
        "sensitivity": Sensitivity.INFRASTRUCTURE,
        "network": False,
    }
    values.update(overrides)
    return ShuttleMeta.model_validate(values)


class Alpha(Shuttle):
    meta = make_meta("alpha")

    async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Finding]:
        return
        yield


class Beta(Shuttle):
    meta = make_meta("beta", input_types={EntityType.IP_ADDRESS}, output_relationship_types=set())

    async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Finding]:
        return
        yield


class AlsoAlpha(Shuttle):
    meta = make_meta("alpha", version="2.0.0")

    async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Finding]:
        return
        yield


class Abstract(Shuttle):
    meta = make_meta("abstract")


class NoMeta(Shuttle):
    async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Finding]:
        return
        yield


class Tampered(Shuttle):
    # model_construct() skips validation: the registry validates again.
    meta = ShuttleMeta.model_construct(**{**make_meta("tampered").__dict__, "name": "Bad Name"})

    async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Finding]:
        return
        yield


def broken() -> None:
    raise RuntimeError("import-time failure")


def point(name: str, obj: str) -> EntryPoint:
    return EntryPoint(name=name, value=f"{__name__}:{obj}", group="nightloom.shuttles")


def test_discover_loads_entry_points() -> None:
    registry = ShuttleRegistry.discover(found=[point("beta", "Beta"), point("alpha", "Alpha")])
    assert [s.meta.name for s in registry.list()] == ["alpha", "beta"]
    assert registry.get("alpha") is Alpha
    assert registry.get("missing") is None
    assert "beta" in registry and len(registry) == 2
    assert [s.meta.name for s in registry.list(EntityType.IP_ADDRESS)] == ["beta"]
    assert registry.list(EntityType.EMAIL) == []


def test_duplicate_names_are_an_error() -> None:
    with pytest.raises(RegistryError, match="Two Shuttles are named 'alpha'"):
        ShuttleRegistry([Alpha, AlsoAlpha])


def test_entry_point_must_be_named_after_its_shuttle() -> None:
    with pytest.raises(RegistryError, match="must be named after its Shuttle"):
        ShuttleRegistry.discover(found=[point("not-alpha", "Alpha")])


@pytest.mark.parametrize(
    ("obj", "message"),
    [
        ("make_meta", "not a Shuttle subclass"),
        ("Abstract", "abstract"),
        ("NoMeta", "no ShuttleMeta"),
        ("Tampered", "invalid metadata"),
        ("broken", "not a Shuttle subclass"),
        ("Missing", "failed to load: AttributeError"),
    ],
)
def test_bad_entry_points_are_errors(obj: str, message: str) -> None:
    with pytest.raises(RegistryError, match=message):
        ShuttleRegistry.discover(found=[point("x", obj)])


def test_meta_must_be_a_shuttle_meta() -> None:
    with pytest.raises(TypeError, match="must be a ShuttleMeta"):

        class Wrong(Shuttle):
            meta = "wrong"  # type: ignore[assignment]


def test_installed_built_in_shuttle_is_discovered() -> None:
    registry = ShuttleRegistry.discover()
    shuttle = registry.get("registrable-domain")
    assert shuttle is not None
    assert shuttle.meta.network is False


def test_cli_lists_installed_shuttles(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["list"]) == 0
    out = capsys.readouterr().out
    assert "registrable-domain" in out
    assert "Shuttle(s) installed." in out


def test_cli_reports_registry_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(**_: Any) -> ShuttleRegistry:
        raise RegistryError("Two Shuttles are named 'x'.")

    monkeypatch.setattr(ShuttleRegistry, "discover", fail)
    assert main(["list"]) == 1
    assert "error: Two Shuttles" in capsys.readouterr().err


def test_default_registry_is_cached() -> None:
    registry_module.default_registry.cache_clear()
    assert registry_module.default_registry() is registry_module.default_registry()
