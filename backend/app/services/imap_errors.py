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
    text = str(exc).lower()
    if "exceed" in text and "limit" in text:
        return True
    return any(
        marker in text
        for marker in [
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
    )
