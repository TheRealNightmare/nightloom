"""The screenshot Shuttle with a fake browser, the request broker, and the hash: no network."""

import io
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from PIL import Image

from nightloom_core.entities import EntityType, build_entity
from nightloom_sdk import (
    EntityFinding,
    FailureReason,
    Finding,
    Mode,
    RelationshipFinding,
    ShuttleConfigurationError,
    ShuttleFailedError,
)
from nightloom_sdk.evidence import MemoryEvidenceStore
from nightloom_sdk.http import DestinationNotAllowedError
from nightloom_sdk.testing import assert_declared, collect, entity_input, fake_context
from nightloom_shuttle_screenshot import ScreenshotShuttle
from nightloom_shuttle_screenshot.broker import RequestBroker
from nightloom_shuttle_screenshot.browser import (
    BrowserUnavailableError,
    NavigationError,
    Rendering,
)
from nightloom_shuttle_screenshot.config import DEFAULT_BROWSER_URL, load_browser_url
from nightloom_shuttle_screenshot.picture import distance, read_picture, to_jpeg

D = EntityType.DOMAIN
U = EntityType.URL
META = ScreenshotShuttle.meta
NOW = datetime(2026, 11, 23, 8, 0, tzinfo=UTC)


def png(width: int = 64, height: int = 40, *, split: int = 32) -> bytes:
    """A two-tone picture: dark left of ``split``, light right of it."""
    image = Image.new("L", (width, height), 230)
    for x in range(split):
        for y in range(height):
            image.putpixel((x, y), 20)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


# --- the hash ----------------------------------------------------------------------------


def test_the_hash_reads_size_and_looks() -> None:
    picture = read_picture(png())
    assert (picture.width, picture.height) == (64, 40)
    assert len(picture.phash) == 16
    # The same picture, larger or compressed, hashes the same or nearly.
    assert distance(picture.phash, read_picture(png(128, 80, split=64)).phash) <= 2
    assert distance(picture.phash, read_picture(to_jpeg(png())).phash) <= 2
    # A picture that looks different does not.
    assert distance(picture.phash, read_picture(png(split=4)).phash) > 10


def test_the_hash_refuses_what_is_not_a_picture() -> None:
    with pytest.raises(ValueError, match="Not a readable image"):
        read_picture(b"<html></html>")
    gif = io.BytesIO()
    Image.new("L", (4, 4)).save(gif, format="GIF")
    with pytest.raises(ValueError, match="Not a PNG or JPEG"):
        read_picture(gif.getvalue())


def test_distance() -> None:
    assert distance("0000000000000000", "0000000000000000") == 0
    assert distance("0000000000000000", "ffffffffffffffff") == 64
    assert distance("00000000000000f0", "0000000000000000") == 4


# --- the broker --------------------------------------------------------------------------


def client(handler: Any) -> httpx.AsyncClient:
    return fake_context(META, transport=httpx.MockTransport(handler)).http


async def test_the_broker_fetches_through_the_guarded_client() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            content=b"body{}",
            headers={"content-type": "text/css", "set-cookie": "a=b", "server": "x"},
        )

    broker = RequestBroker(client(handler))
    reply = await broker.answer(
        "https://example.org/site.css",
        method="GET",
        resource_type="stylesheet",
        headers={"accept": "text/css", "cookie": "secret=1", "referer": "https://example.org/"},
    )
    assert reply is not None
    assert (reply.status, reply.body) == (200, b"body{}")
    assert reply.headers == {"content-type": "text/css"}
    assert "cookie" not in seen[0].headers
    assert "referer" not in seen[0].headers
    assert seen[0].headers["accept"] == "text/css"
    assert (broker.requests, broker.hosts) == (1, {"example.org"})


async def test_the_broker_follows_redirects_checking_each_hop() -> None:
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        if request.url.host == "private.example.org":
            raise DestinationNotAllowedError("private")
        if request.url.path == "/":
            return httpx.Response(301, headers={"location": "/home"})
        return httpx.Response(302, headers={"location": "https://private.example.org/"})

    broker = RequestBroker(client(handler))
    reply = await broker.answer(
        "https://example.org/", method="GET", resource_type="document", headers={}
    )
    assert reply is None
    assert asked == [
        "https://example.org/",
        "https://example.org/home",
        "https://private.example.org/",
    ]
    assert (broker.requests, broker.refused) == (3, 1)


async def test_open_resolves_the_page_and_keeps_it_for_the_browser() -> None:
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        if request.url.path == "/":
            return httpx.Response(303, headers={"location": "https://example.org/home"})
        return httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"})

    broker = RequestBroker(client(handler))
    assert await broker.open("https://example.org/") == "https://example.org/home"
    reply = await broker.answer(
        "https://example.org/home", method="GET", resource_type="document", headers={}
    )
    assert reply is not None
    assert reply.body == b"<html>"
    # Fetched once: the browser's request was answered from what open() kept.
    assert asked == ["https://example.org/", "https://example.org/home"]


async def test_too_many_redirects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": f"/{len(request.url.path)}"})

    broker = RequestBroker(client(handler))
    assert await broker.open("https://example.org/") is None
    assert broker.requests == 6


@pytest.mark.parametrize(
    ("url", "method", "kind"),
    [
        ("https://example.org/", "POST", "document"),
        ("ftp://example.org/x", "GET", "document"),
        ("https://example.org/movie.mp4", "GET", "media"),
        ("wss://example.org/live", "GET", "websocket"),
        ("https://example.org/feed", "GET", "eventsource"),
    ],
)
async def test_the_broker_refuses_what_a_picture_does_not_need(
    url: str, method: str, kind: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("never fetched")

    broker = RequestBroker(client(handler))
    assert await broker.answer(url, method=method, resource_type=kind, headers={}) is None
    assert (broker.requests, broker.refused) == (0, 1)


async def test_redirects_to_other_schemes_are_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "file:///etc/passwd"})

    broker = RequestBroker(client(handler))
    assert await broker.open("https://example.org/") is None
    assert broker.refused == 1


async def test_the_broker_limits_requests_and_bytes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 100)

    broker = RequestBroker(client(handler), max_requests=2, max_total_bytes=150)
    first = await broker.answer(
        "https://example.org/1", method="GET", resource_type="image", headers={}
    )
    assert first is not None
    # The second would pass the byte limit.
    assert (
        await broker.answer(
            "https://example.org/2", method="GET", resource_type="image", headers={}
        )
        is None
    )
    # And the request limit is reached.
    assert (
        await broker.answer(
            "https://example.org/3", method="GET", resource_type="image", headers={}
        )
        is None
    )
    assert broker.requests == 2
    assert broker.refused == 2


async def test_the_broker_counts_refused_destinations_and_failures() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "private.example.org":
            raise DestinationNotAllowedError("private")
        raise httpx.ConnectError("refused")

    broker = RequestBroker(client(handler))
    for host in ("private.example.org", "down.example.org"):
        assert (
            await broker.answer(f"https://{host}/", method="GET", resource_type="image", headers={})
            is None
        )
    assert (broker.refused, broker.failed) == (1, 1)


# --- the Shuttle -------------------------------------------------------------------------


class FakeRenderer:
    """Draws from a table (address → rendering or error); fetches the page through the broker."""

    def __init__(self, pages: dict[str, Rendering | Exception]) -> None:
        self.pages = pages
        self.asked: list[str] = []

    async def render(self, url: str, broker: RequestBroker) -> Rendering:
        self.asked.append(url)
        await broker.answer(url, method="GET", resource_type="document", headers={})
        page = self.pages.get(url, NavigationError())
        if isinstance(page, Exception):
            raise page
        return page


def site(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=b"<html></html>", headers={"content-type": "text/html"})


async def run(
    value: str, kind: EntityType, renderer: FakeRenderer
) -> tuple[list[Finding], tuple[str, ...], MemoryEvidenceStore]:
    store = MemoryEvidenceStore()
    ctx = fake_context(META, transport=httpx.MockTransport(site), evidence=store)
    entity = entity_input(kind, value)
    findings = await collect(ScreenshotShuttle(renderer=renderer, now=lambda: NOW), entity, ctx=ctx)
    assert_declared(META, findings, entity)
    for finding in findings:
        if isinstance(finding, EntityFinding):
            build_entity(finding.type, finding.value, finding.properties)
    return findings, ctx.notes, store


def test_meta() -> None:
    assert META.name == "screenshot"
    assert META.mode == Mode.VISIT
    assert META.input_types == {D, U}
    assert META.cache_ttl_seconds == 0


async def test_a_domain_gets_its_home_page_drawn_and_kept() -> None:
    picture = png()
    renderer = FakeRenderer(
        {
            "https://example.org/": Rendering(
                png=picture,
                final_url="https://example.org/welcome?session=abc",
                title="  Example\n Home ",
            )
        }
    )
    findings, notes, store = await run("example.org", D, renderer)
    [url] = [f for f in findings if isinstance(f, EntityFinding)]
    assert url.type == U
    assert url.value == "https://example.org/welcome"
    props = url.properties or {}
    assert props["screenshot"] in store.files
    assert store.files[props["screenshot"]] == (picture, "image/png")
    assert props["screenshot_at"] == NOW
    assert (props["screenshot_width"], props["screenshot_height"]) == (64, 40)
    assert props["screenshot_phash"] == read_picture(picture).phash
    assert props["title"] == "Example Home"
    [hosts] = [f for f in findings if isinstance(f, RelationshipFinding)]
    assert (hosts.type, hosts.source.value, hosts.target.value) == (
        "HOSTS",
        "example.org",
        "https://example.org/welcome",
    )
    assert any(note.startswith("The page made 1 request(s) to 1 host(s)") for note in notes)


async def test_plain_http_when_https_cannot_be_loaded() -> None:
    renderer = FakeRenderer(
        {"http://example.org/": Rendering(png=png(), final_url="http://example.org/", title="")}
    )
    findings, notes, _ = await run("example.org", D, renderer)
    assert renderer.asked == ["https://example.org/", "http://example.org/"]
    [url] = [f for f in findings if isinstance(f, EntityFinding)]
    assert url.value == "http://example.org/"
    assert "title" not in (url.properties or {})
    assert "example.org could not be loaded over HTTPS; the plain HTTP page was drawn." in notes


async def test_a_redirect_elsewhere_keeps_the_address_asked_for() -> None:
    renderer = FakeRenderer(
        {
            "https://example.org/login?next=/": Rendering(
                png=png(), final_url="https://accounts.example.net/", title="Sign in"
            )
        }
    )
    findings, notes, _ = await run("https://example.org/login?next=/", U, renderer)
    [url] = [f for f in findings if isinstance(f, EntityFinding)]
    assert url.value == entity_input(U, "https://example.org/login?next=/").value
    assert not [f for f in findings if isinstance(f, RelationshipFinding)]
    assert any("redirected to another host (accounts.example.net)" in note for note in notes)


async def test_a_large_screenshot_is_kept_as_jpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("nightloom_shuttle_screenshot.shuttle.MAX_EVIDENCE_BYTES", 10)
    renderer = FakeRenderer(
        {"https://example.org/": Rendering(png=png(), final_url="https://example.org/", title="x")}
    )
    findings, notes, store = await run("example.org", D, renderer)
    [url] = [f for f in findings if isinstance(f, EntityFinding)]
    name = (url.properties or {})["screenshot"]
    assert name.endswith(".jpg")
    assert store.files[name][1] == "image/jpeg"
    assert "The screenshot was kept as a JPEG: the PNG was over 5 MB." in notes


async def test_no_browser_service() -> None:
    renderer = FakeRenderer({"https://example.org/": BrowserUnavailableError()})
    with pytest.raises(ShuttleFailedError) as info:
        await run("example.org", D, renderer)
    assert info.value.reason == FailureReason.SOURCE_UNAVAILABLE


async def test_nothing_loads() -> None:
    with pytest.raises(ShuttleFailedError) as info:
        await run("example.org", D, FakeRenderer({}))
    assert info.value.reason == FailureReason.SOURCE_UNAVAILABLE


def test_the_browser_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NIGHTLOOM_BROWSER_URL", raising=False)
    assert load_browser_url() == DEFAULT_BROWSER_URL
    monkeypatch.setenv("NIGHTLOOM_BROWSER_URL", "ws://127.0.0.1:3000/")
    assert load_browser_url() == "ws://127.0.0.1:3000/"
    ScreenshotShuttle.check_configuration()
    monkeypatch.setenv("NIGHTLOOM_BROWSER_URL", "http://browser:3000/")
    with pytest.raises(ShuttleConfigurationError):
        ScreenshotShuttle.check_configuration()
