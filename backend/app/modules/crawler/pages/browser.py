from __future__ import annotations

import asyncio
import hashlib
import platform
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from functools import lru_cache
from html import unescape
from typing import Any, Literal
from urllib.parse import urlparse

from app.modules.professors.public import (
    is_valid_professor_email,
    normalize_professor_email,
)
from app.services.beautiful_soup import parse_html

from .browser_session import BrowserSessionScope, browser_cookie_session_cache
from .payloads import (
    BrowserPaginationExpansion as BrowserPaginationExpansion,
    BrowserSamePageExpansion as BrowserSamePageExpansion,
    PageSnapshot as PageSnapshot,
)
from .snapshots import (
    DYNAMIC_TEACHER_DIRECTORY_MARKERS as DYNAMIC_TEACHER_DIRECTORY_MARKERS,
    html_to_snapshot as html_to_snapshot,
)
from .url_safety import is_safe_public_crawl_url

async_playwright = None


@lru_cache(maxsize=1)
def _load_async_playwright() -> Any:
    try:
        from playwright.async_api import async_playwright as playwright_factory
    except Exception:  # pragma: no cover - dependency errors become fetch errors later
        return None
    return playwright_factory


def _get_async_playwright() -> Any:
    if async_playwright is not None:
        return async_playwright
    return _load_async_playwright()


MAX_EMBEDDED_FRAME_DOCUMENTS = 4


MAX_RETRIES_FOR_BROWSER_RENDER = 2


MAX_BROWSER_PAGINATION_CLICK_RETRIES = 2


BROWSER_PAGINATION_CHANGE_TIMEOUT_MS = 10000


BROWSER_BLOCKED_RESOURCE_TYPES = frozenset({"font", "media"})


_TRANSPARENT_IMAGE_BYTES = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff"
    b"!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00"
    b"\x00\x02\x02D\x01\x00;"
)


BROWSER_FALLBACK_STATUS = {403, 412, 429}


TRANSIENT_HTTP_STATUS_CODES = frozenset({408, 425, 429})


TRANSIENT_SERVER_STATUS_MIN = 500


TRANSIENT_SERVER_STATUS_MAX = 599


_DYNAMIC_COLLECTION_TOKENS = {
    "cards",
    "grid",
    "items",
    "list",
    "results",
    "rows",
}


_DYNAMIC_MAIN_CONTENT_TOKENS = {
    "article",
    "container",
    "content",
    "detail",
    "main",
    "news",
    "result",
    "results",
}


_DYNAMIC_NON_CONTENT_TOKENS = {
    "aside",
    "banner",
    "breadcrumb",
    "carousel",
    "dots",
    "footer",
    "header",
    "menu",
    "nav",
    "navi",
    "pager",
    "pagination",
    "search",
    "share",
    "slider",
    "social",
    "swiper",
    "tabs",
}


JS_RENDER_TIMEOUT_MS = 30000


BROWSER_WAIT_TIMEOUT_MS = 15000


BROWSER_DELAY_SECONDS = 1.5


BROWSER_WAIT_SELECTOR = "css:body"


DYNAMIC_DIRECTORY_READY_TIMEOUT_MS = 5000


DYNAMIC_DIRECTORY_READY_POLL_MS = 200


DYNAMIC_DIRECTORY_STABLE_MS = 500


DYNAMIC_DIRECTORY_MAX_RETRIES = 1


BROWSER_SPARSE_DIRECTORY_RETRY_DELAY_SECONDS = 1.0


BROWSER_TRANSIENT_RETRY_DELAY_SECONDS = 1.0


BROWSER_SPARSE_DIRECTORY_MAX_TEXT_CHARS = 80


BROWSER_SPARSE_DIRECTORY_MAX_HTML_CHARS = 8_000


BROWSER_SPARSE_DIRECTORY_MAX_LINKS = 5


BROWSER_RESTRICTED_RESPONSE_SETTLE_MS = 2_500


DYNAMIC_PROFILE_READY_TIMEOUT_MS = 10000


DYNAMIC_PROFILE_READY_POLL_MS = 200


DYNAMIC_PROFILE_STABLE_MS = 400


DYNAMIC_PROFILE_MEANINGFUL_TEXT_CHARS = 300


BROWSER_EXTRA_ARGS = (
    "--disable-features=HttpsUpgrades",
    "--disable-blink-features=AutomationControlled",
)


CERTIFICATE_DATE_ERROR_MARKERS = (
    "certificate has expired",
    "certificate is not yet valid",
    "cert_has_expired",
    "cert_not_yet_valid",
    "err_cert_date_invalid",
)


_BROWSER_CONTENT_NAVIGATION_ERROR_MARKERS = (
    "page.content",
    "page is navigating",
)


_TRANSIENT_BROWSER_ERROR_MARKERS = (
    "err_",
    "connection",
    "protocol",
    "fetch failed",
    "timed out",
    "timeout",
    "dns",
    "name resolution",
)


IMMEDIATE_HTTP_COMPATIBILITY_ERROR_MARKERS = (
    "err_connection_closed",
    "err_connection_refused",
    "err_http2_protocol_error",
    "err_ssl_protocol_error",
)


BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


CrawlPageIntent = Literal["generic", "directory", "profile"]


_DEFAULT_BROWSER_WAIT_FOR = object()


@dataclass(frozen=True, slots=True)
class BrowserFetchOptions:
    wait_until: str = "load"
    wait_for: str | None = BROWSER_WAIT_SELECTOR
    wait_for_timeout_ms: int = BROWSER_WAIT_TIMEOUT_MS
    delay_before_return_html_seconds: float = BROWSER_DELAY_SECONDS
    page_timeout_ms: int = JS_RENDER_TIMEOUT_MS
    max_retries: int = MAX_RETRIES_FOR_BROWSER_RENDER
    user_agent: str = BROWSER_USER_AGENT
    wait_for_dynamic_directory: bool = False
    dynamic_directory_ready_timeout_ms: int = DYNAMIC_DIRECTORY_READY_TIMEOUT_MS
    dynamic_directory_ready_poll_ms: int = DYNAMIC_DIRECTORY_READY_POLL_MS
    dynamic_directory_stable_ms: int = DYNAMIC_DIRECTORY_STABLE_MS
    wait_for_dynamic_profile: bool = False
    dynamic_profile_ready_timeout_ms: int = DYNAMIC_PROFILE_READY_TIMEOUT_MS
    dynamic_profile_ready_poll_ms: int = DYNAMIC_PROFILE_READY_POLL_MS
    dynamic_profile_stable_ms: int = DYNAMIC_PROFILE_STABLE_MS
    ignore_https_errors: bool = False


_EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


_AT_REPLACEMENTS = (
    r"\(\s*at\s*\)",
    r"\[\s*at\s*\]",
    r"\s+at\s+",
)


_DOT_REPLACEMENTS = (
    r"\(\s*dot\s*\)",
    r"\[\s*dot\s*\]",
    r"\s+dot\s+",
)


_EMAIL_FULLWIDTH_TRANSLATION = str.maketrans(
    {
        "＠": "@",
        "．": ".",
        "。": ".",
        "﹒": ".",
        "｡": ".",
        "（": "(",
        "）": ")",
        "［": "[",
        "］": "]",
        "【": "[",
        "】": "]",
        "｛": "{",
        "｝": "}",
    }
)


_EMAIL_INVISIBLE_PATTERN = re.compile(r"[\u200b\u200c\u200d\ufeff]")


_EMAIL_CHINESE_EMAIL_SYMBOL_PATTERN = re.compile(r"邮箱符号")


_EMAIL_CHINESE_DOT_PATTERN = re.compile(r"(?<=[A-Za-z0-9])\s*点\s*(?=[A-Za-z0-9])")


def normalize_obfuscated_email_tokens(text: str) -> str:
    normalized = unescape(text).translate(_EMAIL_FULLWIDTH_TRANSLATION)
    normalized = _EMAIL_INVISIBLE_PATTERN.sub("", normalized)
    normalized = _EMAIL_CHINESE_EMAIL_SYMBOL_PATTERN.sub("@", normalized)
    for token in _AT_REPLACEMENTS:
        normalized = re.sub(token, "@", normalized, flags=re.IGNORECASE)
    for token in _DOT_REPLACEMENTS:
        normalized = re.sub(token, ".", normalized, flags=re.IGNORECASE)
    normalized = _EMAIL_CHINESE_DOT_PATTERN.sub(".", normalized)
    normalized = re.sub(r"\s*@\s*", "@", normalized)
    normalized = re.sub(r"\s*\.\s*", ".", normalized)
    return normalized


def extract_first_email_from_text(text: str) -> str | None:
    direct = _EMAIL_PATTERN.findall(text)
    direct_email = _first_normalized_valid_email(direct)
    if direct_email:
        return direct_email

    normalized = normalize_obfuscated_email_tokens(text)
    normalized = re.sub(r"\s+", "", normalized)
    normalized_emails = _EMAIL_PATTERN.findall(normalized)
    return _first_normalized_valid_email(normalized_emails)


def _first_normalized_valid_email(candidates: Sequence[str]) -> str | None:
    for candidate in candidates:
        normalized = normalize_professor_email(candidate)
        if normalized and is_valid_professor_email(normalized):
            return normalized
    return None


def looks_like_unrendered_dynamic_teacher_directory(snapshot: PageSnapshot) -> bool:
    html = snapshot.html or ""
    if not html:
        return False
    lowered = html.lower()
    soup = parse_html(html)
    if snapshot.has_dynamic_teacher_directory_markers or any(
        marker in lowered for marker in DYNAMIC_TEACHER_DIRECTORY_MARKERS
    ):
        legacy_containers = soup.select(".type_info")
        if legacy_containers and not any(
            container.get_text(" ", strip=True) or container.find("a", href=True)
            for container in legacy_containers
        ):
            return True
        dynamic_teacher_lists = soup.select(
            ".teacher-list, .teacher-con .teacher-list, [class*='teacher-list']"
        )
        if dynamic_teacher_lists and not any(
            _dynamic_collection_has_content(container)
            for container in dynamic_teacher_lists
        ):
            return True

    collections = list(soup.select("ul, ol, tbody"))
    populated_families = {
        _dynamic_collection_family(container)
        for container in collections
        if _dynamic_collection_has_content(container)
    }

    for container in collections:
        if _dynamic_collection_has_content(container):
            continue
        if (
            container.has_attr("hidden")
            or str(container.get("aria-hidden") or "").lower() == "true"
        ):
            continue

        container_tokens = _html_structure_tokens(container)
        if container.name != "tbody" and not container_tokens.intersection(
            _DYNAMIC_COLLECTION_TOKENS
        ):
            continue

        ancestors = list(container.parents)
        context_tokens = set().union(
            *(_html_structure_tokens(parent) for parent in ancestors)
        )
        if container_tokens.intersection(_DYNAMIC_NON_CONTENT_TOKENS):
            continue
        if context_tokens.intersection(_DYNAMIC_NON_CONTENT_TOKENS):
            continue
        if any(
            getattr(parent, "name", None) in {"header", "footer", "nav", "aside"}
            for parent in ancestors
        ):
            continue
        if not (
            any(getattr(parent, "name", None) == "main" for parent in ancestors)
            or context_tokens.intersection(_DYNAMIC_MAIN_CONTENT_TOKENS)
        ):
            continue
        if _dynamic_collection_family(container) in populated_families:
            continue
        return True
    return False


def _dynamic_collection_has_content(element: Any) -> bool:
    return bool(
        element.get_text(" ", strip=True)
        or element.find("a", href=True)
        or element.find("img", src=True)
    )


def _dynamic_collection_family(element: Any) -> tuple[str, tuple[str, ...]]:
    tag_name = str(getattr(element, "name", "") or "")
    tokens = _html_class_tokens(element) or _html_structure_tokens(element)
    if not tokens:
        parent = getattr(element, "parent", None)
        tokens = _html_structure_tokens(parent)
    return tag_name, tuple(sorted(tokens))


def _html_class_tokens(element: Any) -> set[str]:
    if not hasattr(element, "get"):
        return set()
    classes = element.get("class") or []
    if isinstance(classes, str):
        values = [classes]
    else:
        values = [str(item) for item in classes]
    return {
        token for token in re.split(r"[^a-z0-9]+", " ".join(values).lower()) if token
    }


def _html_structure_tokens(element: Any) -> set[str]:
    if not hasattr(element, "get"):
        return set()
    values = [str(element.get("id") or "")]
    classes = element.get("class") or []
    if isinstance(classes, str):
        values.append(classes)
    else:
        values.extend(str(item) for item in classes)
    return {
        token for token in re.split(r"[^a-z0-9]+", " ".join(values).lower()) if token
    }


def _is_immediate_http_compatibility_error(
    requested_url: str,
    snapshot: PageSnapshot,
) -> bool:
    if snapshot.status != "failed" or urlparse(requested_url).scheme.lower() != "https":
        return False
    error_message = (snapshot.error_message or "").lower()
    return any(
        marker in error_message for marker in IMMEDIATE_HTTP_COMPATIBILITY_ERROR_MARKERS
    )


def _browser_wait_selector_for_intent(intent: CrawlPageIntent) -> str:
    _ = intent
    return BROWSER_WAIT_SELECTOR


def _browser_fetch_options_for_intent(
    intent: CrawlPageIntent,
    *,
    wait_for: str | None | object = _DEFAULT_BROWSER_WAIT_FOR,
    wait_until: str = "load",
) -> BrowserFetchOptions:
    selected_wait_for = (
        _browser_wait_selector_for_intent(intent)
        if wait_for is _DEFAULT_BROWSER_WAIT_FOR
        else wait_for
    )
    if intent == "directory":
        return BrowserFetchOptions(
            wait_until=wait_until,
            wait_for=selected_wait_for,
            delay_before_return_html_seconds=0,
            max_retries=DYNAMIC_DIRECTORY_MAX_RETRIES,
            wait_for_dynamic_directory=True,
        )
    if intent == "profile":
        return BrowserFetchOptions(
            wait_until=wait_until,
            wait_for=selected_wait_for,
            delay_before_return_html_seconds=0,
            wait_for_dynamic_profile=True,
        )
    return BrowserFetchOptions(wait_until=wait_until, wait_for=selected_wait_for)


def _playwright_launch_options() -> dict[str, object]:
    return {
        "headless": True,
        "args": list(BROWSER_EXTRA_ARGS),
    }


async def _apply_browser_bandwidth_policy(route: Any) -> None:
    resource_type = str(getattr(getattr(route, "request", None), "resource_type", ""))
    if resource_type == "image":
        await route.fulfill(
            status=200,
            content_type="image/gif",
            body=_TRANSPARENT_IMAGE_BYTES,
        )
        return
    if resource_type in BROWSER_BLOCKED_RESOURCE_TYPES:
        await route.abort()
        return
    await route.continue_()


async def _install_browser_bandwidth_policy(page: Any) -> None:
    route = getattr(page, "route", None)
    if callable(route):
        await route("**/*", _apply_browser_bandwidth_policy)


async def _fetch_page_with_playwright_direct(
    absolute_url: str,
    goal: str,
    intent: CrawlPageIntent = "generic",
    *,
    browser_session_scope: BrowserSessionScope | None = None,
) -> PageSnapshot:
    _ = goal
    first_result = await _try_playwright_browser_fetch(
        absolute_url,
        _browser_fetch_options_for_intent(intent),
        browser_session_scope=browser_session_scope,
    )
    if first_result.status == "succeeded":
        return first_result

    if _is_wait_condition_failure(first_result.error_message):
        return await _try_playwright_browser_fetch(
            absolute_url,
            _browser_fetch_options_for_intent(intent, wait_for=None),
            browser_session_scope=browser_session_scope,
        )

    return first_result


async def _fetch_browser_same_page_controls_direct(
    absolute_url: str,
    controls: Sequence[dict[str, object]],
    *,
    intent: CrawlPageIntent,
    browser_session_scope: BrowserSessionScope | None = None,
) -> BrowserSamePageExpansion:
    playwright_factory = _get_async_playwright()
    if playwright_factory is None:
        return BrowserSamePageExpansion(
            status="failed",
            stopped_reason="playwright_unavailable",
            error_message="Playwright same-page expansion unavailable",
        )
    options = _browser_fetch_options_for_intent(intent)
    browser = None
    try:
        async with playwright_factory() as playwright:
            browser = await playwright.chromium.launch(**_playwright_launch_options())
            context = await browser.new_context(
                user_agent=options.user_agent,
                ignore_https_errors=options.ignore_https_errors,
            )
            await _restore_browser_session_cookies(
                context,
                browser_session_scope,
                absolute_url,
            )
            page = await context.new_page()
            await _install_browser_bandwidth_policy(page)
            response = await page.goto(
                absolute_url,
                wait_until=options.wait_until,
                timeout=options.page_timeout_ms,
            )
            response_status = getattr(response, "status", None)
            if options.wait_for:
                selector = (
                    options.wait_for[4:]
                    if options.wait_for.startswith("css:")
                    else options.wait_for
                )
                await page.wait_for_selector(
                    selector, timeout=options.wait_for_timeout_ms
                )
            if (
                isinstance(response_status, int)
                and response_status in BROWSER_FALLBACK_STATUS
            ):
                await page.wait_for_timeout(BROWSER_RESTRICTED_RESPONSE_SETTLE_MS)
            if options.wait_for_dynamic_directory:
                await _wait_for_dynamic_directory_html(
                    page, absolute_url=absolute_url, options=options
                )
            initial_body = await page.locator("body").inner_text()
            initial_snapshot_html = await page.content()
            initial_snapshot = _snapshot_from_browser_html(
                html=initial_snapshot_html,
                final_url=str(getattr(page, "url", "") or absolute_url),
                absolute_url=absolute_url,
            )
            if isinstance(response_status, int):
                initial_snapshot.http_status_code = response_status
            if initial_snapshot.status != "succeeded":
                return BrowserSamePageExpansion(
                    status="failed",
                    stopped_reason="initial_page_failed",
                    error_message=initial_snapshot.error_message,
                )
            seen_fingerprints = {_pagination_snapshot_fingerprint(initial_snapshot)}
            snapshots: list[PageSnapshot] = []
            for control in controls:
                target = {
                    "tag": str(control.get("tag") or "a"),
                    "text": str(control.get("text") or ""),
                    "title": str(control.get("title") or ""),
                    "ariaLabel": str(control.get("aria_label") or ""),
                    "classTokens": list(control.get("class_tokens") or ()),
                    "matchIndex": max(0, int(control.get("match_index") or 0)),
                }
                match = await page.evaluate(
                    _BROWSER_PAGINATION_CONTROL_MATCH_SCRIPT, target
                )
                if not isinstance(match, dict) or not isinstance(
                    match.get("index"), int
                ):
                    continue
                if bool(match.get("disabled")):
                    continue
                body_before = await page.locator("body").inner_text()
                links_before = await _browser_link_signature(page)
                await (
                    page.locator(target["tag"])
                    .nth(int(match["index"]))
                    .click(
                        timeout=BROWSER_PAGINATION_CHANGE_TIMEOUT_MS,
                    )
                )
                changed, _, _ = await _wait_for_same_page_content_change(
                    page,
                    body_before=body_before,
                    links_before=links_before,
                )
                if not changed:
                    continue
                await page.wait_for_timeout(350)
                html = await page.content()
                snapshot = _snapshot_from_browser_html(
                    html=html,
                    final_url=str(getattr(page, "url", "") or absolute_url),
                    absolute_url=absolute_url,
                )
                if snapshot.status != "succeeded":
                    continue
                if isinstance(response_status, int):
                    snapshot.http_status_code = response_status
                fingerprint = _pagination_snapshot_fingerprint(snapshot)
                if fingerprint in seen_fingerprints:
                    continue
                seen_fingerprints.add(fingerprint)
                snapshots.append(snapshot)
            await _remember_browser_session_cookies(context, browser_session_scope)
            return BrowserSamePageExpansion(
                status="succeeded",
                snapshots=tuple(snapshots),
                stopped_reason="controls_processed",
            )
    except Exception as exc:
        return BrowserSamePageExpansion(
            status="failed",
            stopped_reason="browser_error",
            error_message=_format_exception_for_snapshot(
                exc,
                "Playwright same-page expansion failed",
            ),
        )
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass


async def _fetch_browser_pagination_direct(
    absolute_url: str,
    target: dict[str, object],
    *,
    intent: CrawlPageIntent,
    max_pages: int,
    browser_session_scope: BrowserSessionScope | None = None,
) -> BrowserPaginationExpansion:
    last_result: BrowserPaginationExpansion | None = None
    for _attempt in range(MAX_RETRIES_FOR_BROWSER_RENDER + 1):
        result = await _try_fetch_browser_pagination_once(
            absolute_url,
            target,
            intent=intent,
            max_pages=max_pages,
            browser_session_scope=browser_session_scope,
        )
        if _is_certificate_date_error(result.error_message):
            return await _try_fetch_browser_pagination_once(
                absolute_url,
                target,
                intent=intent,
                max_pages=max_pages,
                ignore_https_errors=True,
                browser_session_scope=browser_session_scope,
            )
        if result.status == "succeeded" or result.stopped_reason != "browser_error":
            return result
        last_result = result
    return last_result or BrowserPaginationExpansion(
        status="failed",
        stopped_reason="browser_error",
        error_message="Playwright browser pagination failed",
    )


async def _try_fetch_browser_pagination_once(
    absolute_url: str,
    target: dict[str, object],
    *,
    intent: CrawlPageIntent,
    max_pages: int,
    ignore_https_errors: bool = False,
    browser_session_scope: BrowserSessionScope | None = None,
) -> BrowserPaginationExpansion:
    playwright_factory = _get_async_playwright()
    if playwright_factory is None:
        return BrowserPaginationExpansion(
            status="failed",
            stopped_reason="playwright_unavailable",
            error_message="Playwright browser pagination unavailable",
        )

    options = _browser_fetch_options_for_intent(intent)
    browser = None
    try:
        async with playwright_factory() as playwright:
            browser = await playwright.chromium.launch(**_playwright_launch_options())
            context = await browser.new_context(
                user_agent=options.user_agent,
                ignore_https_errors=ignore_https_errors,
            )
            used_cached_cookies = await _restore_browser_session_cookies(
                context,
                browser_session_scope,
                absolute_url,
            )
            page = await context.new_page()
            await _install_browser_bandwidth_policy(page)
            navigation_response = await page.goto(
                absolute_url,
                wait_until=options.wait_until,
                timeout=options.page_timeout_ms,
            )
            response_status = getattr(navigation_response, "status", None)
            if options.wait_for:
                selector = options.wait_for
                if selector.startswith("css:"):
                    selector = selector[4:]
                await page.wait_for_selector(
                    selector,
                    timeout=options.wait_for_timeout_ms,
                )
            if response_status in BROWSER_FALLBACK_STATUS:
                await page.wait_for_timeout(BROWSER_RESTRICTED_RESPONSE_SETTLE_MS)
            if options.wait_for_dynamic_directory:
                initial_html, _ = await _wait_for_dynamic_directory_html(
                    page,
                    absolute_url=absolute_url,
                    options=options,
                )
            elif options.delay_before_return_html_seconds > 0:
                await page.wait_for_timeout(
                    options.delay_before_return_html_seconds * 1000
                )
                initial_html = await page.content()
            else:
                initial_html = await page.content()
            initial_url = str(getattr(page, "url", "") or absolute_url)
            initial_snapshot = _snapshot_from_browser_html(
                html=initial_html,
                final_url=initial_url,
                absolute_url=absolute_url,
            )
            if isinstance(response_status, int):
                initial_snapshot.http_status_code = response_status
            await _remember_browser_session_cookies(
                context,
                browser_session_scope,
            )
            if (
                used_cached_cookies
                and browser_session_scope is not None
                and _browser_snapshot_unusable_after_cached_cookies(initial_snapshot)
            ):
                browser_cookie_session_cache.discard_for_url(
                    browser_session_scope,
                    absolute_url,
                )
                return BrowserPaginationExpansion(
                    status="failed",
                    stopped_reason="browser_error",
                    error_message=(
                        "Playwright browser pagination rejected cached browser session"
                    ),
                )
            if initial_snapshot.suspicious_empty:
                return BrowserPaginationExpansion(
                    status="failed",
                    stopped_reason="browser_error",
                    error_message="Playwright browser pagination returned empty page content",
                )
            seen_fingerprints = {_pagination_snapshot_fingerprint(initial_snapshot)}
            initial_link_signature = await _browser_link_signature(page)
            seen_link_signatures = {initial_link_signature}
            try:
                pagination_state = await _browser_pagination_state(page)
            except Exception:
                pagination_state = None
            if _should_use_page_number_pagination(pagination_state):
                return await _collect_browser_pagination_by_page_number(
                    page,
                    absolute_url=absolute_url,
                    options=options,
                    initial_state=pagination_state,
                    max_pages=max_pages,
                    seen_fingerprints=seen_fingerprints,
                )
            dynamic_link_pagination = False
            snapshots: list[PageSnapshot] = []
            stopped_reason = "page_limit_reached"

            for _ in range(max(1, int(max_pages)) - 1):
                match = await page.evaluate(
                    _BROWSER_PAGINATION_CONTROL_MATCH_SCRIPT,
                    target,
                )
                if not isinstance(match, dict) or not isinstance(
                    match.get("index"), int
                ):
                    if snapshots:
                        stopped_reason = "control_disappeared"
                        break
                    return BrowserPaginationExpansion(
                        status="failed",
                        stopped_reason="control_not_found",
                        error_message="重新打开页面后未找到模型选择的分页控件",
                    )
                if bool(match.get("disabled")):
                    stopped_reason = "control_disabled"
                    break

                changed = False
                links_before: tuple[str, ...] = ()
                links_after: tuple[str, ...] = ()
                for _click_attempt in range(MAX_BROWSER_PAGINATION_CLICK_RETRIES + 1):
                    body_before = await page.locator("body").inner_text()
                    links_before = await _browser_link_signature(page)
                    await (
                        page.locator(str(target["tag"]))
                        .nth(int(match["index"]))
                        .click(
                            timeout=BROWSER_PAGINATION_CHANGE_TIMEOUT_MS,
                        )
                    )
                    changed, _, links_after = await _wait_for_browser_content_change(
                        page,
                        body_before=body_before,
                        links_before=links_before,
                    )
                    if changed:
                        break
                if not changed:
                    stopped_reason = "content_unchanged"
                    break
                try:
                    await page.wait_for_load_state("networkidle", timeout=3000)
                except Exception:
                    pass
                await page.wait_for_timeout(350)
                links_after = await _browser_link_signature(page)
                if links_after and links_after != links_before:
                    dynamic_link_pagination = True
                if dynamic_link_pagination and links_after in seen_link_signatures:
                    stopped_reason = "content_repeated"
                    break
                html = await page.content()
                final_url = str(getattr(page, "url", "") or absolute_url)
                snapshot = _snapshot_from_browser_html(
                    html=html,
                    final_url=final_url,
                    absolute_url=absolute_url,
                )
                fingerprint = _pagination_snapshot_fingerprint(snapshot)
                if fingerprint in seen_fingerprints:
                    stopped_reason = "content_repeated"
                    break
                seen_fingerprints.add(fingerprint)
                seen_link_signatures.add(links_after)
                snapshots.append(snapshot)

            return BrowserPaginationExpansion(
                status="succeeded",
                snapshots=tuple(snapshots),
                stopped_reason=stopped_reason,
            )
    except Exception as exc:
        return BrowserPaginationExpansion(
            status="failed",
            stopped_reason="browser_error",
            error_message=_format_exception_for_snapshot(
                exc,
                "Playwright browser pagination failed",
            ),
        )
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass


async def _wait_for_browser_content_change(
    page: Any,
    *,
    body_before: str,
    links_before: tuple[str, ...],
) -> tuple[bool, str, tuple[str, ...]]:
    elapsed_ms = 0
    latest_body = body_before
    latest_links = links_before
    while elapsed_ms < BROWSER_PAGINATION_CHANGE_TIMEOUT_MS:
        await page.wait_for_timeout(250)
        elapsed_ms += 250
        latest_body = await page.locator("body").inner_text()
        latest_links = await _browser_link_signature(page)
        if latest_links and latest_links != links_before:
            return True, latest_body, latest_links
        if elapsed_ms >= 1500 and _body_content_changed_substantially(
            body_before,
            latest_body,
        ):
            return True, latest_body, latest_links
    return False, latest_body, latest_links


async def _wait_for_same_page_content_change(
    page: Any,
    *,
    body_before: str,
    links_before: tuple[str, ...],
) -> tuple[bool, str, tuple[str, ...]]:
    """Wait past the transient empty state produced by AJAX list replacement."""

    elapsed_ms = 0
    latest_body = body_before
    latest_links = links_before
    changed_at_ms: int | None = None
    stable_ms = 0
    while elapsed_ms < BROWSER_PAGINATION_CHANGE_TIMEOUT_MS:
        await page.wait_for_timeout(250)
        elapsed_ms += 250
        latest_body = await page.locator("body").inner_text()
        latest_links = await _browser_link_signature(page)
        body_changed = _body_content_changed_substantially(body_before, latest_body)
        links_changed = latest_links != links_before
        if changed_at_ms is None and (body_changed or links_changed):
            changed_at_ms = elapsed_ms
            stable_ms = 0
        if changed_at_ms is None:
            continue
        # A list click often clears the old anchors before the new response
        # arrives.  Do not capture that intermediate empty state as a page.
        if latest_links and latest_links != links_before:
            stable_ms += 250
            if stable_ms >= DYNAMIC_DIRECTORY_STABLE_MS:
                return True, latest_body, latest_links
        elif elapsed_ms - changed_at_ms >= 1500 and body_changed:
            return True, latest_body, latest_links
    return False, latest_body, latest_links


async def _browser_link_signature(page: Any) -> tuple[str, ...]:
    values = await page.evaluate(
        """
        () => Array.from(document.querySelectorAll('a[href]')).map((element) => {
          const text = String(element.innerText || '').replace(/\\s+/g, ' ').trim();
          return `${element.href} ${text}`;
        })
        """
    )
    if not isinstance(values, list):
        return ()
    return tuple(str(value) for value in values)


async def _browser_pagination_state(page: Any) -> dict[str, int | None] | None:
    state = await page.evaluate(_BROWSER_PAGINATION_STATE_SCRIPT)
    if not isinstance(state, dict):
        return None
    normalized: dict[str, int | None] = {}
    for key in ("currentPage", "pageCount", "inputIndex", "jumpControlIndex"):
        value = state.get(key)
        normalized[key] = (
            value if isinstance(value, int) and not isinstance(value, bool) else None
        )
    return normalized


def _should_use_page_number_pagination(
    state: dict[str, int | None] | None,
) -> bool:
    if state is None:
        return False
    return (
        isinstance(state.get("currentPage"), int)
        and int(state["currentPage"] or 0) > 0
        and isinstance(state.get("pageCount"), int)
        and int(state["pageCount"] or 0) > 0
        and isinstance(state.get("inputIndex"), int)
        and int(state["inputIndex"]) >= 0
        and isinstance(state.get("jumpControlIndex"), int)
        and int(state["jumpControlIndex"]) >= 0
    )


async def _collect_browser_pagination_by_page_number(
    page: Any,
    *,
    absolute_url: str,
    options: BrowserFetchOptions,
    initial_state: dict[str, int | None],
    max_pages: int,
    seen_fingerprints: set[str],
) -> BrowserPaginationExpansion:
    snapshots: list[PageSnapshot] = []
    current_page = int(initial_state["currentPage"] or 1)
    page_count = int(initial_state["pageCount"] or current_page)
    last_page = min(page_count, max(1, int(max_pages)))
    stopped_reason = "page_limit_reached"

    for target_page in range(current_page + 1, last_page + 1):
        try:
            await page.goto(
                absolute_url,
                wait_until=options.wait_until,
                timeout=options.page_timeout_ms,
            )
            if options.wait_for:
                selector = options.wait_for
                if selector.startswith("css:"):
                    selector = selector[4:]
                await page.wait_for_selector(
                    selector,
                    timeout=options.wait_for_timeout_ms,
                )
            if options.wait_for_dynamic_directory:
                await _wait_for_dynamic_directory_html(
                    page,
                    absolute_url=absolute_url,
                    options=options,
                )
            elif options.delay_before_return_html_seconds > 0:
                await page.wait_for_timeout(
                    options.delay_before_return_html_seconds * 1000
                )
            state = await _browser_pagination_state(page)
            if not _should_use_page_number_pagination(state):
                stopped_reason = "page_jump_controls_disappeared"
                break
            await page.evaluate(
                _BROWSER_PAGINATION_JUMP_SCRIPT,
                {
                    "inputIndex": state["inputIndex"],
                    "jumpControlIndex": state["jumpControlIndex"],
                    "targetPage": target_page,
                },
            )
            if not await _wait_for_browser_pagination_page(
                page,
                expected_page=target_page,
            ):
                stopped_reason = "page_jump_timeout"
                break
            await page.wait_for_timeout(350)
            html = await page.content()
            final_url = str(getattr(page, "url", "") or absolute_url)
            snapshot = _snapshot_from_browser_html(
                html=html,
                final_url=final_url,
                absolute_url=absolute_url,
            )
            fingerprint = _pagination_snapshot_fingerprint(snapshot)
            if fingerprint in seen_fingerprints:
                stopped_reason = "content_repeated"
                break
            seen_fingerprints.add(fingerprint)
            snapshots.append(snapshot)
        except Exception as exc:
            stopped_reason = "page_jump_failed"
            if not snapshots:
                return BrowserPaginationExpansion(
                    status="failed",
                    stopped_reason="browser_error",
                    error_message=_format_exception_for_snapshot(
                        exc,
                        "Playwright browser pagination failed",
                    ),
                )
            break

    if not snapshots and stopped_reason != "page_limit_reached":
        return BrowserPaginationExpansion(
            status="failed",
            stopped_reason="browser_error",
            error_message=(
                f"Playwright browser pagination page jump failed: {stopped_reason}"
            ),
        )
    return BrowserPaginationExpansion(
        status="succeeded",
        snapshots=tuple(snapshots),
        stopped_reason=stopped_reason,
    )


async def _wait_for_browser_pagination_page(
    page: Any,
    *,
    expected_page: int,
) -> bool:
    elapsed_ms = 0
    while elapsed_ms < BROWSER_PAGINATION_CHANGE_TIMEOUT_MS:
        state = await _browser_pagination_state(page)
        if state is not None and state.get("currentPage") == expected_page:
            return True
        await page.wait_for_timeout(250)
        elapsed_ms += 250
    return False


def _body_content_changed_substantially(before: str, after: str) -> bool:
    if not after or after == before:
        return False
    return SequenceMatcher(None, before, after, autojunk=False).ratio() < 0.995


def _pagination_snapshot_fingerprint(snapshot: PageSnapshot) -> str:
    payload = f"{snapshot.url}\n{snapshot.text}\n" + "\n".join(snapshot.links)
    return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()


_BROWSER_PAGINATION_CONTROL_MATCH_SCRIPT = """
(target) => {
  const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim().slice(0, 240);
  const requiredClasses = Array.isArray(target.classTokens) ? target.classTokens : [];
  const matches = [];
  const nodes = Array.from(document.querySelectorAll(target.tag));
  nodes.forEach((element, index) => {
    const descendant = element.querySelector('[aria-label]');
    const ariaLabel = normalize(
      element.getAttribute('aria-label') || (descendant && descendant.getAttribute('aria-label'))
    );
    const classes = new Set(Array.from(element.classList || []));
    if (target.text && normalize(element.innerText) !== target.text) return;
    if (target.title && normalize(element.getAttribute('title')) !== target.title) return;
    if (target.ariaLabel && ariaLabel !== target.ariaLabel) return;
    if (!requiredClasses.every((token) => classes.has(token))) return;
    const disabled = Boolean(element.disabled)
      || normalize(element.getAttribute('aria-disabled')).toLowerCase() === 'true'
      || Array.from(classes).some((token) => token.toLowerCase().includes('disabled'));
    matches.push({index, disabled});
  });
  return matches[Math.max(0, Number(target.matchIndex) || 0)] || null;
}
"""


_BROWSER_PAGINATION_STATE_SCRIPT = """
/* crawler-pagination-state */
() => {
  const numberValue = (value) => {
    const match = String(value || '').replace(/,/g, '').match(/\\d+/);
    return match ? Number(match[0]) : null;
  };
  const visible = (element) => {
    if (!element) return false;
    const style = window.getComputedStyle(element);
    return style.display !== 'none' && style.visibility !== 'hidden'
      && element.getClientRects().length > 0;
  };
  const current = Array.from(document.querySelectorAll(
    '[curr_page], [data-current-page], [aria-current="page"], .curr_page'
  )).map((element) => numberValue(
    element.getAttribute('curr_page')
      || element.getAttribute('data-current-page')
      || element.textContent
  )).find((value) => value !== null) || null;
  const pageCountFromAttribute = Array.from(document.querySelectorAll(
    '[pagecount], [pageCount], [data-page-count], .all_pages'
  )).map((element) => numberValue(
    element.getAttribute('pagecount')
      || element.getAttribute('pageCount')
      || element.getAttribute('data-page-count')
      || element.textContent
  )).find((value) => value !== null) || null;
  const perPage = Array.from(document.querySelectorAll(
    '.per_count, [data-page-size], [page-size]'
  )).map((element) => numberValue(element.textContent || element.getAttribute('data-page-size') || element.getAttribute('page-size')))
    .find((value) => value !== null) || null;
  const totalRecords = Array.from(document.querySelectorAll(
    '.all_count em, [data-total], [total]'
  )).map((element) => numberValue(element.textContent || element.getAttribute('data-total') || element.getAttribute('total')))
    .find((value) => value !== null) || null;
  const pageCount = pageCountFromAttribute || (
    perPage && totalRecords ? Math.ceil(totalRecords / perPage) : null
  );
  const inputs = Array.from(document.querySelectorAll('input'));
  const inputIndex = inputs.findIndex((element) => {
    if (!visible(element) || ['hidden', 'button', 'submit'].includes(String(element.type || '').toLowerCase())) return false;
    const signal = `${element.id} ${element.className} ${element.name}`.toLowerCase();
    return /page|pager|pagination|jump/.test(signal);
  });
  const controls = Array.from(document.querySelectorAll('a, button, [role="button"]'));
  const jumpControlIndex = controls.findIndex((element) => {
    if (!visible(element)) return false;
    const signal = `${element.textContent || ''} ${element.id} ${element.className} ${element.getAttribute('aria-label') || ''}`.toLowerCase();
    return /jump|go|跳转|转到/.test(signal);
  });
  return {
    currentPage: current,
    pageCount,
    inputIndex: inputIndex >= 0 ? inputIndex : null,
    jumpControlIndex: jumpControlIndex >= 0 ? jumpControlIndex : null,
  };
}
"""


_BROWSER_PAGINATION_JUMP_SCRIPT = """
/* crawler-pagination-jump */
({inputIndex, jumpControlIndex, targetPage}) => {
  const inputs = Array.from(document.querySelectorAll('input'));
  const controls = Array.from(document.querySelectorAll('a, button, [role="button"]'));
  const input = inputs[Number(inputIndex)];
  const control = controls[Number(jumpControlIndex)];
  if (!input || !control) return false;
  input.value = String(targetPage);
  input.dispatchEvent(new Event('input', {bubbles: true}));
  input.dispatchEvent(new Event('change', {bubbles: true}));
  setTimeout(() => control.click(), 0);
  return true;
}
"""


async def _try_playwright_browser_fetch(
    absolute_url: str,
    options: BrowserFetchOptions,
    *,
    browser_session_scope: BrowserSessionScope | None = None,
) -> PageSnapshot:
    last_result: PageSnapshot | None = None
    max_attempts = max(0, options.max_retries) + 1
    for attempt in range(max_attempts):
        last_result = await _try_playwright_browser_fetch_once(
            absolute_url,
            options,
            browser_session_scope=browser_session_scope,
        )
        if not options.ignore_https_errors and _is_certificate_date_error(
            last_result.error_message
        ):
            compatibility_options = replace(
                options,
                ignore_https_errors=True,
                max_retries=0,
            )
            return await _try_playwright_browser_fetch_once(
                absolute_url,
                compatibility_options,
                browser_session_scope=browser_session_scope,
            )
        if _is_immediate_http_compatibility_error(absolute_url, last_result):
            return last_result
        if _is_transient_http_status(last_result.http_status_code):
            if attempt + 1 < max_attempts:
                await asyncio.sleep(BROWSER_TRANSIENT_RETRY_DELAY_SECONDS)
                continue
            status_code = last_result.http_status_code
            return last_result.model_copy(
                update={
                    "status": "failed",
                    "error_message": (
                        f"Playwright browser fetch returned temporary HTTP {status_code}"
                    ),
                    "suspicious_empty": True,
                }
            )
        if _looks_like_sparse_browser_directory_shell(last_result, options=options):
            if attempt + 1 < max_attempts:
                await asyncio.sleep(BROWSER_SPARSE_DIRECTORY_RETRY_DELAY_SECONDS)
                continue
            return last_result.model_copy(
                update={
                    "status": "failed",
                    "error_message": (
                        "Playwright browser fetch returned sparse directory shell after retry"
                    ),
                    "suspicious_empty": True,
                }
            )
        if last_result.status == "succeeded" or _is_wait_condition_failure(
            last_result.error_message
        ):
            return last_result
        if attempt + 1 < max_attempts and _looks_like_transient_browser_error(
            last_result.error_message
        ):
            await asyncio.sleep(BROWSER_TRANSIENT_RETRY_DELAY_SECONDS)
    return last_result or _failed_snapshot(
        url=absolute_url,
        fetch_method="browser",
        error_message="Playwright browser fetch failed",
    )


async def _try_playwright_browser_fetch_once(
    absolute_url: str,
    options: BrowserFetchOptions,
    *,
    browser_session_scope: BrowserSessionScope | None = None,
    _allow_cached_cookie_reset_retry: bool = True,
) -> PageSnapshot:
    playwright_factory = _get_async_playwright()
    if playwright_factory is None:
        return _failed_snapshot(
            url=absolute_url,
            fetch_method="browser",
            error_message="Playwright browser fetch unavailable: failed to import playwright",
        )

    browser = None
    profile_ready = True
    http_status_code: int | None = None
    used_cached_cookies = False
    try:
        async with playwright_factory() as playwright:
            browser = await playwright.chromium.launch(**_playwright_launch_options())
            context = await browser.new_context(
                user_agent=options.user_agent,
                ignore_https_errors=options.ignore_https_errors,
            )
            used_cached_cookies = await _restore_browser_session_cookies(
                context,
                browser_session_scope,
                absolute_url,
            )
            page = await context.new_page()
            await _install_browser_bandwidth_policy(page)
            navigation_response = await page.goto(
                absolute_url,
                wait_until=options.wait_until,
                timeout=options.page_timeout_ms,
            )
            response_status = getattr(navigation_response, "status", None)
            if isinstance(response_status, int):
                http_status_code = response_status
            if options.wait_for:
                selector = options.wait_for
                if selector.startswith("css:"):
                    selector = selector[4:]
                await page.wait_for_selector(
                    selector,
                    timeout=options.wait_for_timeout_ms,
                )
            if http_status_code in BROWSER_FALLBACK_STATUS:
                await page.wait_for_timeout(BROWSER_RESTRICTED_RESPONSE_SETTLE_MS)
            has_child_frames = len(getattr(page, "frames", ())) > 1
            if options.wait_for_dynamic_directory and not has_child_frames:
                html, _ = await _wait_for_dynamic_directory_html(
                    page,
                    absolute_url=absolute_url,
                    options=options,
                )
            elif options.wait_for_dynamic_profile and not has_child_frames:
                html, profile_ready = await _wait_for_dynamic_profile_html(
                    page,
                    absolute_url=absolute_url,
                    options=options,
                )
            elif options.delay_before_return_html_seconds > 0:
                await page.wait_for_timeout(
                    options.delay_before_return_html_seconds * 1000
                )
                html = await page.content()
            else:
                html = await page.content()
            final_url = str(getattr(page, "url", "") or absolute_url)
            embedded_documents = await _collect_browser_embedded_documents(
                page,
                absolute_url=final_url,
            )
            if embedded_documents:
                profile_ready = True
            await _remember_browser_session_cookies(
                context,
                browser_session_scope,
            )
    except Exception as exc:
        return _failed_snapshot(
            url=absolute_url,
            fetch_method="browser",
            error_message=_format_exception_for_snapshot(
                exc,
                "Playwright browser fetch failed",
            ),
            http_status_code=http_status_code,
        )
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass

    snapshot = _snapshot_from_browser_html(
        html=html,
        final_url=final_url,
        absolute_url=absolute_url,
        embedded_documents=embedded_documents,
    )
    snapshot.http_status_code = http_status_code
    if _looks_like_sparse_browser_directory_shell(snapshot, options=options):
        snapshot.suspicious_empty = True
    if options.wait_for_dynamic_profile and not profile_ready:
        snapshot.suspicious_empty = True
    if (
        used_cached_cookies
        and browser_session_scope is not None
        and _allow_cached_cookie_reset_retry
        and _browser_snapshot_unusable_after_cached_cookies(snapshot)
    ):
        browser_cookie_session_cache.discard_for_url(
            browser_session_scope,
            absolute_url,
        )
        return await _try_playwright_browser_fetch_once(
            absolute_url,
            options,
            browser_session_scope=browser_session_scope,
            _allow_cached_cookie_reset_retry=False,
        )
    return snapshot


async def _restore_browser_session_cookies(
    context: Any,
    browser_session_scope: BrowserSessionScope | None,
    absolute_url: str,
) -> bool:
    if browser_session_scope is None:
        return False
    cookies = browser_cookie_session_cache.get_for_url(
        browser_session_scope,
        absolute_url,
    )
    if cookies:
        await context.add_cookies(list(cookies))
        return True
    return False


async def _remember_browser_session_cookies(
    context: Any,
    browser_session_scope: BrowserSessionScope | None,
) -> None:
    if browser_session_scope is None:
        return
    try:
        cookies = await context.cookies()
    except Exception:
        return
    browser_cookie_session_cache.remember(browser_session_scope, cookies)


def _looks_like_sparse_browser_directory_shell(
    snapshot: PageSnapshot,
    *,
    options: BrowserFetchOptions,
) -> bool:
    if (
        not options.wait_for_dynamic_directory
        or snapshot.status != "succeeded"
        or snapshot.http_status_code not in BROWSER_FALLBACK_STATUS
    ):
        return False
    normalized_text = " ".join((snapshot.text or "").split())
    return (
        len(normalized_text) <= BROWSER_SPARSE_DIRECTORY_MAX_TEXT_CHARS
        and len(snapshot.html or "") <= BROWSER_SPARSE_DIRECTORY_MAX_HTML_CHARS
        and len(set(snapshot.links or ())) <= BROWSER_SPARSE_DIRECTORY_MAX_LINKS
    )


def _is_transient_http_status(status_code: int | None) -> bool:
    return bool(
        status_code is not None
        and (
            status_code in TRANSIENT_HTTP_STATUS_CODES
            or TRANSIENT_SERVER_STATUS_MIN <= status_code <= TRANSIENT_SERVER_STATUS_MAX
        )
    )


def _looks_like_transient_browser_error(message: str | None) -> bool:
    normalized = (message or "").lower()
    return bool(normalized) and any(
        marker in normalized for marker in _TRANSIENT_BROWSER_ERROR_MARKERS
    )


def _browser_snapshot_unusable_after_cached_cookies(snapshot: PageSnapshot) -> bool:
    if snapshot.suspicious_empty:
        return True
    return snapshot.http_status_code in {400, 401, 403, 429}


async def _collect_browser_embedded_documents(
    page: Any,
    *,
    absolute_url: str,
) -> tuple[tuple[str, str], ...]:
    """Collect one bounded level of same-host frame documents.

    Frame pages are common for older faculty sites. Their outer frameset has
    no visible body, while the actual profile lives in a child document. We
    deliberately keep this to same-host frames and one level so it cannot turn
    into arbitrary recursive browsing.
    """

    parent_host = (urlparse(absolute_url).hostname or "").lower()
    documents: list[tuple[str, str]] = []
    for frame in list(getattr(page, "frames", ())):
        if frame is getattr(page, "main_frame", None):
            continue
        frame_url = str(getattr(frame, "url", "") or "")
        parsed = urlparse(frame_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.hostname.lower() != parent_host
            or not is_safe_public_crawl_url(frame_url)
        ):
            continue
        try:
            frame_html = await frame.content()
        except Exception:
            continue
        if frame_html:
            documents.append((frame_url, frame_html))
        if len(documents) >= MAX_EMBEDDED_FRAME_DOCUMENTS:
            break
    return tuple(documents)


async def _wait_for_dynamic_directory_html(
    page: Any,
    *,
    absolute_url: str,
    options: BrowserFetchOptions,
) -> tuple[str, bool]:
    timeout_ms = max(0, int(options.dynamic_directory_ready_timeout_ms))
    poll_ms = max(1, int(options.dynamic_directory_ready_poll_ms))
    stable_ms = max(0, int(options.dynamic_directory_stable_ms))
    elapsed_ms = 0
    stable_elapsed_ms = 0
    ready_signature: str | None = None
    latest_html = ""
    best_html = ""
    best_quality: tuple[int, int, int] = (-1, -1, -1)

    while True:
        latest_html = await _try_read_browser_page_content(page)
        if latest_html is None:
            ready_signature = None
            stable_elapsed_ms = 0
        else:
            final_url = str(getattr(page, "url", "") or absolute_url)
            snapshot = _snapshot_from_browser_html(
                html=latest_html,
                final_url=final_url,
                absolute_url=absolute_url,
            )
            quality = _dynamic_directory_snapshot_quality(snapshot)
            if quality > best_quality:
                best_html = latest_html
                best_quality = quality
            if (
                snapshot.status == "succeeded"
                and not snapshot.suspicious_empty
                and not looks_like_unrendered_dynamic_teacher_directory(snapshot)
            ):
                signature = _dynamic_directory_render_signature(snapshot)
                if signature == ready_signature:
                    stable_elapsed_ms += poll_ms
                else:
                    ready_signature = signature
                    stable_elapsed_ms = 0
                if stable_elapsed_ms >= stable_ms:
                    return best_html or latest_html, True
            else:
                stable_elapsed_ms = 0
                ready_signature = None

        if elapsed_ms >= timeout_ms:
            return best_html or latest_html, False
        wait_ms = min(poll_ms, timeout_ms - elapsed_ms)
        if wait_ms <= 0:
            return best_html or latest_html, False
        await page.wait_for_timeout(wait_ms)
        elapsed_ms += wait_ms


def _dynamic_directory_snapshot_quality(snapshot: PageSnapshot) -> tuple[int, int, int]:
    unique_links = set(snapshot.links or [])
    return (
        len(unique_links),
        len(snapshot.links or []),
        len(" ".join((snapshot.text or "").split())),
    )


async def _wait_for_dynamic_profile_html(
    page: Any,
    *,
    absolute_url: str,
    options: BrowserFetchOptions,
) -> tuple[str, bool]:
    timeout_ms = max(0, int(options.dynamic_profile_ready_timeout_ms))
    poll_ms = max(1, int(options.dynamic_profile_ready_poll_ms))
    stable_ms = max(0, int(options.dynamic_profile_stable_ms))
    elapsed_ms = 0
    stable_elapsed_ms = 0
    ready_signature: str | None = None
    best_html = ""
    best_quality: tuple[int, int, int] = (-1, -1, -1)

    while True:
        latest_html = await _try_read_browser_page_content(page)
        if latest_html is None:
            ready_signature = None
            stable_elapsed_ms = 0
        else:
            final_url = str(getattr(page, "url", "") or absolute_url)
            snapshot = _snapshot_from_browser_html(
                html=latest_html,
                final_url=final_url,
                absolute_url=absolute_url,
            )
            quality = _dynamic_profile_snapshot_quality(snapshot)
            if quality > best_quality:
                best_html = latest_html
                best_quality = quality

            if profile_text_has_meaningful_content(snapshot.text):
                signature = _dynamic_directory_render_signature(snapshot)
                if signature == ready_signature:
                    stable_elapsed_ms += poll_ms
                else:
                    ready_signature = signature
                    stable_elapsed_ms = 0
                if stable_elapsed_ms >= stable_ms:
                    return latest_html, True
            else:
                stable_elapsed_ms = 0
                ready_signature = None

        if elapsed_ms >= timeout_ms:
            return best_html or latest_html, False
        wait_ms = min(poll_ms, timeout_ms - elapsed_ms)
        if wait_ms <= 0:
            return best_html or latest_html, False
        await page.wait_for_timeout(wait_ms)
        elapsed_ms += wait_ms


def profile_text_has_meaningful_content(text: str | None) -> bool:
    normalized_text = " ".join((text or "").split())
    if not normalized_text:
        return False
    return bool(extract_first_email_from_text(normalized_text)) or (
        len(normalized_text) >= DYNAMIC_PROFILE_MEANINGFUL_TEXT_CHARS
    )


def _dynamic_profile_snapshot_quality(snapshot: PageSnapshot) -> tuple[int, int, int]:
    normalized_text = " ".join((snapshot.text or "").split())
    return (
        int(bool(extract_first_email_from_text(normalized_text))),
        len(normalized_text),
        len(snapshot.links or []),
    )


def _dynamic_directory_render_signature(snapshot: PageSnapshot) -> str:
    content = snapshot.text + "\0" + "\n".join(snapshot.links)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _is_browser_content_navigation_error(exc: BaseException) -> bool:
    normalized = str(exc).casefold()
    return all(
        marker in normalized for marker in _BROWSER_CONTENT_NAVIGATION_ERROR_MARKERS
    )


async def _try_read_browser_page_content(page: Any) -> str | None:
    try:
        return await page.content()
    except Exception as exc:
        if _is_browser_content_navigation_error(exc):
            return None
        raise


def _is_wait_condition_failure(message: str | None) -> bool:
    normalized_message = (message or "").lower()
    return "wait condition failed" in normalized_message or (
        "wait_for_selector" in normalized_message
        and "timeout" in normalized_message
        and "exceeded" in normalized_message
    )


def _is_certificate_date_error(message: str | None) -> bool:
    normalized_message = (message or "").strip().lower()
    return any(
        marker in normalized_message for marker in CERTIFICATE_DATE_ERROR_MARKERS
    )


def _snapshot_from_browser_html(
    *,
    html: str,
    final_url: str,
    absolute_url: str,
    embedded_documents: Sequence[tuple[str, str]] = (),
) -> PageSnapshot:
    if not html:
        return _failed_snapshot(
            url=absolute_url,
            fetch_method="browser",
            error_message="Playwright browser fetch returned empty HTML",
            suspicious_empty=True,
        )

    snapshot = html_to_snapshot(
        final_url or absolute_url,
        html,
        "browser",
        embedded_documents=embedded_documents,
    )
    if not snapshot.text.strip():
        snapshot.suspicious_empty = True
    return snapshot


def _run_browser_fetch_with_proactor_loop(
    absolute_url: str,
    goal: str,
    intent: CrawlPageIntent = "generic",
    browser_session_scope: BrowserSessionScope | None = None,
) -> PageSnapshot:
    from app.core.windows_event_loop import ensure_windows_proactor_event_loop_policy

    ensure_windows_proactor_event_loop_policy()
    return asyncio.run(
        _fetch_page_with_playwright_direct(
            absolute_url,
            goal,
            intent,
            browser_session_scope=browser_session_scope,
        )
    )


def _run_browser_pagination_with_proactor_loop(
    absolute_url: str,
    target: dict[str, object],
    intent: CrawlPageIntent,
    max_pages: int,
    browser_session_scope: BrowserSessionScope | None = None,
) -> BrowserPaginationExpansion:
    from app.core.windows_event_loop import ensure_windows_proactor_event_loop_policy

    ensure_windows_proactor_event_loop_policy()
    return asyncio.run(
        _fetch_browser_pagination_direct(
            absolute_url,
            target,
            intent=intent,
            max_pages=max_pages,
            browser_session_scope=browser_session_scope,
        )
    )


def _run_browser_same_page_controls_with_proactor_loop(
    absolute_url: str,
    controls: Sequence[dict[str, object]],
    intent: CrawlPageIntent,
    browser_session_scope: BrowserSessionScope | None = None,
) -> BrowserSamePageExpansion:
    from app.core.windows_event_loop import ensure_windows_proactor_event_loop_policy

    ensure_windows_proactor_event_loop_policy()
    return asyncio.run(
        _fetch_browser_same_page_controls_direct(
            absolute_url,
            controls,
            intent=intent,
            browser_session_scope=browser_session_scope,
        )
    )


def _should_offload_browser_fetch_to_thread() -> bool:
    if platform.system() != "Windows":
        return False

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False

    proactor_type = getattr(asyncio, "ProactorEventLoop", None)
    if proactor_type is not None and isinstance(loop, proactor_type):
        return False

    return True


def _format_exception_for_snapshot(exc: BaseException, context: str) -> str:
    message = str(exc).strip()
    if message:
        return f"{context}: {type(exc).__name__}: {message}"
    return f"{context}: {type(exc).__name__}"


def _failed_snapshot(
    url: str,
    fetch_method: str,
    error_message: str,
    *,
    suspicious_empty: bool = False,
    http_status_code: int | None = None,
) -> PageSnapshot:
    return PageSnapshot(
        url=url,
        title=None,
        text="",
        html="",
        links=[],
        fetch_method=fetch_method,
        status="failed",
        http_status_code=http_status_code,
        error_message=error_message,
        suspicious_empty=suspicious_empty,
    )
