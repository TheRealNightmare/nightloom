"""The operator's optional GitHub token: ``NIGHTLOOM_GITHUB_TOKEN``.

Without one, GitHub allows 60 API requests an hour from the server's
address; with one, 5,000. A fine-grained token with no permissions at all
(public data only) is enough and the safest. The token is read from the
worker's environment, never stored with a case, logged or shown. An empty
or unset variable means "no token".
"""

import os
import re

from nightloom_sdk import ShuttleConfigurationError

TOKEN_VARIABLE = "NIGHTLOOM_GITHUB_TOKEN"  # noqa: S105 (a variable name, not a token)
# Classic (ghp_...) and fine-grained (github_pat_...) personal access tokens, and the
# other gh?_ token kinds.
TOKEN_PATTERN = re.compile(r"^(gh[pousr]_[A-Za-z0-9]{30,251}|github_pat_[A-Za-z0-9_]{22,255})$")


def load_token() -> str | None:
    """The configured token, ``None`` if there is none; a malformed one is a configuration error."""
    value = os.environ.get(TOKEN_VARIABLE, "").strip()
    if not value:
        return None
    if not TOKEN_PATTERN.fullmatch(value):
        raise ShuttleConfigurationError(
            f"{TOKEN_VARIABLE} must be a GitHub token (ghp_... or github_pat_...)."
        )
    return value


__all__ = ["TOKEN_PATTERN", "TOKEN_VARIABLE", "load_token"]
