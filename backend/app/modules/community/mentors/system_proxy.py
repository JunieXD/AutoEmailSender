from __future__ import annotations

import sys
from collections.abc import Mapping
from urllib import request as urllib_request
from urllib.parse import urlsplit, urlunsplit


SUPPORTED_TARGET_SCHEMES = {"http", "https"}
SUPPORTED_PROXY_SCHEMES = {"http", "https", "socks5", "socks5h"}


def resolve_system_proxy(target_url: str) -> str | None:
    """Resolve the current OS proxy for one URL without changing process state."""
    try:
        target = urlsplit(target_url)
    except ValueError:
        return None
    if target.scheme not in SUPPORTED_TARGET_SCHEMES or not target.hostname:
        return None

    proxies, bypassed = _read_platform_proxy_settings(target.hostname)
    if bypassed:
        return None
    return _normalize_proxy_url(
        proxies.get(target.scheme) or proxies.get("all"),
    )


def _read_platform_proxy_settings(hostname: str) -> tuple[Mapping[str, str], bool]:
    if sys.platform == "win32":
        proxy_reader = getattr(urllib_request, "getproxies_registry", None)
        bypass_reader = getattr(urllib_request, "proxy_bypass_registry", None)
        try:
            proxies = proxy_reader() if callable(proxy_reader) else {}
            bypassed = (
                bool(bypass_reader(hostname)) if callable(bypass_reader) else False
            )
        except (OSError, TypeError, ValueError):
            return {}, False
        return proxies, bypassed

    try:
        return urllib_request.getproxies(), urllib_request.proxy_bypass(hostname)
    except (OSError, TypeError, ValueError):
        return {}, False


def _normalize_proxy_url(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if "://" not in candidate:
        candidate = f"http://{candidate}"
    try:
        parsed = urlsplit(candidate)
        _ = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.lower() not in SUPPORTED_PROXY_SCHEMES
        or not parsed.hostname
        or any(character.isspace() for character in parsed.netloc)
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        return None
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, "", "", ""))
