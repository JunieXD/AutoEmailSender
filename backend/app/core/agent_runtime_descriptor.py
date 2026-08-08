from __future__ import annotations

import json
import os
from pathlib import Path


RUNTIME_ID_ENV = "AUTO_EMAIL_SENDER_RUNTIME_ID"
DESKTOP_PID_ENV = "AUTO_EMAIL_SENDER_DESKTOP_PID"
RUNTIME_PROTOCOL_VERSION = "3"


def get_runtime_id() -> str:
    value = os.getenv(RUNTIME_ID_ENV, "").strip()
    return value or "development"


def get_desktop_pid() -> int:
    value = os.getenv(DESKTOP_PID_ENV, "").strip()
    try:
        pid = int(value)
    except ValueError:
        return os.getppid()
    return pid if pid > 0 else os.getppid()


def get_agent_runtime_file_path(data_dir: Path) -> Path:
    return data_dir / "agent" / "runtime.json"


def cleanup_owned_runtime_descriptor(data_dir: Path, runtime_id: str) -> bool:
    if not runtime_id:
        return False
    runtime_path = get_agent_runtime_file_path(data_dir)
    try:
        payload = json.loads(runtime_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict) or payload.get("runtime_id") != runtime_id:
        return False
    try:
        runtime_path.unlink(missing_ok=True)
    except OSError:
        return False
    return True


__all__ = [
    "DESKTOP_PID_ENV",
    "RUNTIME_ID_ENV",
    "RUNTIME_PROTOCOL_VERSION",
    "cleanup_owned_runtime_descriptor",
    "get_agent_runtime_file_path",
    "get_desktop_pid",
    "get_runtime_id",
]
