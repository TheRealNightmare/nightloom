"""Minimal stdlib logging setup."""

import logging

from nightloom_api.request_id import RequestIdLogFilter

# ``request_id`` is filled in by RequestIdLogFilter ("-" outside a request).
LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s [%(request_id)s]: %(message)s"


def configure_logging(level: str) -> None:
    """Configure the root logger with a single stream handler at ``level``.

    The handler tags every record with the current request ID.
    """
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        handler.addFilter(RequestIdLogFilter())
        root.addHandler(handler)
    root.setLevel(level)
