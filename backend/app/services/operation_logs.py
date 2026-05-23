from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from types import FunctionType, MethodType
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.request_context import get_request_id
from app.models import OperationLog


SENSITIVE_KEY_PARTS = {
    "apikey",
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
}
SENSITIVE_KEYS = {
    "body",
    "bodyhtml",
    "bodytext",
    "content",
    "emailbody",
    "generatedcontenttext",
    "payload",
    "requestbody",
    "responsebody",
}
TOKEN_USAGE_KEYS = {
    "cachedtokens",
    "completiontokens",
    "inputtokens",
    "outputtokens",
    "prompttokens",
    "totaltokens",
}
MAX_STRING_LENGTH = 1000
MAX_DEPTH = 10
REDACTED = "[REDACTED]"
MESSAGE_KEY_VALUE_PATTERN = re.compile(
    r"(?P<key>\b(?:api[_-]?key|authorization|cookie|password|secret|smtpPassword|token)\b)"
    r"(?P<separator>\s*[:=]\s*)"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\s,;]+)",
    re.IGNORECASE,
)
MESSAGE_BEARER_PATTERN = re.compile(
    r"(?P<prefix>\bAuthorization\s*:\s*Bearer\s+)(?P<value>[^\s,;]+)",
    re.IGNORECASE,
)
URL_PATTERN = re.compile(r"https?://[^\s<>'\"]+")


async def record_operation_log(
    session: AsyncSession,
    *,
    category: str,
    event_name: str,
    level: str = "info",
    message: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    metadata: object | None = None,
    request_id: str | None = None,
    now: datetime | None = None,
) -> OperationLog:
    await cleanup_old_operation_logs(session, now=now)
    log = OperationLog(
        request_id=request_id if request_id is not None else get_request_id(),
        category=category,
        event_name=event_name,
        level=level,
        message=_safe_message(message),
        entity_type=entity_type,
        entity_id=entity_id,
        event_metadata=_safe_json(metadata),
    )
    session.add(log)
    await session.flush()
    return log


async def cleanup_old_operation_logs(
    session: AsyncSession,
    *,
    retention_days: int | None = None,
    now: datetime | None = None,
) -> int:
    resolved_retention_days = (
        get_settings().operation_log_retention_days
        if retention_days is None
        else retention_days
    )
    if resolved_retention_days <= 0:
        return 0

    resolved_now = now or datetime.now(UTC)
    cutoff = resolved_now - timedelta(days=resolved_retention_days)
    result = await session.execute(
        delete(OperationLog)
        .where(OperationLog.created_at < cutoff)
        .execution_options(synchronize_session=False),
    )
    return int(result.rowcount or 0)


def _safe_json(value: object | None) -> object | None:
    if value is None:
        return None
    try:
        return _to_jsonable(value, seen=set(), depth=0)
    except Exception as exc:
        return {"metadata_error": _stringify_exception(exc)}


def _to_jsonable(value: object, *, seen: set[int], depth: int) -> object:
    if depth > MAX_DEPTH:
        return "[MaxDepth]"
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return _truncate_string(value)
    if isinstance(value, BaseException):
        return _stringify_exception(value)
    if isinstance(value, bytes):
        return _truncate_string(value.decode("utf-8", errors="replace"))
    if isinstance(value, FunctionType | MethodType):
        return _truncate_string(repr(value))

    value_id = id(value)
    if isinstance(value, Mapping):
        if value_id in seen:
            return "[Circular]"
        seen.add(value_id)
        try:
            return {
                str(key): (
                    REDACTED
                    if _is_sensitive_key(str(key))
                    else _to_jsonable(item, seen=seen, depth=depth + 1)
                )
                for key, item in value.items()
            }
        finally:
            seen.remove(value_id)

    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        if value_id in seen:
            return "[Circular]"
        seen.add(value_id)
        try:
            return [_to_jsonable(item, seen=seen, depth=depth + 1) for item in value]
        finally:
            seen.remove(value_id)

    return _truncate_string(repr(value))


def _is_sensitive_key(key: str) -> bool:
    normalized = "".join(char for char in key.lower() if char.isalnum())
    if normalized in TOKEN_USAGE_KEYS:
        return False
    return normalized in SENSITIVE_KEYS or any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _safe_message(message: str | None) -> str | None:
    if message is None:
        return None
    try:
        return _sanitize_message(message)
    except Exception:
        return "[MessageSanitizationFailed]"


def _sanitize_message(message: str) -> str:
    sanitized = URL_PATTERN.sub(_strip_url_query_and_fragment, message)
    sanitized = MESSAGE_BEARER_PATTERN.sub(r"\g<prefix>[REDACTED]", sanitized)

    def replace_value(match: re.Match[str]) -> str:
        key = match.group("key")
        separator = match.group("separator")
        return f"{key}{separator}{REDACTED}"

    return _truncate_string(MESSAGE_KEY_VALUE_PATTERN.sub(replace_value, sanitized))


def _strip_url_query_and_fragment(match: re.Match[str]) -> str:
    raw_url = match.group(0)
    parsed = urlsplit(raw_url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _truncate_string(value: str) -> str:
    if len(value) <= MAX_STRING_LENGTH:
        return value
    return f"{value[:MAX_STRING_LENGTH]}...[truncated]"


def _stringify_exception(exc: BaseException) -> str:
    return _truncate_string(f"{exc.__class__.__name__}: {exc}")
