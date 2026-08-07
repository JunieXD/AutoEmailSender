from __future__ import annotations

import argparse
import asyncio
import importlib
import os
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any

import uvicorn

from app.core.config import get_settings
from app.core.instance_lock import (
    BackendInstanceAlreadyRunningError,
    BackendInstanceLock,
)
from app.core.process_liveness import process_is_running
from app.core.startup_logging import write_startup_phase_log


PACKAGED_RUNTIME_SELF_CHECK_MODULES = (
    "main",
    "aiosqlite",
    "socksio",
    "openai",
    "httpx",
    "tldextract",
    "app.services.document_extraction",
    "defusedxml",
    "lxml.etree",
    "mammoth",
    "pdfminer",
    "pdfplumber",
    "pypdf",
    "playwright.async_api",
    "playwright.sync_api",
    "docx",
    "openpyxl",
)
DESKTOP_PARENT_PID_ENV = "AUTO_EMAIL_SENDER_DESKTOP_PID"
DESKTOP_PARENT_POLL_SECONDS = 1.0


def parse_desktop_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Auto Email Sender desktop backend.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--document-self-check", type=Path)
    return parser.parse_args(argv)


def build_uvicorn_options(argv: Sequence[str] | None = None) -> dict[str, Any]:
    args = parse_desktop_args(argv)
    return {
        "app": "main:app",
        "host": args.host,
        "port": args.port,
        "reload": False,
    }


def get_desktop_parent_pid() -> int | None:
    raw_pid = os.getenv(DESKTOP_PARENT_PID_ENV, "").strip()
    if not raw_pid:
        return None
    try:
        pid = int(raw_pid)
    except ValueError:
        return None
    return pid if pid > 0 else None


async def watch_desktop_parent(
    server: uvicorn.Server,
    desktop_pid: int,
    *,
    poll_seconds: float = DESKTOP_PARENT_POLL_SECONDS,
) -> None:
    while not server.should_exit:
        await asyncio.sleep(poll_seconds)
        if process_is_running(desktop_pid):
            continue
        write_startup_phase_log(
            "desktop_entry.parent_stopped",
            detail=f"desktop_pid={desktop_pid}",
        )
        server.should_exit = True
        return


async def serve_desktop_backend(
    options: dict[str, Any],
    *,
    desktop_pid: int | None,
) -> None:
    app_path = options.pop("app")
    server = uvicorn.Server(uvicorn.Config(app_path, **options))
    parent_task = (
        asyncio.create_task(watch_desktop_parent(server, desktop_pid))
        if desktop_pid is not None
        else None
    )
    try:
        await server.serve()
    finally:
        if parent_task is not None:
            parent_task.cancel()
            with suppress(asyncio.CancelledError):
                await parent_task


def run_packaged_runtime_self_check() -> int:
    for module_name in PACKAGED_RUNTIME_SELF_CHECK_MODULES:
        importlib.import_module(module_name)
    print("packaged runtime self-check ok")
    return 0


def run_packaged_document_self_check(fixture_dir: Path) -> int:
    from app.services.file_storage import extract_text_from_document

    cases = {
        "borderless_form_resume.pdf": ("FORM-TABLE-SENTINEL", "| 时间", "| ----"),
        "docx_named_pdf.pdf": ("RICH-DOCX-SENTINEL",),
        "equation_resume.docx": ("EQUATION-DOCX-SENTINEL", "$x+1=2$", "$$E=mc\\^2$$"),
        "pdf_named_docx.docx": ("研究生申请简历",),
    }
    for file_name, sentinels in cases.items():
        path = fixture_dir / file_name
        content = extract_text_from_document(path.as_posix()) or ""
        missing = [sentinel for sentinel in sentinels if sentinel not in content]
        if missing:
            raise RuntimeError(f"packaged document self-check failed for {file_name}: {missing}")

    print("packaged document self-check ok")
    return 0


def main() -> None:
    args = parse_desktop_args()
    if args.self_check:
        raise SystemExit(run_packaged_runtime_self_check())
    if args.document_self_check is not None:
        raise SystemExit(run_packaged_document_self_check(args.document_self_check))

    options = build_uvicorn_options()
    write_startup_phase_log(
        "desktop_entry.start",
        detail=f"host={options['host']} port={options['port']}",
    )
    instance_lock = BackendInstanceLock(get_settings().data_dir)
    try:
        instance_lock.acquire()
    except BackendInstanceAlreadyRunningError as exc:
        write_startup_phase_log("desktop_entry.instance_conflict", detail=str(exc))
        raise SystemExit(str(exc)) from None
    try:
        asyncio.run(
            serve_desktop_backend(
                options,
                desktop_pid=get_desktop_parent_pid(),
            )
        )
    finally:
        instance_lock.release()


if __name__ == "__main__":
    main()
