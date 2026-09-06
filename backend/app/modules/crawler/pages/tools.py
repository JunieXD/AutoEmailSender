from __future__ import annotations

import asyncio
import re
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.crawl_job import (
    CrawlCandidate,
)
from app.modules.llm.public import LLMRuntimeAdaptation

from ..runtime.lease import CrawlerClaimFence
from .browser import (
    _AT_REPLACEMENTS as _AT_REPLACEMENTS,
    _BROWSER_CONTENT_NAVIGATION_ERROR_MARKERS as _BROWSER_CONTENT_NAVIGATION_ERROR_MARKERS,
    _BROWSER_PAGINATION_CONTROL_MATCH_SCRIPT as _BROWSER_PAGINATION_CONTROL_MATCH_SCRIPT,
    _BROWSER_PAGINATION_JUMP_SCRIPT as _BROWSER_PAGINATION_JUMP_SCRIPT,
    _BROWSER_PAGINATION_STATE_SCRIPT as _BROWSER_PAGINATION_STATE_SCRIPT,
    _DEFAULT_BROWSER_WAIT_FOR as _DEFAULT_BROWSER_WAIT_FOR,
    _DOT_REPLACEMENTS as _DOT_REPLACEMENTS,
    _DYNAMIC_COLLECTION_TOKENS as _DYNAMIC_COLLECTION_TOKENS,
    _DYNAMIC_MAIN_CONTENT_TOKENS as _DYNAMIC_MAIN_CONTENT_TOKENS,
    _DYNAMIC_NON_CONTENT_TOKENS as _DYNAMIC_NON_CONTENT_TOKENS,
    _EMAIL_CHINESE_DOT_PATTERN as _EMAIL_CHINESE_DOT_PATTERN,
    _EMAIL_CHINESE_EMAIL_SYMBOL_PATTERN as _EMAIL_CHINESE_EMAIL_SYMBOL_PATTERN,
    _EMAIL_FULLWIDTH_TRANSLATION as _EMAIL_FULLWIDTH_TRANSLATION,
    _EMAIL_INVISIBLE_PATTERN as _EMAIL_INVISIBLE_PATTERN,
    _EMAIL_PATTERN as _EMAIL_PATTERN,
    _TRANSIENT_BROWSER_ERROR_MARKERS as _TRANSIENT_BROWSER_ERROR_MARKERS,
    _TRANSPARENT_IMAGE_BYTES as _TRANSPARENT_IMAGE_BYTES,
    BROWSER_BLOCKED_RESOURCE_TYPES as BROWSER_BLOCKED_RESOURCE_TYPES,
    BROWSER_DELAY_SECONDS as BROWSER_DELAY_SECONDS,
    BROWSER_EXTRA_ARGS as BROWSER_EXTRA_ARGS,
    BROWSER_FALLBACK_STATUS as BROWSER_FALLBACK_STATUS,
    BROWSER_PAGINATION_CHANGE_TIMEOUT_MS as BROWSER_PAGINATION_CHANGE_TIMEOUT_MS,
    BROWSER_RESTRICTED_RESPONSE_SETTLE_MS as BROWSER_RESTRICTED_RESPONSE_SETTLE_MS,
    BROWSER_SPARSE_DIRECTORY_MAX_HTML_CHARS as BROWSER_SPARSE_DIRECTORY_MAX_HTML_CHARS,
    BROWSER_SPARSE_DIRECTORY_MAX_LINKS as BROWSER_SPARSE_DIRECTORY_MAX_LINKS,
    BROWSER_SPARSE_DIRECTORY_MAX_TEXT_CHARS as BROWSER_SPARSE_DIRECTORY_MAX_TEXT_CHARS,
    BROWSER_SPARSE_DIRECTORY_RETRY_DELAY_SECONDS as BROWSER_SPARSE_DIRECTORY_RETRY_DELAY_SECONDS,
    BROWSER_TRANSIENT_RETRY_DELAY_SECONDS as BROWSER_TRANSIENT_RETRY_DELAY_SECONDS,
    BROWSER_USER_AGENT as BROWSER_USER_AGENT,
    BROWSER_WAIT_SELECTOR as BROWSER_WAIT_SELECTOR,
    BROWSER_WAIT_TIMEOUT_MS as BROWSER_WAIT_TIMEOUT_MS,
    CERTIFICATE_DATE_ERROR_MARKERS as CERTIFICATE_DATE_ERROR_MARKERS,
    DYNAMIC_DIRECTORY_MAX_RETRIES as DYNAMIC_DIRECTORY_MAX_RETRIES,
    DYNAMIC_DIRECTORY_READY_POLL_MS as DYNAMIC_DIRECTORY_READY_POLL_MS,
    DYNAMIC_DIRECTORY_READY_TIMEOUT_MS as DYNAMIC_DIRECTORY_READY_TIMEOUT_MS,
    DYNAMIC_DIRECTORY_STABLE_MS as DYNAMIC_DIRECTORY_STABLE_MS,
    DYNAMIC_PROFILE_MEANINGFUL_TEXT_CHARS as DYNAMIC_PROFILE_MEANINGFUL_TEXT_CHARS,
    DYNAMIC_PROFILE_READY_POLL_MS as DYNAMIC_PROFILE_READY_POLL_MS,
    DYNAMIC_PROFILE_READY_TIMEOUT_MS as DYNAMIC_PROFILE_READY_TIMEOUT_MS,
    DYNAMIC_PROFILE_STABLE_MS as DYNAMIC_PROFILE_STABLE_MS,
    IMMEDIATE_HTTP_COMPATIBILITY_ERROR_MARKERS as IMMEDIATE_HTTP_COMPATIBILITY_ERROR_MARKERS,
    JS_RENDER_TIMEOUT_MS as JS_RENDER_TIMEOUT_MS,
    MAX_BROWSER_PAGINATION_CLICK_RETRIES as MAX_BROWSER_PAGINATION_CLICK_RETRIES,
    MAX_EMBEDDED_FRAME_DOCUMENTS as MAX_EMBEDDED_FRAME_DOCUMENTS,
    MAX_RETRIES_FOR_BROWSER_RENDER as MAX_RETRIES_FOR_BROWSER_RENDER,
    TRANSIENT_HTTP_STATUS_CODES as TRANSIENT_HTTP_STATUS_CODES,
    TRANSIENT_SERVER_STATUS_MAX as TRANSIENT_SERVER_STATUS_MAX,
    TRANSIENT_SERVER_STATUS_MIN as TRANSIENT_SERVER_STATUS_MIN,
    BrowserFetchOptions as BrowserFetchOptions,
    CrawlPageIntent as CrawlPageIntent,
    _apply_browser_bandwidth_policy as _apply_browser_bandwidth_policy,
    _body_content_changed_substantially as _body_content_changed_substantially,
    _browser_fetch_options_for_intent as _browser_fetch_options_for_intent,
    _browser_link_signature as _browser_link_signature,
    _browser_pagination_state as _browser_pagination_state,
    _browser_snapshot_unusable_after_cached_cookies as _browser_snapshot_unusable_after_cached_cookies,
    _browser_wait_selector_for_intent as _browser_wait_selector_for_intent,
    _collect_browser_embedded_documents as _collect_browser_embedded_documents,
    _collect_browser_pagination_by_page_number as _collect_browser_pagination_by_page_number,
    _dynamic_collection_family as _dynamic_collection_family,
    _dynamic_collection_has_content as _dynamic_collection_has_content,
    _dynamic_directory_render_signature as _dynamic_directory_render_signature,
    _dynamic_directory_snapshot_quality as _dynamic_directory_snapshot_quality,
    _dynamic_profile_snapshot_quality as _dynamic_profile_snapshot_quality,
    _failed_snapshot as _failed_snapshot,
    _fetch_browser_pagination_direct as _fetch_browser_pagination_direct,
    _fetch_browser_same_page_controls_direct as _fetch_browser_same_page_controls_direct,
    _fetch_page_with_playwright_direct as _fetch_page_with_playwright_direct,
    _first_normalized_valid_email as _first_normalized_valid_email,
    _format_exception_for_snapshot as _format_exception_for_snapshot,
    _get_async_playwright as _get_async_playwright,
    _html_class_tokens as _html_class_tokens,
    _html_structure_tokens as _html_structure_tokens,
    _install_browser_bandwidth_policy as _install_browser_bandwidth_policy,
    _is_browser_content_navigation_error as _is_browser_content_navigation_error,
    _is_certificate_date_error as _is_certificate_date_error,
    _is_immediate_http_compatibility_error as _is_immediate_http_compatibility_error,
    _is_transient_http_status as _is_transient_http_status,
    _is_wait_condition_failure as _is_wait_condition_failure,
    _load_async_playwright as _load_async_playwright,
    _looks_like_sparse_browser_directory_shell as _looks_like_sparse_browser_directory_shell,
    _looks_like_transient_browser_error as _looks_like_transient_browser_error,
    _pagination_snapshot_fingerprint as _pagination_snapshot_fingerprint,
    _playwright_launch_options as _playwright_launch_options,
    _remember_browser_session_cookies as _remember_browser_session_cookies,
    _restore_browser_session_cookies as _restore_browser_session_cookies,
    _run_browser_fetch_with_proactor_loop as _run_browser_fetch_with_proactor_loop,
    _run_browser_pagination_with_proactor_loop as _run_browser_pagination_with_proactor_loop,
    _run_browser_same_page_controls_with_proactor_loop as _run_browser_same_page_controls_with_proactor_loop,
    _should_offload_browser_fetch_to_thread as _should_offload_browser_fetch_to_thread,
    _should_use_page_number_pagination as _should_use_page_number_pagination,
    _snapshot_from_browser_html as _snapshot_from_browser_html,
    _try_fetch_browser_pagination_once as _try_fetch_browser_pagination_once,
    _try_playwright_browser_fetch as _try_playwright_browser_fetch,
    _try_playwright_browser_fetch_once as _try_playwright_browser_fetch_once,
    _try_read_browser_page_content as _try_read_browser_page_content,
    _wait_for_browser_content_change as _wait_for_browser_content_change,
    _wait_for_browser_pagination_page as _wait_for_browser_pagination_page,
    _wait_for_dynamic_directory_html as _wait_for_dynamic_directory_html,
    _wait_for_dynamic_profile_html as _wait_for_dynamic_profile_html,
    _wait_for_same_page_content_change as _wait_for_same_page_content_change,
    async_playwright as async_playwright,
    extract_first_email_from_text as extract_first_email_from_text,
    looks_like_unrendered_dynamic_teacher_directory as looks_like_unrendered_dynamic_teacher_directory,
    normalize_obfuscated_email_tokens as normalize_obfuscated_email_tokens,
    profile_text_has_meaningful_content as profile_text_has_meaningful_content,
)
from .browser_session import (
    BrowserSessionScope,
)
from .fetch_ledger import (
    PageFetchDecision,
    get_page_fetch_decision,
    mark_page_fetch_result,
    normalize_fetch_url,
    should_prefer_browser_for_fetch_domain,
)
from .payloads import (
    _CONFIDENCE_LABEL_MAP as _CONFIDENCE_LABEL_MAP,
    BrowserPaginationExpansion as BrowserPaginationExpansion,
    BrowserSamePageExpansion as BrowserSamePageExpansion,
    CandidateBatchFailure as CandidateBatchFailure,
    CandidateEnrichmentPayload as CandidateEnrichmentPayload,
    CandidatePersistenceResult as CandidatePersistenceResult,
    PageSnapshot as PageSnapshot,
    ProfessorCandidatePayload as ProfessorCandidatePayload,
    SharedCandidateSaveResult as SharedCandidateSaveResult,
    _clamp_confidence as _clamp_confidence,
    _clean_optional as _clean_optional,
    _clean_required as _clean_required,
    _normalize_confidence_value as _normalize_confidence_value,
    _try_float as _try_float,
)
from .persistence import (
    _MERGEABLE_TEXT_FIELDS as _MERGEABLE_TEXT_FIELDS,
    CrawlJobCanceled as CrawlJobCanceled,
    CrawlJobPaused as CrawlJobPaused,
    _candidate_missing_contact_path as _candidate_missing_contact_path,
    _candidate_profile_url_matches_known_listing_url as _candidate_profile_url_matches_known_listing_url,
    _clear_listing_profile_url as _clear_listing_profile_url,
    _field_confidence as _field_confidence,
    _field_source_entry as _field_source_entry,
    _filter_accepted_candidate_payloads as _filter_accepted_candidate_payloads,
    _find_existing_candidate_for_payload as _find_existing_candidate_for_payload,
    _first_valid_email as _first_valid_email,
    _get_job_status as _get_job_status,
    _is_crawl_job_stopped as _is_crawl_job_stopped,
    _is_spa_route_fragment as _is_spa_route_fragment,
    _known_listing_urls_for_job as _known_listing_urls_for_job,
    _looks_like_hostname_without_scheme as _looks_like_hostname_without_scheme,
    _merge_candidate_payload as _merge_candidate_payload,
    _normalize_candidate_payloads_for_save as _normalize_candidate_payloads_for_save,
    _normalize_candidate_profile_urls_for_save as _normalize_candidate_profile_urls_for_save,
    _normalize_listing_url as _normalize_listing_url,
    _save_normalized_candidate_payloads as _save_normalized_candidate_payloads,
    ensure_crawl_job_can_continue as ensure_crawl_job_can_continue,
    normalize_candidate_payload as normalize_candidate_payload,
    normalize_candidate_profile_url as normalize_candidate_profile_url,
    normalize_navigable_url as normalize_navigable_url,
    record_page_snapshot as record_page_snapshot,
    save_candidate_payloads_shared as save_candidate_payloads_shared,
)
from .snapshots import (
    CLIENT_ENCRYPTED_PROFILE_FIELD_MARKERS as CLIENT_ENCRYPTED_PROFILE_FIELD_MARKERS,
    DYNAMIC_TEACHER_DIRECTORY_MARKERS as DYNAMIC_TEACHER_DIRECTORY_MARKERS,
    INVALID_PROFILE_PAGE_MARKERS as INVALID_PROFILE_PAGE_MARKERS,
    MAX_LINKS as MAX_LINKS,
    MAX_TEXT_CHARS as MAX_TEXT_CHARS,
    _bound_snapshot_html as _bound_snapshot_html,
    _clean_snapshot_soup as _clean_snapshot_soup,
    html_to_snapshot as html_to_snapshot,
)
from .url_safety import (
    TEMPORARY_DNS_RESOLUTION_MESSAGE,
    TEMPORARY_FINAL_DNS_RESOLUTION_MESSAGE,
    UNSAFE_CRAWL_URL_MESSAGE,
    TemporaryCrawlDNSResolutionError,
    build_safe_crawl_transport as _build_safe_crawl_transport,
    is_allowed_crawl_url,
    is_resolved_allowed_crawl_url as _is_resolved_allowed_crawl_url,
    is_safe_public_crawl_url,
    resolve_safe_public_crawl_url as _resolve_safe_public_crawl_url,
    resolved_allowed_crawl_url_error as _resolved_allowed_crawl_url_error,
    validate_safe_public_crawl_url as validate_safe_public_crawl_url,
)

if TYPE_CHECKING:
    pass


MAX_HTTP_REDIRECTS = 5
MAX_BROWSER_INTERACTIVE_PAGES = 500
MAX_BROWSER_SAME_PAGE_CONTROLS = 24
MAX_PAGE_SNAPSHOT_CACHE_ENTRIES = 64
MAX_BINARY_RESOURCE_BYTES = 2 * 1024 * 1024
# Preserve image load events without downloading the original asset. OCR still
# fetches selected image URLs explicitly through fetch_binary_resource.
UNAVAILABLE_PROFILE_HTTP_STATUS_CODES = frozenset({404, 410})
_SOFT_404_PROFILE_TITLE_PATTERNS = (
    re.compile(r"(?:^|\s)404(?:\s|$|错误|页面)", re.IGNORECASE),
    re.compile(r"\b(?:page\s+)?not\s+found\b", re.IGNORECASE),
    re.compile(r"(?:页面|内容|资源)(?:未找到|不存在|已删除)"),
)
_SOFT_404_PROFILE_BODY_PATTERNS = (
    re.compile(
        r"(?:访问|请求|查找)的?(?:页面|内容|资源).{0,20}(?:未找到|不存在|已删除)"
    ),
    re.compile(r"(?:页面|内容|资源)(?:未找到|不存在|已删除)"),
    re.compile(
        r"\b(?:requested\s+)?(?:page|url|resource).{0,30}not\s+found\b", re.IGNORECASE
    ),
    re.compile(
        r"\b(?:page|resource).{0,20}(?:was\s+)?(?:removed|deleted)\b", re.IGNORECASE
    ),
)
_MAX_SOFT_404_PROFILE_TEXT_CHARS = 800
HTTP_COMPATIBILITY_ERROR_MARKERS = (
    "err_connection",
    "err_http2_protocol",
    "connection closed",
    "connection reset",
    "connection refused",
    "protocol error",
    "fetch failed",
    "timed out",
    "timeout",
    "certificate",
)


def _normalize_url_for_deduplication(url: str) -> str:
    parsed = urlparse(url.strip())
    if not _is_spa_route_fragment(parsed.fragment):
        parsed = parsed._replace(fragment="")
    return parsed.geturl()


def _normalize_page_cache_url(url: str) -> str:
    return _normalize_url_for_deduplication(url)


def _merge_json_dict(current: object, incoming: object) -> dict[str, object]:
    merged: dict[str, object] = {}
    if isinstance(current, dict):
        merged.update(current)
    if isinstance(incoming, dict):
        merged.update(incoming)
    return merged


def _append_json_list(
    current: object, item: dict[str, object], *, limit: int = 20
) -> list[dict[str, object]]:
    entries = list(current) if isinstance(current, list) else []
    entries.append(item)
    return entries[-limit:]


_SOURCE_PRIORITY = {"profile_page": 4, "page_chunk": 3, "list_chunk": 2, None: 1}


def should_replace_field(
    *,
    old_value: object,
    new_value: object,
    old_source_kind: str | None,
    new_source_kind: str | None,
    old_confidence: float | None,
    new_confidence: float | None,
    old_boundary_risk: bool,
    new_boundary_risk: bool,
) -> bool:
    if new_value in (None, ""):
        return False
    if old_value in (None, ""):
        return True
    if _SOURCE_PRIORITY.get(new_source_kind, 1) > _SOURCE_PRIORITY.get(
        old_source_kind, 1
    ):
        return True
    if old_boundary_risk and not new_boundary_risk:
        return True
    return (new_confidence or 0) > (old_confidence or 0) + 0.2


@dataclass(frozen=True)
class CrawlToolContext:
    job_id: int
    start_url: str
    university: str
    school: str
    session_factory: async_sessionmaker[AsyncSession]
    http_blocked_hosts: set[str] = field(default_factory=set)
    denied_urls: dict[str, str] = field(default_factory=dict)
    page_snapshot_cache: OrderedDict[str, PageSnapshot] = field(
        default_factory=OrderedDict
    )
    known_listing_urls: set[str] = field(default_factory=set)
    llm_adaptation: LLMRuntimeAdaptation = field(
        default_factory=lambda: LLMRuntimeAdaptation("chat_completions", None)
    )
    entry_type: str | None = None
    claim_fence: CrawlerClaimFence | None = None
    allow_public_dns_fallback: bool = False
    profile_entry_url: str | None = None
    profile_landing_hosts: set[str] = field(default_factory=set)
    crawl_run_id: int | None = None

    @property
    def browser_session_scope(self) -> BrowserSessionScope:
        if self.crawl_run_id is not None:
            return "run", self.crawl_run_id
        return "job", self.job_id

    def mark_http_blocked(self, url: str) -> None:
        host = (urlparse(url).hostname or "").lower()
        if host:
            self.http_blocked_hosts.add(host)

    def is_http_blocked(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return bool(host and host in self.http_blocked_hosts)

    def mark_denied_url(self, url: str, reason: str) -> None:
        normalized = _normalize_page_cache_url(url)
        if normalized:
            self.denied_urls[normalized] = reason

    def is_denied_url(self, url: str) -> bool:
        return _normalize_page_cache_url(url) in self.denied_urls

    def denied_url_reason(self, url: str) -> str | None:
        return self.denied_urls.get(_normalize_page_cache_url(url))

    def get_cached_page_snapshot(self, url: str) -> PageSnapshot | None:
        normalized = _normalize_page_cache_url(url)
        snapshot = self.page_snapshot_cache.get(normalized)
        if snapshot is not None:
            self.page_snapshot_cache.move_to_end(normalized)
        return snapshot

    def remember_page_snapshot(self, snapshot: PageSnapshot) -> None:
        if snapshot.url:
            self.remember_page_snapshot_for_url(snapshot.url, snapshot)

    def remember_page_snapshot_for_url(
        self,
        url: str,
        snapshot: PageSnapshot,
    ) -> None:
        normalized = _normalize_page_cache_url(url)
        self.page_snapshot_cache[normalized] = snapshot
        self.page_snapshot_cache.move_to_end(normalized)
        while len(self.page_snapshot_cache) > MAX_PAGE_SNAPSHOT_CACHE_ENTRIES:
            self.page_snapshot_cache.popitem(last=False)

    def forget_page_snapshot(self, url: str) -> None:
        self.page_snapshot_cache.pop(_normalize_page_cache_url(url), None)

    def allows_url(self, url: str) -> bool:
        if is_allowed_crawl_url(self.start_url, url):
            return True
        parsed = urlparse(urljoin(self.start_url, url))
        host = (parsed.hostname or "").lower()
        return bool(
            host
            and host in self.profile_landing_hosts
            and is_safe_public_crawl_url(parsed.geturl())
        )

    def accept_profile_redirect(self, requested_url: str, target_url: str) -> bool:
        if not self.profile_entry_url or not _is_profile_entry_request(
            self.profile_entry_url,
            requested_url,
        ):
            return False
        try:
            safe_url = _resolve_safe_public_crawl_url(
                target_url,
                allow_public_dns_fallback=self.allow_public_dns_fallback,
            )
        except ValueError:
            return False
        if is_allowed_crawl_url(self.start_url, target_url):
            return True
        if (
            self.profile_landing_hosts
            and safe_url.hostname not in self.profile_landing_hosts
        ):
            return False
        self.profile_landing_hosts.add(safe_url.hostname)
        return True


def _is_resolved_context_url(ctx: CrawlToolContext, candidate_url: str) -> bool:
    return _resolved_context_url_error(ctx, candidate_url) is None


def _resolved_context_url_error(
    ctx: CrawlToolContext,
    candidate_url: str,
) -> str | None:
    absolute_candidate_url = urljoin(ctx.start_url, candidate_url)
    if not ctx.allows_url(absolute_candidate_url):
        return UNSAFE_CRAWL_URL_MESSAGE
    try:
        _resolve_safe_public_crawl_url(
            ctx.start_url,
            allow_public_dns_fallback=ctx.allow_public_dns_fallback,
        )
        _resolve_safe_public_crawl_url(
            absolute_candidate_url,
            allow_public_dns_fallback=ctx.allow_public_dns_fallback,
        )
    except TemporaryCrawlDNSResolutionError:
        return TEMPORARY_DNS_RESOLUTION_MESSAGE
    except ValueError:
        return UNSAFE_CRAWL_URL_MESSAGE
    return None


def _is_profile_entry_request(profile_entry_url: str, requested_url: str) -> bool:
    entry = urlparse(profile_entry_url)
    requested = urlparse(requested_url)
    return bool(
        entry.scheme in {"http", "https"}
        and requested.scheme in {"http", "https"}
        and (entry.hostname or "").lower() == (requested.hostname or "").lower()
        and (entry.path or "/") == (requested.path or "/")
        and entry.query == requested.query
    )


def build_candidate_enrichment_prompt(
    candidate: CrawlCandidate,
    page_text: str,
) -> str:
    return f"""
你正在补全已发现的导师候选详情。

要求：
- 只补全缺失字段：email, title, department, research_direction, recent_papers
- 只输出一个 JSON 对象，不要输出 Markdown、解释或前后缀文本
- JSON 字段必须包含：page_relation, email, title, department, research_direction, recent_papers
- page_relation 只能是 matched、mismatched 或 uncertain：页面明确是当前姓名对应的个人资料页时填写 matched；明确是学院首页、多人名单或另一个人的页面时填写 mismatched；证据不足时填写 uncertain
- 页面主体明确属于机构公共页或多人页，并且没有当前人的个人资料证据时，应填写 mismatched
- 只有明确不匹配时才填写 mismatched。姓名写法不同、页面内容较少或无法确定时必须填写 uncertain，不要误判为 mismatched
- recent_papers 必须是 JSON 数组，例如 ["Paper A", "Paper B"]；最多返回 8 篇，优先保留最新或最具代表性的论文并保持页面原有顺序；没有证据时返回 []，不要输出拼接字符串
- 不要改写已有基础字段
- 如果正文出现该导师的邮箱，必须补全 email 字段；如邮箱被反爬混淆，请根据页面上下文还原为标准邮箱格式。常见混淆包括但不限于 at、(at)、[at]、[@]、邮箱符号 表示 @，dot、(dot)、[dot]、点 表示 .，以及全角符号。如果正文出现多个邮箱，只填写最可能属于该导师的一个；无法明确判断则保持为空
- 如果正文出现教授、副教授、助理教授、讲师、研究员、副研究员、助理研究员、特聘研究员等职称，必须补全 title 字段；不要把院长、主任、教师等行政职务或普通岗位当作职称
- 字段值尽量保持页面原文：页面是中文就保留中文，页面是英文就保留英文；不要翻译、音译或拼音化已有内容
- 没有证据的字符串字段保持为空字符串，recent_papers 保持 []

输出示例：
{{"page_relation": "matched", "email": "zhang@example.edu", "title": "教授", "department": "软件工程系", "research_direction": "大语言模型、软件工程", "recent_papers": []}}

已知基础信息：
- 姓名：{candidate.name or "未知"}
- 邮箱：{candidate.email or "未知"}
- 职称：{candidate.title or "未知"}
- 资料页：{candidate.profile_url or "未知"}

资料页正文：
{page_text}
"""


async def crawl_page_with_http(ctx: CrawlToolContext, url: str) -> PageSnapshot:
    absolute_url = urljoin(ctx.start_url, url)
    snapshot = _pre_request_rejected_snapshot(ctx, absolute_url, "http")
    if snapshot is not None:
        await record_page_snapshot(ctx, snapshot)
        return snapshot

    try:
        response: httpx.Response | Any | None = None
        current_url = absolute_url
        for redirect_count in range(MAX_HTTP_REDIRECTS + 1):
            snapshot = _pre_request_rejected_snapshot(ctx, current_url, "http")
            if snapshot is not None:
                await record_page_snapshot(ctx, snapshot)
                return snapshot

            safe_url = _resolve_safe_public_crawl_url(
                current_url,
                allow_public_dns_fallback=ctx.allow_public_dns_fallback,
            )
            transport = _build_safe_crawl_transport(
                hostname=safe_url.hostname,
                resolved_ip=safe_url.resolved_ips[0],
            )
            async with httpx.AsyncClient(
                follow_redirects=False,
                timeout=20.0,
                transport=transport,
                trust_env=False,
            ) as client:
                response = await client.get(
                    current_url,
                    headers={"User-Agent": "AutoEmailSenderCrawler/0.1"},
                )
            if not getattr(response, "is_redirect", False):
                if response.status_code in BROWSER_FALLBACK_STATUS:
                    snapshot = html_to_snapshot(
                        str(response.url), response.text, "http"
                    )
                    snapshot.http_status_code = response.status_code
                    snapshot.status = "failed"
                    snapshot.error_message = (
                        f"HTTP {response.status_code} blocked, browser fallback advised"
                    )
                    snapshot.suspicious_empty = True
                    await record_page_snapshot(ctx, snapshot)
                    return snapshot

                if _is_transient_http_status(response.status_code):
                    snapshot = html_to_snapshot(
                        str(response.url), response.text, "http"
                    )
                    snapshot.http_status_code = response.status_code
                    snapshot.status = "failed"
                    snapshot.error_message = (
                        f"HTTP {response.status_code} temporary server response"
                    )
                    snapshot.suspicious_empty = True
                    await record_page_snapshot(ctx, snapshot)
                    return snapshot

                response.raise_for_status()
                break

            if redirect_count >= MAX_HTTP_REDIRECTS:
                snapshot = _failed_snapshot(
                    url=str(response.url),
                    fetch_method="http",
                    error_message="重定向次数过多，已拒绝抓取",
                )
                await record_page_snapshot(ctx, snapshot)
                return snapshot

            location = response.headers.get("Location") or response.headers.get(
                "location"
            )
            if not location:
                snapshot = _failed_snapshot(
                    url=str(response.url),
                    fetch_method="http",
                    error_message="重定向响应缺少 Location，已拒绝抓取",
                )
                await record_page_snapshot(ctx, snapshot)
                return snapshot

            next_url = urljoin(str(response.url), location)
            if not ctx.allows_url(next_url):
                ctx.accept_profile_redirect(absolute_url, next_url)
            snapshot = _pre_request_rejected_snapshot(ctx, next_url, "http")
            if snapshot is not None:
                await record_page_snapshot(ctx, snapshot)
                return snapshot
            current_url = next_url
        if response is None:
            raise RuntimeError("HTTP 抓取未返回响应")
    except Exception as exc:
        snapshot = _failed_snapshot(
            url=absolute_url,
            fetch_method="http",
            error_message=_format_exception_for_snapshot(exc, "HTTP request failed"),
            http_status_code=getattr(response, "status_code", None),
        )
        await record_page_snapshot(ctx, snapshot)
        return snapshot

    final_url = str(response.url)
    if not is_safe_public_crawl_url(final_url):
        snapshot = _failed_snapshot(
            url=final_url,
            fetch_method="http",
            error_message=UNSAFE_CRAWL_URL_MESSAGE,
        )
        await record_page_snapshot(ctx, snapshot)
        return snapshot

    if not ctx.allows_url(final_url):
        snapshot = _final_url_rejected_snapshot(
            final_url=final_url,
            fetch_method="http",
        )
        await record_page_snapshot(ctx, snapshot)
        return snapshot

    snapshot = html_to_snapshot(final_url, response.text, "http")
    snapshot.http_status_code = response.status_code
    snapshot.links = [
        link for link in snapshot.links if _is_same_host_http_url(final_url, link)
    ][:MAX_LINKS]
    await record_page_snapshot(ctx, snapshot)
    return snapshot


async def fetch_binary_resource(
    ctx: CrawlToolContext,
    url: str,
    *,
    max_bytes: int = MAX_BINARY_RESOURCE_BYTES,
) -> tuple[str, str, bytes]:
    """Fetch a small same-school resource through the crawler's pinned transport."""

    absolute_url = urljoin(ctx.start_url, url)
    current_url = absolute_url
    for redirect_count in range(MAX_HTTP_REDIRECTS + 1):
        if not _is_resolved_context_url(ctx, current_url):
            raise ValueError(UNSAFE_CRAWL_URL_MESSAGE)
        safe_url = _resolve_safe_public_crawl_url(
            current_url,
            allow_public_dns_fallback=ctx.allow_public_dns_fallback,
        )
        transport = _build_safe_crawl_transport(
            hostname=safe_url.hostname,
            resolved_ip=safe_url.resolved_ips[0],
        )
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=20.0,
            transport=transport,
            trust_env=False,
        ) as client:
            async with client.stream(
                "GET",
                current_url,
                headers={"User-Agent": "AutoEmailSenderCrawler/0.1"},
            ) as response:
                if response.is_redirect:
                    if redirect_count >= MAX_HTTP_REDIRECTS:
                        raise ValueError("资源重定向次数过多，已拒绝抓取")
                    location = response.headers.get("Location") or response.headers.get(
                        "location"
                    )
                    if not location:
                        raise ValueError("资源重定向响应缺少 Location，已拒绝抓取")
                    current_url = urljoin(str(response.url), location)
                    continue
                response.raise_for_status()
                declared_size = response.headers.get("Content-Length")
                if declared_size:
                    try:
                        if int(declared_size) > max_bytes:
                            raise ValueError("资源体积超过抓取上限")
                    except ValueError as exc:
                        if str(exc) == "资源体积超过抓取上限":
                            raise
                chunks: list[bytes] = []
                total_bytes = 0
                async for chunk in response.aiter_bytes():
                    total_bytes += len(chunk)
                    if total_bytes > max_bytes:
                        raise ValueError("资源体积超过抓取上限")
                    chunks.append(chunk)
                return (
                    str(response.url),
                    str(response.headers.get("Content-Type") or "")
                    .split(";", 1)[0]
                    .strip()
                    .lower(),
                    b"".join(chunks),
                )
    raise ValueError("资源抓取失败")


def _snapshot_from_page_fetch_decision(
    url: str, decision: PageFetchDecision
) -> PageSnapshot | None:
    if decision.action == "skip_terminal_failed":
        message = decision.message or decision.terminal_reason or "terminal_failed"
        if decision.terminal_reason == "transient_retry_exhausted":
            error_message = f"页面暂时无法访问，重试次数已用尽：{message}"
        elif message.startswith("该页面此前已明确抓取失败，已跳过："):
            error_message = message
        else:
            error_message = f"该页面此前已明确抓取失败，已跳过：{message}"
        return _failed_snapshot(
            url=url,
            fetch_method="ledger",
            error_message=error_message,
        )
    if decision.action == "skip_processed":
        return _failed_snapshot(
            url=url,
            fetch_method="ledger",
            error_message="该页面已处理完成，已跳过重复抓取",
        )
    if decision.action == "claim_chunk":
        return _failed_snapshot(
            url=url,
            fetch_method="ledger",
            error_message="该页面已有待处理片段，请领取 chunk，不要重复抓取",
        )
    return None


async def crawl_page_with_browser_fallback(
    ctx: CrawlToolContext,
    url: str,
    *,
    intent: CrawlPageIntent = "generic",
    force_fetch: bool = False,
) -> PageSnapshot:
    await _ensure_crawl_job_can_continue_for_context(ctx)
    absolute_url = urljoin(ctx.start_url, url)
    denied_snapshot = _denied_url_snapshot(ctx, absolute_url, "http")
    if denied_snapshot is not None:
        await record_page_snapshot(ctx, denied_snapshot)
        await _ensure_crawl_job_can_continue_for_context(ctx)
        return denied_snapshot

    if not force_fetch:
        cached = ctx.get_cached_page_snapshot(absolute_url)
        if cached is not None:
            await _ensure_crawl_job_can_continue_for_context(ctx)
            return cached

        decision = await get_page_fetch_decision(
            ctx.session_factory, job_id=ctx.job_id, url=absolute_url
        )
        decision_snapshot = _snapshot_from_page_fetch_decision(absolute_url, decision)
        if decision_snapshot is not None:
            await _ensure_crawl_job_can_continue_for_context(ctx)
            return decision_snapshot

    prefer_browser_for_domain = intent != "profile" and (
        await should_prefer_browser_for_fetch_domain(
            ctx.session_factory,
            job_id=ctx.job_id,
            url=absolute_url,
        )
    )
    http_blocked_for_host = ctx.is_http_blocked(absolute_url)
    if http_blocked_for_host or prefer_browser_for_domain:
        browser_snapshot = await browser_investigate(
            ctx,
            absolute_url,
            goal="",
            intent=intent,
            force_fetch=force_fetch,
        )
        compatibility_browser_snapshot = await _try_http_compatibility_browser_fallback(
            ctx,
            requested_url=absolute_url,
            failed_snapshot=browser_snapshot,
            intent=intent,
        )
        snapshot = (
            compatibility_browser_snapshot
            if compatibility_browser_snapshot is not None
            and compatibility_browser_snapshot.status == "succeeded"
            else browser_snapshot
        )
        snapshot = _apply_runtime_url_denylist_after_fetch(
            ctx,
            requested_url=absolute_url,
            snapshot=snapshot,
        )
        await mark_page_fetch_result(
            ctx.session_factory,
            job_id=ctx.job_id,
            original_url=absolute_url,
            snapshot=snapshot,
            fetch_mode="browser",
            direct_status="skipped_by_domain_browser_preference",
            fallback_reason=(
                "same_domain_previously_required_browser"
                if prefer_browser_for_domain
                else "same_host_http_previously_blocked"
            ),
            browser_status=browser_snapshot.status,
        )
        if snapshot.status == "succeeded":
            ctx.forget_page_snapshot(absolute_url)
            ctx.forget_page_snapshot(browser_snapshot.url)
            ctx.remember_page_snapshot(snapshot)
        await _ensure_crawl_job_can_continue_for_context(ctx)
        return snapshot

    http_snapshot = await crawl_page_with_http(ctx, url)
    await _ensure_crawl_job_can_continue_for_context(ctx)
    https_upgrade_url = _profile_https_upgrade_url(
        start_url=ctx.start_url,
        requested_url=absolute_url,
        intent=intent,
        failed_snapshot=http_snapshot,
    )
    if https_upgrade_url is not None:
        upgraded_snapshot = await crawl_page_with_browser_fallback(
            ctx,
            https_upgrade_url,
            intent=intent,
            force_fetch=True,
        )
        await _ensure_crawl_job_can_continue_for_context(ctx)
        if upgraded_snapshot.status == "succeeded":
            processed_snapshot = _apply_runtime_url_denylist_after_fetch(
                ctx,
                requested_url=absolute_url,
                snapshot=upgraded_snapshot,
            )
            if processed_snapshot.status == "succeeded":
                await mark_page_fetch_result(
                    ctx.session_factory,
                    job_id=ctx.job_id,
                    original_url=absolute_url,
                    snapshot=processed_snapshot.model_copy(
                        update={"url": absolute_url}
                    ),
                    fetch_mode=(
                        "browser"
                        if processed_snapshot.fetch_method == "browser"
                        else "direct"
                    ),
                    direct_status=http_snapshot.status,
                    fallback_reason="same_host_http_profile_upgraded_to_https_after_400",
                    browser_status=(
                        processed_snapshot.status
                        if processed_snapshot.fetch_method == "browser"
                        else None
                    ),
                )
                ctx.forget_page_snapshot(absolute_url)
                ctx.remember_page_snapshot(processed_snapshot)
                ctx.remember_page_snapshot_for_url(absolute_url, processed_snapshot)
                return processed_snapshot
    if _should_use_browser_fallback(http_snapshot):
        if _is_http_blocked_snapshot(http_snapshot):
            ctx.mark_http_blocked(http_snapshot.url or absolute_url)
        browser_snapshot = await browser_investigate(
            ctx,
            url,
            goal="",
            intent=intent,
            force_fetch=force_fetch,
        )
        compatibility_browser_snapshot = await _try_http_compatibility_browser_fallback(
            ctx,
            requested_url=absolute_url,
            failed_snapshot=browser_snapshot,
            intent=intent,
        )
        selected_snapshot = browser_snapshot
        fetch_mode = "browser"
        if (
            browser_snapshot.status != "succeeded"
            and compatibility_browser_snapshot is not None
            and compatibility_browser_snapshot.status == "succeeded"
        ):
            selected_snapshot = compatibility_browser_snapshot
            fetch_mode = "direct"
        elif (
            browser_snapshot.status != "succeeded"
            and http_snapshot.status == "succeeded"
        ):
            selected_snapshot = http_snapshot
            fetch_mode = "direct"
        processed_snapshot = _apply_runtime_url_denylist_after_fetch(
            ctx,
            requested_url=absolute_url,
            snapshot=selected_snapshot,
        )
        ledger_urls = [processed_snapshot.url]
        if fetch_mode == "direct":
            ledger_urls.extend((absolute_url, browser_snapshot.url, http_snapshot.url))
            if compatibility_browser_snapshot is not None:
                ledger_urls.append(compatibility_browser_snapshot.url)
        recorded_urls: set[str] = set()
        for ledger_url in ledger_urls:
            normalized_ledger_url = normalize_fetch_url(ledger_url)
            if normalized_ledger_url in recorded_urls:
                continue
            recorded_urls.add(normalized_ledger_url)
            ledger_snapshot = processed_snapshot.model_copy(update={"url": ledger_url})
            await mark_page_fetch_result(
                ctx.session_factory,
                job_id=ctx.job_id,
                original_url=absolute_url,
                snapshot=ledger_snapshot,
                fetch_mode=fetch_mode,
                direct_status=http_snapshot.status,
                fallback_reason=http_snapshot.error_message or "direct_fetch_unusable",
                browser_status=browser_snapshot.status,
            )
        if fetch_mode == "direct":
            ctx.forget_page_snapshot(absolute_url)
            ctx.forget_page_snapshot(browser_snapshot.url)
            if _should_remember_page_snapshot(processed_snapshot):
                ctx.remember_page_snapshot(processed_snapshot)
        await _ensure_crawl_job_can_continue_for_context(ctx)
        return processed_snapshot
    processed_snapshot = _apply_runtime_url_denylist_after_fetch(
        ctx,
        requested_url=absolute_url,
        snapshot=http_snapshot,
    )
    await mark_page_fetch_result(
        ctx.session_factory,
        job_id=ctx.job_id,
        original_url=absolute_url,
        snapshot=processed_snapshot,
        fetch_mode="direct",
        direct_status=processed_snapshot.status,
    )
    if _should_remember_page_snapshot(processed_snapshot):
        ctx.remember_page_snapshot(processed_snapshot)
    await _ensure_crawl_job_can_continue_for_context(ctx)
    return processed_snapshot


def _denied_url_snapshot(
    ctx: CrawlToolContext,
    url: str,
    fetch_method: str,
) -> PageSnapshot | None:
    reason = ctx.denied_url_reason(url)
    if reason is None:
        return None
    snapshot = _failed_snapshot(
        url=url,
        fetch_method=fetch_method,
        error_message=f"该 URL 已在本轮抓取中判定为无关页面，已跳过：{reason}",
    )
    snapshot.links = []
    return snapshot


def _apply_runtime_url_denylist_after_fetch(
    ctx: CrawlToolContext,
    *,
    requested_url: str,
    snapshot: PageSnapshot,
) -> PageSnapshot:
    if snapshot.status != "succeeded":
        return snapshot
    final_url = snapshot.url or requested_url
    if not ctx.allows_url(final_url) and not ctx.accept_profile_redirect(
        requested_url,
        final_url,
    ):
        ctx.mark_denied_url(final_url, "最终地址不在允许的同校公网域名范围内")
        return _failed_snapshot(
            url=final_url,
            fetch_method=snapshot.fetch_method,
            error_message="最终 URL 不在允许的同校公网域名范围内，已拒绝抓取结果",
        )
    return snapshot


async def browser_investigate(
    ctx: CrawlToolContext,
    url: str,
    goal: str,
    intent: CrawlPageIntent = "generic",
    *,
    force_fetch: bool = False,
) -> PageSnapshot:
    await _ensure_crawl_job_can_continue_for_context(ctx)
    absolute_url = urljoin(ctx.start_url, url)
    denied_snapshot = _denied_url_snapshot(ctx, absolute_url, "browser")
    if denied_snapshot is not None:
        await record_page_snapshot(ctx, denied_snapshot)
        await _ensure_crawl_job_can_continue_for_context(ctx)
        return denied_snapshot

    if not force_fetch:
        cached = ctx.get_cached_page_snapshot(absolute_url)
        if cached is not None:
            await _ensure_crawl_job_can_continue_for_context(ctx)
            return cached

        decision = await get_page_fetch_decision(
            ctx.session_factory, job_id=ctx.job_id, url=absolute_url
        )
        decision_snapshot = _snapshot_from_page_fetch_decision(absolute_url, decision)
        if decision_snapshot is not None:
            await _ensure_crawl_job_can_continue_for_context(ctx)
            return decision_snapshot

    if _has_unsafe_public_crawl_url(ctx.start_url, absolute_url):
        snapshot = _failed_snapshot(
            url=absolute_url,
            fetch_method="browser",
            error_message=UNSAFE_CRAWL_URL_MESSAGE,
        )
        await record_page_snapshot(ctx, snapshot)
        await _ensure_crawl_job_can_continue_for_context(ctx)
        return snapshot

    if not ctx.allows_url(absolute_url):
        snapshot = _failed_snapshot(
            url=url,
            fetch_method="browser",
            error_message="URL 不在入口页面同域范围内，已拒绝浏览器调查",
        )
        await record_page_snapshot(ctx, snapshot)
        await _ensure_crawl_job_can_continue_for_context(ctx)
        return snapshot

    snapshot = await _crawl_page_with_browser(ctx, absolute_url, goal, intent)
    processed_snapshot = _apply_runtime_url_denylist_after_fetch(
        ctx,
        requested_url=absolute_url,
        snapshot=snapshot,
    )
    await record_page_snapshot(ctx, processed_snapshot)
    await mark_page_fetch_result(
        ctx.session_factory,
        job_id=ctx.job_id,
        original_url=absolute_url,
        snapshot=processed_snapshot,
    )
    if _should_remember_page_snapshot(processed_snapshot):
        ctx.remember_page_snapshot(processed_snapshot)
    await _ensure_crawl_job_can_continue_for_context(ctx)
    return processed_snapshot


def _should_remember_page_snapshot(snapshot: PageSnapshot) -> bool:
    if snapshot.status == "succeeded":
        return True
    if snapshot.status != "failed":
        return False
    if snapshot.suspicious_empty:
        return True
    error_message = (snapshot.error_message or "").lower()
    return any(
        marker in error_message
        for marker in (
            "anti-bot",
            "blocked",
            "captcha",
            "cloudflare",
            "access denied",
            "security check",
        )
    )


def _should_use_browser_fallback(snapshot: PageSnapshot) -> bool:
    if snapshot.fetch_method != "http":
        return False

    if snapshot.suspicious_empty:
        return True

    if looks_like_unrendered_dynamic_teacher_directory(snapshot):
        return True

    if looks_like_client_encrypted_profile_fields(snapshot):
        return True

    if _looks_like_unrendered_or_error_profile_page(snapshot):
        return True

    if snapshot.status == "failed":
        error_message = (snapshot.error_message or "").lower()
        if any(str(marker) in error_message for marker in BROWSER_FALLBACK_STATUS):
            return True
        if any(marker in error_message for marker in HTTP_COMPATIBILITY_ERROR_MARKERS):
            return True
        if "cf-" in error_message:
            return True
        return any(
            marker in error_message
            for marker in (
                "cloudflare",
                "please",
                "anti-bot",
                "captcha",
                "security check",
                "verify you",
                "enable javascript",
            )
        )

    text = (snapshot.text or "").lower()
    if not text.strip():
        return True
    if len(text) >= 80:
        return False

    return any(
        marker in text
        for marker in (
            "cloudflare",
            "just a moment",
            "please enable javascript",
            "please verify",
            "anti-bot",
            "access denied",
            "captcha",
            "security check",
        )
    )


def _looks_like_unrendered_or_error_profile_page(snapshot: PageSnapshot) -> bool:
    if snapshot.has_invalid_profile_page_markers:
        return True
    haystack = f"{snapshot.title or ''}\n{snapshot.text}\n{snapshot.html[:2000]}"
    return any(marker in haystack for marker in INVALID_PROFILE_PAGE_MARKERS)


def looks_like_unavailable_profile_page(snapshot: PageSnapshot) -> bool:
    """Identify a missing profile without treating arbitrary 404 text as fatal."""

    if snapshot.http_status_code in UNAVAILABLE_PROFILE_HTTP_STATUS_CODES:
        return True
    if snapshot.status != "succeeded":
        return False

    title = (snapshot.title or "").strip()
    text = re.sub(r"\s+", " ", snapshot.text or "").strip()
    if not title or not text or len(text) > _MAX_SOFT_404_PROFILE_TEXT_CHARS:
        return False
    return any(
        pattern.search(title) for pattern in _SOFT_404_PROFILE_TITLE_PATTERNS
    ) and any(pattern.search(text) for pattern in _SOFT_404_PROFILE_BODY_PATTERNS)


def looks_like_client_encrypted_profile_fields(snapshot: PageSnapshot) -> bool:
    if snapshot.has_client_encrypted_profile_fields:
        return True
    html = snapshot.html or ""
    return any(marker in html for marker in CLIENT_ENCRYPTED_PROFILE_FIELD_MARKERS)


def _is_http_blocked_snapshot(snapshot: PageSnapshot) -> bool:
    if snapshot.fetch_method != "http":
        return False
    error_message = (snapshot.error_message or "").lower()
    return any(str(status) in error_message for status in BROWSER_FALLBACK_STATUS)


def _http_compatibility_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return None
    return parsed._replace(scheme="http").geturl()


def _profile_https_upgrade_url(
    *,
    start_url: str,
    requested_url: str,
    intent: CrawlPageIntent,
    failed_snapshot: PageSnapshot,
) -> str | None:
    if (
        intent != "profile"
        or failed_snapshot.status != "failed"
        or failed_snapshot.http_status_code != 400
    ):
        return None
    start = urlparse(start_url)
    requested = urlparse(requested_url)
    if (
        start.scheme.lower() != "https"
        or requested.scheme.lower() != "http"
        or not start.hostname
        or not requested.hostname
        or start.hostname.lower() != requested.hostname.lower()
        or start.port is not None
        or requested.port is not None
    ):
        return None
    return requested._replace(scheme="https").geturl()


def _should_try_http_compatibility_fallback(
    requested_url: str,
    snapshot: PageSnapshot,
) -> bool:
    if snapshot.status != "failed" or urlparse(requested_url).scheme.lower() != "https":
        return False
    error_message = (snapshot.error_message or "").lower()
    return any(marker in error_message for marker in HTTP_COMPATIBILITY_ERROR_MARKERS)


async def _try_http_compatibility_browser_fallback(
    ctx: CrawlToolContext,
    *,
    requested_url: str,
    failed_snapshot: PageSnapshot,
    intent: CrawlPageIntent,
) -> PageSnapshot | None:
    if not _should_try_http_compatibility_fallback(requested_url, failed_snapshot):
        return None
    compatibility_url = _http_compatibility_url(requested_url)
    if compatibility_url is None or not _is_resolved_allowed_crawl_url(
        ctx.start_url,
        compatibility_url,
        allow_public_dns_fallback=ctx.allow_public_dns_fallback,
    ):
        return None
    snapshot = await _crawl_page_with_browser(
        ctx,
        compatibility_url,
        "",
        intent,
    )
    await record_page_snapshot(ctx, snapshot)
    return snapshot


async def _crawl_page_with_browser(
    ctx: CrawlToolContext,
    absolute_url: str,
    goal: str,
    intent: CrawlPageIntent = "generic",
) -> PageSnapshot:
    context_url_error = _resolved_context_url_error(ctx, absolute_url)
    if context_url_error is not None:
        return _failed_snapshot(
            url=absolute_url,
            fetch_method="browser",
            error_message=context_url_error,
        )
    browser_session_scope = ctx.browser_session_scope
    if _should_offload_browser_fetch_to_thread():
        snapshot = await asyncio.to_thread(
            _run_browser_fetch_with_proactor_loop,
            absolute_url,
            goal,
            intent,
            browser_session_scope,
        )
    else:
        snapshot = await _fetch_page_with_playwright_direct(
            absolute_url,
            goal,
            intent,
            browser_session_scope=browser_session_scope,
        )
    if snapshot.status == "succeeded":
        final_url_error = _resolved_context_url_error(ctx, snapshot.url)
    else:
        final_url_error = None
    if snapshot.status == "succeeded" and final_url_error is not None:
        if final_url_error == TEMPORARY_DNS_RESOLUTION_MESSAGE:
            return _failed_snapshot(
                url=snapshot.url,
                fetch_method="browser",
                error_message=TEMPORARY_FINAL_DNS_RESOLUTION_MESSAGE,
            )
        if not ctx.accept_profile_redirect(absolute_url, snapshot.url):
            return _failed_snapshot(
                url=snapshot.url,
                fetch_method="browser",
                error_message="浏览器最终 URL 不在允许的同校公网域名范围内",
            )
    return snapshot


async def expand_browser_pagination(
    ctx: CrawlToolContext,
    url: str,
    *,
    tag: str,
    text: str,
    title: str,
    aria_label: str,
    class_tokens: Sequence[str],
    match_index: int,
    intent: CrawlPageIntent = "generic",
    max_pages: int = MAX_BROWSER_INTERACTIVE_PAGES,
) -> BrowserPaginationExpansion:
    """Replay one model-selected next-page control and collect each changed state."""

    absolute_url = urljoin(ctx.start_url, url)
    allowed_url_error = _resolved_allowed_crawl_url_error(
        ctx.start_url,
        absolute_url,
        allow_public_dns_fallback=ctx.allow_public_dns_fallback,
    )
    if allowed_url_error is not None:
        return BrowserPaginationExpansion(
            status="failed",
            stopped_reason="unsafe_url",
            error_message=allowed_url_error,
        )
    target = {
        "tag": tag,
        "text": text,
        "title": title,
        "ariaLabel": aria_label,
        "classTokens": list(class_tokens),
        "matchIndex": max(0, int(match_index)),
    }
    browser_session_scope = ctx.browser_session_scope
    if _should_offload_browser_fetch_to_thread():
        result = await asyncio.to_thread(
            _run_browser_pagination_with_proactor_loop,
            absolute_url,
            target,
            intent,
            max_pages,
            browser_session_scope,
        )
    else:
        result = await _fetch_browser_pagination_direct(
            absolute_url,
            target,
            intent=intent,
            max_pages=max_pages,
            browser_session_scope=browser_session_scope,
        )
    for snapshot in result.snapshots:
        final_url_error = _resolved_allowed_crawl_url_error(
            ctx.start_url,
            snapshot.url,
            allow_public_dns_fallback=ctx.allow_public_dns_fallback,
        )
        if final_url_error is not None:
            return BrowserPaginationExpansion(
                status="failed",
                snapshots=result.snapshots,
                stopped_reason="unsafe_final_url",
                error_message=(
                    TEMPORARY_FINAL_DNS_RESOLUTION_MESSAGE
                    if final_url_error == TEMPORARY_DNS_RESOLUTION_MESSAGE
                    else "浏览器分页后的最终 URL 不在允许的同校公网域名范围内"
                ),
            )
    return result


async def expand_browser_same_page_controls(
    ctx: CrawlToolContext,
    url: str,
    *,
    controls: Sequence[dict[str, object]],
    intent: CrawlPageIntent = "directory",
    max_controls: int = MAX_BROWSER_SAME_PAGE_CONTROLS,
) -> BrowserSamePageExpansion:
    """Click model-selected same-page list controls and collect changed states."""

    absolute_url = urljoin(ctx.start_url, url)
    allowed_url_error = _resolved_context_url_error(ctx, absolute_url)
    if allowed_url_error is not None:
        return BrowserSamePageExpansion(
            status="failed",
            stopped_reason="unsafe_url",
            error_message=allowed_url_error,
        )
    selected_controls = list(controls)[: max(0, int(max_controls))]
    if not selected_controls:
        return BrowserSamePageExpansion(
            status="succeeded",
            stopped_reason="no_controls",
        )
    browser_session_scope = ctx.browser_session_scope
    if _should_offload_browser_fetch_to_thread():
        result = await asyncio.to_thread(
            _run_browser_same_page_controls_with_proactor_loop,
            absolute_url,
            selected_controls,
            intent,
            browser_session_scope,
        )
    else:
        result = await _fetch_browser_same_page_controls_direct(
            absolute_url,
            selected_controls,
            intent=intent,
            browser_session_scope=browser_session_scope,
        )
    for snapshot in result.snapshots:
        final_url_error = _resolved_context_url_error(ctx, snapshot.url)
        if final_url_error is not None:
            return BrowserSamePageExpansion(
                status="failed",
                snapshots=result.snapshots,
                stopped_reason="unsafe_final_url",
                error_message=final_url_error,
            )
    return result


async def _ensure_crawl_job_can_continue_for_context(ctx: CrawlToolContext) -> None:
    async with ctx.session_factory() as session:
        await ensure_crawl_job_can_continue(session, ctx.job_id)


def _has_unsafe_public_crawl_url(start_url: str, candidate_url: str) -> bool:
    return not is_safe_public_crawl_url(start_url) or not is_safe_public_crawl_url(
        candidate_url
    )


def _is_same_host_http_url(start_url: str, candidate_url: str) -> bool:
    start = urlparse(start_url)
    candidate = urlparse(urljoin(start_url, candidate_url))
    return (
        candidate.scheme in {"http", "https"}
        and (start.hostname or "").lower() == (candidate.hostname or "").lower()
    )


def _pre_request_rejected_snapshot(
    ctx: CrawlToolContext,
    target_url: str,
    fetch_method: str,
) -> PageSnapshot | None:
    if _has_unsafe_public_crawl_url(ctx.start_url, target_url):
        return _failed_snapshot(
            url=target_url,
            fetch_method=fetch_method,
            error_message=UNSAFE_CRAWL_URL_MESSAGE,
        )

    if not ctx.allows_url(target_url):
        return _failed_snapshot(
            url=target_url,
            fetch_method=fetch_method,
            error_message="URL 不在入口页面同域范围内，已拒绝抓取",
        )

    return None


def _final_url_rejected_snapshot(final_url: str, fetch_method: str) -> PageSnapshot:
    return _failed_snapshot(
        url=final_url,
        fetch_method=fetch_method,
        error_message="最终 URL 不在允许范围内，已拒绝抓取结果",
    )
