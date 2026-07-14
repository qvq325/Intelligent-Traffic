"""Development entry point for the web application."""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from backend.app import app


ENV_FILE = Path(__file__).resolve().with_name(".env")


def main() -> None:
    load_dotenv(ENV_FILE)
    host = os.getenv("VIDEOTEST_HOST", "127.0.0.1")
    port = int(os.getenv("VIDEOTEST_PORT", "8000"))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
