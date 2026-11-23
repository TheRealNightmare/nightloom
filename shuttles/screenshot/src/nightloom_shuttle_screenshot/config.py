"""Where the browser service is: ``NIGHTLOOM_BROWSER_URL``."""

import os
from urllib.parse import urlsplit

from nightloom_sdk import ShuttleConfigurationError

# The `browser` Compose service (profile `screenshots`), on its internal network.
DEFAULT_BROWSER_URL = "ws://browser:3000/"


def load_browser_url() -> str:
    """The browser service's ``ws://`` address, checked."""
    value = os.environ.get("NIGHTLOOM_BROWSER_URL", "").strip() or DEFAULT_BROWSER_URL
    parts = urlsplit(value)
    if parts.scheme not in ("ws", "wss") or not parts.hostname:
        raise ShuttleConfigurationError(
            "NIGHTLOOM_BROWSER_URL must be a ws:// or wss:// address, such as ws://browser:3000/."
        )
    return value


__all__ = ["DEFAULT_BROWSER_URL", "load_browser_url"]
