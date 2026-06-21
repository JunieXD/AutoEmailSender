from __future__ import annotations

import argparse
import importlib
from collections.abc import Sequence
from typing import Any

import uvicorn

from app.core.startup_logging import write_startup_phase_log


PACKAGED_RUNTIME_SELF_CHECK_MODULES = (
    "main",
    "aiosqlite",
    "tiktoken",
    "tiktoken_ext.openai_public",
    "socksio",
    "langchain_openai",
    "openai",
    "httpx",
    "markitdown",
    "mammoth",
    "pdfminer",
    "pdfplumber",
    "pypdf",
    "playwright.async_api",
    "docx",
    "openpyxl",
)


def parse_desktop_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Auto Email Sender desktop backend.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--self-check", action="store_true")
    return parser.parse_args(argv)


def build_uvicorn_options(argv: Sequence[str] | None = None) -> dict[str, Any]:
    args = parse_desktop_args(argv)
    return {
        "app": "main:app",
        "host": args.host,
        "port": args.port,
        "reload": False,
    }


def run_packaged_runtime_self_check() -> int:
    for module_name in PACKAGED_RUNTIME_SELF_CHECK_MODULES:
        importlib.import_module(module_name)

    import tiktoken

    tiktoken.get_encoding("o200k_base").encode("packaged runtime self check")
    print("packaged runtime self-check ok")
    return 0


def main() -> None:
    args = parse_desktop_args()
    if args.self_check:
        raise SystemExit(run_packaged_runtime_self_check())

    options = build_uvicorn_options()
    app_path = options.pop("app")
    write_startup_phase_log(
        "desktop_entry.start",
        detail=f"host={options['host']} port={options['port']}",
    )
    uvicorn.run(app_path, **options)


if __name__ == "__main__":
    main()
