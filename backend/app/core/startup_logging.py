from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path


def write_startup_phase_log(phase: str, *, detail: str | None = None) -> None:
    data_dir = os.environ.get("AUTO_EMAIL_SENDER_DATA_DIR")
    if not data_dir:
        print(f"[startup] {phase}{_format_detail(detail)}", file=sys.stderr)
        return

    try:
        log_path = Path(data_dir) / "logs" / "startup.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        line = f"[{datetime.now(UTC).isoformat()}] phase={phase}{_format_detail(detail)}\n"
        with log_path.open("a", encoding="utf-8", newline="\n") as file:
            file.write(line)
    except Exception as exc:
        print(f"[startup] failed to write phase log: {phase}: {exc}", file=sys.stderr)


def _format_detail(detail: str | None) -> str:
    return "" if detail is None else f" detail={detail}"
