from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import LLMProfile
from app.services.beautiful_soup import parse_html
from ..jobs.runs import extract_token_usage_from_llm_response
from ..llm.structured_output import request_crawler_structured_completion
from ..pages.url_safety import is_safe_public_crawl_url
from .url_utils import is_same_domain, normalize_url
from app.modules.llm.public import LLMRuntimeAdaptation


ENTRY_EXPANSION_MODE = "entry"
PAGINATION_EXPANSION_MODE = "pagination"
NO_EXPANSION_MODE = "none"

START_DISCOVERY_REASON = "start"
ENTRY_DISCOVERY_REASON = "entry"
IFRAME_DISCOVERY_REASON = "iframe"
PAGINATION_DISCOVERY_REASON = "pagination"

MAX_ROUTING_LINKS = 1200
MAX_ROUTING_CONTROLS = 200
MAX_ROUTING_VISIBLE_TEXT_CHARS = 8000
_CONTROL_STATE_CLASS_MARKERS = ("active", "current", "disabled", "selected")


class EntryRoutingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    discovered_urls: list[str]


class PaginationRoutingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_expansion: bool = Field(strict=True)
    pagination_urls: list[str] = Field(max_length=1)
    pagination_control_id: str | None

    @model_validator(mode="after")
    def _validate_expansion_contract(self) -> "PaginationRoutingPayload":
        control_id = (self.pagination_control_id or "").strip()
        if self.pagination_control_id is not None and not control_id:
            raise ValueError("pagination_control_id 只能是非空控件 ID 或 null")
        if self.allow_expansion != bool(self.pagination_urls or control_id):
            raise ValueError(
                "allow_expansion 必须与 pagination_urls 或 pagination_control_id 是否存在一致"
            )
        return self


@dataclass(frozen=True, slots=True)
class PageRouteLink:
    url: str
    label: str
    kind: Literal["link", "iframe"]


@dataclass(frozen=True, slots=True)
class PageRouteControl:
    control_id: str
    tag: str
    text: str
    title: str
    aria_label: str
    class_tokens: tuple[str, ...]
    match_index: int


@dataclass(frozen=True, slots=True)
class RoutingAttempt:
    phase: Literal["entry", "pagination"]
    attempt_number: int
    raw_model_text: str
    raw_payload: dict[str, Any] | None = None
    error: str | None = None
    usage: dict[str, int | None] | None = None


@dataclass(frozen=True, slots=True)
class PageRoutingResult:
    discovered_urls: list[str]
    entry_discovery_reasons: dict[str, str]
    allow_expansion: bool
    pagination_urls: list[str]
    usage: dict[str, int] | None
    pagination_control: PageRouteControl | None = None
    attempts: list[RoutingAttempt] = field(default_factory=list)


async def invoke_page_routing_agent(
    llm_profile: LLMProfile,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    university: str,
    school: str,
    start_url: str,
    source_url: str,
    title: str | None,
    page_text: str,
    page_html: str,
    expansion_mode: str,
    adaptation: LLMRuntimeAdaptation,
) -> PageRoutingResult:
    links = extract_page_route_links(source_url, page_html)
    controls = extract_page_route_controls(page_html)
    routing_context = build_page_routing_context(
        title=title,
        page_text=page_text,
        links=links,
        controls=controls,
    )
    attempts: list[RoutingAttempt] = []
    accumulated_usage = {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0}

    discovered_urls: list[str] = []
    entry_reasons: dict[str, str] = {}
    current_adaptation = adaptation
    if expansion_mode == ENTRY_EXPANSION_MODE:
        (
            entry_payload,
            entry_attempts,
            current_adaptation,
        ) = await _invoke_structured_routing_phase(
            llm_profile,
            session_factory=session_factory,
            adaptation=current_adaptation,
            phase="entry",
            prompt=build_entry_routing_prompt(
                university=university,
                school=school,
                source_url=source_url,
                routing_context=routing_context,
            ),
            result_model=EntryRoutingPayload,
        )
        attempts.extend(entry_attempts)
        _accumulate_attempt_usage(accumulated_usage, entry_attempts)
        discovered_urls = filter_model_selected_route_urls(
            entry_payload.discovered_urls,
            links=links,
            source_url=source_url,
            start_url=start_url,
        )
        link_kinds = {link.url: link.kind for link in links}
        entry_reasons = {
            url: (
                IFRAME_DISCOVERY_REASON
                if link_kinds.get(url) == "iframe"
                else ENTRY_DISCOVERY_REASON
            )
            for url in discovered_urls
        }

    pagination_payload, pagination_attempts, _ = await _invoke_structured_routing_phase(
        llm_profile,
        session_factory=session_factory,
        adaptation=current_adaptation,
        phase="pagination",
        prompt=build_pagination_routing_prompt(
            university=university,
            school=school,
            source_url=source_url,
            routing_context=routing_context,
        ),
        result_model=PaginationRoutingPayload,
    )
    attempts.extend(pagination_attempts)
    _accumulate_attempt_usage(accumulated_usage, pagination_attempts)
    pagination_urls = filter_model_selected_route_urls(
        pagination_payload.pagination_urls,
        links=links,
        source_url=source_url,
        start_url=start_url,
        max_results=1,
    )
    pagination_control = None
    if not pagination_urls:
        pagination_control = select_model_pagination_control(
            pagination_payload.pagination_control_id,
            controls=controls,
        )

    return PageRoutingResult(
        discovered_urls=discovered_urls,
        entry_discovery_reasons=entry_reasons,
        allow_expansion=bool(pagination_urls or pagination_control),
        pagination_urls=pagination_urls,
        usage=(accumulated_usage if any(accumulated_usage.values()) else None),
        pagination_control=pagination_control,
        attempts=attempts,
    )


def extract_page_route_links(source_url: str, page_html: str) -> list[PageRouteLink]:
    soup = parse_html(page_html or "")
    links_by_url: dict[str, PageRouteLink] = {}
    for tag in soup.find_all(["a", "iframe"]):
        kind: Literal["link", "iframe"] = "iframe" if tag.name == "iframe" else "link"
        raw_url = tag.get("src") if kind == "iframe" else tag.get("href")
        if not isinstance(raw_url, str) or not raw_url.strip():
            continue
        absolute_url = urljoin(source_url, raw_url.strip())
        try:
            normalized_url = normalize_url(absolute_url)
            scheme = urlsplit(normalized_url).scheme
        except ValueError:
            continue
        if scheme not in {"http", "https"}:
            continue
        label = _route_link_label(tag, kind=kind)
        record = PageRouteLink(url=normalized_url, label=label, kind=kind)
        existing = links_by_url.get(normalized_url)
        if existing is None or (existing.kind != "iframe" and kind == "iframe"):
            links_by_url[normalized_url] = record
        elif existing.kind == kind and label not in existing.label.split(" | "):
            links_by_url[normalized_url] = PageRouteLink(
                url=normalized_url,
                label=" | ".join((existing.label, label))[:240],
                kind=kind,
            )
        if len(links_by_url) >= MAX_ROUTING_LINKS:
            break
    return list(links_by_url.values())


def extract_page_route_controls(page_html: str) -> list[PageRouteControl]:
    soup = parse_html(page_html or "")
    controls: list[PageRouteControl] = []
    signature_counts: dict[tuple[object, ...], int] = {}
    for tag in soup.select("button, [role='button'], li[tabindex], a"):
        if tag.name == "a" and not _is_non_url_anchor_control(tag.get("href")):
            continue
        if _has_interactive_control_ancestor(tag):
            continue
        aria_disabled = str(tag.get("aria-disabled") or "").strip().lower()
        class_tokens = tuple(
            token
            for token in _normalized_class_tokens(tag.get("class"))
            if not any(
                marker in token.lower() for marker in _CONTROL_STATE_CLASS_MARKERS
            )
        )
        if tag.has_attr("disabled") or aria_disabled == "true":
            continue
        raw_classes = _normalized_class_tokens(tag.get("class"))
        if any("disabled" in token.lower() for token in raw_classes):
            continue
        text_value = _normalize_control_text(tag.get_text(" ", strip=True))
        title = _normalize_control_text(str(tag.get("title") or ""))
        aria_label = _normalize_control_text(str(tag.get("aria-label") or ""))
        if not aria_label:
            labelled_descendant = tag.find(attrs={"aria-label": True})
            if labelled_descendant is not None:
                aria_label = _normalize_control_text(
                    str(labelled_descendant.get("aria-label") or "")
                )
        if not any((text_value, title, aria_label, class_tokens)):
            continue
        tag_name = str(tag.name or "").lower()
        signature = (
            tag_name,
            text_value,
            title,
            aria_label,
            class_tokens,
        )
        match_index = signature_counts.get(signature, 0)
        signature_counts[signature] = match_index + 1
        controls.append(
            PageRouteControl(
                control_id=f"control-{len(controls) + 1}",
                tag=tag_name,
                text=text_value,
                title=title,
                aria_label=aria_label,
                class_tokens=class_tokens,
                match_index=match_index,
            )
        )
        if len(controls) >= MAX_ROUTING_CONTROLS:
            break
    return controls


def build_page_routing_context(
    *,
    title: str | None,
    page_text: str,
    links: list[PageRouteLink],
    controls: list[PageRouteControl] | None = None,
) -> str:
    link_lines = [f"- [{link.kind}] {link.label} -> {link.url}" for link in links]
    control_lines = [
        (
            f"- [control] {control.control_id} | tag={control.tag} | "
            f"text={control.text or '（空）'} | title={control.title or '（空）'} | "
            f"aria={control.aria_label or '（空）'} | "
            f"class={' '.join(control.class_tokens) or '（空）'}"
        )
        for control in (controls or [])
    ]
    return (
        f"页面标题：{title or ''}\n"
        f"页面可见文字摘要：\n{_head_tail(page_text or '', MAX_ROUTING_VISIBLE_TEXT_CHARS)}\n"
        "本页可选择链接（只能从这里选择）：\n"
        + ("\n".join(link_lines) if link_lines else "（无可选择链接）")
        + "\n本页可选择的无可直接访问 URL 的交互控件（只能按 ID 选择）：\n"
        + ("\n".join(control_lines) if control_lines else "（无可选择控件）")
    )


def build_entry_routing_prompt(
    *,
    university: str,
    school: str,
    source_url: str,
    routing_context: str,
) -> str:
    return (
        "你只负责入口选路，不提取人员，也不判断分页。\n"
        "目标是尽量完整地找到当前单位的学术人员名单，判断页面用途和链接之间的关系，不依赖固定栏目字眼。\n"
        "当前页没有直接列出多人时，只选择能直接到达目标名单的最少必要页面或承载主要名单的 iframe。\n"
        "当前页直接列出多人时，仍检查它是否只是总名单的一个明确部分：若附近列出同一单位的并列人员分类，且有各自名单链接，选择尚未覆盖的分类页面。\n"
        "分类可以基于人员类别或单位内部组织；选择显示不同人员的名单页，当前页已显示的部分不要重复。若有多套分类，只保留覆盖更完整、相互重叠更少的一套，不要把同一批人按另一种维度再抓一遍。\n"
        "分类的前后页、页码不是分类，分页由另一阶段处理。\n"
        "如果当前页已经是完整名单，或没有明确的互补名单，返回空数组。\n"
        "不要选择当前页、个人主页、首页或上级目录、下属单位主页、非人员栏目、按表彰或项目选出的少数人、登录区、文件或站外页面。\n"
        "同一学校主域下的兄弟子域只有在链接明确属于目标名单时才可选择。不要试探性扩散。\n"
        "只能逐字返回下方可选择链接中的 URL，不能改写或猜造 URL。\n"
        '只输出一个 JSON 对象，不要解释、Markdown 或代码块，格式为：{"discovered_urls":[]}。\n'
        f"学校：{university}\n"
        f"学院/单位：{school}\n"
        f"当前 URL：{source_url}\n"
        f"{routing_context}"
    )


def build_pagination_routing_prompt(
    *,
    university: str,
    school: str,
    source_url: str,
    routing_context: str,
) -> str:
    return (
        "你只判断当前页是否还有同一份人员名单的下一部分。\n"
        "最多选择一个能让同一名单恰好向后推进一页的链接；没有这种链接时，才选择一个作用相同的控件。"
        "不要选择第一页、最后一页、上一页、具体页码跳转、分类或筛选；没有就不扩展。URL 与控件不能同时选择。\n"
        '只输出 JSON：{"allow_expansion":false,"pagination_urls":[],"pagination_control_id":null}。\n'
        f"当前 URL：{source_url}\n"
        f"{routing_context}"
    )


def filter_model_selected_route_urls(
    selected_urls: list[str],
    *,
    links: list[PageRouteLink],
    source_url: str,
    start_url: str,
    max_results: int | None = None,
) -> list[str]:
    available_urls = {link.url for link in links}
    current_url = normalize_url(source_url)
    accepted: list[str] = []
    seen: set[str] = set()
    for selected_url in selected_urls:
        try:
            normalized = normalize_url(selected_url, base_url=source_url)
        except (TypeError, ValueError):
            continue
        if (
            normalized == current_url
            or normalized in seen
            or normalized not in available_urls
        ):
            continue
        if not is_safe_public_crawl_url(normalized) or not is_same_domain(
            normalized, start_url
        ):
            continue
        seen.add(normalized)
        accepted.append(normalized)
    return accepted if max_results is None else accepted[:max_results]


def select_model_pagination_control(
    selected_control_id: str | None,
    *,
    controls: list[PageRouteControl],
) -> PageRouteControl | None:
    normalized_id = (selected_control_id or "").strip()
    if not normalized_id:
        return None
    return next(
        (control for control in controls if control.control_id == normalized_id),
        None,
    )


async def _invoke_structured_routing_phase(
    llm_profile: LLMProfile,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    adaptation: LLMRuntimeAdaptation,
    phase: Literal["entry", "pagination"],
    prompt: str,
    result_model: type[EntryRoutingPayload] | type[PaginationRoutingPayload],
) -> tuple[
    EntryRoutingPayload | PaginationRoutingPayload,
    list[RoutingAttempt],
    LLMRuntimeAdaptation,
]:
    completion, payload, _structured_mode = await request_crawler_structured_completion(
        session_factory,
        llm_profile,
        adaptation,
        prompt=prompt,
        result_model=result_model,
    )
    usage = extract_token_usage_from_llm_response(completion)
    return (
        payload,
        [
            RoutingAttempt(
                phase=phase,
                attempt_number=1,
                raw_model_text=completion.content,
                raw_payload=payload.model_dump(),
                usage=usage,
            )
        ],
        adaptation,
    )


def _route_link_label(tag: Any, *, kind: Literal["link", "iframe"]) -> str:
    label = " ".join(tag.get_text(" ", strip=True).split())
    if not label:
        image = tag.find("img") if kind == "link" else None
        if image is not None:
            label = " ".join(str(image.get("alt") or "").split())
    if not label:
        label = " ".join(
            str(
                tag.get("title") or tag.get("aria-label") or tag.get("name") or ""
            ).split()
        )
    if not label:
        label = "嵌入页面" if kind == "iframe" else "无文字链接"
    return label[:240]


def _is_non_url_anchor_control(href: object) -> bool:
    raw_href = str(href or "").strip()
    if not raw_href:
        return True
    lowered = raw_href.lower()
    return lowered.startswith("javascript:") or raw_href.startswith("#")


def _has_interactive_control_ancestor(tag: Any) -> bool:
    parent = getattr(tag, "parent", None)
    while parent is not None and getattr(parent, "name", None) is not None:
        name = str(parent.name).lower()
        role = str(parent.get("role") or "").lower()
        if (
            name == "button"
            or role == "button"
            or (name == "li" and parent.has_attr("tabindex"))
        ):
            return True
        parent = getattr(parent, "parent", None)
    return False


def _normalized_class_tokens(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        tokens = [str(token).strip() for token in value]
    else:
        tokens = str(value or "").split()
    return tuple(token for token in tokens if token)[:12]


def _normalize_control_text(value: str) -> str:
    return " ".join(value.split())[:240]


def _head_tail(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    half = max_chars // 2
    return f"{value[:half]}\n……（中间省略）……\n{value[-half:]}"


def _accumulate_attempt_usage(
    accumulated: dict[str, int],
    attempts: list[RoutingAttempt],
) -> None:
    for attempt in attempts:
        if attempt.usage is None:
            continue
        accumulated["input_tokens"] += int(attempt.usage.get("input_tokens") or 0)
        accumulated["output_tokens"] += int(attempt.usage.get("output_tokens") or 0)
        accumulated["cached_tokens"] += int(attempt.usage.get("cached_tokens") or 0)
