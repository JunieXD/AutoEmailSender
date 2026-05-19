from __future__ import annotations

import argparse
from collections.abc import Sequence
from typing import Any

import uvicorn

from app.core.startup_logging import write_startup_phase_log


def build_uvicorn_options(argv: Sequence[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description="Run Auto Email Sender desktop backend.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    return {
        "app": "main:app",
        "host": args.host,
        "port": args.port,
        "reload": False,
    }


def main() -> None:
    options = build_uvicorn_options()
    app_path = options.pop("app")
    write_startup_phase_log(
        "desktop_entry.start",
        detail=f"host={options['host']} port={options['port']}",
    )
    uvicorn.run(app_path, **options)


if __name__ == "__main__":
    main()
