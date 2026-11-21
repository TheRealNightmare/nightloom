"""Result cache keys and settings, without a database."""

import uuid

import pytest
from pydantic import ValidationError

from nightloom_api.config import Settings
from nightloom_api.jobs.result_cache import CacheScope, cache_key, encoded_size

CASE = uuid.UUID("00000000-0000-4000-8000-000000000001")
OTHER = uuid.UUID("00000000-0000-4000-8000-000000000002")


def key(**overrides: object) -> str:
    values: dict[str, object] = {
        "scope": CacheScope.CASE,
        "case_id": CASE,
        "shuttle_name": "dns",
        "shuttle_version": "1.0.0",
        "entity_key": "domain:example.org",
    }
    values.update(overrides)
    return cache_key(**values)  # type: ignore[arg-type]


def test_keys_name_the_scope_shuttle_version_entity_and_options() -> None:
    base = key()
    assert len(base) == 64 and int(base, 16) >= 0  # SHA-256, hex
    assert key() == base
    variants = [
        key(case_id=OTHER),
        key(scope=CacheScope.INSTANCE),
        key(shuttle_name="crtsh"),
        key(shuttle_version="1.0.1"),
        key(entity_key="domain:www.example.org"),
        key(options={"depth": 1}),
    ]
    assert len({base, *variants}) == len(variants) + 1
    # Instance-wide keys do not depend on the case.
    assert key(scope=CacheScope.INSTANCE) == key(scope=CacheScope.INSTANCE, case_id=OTHER)
    # Options are normalized: their order never matters; none equals empty.
    assert key(options={"a": 1, "b": 2}) == key(options={"b": 2, "a": 1})
    assert key(options={}) == base


def test_there_are_no_keys_when_the_cache_is_off() -> None:
    with pytest.raises(ValueError, match="off"):
        key(scope=CacheScope.OFF)


def test_encoded_size_is_compact_utf8_json() -> None:
    assert encoded_size([], []) == len(b"[[],[]]")
    assert encoded_size([{"value": "é"}], ["n"]) == len('[[{"value":"é"}],["n"]]'.encode())


def test_settings_default_to_a_per_case_cache() -> None:
    settings = Settings(env="test")
    assert settings.result_cache_scope == "case"
    assert settings.result_cache_max_bytes == 512 * 1024 * 1024
    assert settings.result_cache_max_entry_bytes == 5 * 1024 * 1024
    assert Settings(env="test", result_cache_scope="instance").result_cache_scope == "instance"
    assert Settings(env="test", result_cache_scope="off").result_cache_scope == "off"
    with pytest.raises(ValidationError):
        Settings(env="test", result_cache_scope="everyone")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Settings(env="test", result_cache_max_bytes=-1)
    # Every scope a setting may hold is a CacheScope.
    for scope in ("case", "instance", "off"):
        assert CacheScope(scope).value == scope
