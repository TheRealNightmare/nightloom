"""Short, credential-free descriptions of dependency errors.

Driver exceptions can quote connection URLs, user names or passwords. Only the
output of :func:`describe_error` may be returned to clients or logged.
"""

import re
from collections.abc import Iterable

MAX_LENGTH = 120
REDACTED = "***"

# Anything shaped like ``scheme://...`` (connection URLs, endpoints).
_URL = re.compile(r"""[A-Za-z][A-Za-z0-9+.\-]*://[^\s'"<>()\[\]]*""")
# ``user:password@host`` fragments without a scheme.
_USERINFO = re.compile(r"""[^\s/@:'"]+:[^\s/@'"]*@""")


def describe_error(exc: BaseException, secrets: Iterable[str] = ()) -> str:
    """Return ``"<ExceptionType>: <first line of message>"``, redacted and truncated.

    Known secret values are replaced first, then any URL or ``user:pass@``
    fragment, so a credential never survives even if it was not listed.
    """
    name = type(exc).__name__
    lines = str(exc).strip().splitlines()
    message = lines[0].strip() if lines else ""
    # Longest first, so a URL is replaced before the password inside it.
    for secret in sorted(set(secrets), key=len, reverse=True):
        if secret:
            message = message.replace(secret, REDACTED)
    message = _URL.sub("<url>", message)
    message = _USERINFO.sub(f"{REDACTED}@", message)

    text = f"{name}: {message}" if message else name
    if len(text) > MAX_LENGTH:
        text = text[: MAX_LENGTH - 1].rstrip() + "…"
    return text
