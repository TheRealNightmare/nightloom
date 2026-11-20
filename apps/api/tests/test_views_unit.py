"""Saved-view validation and the shared write limiter, without services."""

import math
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from nightloom_api.layouts.ratelimit import KEY_PREFIX, LayoutRateLimiter
from nightloom_api.views.schemas import (
    CAMERA_AT_TARGET,
    NAME_CONTROL,
    NOT_A_FOCUS_KEY,
    NOT_A_GROUP,
    ZERO_UP,
    ViewCreate,
    ViewState,
    ViewStateV1,
    ViewStateV2,
    ViewUpdate,
)

VIEW_STATE: TypeAdapter[ViewStateV1 | ViewStateV2] = TypeAdapter(ViewState)

STATE: dict[str, Any] = {
    "version": 1,
    "mode": "2d",
    "camera": {"x": 0, "y": 0, "ratio": 1, "angle": 0},
    "filters": {"seen": {"kind": "range", "from": "2026-11-01", "to": "2026-11-20"}},
    "groups": {"expanded": ["group:domain:example.org|url"]},
    "positions": {"domain:example.org": [1, 2]},
}


def test_stored_state_uses_aliases_and_iso_dates() -> None:
    stored = VIEW_STATE.validate_python(STATE).stored()
    assert stored["filters"]["seen"] == {"kind": "range", "from": "2026-11-01", "to": "2026-11-20"}
    assert stored["positions"] == {"domain:example.org": [1.0, 2.0]}
    assert stored["camera"] == {"x": 0.0, "y": 0.0, "ratio": 1.0, "angle": 0.0}


def test_angle_may_be_a_full_turn_either_way() -> None:
    for angle in (math.tau, -math.tau):
        VIEW_STATE.validate_python({**STATE, "camera": {**STATE["camera"], "angle": angle}})


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"groups": {"expanded": ["url"]}}, NOT_A_GROUP),
        ({"filters": {"focus": {"key": "nope"}}}, NOT_A_FOCUS_KEY),
    ],
)
def test_messages_never_echo_the_input(change: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError) as caught:
        VIEW_STATE.validate_python({**STATE, **change})
    assert message in str(caught.value)


STATE_V2: dict[str, Any] = {
    **STATE,
    "version": 2,
    "mode": "3d",
    "camera3d": {"position": [1, 2, 300], "target": [1, 2, 0], "up": [0, 1, 0]},
    "depth": "time",
}


def test_version_1_states_are_still_accepted_as_2d() -> None:
    state = VIEW_STATE.validate_python(STATE)
    assert isinstance(state, ViewStateV1)
    assert state.stored()["version"] == 1
    assert "camera3d" not in state.stored()


def test_version_2_stores_the_3d_camera_and_depth() -> None:
    state = VIEW_STATE.validate_python(STATE_V2)
    assert isinstance(state, ViewStateV2)
    stored = state.stored()
    assert stored["mode"] == "3d"
    assert stored["depth"] == "time"
    assert stored["camera3d"] == {
        "position": [1.0, 2.0, 300.0],
        "target": [1.0, 2.0, 0.0],
        "up": [0.0, 1.0, 0.0],
    }


def test_version_2_without_a_3d_camera_stores_none() -> None:
    plain = {key: value for key, value in STATE_V2.items() if key != "camera3d"}
    stored = VIEW_STATE.validate_python({**plain, "mode": "2d", "depth": "flat"}).stored()
    assert "camera3d" not in stored
    assert stored["depth"] == "flat"


@pytest.mark.parametrize(
    "change",
    [
        {"version": 3},
        {"mode": "4d"},
        {"depth": "colour"},
        {"depth": None},
        {"camera3d": {"position": [0, 0, 1], "target": [0, 0, 0]}},
        {"camera3d": {"position": [0, 0, 1], "target": [0, 0, 0], "up": [0, 2, 0]}},
        {"camera3d": {"position": [0, 0, 1e6 + 1], "target": [0, 0, 0], "up": [0, 1, 0]}},
        {"camera3d": {"position": [0, 0], "target": [0, 0, 0], "up": [0, 1, 0]}},
        {"camera3d": {"position": [0, 0, "1"], "target": [0, 0, 0], "up": [0, 1, 0]}},
        {"camera3d": {"position": [0, 0, 1], "target": [0, 0, 0], "up": [0, 1, 0], "roll": 0}},
    ],
)
def test_invalid_version_2_states_are_refused(change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        VIEW_STATE.validate_python({**STATE_V2, **change})


def test_version_1_refuses_3d_fields() -> None:
    for change in ({"mode": "3d"}, {"depth": "time"}, {"camera3d": STATE_V2["camera3d"]}):
        with pytest.raises(ValidationError):
            VIEW_STATE.validate_python({**STATE, **change})


@pytest.mark.parametrize(
    ("camera3d", "message"),
    [
        ({"position": [0, 0, 1], "target": [0, 0, 0], "up": [0, 0, 0]}, ZERO_UP),
        ({"position": [5, 5, 5], "target": [5, 5, 5], "up": [0, 1, 0]}, CAMERA_AT_TARGET),
    ],
)
def test_unusable_3d_cameras_are_refused(camera3d: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError) as caught:
        VIEW_STATE.validate_python({**STATE_V2, "camera3d": camera3d})
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
