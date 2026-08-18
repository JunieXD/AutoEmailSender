from __future__ import annotations

from collections import OrderedDict
from threading import RLock
import time
from typing import Any, Literal, TypeAlias
from urllib.parse import urlparse


BrowserSessionScope: TypeAlias = tuple[Literal["job", "run"], int]
_CookieKey: TypeAlias = tuple[str, str, str]


class BrowserCookieSessionCache:
    """Keep browser challenge cookies in memory for one crawl run."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 2 * 60 * 60,
        max_scopes: int = 32,
        max_cookies_per_scope: int = 256,
    ) -> None:
        self._ttl_seconds = max(1.0, float(ttl_seconds))
        self._max_scopes = max(1, int(max_scopes))
        self._max_cookies_per_scope = max(1, int(max_cookies_per_scope))
        self._cookies: OrderedDict[
            BrowserSessionScope,
            tuple[float, OrderedDict[_CookieKey, dict[str, Any]]],
        ] = OrderedDict()
        self._lock = RLock()

    def get_for_url(
        self,
        scope: BrowserSessionScope,
        url: str,
    ) -> tuple[dict[str, Any], ...]:
        hostname = (urlparse(url).hostname or "").lower()
        if not hostname:
            return ()
        with self._lock:
            now = time.monotonic()
            self._discard_expired_scopes(now)
            entry = self._cookies.get(scope)
            if entry is None:
                return ()
            _, cookies = entry
            self._discard_expired_cookies(cookies)
            self._cookies[scope] = (now, cookies)
            self._cookies.move_to_end(scope)
            return tuple(
                dict(cookie)
                for cookie in cookies.values()
                if _cookie_matches_hostname(cookie, hostname)
            )

    def remember(
        self,
        scope: BrowserSessionScope,
        cookies: list[dict[str, Any]],
    ) -> None:
        if not cookies:
            return
        with self._lock:
            now = time.monotonic()
            self._discard_expired_scopes(now)
            entry = self._cookies.get(scope)
            stored = entry[1] if entry is not None else OrderedDict()
            self._discard_expired_cookies(stored)
            for cookie in cookies:
                key = _cookie_key(cookie)
                if key is None or _cookie_is_expired(cookie):
                    continue
                stored[key] = dict(cookie)
                stored.move_to_end(key)
            while len(stored) > self._max_cookies_per_scope:
                stored.popitem(last=False)
            if not stored:
                self._cookies.pop(scope, None)
                return
            self._cookies[scope] = (now, stored)
            self._cookies.move_to_end(scope)
            while len(self._cookies) > self._max_scopes:
                self._cookies.popitem(last=False)

    def discard_scope(self, scope: BrowserSessionScope) -> None:
        with self._lock:
            self._cookies.pop(scope, None)

    def discard_for_url(self, scope: BrowserSessionScope, url: str) -> None:
        hostname = (urlparse(url).hostname or "").lower()
        if not hostname:
            return
        with self._lock:
            now = time.monotonic()
            self._discard_expired_scopes(now)
            entry = self._cookies.get(scope)
            if entry is None:
                return
            _, cookies = entry
            matching_keys = [
                key
                for key, cookie in cookies.items()
                if _cookie_matches_hostname(cookie, hostname)
            ]
            for key in matching_keys:
                cookies.pop(key, None)
            if not cookies:
                self._cookies.pop(scope, None)
                return
            self._cookies[scope] = (now, cookies)
            self._cookies.move_to_end(scope)

    def clear(self) -> None:
        with self._lock:
            self._cookies.clear()

    def _discard_expired_scopes(self, now: float) -> None:
        expired = [
            scope
            for scope, (last_accessed, _cookies) in self._cookies.items()
            if now - last_accessed >= self._ttl_seconds
        ]
        for scope in expired:
            self._cookies.pop(scope, None)

    @staticmethod
    def _discard_expired_cookies(
        cookies: OrderedDict[_CookieKey, dict[str, Any]],
    ) -> None:
        expired = [key for key, cookie in cookies.items() if _cookie_is_expired(cookie)]
        for key in expired:
            cookies.pop(key, None)


def _cookie_key(cookie: dict[str, Any]) -> _CookieKey | None:
    name = str(cookie.get("name") or "").strip()
    domain = str(cookie.get("domain") or "").strip().lower()
    path = str(cookie.get("path") or "/").strip() or "/"
    if not name or not domain:
        return None
    return name, domain, path


def _cookie_matches_hostname(cookie: dict[str, Any], hostname: str) -> bool:
    domain = str(cookie.get("domain") or "").strip().lower().lstrip(".")
    return bool(domain and (hostname == domain or hostname.endswith(f".{domain}")))


def _cookie_is_expired(cookie: dict[str, Any]) -> bool:
    expires = cookie.get("expires")
    if not isinstance(expires, (int, float)) or expires <= 0:
        return False
    return float(expires) <= time.time()


browser_cookie_session_cache = BrowserCookieSessionCache()
