"""Development entry point for the web application."""

from __future__ import annotations

import os

import uvicorn

from backend.app import app


def main() -> None:
    host = os.getenv("VIDEOTEST_HOST", "127.0.0.1")
    port = int(os.getenv("VIDEOTEST_PORT", "8000"))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
