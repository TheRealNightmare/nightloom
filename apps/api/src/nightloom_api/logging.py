"""Minimal stdlib logging setup."""

import logging

from nightloom_api.request_id import RequestIdLogFilter

# ``request_id`` is filled in by RequestIdLogFilter ("-" outside a request).
LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s [%(request_id)s]: %(message)s"
# Libraries whose INFO lines quote request URLs, query strings included (a
# searched name, or an API key passed as a parameter): warnings only.
QUIET_LOGGERS = ("httpx", "httpcore")


def configure_logging(level: str) -> None:
    """Configure the root logger with a single stream handler at ``level``.

    The handler tags every record with the current request ID. The loggers
    in :data:`QUIET_LOGGERS` never log below WARNING.
    """
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        handler.addFilter(RequestIdLogFilter())
        root.addHandler(handler)
    root.setLevel(level)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(max(logging.WARNING, root.level))
