from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class CliError(Exception):
    code: str
    message: str
    exit_code: int
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)
    suggested_command: str | None = None

    def __str__(self) -> str:
        return self.message


class RuntimeUnavailableError(CliError):
    def __init__(
        self,
        message: str = (
            "Auto Email Sender 当前不可用。请先手动打开软件，"
            "等待本地服务加载完成后再重试。"
        ),
    ) -> None:
        super().__init__(
            code="APP_UNAVAILABLE",
            message=message,
            exit_code=7,
            retryable=True,
            suggested_command="auto-email-sender --format json doctor",
        )


class RuntimeProtocolMismatchError(CliError):
    def __init__(self, *, expected: str, actual: str) -> None:
        super().__init__(
            code="RUNTIME_PROTOCOL_MISMATCH",
            message=(
                "当前命令行与正在运行的 Auto Email Sender 桌面版不兼容"
                f"（命令行协议 {expected}，桌面端协议 {actual}）。"
                "请更新桌面软件，或在个人中心展开“命令行与 Agent”后点击“重新安装”。"
            ),
            exit_code=7,
            suggested_command="auto-email-sender --format json doctor",
        )
