# nightloom-shuttle-screenshot

A built-in Nightloom Shuttle that keeps a screenshot of a site's page with the case.

| Shuttle | Input | What it does |
| --- | --- | --- |
| `screenshot` | Domain, URL | Draws the page (`https://<domain>/`, then `http://`; or the URL) in the browser service, keeps the 1280 × 800 window as a PNG with the case, and adds `screenshot`, `screenshot_at`, `screenshot_width`, `screenshot_height`, `screenshot_phash` and `title` to the page's URL. A Domain `HOSTS` the URL. |

It **visits the site** (`mode=visit`), so it runs where a case owner allows website visits, or on verified domains. It is never cached: the screenshot belongs to one case.

## The browser service

Pages are drawn by Playwright's browser server in the optional `browser` Compose service (`make up SCREENSHOTS=1`, see `deploy/browser`). That container has **no network of its own**: it is only on an internal Compose network shared with the worker. Every request the page makes is intercepted and answered by the Shuttle through its guarded HTTP client (`broker.py`):

- public addresses only, rate-limited per host, at most 5 MB per response;
- only `GET` and `HEAD`, only `http(s)`, only what a still picture needs (no video, audio, WebSockets or event streams);
- at most 150 requests and 25 MB per page;
- redirects are handed back to the browser, so each hop is checked again;
- cookies are never sent and `Set-Cookie` is never passed on.

The browser context is fresh for every page (no cookies or storage), blocks service workers, refuses downloads and closes pop-up windows. `NIGHTLOOM_BROWSER_URL` (default `ws://browser:3000/`) says where the service is. The Python `playwright` package must match the service's Playwright in major and minor version (1.65).

## The perceptual hash

`screenshot_phash` is a 64-bit dHash (`picture.py`): the picture in greys, shrunk to 9 × 8, one bit per pair of neighbouring pixels. Pages that look alike have hashes a few bits apart; the case's *Looks like* list shows pages at most 10 bits apart. A copy of a login page on another domain, a staging server or a parked page template stands out this way.
