from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import re
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


_SECRET_MESSAGE_PATTERN = re.compile(
    r"(?P<key>\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|"
    r"auth[_-]?token|bearer[_-]?token|password|secret|credential|authorization|cookie|"
    r"smtp[_-]?password|imap[_-]?password)\b)"
    r"(?P<quote>[\"']?)(?P<separator>\s*[:=]\s*)"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\s,;]+)",
    re.IGNORECASE,
)
_BEARER_PATTERN = re.compile(
    r"(?P<prefix>\bAuthorization\s*:\s*Bearer\s+)(?P<value>[^\s,;]+)",
    re.IGNORECASE,
)
_SENSITIVE_KEY_NAMES = {
    "password",
    "apikey",
    "accesstoken",
    "refreshtoken",
    "idtoken",
    "authtoken",
    "bearertoken",
    "secret",
    "credential",
    "authorization",
    "cookie",
    "smtppassword",
    "imappassword",
}
_SAFE_TOKEN_KEY_NAMES = {
    "comparison_token",
    "input_tokens",
    "output_tokens",
    "cached_tokens",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
}
_BOUNDED_ERROR_LIST_KEYS = {
    "allowed_fields",
    "available_fields",
    "allowed_operators",
    "available_sections",
    "available_views",
    "suggestions",
}
_MAX_ERROR_LIST_ITEMS = 20


def sanitize_error_message(message: object) -> str:
    """Redact key/value-shaped credentials before an error reaches an Agent."""

    sanitized = _BEARER_PATTERN.sub(r"\g<prefix>[REDACTED]", str(message))

    def replace_secret(match: re.Match[str]) -> str:
        return f"{match.group('key')}{match.group('quote')}{match.group('separator')}[REDACTED]"

    return _SECRET_MESSAGE_PATTERN.sub(replace_secret, sanitized)


def redact_error_details(details: object) -> dict[str, Any]:
    """Recursively redact credential-shaped fields in structured error data."""

    value = _redact_error_value(details)
    return value if isinstance(value, dict) else {}


def _redact_error_value(value: object) -> object:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return sanitize_error_message(value)
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        bounded_metadata: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            result[key_text] = (
                "[REDACTED]"
                if _is_sensitive_key(key_text)
                else _redact_error_value(item)
            )
            if (
                key_text in _BOUNDED_ERROR_LIST_KEYS
                and isinstance(result[key_text], list)
                and len(result[key_text]) > _MAX_ERROR_LIST_ITEMS
            ):
                total = len(result[key_text])
                result[key_text] = result[key_text][:_MAX_ERROR_LIST_ITEMS]
                bounded_metadata[f"{key_text}_total_count"] = total
                bounded_metadata[f"{key_text}_truncated"] = True
        result.update(bounded_metadata)
        return result
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
        return [_redact_error_value(item) for item in value]
    return "[UNSERIALIZABLE]"


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    if normalized in _SAFE_TOKEN_KEY_NAMES:
        return False
    compact = normalized.replace("_", "")
    if compact in _SENSITIVE_KEY_NAMES:
        return True
    return compact.endswith("password") or compact.endswith("token")


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


class ExternalExecutionUnknownError(CliError):
    """A non-idempotent external request may have reached its provider."""

    def __init__(self, *, command: str, request_id: str | None = None) -> None:
        details: dict[str, Any] = {"command": command}
        if request_id:
            details["request_id"] = request_id
        super().__init__(
            code="EXTERNAL_EXECUTION_UNKNOWN",
            message=(
                "外部服务执行结果未知；为避免重复副作用，CLI 不会自动重试。"
                "请先读取对应对象或任务状态，确认实际结果后再决定下一步。"
            ),
            exit_code=9,
            retryable=False,
            details=details,
            suggested_command="auto-email-sender --format json status",
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
