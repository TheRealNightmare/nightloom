"""The per-host token bucket, on a fake clock."""

import pytest

from nightloom_sdk.meta import RateLimit
from nightloom_sdk.ratelimit import HostRateLimiter


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def time() -> FakeTime:
    return FakeTime()


def limiter(time: FakeTime, requests: int = 2, period: float = 10) -> HostRateLimiter:
    return HostRateLimiter(
        RateLimit(requests=requests, period_seconds=period), clock=time.clock, sleep=time.sleep
    )


async def test_burst_then_waits_for_refill(time: FakeTime) -> None:
    limit = limiter(time)
    assert await limit.acquire("example.org") == 0
    assert await limit.acquire("example.org") == 0
    # Empty: one token takes period / requests = 5 seconds.
    assert await limit.acquire("example.org") == pytest.approx(5)
    assert time.slept == [pytest.approx(5)]
    assert limit.limit.requests == 2


async def test_hosts_have_separate_buckets(time: FakeTime) -> None:
    limit = limiter(time, requests=1)
    assert await limit.acquire("a.example") == 0
    assert await limit.acquire("b.example") == 0
    assert await limit.acquire("A.EXAMPLE") == pytest.approx(10)


async def test_refill_is_capped_at_the_burst_size(time: FakeTime) -> None:
    limit = limiter(time, requests=2, period=10)
    await limit.acquire("example.org")
    time.now += 1_000  # a long pause refills only up to `requests`
    assert await limit.acquire("example.org") == 0
    assert await limit.acquire("example.org") == 0
    assert await limit.acquire("example.org") == pytest.approx(5)


async def test_long_run_rate_never_exceeds_the_limit(time: FakeTime) -> None:
    limit = limiter(time, requests=3, period=6)
    start = time.now
    for _ in range(30):
        await limit.acquire("example.org")
    # 3 at once, then one every 2 s for the remaining 27.
    assert time.now - start == pytest.approx(54)
