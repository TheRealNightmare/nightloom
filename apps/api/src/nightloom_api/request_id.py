"""Request IDs: one identifier per HTTP request, for logs, audit events and responses.

:class:`RequestIdMiddleware` accepts the caller's ``X-Request-ID`` only if it
matches :data:`REQUEST_ID_PATTERN` (the web proxy always sends one), otherwise
it generates a UUID4. The ID is available through :func:`current_request_id`
while the request is handled, is added to every log record by
:class:`RequestIdLogFilter`, and is returned in the ``X-Request-ID`` response
header.

The middleware is plain ASGI rather than Starlette's ``BaseHTTPMiddleware``,
which runs the app in a separate task (so context variables set inside it do
not propagate) and buffers streaming responses.
"""

import logging
import re
import uuid
from contextvars import ContextVar

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{8,64}")
# Shown in log records written outside a request.
NO_REQUEST_ID = "-"

_request_id: ContextVar[str | None] = ContextVar("nightloom_request_id", default=None)


def is_valid_request_id(value: str | None) -> bool:
    """Whether ``value`` may be used as a request ID (8-64 of ``A-Z a-z 0-9 . _ -``)."""
    return value is not None and REQUEST_ID_PATTERN.fullmatch(value) is not None


def new_request_id() -> str:
    """A fresh request ID (UUID4)."""
    return str(uuid.uuid4())


def current_request_id() -> str | None:
    """The ID of the request being handled, or ``None`` outside a request."""
    return _request_id.get()


def _incoming_request_id(scope: Scope) -> str | None:
    wanted = REQUEST_ID_HEADER.lower().encode("latin-1")
    for name, value in scope.get("headers", ()):
        if name == wanted:
            return str(value.decode("latin-1"))
    return None


class RequestIdMiddleware:
    """Assign each HTTP request an ID and echo it in the ``X-Request-ID`` response header."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = _incoming_request_id(scope)
        request_id = incoming if is_valid_request_id(incoming) else new_request_id()
        assert request_id is not None  # noqa: S101 - narrowed for mypy

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
            await send(message)

        token = _request_id.set(request_id)
        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            _request_id.reset(token)


class RequestIdLogFilter(logging.Filter):
    """Adds ``request_id`` to every record (``-`` outside a request); never drops one."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id() or NO_REQUEST_ID
        return True
