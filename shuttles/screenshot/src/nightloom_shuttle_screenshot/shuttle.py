"""The screenshot Shuttle: what a site's page looks like, kept with the case.

**Visits the site** (``mode=visit``): it draws the page in the **browser
service** (``NIGHTLOOM_BROWSER_URL``, the optional ``browser`` Compose
service), which has no network of its own: every request the page makes is
fetched by this Shuttle through its guarded HTTP client (see
:mod:`nightloom_shuttle_screenshot.broker`). For a Domain it opens
``https://<domain>/`` (then ``http://`` if that cannot be loaded); for a URL,
the URL.

It keeps the PNG of the 1280 x 800 window as **evidence** with the case
(:meth:`~nightloom_sdk.context.ShuttleContext.save_evidence`; as a JPEG if
the PNG is over 5 MB) and yields the page's URL (after redirects, if they
stayed on the same host) with ``screenshot`` (the evidence name),
``screenshot_at``, ``screenshot_width``, ``screenshot_height``,
``screenshot_phash`` (a 64-bit perceptual hash: look-alike pages have close
hashes, which the case's *Looks like* list uses) and ``title``; a Domain
``HOSTS`` that URL.

Never cached (``cache_ttl_seconds=0``): evidence belongs to one case.
Provenance **A2** (0.9): what the site itself showed.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from nightloom_core.entities import EntityType, Sensitivity
from nightloom_core.normalize import NormalizationError, normalize
from nightloom_core.relationships import RelationType
from nightloom_sdk import (
    EntityFinding,
    EntityInput,
    EntityRef,
    EvidenceError,
    EvidenceUnavailableError,
    FailureReason,
    Finding,
    Mode,
    RateLimit,
    RelationshipFinding,
    Shuttle,
    ShuttleContext,
    ShuttleFailedError,
    ShuttleMeta,
)
from nightloom_sdk.evidence import MAX_EVIDENCE_BYTES
from nightloom_shuttle_screenshot.broker import RequestBroker
from nightloom_shuttle_screenshot.browser import (
    BrowserUnavailableError,
    NavigationError,
    PlaywrightRenderer,
    Renderer,
    Rendering,
)
from nightloom_shuttle_screenshot.config import load_browser_url
from nightloom_shuttle_screenshot.picture import Picture, read_picture, to_jpeg

D = EntityType.DOMAIN
U = EntityType.URL

TITLE_MAX = 500
ADMIRALTY, CONFIDENCE = "A2", 0.9

NOTE_NO_BROWSER = (
    "The browser service did not answer. Start it with `make up SCREENSHOTS=1` "
    "(or the `screenshots` Compose profile)."
)
NOTE_NO_STORAGE = "The screenshot could not be stored with the case (object storage unavailable)."
NOTE_HTTP_ONLY = "{host} could not be loaded over HTTPS; the plain HTTP page was drawn."
NOTE_OTHER_HOST = (
    "The page redirected to another host ({host}); the screenshot is kept on the address asked for."
)
NOTE_REQUESTS = (
    "The page made {requests} request(s) to {hosts} host(s); {refused} were refused "
    "and {failed} failed."
)
NOTE_JPEG = "The screenshot was kept as a JPEG: the PNG was over 5 MB."

Now = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ScreenshotShuttle(Shuttle):
    """A page's screenshot and perceptual hash. See the module docstring."""

    meta = ShuttleMeta(
        name="screenshot",
        version="1.0.0",
        title="Screenshot",
        description=(
            "Draws the page in Nightloom's sandboxed browser (which has no network of its own: "
            "every request goes through the same checks as other Shuttles) and keeps a "
            "screenshot with the case, with a perceptual hash so pages that look alike can be "
            "found. Visits the site, so it runs where this case allows website visits, or on "
            "a verified domain. Needs the browser service."
        ),
        input_types=frozenset({D, U}),
        output_entity_types=frozenset({D, U}),
        output_relationship_types=frozenset({RelationType.HOSTS}),
        mode=Mode.VISIT,
        sensitivity=Sensitivity.INFRASTRUCTURE,
        network=True,
        # Pages load many files from a few hosts at once.
        rate_limit=RateLimit(requests=20, period_seconds=1),
        timeout_seconds=120,
        cache_ttl_seconds=0,
        default_admiralty=ADMIRALTY,
        default_confidence=CONFIDENCE,
    )

    @classmethod
    def check_configuration(cls) -> None:
        load_browser_url()

    def __init__(self, *, renderer: Renderer | None = None, now: Now = _utcnow) -> None:
        """``renderer`` and ``now`` replace the browser service and the clock in tests."""
        self._renderer = renderer
        self._now = now

    async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Finding]:
        renderer = self._renderer or PlaywrightRenderer(load_browser_url())
        if entity.type == D:
            targets = [f"https://{entity.value}/", f"http://{entity.value}/"]
        else:
            targets = [entity.value]
        rendering: Rendering | None = None
        broker = RequestBroker(ctx.http)
        for index, target in enumerate(targets):
            ctx.raise_if_cancelled()
            ctx.progress(f"Drawing {urlsplit(target).scheme}://{urlsplit(target).hostname}/.")
            try:
                rendering = await renderer.render(target, broker)
            except BrowserUnavailableError as error:
                ctx.note(NOTE_NO_BROWSER)
                raise ShuttleFailedError(FailureReason.SOURCE_UNAVAILABLE) from error
            except NavigationError:
                continue
            if index > 0:
                ctx.note(NOTE_HTTP_ONLY.format(host=entity.value))
            break
        ctx.note(
            NOTE_REQUESTS.format(
                requests=broker.requests,
                hosts=len(broker.hosts),
                refused=broker.refused,
                failed=broker.failed,
            )
        )
        if rendering is None:
            raise ShuttleFailedError(FailureReason.SOURCE_UNAVAILABLE)

        page_url = self._page_url(ctx, entity, targets[0], rendering.final_url)
        data, content_type = rendering.png, "image/png"
        if len(data) > MAX_EVIDENCE_BYTES:
            data, content_type = await asyncio.to_thread(to_jpeg, data), "image/jpeg"
            ctx.note(NOTE_JPEG)
        picture: Picture = await asyncio.to_thread(read_picture, data)
        try:
            name = await ctx.save_evidence(data, content_type=content_type)
        except (EvidenceUnavailableError, EvidenceError) as error:
            ctx.note(NOTE_NO_STORAGE)
            raise ShuttleFailedError(FailureReason.SOURCE_UNAVAILABLE) from error

        properties: dict[str, Any] = {
            "screenshot": name,
            "screenshot_at": self._now(),
            "screenshot_width": picture.width,
            "screenshot_height": picture.height,
            "screenshot_phash": picture.phash,
        }
        title = " ".join(rendering.title.split())[:TITLE_MAX]
        if title:
            properties["title"] = title
        yield EntityFinding(type=U, value=page_url, properties=properties)
        if entity.type == D:
            yield RelationshipFinding(
                type=RelationType.HOSTS,
                source=entity.ref,
                target=EntityRef(type=U, value=page_url),
            )

    @staticmethod
    def _page_url(ctx: ShuttleContext, entity: EntityInput, asked: str, final: str) -> str:
        """The URL to keep the screenshot on: the final page if it is on the same host."""
        start = entity.value if entity.type == U else asked
        start_host = (urlsplit(start).hostname or "").lower()
        final_host = (urlsplit(final).hostname or "").lower()
        if final_host != start_host:
            ctx.note(NOTE_OTHER_HOST.format(host=final_host or "?"))
            return start
        # The page's own query string is dropped (it can hold tokens), as for Wayback URLs.
        candidate = final.split("#", 1)[0].split("?", 1)[0]
        if entity.type == U:
            return entity.value if candidate == start.split("?", 1)[0] else candidate
        try:
            return normalize(U, candidate).value
        except NormalizationError:
            return start


__all__ = ["ScreenshotShuttle"]
