from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from functools import lru_cache
from html import escape, unescape
import hashlib
import ipaddress
import platform
import re
import socket
from typing import TYPE_CHECKING, Any, Literal, TypedDict
from urllib.parse import urljoin, urlparse

import httpx
import httpcore
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.time import utc_now
from app.models.crawl_job import CrawlCandidate, CrawlJob, CrawlJobStatus, CrawlPage, CrawlPageTask
from app.modules.crawler.candidate_identity import (
    candidate_identity_values,
    canonical_candidate_clause,
    consolidate_candidate_identity,
    find_canonical_candidate_for_identity,
    merge_candidate_payload as merge_candidate_payload_shared,
)
from .chunking import MAX_CRAWL_HTML_CHARS
from .browser_session import (
    BrowserSessionScope,
    browser_cookie_session_cache,
)
from .domain_policy import registrable_domain_from_hostname
from .fetch_ledger import (
    PageFetchDecision,
    get_page_fetch_decision,
    mark_page_fetch_result,
    normalize_fetch_url,
    should_prefer_browser_for_fetch_domain,
)
from app.services.html_text import html_to_text
from app.services.beautiful_soup import is_comment, parse_html
from app.modules.llm.public import LLMRuntimeAdaptation
from ..v2.lease import CrawlerV2ClaimFence, fence_crawler_v2_claim
from app.modules.professors.public import (
    RECENT_PAPERS_MAX_ITEMS,
    is_valid_professor_email,
    normalize_professor_email,
    normalize_professor_title,
    normalize_recent_papers,
    normalize_research_direction,
)

if TYPE_CHECKING:
    from bs4 import BeautifulSoup

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


MAX_TEXT_CHARS = 12000
MAX_LINKS = 200
MAX_EMBEDDED_FRAME_DOCUMENTS = 4
MAX_HTTP_REDIRECTS = 5
MAX_RETRIES_FOR_BROWSER_RENDER = 2
MAX_BROWSER_INTERACTIVE_PAGES = 500
MAX_BROWSER_PAGINATION_CLICK_RETRIES = 2
BROWSER_PAGINATION_CHANGE_TIMEOUT_MS = 10000
MAX_PAGE_SNAPSHOT_CACHE_ENTRIES = 64
MAX_BINARY_RESOURCE_BYTES = 2 * 1024 * 1024
BROWSER_FALLBACK_STATUS = {403, 412, 429}
INVALID_PROFILE_PAGE_MARKERS = (
    "{{name}}",
    "{{email}}",
    "{{data}}",
    "FineCMS error",
    "SQL syntax",
)
UNAVAILABLE_PROFILE_HTTP_STATUS_CODES = frozenset({404, 410})
_SOFT_404_PROFILE_TITLE_PATTERNS = (
    re.compile(r"(?:^|\s)404(?:\s|$|错误|页面)", re.IGNORECASE),
    re.compile(r"\b(?:page\s+)?not\s+found\b", re.IGNORECASE),
    re.compile(r"(?:页面|内容|资源)(?:未找到|不存在|已删除)"),
)
_SOFT_404_PROFILE_BODY_PATTERNS = (
    re.compile(r"(?:访问|请求|查找)的?(?:页面|内容|资源).{0,20}(?:未找到|不存在|已删除)"),
    re.compile(r"(?:页面|内容|资源)(?:未找到|不存在|已删除)"),
    re.compile(r"\b(?:requested\s+)?(?:page|url|resource).{0,30}not\s+found\b", re.IGNORECASE),
    re.compile(r"\b(?:page|resource).{0,20}(?:was\s+)?(?:removed|deleted)\b", re.IGNORECASE),
)
_MAX_SOFT_404_PROFILE_TEXT_CHARS = 800
CLIENT_ENCRYPTED_PROFILE_FIELD_MARKERS = ("_tsites_encrypt_field",)
DYNAMIC_TEACHER_DIRECTORY_MARKERS = (
    "search_teacher.js",
    "_wp3services/generalquery?queryobj=articles",
    "queryobj=articles",
)
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
_BROWSER_CONTENT_NAVIGATION_ERROR_MARKERS = (
    "page.content",
    "page is navigating",
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
UNSAFE_CRAWL_URL_MESSAGE = "URL 不允许指向本机、内网或不可解析地址"
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


class PageSnapshot(BaseModel):
    page_id: int | None = None
    url: str
    title: str | None = None
    text: str = ""
    html: str = ""
    links: list[str] = Field(default_factory=list)
    fetch_method: str
    status: Literal["succeeded", "failed"]
    http_status_code: int | None = None
    error_message: str | None = None
    suspicious_empty: bool = False
    has_client_encrypted_profile_fields: bool = False
    has_dynamic_teacher_directory_markers: bool = False
    has_invalid_profile_page_markers: bool = False


@dataclass(frozen=True, slots=True)
class BrowserPaginationExpansion:
    status: Literal["succeeded", "failed"]
    snapshots: tuple[PageSnapshot, ...] = ()
    stopped_reason: str | None = None
    error_message: str | None = None


class ProfessorCandidatePayload(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str = Field(validation_alias=AliasChoices("name", "姓名"))
    email: str | None = Field(
        default=None,
        validation_alias=AliasChoices("email", "邮箱", "邮箱地址"),
    )
    title: str | None = Field(
        default=None,
        validation_alias=AliasChoices("title", "职称", "岗位"),
    )
    university: str | None = Field(
        default=None,
        validation_alias=AliasChoices("university", "学校", "院校"),
    )
    school: str | None = Field(
        default=None,
        validation_alias=AliasChoices("school", "学院", "院系", "学院/单位", "单位"),
    )
    department: str | None = Field(
        default=None,
        validation_alias=AliasChoices("department", "部门", "系别"),
    )
    research_direction: str | None = Field(
        default=None,
        validation_alias=AliasChoices("research_direction", "研究方向", "研究领域"),
    )
    recent_papers: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("recent_papers", "近期论文", "代表论文"),
    )
    profile_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("profile_url", "主页URL", "主页链接", "个人主页"),
    )
    source_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("source_url", "证据来源", "来源页面", "页面URL"),
    )
    confidence: float = Field(
        default=0.0,
        validation_alias=AliasChoices("confidence", "置信度"),
    )
    field_confidence: dict[str, float] | None = Field(
        default=None,
        validation_alias=AliasChoices("field_confidence", "字段置信度"),
    )
    evidence: dict[str, object] | None = Field(
        default=None,
        validation_alias=AliasChoices("evidence", "证据"),
    )
    source_chunk_id: str | None = None
    source_kind: str | None = None
    boundary_risk: bool = False
    identity_key: str | None = None
    merge_history: list[dict[str, object]] | None = None
    field_sources: dict[str, object] | None = None
    conflicts: dict[str, object] | None = None

    @field_validator("research_direction", mode="before")
    @classmethod
    def _normalize_research_direction(cls, value: object) -> object:
        return normalize_research_direction(value)

    @field_validator("recent_papers", mode="before")
    @classmethod
    def _normalize_recent_papers(cls, value: object) -> list[str]:
        return normalize_recent_papers(value)

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalize_confidence(cls, value: object) -> float:
        return _clamp_confidence(value)

    @field_validator("field_confidence", mode="before")
    @classmethod
    def _normalize_field_confidence(cls, value: object) -> dict[str, float] | None:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            numeric = _try_float(stripped)
            return {"overall": numeric} if numeric is not None else None
        if isinstance(value, (int, float)):
            return {"overall": float(value)}
        if not isinstance(value, dict):
            return None

        normalized: dict[str, float] = {}
        for key, item in value.items():
            if str(key) == "fields" and isinstance(item, dict):
                for nested_key, nested_item in item.items():
                    numeric = _normalize_confidence_value(nested_item)
                    if numeric is not None and str(nested_key).strip():
                        normalized[str(nested_key).strip()] = numeric
                continue
            numeric = _normalize_confidence_value(item)
            if numeric is not None and str(key).strip():
                normalized[str(key).strip()] = numeric
        return normalized or None

    @field_validator("evidence", mode="before")
    @classmethod
    def _normalize_evidence(cls, value: object) -> dict[str, object] | None:
        if value is None:
            return None
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            return {"summary": stripped} if stripped else None
        return None


class CandidateEnrichmentPayload(BaseModel):
    page_relation: Literal["matched", "mismatched", "uncertain"] = "uncertain"
    email: str | None = None
    title: str | None = None
    department: str | None = None
    research_direction: str | None = None
    recent_papers: list[str] = Field(default_factory=list)

    @field_validator("title", mode="before")
    @classmethod
    def _normalize_title(cls, value: object) -> str | None:
        return normalize_professor_title(_clean_optional(value))

    @field_validator("recent_papers", mode="before")
    @classmethod
    def _normalize_recent_papers(cls, value: object) -> list[str]:
        return normalize_recent_papers(value)


class CandidateBatchFailure(TypedDict):
    index: int
    name: str | None
    reason: str


class SharedCandidateSaveResult(TypedDict):
    attempted_count: int
    saved_count: int
    merged_count: int
    skipped_duplicate_count: int
    rejected_count: int
    rejected_items: list[CandidateBatchFailure]
    saved: list[CrawlCandidate]


@dataclass(frozen=True)
class CandidatePersistenceResult:
    saved: list[CrawlCandidate]
    merged_count: int = 0
    skipped_duplicate_count: int = 0

def _is_spa_route_fragment(fragment: str) -> bool:
    return fragment.startswith("/") or fragment.startswith("!/")

def _normalize_url_for_deduplication(url: str) -> str:
    parsed = urlparse(url.strip())
    if not _is_spa_route_fragment(parsed.fragment):
        parsed = parsed._replace(fragment="")
    return parsed.geturl()

def normalize_navigable_url(value: object, *, base_url: str | None = None) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    absolute = urljoin(base_url or "", raw) if base_url else raw
    parsed = urlparse(absolute)
    if not _is_spa_route_fragment(parsed.fragment):
        parsed = parsed._replace(fragment="")
    normalized = parsed.geturl().rstrip("/")
    return normalized or None

def _normalize_page_cache_url(url: str) -> str:
    return _normalize_url_for_deduplication(url)


def normalize_candidate_profile_url(value: object, *, base_url: str | None = None) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if base_url and _looks_like_hostname_without_scheme(raw):
        base_scheme = urlparse(base_url).scheme.lower()
        raw = f"{base_scheme if base_scheme in {'http', 'https'} else 'https'}://{raw}"
    return normalize_navigable_url(raw, base_url=base_url)


def _looks_like_hostname_without_scheme(value: str) -> bool:
    if value.startswith(("/", "./", "../", "//", "#", "?")) or "://" in value:
        return False
    authority = re.split(r"[/#?]", value, maxsplit=1)[0]
    if "@" in authority:
        return False
    host = authority.rsplit(":", 1)[0] if authority.rsplit(":", 1)[-1].isdigit() else authority
    labels = host.rstrip(".").split(".")
    if len(labels) < 3:
        return False
    return all(
        label
        and len(label) <= 63
        and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
        for label in labels
    )



def _normalize_listing_url(value: object, *, base_url: str | None = None) -> str | None:
    return normalize_navigable_url(value, base_url=base_url)


def _candidate_profile_url_matches_known_listing_url(profile_url: str | None, listing_urls: set[str]) -> bool:
    return bool(profile_url and profile_url in listing_urls)


def _clear_listing_profile_url(payload: dict[str, Any], removed_profile_url: str) -> None:
    payload["profile_url"] = None
    field_confidence = payload.get("field_confidence")
    if isinstance(field_confidence, dict):
        field_confidence.pop("profile_url", None)
    evidence = payload.get("evidence")
    if not isinstance(evidence, dict):
        evidence = {}
    evidence["profile_url_removed_reason"] = "matches_known_listing_url"
    evidence["removed_profile_url"] = removed_profile_url
    payload["evidence"] = evidence

def _candidate_missing_contact_path(payload: dict[str, Any]) -> bool:
    email = str(payload.get("email") or "").strip()
    profile_url = str(payload.get("profile_url") or "").strip()
    return not email and not profile_url

_MERGEABLE_TEXT_FIELDS = (
    "email",
    "title",
    "university",
    "school",
    "department",
    "research_direction",
    "profile_url",
    "source_url",
)


def _merge_json_dict(current: object, incoming: object) -> dict[str, object]:
    merged: dict[str, object] = {}
    if isinstance(current, dict):
        merged.update(current)
    if isinstance(incoming, dict):
        merged.update(incoming)
    return merged


def _append_json_list(current: object, item: dict[str, object], *, limit: int = 20) -> list[dict[str, object]]:
    entries = list(current) if isinstance(current, list) else []
    entries.append(item)
    return entries[-limit:]


def _field_source_entry(payload: dict[str, Any], field_name: str) -> dict[str, object]:
    return {
        "source_kind": payload.get("source_kind"),
        "source_chunk_id": payload.get("source_chunk_id"),
        "source_url": payload.get("source_url"),
        "confidence": _field_confidence(payload.get("field_confidence"), field_name),
        "boundary_risk": bool(payload.get("boundary_risk")),
    }


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
    if _SOURCE_PRIORITY.get(new_source_kind, 1) > _SOURCE_PRIORITY.get(old_source_kind, 1):
        return True
    if old_boundary_risk and not new_boundary_risk:
        return True
    return (new_confidence or 0) > (old_confidence or 0) + 0.2


def _field_confidence(value: object, field_name: str) -> float | None:
    if not isinstance(value, dict):
        return None
    raw = value.get(field_name)
    return float(raw) if isinstance(raw, (int, float)) else None


def _merge_candidate_payload(existing: CrawlCandidate, payload: dict[str, Any]) -> bool:
    return merge_candidate_payload_shared(existing, payload)


async def _known_listing_urls_for_job(session: AsyncSession, *, job_id: int, start_url: str) -> set[str]:
    listing_urls: set[str] = set()
    job = await session.get(CrawlJob, job_id)
    if job is not None:
        for url in [job.start_url, *(job.start_urls or [])]:
            normalized = _normalize_listing_url(url, base_url=start_url)
            if normalized:
                listing_urls.add(normalized)
    else:
        normalized = _normalize_listing_url(start_url, base_url=start_url)
        if normalized:
            listing_urls.add(normalized)

    rows = await session.scalars(select(CrawlPageTask.normalized_url).where(CrawlPageTask.job_id == job_id))
    for url in rows:
        normalized = _normalize_listing_url(url, base_url=start_url)
        if normalized:
            listing_urls.add(normalized)
    return listing_urls


async def _find_existing_candidate_for_payload(
    session: AsyncSession,
    *,
    job_id: int,
    name: str | None,
    email: str | None,
    profile_url: str | None,
    identity_key: str | None = None,
) -> CrawlCandidate | None:
    row = await find_canonical_candidate_for_identity(
        session,
        job_id=job_id,
        name=name,
        email=email,
        profile_url=profile_url,
    )
    if row is not None:
        return row
    if identity_key:
        return await session.scalar(
            select(CrawlCandidate).where(
                CrawlCandidate.job_id == job_id,
                CrawlCandidate.identity_key == identity_key,
                canonical_candidate_clause(),
            )
        )
    return None


@dataclass(frozen=True)
class CrawlToolContext:
    job_id: int
    start_url: str
    university: str
    school: str
    session_factory: async_sessionmaker[AsyncSession]
    http_blocked_hosts: set[str] = field(default_factory=set)
    denied_urls: dict[str, str] = field(default_factory=dict)
    page_snapshot_cache: OrderedDict[str, PageSnapshot] = field(default_factory=OrderedDict)
    known_listing_urls: set[str] = field(default_factory=set)
    llm_adaptation: LLMRuntimeAdaptation = field(
        default_factory=lambda: LLMRuntimeAdaptation("chat_completions", None)
    )
    entry_type: str | None = None
    claim_fence: CrawlerV2ClaimFence | None = None
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
            normalized = _normalize_page_cache_url(snapshot.url)
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
        if self.profile_landing_hosts and safe_url.hostname not in self.profile_landing_hosts:
            return False
        self.profile_landing_hosts.add(safe_url.hostname)
        return True


class CrawlJobPaused(RuntimeError):
    """Raised internally when a crawl job is paused at a safe checkpoint."""


class CrawlJobCanceled(RuntimeError):
    """Raised internally when a crawl job is canceled at a safe checkpoint."""


@dataclass(frozen=True)
class _SafeCrawlUrl:
    hostname: str
    resolved_ips: tuple[str, ...]


def is_allowed_crawl_url(start_url: str, candidate_url: str) -> bool:
    start = urlparse(start_url)
    candidate = urlparse(urljoin(start_url, candidate_url))
    absolute_candidate_url = candidate.geturl()
    if not is_safe_public_crawl_url(start_url):
        return False
    if not is_safe_public_crawl_url(absolute_candidate_url):
        return False
    start_host = (start.hostname or "").lower()
    candidate_host = (candidate.hostname or "").lower()
    start_domain = _registrable_domain(start_host)
    candidate_domain = _registrable_domain(candidate_host)
    return bool(start_domain and start_domain == candidate_domain)


def _is_resolved_allowed_crawl_url(
    start_url: str,
    candidate_url: str,
    *,
    allow_public_dns_fallback: bool = False,
) -> bool:
    absolute_candidate_url = urljoin(start_url, candidate_url)
    if not is_allowed_crawl_url(start_url, absolute_candidate_url):
        return False
    try:
        _resolve_safe_public_crawl_url(
            start_url,
            allow_public_dns_fallback=allow_public_dns_fallback,
        )
        _resolve_safe_public_crawl_url(
            absolute_candidate_url,
            allow_public_dns_fallback=allow_public_dns_fallback,
        )
    except ValueError:
        return False
    return True


def _is_resolved_context_url(ctx: CrawlToolContext, candidate_url: str) -> bool:
    absolute_candidate_url = urljoin(ctx.start_url, candidate_url)
    if not ctx.allows_url(absolute_candidate_url):
        return False
    try:
        _resolve_safe_public_crawl_url(
            ctx.start_url,
            allow_public_dns_fallback=ctx.allow_public_dns_fallback,
        )
        _resolve_safe_public_crawl_url(
            absolute_candidate_url,
            allow_public_dns_fallback=ctx.allow_public_dns_fallback,
        )
    except ValueError:
        return False
    return True


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


def is_safe_public_crawl_url(url: str) -> bool:
    try:
        validate_safe_public_crawl_url(url)
    except ValueError:
        return False
    return True


def validate_safe_public_crawl_url(url: str) -> None:
    _validate_safe_crawl_url_literal(url)


def _validate_safe_crawl_url_literal(url: str) -> tuple[str, str, int]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(UNSAFE_CRAWL_URL_MESSAGE)

    host = parsed.hostname
    if not host:
        raise ValueError(UNSAFE_CRAWL_URL_MESSAGE)

    normalized_host = host.rstrip(".").lower()
    if normalized_host == "localhost" or normalized_host.endswith(".localhost"):
        raise ValueError(UNSAFE_CRAWL_URL_MESSAGE)

    try:
        ip_address = ipaddress.ip_address(normalized_host)
    except ValueError:
        return normalized_host, parsed.scheme, parsed.port or _default_port_for_scheme(parsed.scheme)

    if _is_unsafe_ip_address(ip_address):
        raise ValueError(UNSAFE_CRAWL_URL_MESSAGE)
    return normalized_host, parsed.scheme, parsed.port or _default_port_for_scheme(parsed.scheme)


def _resolve_safe_public_crawl_url(
    url: str,
    *,
    allow_public_dns_fallback: bool = False,
) -> _SafeCrawlUrl:
    normalized_host, _scheme, port = _validate_safe_crawl_url_literal(url)
    try:
        ip_address = ipaddress.ip_address(normalized_host)
    except ValueError:
        try:
            resolved_ips = _resolve_system_host_ips(normalized_host, port)
        except ValueError:
            if not allow_public_dns_fallback:
                raise
            resolved_ips = _resolve_public_dns_host_ips(normalized_host)
        return _SafeCrawlUrl(hostname=normalized_host, resolved_ips=resolved_ips)
    return _SafeCrawlUrl(hostname=normalized_host, resolved_ips=(str(ip_address),))


def _resolve_system_host_ips(host: str, port: int) -> tuple[str, ...]:
    try:
        address_infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(UNSAFE_CRAWL_URL_MESSAGE) from exc

    if not address_infos:
        raise ValueError(UNSAFE_CRAWL_URL_MESSAGE)

    resolved_ips: list[str] = []
    for address_info in address_infos:
        sockaddr = address_info[4]
        if not sockaddr:
            raise ValueError(UNSAFE_CRAWL_URL_MESSAGE)
        ip_text = str(sockaddr[0])
        try:
            ip_address = ipaddress.ip_address(ip_text)
        except ValueError as exc:
            raise ValueError(UNSAFE_CRAWL_URL_MESSAGE) from exc
        if _is_unsafe_ip_address(ip_address):
            raise ValueError(UNSAFE_CRAWL_URL_MESSAGE)
        normalized_ip = str(ip_address)
        if normalized_ip not in resolved_ips:
            resolved_ips.append(normalized_ip)
    return tuple(resolved_ips)


@lru_cache(maxsize=256)
def _resolve_public_dns_host_ips(host: str) -> tuple[str, ...]:
    resolved_ips: list[str] = []
    for record_type in ("A", "AAAA"):
        try:
            response = httpx.get(
                "https://cloudflare-dns.com/dns-query",
                params={"name": host, "type": record_type},
                headers={"Accept": "application/dns-json"},
                timeout=5.0,
                follow_redirects=True,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise ValueError(UNSAFE_CRAWL_URL_MESSAGE) from exc
        if payload.get("Status") != 0:
            raise ValueError(UNSAFE_CRAWL_URL_MESSAGE)
        for answer in payload.get("Answer") or []:
            if not isinstance(answer, dict) or answer.get("type") not in {1, 28}:
                continue
            try:
                ip_address = ipaddress.ip_address(str(answer.get("data") or ""))
            except ValueError as exc:
                raise ValueError(UNSAFE_CRAWL_URL_MESSAGE) from exc
            if _is_unsafe_ip_address(ip_address):
                raise ValueError(UNSAFE_CRAWL_URL_MESSAGE)
            normalized_ip = str(ip_address)
            if normalized_ip not in resolved_ips:
                resolved_ips.append(normalized_ip)
    if not resolved_ips:
        raise ValueError(UNSAFE_CRAWL_URL_MESSAGE)
    return tuple(resolved_ips)


def _default_port_for_scheme(scheme: str) -> int:
    return 80 if scheme == "http" else 443


def _registrable_domain(hostname: str) -> str:
    return registrable_domain_from_hostname(hostname)


class _PinnedCrawlNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(
        self,
        *,
        hostname: str,
        resolved_ip: str,
        network_backend: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        self._hostname = hostname.rstrip(".").lower()
        self._resolved_ip = resolved_ip
        self._network_backend = network_backend or _default_async_network_backend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        if host.rstrip(".").lower() != self._hostname:
            raise httpcore.ConnectError("crawl transport attempted an unvalidated host")
        return await self._network_backend.connect_tcp(
            self._resolved_ip,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        return await self._network_backend.connect_unix_socket(
            path,
            timeout=timeout,
            socket_options=socket_options,
        )

    async def sleep(self, seconds: float) -> None:
        await self._network_backend.sleep(seconds)


def _default_async_network_backend() -> httpcore.AsyncNetworkBackend:
    return httpcore.AnyIOBackend()


def _build_safe_crawl_transport(
    *,
    hostname: str,
    resolved_ip: str,
    network_backend: httpcore.AsyncNetworkBackend | None = None,
) -> httpx.AsyncHTTPTransport:
    transport = httpx.AsyncHTTPTransport(
        trust_env=False,
        proxy=None,
        http2=False,
        limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
    )
    transport._pool._network_backend = _PinnedCrawlNetworkBackend(  # type: ignore[attr-defined]
        hostname=hostname,
        resolved_ip=resolved_ip,
        network_backend=network_backend,
    )
    return transport


def _is_unsafe_ip_address(ip_address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(
        (
            not ip_address.is_global,
            ip_address.is_private,
            ip_address.is_loopback,
            ip_address.is_link_local,
            ip_address.is_multicast,
            ip_address.is_unspecified,
            ip_address.is_reserved,
        )
    )


def normalize_candidate_payload(
    candidate: ProfessorCandidatePayload,
    *,
    university: str,
    school: str,
) -> dict[str, Any]:
    papers = normalize_recent_papers(candidate.recent_papers, max_items=RECENT_PAPERS_MAX_ITEMS)
    field_confidence = None
    if candidate.field_confidence is not None:
        field_confidence = {
            str(key).strip(): _clamp_confidence(value)
            for key, value in candidate.field_confidence.items()
            if str(key).strip()
        }

    return {
        "name": _clean_required(candidate.name),
        "email": _first_valid_email(candidate.email),
        "title": normalize_professor_title(_clean_optional(candidate.title)),
        "university": _clean_optional(candidate.university) or _clean_required(university),
        "school": _clean_optional(candidate.school) or _clean_required(school),
        "department": _clean_optional(candidate.department),
        "research_direction": _clean_optional(candidate.research_direction),
        "recent_papers": papers,
        "profile_url": _clean_optional(candidate.profile_url),
        "source_url": _clean_optional(candidate.source_url),
        "confidence": _clamp_confidence(candidate.confidence),
        "field_confidence": field_confidence,
        "evidence": candidate.evidence,
        "source_chunk_id": getattr(candidate, "source_chunk_id", None),
        "source_kind": getattr(candidate, "source_kind", None),
        "boundary_risk": bool(getattr(candidate, "boundary_risk", False)),
        "identity_key": getattr(candidate, "identity_key", None),
        "merge_history": getattr(candidate, "merge_history", None),
        "field_sources": getattr(candidate, "field_sources", None),
        "conflicts": getattr(candidate, "conflicts", None),
    }


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


def _first_valid_email(value: str | None) -> str | None:
    cleaned = _clean_optional(value)
    if not cleaned:
        return None
    return extract_first_email_from_text(cleaned)


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
                    snapshot = html_to_snapshot(str(response.url), response.text, "http")
                    snapshot.http_status_code = response.status_code
                    snapshot.status = "failed"
                    snapshot.error_message = (
                        f"HTTP {response.status_code} blocked, browser fallback advised"
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

            location = response.headers.get("Location") or response.headers.get("location")
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
                    location = response.headers.get("Location") or response.headers.get("location")
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
                    str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower(),
                    b"".join(chunks),
                )
    raise ValueError("资源抓取失败")


def _snapshot_from_page_fetch_decision(url: str, decision: PageFetchDecision) -> PageSnapshot | None:
    if decision.action == "skip_terminal_failed":
        message = decision.message or decision.terminal_reason or "terminal_failed"
        if message.startswith("该页面此前已明确抓取失败，已跳过："):
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

        decision = await get_page_fetch_decision(ctx.session_factory, job_id=ctx.job_id, url=absolute_url)
        decision_snapshot = _snapshot_from_page_fetch_decision(absolute_url, decision)
        if decision_snapshot is not None:
            await _ensure_crawl_job_can_continue_for_context(ctx)
            return decision_snapshot

    prefer_browser_for_domain = await should_prefer_browser_for_fetch_domain(
        ctx.session_factory,
        job_id=ctx.job_id,
        url=absolute_url,
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
        elif browser_snapshot.status != "succeeded" and http_snapshot.status == "succeeded":
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

        decision = await get_page_fetch_decision(ctx.session_factory, job_id=ctx.job_id, url=absolute_url)
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
    return any(pattern.search(title) for pattern in _SOFT_404_PROFILE_TITLE_PATTERNS) and any(
        pattern.search(text) for pattern in _SOFT_404_PROFILE_BODY_PATTERNS
    )


def looks_like_client_encrypted_profile_fields(snapshot: PageSnapshot) -> bool:
    if snapshot.has_client_encrypted_profile_fields:
        return True
    html = snapshot.html or ""
    return any(marker in html for marker in CLIENT_ENCRYPTED_PROFILE_FIELD_MARKERS)


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

    collections = list(soup.select("ul, ol, tbody"))
    populated_families = {
        _dynamic_collection_family(container)
        for container in collections
        if _dynamic_collection_has_content(container)
    }

    for container in collections:
        if _dynamic_collection_has_content(container):
            continue
        if container.has_attr("hidden") or str(container.get("aria-hidden") or "").lower() == "true":
            continue

        container_tokens = _html_structure_tokens(container)
        if container.name != "tbody" and not container_tokens.intersection(_DYNAMIC_COLLECTION_TOKENS):
            continue

        ancestors = list(container.parents)
        context_tokens = set().union(*(_html_structure_tokens(parent) for parent in ancestors))
        if container_tokens.intersection(_DYNAMIC_NON_CONTENT_TOKENS):
            continue
        if context_tokens.intersection(_DYNAMIC_NON_CONTENT_TOKENS):
            continue
        if any(getattr(parent, "name", None) in {"header", "footer", "nav", "aside"} for parent in ancestors):
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
        token
        for token in re.split(r"[^a-z0-9]+", " ".join(values).lower())
        if token
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
        token
        for token in re.split(r"[^a-z0-9]+", " ".join(values).lower())
        if token
    }


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


def _is_immediate_http_compatibility_error(
    requested_url: str,
    snapshot: PageSnapshot,
) -> bool:
    if snapshot.status != "failed" or urlparse(requested_url).scheme.lower() != "https":
        return False
    error_message = (snapshot.error_message or "").lower()
    return any(
        marker in error_message
        for marker in IMMEDIATE_HTTP_COMPATIBILITY_ERROR_MARKERS
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


def _browser_fetch_options_for_goal(goal: str) -> BrowserFetchOptions:
    _ = goal
    return _browser_fetch_options_for_intent("generic")


def _playwright_launch_options() -> dict[str, object]:
    return {
        "headless": True,
        "args": list(BROWSER_EXTRA_ARGS),
    }


async def _crawl_page_with_browser(
    ctx: CrawlToolContext,
    absolute_url: str,
    goal: str,
    intent: CrawlPageIntent = "generic",
) -> PageSnapshot:
    if not _is_resolved_context_url(ctx, absolute_url):
        return _failed_snapshot(
            url=absolute_url,
            fetch_method="browser",
            error_message=UNSAFE_CRAWL_URL_MESSAGE,
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
    if snapshot.status == "succeeded" and not _is_resolved_context_url(ctx, snapshot.url):
        if not ctx.accept_profile_redirect(absolute_url, snapshot.url):
            return _failed_snapshot(
                url=snapshot.url,
                fetch_method="browser",
                error_message="浏览器最终 URL 不在允许的同校公网域名范围内",
            )
    return snapshot


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
    if not _is_resolved_allowed_crawl_url(
        ctx.start_url,
        absolute_url,
        allow_public_dns_fallback=ctx.allow_public_dns_fallback,
    ):
        return BrowserPaginationExpansion(
            status="failed",
            stopped_reason="unsafe_url",
            error_message=UNSAFE_CRAWL_URL_MESSAGE,
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
        if not _is_resolved_allowed_crawl_url(
            ctx.start_url,
            snapshot.url,
            allow_public_dns_fallback=ctx.allow_public_dns_fallback,
        ):
            return BrowserPaginationExpansion(
                status="failed",
                snapshots=result.snapshots,
                stopped_reason="unsafe_final_url",
                error_message="浏览器分页后的最终 URL 不在允许的同校公网域名范围内",
            )
    return result


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
            dynamic_link_pagination = False
            snapshots: list[PageSnapshot] = []
            stopped_reason = "page_limit_reached"

            for _ in range(max(1, int(max_pages)) - 1):
                match = await page.evaluate(
                    _BROWSER_PAGINATION_CONTROL_MATCH_SCRIPT,
                    target,
                )
                if not isinstance(match, dict) or not isinstance(match.get("index"), int):
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
                    await page.locator(str(target["tag"])).nth(int(match["index"])).click(
                        timeout=BROWSER_PAGINATION_CHANGE_TIMEOUT_MS,
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
        if (
            not options.ignore_https_errors
            and _is_certificate_date_error(last_result.error_message)
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
        if last_result.status == "succeeded" or _is_wait_condition_failure(last_result.error_message):
            return last_result
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
                await page.wait_for_timeout(options.delay_before_return_html_seconds * 1000)
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
    return all(marker in normalized for marker in _BROWSER_CONTENT_NAVIGATION_ERROR_MARKERS)


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
        marker in normalized_message
        for marker in CERTIFICATE_DATE_ERROR_MARKERS
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


def _normalize_candidate_payloads_for_save(
    ctx: CrawlToolContext,
    candidates: Sequence[ProfessorCandidatePayload | dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[CandidateBatchFailure]]:
    payloads: list[dict[str, Any]] = []
    failed_items: list[CandidateBatchFailure] = []
    for index, candidate in enumerate(candidates):
        try:
            payload = normalize_candidate_payload(
                candidate,
                university=ctx.university,
                school=ctx.school,
            )
            if payload.get("source_kind") in (None, ""):
                payload["source_kind"] = (
                    "profile_page" if ctx.entry_type == "profile" else "list_chunk"
                )
            payloads.append(payload)
        except (TypeError, ValueError) as exc:
            failed_items.append(
                {
                    "index": index,
                    "name": _clean_optional(getattr(candidate, "name", None)),
                    "reason": str(exc),
                }
            )
    return payloads, failed_items


def _filter_accepted_candidate_payloads(
    payloads: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[CandidateBatchFailure]]:
    accepted_payloads: list[dict[str, Any]] = []
    rejected_items: list[CandidateBatchFailure] = []
    for index, payload in enumerate(payloads):
        if _candidate_missing_contact_path(payload):
            rejected_items.append(
                {
                    "index": index,
                    "name": _clean_optional(payload.get("name")),
                    "reason": "缺少邮箱和详情页链接，无法用于联系或后续补全",
                }
            )
            continue
        accepted_payloads.append(payload)
    return accepted_payloads, rejected_items


async def _normalize_candidate_profile_urls_for_save(
    ctx: CrawlToolContext,
    payloads: Sequence[dict[str, Any]],
) -> None:
    """Normalize candidate profile URLs before contact-path validation."""

    known_listing_urls: set[str] = set(ctx.known_listing_urls)
    if ctx.entry_type != "profile":
        async with ctx.session_factory() as session:
            known_listing_urls.update(
                await _known_listing_urls_for_job(
                    session,
                    job_id=ctx.job_id,
                    start_url=ctx.start_url,
                )
            )

    for payload in payloads:
        normalized_profile_url = normalize_candidate_profile_url(
            payload.get("profile_url"),
            base_url=ctx.start_url,
        )
        if _candidate_profile_url_matches_known_listing_url(
            normalized_profile_url,
            known_listing_urls,
        ):
            _clear_listing_profile_url(payload, normalized_profile_url or "")
        else:
            payload["profile_url"] = normalized_profile_url


async def save_candidate_payloads_shared(
    ctx: CrawlToolContext,
    candidates: Sequence[ProfessorCandidatePayload | dict[str, Any]],
) -> SharedCandidateSaveResult:
    payloads, failed_items = _normalize_candidate_payloads_for_save(ctx, candidates)
    if failed_items:
        return {
            "attempted_count": len(candidates),
            "saved_count": 0,
            "merged_count": 0,
            "skipped_duplicate_count": 0,
            "rejected_count": 0,
            "rejected_items": failed_items,
            "saved": [],
        }
    await _normalize_candidate_profile_urls_for_save(ctx, payloads)
    accepted_payloads, rejected_items = _filter_accepted_candidate_payloads(payloads)
    persistence = await _save_normalized_candidate_payloads(ctx, accepted_payloads)
    return {
        "attempted_count": len(candidates),
        "saved_count": len(persistence.saved),
        "merged_count": persistence.merged_count,
        "skipped_duplicate_count": persistence.skipped_duplicate_count,
        "rejected_count": len(rejected_items),
        "rejected_items": rejected_items,
        "saved": persistence.saved,
    }


async def _save_normalized_candidate_payloads(
    ctx: CrawlToolContext,
    payloads: Sequence[dict[str, Any]],
) -> CandidatePersistenceResult:
    saved: list[CrawlCandidate] = []
    merged_count = 0
    skipped_duplicate_count = 0
    async with ctx.session_factory() as session:
        if ctx.claim_fence is not None and not await fence_crawler_v2_claim(
            session,
            ctx.claim_fence,
        ):
            await session.rollback()
            return CandidatePersistenceResult(saved=[])
        if await _is_crawl_job_stopped(session, ctx.job_id):
            return CandidatePersistenceResult(saved=[])

        for payload in payloads:
            payload["recent_papers"] = normalize_recent_papers(payload.get("recent_papers"))
            email = payload["email"]
            normalized_email = str(email).lower() if email else None
            normalized_profile_url = payload.get("profile_url")
            identity_key = payload.get("identity_key") or normalized_email or normalized_profile_url

            existing = await _find_existing_candidate_for_payload(
                session,
                job_id=ctx.job_id,
                name=payload.get("name"),
                email=normalized_email,
                profile_url=normalized_profile_url,
                identity_key=identity_key,
            )
            identities = candidate_identity_values(
                name=payload.get("name"),
                email=normalized_email,
                profile_url=normalized_profile_url,
            )
            if existing is not None:
                if _merge_candidate_payload(existing, payload):
                    merged_count += 1
                else:
                    skipped_duplicate_count += 1
                await consolidate_candidate_identity(
                    session,
                    existing,
                    additional_identities=identities,
                )
                continue

            if not payload.get("identity_key"):
                payload["identity_key"] = identity_key
            if not payload.get("field_sources"):
                payload["field_sources"] = {
                    field_name: _field_source_entry(payload, field_name)
                    for field_name in (*_MERGEABLE_TEXT_FIELDS, "recent_papers")
                    if payload.get(field_name) not in (None, "", [])
                }

            if await _is_crawl_job_stopped(session, ctx.job_id):
                await session.rollback()
                return CandidatePersistenceResult(saved=[])

            row = CrawlCandidate(job_id=ctx.job_id, **payload)
            try:
                async with session.begin_nested():
                    session.add(row)
                    await session.flush()
            except IntegrityError:
                existing = await _find_existing_candidate_for_payload(
                    session,
                    job_id=ctx.job_id,
                    name=payload.get("name"),
                    email=normalized_email,
                    profile_url=normalized_profile_url,
                    identity_key=identity_key,
                )
                if existing is None:
                    raise
                if _merge_candidate_payload(existing, payload):
                    merged_count += 1
                else:
                    skipped_duplicate_count += 1
                await consolidate_candidate_identity(
                    session,
                    existing,
                    additional_identities=identities,
                )
                continue
            canonical = await consolidate_candidate_identity(
                session,
                row,
                additional_identities=identities,
            )
            if canonical.id == row.id:
                saved.append(row)
            else:
                merged_count += 1

        if await _is_crawl_job_stopped(session, ctx.job_id):
            await session.rollback()
            return CandidatePersistenceResult(saved=[])

        await session.commit()
        for row in saved:
            await session.refresh(row)
    return CandidatePersistenceResult(
        saved=saved,
        merged_count=merged_count,
        skipped_duplicate_count=skipped_duplicate_count,
    )


async def record_page_snapshot(ctx: CrawlToolContext, snapshot: PageSnapshot) -> CrawlPage | None:
    row = CrawlPage(
        job_id=ctx.job_id,
        url=snapshot.url,
        parent_url=None,
        fetch_method=snapshot.fetch_method,
        page_type="unknown",
        status=snapshot.status,
        title=snapshot.title,
        text_excerpt=snapshot.text[:MAX_TEXT_CHARS] or None,
        error_message=snapshot.error_message,
        created_at=utc_now(),
    )
    async with ctx.session_factory() as session:
        if await _is_crawl_job_stopped(session, ctx.job_id):
            return None

        session.add(row)
        if await _is_crawl_job_stopped(session, ctx.job_id):
            await session.rollback()
            return None

        await session.commit()
        await session.refresh(row)
        snapshot.page_id = row.id
        return row


def html_to_snapshot(
    url: str,
    html: str,
    fetch_method: str,
    *,
    embedded_documents: Sequence[tuple[str, str]] = (),
) -> PageSnapshot:
    """Turn one page and optional frame documents into a bounded snapshot.

    The old implementation sliced raw HTML before parsing. Excel-exported
    faculty pages put their contact row after a large amount of invisible
    style/VML markup, so that slice could remove the only email. We now remove
    non-visible markup first, extract text and links from the complete cleaned
    documents, and only then apply the existing snapshot budget.
    """

    documents = [(url, html)] + [
        (document_url, document_html)
        for document_url, document_html in embedded_documents
        if document_html
    ]
    cleaned_documents = [
        (document_url, _clean_snapshot_soup(document_html))
        for document_url, document_html in documents
    ]
    has_client_encrypted_profile_fields = any(
        any(marker in document_html for marker in CLIENT_ENCRYPTED_PROFILE_FIELD_MARKERS)
        for _document_url, document_html in documents
    )
    has_dynamic_teacher_directory_markers = any(
        any(marker in document_html.lower() for marker in DYNAMIC_TEACHER_DIRECTORY_MARKERS)
        for _document_url, document_html in documents
    )
    has_invalid_profile_page_markers = any(
        any(marker.lower() in document_html.lower() for marker in INVALID_PROFILE_PAGE_MARKERS)
        for _document_url, document_html in documents
    )
    main_soup = cleaned_documents[0][1]
    title = _clean_optional(
        main_soup.title.get_text(" ", strip=True) if main_soup.title else None
    )
    if not title:
        for _document_url, document_soup in cleaned_documents[1:]:
            if document_soup.title:
                title = _clean_optional(document_soup.title.get_text(" ", strip=True))
                if title:
                    break

    text_parts = [
        html_to_text(str(document_soup))
        for _document_url, document_soup in cleaned_documents
    ]
    text = "\n\n".join(part for part in text_parts if part)
    text = text.replace("\ufeff", "").strip()[:MAX_TEXT_CHARS]

    links: list[str] = []
    seen_links: set[str] = set()
    for document_url, document_soup in cleaned_documents:
        for tag in document_soup.find_all("a", href=True):
            link = urljoin(document_url, str(tag["href"]).strip())
            parsed = urlparse(link)
            if parsed.scheme not in {"http", "https"} or link in seen_links:
                continue
            seen_links.add(link)
            links.append(link)
            if len(links) >= MAX_LINKS:
                break
        if len(links) >= MAX_LINKS:
            break

    serialized_documents = [str(main_soup)]
    for document_url, document_soup in cleaned_documents[1:]:
        serialized_documents.append(
            '<section data-crawl-frame-url="{}">{}</section>'.format(
                escape(document_url, quote=True),
                document_soup,
            )
        )
    bounded_html = _bound_snapshot_html("\n".join(serialized_documents))

    return PageSnapshot(
        url=url,
        title=title,
        text=text,
        html=bounded_html,
        links=links,
        fetch_method=fetch_method,
        status="succeeded",
        suspicious_empty=not text,
        has_client_encrypted_profile_fields=has_client_encrypted_profile_fields,
        has_dynamic_teacher_directory_markers=has_dynamic_teacher_directory_markers,
        has_invalid_profile_page_markers=has_invalid_profile_page_markers,
    )


def _clean_snapshot_soup(html: str) -> BeautifulSoup:
    soup = parse_html(html or "")
    for tag in soup.find_all(True):
        attributes = " ".join(
            f"{attribute}={attribute_value}"
            for attribute, attribute_value in tag.attrs.items()
        )
        if any(marker in attributes for marker in CLIENT_ENCRYPTED_PROFILE_FIELD_MARKERS):
            tag.decompose()
    for tag in soup(["script", "style", "noscript", "template", "noframes"]):
        tag.decompose()
    for comment in soup.find_all(string=is_comment):
        comment.extract()
    return soup


def _bound_snapshot_html(html: str) -> str:
    if len(html) <= MAX_CRAWL_HTML_CHARS:
        return html
    marker = '\n<div data-crawl-truncated="true"></div>\n'
    available = max(0, MAX_CRAWL_HTML_CHARS - len(marker))
    head_size = available // 2
    tail_size = available - head_size
    return html[:head_size] + marker + (html[-tail_size:] if tail_size else "")


def _format_exception_for_snapshot(exc: BaseException, context: str) -> str:
    message = str(exc).strip()
    if message:
        return f"{context}: {type(exc).__name__}: {message}"
    return f"{context}: {type(exc).__name__}"


def _format_message_with_fallback(message: str, fallback: str) -> str:
    message = message.strip()
    return message or fallback


def _clean_required(value: object) -> str:
    cleaned = str(value).strip() if value is not None else ""
    if not cleaned:
        raise ValueError("必填文本不能为空")
    return cleaned


def _clean_optional(value: object) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _try_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


_CONFIDENCE_LABEL_MAP = {
    "very high": 1.0,
    "high": 0.9,
    "medium": 0.6,
    "moderate": 0.6,
    "low": 0.3,
    "very low": 0.1,
    "高": 0.9,
    "较高": 0.8,
    "中": 0.6,
    "中等": 0.6,
    "一般": 0.5,
    "低": 0.3,
    "较低": 0.2,
}


def _normalize_confidence_value(value: object) -> float | None:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    if not isinstance(value, str):
        return None

    stripped = value.strip()
    if not stripped:
        return None

    if stripped.endswith("%"):
        numeric = _try_float(stripped[:-1].strip())
        if numeric is not None:
            return numeric / 100

    numeric = _try_float(stripped)
    if numeric is not None:
        return numeric

    normalized = re.sub(r"[\s_-]+", " ", stripped.casefold())
    return _CONFIDENCE_LABEL_MAP.get(normalized)


def _clamp_confidence(value: object) -> float:
    number = _normalize_confidence_value(value)
    if number is None:
        return 0.0
    return min(1.0, max(0.0, number))


async def _load_existing_candidate_emails(session: AsyncSession, job_id: int) -> set[str]:
    result = await session.scalars(
        select(CrawlCandidate.email).where(
            CrawlCandidate.job_id == job_id,
            CrawlCandidate.email.is_not(None),
        )
    )
    return {email.lower() for email in result if email}


async def _load_existing_candidate_profile_urls(session: AsyncSession, job_id: int) -> set[str]:
    result = await session.scalars(
        select(CrawlCandidate.profile_url).where(
            CrawlCandidate.job_id == job_id,
            CrawlCandidate.profile_url.is_not(None),
        )
    )
    return {
        normalized
        for profile_url in result
        if (normalized := normalize_candidate_profile_url(profile_url))
    }


async def ensure_crawl_job_can_continue(session: AsyncSession, job_id: int) -> None:
    status = await _get_job_status(session, job_id)
    if status == CrawlJobStatus.PAUSED.value:
        raise CrawlJobPaused()
    if status == CrawlJobStatus.CANCELED.value:
        raise CrawlJobCanceled()


async def _ensure_crawl_job_can_continue_for_context(ctx: CrawlToolContext) -> None:
    async with ctx.session_factory() as session:
        await ensure_crawl_job_can_continue(session, ctx.job_id)


async def _is_crawl_job_stopped(session: AsyncSession, job_id: int) -> bool:
    status = await _get_job_status(session, job_id)
    return status in {CrawlJobStatus.PAUSED.value, CrawlJobStatus.CANCELED.value}


async def _get_job_status(session: AsyncSession, job_id: int) -> str | None:
    return await session.scalar(select(CrawlJob.status).where(CrawlJob.id == job_id))


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


def _has_unsafe_public_crawl_url(start_url: str, candidate_url: str) -> bool:
    return not is_safe_public_crawl_url(start_url) or not is_safe_public_crawl_url(candidate_url)


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
