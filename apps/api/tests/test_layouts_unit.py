"""Layout validation and the save rate limiter, without services."""

import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from nightloom_api.layouts.ratelimit import LayoutRateLimiter
from nightloom_api.layouts.schemas import MAX_COORDINATE, NOT_A_KEY, TOO_MANY, LayoutIn


def test_accepts_canonical_keys_and_finite_coordinates() -> None:
    body = LayoutIn.model_validate_json(
        '{"positions": {"domain:example.org": [1, -2.5], "asn:64496": [1000000, -1000000]}}'
    )
    assert body.positions == {"domain:example.org": [1.0, -2.5], "asn:64496": [1e6, -1e6]}


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ('{"positions": {"Domain:example.org": [0, 0]}}', NOT_A_KEY),
        ('{"positions": {"domain:EXAMPLE.org": [0, 0]}}', NOT_A_KEY),
        ('{"positions": {"domain:example.org": [Infinity, 0]}}', ""),
        (f'{{"positions": {{"domain:example.org": [{MAX_COORDINATE * 2}, 0]}}}}', ""),
        ('{"positions": {"domain:example.org": [0, "1"]}}', ""),
        ('{"positions": {"domain:example.org": [false, 1]}}', ""),
    ],
)
def test_refuses_bad_entries(raw: str, message: str) -> None:
    with pytest.raises(ValidationError) as caught:
        LayoutIn.model_validate_json(raw)
    assert message in str(caught.value)


def test_refuses_too_many_before_parsing_keys() -> None:
    positions = {f"bogus-{i}": [0, 0] for i in range(10_001)}
    with pytest.raises(ValidationError) as caught:
        LayoutIn.model_validate({"positions": positions})
    assert TOO_MANY in str(caught.value)
    assert NOT_A_KEY not in str(caught.value)


class FakePipeline:
    def __init__(self, cache: "FakeCache") -> None:
        self.cache = cache
        self.ops: list[tuple[str, Any]] = []

    async def __aenter__(self) -> "FakePipeline":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def incr(self, key: str) -> None:
        self.ops.append(("incr", key))

    def expire(self, key: str, seconds: int, nx: bool) -> None:
        self.ops.append(("expire", (key, seconds)))

    def ttl(self, key: str) -> None:
        self.ops.append(("ttl", key))

    async def execute(self) -> list[Any]:
        results: list[Any] = []
        for op, arg in self.ops:
            if op == "incr":
                self.cache.counts[arg] = self.cache.counts.get(arg, 0) + 1
                results.append(self.cache.counts[arg])
            elif op == "expire":
                results.append(True)
            else:
                results.append(self.cache.ttl_value)
        return results


class FakeCache:
    def __init__(self, ttl: int = 42) -> None:
        self.counts: dict[str, int] = {}
        self.ttl_value = ttl

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        return FakePipeline(self)

    async def decr(self, key: str) -> None:
        self.counts[key] -= 1


async def test_rate_limiter_counts_per_user_and_does_not_count_refusals() -> None:
    cache = FakeCache()
    limiter = LayoutRateLimiter(cache, max_writes=2)  # type: ignore[arg-type]
    user, other = uuid.uuid4(), uuid.uuid4()
    assert await limiter.hit(user) is None
    assert await limiter.hit(user) is None
    assert await limiter.hit(user) == 42
    assert await limiter.hit(other) is None
    assert cache.counts[f"nightloom:layout:user:{user}"] == 2


async def test_rate_limiter_waits_a_window_when_the_expiry_is_lost() -> None:
    limiter = LayoutRateLimiter(FakeCache(ttl=-1), max_writes=0, window_seconds=60)  # type: ignore[arg-type]
    assert await limiter.hit(uuid.uuid4()) == 60
