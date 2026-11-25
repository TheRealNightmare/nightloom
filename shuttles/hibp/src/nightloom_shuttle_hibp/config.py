"""The operator's Have I Been Pwned API key: ``NIGHTLOOM_HIBP_API_KEY``.

Only the email Shuttle needs it (HIBP's ``breachedaccount`` endpoint is for
paid subscribers); the site Shuttle uses the free public breach list. The
key is read from the worker's environment, never stored with a case, never
logged and never shown. An empty or unset variable means "no key".
"""

import os
import re

from nightloom_sdk import ShuttleConfigurationError

KEY_VARIABLE = "NIGHTLOOM_HIBP_API_KEY"
# HIBP API keys are 32 hexadecimal characters.
KEY_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")


def load_api_key() -> str | None:
    """The configured key, ``None`` if there is none; a malformed one is a configuration error."""
    value = os.environ.get(KEY_VARIABLE, "").strip()
    if not value:
        return None
    if not KEY_PATTERN.fullmatch(value):
        raise ShuttleConfigurationError(
            f"{KEY_VARIABLE} must be an HIBP API key: 32 hexadecimal characters."
        )
    return value


__all__ = ["KEY_PATTERN", "KEY_VARIABLE", "load_api_key"]
