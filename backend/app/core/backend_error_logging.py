from __future__ import annotations

import traceback
from datetime import UTC, datetime

from app.core.config import get_settings
from app.core.sqlite_diagnostics import sqlite_lock_diagnostic_line

BACKEND_ERROR_LOG_NAME = "backend-errors.log"


def _append_backend_error_entry(entry: str) -> None:
    log_dir = get_settings().data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / BACKEND_ERROR_LOG_NAME).open(
        "a", encoding="utf-8", newline="\n"
    ) as file:
        file.write(entry)


def write_backend_error_log(
    *,
    request_id: str,
    method: str,
    path: str,
    exc: BaseException,
) -> None:
    """Persist unexpected backend exceptions for diagnostic export."""

    try:
        timestamp = datetime.now(UTC).isoformat()
        traceback_text = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )
        diagnostic_line = sqlite_lock_diagnostic_line(exc)
        diagnostic_text = f"{diagnostic_line}\n" if diagnostic_line else ""
        entry = (
            f"[{timestamp}] request_id={request_id} {method} {path}\n"
            f"{diagnostic_text}"
            f"{traceback_text}\n"
        )
        _append_backend_error_entry(entry)
    except Exception:
        return


def write_backend_worker_error_log(
    *,
    worker_name: str,
    exc: BaseException,
) -> None:
    try:
        timestamp = datetime.now(UTC).isoformat()
        traceback_text = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )
        diagnostic_line = sqlite_lock_diagnostic_line(exc)
        diagnostic_text = f"{diagnostic_line}\n" if diagnostic_line else ""
        entry = (
            f"[{timestamp}] worker_name={worker_name}\n"
            f"{diagnostic_text}"
            f"{traceback_text}\n"
        )
        _append_backend_error_entry(entry)
    except Exception:
        return
