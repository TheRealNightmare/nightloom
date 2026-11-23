"""Drawing a page in the browser service, with every request answered by the broker.

:class:`PlaywrightRenderer` connects to the browser service (Playwright's
``run-server``, see ``deploy/browser``) at ``NIGHTLOOM_BROWSER_URL``, opens a
fresh context (no cookies or storage from anything else, service workers
blocked, downloads refused, a fixed 1280 x 800 window), sends **every**
request through :class:`~nightloom_shuttle_screenshot.broker.RequestBroker`,
closes WebSockets and pop-up windows, loads the page, waits briefly for it
to settle and takes a PNG of the window (not the whole scrolling page).

The browser service itself has no route to any network but the worker's, so
a request that somehow escaped interception would go nowhere.
"""

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Protocol

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, Route, WebSocketRoute, async_playwright

from nightloom_shuttle_screenshot.broker import RequestBroker

VIEWPORT = {"width": 1280, "height": 800}
CONNECT_TIMEOUT_MS = 15_000
NAVIGATION_TIMEOUT_MS = 30_000
# After the load event: time for late scripts and fonts to draw.
SETTLE_SECONDS = 1.5
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0 Safari/537.36 Nightloom (+https://github.com/TheRealNightmare/nightloom)"
)


class BrowserUnavailableError(Exception):
    """The browser service did not answer."""


class NavigationError(Exception):
    """The page could not be loaded (refused, unreachable or too slow)."""


@dataclass(frozen=True)
class Rendering:
    png: bytes
    # The page's address after redirects, and its title.
    final_url: str
    title: str


class Renderer(Protocol):
    async def render(self, url: str, broker: RequestBroker) -> Rendering: ...


class PlaywrightRenderer:
    """Renders pages in the browser service at ``endpoint`` (a ``ws://`` address)."""

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint

    async def render(self, url: str, broker: RequestBroker) -> Rendering:
        async with async_playwright() as playwright:
            try:
                browser = await playwright.chromium.connect(
                    self.endpoint, timeout=CONNECT_TIMEOUT_MS
                )
            except PlaywrightError as error:
                raise BrowserUnavailableError from error
            try:
                context = await browser.new_context(
                    viewport=VIEWPORT,  # type: ignore[arg-type]
                    user_agent=USER_AGENT,
                    service_workers="block",
                    accept_downloads=False,
                    java_script_enabled=True,
                    locale="en-US",
                )

                drawn: list[Page] = []

                async def handle(route: Route) -> None:
                    request = route.request
                    try:
                        # Windows the page opens itself never load (only the first page is drawn).
                        other_page = bool(drawn) and request.frame.page != drawn[0]
                    except PlaywrightError:
                        other_page = True
                    if other_page:
                        broker.refused += 1
                        await _abort(route)
                        return
                    reply = await broker.answer(
                        request.url,
                        method=request.method,
                        resource_type=request.resource_type,
                        headers=await request.all_headers(),
                    )
                    if reply is None:
                        await _abort(route)
                        return
                    with contextlib.suppress(PlaywrightError):  # the page went away meanwhile
                        await route.fulfill(
                            status=reply.status, headers=reply.headers, body=reply.body
                        )

                async def no_sockets(socket: WebSocketRoute) -> None:
                    await socket.close()

                await context.route("**/*", handle)
                await context.route_web_socket("**/*", no_sockets)
                context.on("page", _close_popups)
                final = await broker.open(url)
                if final is None:
                    raise NavigationError
                page = await context.new_page()
                drawn.append(page)
                try:
                    await page.goto(final, wait_until="load", timeout=NAVIGATION_TIMEOUT_MS)
                except PlaywrightError as error:
                    raise NavigationError from error
                await asyncio.sleep(SETTLE_SECONDS)
                png = await page.screenshot(type="png", full_page=False)
                return Rendering(png=png, final_url=page.url, title=await page.title())
            finally:
                await browser.close()


async def _abort(route: Route) -> None:
    with contextlib.suppress(PlaywrightError):  # the page went away meanwhile
        await route.abort("blockedbyclient")


async def _close_popups(page: Page) -> None:
    """Windows the page opens itself are closed at once (only the first page is drawn)."""
    with contextlib.suppress(PlaywrightError):
        if await page.opener() is not None:
            await page.close()


__all__ = [
    "BrowserUnavailableError",
    "NavigationError",
    "PlaywrightRenderer",
    "Renderer",
    "Rendering",
]
