from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, NotRequired, TypedDict, cast

from app.core.process_liveness import process_is_running


RuntimeProcessRole = Literal["api", "worker"]
RuntimeProcessState = Literal["starting", "ready", "stopping", "error"]
RUNTIME_PROCESS_STATUS_PROTOCOL_VERSION = "2"


class RuntimeSubsystemStatus(TypedDict):
    last_started_at: str | None
    last_succeeded_at: str | None
    last_failed_at: str | None
    consecutive_failures: int
    error: str | None


class RuntimeProcessStatus(TypedDict):
    protocol_version: str
    runtime_id: str
    role: RuntimeProcessRole
    pid: int
    generation: str
    state: RuntimeProcessState
    started_at: str
    updated_at: str
    error: str | None
    heartbeat_at: NotRequired[str]
    health: NotRequired[Literal["healthy", "degraded"]]
    draining: NotRequired[bool]
    subsystems: NotRequired[dict[str, RuntimeSubsystemStatus]]


def get_runtime_process_status_path(data_dir: Path, role: RuntimeProcessRole) -> Path:
    return data_dir / "runtime" / f"{role}.json"


def write_runtime_process_status(
    data_dir: Path,
    *,
    runtime_id: str,
    role: RuntimeProcessRole,
    generation: str,
    state: RuntimeProcessState,
    started_at: datetime,
    error: str | None = None,
    health: Literal["healthy", "degraded"] | None = None,
    draining: bool | None = None,
    subsystems: dict[str, RuntimeSubsystemStatus] | None = None,
) -> RuntimeProcessStatus:
    now = datetime.now(UTC)
    payload: RuntimeProcessStatus = {
        "protocol_version": RUNTIME_PROCESS_STATUS_PROTOCOL_VERSION,
        "runtime_id": runtime_id,
        "role": role,
        "pid": os.getpid(),
        "generation": generation,
        "state": state,
        "started_at": started_at.astimezone(UTC).isoformat(),
        "updated_at": now.isoformat(),
        "error": error,
    }
    if role == "worker":
        payload["heartbeat_at"] = now.isoformat()
        payload["health"] = health or "healthy"
        payload["draining"] = bool(draining)
        payload["subsystems"] = subsystems or {}
    status_path = get_runtime_process_status_path(data_dir, role)
    status_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary_path = status_path.parent / f".{role}-{uuid.uuid4().hex}.tmp"
    try:
        temporary_path.write_text(
            f"{json.dumps(payload, ensure_ascii=False, sort_keys=True)}\n",
            encoding="utf-8",
        )
        try:
            temporary_path.chmod(0o600)
        except OSError:
            pass
        temporary_path.replace(status_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return payload


def read_runtime_process_status(
    data_dir: Path,
    role: RuntimeProcessRole,
) -> RuntimeProcessStatus | None:
    status_path = get_runtime_process_status_path(data_dir, role)
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    required = {
        "protocol_version",
        "runtime_id",
        "role",
        "pid",
        "generation",
        "state",
        "started_at",
        "updated_at",
        "error",
    }
    if not required.issubset(payload):
        return None
    return cast(RuntimeProcessStatus, payload)


def cleanup_owned_runtime_process_status(
    data_dir: Path,
    *,
    runtime_id: str,
    role: RuntimeProcessRole,
    generation: str,
) -> bool:
    current = read_runtime_process_status(data_dir, role)
    if (
        current is None
        or current.get("runtime_id") != runtime_id
        or current.get("generation") != generation
        or current.get("pid") != os.getpid()
    ):
        return False
    try:
        get_runtime_process_status_path(data_dir, role).unlink(missing_ok=True)
    except OSError:
        return False
    return True


def require_ready_api_leader(
    data_dir: Path,
    *,
    runtime_id: str,
    api_pid: int,
) -> RuntimeProcessStatus:
    status = read_runtime_process_status(data_dir, "api")
    if status is None:
        raise RuntimeError("API runtime status is missing; start and await the API before Worker")
    if status.get("protocol_version") != RUNTIME_PROCESS_STATUS_PROTOCOL_VERSION:
        raise RuntimeError("API runtime status protocol is not supported by this Worker")
    if status.get("runtime_id") != runtime_id:
        raise RuntimeError("API runtime id does not match the Worker runtime group")
    if status.get("pid") != api_pid:
        raise RuntimeError("API pid does not match the Worker runtime group")
    if status.get("state") != "ready":
        raise RuntimeError("API is not ready; Worker startup is not allowed")
    if not process_is_running(api_pid):
        raise RuntimeError("API process is no longer running")
    return status


__all__ = [
    "RUNTIME_PROCESS_STATUS_PROTOCOL_VERSION",
    "RuntimeProcessRole",
    "RuntimeProcessState",
    "RuntimeProcessStatus",
    "RuntimeSubsystemStatus",
    "cleanup_owned_runtime_process_status",
    "get_runtime_process_status_path",
    "read_runtime_process_status",
    "require_ready_api_leader",
    "write_runtime_process_status",
]
