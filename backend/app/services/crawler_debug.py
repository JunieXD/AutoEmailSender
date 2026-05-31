from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from app.core.time import utc_now

from types import FunctionType, MethodType
from typing import Any

from app.core.config import get_settings


DEBUG_SENSITIVE_KEY_PARTS = {
    "apikey",
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
}
DEBUG_MAX_DEPTH = 20
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


def append_crawler_debug_event(job_id: int, event: Any) -> Path | None:
    settings = get_settings()
    if not settings.crawler_debug_enabled:
        return None

    debug_file = settings.crawler_debug_dir / f"crawl-job-{job_id}.jsonl"
    record = {
        "recorded_at": utc_now().isoformat(),
        "job_id": job_id,
        "raw_event": _safe_debug_json(event),
    }
    with debug_file.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False))
        handle.write("\n")
    return debug_file


def crawler_debug_file_path(job_id: int) -> Path:
    return get_settings().crawler_debug_dir / f"crawl-job-{job_id}.jsonl"


def _safe_debug_json(value: object | None) -> object | None:
    if value is None:
        return None
    try:
        return _to_debug_jsonable(value, seen=set(), depth=0)
    except Exception as exc:
        return {"debug_metadata_error": _stringify_exception(exc)}


def _to_debug_jsonable(value: object, *, seen: set[int], depth: int) -> object:
    if depth > DEBUG_MAX_DEPTH:
        return "[MaxDepth]"
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return _sanitize_debug_string(value)
    if isinstance(value, BaseException):
        return _stringify_exception(value)
    if isinstance(value, bytes):
        return _sanitize_debug_string(value.decode("utf-8", errors="replace"))
    if isinstance(value, FunctionType | MethodType):
        return repr(value)

    value_id = id(value)
    if isinstance(value, Mapping):
        if value_id in seen:
            return "[Circular]"
        seen.add(value_id)
        try:
            return {
                str(key): (
                    REDACTED
                    if _is_debug_sensitive_key(str(key))
                    else _to_debug_jsonable(item, seen=seen, depth=depth + 1)
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
            return [_to_debug_jsonable(item, seen=seen, depth=depth + 1) for item in value]
        finally:
            seen.remove(value_id)

    return _sanitize_debug_string(repr(value))


def _is_debug_sensitive_key(key: str) -> bool:
    normalized = "".join(char for char in key.lower() if char.isalnum())
    return any(part in normalized for part in DEBUG_SENSITIVE_KEY_PARTS)


def _sanitize_debug_string(value: str) -> str:
    sanitized = MESSAGE_BEARER_PATTERN.sub(r"\g<prefix>[REDACTED]", value)

    def replace_value(match: re.Match[str]) -> str:
        key = match.group("key")
        separator = match.group("separator")
        return f"{key}{separator}{REDACTED}"

    return MESSAGE_KEY_VALUE_PATTERN.sub(replace_value, sanitized)


def _stringify_exception(exc: BaseException) -> str:
    return f"{exc.__class__.__name__}: {exc}"


