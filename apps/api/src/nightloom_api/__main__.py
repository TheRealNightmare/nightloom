"""Run the development server: ``python -m nightloom_api``."""

import uvicorn

from nightloom_api.config import get_settings

HOST = "127.0.0.1"
PORT = 8000


def main() -> None:
    """Start uvicorn on 127.0.0.1:8000, reloading on changes in development."""
    settings = get_settings()
    uvicorn.run(
        "nightloom_api.main:app",
        host=HOST,
        port=PORT,
        reload=settings.env == "development",
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
