from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from ipaddress import ip_address
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

import httpx

from auto_email_sender_cli.errors import (
    RuntimeProtocolMismatchError,
    RuntimeUnavailableError,
)
from auto_email_sender_cli.version import PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class RuntimeProcessDescriptor:
    pid: int
    started_at: str

    @classmethod
    def from_mapping(cls, value: object, *, field: str) -> RuntimeProcessDescriptor:
        if not isinstance(value, dict):
            raise ValueError(f"runtime descriptor field {field} must be an object")
        pid = value.get("pid")
        started_at = value.get("started_at")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise ValueError(f"runtime descriptor field {field}.pid must be a positive integer")
        if not isinstance(started_at, str) or not started_at.strip():
            raise ValueError(f"runtime descriptor field {field}.started_at must be a non-empty string")
        return cls(pid=pid, started_at=started_at.strip())


@dataclass(frozen=True, slots=True)
class RuntimeDescriptor:
    protocol_version: str
    app_version: str
    runtime_id: str
    base_url: str
    access_token: str
    desktop: RuntimeProcessDescriptor
    backend: RuntimeProcessDescriptor
    published_at: str

    @classmethod
    def from_mapping(cls, value: object) -> RuntimeDescriptor:
        if not isinstance(value, dict):
            raise ValueError("runtime descriptor must be an object")
        string_fields = (
            "protocol_version",
            "app_version",
            "runtime_id",
            "base_url",
            "access_token",
            "published_at",
        )
        strings: dict[str, str] = {}
        for field in string_fields:
            raw = value.get(field)
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError(f"runtime descriptor field {field} must be a non-empty string")
            strings[field] = raw.strip()
        return cls(
            desktop=RuntimeProcessDescriptor.from_mapping(value.get("desktop"), field="desktop"),
            backend=RuntimeProcessDescriptor.from_mapping(value.get("backend"), field="backend"),
            **strings,
        )

    @property
    def desktop_pid(self) -> int:
        return self.desktop.pid

    @property
    def backend_pid(self) -> int:
        return self.backend.pid


@dataclass(frozen=True, slots=True)
class RuntimeProbe:
    desktop_process_running: bool
    backend_process_running: bool
    backend_reachable: bool
    runtime_matches: bool
    backend_ready: bool
    backend_state: str | None = None
    message: str | None = None


def create_runtime_http_client(
    *,
    base_url: str | None,
    timeout: float,
) -> httpx.Client:
    """Create the transport used only for the desktop runtime API.

    File-based runtime descriptors are published by the desktop app and are
    loopback-only by contract. Explicit non-loopback development overrides
    retain HTTPX's existing environment-proxy behavior.
    """

    effective_base_url = base_url
    if effective_base_url is None and _environment_runtime_configured():
        effective_base_url = os.getenv("AUTO_EMAIL_SENDER_BASE_URL")
    trust_env = bool(effective_base_url) and not _is_loopback_url(effective_base_url)
    return httpx.Client(timeout=timeout, trust_env=trust_env)


def _is_loopback_url(value: str) -> bool:
    try:
        hostname = urlsplit(value).hostname
    except ValueError:
        return False
    if hostname is None:
        return False
    normalized = hostname.rstrip(".").lower()
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return True
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


def _is_desktop_runtime_base_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme.lower() == "http"
        and port is not None
        and port > 0
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
        and "?" not in value
        and "#" not in value
        and _is_loopback_url(value)
    )


def get_runtime_file_path() -> Path:
    override = os.getenv("AUTO_EMAIL_SENDER_RUNTIME_FILE")
    if override and override.strip():
        return Path(override).expanduser().resolve()

    data_dir = os.getenv("AUTO_EMAIL_SENDER_DATA_DIR")
    if data_dir and data_dir.strip():
        return Path(data_dir).expanduser().resolve() / "agent" / "runtime.json"

    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "auto-email-sender-desktop"
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
        desktop_pid = _positive_env_pid("AUTO_EMAIL_SENDER_DESKTOP_PID", os.getpid())
        backend_pid = _positive_env_pid("AUTO_EMAIL_SENDER_BACKEND_PID", os.getpid())
        return RuntimeDescriptor.from_mapping(
            {
                "protocol_version": os.getenv("AUTO_EMAIL_SENDER_PROTOCOL_VERSION", PROTOCOL_VERSION),
                "app_version": os.getenv("AUTO_EMAIL_SENDER_APP_VERSION", "development"),
                "runtime_id": os.getenv("AUTO_EMAIL_SENDER_RUNTIME_ID", "environment"),
                "base_url": base_url,
                "access_token": token,
                "desktop": {"pid": desktop_pid, "started_at": "environment"},
                "backend": {"pid": backend_pid, "started_at": "environment"},
                "published_at": "environment",
            },
        )

    path = get_runtime_file_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeUnavailableError(
            "Auto Email Sender 当前未运行。请先手动打开软件，等待本地服务加载完成后再重试。",
        ) from exc
    except OSError as exc:
        raise RuntimeUnavailableError(f"无法读取本地运行信息：{exc}") from exc

    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("runtime descriptor must be an object")
        protocol_version = payload.get("protocol_version")
        if isinstance(protocol_version, str) and protocol_version != PROTOCOL_VERSION:
            raise RuntimeProtocolMismatchError(
                expected=PROTOCOL_VERSION,
                actual=protocol_version,
            )
        descriptor = RuntimeDescriptor.from_mapping(payload)
        if not _is_desktop_runtime_base_url(descriptor.base_url):
            raise ValueError("runtime descriptor base_url must be a local HTTP origin")
        return descriptor
    except RuntimeProtocolMismatchError:
        raise
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
    """Check process liveness without sending a Windows termination signal."""

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


def probe_runtime_descriptor(
    descriptor: RuntimeDescriptor,
    *,
    timeout: float = 1.0,
    http_client: httpx.Client | None = None,
) -> RuntimeProbe:
    desktop_running = process_is_running(descriptor.desktop_pid)
    backend_running = process_is_running(descriptor.backend_pid)
    owned_client: httpx.Client | None = None
    try:
        client = http_client
        if client is None:
            owned_client = create_runtime_http_client(
                base_url=descriptor.base_url,
                timeout=timeout,
            )
            client = owned_client
        response = client.get(
            f"{descriptor.base_url.rstrip('/')}/api/agent/v1/runtime",
            headers={"Authorization": f"Bearer {descriptor.access_token}"},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        return RuntimeProbe(
            desktop_process_running=desktop_running,
            backend_process_running=backend_running,
            backend_reachable=False,
            runtime_matches=False,
            backend_ready=False,
            message=str(exc),
        )
    finally:
        if owned_client is not None:
            owned_client.close()

    if not response.is_success:
        return RuntimeProbe(
            desktop_process_running=desktop_running,
            backend_process_running=backend_running,
            backend_reachable=True,
            runtime_matches=False,
            backend_ready=False,
            message=f"runtime handshake returned HTTP {response.status_code}",
        )
    try:
        payload = response.json()
    except ValueError:
        payload = None
    matches = bool(
        isinstance(payload, dict)
        and payload.get("runtime_id") == descriptor.runtime_id
        and payload.get("protocol_version") == descriptor.protocol_version
        and payload.get("app_version") == descriptor.app_version
        and payload.get("backend_pid") == descriptor.backend_pid
        and payload.get("desktop_pid") == descriptor.desktop_pid
    )
    backend_state = payload.get("state") if isinstance(payload, dict) else None
    return RuntimeProbe(
        desktop_process_running=desktop_running,
        backend_process_running=backend_running,
        backend_reachable=True,
        runtime_matches=matches,
        backend_ready=matches and backend_state == "ready",
        backend_state=backend_state if isinstance(backend_state, str) else None,
        message=None if matches else "本地服务身份与运行描述不一致。",
    )


def ensure_runtime_descriptor(
    *,
    http_client: httpx.Client | None = None,
) -> RuntimeDescriptor:
    """Return an authenticated, ready runtime published by the desktop app."""

    descriptor = ensure_runtime_protocol_compatible(load_runtime_descriptor())
    if _environment_runtime_configured():
        return descriptor

    probe = probe_runtime_descriptor(descriptor, http_client=http_client)
    if not probe.desktop_process_running:
        raise RuntimeUnavailableError(
            "Auto Email Sender 桌面进程已停止，运行信息已过期。请重新打开软件后重试。",
        )
    if not probe.backend_reachable:
        raise RuntimeUnavailableError(
            "Auto Email Sender 正在启动或本地服务暂时无法连接。请等待加载完成后重试。",
        )
    if not probe.runtime_matches:
        raise RuntimeUnavailableError(
            "本地服务身份与运行信息不一致，请等待桌面应用完成恢复后重试。",
        )
    if not probe.backend_ready:
        raise RuntimeUnavailableError(
            "Auto Email Sender 本地服务尚未就绪。请等待软件加载完成后再重试。",
        )
    return descriptor


def _environment_runtime_configured() -> bool:
    return bool(os.getenv("AUTO_EMAIL_SENDER_BASE_URL") and os.getenv("AUTO_EMAIL_SENDER_AGENT_TOKEN"))


def ensure_runtime_protocol_compatible(descriptor: RuntimeDescriptor) -> RuntimeDescriptor:
    if descriptor.protocol_version != PROTOCOL_VERSION:
        raise RuntimeProtocolMismatchError(expected=PROTOCOL_VERSION, actual=descriptor.protocol_version)
    return descriptor


def _positive_env_pid(name: str, fallback: int) -> int:
    try:
        value = int(os.getenv(name, ""))
    except ValueError:
        return fallback
    return value if value > 0 else fallback
