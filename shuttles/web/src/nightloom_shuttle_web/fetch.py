"""Reading one public page the way a browser would, with the limits every visit keeps.

- ``GET`` only, through the Shuttles' guarded client (public addresses only).
- Redirects are followed only on the same host, at most
  :data:`MAX_REDIRECTS` times; a redirect to another host is reported, never
  followed (that host is not the one the case chose to visit).
- At most ``max_bytes`` of the body are read; the rest is never downloaded.
"""

import ssl
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from nightloom_sdk.http import DestinationNotAllowedError

MAX_REDIRECTS = 3


@dataclass(frozen=True)
class Page:
    """What one visit got: the final address, status, headers and (part of) the body."""

    url: str
    status: int
    headers: httpx.Headers
    body: bytes
    truncated: bool
    redirected_elsewhere: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == 200

    def text(self) -> str:
        encoding = self.headers.get_list("content-type")
        charset = "utf-8"
        for value in encoding:
            for part in value.split(";"):
                name, _, given = part.strip().partition("=")
                if name.lower() == "charset" and given:
                    charset = given.strip("\"'")
        try:
            return self.body.decode(charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")

    @property
    def content_type(self) -> str:
        value: str = self.headers.get("content-type", "")
        return value.split(";", 1)[0].strip().lower()


class VisitError(Exception):
    """The page could not be read: ``kind`` is not_public, tls or unreachable."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def _tls_failure(error: BaseException) -> bool:
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, ssl.SSLError):
            return True
        current = current.__cause__ or current.__context__
    return False


async def fetch(client: httpx.AsyncClient, url: str, *, max_bytes: int) -> Page:
    """``GET url`` and same-host redirects; raises :class:`VisitError` if nothing answered."""
    host = (urlsplit(url).hostname or "").lower()
    try:
        for hop in range(MAX_REDIRECTS + 1):
            async with client.stream("GET", url, follow_redirects=False) as response:
                location = response.headers.get("location")
                if response.is_redirect and location and hop < MAX_REDIRECTS:
                    target = urljoin(url, location)
                    parts = urlsplit(target)
                    same = (parts.hostname or "").lower() == host
                    if parts.scheme in ("http", "https") and same:
                        url = target
                        continue
                    return Page(
                        url=url,
                        status=response.status_code,
                        headers=response.headers,
                        body=b"",
                        truncated=False,
                        redirected_elsewhere=parts.hostname or "?",
                    )
                body = bytearray()
                truncated = False
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) >= max_bytes:
                        truncated = len(body) > max_bytes
                        del body[max_bytes:]
                        break
                return Page(
                    url=url,
                    status=response.status_code,
                    headers=response.headers,
                    body=bytes(body),
                    truncated=truncated,
                )
    except DestinationNotAllowedError as error:
        raise VisitError("not_public") from error
    except httpx.HTTPError as error:
        raise VisitError("tls" if _tls_failure(error) else "unreachable") from error
    raise AssertionError("unreachable")  # pragma: no cover - the loop always returns


__all__ = ["MAX_REDIRECTS", "Page", "VisitError", "fetch"]
