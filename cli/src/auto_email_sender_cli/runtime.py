from __future__ import annotations

import os
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from auto_email_sender_cli.errors import RuntimeUnavailableError


class RuntimeDescriptor(BaseModel):
    model_config = ConfigDict(extra="ignore")

    protocol_version: str
    app_version: str
    base_url: str
    access_token: str = Field(min_length=1)
    desktop_pid: int = Field(gt=0)
    started_at: str


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


def get_agent_installation_file_path() -> Path:
    return get_runtime_file_path().with_name("installation.json")


def load_runtime_descriptor() -> RuntimeDescriptor:
    base_url = os.getenv("AUTO_EMAIL_SENDER_BASE_URL")
    token = os.getenv("AUTO_EMAIL_SENDER_AGENT_TOKEN")
    if base_url and token:
        return RuntimeDescriptor(
            protocol_version=os.getenv("AUTO_EMAIL_SENDER_PROTOCOL_VERSION", "1"),
            app_version=os.getenv("AUTO_EMAIL_SENDER_APP_VERSION", "development"),
            base_url=base_url,
            access_token=token,
            desktop_pid=os.getpid(),
            started_at="environment",
        )

    path = get_runtime_file_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeUnavailableError(
            f"没有找到本地运行信息：{path}。请启动 Auto Email Sender 或运行诊断。",
        ) from exc
    except OSError as exc:
        raise RuntimeUnavailableError(f"无法读取本地运行信息：{exc}") from exc

    try:
        return RuntimeDescriptor.model_validate_json(raw)
    except ValidationError as exc:
        raise RuntimeUnavailableError("本地运行信息无效，请在个人中心修复命令行支持。") from exc


def process_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def ensure_runtime_descriptor(
    *,
    timeout_seconds: float | None = None,
    poll_interval_seconds: float = 0.25,
) -> RuntimeDescriptor:
    """Return a ready desktop runtime, launching the installed app when needed."""

    if _environment_runtime_configured():
        return load_runtime_descriptor()

    descriptor = _load_runtime_if_present()
    process_running = bool(
        descriptor is not None and process_is_running(descriptor.desktop_pid)
    )
    if descriptor is not None and process_running and _runtime_is_ready(descriptor):
        return descriptor

    if not process_running:
        launch_desktop_app()

    timeout = timeout_seconds
    if timeout is None:
        raw_timeout = os.getenv("AUTO_EMAIL_SENDER_STARTUP_TIMEOUT_SECONDS", "90")
        try:
            timeout = max(1.0, float(raw_timeout))
        except ValueError:
            timeout = 90.0
    deadline = time.monotonic() + timeout
    last_descriptor = descriptor
    while time.monotonic() < deadline:
        candidate = _load_runtime_if_present()
        if candidate is not None:
            last_descriptor = candidate
            if (
                process_is_running(candidate.desktop_pid)
                and _runtime_is_ready(candidate)
            ):
                return candidate
        time.sleep(poll_interval_seconds)

    state = (
        f"pid={last_descriptor.desktop_pid}"
        if last_descriptor is not None
        else f"runtime={get_runtime_file_path()}"
    )
    raise RuntimeUnavailableError(
        f"Auto Email Sender 已尝试后台启动，但本地服务未在 {timeout:g} 秒内就绪（{state}）。"
    )


def launch_desktop_app(executable_path: Path | None = None) -> Path:
    executable = executable_path or locate_desktop_executable()
    popen_options: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        popen_options["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NO_WINDOW
        )
    else:
        popen_options["start_new_session"] = True
    try:
        subprocess.Popen(
            [str(executable), "--agent-background"],
            **popen_options,  # type: ignore[arg-type]
        )
    except OSError as exc:
        raise RuntimeUnavailableError(
            f"无法自动启动 Auto Email Sender：{exc}。请手动打开软件后重试。"
        ) from exc
    return executable


def locate_desktop_executable() -> Path:
    override = os.getenv("AUTO_EMAIL_SENDER_DESKTOP_EXECUTABLE")
    if override and override.strip():
        return _require_executable(Path(override).expanduser())

    installation_path = get_agent_installation_file_path()
    try:
        installation = json.loads(installation_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        installation = None
    if isinstance(installation, dict):
        configured = installation.get("desktop_executable")
        if isinstance(configured, str) and configured.strip():
            candidate = Path(configured).expanduser()
            if candidate.is_file():
                return candidate.resolve()

    for candidate in _desktop_executable_candidates():
        if candidate.is_file():
            return candidate.resolve()

    raise RuntimeUnavailableError(
        "找不到 Auto Email Sender 桌面程序。请先安装桌面版，或在个人中心修复“命令行与 Agent”。"
    )


def _desktop_executable_candidates() -> list[Path]:
    candidates: list[Path] = []
    cli_executable = Path(sys.executable).resolve()
    if cli_executable.parent.name.lower() == "cli":
        resources_path = cli_executable.parent.parent
        if sys.platform == "darwin" and resources_path.name == "Resources":
            candidates.append(
                resources_path.parent / "MacOS" / "Auto Email Sender"
            )
        elif sys.platform == "win32" and resources_path.name.lower() == "resources":
            candidates.append(resources_path.parent / "Auto Email Sender.exe")

    if sys.platform == "darwin":
        for applications_path in (Path("/Applications"), Path.home() / "Applications"):
            candidates.append(
                applications_path
                / "Auto Email Sender.app"
                / "Contents"
                / "MacOS"
                / "Auto Email Sender"
            )
    elif sys.platform == "win32":
        local_app_data = os.getenv("LOCALAPPDATA")
        if local_app_data:
            candidates.append(
                Path(local_app_data)
                / "Programs"
                / "Auto Email Sender"
                / "Auto Email Sender.exe"
            )
    return candidates


def _require_executable(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_file():
        raise RuntimeUnavailableError(
            f"指定的 Auto Email Sender 桌面程序不存在：{resolved}"
        )
    return resolved


def _environment_runtime_configured() -> bool:
    return bool(
        os.getenv("AUTO_EMAIL_SENDER_BASE_URL")
        and os.getenv("AUTO_EMAIL_SENDER_AGENT_TOKEN")
    )


def _load_runtime_if_present() -> RuntimeDescriptor | None:
    try:
        return load_runtime_descriptor()
    except RuntimeUnavailableError:
        return None


def _runtime_is_ready(descriptor: RuntimeDescriptor) -> bool:
    try:
        response = httpx.get(
            f"{descriptor.base_url.rstrip('/')}/ready",
            timeout=0.8,
        )
    except httpx.HTTPError:
        return False
    return response.is_success
