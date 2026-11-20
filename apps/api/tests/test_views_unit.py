"""Saved-view validation and the shared write limiter, without services."""

import math
from typing import Any

import pytest
from pydantic import ValidationError

from nightloom_api.layouts.ratelimit import KEY_PREFIX, LayoutRateLimiter
from nightloom_api.views.schemas import (
    NAME_CONTROL,
    NOT_A_FOCUS_KEY,
    NOT_A_GROUP,
    ViewCreate,
    ViewState,
    ViewUpdate,
)

STATE: dict[str, Any] = {
    "version": 1,
    "mode": "2d",
    "camera": {"x": 0, "y": 0, "ratio": 1, "angle": 0},
    "filters": {"seen": {"kind": "range", "from": "2026-11-01", "to": "2026-11-20"}},
    "groups": {"expanded": ["group:domain:example.org|url"]},
    "positions": {"domain:example.org": [1, 2]},
}


def test_stored_state_uses_aliases_and_iso_dates() -> None:
    stored = ViewState.model_validate(STATE).stored()
    assert stored["filters"]["seen"] == {"kind": "range", "from": "2026-11-01", "to": "2026-11-20"}
    assert stored["positions"] == {"domain:example.org": [1.0, 2.0]}
    assert stored["camera"] == {"x": 0.0, "y": 0.0, "ratio": 1.0, "angle": 0.0}


def test_angle_may_be_a_full_turn_either_way() -> None:
    for angle in (math.tau, -math.tau):
        ViewState.model_validate({**STATE, "camera": {**STATE["camera"], "angle": angle}})


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"groups": {"expanded": ["url"]}}, NOT_A_GROUP),
        ({"filters": {"focus": {"key": "nope"}}}, NOT_A_FOCUS_KEY),
    ],
)
def test_messages_never_echo_the_input(change: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError) as caught:
        ViewState.model_validate({**STATE, **change})
    assert message in str(caught.value)


def test_names_are_trimmed_and_refuse_controls() -> None:
    assert ViewCreate.model_validate({"name": "  Hosts ", "state": STATE}).name == "Hosts"
    with pytest.raises(ValidationError) as caught:
        ViewCreate.model_validate({"name": "a\x1bb", "state": STATE})
    assert NAME_CONTROL in str(caught.value)


def test_an_update_needs_something() -> None:
    assert ViewUpdate.model_validate({"name": "x"}).state is None
    with pytest.raises(ValidationError):
        ViewUpdate.model_validate({})


class CountingCache:
    def __init__(self) -> None:
        self.keys: list[str] = []

    def pipeline(self, transaction: bool) -> "CountingCache":
        return self

    async def __aenter__(self) -> "CountingCache":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def incr(self, key: str) -> None:
        self.keys.append(key)

    def expire(self, key: str, seconds: int, nx: bool) -> None:
        return None

    def ttl(self, key: str) -> None:
        return None

    async def execute(self) -> list[int]:
        return [1, 1, 60]


async def test_limiter_counts_under_its_own_prefix() -> None:
    import uuid

    cache = CountingCache()
    user = uuid.uuid4()
    assert await LayoutRateLimiter(cache, key_prefix="nightloom:views:user").hit(user) is None  # type: ignore[arg-type]
    assert await LayoutRateLimiter(cache).hit(user) is None  # type: ignore[arg-type]
    assert cache.keys == [f"nightloom:views:user:{user}", f"{KEY_PREFIX}:{user}"]
