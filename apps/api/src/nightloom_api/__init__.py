"""Nightloom HTTP API."""

from importlib.metadata import PackageNotFoundError, version

DIST_NAME = "nightloom-api"


def get_version() -> str:
    """Return the installed package version, or ``"0.0.0"`` if it is not installed."""
    try:
        return version(DIST_NAME)
    except PackageNotFoundError:
        return "0.0.0"


__version__ = get_version()

__all__ = ["DIST_NAME", "__version__", "get_version"]
