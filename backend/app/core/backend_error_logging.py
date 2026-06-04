from __future__ import annotations

import traceback
from datetime import UTC, datetime

from app.core.config import get_settings

BACKEND_ERROR_LOG_NAME = "backend-errors.log"

def write_backend_error_log(
    *,
    request_id: str,
    method: str,
    path: str,
    exc: BaseException,
) -> None:
    """Persist unexpected backend exceptions for diagnostic export."""

    try:
        log_dir = get_settings().data_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).isoformat()
        traceback_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        entry = (
            f"[{timestamp}] request_id={request_id} {method} {path}\n"
            f"{traceback_text}\n"
        )
        with (log_dir / BACKEND_ERROR_LOG_NAME).open("a", encoding="utf-8", newline="\n") as file:
            file.write(entry)
    except Exception:
        return
