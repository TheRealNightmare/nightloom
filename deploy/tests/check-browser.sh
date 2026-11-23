#!/usr/bin/env bash
# Checks the screenshot Shuttle's browser service in the running stack
# (`make up SCREENSHOTS=1`):
#
#  1. the browser container has no route out: a request to the internet fails;
#  2. the worker reaches it on the internal network (NIGHTLOOM_BROWSER_URL) and
#     draws a page whose every request is answered by a fake site through the
#     broker, so the Python and Node Playwright versions match.
#
# Usage: deploy/tests/check-browser.sh <docker compose command and its options...>
set -euo pipefail

if [ "$#" -eq 0 ]; then
  echo "usage: $0 docker compose -f deploy/compose.yaml ... --profile screenshots" >&2
  exit 2
fi
compose=("$@")

echo "== The browser cannot reach the internet"
"${compose[@]}" exec -T browser node -e '
  fetch("https://example.com/", { signal: AbortSignal.timeout(10000) }).then(
    () => { console.error("  FAIL: the browser container reached the internet"); process.exit(1); },
    () => { console.log("  ok: no route out"); process.exit(0); },
  );'

echo "== The worker draws a page in the browser service"
"${compose[@]}" exec -T worker python - <<'PY'
import asyncio, io, os

import httpx
from PIL import Image

from nightloom_sdk.testing import fake_context
from nightloom_shuttle_screenshot import ScreenshotShuttle
from nightloom_shuttle_screenshot.broker import RequestBroker
from nightloom_shuttle_screenshot.browser import PlaywrightRenderer

PAGE = (b"<!doctype html><title>Nightloom browser check</title>"
        b"<style>body{background:#123456}</style><h1>ok</h1>")

def site(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=PAGE, headers={"content-type": "text/html"})

async def main() -> None:
    ctx = fake_context(ScreenshotShuttle.meta, transport=httpx.MockTransport(site))
    renderer = PlaywrightRenderer(os.environ["NIGHTLOOM_BROWSER_URL"])
    rendering = await renderer.render("https://example.org/", RequestBroker(ctx.http))
    assert rendering.title == "Nightloom browser check", rendering.title
    with Image.open(io.BytesIO(rendering.png)) as image:
        assert image.size == (1280, 800), image.size
        assert image.convert("RGB").getpixel((1000, 600)) == (0x12, 0x34, 0x56)
    print(f"  ok: drew {rendering.final_url} ({len(rendering.png)} bytes of PNG)")

asyncio.run(main())
PY
echo "The browser service is isolated and works with the worker."
