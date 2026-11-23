"""The screenshot Shuttle against a real browser service (Playwright's server).

Opt-in: set ``NIGHTLOOM_TEST_BROWSER_URL`` (CI's integration job points it at
the stack's ``browser`` service). The site is fake: every request the page
makes is answered by a mock transport through the broker, so nothing touches
the network, which is also what makes the browser service safe.
"""

import io
import os

import httpx
import pytest
from PIL import Image

from nightloom_core.entities import EntityType
from nightloom_sdk import EntityFinding
from nightloom_sdk.evidence import MemoryEvidenceStore
from nightloom_sdk.testing import collect, entity_input, fake_context
from nightloom_shuttle_screenshot import ScreenshotShuttle
from nightloom_shuttle_screenshot.broker import RequestBroker
from nightloom_shuttle_screenshot.browser import PlaywrightRenderer
from nightloom_shuttle_screenshot.picture import read_picture

BROWSER_URL = os.environ.get("NIGHTLOOM_TEST_BROWSER_URL", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not BROWSER_URL, reason="NIGHTLOOM_TEST_BROWSER_URL is not set"),
]

PAGE = b"""<!doctype html><html><head><title>Nightloom test page</title>
<link rel=stylesheet href="/site.css"><script src="https://cdn.example.net/app.js"></script>
</head><body><h1>Hello from the broker</h1><img src="/logo.png">
<script>
try { new WebSocket("wss://example.org/live") } catch (e) {}
fetch("/api", {method: "POST", body: "x"}).catch(() => {});
window.open("https://popup.example.org/");
</script></body></html>"""


def logo() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (200, 100), (250, 200, 0)).save(out, format="PNG")
    return out.getvalue()


class Site:
    def __init__(self) -> None:
        self.asked: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(f"{request.method} {request.url}")
        path = request.url.path
        if request.url.host == "example.org" and path == "/":
            return httpx.Response(302, headers={"location": "https://example.org/home"})
        if path == "/home":
            return httpx.Response(
                200, content=PAGE, headers={"content-type": "text/html", "set-cookie": "s=1"}
            )
        if path == "/site.css":
            css = b"body{background:#123456;color:#fff;font:40px sans-serif}"
            return httpx.Response(200, content=css, headers={"content-type": "text/css"})
        if path == "/logo.png":
            return httpx.Response(200, content=logo(), headers={"content-type": "image/png"})
        if path == "/app.js":
            return httpx.Response(200, content=b"", headers={"content-type": "text/javascript"})
        return httpx.Response(404)


async def test_the_page_is_drawn_from_what_the_broker_answers() -> None:
    site = Site()
    ctx = fake_context(ScreenshotShuttle.meta, transport=httpx.MockTransport(site.handler))
    broker = RequestBroker(ctx.http)
    rendering = await PlaywrightRenderer(BROWSER_URL).render("https://example.org/", broker)
    assert rendering.final_url == "https://example.org/home"
    assert rendering.title == "Nightloom test page"
    picture = read_picture(rendering.png)
    assert (picture.width, picture.height) == (1280, 800)
    assert site.asked == [
        "GET https://example.org/",
        "GET https://example.org/home",
        "GET https://example.org/site.css",
        "GET https://cdn.example.net/app.js",
        "GET https://example.org/logo.png",
    ]
    # The POST and the pop-up were refused; the WebSocket never got a request.
    assert broker.refused == 2
    # The stylesheet's background (#123456) fills the window.
    with Image.open(io.BytesIO(rendering.png)) as image:
        assert image.convert("RGB").getpixel((1200, 700)) == (0x12, 0x34, 0x56)


async def test_the_shuttle_keeps_the_screenshot() -> None:
    site = Site()
    store = MemoryEvidenceStore()
    ctx = fake_context(
        ScreenshotShuttle.meta, transport=httpx.MockTransport(site.handler), evidence=store
    )
    shuttle = ScreenshotShuttle(renderer=PlaywrightRenderer(BROWSER_URL))
    findings = await collect(shuttle, entity_input(EntityType.DOMAIN, "example.org"), ctx=ctx)
    [url] = [f for f in findings if isinstance(f, EntityFinding)]
    assert url.value == "https://example.org/home"
    props = url.properties or {}
    assert props["screenshot"] in store.files
    assert props["title"] == "Nightloom test page"
