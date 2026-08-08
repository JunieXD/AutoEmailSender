from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys

from auto_email_sender_cli.errors import (
    RuntimeProtocolMismatchError,
    RuntimeUnavailableError,
)
from auto_email_sender_cli.version import PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class RuntimeDescriptor:
    protocol_version: str
    app_version: str
    base_url: str
    access_token: str
    desktop_pid: int
    started_at: str

    @classmethod
    def from_mapping(cls, value: object) -> RuntimeDescriptor:
        if not isinstance(value, dict):
            raise ValueError("runtime descriptor must be an object")
        string_fields = (
            "protocol_version",
            "app_version",
            "base_url",
            "access_token",
            "started_at",
        )
        strings: dict[str, str] = {}
        for field in string_fields:
            raw = value.get(field)
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError(f"runtime descriptor field {field} must be a non-empty string")
            strings[field] = raw.strip()
        desktop_pid = value.get("desktop_pid")
        if not isinstance(desktop_pid, int) or isinstance(desktop_pid, bool) or desktop_pid <= 0:
            raise ValueError("runtime descriptor field desktop_pid must be a positive integer")
        return cls(desktop_pid=desktop_pid, **strings)


def get_runtime_file_path() -> Path:
    override = os.getenv("AUTO_EMAIL_SENDER_RUNTIME_FILE")
    if override and override.strip():
        return Path(override).expanduser().resolve()

    data_dir = os.getenv("AUTO_EMAIL_SENDER_DATA_DIR")
    if data_dir and data_dir.strip():
        return Path(data_dir).expanduser().resolve() / "agent" / "runtime.json"

    if sys.platform == "darwin":
        base = (
            Path.home()
            / "Library"
            / "Application Support"
            / "auto-email-sender-desktop"
        )
    elif sys.platform == "win32":
        app_data = os.getenv("APPDATA")
        base = Path(app_data) if app_data else Path.home() / "AppData" / "Roaming"
        base = base / "auto-email-sender-desktop"
    else:
        state_home = os.getenv("XDG_STATE_HOME")
        base = (
            Path(state_home).expanduser()
            if state_home
            else Path.home() / ".local" / "state"
        ) / "auto-email-sender"
    return base / "agent" / "runtime.json"


def load_runtime_descriptor() -> RuntimeDescriptor:
    base_url = os.getenv("AUTO_EMAIL_SENDER_BASE_URL")
    token = os.getenv("AUTO_EMAIL_SENDER_AGENT_TOKEN")
    if base_url and token:
        return RuntimeDescriptor.from_mapping(
            {
                "protocol_version": os.getenv("AUTO_EMAIL_SENDER_PROTOCOL_VERSION", PROTOCOL_VERSION),
                "app_version": os.getenv("AUTO_EMAIL_SENDER_APP_VERSION", "development"),
                "base_url": base_url,
                "access_token": token,
                "desktop_pid": os.getpid(),
                "started_at": "environment",
            },
        )

    path = get_runtime_file_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeUnavailableError(
            "Auto Email Sender 当前未运行。请先手动打开软件，"
            "等待本地服务加载完成后再重试。",
        ) from exc
    except OSError as exc:
        raise RuntimeUnavailableError(f"无法读取本地运行信息：{exc}") from exc

    try:
        return RuntimeDescriptor.from_mapping(json.loads(raw))
    except (json.JSONDecodeError, ValueError) as exc:
        raise RuntimeUnavailableError("本地运行信息无效，请在个人中心修复命令行支持。") from exc


def process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _windows_process_is_running(pid)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_process_is_running(pid: int) -> bool:
    """Check process liveness without relying on unsupported Windows signal 0."""

    process_query_limited_information = 0x1000
    still_active = 259
    error_access_denied = 5
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    get_exit_code_process = kernel32.GetExitCodeProcess
    get_exit_code_process.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    get_exit_code_process.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    handle = open_process(process_query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() == error_access_denied
    try:
        exit_code = wintypes.DWORD()
        if not get_exit_code_process(handle, ctypes.byref(exit_code)):
            return ctypes.get_last_error() == error_access_denied
        return exit_code.value == still_active
    finally:
        close_handle(handle)


def ensure_runtime_descriptor() -> RuntimeDescriptor:
    """Return a live, protocol-compatible runtime published by the desktop app."""

    if _environment_runtime_configured():
        return ensure_runtime_protocol_compatible(load_runtime_descriptor())

    descriptor = load_runtime_descriptor()
    if not process_is_running(descriptor.desktop_pid):
        raise RuntimeUnavailableError(
            "Auto Email Sender 当前未运行。请先手动打开软件，"
            "等待本地服务加载完成后再重试。",
        )
    return ensure_runtime_protocol_compatible(descriptor)


def _environment_runtime_configured() -> bool:
    return bool(
        os.getenv("AUTO_EMAIL_SENDER_BASE_URL")
        and os.getenv("AUTO_EMAIL_SENDER_AGENT_TOKEN")
    )


def ensure_runtime_protocol_compatible(descriptor: RuntimeDescriptor) -> RuntimeDescriptor:
    if descriptor.protocol_version != PROTOCOL_VERSION:
        raise RuntimeProtocolMismatchError(
            expected=PROTOCOL_VERSION,
            actual=descriptor.protocol_version,
        )
    return descriptor
