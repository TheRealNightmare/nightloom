"""Which names the crt.sh Shuttle keeps, and its crt.sh client: no network."""

from collections.abc import Callable

import httpx
import pytest

from nightloom_sdk.http import DestinationNotAllowedError, create_http_client
from nightloom_shuttle_crtsh import CrtShShuttle
from nightloom_shuttle_crtsh.client import (
    BACKOFF_SECONDS,
    RETRIES,
    CrtShClient,
    InvalidResponseError,
    ResultTooLargeError,
    SourceUnavailableError,
)
from nightloom_shuttle_crtsh.names import DropReason, Name, classify, in_scope

Handler = Callable[[httpx.Request], httpx.Response]

# --- names --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("example.org", Name("example.org", wildcard=False)),
        ("www.example.org", Name("www.example.org", wildcard=False)),
        (" WWW.Example.ORG. ", Name("www.example.org", wildcard=False)),
        ("*.example.org", Name("example.org", wildcard=True)),
        ("*.dev.example.org", Name("dev.example.org", wildcard=True)),
        ("bücher.example.org", Name("xn--bcher-kva.example.org", wildcard=False)),
        ("xn--bcher-kva.example.org", Name("xn--bcher-kva.example.org", wildcard=False)),
        ("_acme-challenge.example.org", Name("_acme-challenge.example.org", wildcard=False)),
        # Email addresses are person data: dropped before anything else.
        ("hostmaster@example.org", DropReason.EMAIL),
        ("Someone.Else@Mail.Example.org", DropReason.EMAIL),
        ("@example.org", DropReason.EMAIL),
        ("192.0.2.25", DropReason.IP_ADDRESS),
        ("2001:db8::25", DropReason.IP_ADDRESS),
        ("[2001:db8::25]", DropReason.IP_ADDRESS),
        ("fe80::1%eth0", DropReason.IP_ADDRESS),
        ("notexample.org", DropReason.OUT_OF_SCOPE),
        ("example.org.attacker.example", DropReason.OUT_OF_SCOPE),
        ("shop.example.net", DropReason.OUT_OF_SCOPE),
        ("*.example.com", DropReason.OUT_OF_SCOPE),
        ("org", DropReason.INVALID),
        ("", DropReason.INVALID),
        ("   ", DropReason.INVALID),
        ("*.*.example.org", DropReason.INVALID),
        ("a*.example.org", DropReason.INVALID),
        ("bad..example.org", DropReason.INVALID),
        ("x" * 301 + ".example.org", DropReason.INVALID),
        ("a b.example.org", DropReason.INVALID),
    ],
)
def test_classify(raw: str, expected: Name | DropReason) -> None:
    assert classify(raw, "example.org") == expected


def test_scope_is_label_aligned() -> None:
    assert in_scope("example.org", "example.org")
    assert in_scope("a.b.example.org", "example.org")
    assert not in_scope("badexample.org", "example.org")
    assert classify("www.shop.example.org", "shop.example.org") == Name(
        "www.shop.example.org", wildcard=False
    )
    assert classify("www.example.org", "shop.example.org") is DropReason.OUT_OF_SCOPE


# --- the client ---------------------------------------------------------------------------


class Clock:
    """Fake time: sleeping moves it forward, instantly."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def client(handler: Handler, clock: Clock, **kwargs: object) -> CrtShClient:
    # No rate limiter here: these tests are about retries.
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return CrtShClient(http, sleep=clock.sleep, clock=clock, **kwargs)  # type: ignore[arg-type]


def answers(*replies: httpx.Response | Exception) -> tuple[Handler, list[httpx.Request]]:
    """A handler giving ``replies`` in order (the last one repeats), and the requests it saw."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        reply = replies[min(len(seen) - 1, len(replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return reply

    return handler, seen


async def test_search_query_and_parameters() -> None:
    handler, seen = answers(httpx.Response(200, json=[{"id": 1}]))
    crtsh = client(handler, Clock())
    assert await crtsh.search("%.example.org") == [{"id": 1}]
    assert await crtsh.search("example.org", exclude_expired=True) == [{"id": 1}]
    assert str(seen[0].url) == "https://crt.sh/?q=%25.example.org&output=json"
    assert str(seen[1].url) == "https://crt.sh/?q=example.org&output=json&exclude=expired"
    assert crtsh.requests == 2


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
async def test_busy_answers_are_retried_with_backoff(status: int) -> None:
    clock = Clock()
    handler, seen = answers(
        httpx.Response(status), httpx.Response(status), httpx.Response(200, json=[])
    )
    crtsh = client(handler, clock)
    assert await crtsh.search("example.org") == []
    assert len(seen) == 3
    assert clock.slept == list(BACKOFF_SECONDS[:2])
    assert crtsh.retries == 2


async def test_gives_up_after_the_retries() -> None:
    clock = Clock()
    handler, seen = answers(httpx.Response(502))
    crtsh = client(handler, clock)
    with pytest.raises(SourceUnavailableError, match="HTTP 502"):
        await crtsh.search("example.org")
    assert len(seen) == RETRIES + 1 == 4
    assert clock.slept == [2.0, 5.0, 10.0]


async def test_timeouts_and_network_errors_are_retried() -> None:
    handler, seen = answers(
        httpx.ReadTimeout("slow"),
        httpx.ConnectError("refused"),
        httpx.Response(200, json=[]),
    )
    assert await client(handler, Clock()).search("example.org") == []
    assert len(seen) == 3
    handler, seen = answers(httpx.ReadTimeout("slow"))
    with pytest.raises(SourceUnavailableError, match="ReadTimeout"):
        await client(handler, Clock()).search("example.org")
    assert len(seen) == 4


async def test_downloads_retry_once() -> None:
    handler, seen = answers(httpx.Response(503), httpx.Response(200, content=b"PEM"))
    assert await client(handler, Clock()).download(42) == b"PEM"
    assert str(seen[0].url) == "https://crt.sh/?d=42"
    handler, seen = answers(httpx.Response(503))
    with pytest.raises(SourceUnavailableError):
        await client(handler, Clock()).download(42)
    assert len(seen) == 2


async def test_no_try_starts_after_the_deadline() -> None:
    clock = Clock()
    handler, seen = answers(httpx.Response(503))
    crtsh = client(handler, clock, deadline=6.0)
    with pytest.raises(SourceUnavailableError, match="out of time after: HTTP 503"):
        await crtsh.search("example.org")
    assert len(seen) == 2  # at 0 s and 2 s; the next would start at 7 s
    crtsh.extend_deadline(100.0)
    with pytest.raises(SourceUnavailableError):
        await crtsh.search("example.org")
    assert len(seen) == 6


async def test_before_request_runs_before_every_try() -> None:
    calls: list[int] = []
    handler, _ = answers(httpx.Response(503), httpx.Response(200, json=[]))
    crtsh = client(handler, Clock(), before_request=lambda: calls.append(1))
    await crtsh.search("example.org")
    assert len(calls) == 2


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        (httpx.Response(400), "HTTP 400"),
        (httpx.Response(404), "HTTP 404"),
        (httpx.Response(200, content=b"<html>crt.sh is busy</html>"), "not JSON"),
        (httpx.Response(200, json={"error": "nope"}), "not a JSON list"),
    ],
)
async def test_unusable_answers_are_not_retried(reply: httpx.Response, message: str) -> None:
    handler, seen = answers(reply)
    with pytest.raises(InvalidResponseError, match=message):
        await client(handler, Clock()).search("example.org")
    assert len(seen) == 1


async def test_an_empty_body_means_no_results() -> None:
    handler, _ = answers(httpx.Response(200, content=b"  \n"))
    assert await client(handler, Clock()).search("example.org") == []


async def test_too_large_is_not_retried() -> None:
    """The guarded client's 5 MB cap surfaces as ResultTooLargeError, at once."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"[" + b" " * 6_000_000 + b"]")

    http = create_http_client(CrtShShuttle.meta, transport=httpx.MockTransport(handler))
    async with http:
        crtsh = CrtShClient(http, sleep=Clock().sleep)
        with pytest.raises(ResultTooLargeError):
            await crtsh.search("%.example.org")
    assert len(seen) == 1


async def test_a_refused_destination_is_not_retried() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise DestinationNotAllowedError("refused", request=request)

    crtsh = client(handler, Clock())
    with pytest.raises(SourceUnavailableError, match="refused address"):
        await crtsh.search("example.org")
    assert crtsh.requests == 1


async def test_error_messages_never_quote_the_response() -> None:
    secret = "sk-live-0123456789"
    for reply in (
        httpx.Response(403, json={"detail": secret}),
        httpx.Response(200, content=f"<p>{secret}</p>".encode()),
    ):
        handler, _ = answers(reply)
        with pytest.raises(InvalidResponseError) as caught:
            await client(handler, Clock()).search("example.org")
        assert secret not in str(caught.value)
