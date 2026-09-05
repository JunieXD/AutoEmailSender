from __future__ import annotations


def is_provider_throttle_error(exc: object) -> bool:
    text = str(exc).lower()
    if not text:
        return False
    direct_markers = [
        "fetch volume limit exceed",
        "too many requests",
        "too many login failures",
        "too many simultaneous connections",
        "maximum number of connections",
        "rate limit",
        "rate limited",
        "temporarily blocked",
        "try again later",
        "登录过于频繁",
        "请求过于频繁",
        "操作过于频繁",
        "连接数过多",
        "超过频率限制",
        "稍后再试",
        "流量超限",
    ]
    if any(marker in text for marker in direct_markers):
        return True
    return "exceed" in text and "limit" in text


def is_account_level_throttle_error(exc: object) -> bool:
    return is_provider_throttle_error(exc)


def imap_status_text(status: object) -> str:
    if isinstance(status, (bytes, bytearray)):
        return bytes(status).decode("utf-8", errors="ignore")
    return str(status)


def format_imap_response_detail(status: object, payload: object) -> str:
    status_text = imap_status_text(status)
    payload_text = _format_imap_payload_text(payload)
    if payload_text:
        return f"{status_text}: {payload_text}"
    return status_text


def _format_imap_payload_text(payload: object) -> str:
    if not payload:
        return ""
    items = payload if isinstance(payload, (list, tuple)) else [payload]
    parts: list[str] = []
    for item in items:
        if isinstance(item, (bytes, bytearray)):
            text = bytes(item).decode("utf-8", errors="ignore")
        elif isinstance(item, tuple):
            text = " ".join(_format_imap_payload_text(part) for part in item)
        else:
            text = str(item)
        text = text.strip()
        if text:
            parts.append(text)
    return " ".join(parts)[:500]
