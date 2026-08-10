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

from app.core.agent_runtime_descriptor import (
    cleanup_owned_runtime_descriptor,
    get_runtime_id,
)
from app.core.backend_role import BACKEND_ROLES, BackendRole, set_backend_role
from app.core.config import get_settings
from app.core.instance_lock import (
    BackendInstanceAlreadyRunningError,
    BackendInstanceLock,
    BackendWorkerAlreadyRunningError,
    BackendWorkerLock,
)
from app.core.process_liveness import process_is_running
from app.core.startup_logging import write_startup_phase_log
from app.services.worker_process import run_worker_process


PACKAGED_RUNTIME_SHARED_SELF_CHECK_MODULES = (
    "app.core.backend_role",
    "app.core.runtime_group",
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
PACKAGED_RUNTIME_API_SELF_CHECK_MODULES = (
    "main",
)
PACKAGED_RUNTIME_WORKER_SELF_CHECK_MODULES = (
    "app.services.worker_process",
    "app.services.runtime_manager",
    "app.modules.campaigns.public",
    "app.modules.communications.public",
    "app.modules.crawler.public",
    "app.modules.matching.public",
    "app.modules.professors.enrichment.public",
)
PACKAGED_RUNTIME_SELF_CHECK_MODULES = tuple(
    dict.fromkeys(
        (
            *PACKAGED_RUNTIME_SHARED_SELF_CHECK_MODULES,
            *PACKAGED_RUNTIME_API_SELF_CHECK_MODULES,
            *PACKAGED_RUNTIME_WORKER_SELF_CHECK_MODULES,
        )
    )
)
DESKTOP_PARENT_PID_ENV = "AUTO_EMAIL_SENDER_DESKTOP_PID"
DESKTOP_PARENT_POLL_SECONDS = 1.0


def parse_desktop_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Auto Email Sender desktop backend.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--role", choices=BACKEND_ROLES, default="combined")
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


def packaged_runtime_self_check_modules(role: BackendRole) -> tuple[str, ...]:
    role_modules = {
        "api": PACKAGED_RUNTIME_API_SELF_CHECK_MODULES,
        "worker": PACKAGED_RUNTIME_WORKER_SELF_CHECK_MODULES,
        "combined": (
            *PACKAGED_RUNTIME_API_SELF_CHECK_MODULES,
            *PACKAGED_RUNTIME_WORKER_SELF_CHECK_MODULES,
        ),
    }[role]
    return tuple(
        dict.fromkeys((*PACKAGED_RUNTIME_SHARED_SELF_CHECK_MODULES, *role_modules))
    )


def run_packaged_runtime_self_check(role: BackendRole = "combined") -> int:
    set_backend_role(role)
    for module_name in packaged_runtime_self_check_modules(role):
        importlib.import_module(module_name)
    print(f"packaged runtime self-check role={role} ok")
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
        raise SystemExit(run_packaged_runtime_self_check(args.role))
    if args.document_self_check is not None:
        raise SystemExit(run_packaged_document_self_check(args.document_self_check))

    role: BackendRole = args.role
    set_backend_role(role)
    options = build_uvicorn_options()
    write_startup_phase_log(
        "desktop_entry.start",
        detail=f"role={role} host={options['host']} port={options['port']}",
    )
    data_dir = get_settings().data_dir
    runtime_id = get_runtime_id()
    locks = []
    if role in {"api", "combined"}:
        locks.append(BackendInstanceLock(data_dir))
    if role in {"worker", "combined"}:
        locks.append(BackendWorkerLock(data_dir))
    try:
        for role_lock in locks:
            role_lock.acquire()
    except (BackendInstanceAlreadyRunningError, BackendWorkerAlreadyRunningError) as exc:
        for role_lock in reversed(locks):
            role_lock.release()
        write_startup_phase_log("desktop_entry.instance_conflict", detail=str(exc))
        raise SystemExit(str(exc)) from None
    try:
        if role == "worker":
            asyncio.run(run_worker_process(desktop_pid=get_desktop_parent_pid()))
        else:
            asyncio.run(
                serve_desktop_backend(
                    options,
                    desktop_pid=get_desktop_parent_pid(),
                )
            )
    finally:
        if role in {"api", "combined"}:
            cleanup_owned_runtime_descriptor(data_dir, runtime_id)
        for role_lock in reversed(locks):
            role_lock.release()


if __name__ == "__main__":
    main()
