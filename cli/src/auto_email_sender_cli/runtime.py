from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from auto_email_sender_cli.errors import (
    RuntimeProtocolMismatchError,
    RuntimeUnavailableError,
)
from auto_email_sender_cli.version import PROTOCOL_VERSION


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


def load_runtime_descriptor() -> RuntimeDescriptor:
    base_url = os.getenv("AUTO_EMAIL_SENDER_BASE_URL")
    token = os.getenv("AUTO_EMAIL_SENDER_AGENT_TOKEN")
    if base_url and token:
        return RuntimeDescriptor(
            protocol_version=os.getenv("AUTO_EMAIL_SENDER_PROTOCOL_VERSION", PROTOCOL_VERSION),
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
            "Auto Email Sender 当前未运行。请先手动打开软件，"
            "等待本地服务加载完成后再重试。",
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


def ensure_runtime_descriptor() -> RuntimeDescriptor:
    """Return the ready desktop runtime published by a manually opened app."""

    if _environment_runtime_configured():
        return ensure_runtime_protocol_compatible(load_runtime_descriptor())

    descriptor = load_runtime_descriptor()
    if not process_is_running(descriptor.desktop_pid):
        raise RuntimeUnavailableError(
            "Auto Email Sender 当前未运行。请先手动打开软件，"
            "等待本地服务加载完成后再重试。",
        )
    if not _runtime_is_ready(descriptor):
        raise RuntimeUnavailableError(
            "Auto Email Sender 正在启动或本地服务尚未就绪。"
            "请等待软件加载完成后再重试。",
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


def _runtime_is_ready(descriptor: RuntimeDescriptor) -> bool:
    try:
        response = httpx.get(
            f"{descriptor.base_url.rstrip('/')}/ready",
            timeout=0.8,
        )
    except httpx.HTTPError:
        return False
    return response.is_success
