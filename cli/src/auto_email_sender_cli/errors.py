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
    def __init__(self, message: str = "Auto Email Sender 本地服务当前不可用。") -> None:
        super().__init__(
            code="APP_UNAVAILABLE",
            message=message,
            exit_code=7,
            retryable=True,
            suggested_command="auto-email-sender --format json doctor",
        )
