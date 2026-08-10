from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit


MAX_STRING_LENGTH = 1000
MAX_DIAGNOSTIC_TEXT_LENGTH = 250_000
REDACTED = "[REDACTED]"
MESSAGE_KEY_VALUE_PATTERN = re.compile(
    r"(?P<key>\b(?:"
    r"api[_-]?key|authorization|cookie|password|secret|smtpPassword|token|"
    r"body(?:[_-]?(?:html|text))?|content|email[_-]?body|"
    r"generated[_-]?content[_-]?text|payload|request[_-]?body|response[_-]?body"
    r")\b)"
    r"(?P<key_quote>[\"']?)"
    r"(?P<separator>\s*[:=]\s*)"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\s,;]+)",
    re.IGNORECASE,
)
MESSAGE_BEARER_PATTERN = re.compile(
    r"(?P<prefix>\bAuthorization\s*:\s*Bearer\s+)(?P<value>[^\s,;]+)",
    re.IGNORECASE,
)
MESSAGE_COOKIE_HEADER_PATTERN = re.compile(
    r"(?P<prefix>\b(?:Cookie|Set-Cookie)\s*:\s*)(?P<value>[^\r\n]+)",
    re.IGNORECASE,
)
URL_PATTERN = re.compile(r"https?://[^\s<>'\"]+")


def sanitize_diagnostic_text(value: object | None) -> str:
    """Redact a diagnostic text file without reducing it to an error summary."""

    if value is None:
        return ""
    try:
        return sanitize_text(
            str(value),
            max_length=MAX_DIAGNOSTIC_TEXT_LENGTH,
        )
    except Exception:
        return "[DiagnosticSanitizationFailed]"


def sanitize_text(value: str, *, max_length: int = MAX_STRING_LENGTH) -> str:
    sanitized = URL_PATTERN.sub(_strip_url_query_and_fragment, value)
    sanitized = MESSAGE_BEARER_PATTERN.sub(r"\g<prefix>[REDACTED]", sanitized)
    sanitized = MESSAGE_COOKIE_HEADER_PATTERN.sub(r"\g<prefix>[REDACTED]", sanitized)

    def replace_value(match: re.Match[str]) -> str:
        key = match.group("key")
        key_quote = match.group("key_quote")
        separator = match.group("separator")
        return f"{key}{key_quote}{separator}{REDACTED}"

    return truncate_text(
        MESSAGE_KEY_VALUE_PATTERN.sub(replace_value, sanitized),
        max_length=max_length,
    )


def truncate_text(value: str, *, max_length: int = MAX_STRING_LENGTH) -> str:
    if len(value) <= max_length:
        return value
    return f"{value[:max_length]}...[truncated]"


def _strip_url_query_and_fragment(match: re.Match[str]) -> str:
    raw_url = match.group(0)
    parsed = urlsplit(raw_url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


__all__ = [
    "MAX_STRING_LENGTH",
    "REDACTED",
    "sanitize_diagnostic_text",
    "sanitize_text",
    "truncate_text",
]
