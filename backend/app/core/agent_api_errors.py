from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Mapping, Sequence
import re

from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


_AGENT_SECRET_MESSAGE_PATTERN = re.compile(
    r"(?P<key>\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|"
    r"auth[_-]?token|bearer[_-]?token|password|secret|credential|authorization|cookie|"
    r"smtp[_-]?password|imap[_-]?password)\b)"
    r"(?P<quote>[\"']?)(?P<separator>\s*[:=]\s*)"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\s,;]+)",
    re.IGNORECASE,
)
_AGENT_BEARER_PATTERN = re.compile(
    r"(?P<prefix>\bAuthorization\s*:\s*Bearer\s+)(?P<value>[^\s,;]+)",
    re.IGNORECASE,
)
_AGENT_SENSITIVE_KEYS = {
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
_AGENT_SAFE_TOKEN_KEYS = {
    "comparison_token",
    "input_tokens",
    "output_tokens",
    "cached_tokens",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
}


@dataclass(slots=True)
class AgentApiError(Exception):
    status_code: int
    code: str
    message: str
    retryable: bool = False
    details: dict[str, object] = field(default_factory=dict)
    suggested_command: str | None = None
    # Internal marker used by the durable mutation wrapper.  It is deliberately
    # not exposed in the JSON error envelope: callers only need the stable
    # EXTERNAL_EXECUTION_UNKNOWN code when an external provider may have run.
    external_execution_unknown: bool = False

    def __str__(self) -> str:
        return self.message


async def agent_api_error_handler(
    request: Request,
    exc: AgentApiError,
) -> JSONResponse:
    _ = request
    error: dict[str, object] = {
        "code": exc.code,
        "message": _sanitize_agent_message(exc.message),
        "retryable": exc.retryable,
        "details": _sanitize_agent_details(exc.details),
    }
    if exc.suggested_command:
        error["suggested_action"] = {
            "command": _sanitize_agent_message(exc.suggested_command),
        }
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": error},
    )


async def http_exception_handler(
    request: Request,
    exc: HTTPException,
) -> JSONResponse:
    """Normalize router-level HTTPException values for the Agent API.

    A few read-only routes intentionally use FastAPI's small
    ``HTTPException`` convenience.  Without this adapter those routes return
    ``{"detail": ...}``, while every other Agent route returns the structured
    error envelope.  Keep the desktop/UI API's native response unchanged.
    """

    is_agent_path = request.url.path == "/api/agent/v1" or request.url.path.startswith(
        "/api/agent/v1/",
    )
    if not is_agent_path:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": jsonable_encoder(exc.detail)},
            headers=exc.headers,
        )

    status_code = int(exc.status_code)
    if status_code == 404:
        code = "RESOURCE_NOT_FOUND"
    elif status_code == 409:
        code = "CONFLICT"
    elif status_code == 422:
        code = "INVALID_AGENT_REQUEST"
    elif 400 <= status_code < 500:
        code = "INVALID_ARGUMENT"
    else:
        code = f"HTTP_{status_code}"
    detail = exc.detail
    message = detail if isinstance(detail, str) else "本地服务请求失败。"
    details: dict[str, object] = {"http_status": status_code}
    if isinstance(detail, Mapping):
        sanitized_detail = _sanitize_agent_value(detail)
        if isinstance(sanitized_detail, dict):
            details["detail"] = sanitized_detail
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": _sanitize_agent_message(message),
                "retryable": status_code >= 500,
                "details": _sanitize_agent_details(details),
            },
        },
        headers=exc.headers,
    )


def _sanitize_agent_message(message: str) -> str:
    """Keep credentials out of Agent-facing error messages."""

    sanitized = _AGENT_BEARER_PATTERN.sub(r"\g<prefix>[REDACTED]", str(message))

    def replace_secret(match: re.Match[str]) -> str:
        return f"{match.group('key')}{match.group('quote')}{match.group('separator')}[REDACTED]"

    return _AGENT_SECRET_MESSAGE_PATTERN.sub(replace_secret, sanitized)


def _sanitize_agent_details(value: object) -> dict[str, object]:
    """Recursively redact credential-shaped fields in structured details."""

    sanitized = _sanitize_agent_value(value)
    return sanitized if isinstance(sanitized, dict) else {}


def _sanitize_agent_value(value: object) -> object:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return _sanitize_agent_message(value)
    if isinstance(value, Mapping):
        return {
            str(key): (
                "[REDACTED]"
                if _is_agent_sensitive_key(str(key))
                else _sanitize_agent_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
        return [_sanitize_agent_value(item) for item in value]
    # AgentApiError.details should already be JSON-compatible.  Do not fall
    # back to repr(value), because an arbitrary object may contain a secret.
    return "[UNSERIALIZABLE]"


def _is_agent_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    if normalized in _AGENT_SAFE_TOKEN_KEYS:
        return False
    compact = normalized.replace("_", "")
    if compact in _AGENT_SENSITIVE_KEYS:
        return True
    return compact.endswith("password") or compact.endswith("token")


async def request_validation_error_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Keep Agent validation failures from reflecting submitted credentials."""

    if request.url.path.startswith("/api/agent/v1/"):
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "INVALID_AGENT_REQUEST",
                    "message": "请求参数无效；请查看命令帮助后重试。",
                    "retryable": False,
                    "details": {"validation_error_count": len(exc.errors())},
                },
            },
        )
    return JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(exc.errors())},
    )
