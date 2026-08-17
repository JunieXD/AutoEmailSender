from __future__ import annotations

import json
import hashlib
import re
import ssl
from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import ceil
from time import perf_counter
from textwrap import dedent
from typing import TYPE_CHECKING, Literal, TypeVar
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import IdentityMaterial, IdentityProfile, LLMProfile, Professor
from app.modules.campaigns.public import build_template_context
from app.services.beautiful_soup import parse_html
from app.services.html_text import html_to_text
from app.modules.communications.public import text_to_html
from app.services.rich_text import (
    normalize_email_html,
    render_rich_text_document,
    text_to_email_html,
)
from app.services.template_draft_rewrite import (
    DraftRewriteProtectedToken,
    DraftRewriteSourceBlock,
    apply_draft_rewrite_replacements,
    build_draft_rewrite_document,
    render_draft_template_text,
)


DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_LLM_TEMPERATURE = 0.2
DEFAULT_LLM_MAX_TOKENS = 6000
STEPFUN_PROBE_MAX_TOKENS = 128
STRUCTURED_OUTPUT_CONTROL_KEY = "__structured_output_control__"

_STEPFUN_OPENAI_BASE_URLS = frozenset(
    {
        "https://api.stepfun.com/v1",
        "https://api.stepfun.com/step_plan/v1",
    }
)
SYSTEM_MATCH_ONLY_PROMPT = dedent(
    """
    你是研究生套磁助理。你必须只输出 JSON，不要输出任何解释、Markdown 代码块或多余文字。
    只做匹配分析，不要生成邮件草稿。

    JSON 字段必须包含：
    - match_score: 0-100 的整数
    - match_reason: 简洁中文说明
    - fit_points: 字符串数组
    - risk_points: 字符串数组
    - keywords: 字符串数组

    输出示例：
    {
      "match_score": 84,
      "match_reason": "导师方向与默认材料中的研究经历较匹配。",
      "fit_points": ["研究问题接近", "背景技能可迁移"],
      "risk_points": ["材料里缺少该导师近期方向的直接成果"],
      "keywords": ["多模态", "信息抽取"]
    }

    评分量表：
    match_score 总分为 100 分，由以下 4 个维度组成。你必须先按维度判断，再给出总分。

    1. 研究主题匹配度：0-45
       衡量默认材料与导师研究方向是否在研究问题、应用场景或领域上有交集。
       - 40-45：具体研究问题高度重合。
       - 30-39：同一方向，有明确交集。
       - 15-29：宽泛领域相关，但具体问题不同。
       - 1-14：只有弱相关背景。
       - 0：看不到相关性。

    2. 能力与方法匹配度：0-25
       衡量默认材料中的技能、方法、项目、论文或工具是否能支撑导师方向。
       - 21-25：能力可以直接支撑导师方向。
       - 13-20：有部分可迁移能力。
       - 5-12：只有基础背景或泛化能力。
       - 0：看不到支撑能力。

    3. 近期论文交集：0-20
       衡量导师近期论文与默认材料是否存在可引用、可展开的具体交集。
       - 16-20：近期论文主题与默认材料中的研究、项目或技能高度相关，可直接写入套磁理由。
       - 9-15：近期论文与默认材料有明确但不完全直接的交集。
       - 1-8：有近期论文，但与默认材料只有弱相关或泛化关联。
       - 0：没有近期论文，或近期论文与默认材料看不到有效交集。

    4. 个性化理由充分度：0-10
       衡量能否写出具体、可信、不空泛的套磁理由。
       - 8-10：能基于导师方向或论文提炼出具体匹配点。
       - 4-7：能写出合理但不够具体的理由。
       - 1-3：只能泛泛表达兴趣。
       - 0：无法形成可信理由。

    用户意向研究方向评分原则：
    - 如果用户意向研究方向非空，并且导师研究方向或近期论文与该意向方向明确相似，可以作为加分信号提高 match_score。
    - 加分应体现在研究主题匹配度和个性化理由充分度中，并在 match_reason 或 fit_points 中说明相似点。
    - 用户意向研究方向不能替代默认材料中的证据；如果默认材料完全缺少支撑，仍需遵守材料证据不足的上限规则。
    - 用户意向研究方向为空或与导师方向不相似时，不要因为该项额外扣分。

    近期论文评分原则：
    - 有近期论文，且论文主题和默认材料有明确交集：应明显高于只有宽泛研究方向的导师。
    - 有近期论文，但论文和默认材料交集弱：不因论文数量多而加分。
    - 没有近期论文但研究方向具体：match_score 通常最高 80；只有在研究方向非常具体且默认材料高度重合时才可略高于 80，并必须说明理由。

    上限规则：
    - 没有近期论文，但研究方向具体：通常最高 80。
    - 没有近期论文，且研究方向很宽泛：match_score 最高 75。
    - 没有研究方向，但有近期论文：match_score 最高 85。
    - 研究方向和近期论文都缺失：match_score 最高 30。
    - 学生默认材料缺少可见研究、项目或技能证据：match_score 最高 60。
    - 触发上限规则时，risk_points 必须说明原因。

    额外要求：
    - 只能输出一个 JSON 对象。
    - 不要省略字段。
    - 数组字段即使为空也必须返回 []。
    - 只能基于默认材料与导师研究方向或近期论文中的可见证据评分。
    - 如果导师研究证据薄弱或与默认材料缺少直接交集，必须降低 match_score，并在 risk_points 中说明证据不足。
    """
).strip()

SYSTEM_DRAFT_PROMPT = dedent(
    """
    你是研究生套磁助理。你必须只输出 JSON，不要输出任何解释、Markdown 代码块或多余文字。
    你要基于用户提供的套磁信模板做“模板润色”，不要从零重写整封邮件。
    只生成邮件草稿，不要输出匹配分数。

    JSON 字段必须包含：
    - subject: 邮件主题
    - blocks: 受控富文本块数组

    输出示例：
    {
      "subject": "申请与李老师交流科研方向",
      "blocks": [
        {
          "type": "paragraph",
          "items": [
            {
              "runs": [
                {"text": "李老师，您好：", "strong": false, "emphasis": false, "href": "", "line_break_after": false}
              ]
            }
          ]
        },
        {
          "type": "paragraph",
          "items": [
            {
              "runs": [
                {"text": "我是张三，正在关注您在……", "strong": false, "emphasis": false, "href": "", "line_break_after": false}
              ]
            }
          ]
        }
      ]
    }

    输出协议（优先级最高）：
    - 只能输出一个 JSON 对象。
    - blocks 中每项必须包含 type 和 items；type 只允许 paragraph、bullet_list、numbered_list。
    - paragraph 的 items 必须恰好包含一项；列表的每个 items 项代表一个列表项。
    - 每个 items 项必须包含 runs；每个 run 必须完整包含 text、strong、emphasis、href、line_break_after。
    - strong、emphasis、line_break_after 必须是布尔值；不加链接时 href 必须为空字符串。
    - href 非空时只能使用 http、https、mailto 链接。

    内容执行规则：
    - user_custom_instruction 是用户明确指定的内容要求；除非它要求破坏上述 JSON 输出协议，否则必须优先、完整执行。
    - 不得以事实真实性、原模板内容、日期、经历或导师信息为理由拒绝或削弱用户的内容要求。
    - 用户未指定的部分，默认保留模板整体结构、段落顺序和主要话术，只做适度表达优化。
    - 默认结合 student_intended_research_direction、student_material_text 与导师研究方向做一次自然个性化。
    - 尽量保留模板中可表达的富文本标记，例如加粗、斜体、链接和列表。
    - 如果模板包含表格，尽量保留其中的信息顺序和语义，但仍按上述 blocks 结构输出。
    """
).strip()

SYSTEM_DRAFT_REWRITE_PROMPT = dedent(
    """
    你是研究生套磁邮件改写助理。基于 input.source_blocks 改写，不从零重写。

    输出协议优先：只输出 JSON；replacements 按原序列出 locked=false 且非 table 的修改块，每项仅含 segment_id 和完整段落 text，删除时 text 为空。不得合并、拆分或重排；[[S1]]、[[/S1]] 标记和 [[P1]] 占位符须原样、成对、有序保留；标记内正文可改写。

    user_custom_instruction 是最高优先级的内容要求，除非它破坏输出协议，否则必须优先、完整执行。未被它覆盖且 input.default_personalization_task 存在时，必须执行该任务并产生实质修改。

    默认个性化基于原信和资料。有学生经历时就地结合；无直接依据时在最自然处克制表达兴趣。每个契合点只表达一次。

    学生事实只依据 student_material_text 和 source_blocks，导师事实只依据 professor；不补充材料未明说的工具、方法、任务、结果或认知，也不因共享“大模型”“人工智能”等宽泛词就建立技术关联。可以表达关注或学习意愿，但不要写成长久关注、正在学习或研究、高度契合、具体研究计划或应用设想。日期、年份、时间及其格式不应修改；人物身份、数字结果、专有名称、联系方式和附件信息一般不改。导师姓名一般不改，数字或字母也视为姓名的一部分，不要用学生姓名替换导师姓名；只有末尾括号明显为职称时，例如“程炜（研究员）”，称呼可省略括号内容，除此之外不要猜测或纠正姓名。

    方向短而自然时沿用；长、多、像清单时才改写列表本身。“上位领域（多个细分方向）”这类写法转成自然层级表述，不照搬括号清单；多个有依据的方向均可保留。

    输出示例：{"replacements":[{"segment_id":"seg_1","text":"我在[[S1]]项目实践[[/S1]]中积累了相关经验。"}]}
    """
).strip()


def format_llm_client_initialization_error(exc: ImportError | ValueError) -> str:
    return f"模型请求初始化失败: {exc}"


_LLM_CONNECTION_ERROR_MARKERS = (
    "all connection attempts failed",
    "bad record mac",
    "connecterror",
    "connect error",
    "connection refused",
    "connection reset",
    "failed to establish a new connection",
    "name or service not known",
    "temporary failure in name resolution",
    "nodename nor servname",
    "getaddrinfo failed",
    "network is unreachable",
    "no route to host",
    "ssl/tls alert",
    "sslv3_alert_bad_record_mac",
)

_LLM_TLS_ERROR_MARKERS = (
    "bad record mac",
    "ssl/tls alert",
    "sslv3_alert_bad_record_mac",
)

_LLM_TLS_CONNECTION_ERROR_MESSAGE = "模型服务 TLS 连接失败，请检查系统代理、网络或稍后重试。"
_LLM_RUNTIME_LOG_NAME = "llm-runtime.log"
_LOG_URL_PATTERN = re.compile(r"https?://[^\s'\"<>]+")


def format_llm_runtime_error_for_user(message_or_exc: object) -> str:
    message = str(message_or_exc).strip()
    if not message:
        return "模型请求失败"
    if message.rstrip(":").strip() in {"模型请求失败", "获取模型列表失败"}:
        return message.rstrip(":").strip()
    if "模型服务连接失败" in message:
        return message

    haystack_parts = [message]
    haystack_parts.append(type(message_or_exc).__name__)
    cause = getattr(message_or_exc, "__cause__", None)
    if cause is not None:
        haystack_parts.append(type(cause).__name__)
        haystack_parts.append(str(cause))
    haystack = " ".join(haystack_parts).lower()
    if any(marker in haystack for marker in _LLM_TLS_ERROR_MARKERS):
        return _LLM_TLS_CONNECTION_ERROR_MESSAGE
    if any(marker in haystack for marker in _LLM_CONNECTION_ERROR_MARKERS):
        return "模型服务连接失败，请检查系统代理或网络后重试。"

    return message


def _append_llm_runtime_log(entry: str) -> None:
    try:
        log_dir = get_settings().data_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / _LLM_RUNTIME_LOG_NAME).open("a", encoding="utf-8", newline="\n") as file:
            file.write(entry)
    except Exception:
        return


def _exception_chain_details(exc: BaseException) -> list[dict[str, str]]:
    details: list[dict[str, str]] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        details.append(
            {
                "type": type(current).__name__,
                "message": _sanitize_log_text(str(current)),
                "repr": _sanitize_log_text(repr(current)),
            },
        )
        current = current.__cause__ or current.__context__
    return details


def _sanitize_log_text(value: str) -> str:
    def replace_url(match: re.Match[str]) -> str:
        url = match.group(0)
        trailing = ""
        while url and url[-1] in ".,);]":
            trailing = f"{url[-1]}{trailing}"
            url = url[:-1]
        return f"{_strip_url_query_and_fragment(url) or url}{trailing}"

    return _LOG_URL_PATTERN.sub(replace_url, value)


def _is_tls_bad_record_mac_error(exc: BaseException) -> bool:
    haystack = " ".join(
        part
        for detail in _exception_chain_details(exc)
        for part in (detail["type"], detail["message"], detail["repr"])
    ).lower()
    return any(marker in haystack for marker in _LLM_TLS_ERROR_MARKERS)


def _strip_url_query_and_fragment(url: str | None) -> str | None:
    if url is None:
        return None
    parsed = urlsplit(url)
    hostname = parsed.hostname
    if hostname is None:
        netloc = parsed.netloc.rsplit("@", 1)[-1]
    else:
        netloc = f"[{hostname}]" if ":" in hostname else hostname
        try:
            port = parsed.port
        except ValueError:
            port = None
        if port is not None:
            netloc = f"{netloc}:{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _log_llm_http_exception(
    *,
    profile: LLMProfile,
    request_url: str,
    endpoint_kind: str,
    tls_mode: str,
    exc: BaseException,
    will_retry: bool,
    retry_reason: str | None = None,
) -> None:
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "event": "llm_http_request_failed",
        "provider": profile.provider,
        "model_name": profile.model_name,
        "api_base_url": _strip_url_query_and_fragment(resolve_base_url(profile.api_base_url)),
        "request_url": _strip_url_query_and_fragment(request_url),
        "endpoint_kind": endpoint_kind,
        "tls_mode": tls_mode,
        "will_retry": will_retry,
        "retry_reason": retry_reason,
        "error_chain": _exception_chain_details(exc),
    }
    _append_llm_runtime_log(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def _endpoint_protocol_switch_reason(error: "LLMEndpointProtocolError") -> str | int:
    if error.response_envelope is not None:
        return error.response_envelope
    if error.status_code is not None:
        return error.status_code
    return "protocol_error"


async def _record_endpoint_protocol_switch(
    session: "AsyncSession",
    *,
    profile: LLMProfile,
    protocol_error: "LLMEndpointProtocolError",
    completion: "ChatCompletionResult",
) -> None:
    reason = _endpoint_protocol_switch_reason(protocol_error)
    attempted_urls = [
        sanitized
        for url in completion.attempted_urls
        if (sanitized := _strip_url_query_and_fragment(url)) is not None
    ]
    metadata = {
        "old_endpoint_kind": protocol_error.failed_endpoint_kind,
        "new_endpoint_kind": completion.endpoint_kind,
        "reason": reason,
        "retried": True,
        "endpoint_kind": completion.endpoint_kind,
        "request_url": _strip_url_query_and_fragment(completion.request_url),
        "attempted_urls": attempted_urls,
    }
    _append_llm_runtime_log(
        json.dumps(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "event": "llm_endpoint_protocol_switched",
                "provider": profile.provider,
                "model_name": profile.model_name,
                "api_base_url": _strip_url_query_and_fragment(resolve_base_url(profile.api_base_url)),
                **metadata,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
    )

    from app.services.operation_logs import record_operation_log

    try:
        async with session.begin_nested():
            await record_operation_log(
                session,
                category="llm",
                event_name="llm.endpoint_protocol_switched",
                entity_type="llm_profile",
                entity_id=str(profile.id) if profile.id is not None else None,
                metadata=metadata,
            )
    except Exception:
        return


def _build_tls12_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    return context


def _should_retry_with_tls12(profile: LLMProfile, exc: BaseException, tls_mode: str) -> bool:
    return tls_mode != "tls12" and is_deepseek_profile(profile) and _is_tls_bad_record_mac_error(exc)


async def _send_llm_http_request(
    *,
    method: str,
    profile: LLMProfile,
    url: str,
    endpoint_kind: str,
    headers: dict[str, str],
    timeout: httpx.Timeout,
    json_body: dict[str, object] | None = None,
) -> httpx.Response:
    tls_context: ssl.SSLContext | None = None
    while True:
        tls_mode = "tls12" if tls_context is not None else "default"
        client_kwargs: dict[str, object] = {"timeout": timeout}
        if tls_context is not None:
            client_kwargs["verify"] = tls_context
        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                if method == "GET":
                    return await client.get(url, headers=headers)
                if method == "POST":
                    return await client.post(url, headers=headers, json=json_body)
                raise ValueError(f"Unsupported LLM HTTP method: {method}")
        except httpx.TimeoutException:
            raise
        except (httpx.HTTPError, ssl.SSLError) as exc:
            will_retry = _should_retry_with_tls12(profile, exc, tls_mode)
            retry_reason = "tls12_retry" if will_retry else None
            _log_llm_http_exception(
                profile=profile,
                request_url=url,
                endpoint_kind=endpoint_kind,
                tls_mode=tls_mode,
                exc=exc,
                will_retry=will_retry,
                retry_reason=retry_reason,
            )
            if will_retry:
                tls_context = _build_tls12_context()
                continue
            raise


class LLMRuntimeError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        request_url: str | None = None,
        attempted_urls: list[str] | None = None,
        endpoint_kind: str | None = None,
        status_code: int | None = None,
        duration_ms: int | None = None,
        usage: object | None = None,
        raw_content: str | None = None,
    ) -> None:
        super().__init__(message)
        self.request_url = request_url
        self.attempted_urls = attempted_urls or ([request_url] if request_url else [])
        self.endpoint_kind = endpoint_kind
        self.status_code = status_code
        self.duration_ms = duration_ms
        self.usage = usage
        self.raw_content = raw_content


class LLMEndpointProtocolError(LLMRuntimeError):
    def __init__(
        self,
        message: str,
        *,
        failed_endpoint_kind: Literal["chat_completions", "responses"],
        response_envelope: Literal["other_endpoint", "invalid"] | None,
        request_url: str | None = None,
        attempted_urls: list[str] | None = None,
        status_code: int | None = None,
        duration_ms: int | None = None,
    ) -> None:
        super().__init__(
            message,
            request_url=request_url,
            attempted_urls=attempted_urls,
            endpoint_kind=failed_endpoint_kind,
            status_code=status_code,
            duration_ms=duration_ms,
        )
        self.failed_endpoint_kind = failed_endpoint_kind
        self.response_envelope = response_envelope


@dataclass(slots=True)
class ChatCompletionUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None


@dataclass(slots=True)
class ChatCompletionResult:
    content: str
    usage: ChatCompletionUsage | None = None
    request_url: str | None = None
    attempted_urls: list[str] = field(default_factory=list)
    endpoint_kind: str | None = None
    status_code: int | None = None
    duration_ms: int | None = None


@dataclass(frozen=True, slots=True)
class LLMRuntimeAdaptation:
    """The endpoint protocol and thinking override learned for one model."""

    endpoint_kind: Literal["chat_completions", "responses"]
    thinking_extra_body: dict[str, object] | None
    endpoint_attempted_urls: tuple[str, ...] = field(default_factory=tuple, compare=False)


@dataclass(slots=True)
class DraftTokenEstimate:
    estimated_prompt_tokens: int
    estimated_completion_tokens_upper_bound: int
    estimated_total_tokens_upper_bound: int


@dataclass(slots=True)
class MatchPromptParts:
    prompt: str
    stable_prefix: str
    prompt_hash: str
    stable_prefix_hash: str
    prompt_cache_key: str | None = None


@dataclass(slots=True)
class DraftRewritePromptParts:
    prompt: str
    stable_prefix: str
    prompt_hash: str
    stable_prefix_hash: str
    prompt_cache_key: str | None = None


class MatchEvaluationResult(BaseModel):
    match_score: int = Field(ge=0, le=100)
    match_reason: str
    fit_points: list[str] = Field(default_factory=list)
    risk_points: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class MatchEvaluationWireResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    match_score: int = Field(strict=True)
    match_reason: str
    fit_points: list[str]
    risk_points: list[str]
    keywords: list[str]


class DraftGenerationResult(BaseModel):
    subject: str
    body_text: str | None = None
    body_html: str | None = None
    rich_body: dict[str, object] | None = None


class DraftBodyRunWire(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    strong: bool
    emphasis: bool
    href: str
    line_break_after: bool


class DraftBodyItemWire(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runs: list[DraftBodyRunWire]


class DraftBodyBlockWire(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["paragraph", "bullet_list", "numbered_list"]
    items: list[DraftBodyItemWire]


class DraftGenerationWireResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str
    blocks: list[DraftBodyBlockWire]


class DraftRewriteSegmentReplacement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_id: str
    text: str


class DraftRewriteResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    replacements: list[DraftRewriteSegmentReplacement]


@dataclass(slots=True)
class DraftRewritePreferences:
    draft_rewrite_intensity: str = "moderate"
    draft_rewrite_tone: str = "polite"
    draft_rewrite_formality: str = "balanced"
    draft_rewrite_length: str = "default"
    draft_rewrite_specificity: str = "balanced"
    draft_template_preservation: str = "structure_first"
    draft_custom_instruction: str = ""
    intended_research_direction: str = ""


class LLMProbeResult(BaseModel):
    ok: bool
    message: str
    resolved_base_url: str | None = None
    request_url: str | None = None
    attempted_urls: list[str] = Field(default_factory=list)
    endpoint_kind: str | None = None
    status_code: int | None = None
    duration_ms: int | None = None
    consumes_tokens: bool = True
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    response_preview: str | None = None


class LLMModelCatalogResult(BaseModel):
    ok: bool
    message: str
    resolved_base_url: str | None = None
    request_url: str | None = None
    attempted_urls: list[str] = Field(default_factory=list)
    endpoint_kind: str | None = None
    status_code: int | None = None
    duration_ms: int | None = None
    consumes_tokens: bool = False
    models: list[str] = Field(default_factory=list)
    selected_model_available: bool | None = None


@dataclass(slots=True)
class GeneratedMatchEvaluation:
    result: MatchEvaluationResult
    usage: ChatCompletionUsage | None = None
    request_url: str | None = None
    attempted_urls: list[str] = field(default_factory=list)
    endpoint_kind: str | None = None
    status_code: int | None = None
    duration_ms: int | None = None
    prompt_hash: str | None = None
    stable_prefix_hash: str | None = None
    prompt_cache_key: str | None = None


@dataclass(slots=True)
class GeneratedDraftContent:
    result: DraftGenerationResult
    usage: ChatCompletionUsage | None = None
    prompt_hash: str | None = None
    stable_prefix_hash: str | None = None
    prompt_cache_key: str | None = None


StructuredResultT = TypeVar("StructuredResultT", bound=BaseModel)


async def _legacy_probe_llm_profile(profile: LLMProfile) -> LLMProbeResult:
    base_url = resolve_base_url(profile.api_base_url)
    try:
        payload = {
            "model": profile.model_name,
            "messages": [
                {
                    "role": "user",
                    "content": "只回复 OK",
                },
            ],
            "temperature": 0,
            "max_tokens": min(profile.max_tokens or DEFAULT_LLM_MAX_TOKENS, 8),
        }
        if is_deepseek_profile(profile):
            payload["thinking"] = {"type": "disabled"}
        completion = await request_chat_completion(profile, payload)
    except LLMRuntimeError as exc:
        return LLMProbeResult(
            ok=False,
            message=str(exc),
            resolved_base_url=base_url,
            request_url=exc.request_url,
            attempted_urls=exc.attempted_urls,
            endpoint_kind=exc.endpoint_kind,
            response_preview=None,
        )

    preview = completion.content.strip().replace("\n", " ")[:200]
    return LLMProbeResult(
        ok=True,
        message="模型连通性测试成功",
        resolved_base_url=base_url,
        request_url=completion.request_url,
        attempted_urls=[completion.request_url] if completion.request_url else [],
        endpoint_kind=completion.endpoint_kind,
        response_preview=preview or None,
    )


async def generate_match_evaluation(
    *,
    identity: IdentityProfile,
    primary_material: IdentityMaterial | None,
    llm_profile: LLMProfile,
    professor: Professor,
    available_materials: list[IdentityMaterial],
    intended_research_direction: str | None = None,
    thinking_extra_body: dict[str, object] | None = None,
    session: "AsyncSession | None" = None,
    adaptation: LLMRuntimeAdaptation | None = None,
) -> GeneratedMatchEvaluation:
    prompt_parts = build_match_prompt_parts(
        identity=identity,
        primary_material=primary_material,
        professor=professor,
        available_materials=available_materials,
        intended_research_direction=intended_research_direction,
        llm_profile=llm_profile,
    )
    payload: dict[str, object] = {
        "model": llm_profile.model_name,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_MATCH_ONLY_PROMPT,
            },
            {
                "role": "user",
                "content": prompt_parts.prompt,
            },
        ],
        "temperature": 0,
        "max_tokens": llm_profile.max_tokens or DEFAULT_LLM_MAX_TOKENS,
    }
    if prompt_parts.prompt_cache_key:
        payload["prompt_cache_key"] = prompt_parts.prompt_cache_key

    completion, wire_result, _structured_mode = await request_structured_completion(
        llm_profile,
        payload,
        MatchEvaluationWireResult,
        extra_body=thinking_extra_body,
        session=session,
        adaptation=adaptation,
    )
    result = MatchEvaluationResult.model_validate(wire_result.model_dump())
    return GeneratedMatchEvaluation(
        result=result,
        usage=completion.usage,
        request_url=completion.request_url,
        attempted_urls=completion.attempted_urls,
        endpoint_kind=completion.endpoint_kind,
        status_code=completion.status_code,
        duration_ms=completion.duration_ms,
        prompt_hash=prompt_parts.prompt_hash,
        stable_prefix_hash=prompt_parts.stable_prefix_hash,
        prompt_cache_key=prompt_parts.prompt_cache_key,
    )


async def generate_draft_content(
    *,
    identity: IdentityProfile,
    primary_material: IdentityMaterial | None,
    llm_profile: LLMProfile,
    professor: Professor,
    available_materials: list[IdentityMaterial],
    custom_subject: str | None = None,
    custom_body: str | None = None,
    custom_body_html: str | None = None,
    current_match: MatchEvaluationResult | None = None,
    max_tokens: int | None = None,
    rewrite_preferences: DraftRewritePreferences | None = None,
    thinking_extra_body: dict[str, object] | None = None,
    session: "AsyncSession | None" = None,
    adaptation: LLMRuntimeAdaptation | None = None,
) -> GeneratedDraftContent:
    template_html = custom_body_html
    if not template_html and custom_body:
        template_html = text_to_email_html(custom_body).html

    if template_html:
        template_context = build_template_context(identity, professor)
        rewrite_document = build_draft_rewrite_document(template_html, template_context)
        rendered_subject = render_draft_template_text(custom_subject, template_context).strip()
        editable_blocks = [
            block
            for block in rewrite_document.blocks
            if block.type != "table" and not block.locked
        ]
        if not editable_blocks:
            rendered = apply_draft_rewrite_replacements(rewrite_document, [])
            return GeneratedDraftContent(
                result=DraftGenerationResult(
                    subject=rendered_subject,
                    body_text=rendered.text,
                    body_html=rendered.html,
                ),
            )
        prompt_parts = build_draft_rewrite_prompt_parts(
            identity=identity,
            primary_material=primary_material,
            professor=professor,
            available_materials=available_materials,
            subject_template=custom_subject,
            source_blocks=rewrite_document.blocks,
            current_match=current_match,
            rewrite_preferences=rewrite_preferences,
            llm_profile=llm_profile,
            protected_tokens=rewrite_document.protected_tokens,
        )
        payload: dict[str, object] = {
            "model": llm_profile.model_name,
            "messages": [
                {
                    "role": "system",
                    "content": SYSTEM_DRAFT_REWRITE_PROMPT,
                },
                {
                    "role": "user",
                    "content": prompt_parts.prompt,
                },
            ],
            "temperature": llm_profile.temperature if llm_profile.temperature is not None else DEFAULT_LLM_TEMPERATURE,
            "max_tokens": max_tokens or DEFAULT_LLM_MAX_TOKENS,
        }
        if prompt_parts.prompt_cache_key is not None:
            payload["prompt_cache_key"] = prompt_parts.prompt_cache_key
        completion, rewrite_result, _structured_mode = await request_structured_completion(
            llm_profile,
            payload,
            DraftRewriteResult,
            extra_body=thinking_extra_body,
            session=session,
            adaptation=adaptation,
        )
        replacements = [item.model_dump() for item in rewrite_result.replacements]
        try:
            rendered = apply_draft_rewrite_replacements(
                rewrite_document,
                replacements,
            )
        except ValueError as exc:
            raise LLMRuntimeError(
                str(exc),
                request_url=completion.request_url,
                attempted_urls=completion.attempted_urls,
                endpoint_kind=completion.endpoint_kind,
                status_code=completion.status_code,
                duration_ms=completion.duration_ms,
                usage=completion.usage,
                raw_content=completion.content,
            ) from exc
        return GeneratedDraftContent(
            result=DraftGenerationResult(
                subject=rendered_subject,
                body_text=rendered.text,
                body_html=rendered.html,
            ),
            usage=completion.usage,
            prompt_hash=prompt_parts.prompt_hash,
            stable_prefix_hash=prompt_parts.stable_prefix_hash,
            prompt_cache_key=prompt_parts.prompt_cache_key,
        )

    prompt = build_draft_prompt(
        identity=identity,
        primary_material=primary_material,
        professor=professor,
        available_materials=available_materials,
        custom_subject=custom_subject,
        custom_body=custom_body,
        custom_body_html=custom_body_html,
        current_match=current_match,
        rewrite_preferences=rewrite_preferences,
    )
    completion, wire_result, _structured_mode = await request_structured_completion(
        llm_profile,
        {
            "model": llm_profile.model_name,
            "messages": [
                {
                    "role": "system",
                    "content": SYSTEM_DRAFT_PROMPT,
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            "temperature": llm_profile.temperature if llm_profile.temperature is not None else DEFAULT_LLM_TEMPERATURE,
            "max_tokens": max_tokens or DEFAULT_LLM_MAX_TOKENS,
        },
        DraftGenerationWireResult,
        extra_body=thinking_extra_body,
        session=session,
        adaptation=adaptation,
    )
    result = _draft_generation_wire_to_result(wire_result)
    return GeneratedDraftContent(result=result, usage=completion.usage)

def estimate_draft_content_tokens(
    *,
    identity: IdentityProfile,
    primary_material: IdentityMaterial | None,
    llm_profile: LLMProfile,
    professor: Professor,
    available_materials: list[IdentityMaterial],
    custom_subject: str | None = None,
    custom_body: str | None = None,
    custom_body_html: str | None = None,
    current_match: MatchEvaluationResult | None = None,
    rewrite_preferences: DraftRewritePreferences | None = None,
    max_tokens: int | None = None,
) -> DraftTokenEstimate:
    template_html = custom_body_html
    if not template_html and custom_body:
        template_html = text_to_email_html(custom_body).html

    if template_html:
        template_context = build_template_context(identity, professor)
        rewrite_document = build_draft_rewrite_document(template_html, template_context)
        if not any(
            block.type != "table" and not block.locked
            for block in rewrite_document.blocks
        ):
            return DraftTokenEstimate(
                estimated_prompt_tokens=0,
                estimated_completion_tokens_upper_bound=0,
                estimated_total_tokens_upper_bound=0,
            )
        prompt_parts = build_draft_rewrite_prompt_parts(
            identity=identity,
            primary_material=primary_material,
            professor=professor,
            available_materials=available_materials,
            subject_template=custom_subject,
            source_blocks=rewrite_document.blocks,
            current_match=current_match,
            rewrite_preferences=rewrite_preferences,
            llm_profile=llm_profile,
            protected_tokens=rewrite_document.protected_tokens,
        )
        prompt_text = f"{SYSTEM_DRAFT_REWRITE_PROMPT}\n\n{prompt_parts.prompt}"
    else:
        prompt = build_draft_prompt(
            identity=identity,
            primary_material=primary_material,
            professor=professor,
            available_materials=available_materials,
            custom_subject=custom_subject,
            custom_body=custom_body,
            custom_body_html=custom_body_html,
            current_match=current_match,
            rewrite_preferences=rewrite_preferences,
        )
        prompt_text = f"{SYSTEM_DRAFT_PROMPT}\n\n{prompt}"

    completion_cap = max_tokens or llm_profile.max_tokens or DEFAULT_LLM_MAX_TOKENS
    estimated_prompt_tokens = estimate_text_tokens(prompt_text)
    estimated_total_tokens_upper_bound = estimated_prompt_tokens + completion_cap
    return DraftTokenEstimate(
        estimated_prompt_tokens=estimated_prompt_tokens,
        estimated_completion_tokens_upper_bound=completion_cap,
        estimated_total_tokens_upper_bound=estimated_total_tokens_upper_bound,
    )


async def probe_llm_profile(
    profile: LLMProfile,
    *,
    session: "AsyncSession | None" = None,
    thinking_extra_body: dict[str, object] | None = None,
    adaptation: LLMRuntimeAdaptation | None = None,
) -> LLMProbeResult:
    """Test that the model is reachable. Single-turn ping only.

    Session-owning callers provide a pre-resolved ``adaptation`` so endpoint
    protocol cache misses can be learned and committed with the probe result.
    """

    base_url = resolve_base_url(profile.api_base_url)
    requires_final_text = is_stepfun_profile(profile)
    payload = {
        "model": profile.model_name,
        "messages": [
            {
                "role": "user",
                "content": "只回复 OK",
            },
        ],
        "temperature": 0,
        "max_tokens": probe_max_tokens_for_profile(profile, fallback=8),
    }

    try:
        completion = await request_chat_completion(
            profile,
            payload,
            extra_body=thinking_extra_body,
            allow_empty_content=not requires_final_text,
            session=session,
            adaptation=adaptation,
        )
    except LLMRuntimeError as exc:
        return LLMProbeResult(
            ok=False,
            message=str(exc),
            resolved_base_url=base_url,
            request_url=exc.request_url,
            attempted_urls=exc.attempted_urls,
            endpoint_kind=exc.endpoint_kind,
            status_code=exc.status_code,
            duration_ms=exc.duration_ms,
            consumes_tokens=True,
            response_preview=None,
        )

    preview = (completion.content or "").strip().replace("\n", " ")[:200]
    return LLMProbeResult(
        ok=True,
        message="模型可用性测试成功",
        resolved_base_url=base_url,
        request_url=completion.request_url,
        attempted_urls=completion.attempted_urls,
        endpoint_kind=completion.endpoint_kind,
        status_code=completion.status_code,
        duration_ms=completion.duration_ms,
        consumes_tokens=True,
        prompt_tokens=completion.usage.prompt_tokens if completion.usage else None,
        completion_tokens=completion.usage.completion_tokens if completion.usage else None,
        total_tokens=completion.usage.total_tokens if completion.usage else None,
        response_preview=preview or None,
    )


async def fetch_llm_profile_models(profile: LLMProfile) -> LLMModelCatalogResult:
    base_url = resolve_base_url(profile.api_base_url)
    timeout_seconds = get_settings().llm_request_timeout_seconds
    timeout = httpx.Timeout(timeout_seconds)
    headers = {
        "Authorization": f"Bearer {profile.api_key}",
        "Content-Type": "application/json",
        "Connection": "close",
    }
    url = build_endpoint_url(base_url, "models")
    start = perf_counter()

    try:
        response = await _send_llm_http_request(
            method="GET",
            profile=profile,
            url=url,
            endpoint_kind="models",
            headers=headers,
            timeout=timeout,
        )
    except (ImportError, ValueError) as exc:
        return LLMModelCatalogResult(
            ok=False,
            message=format_llm_client_initialization_error(exc),
            resolved_base_url=base_url,
            request_url=url,
            attempted_urls=[url],
            endpoint_kind="models",
            duration_ms=compute_duration_ms(start),
            consumes_tokens=False,
        )
    except httpx.TimeoutException:
        return LLMModelCatalogResult(
            ok=False,
            message=f"获取模型列表超时（{timeout_seconds} 秒）",
            resolved_base_url=base_url,
            request_url=url,
            attempted_urls=[url],
            endpoint_kind="models",
            duration_ms=compute_duration_ms(start),
            consumes_tokens=False,
        )
    except (httpx.HTTPError, ssl.SSLError) as exc:
        return LLMModelCatalogResult(
            ok=False,
            message=format_llm_runtime_error_for_user(f"获取模型列表失败: {exc}"),
            resolved_base_url=base_url,
            request_url=url,
            attempted_urls=[url],
            endpoint_kind="models",
            duration_ms=compute_duration_ms(start),
            consumes_tokens=False,
        )

    duration_ms = compute_duration_ms(start)
    if response.status_code >= 400:
        return LLMModelCatalogResult(
            ok=False,
            message=format_http_error(response.status_code, response.text, url),
            resolved_base_url=base_url,
            request_url=url,
            attempted_urls=[url],
            endpoint_kind="models",
            status_code=response.status_code,
            duration_ms=duration_ms,
            consumes_tokens=False,
        )

    try:
        data = response.json()
        models = extract_model_ids(data)
    except (TypeError, ValueError) as exc:
        return LLMModelCatalogResult(
            ok=False,
            message=f"模型列表返回格式无法解析: {exc}",
            resolved_base_url=base_url,
            request_url=url,
            attempted_urls=[url],
            endpoint_kind="models",
            status_code=response.status_code,
            duration_ms=duration_ms,
            consumes_tokens=False,
        )

    selected_model_available = profile.model_name in models if profile.model_name else None
    message = f"已获取 {len(models)} 个模型"
    if profile.model_name:
        if selected_model_available:
            message = f"{message}，当前模型已在列表中"
        else:
            message = f"{message}，但当前模型不在列表中"

    return LLMModelCatalogResult(
        ok=True,
        message=message,
        resolved_base_url=base_url,
        request_url=url,
        attempted_urls=[url],
        endpoint_kind="models",
        status_code=response.status_code,
        duration_ms=duration_ms,
        consumes_tokens=False,
        models=models,
        selected_model_available=selected_model_available,
    )


async def _request_completion_endpoint(
    profile: LLMProfile,
    payload: dict[str, object],
    *,
    endpoint_kind: Literal["chat_completions", "responses"],
    extra_body: dict[str, object] | None = None,
    allow_empty_content: bool = False,
) -> ChatCompletionResult:
    from .adaptation.thinking import merge_extra_body

    chat_payload = merge_extra_body(payload, extra_body)
    base_url = resolve_base_url(profile.api_base_url)
    if endpoint_kind == "chat_completions":
        url = build_endpoint_url(base_url, "chat/completions")
        request_body = build_chat_completions_payload(chat_payload)
        content_extractor = extract_chat_completion_content
    else:
        url = build_endpoint_url(base_url, "responses")
        request_body = build_responses_payload(chat_payload)
        content_extractor = extract_responses_content

    timeout_seconds = get_settings().llm_request_timeout_seconds
    timeout = httpx.Timeout(timeout_seconds)
    headers = {
        "Authorization": f"Bearer {profile.api_key}",
        "Content-Type": "application/json",
    }
    start = perf_counter()
    try:
        response = await _send_llm_http_request(
            method="POST",
            profile=profile,
            url=url,
            endpoint_kind=endpoint_kind,
            headers=headers,
            timeout=timeout,
            json_body=request_body,
        )
    except (ImportError, ValueError) as exc:
        raise LLMRuntimeError(
            format_llm_client_initialization_error(exc),
            request_url=url,
            endpoint_kind=endpoint_kind,
            duration_ms=compute_duration_ms(start),
        ) from exc
    except httpx.TimeoutException as exc:
        raise LLMRuntimeError(
            f"模型请求超时（{timeout_seconds} 秒）",
            request_url=url,
            endpoint_kind=endpoint_kind,
            duration_ms=compute_duration_ms(start),
        ) from exc
    except (httpx.HTTPError, ssl.SSLError) as exc:
        raise LLMRuntimeError(
            format_llm_runtime_error_for_user(f"模型请求失败: {exc}"),
            request_url=url,
            endpoint_kind=endpoint_kind,
            duration_ms=compute_duration_ms(start),
        ) from exc

    duration_ms = compute_duration_ms(start)
    if response.status_code in (404, 405, 501):
        raise LLMEndpointProtocolError(
            format_http_error(response.status_code, response.text, url),
            failed_endpoint_kind=endpoint_kind,
            response_envelope=None,
            request_url=url,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
    if not 200 <= response.status_code < 300:
        raise LLMRuntimeError(
            format_http_error(response.status_code, response.text, url),
            request_url=url,
            endpoint_kind=endpoint_kind,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )

    try:
        data = response.json()
    except (TypeError, ValueError) as exc:
        raise LLMEndpointProtocolError(
            "模型响应缺少有效的 JSON 外壳",
            failed_endpoint_kind=endpoint_kind,
            response_envelope="invalid",
            request_url=url,
            status_code=response.status_code,
            duration_ms=duration_ms,
        ) from exc

    from .adaptation.endpoint import classify_response_envelope

    response_envelope = classify_response_envelope(endpoint_kind, data)
    if response_envelope != "valid":
        raise LLMEndpointProtocolError(
            "模型响应与请求端点协议不匹配"
            if response_envelope == "other_endpoint"
            else "模型响应缺少有效的端点外壳",
            failed_endpoint_kind=endpoint_kind,
            response_envelope=response_envelope,
            request_url=url,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )

    if not isinstance(data, dict):
        raise LLMEndpointProtocolError(
            "模型响应缺少有效的端点外壳",
            failed_endpoint_kind=endpoint_kind,
            response_envelope="invalid",
            request_url=url,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )

    try:
        content = content_extractor(data)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise LLMRuntimeError(
            "模型响应缺少可解析的文本内容",
            request_url=url,
            endpoint_kind=endpoint_kind,
            status_code=response.status_code,
            duration_ms=duration_ms,
        ) from exc

    if not isinstance(content, str) or not content.strip():
        if allow_empty_content:
            # 测活路径用：思考模型可能把回答放在 reasoning_content 字段，
            # content 为空字符串。这种情况视为"模型可达"，不抛错。
            content = "" if not isinstance(content, str) else content
        else:
            raise LLMRuntimeError(
                _empty_content_error_message(profile, data, endpoint_kind),
                request_url=url,
                endpoint_kind=endpoint_kind,
                status_code=response.status_code,
                duration_ms=duration_ms,
            )

    return ChatCompletionResult(
        content=content,
        usage=parse_completion_usage(data.get("usage")),
        request_url=url,
        attempted_urls=[url],
        endpoint_kind=endpoint_kind,
        status_code=response.status_code,
        duration_ms=duration_ms,
    )


async def ensure_llm_runtime_adaptation(
    session: "AsyncSession",
    profile: LLMProfile,
    *,
    failed_endpoint_kind: Literal["chat_completions", "responses"] | None = None,
) -> LLMRuntimeAdaptation:
    """Load or learn the endpoint and thinking adaptation for ``profile``.

    Endpoint discovery is serialized per target. The second cache read under
    the lock prevents concurrent requests from issuing duplicate probes.
    """

    from .adaptation.endpoint import (
        endpoint_adaptation_lock,
        endpoint_candidates,
        get_cached_endpoint_kind,
        record_endpoint_adaptation,
    )
    from .adaptation.thinking import ensure_thinking_adaptation

    api_base_url = resolve_base_url(profile.api_base_url)
    endpoint_kind = await get_cached_endpoint_kind(
        session,
        api_base_url=api_base_url,
        model_name=profile.model_name,
    )
    endpoint_attempted_urls: list[str] = []
    if endpoint_kind is None:
        async with endpoint_adaptation_lock(api_base_url, profile.model_name) as coordination:
            endpoint_kind = await get_cached_endpoint_kind(
                session,
                api_base_url=api_base_url,
                model_name=profile.model_name,
            )
            if endpoint_kind is None:
                if coordination.learned_endpoint_kind is not None:
                    endpoint_kind = coordination.learned_endpoint_kind
                elif coordination.probe_error is not None:
                    raise coordination.probe_error
                else:
                    try:
                        requires_final_text = is_stepfun_profile(profile)
                        probe_payload = {
                            "model": profile.model_name,
                            "messages": [{"role": "user", "content": "只回复 OK"}],
                            "temperature": 0,
                            "max_tokens": probe_max_tokens_for_profile(profile, fallback=8),
                        }
                        last_protocol_error: LLMEndpointProtocolError | None = None
                        for candidate in endpoint_candidates(failed_endpoint_kind):
                            try:
                                completion = await _request_completion_endpoint(
                                    profile,
                                    probe_payload,
                                    endpoint_kind=candidate,
                                    allow_empty_content=not requires_final_text,
                                )
                            except LLMEndpointProtocolError as exc:
                                last_protocol_error = exc
                                endpoint_attempted_urls.extend(exc.attempted_urls)
                                continue
                            except LLMRuntimeError as exc:
                                exc.attempted_urls = [
                                    *endpoint_attempted_urls,
                                    *exc.attempted_urls,
                                ]
                                raise
                            endpoint_attempted_urls.extend(completion.attempted_urls)
                            endpoint_kind = candidate
                            await record_endpoint_adaptation(
                                session,
                                api_base_url=api_base_url,
                                model_name=profile.model_name,
                                endpoint_kind=endpoint_kind,
                            )
                            coordination.learned_endpoint_kind = endpoint_kind
                            break
                        if endpoint_kind is None:
                            assert last_protocol_error is not None
                            raise last_protocol_error
                    except Exception as exc:
                        coordination.probe_error = exc
                        raise

    thinking_extra_body = await ensure_thinking_adaptation(
        session,
        profile,
        endpoint_kind=endpoint_kind,
    )
    return LLMRuntimeAdaptation(
        endpoint_kind,
        thinking_extra_body,
        tuple(endpoint_attempted_urls),
    )


def _merge_attempted_urls(*url_lists: list[str] | tuple[str, ...]) -> list[str]:
    merged: list[str] = []
    for urls in url_lists:
        for url in urls:
            if url not in merged:
                merged.append(url)
    return merged


def _merge_protocol_error_attempts(
    protocol_error: LLMEndpointProtocolError,
    error: LLMRuntimeError,
    *additional_url_lists: list[str] | tuple[str, ...],
) -> None:
    error.attempted_urls = [
        *protocol_error.attempted_urls,
        *(url for urls in additional_url_lists for url in urls),
        *error.attempted_urls,
    ]


async def request_chat_completion(
    profile: LLMProfile,
    payload: dict[str, object],
    *,
    extra_body: dict[str, object] | None = None,
    allow_empty_content: bool = False,
    session: "AsyncSession | None" = None,
    adaptation: LLMRuntimeAdaptation | None = None,
) -> ChatCompletionResult:
    if session is not None:
        active_adaptation = adaptation or await ensure_llm_runtime_adaptation(session, profile)
        try:
            completion = await _request_completion_endpoint(
                profile,
                payload,
                endpoint_kind=active_adaptation.endpoint_kind,
                extra_body=active_adaptation.thinking_extra_body,
                allow_empty_content=allow_empty_content,
            )
            completion.attempted_urls = _merge_attempted_urls(
                active_adaptation.endpoint_attempted_urls,
                completion.attempted_urls,
            )
            return completion
        except LLMEndpointProtocolError as protocol_error:
            from .adaptation.endpoint import invalidate_endpoint_adaptation
            from .adaptation.thinking import invalidate_thinking_adaptation

            api_base_url = resolve_base_url(profile.api_base_url)
            await invalidate_endpoint_adaptation(
                session,
                api_base_url=api_base_url,
                model_name=profile.model_name,
                failed_endpoint_kind=active_adaptation.endpoint_kind,
            )
            await invalidate_thinking_adaptation(
                session,
                api_base_url=api_base_url,
                model_name=profile.model_name,
                endpoint_kind=active_adaptation.endpoint_kind,
                expected_extra_body=active_adaptation.thinking_extra_body,
            )
            try:
                retry_adaptation = await ensure_llm_runtime_adaptation(
                    session,
                    profile,
                    failed_endpoint_kind=active_adaptation.endpoint_kind,
                )
            except LLMRuntimeError as retry_error:
                _merge_protocol_error_attempts(protocol_error, retry_error)
                raise
            try:
                completion = await _request_completion_endpoint(
                    profile,
                    payload,
                    endpoint_kind=retry_adaptation.endpoint_kind,
                    extra_body=retry_adaptation.thinking_extra_body,
                    allow_empty_content=allow_empty_content,
                )
            except LLMRuntimeError as retry_error:
                _merge_protocol_error_attempts(
                    protocol_error,
                    retry_error,
                    retry_adaptation.endpoint_attempted_urls,
                )
                raise
            completion.attempted_urls = _merge_attempted_urls(
                protocol_error.attempted_urls,
                retry_adaptation.endpoint_attempted_urls,
                completion.attempted_urls,
            )
            await _record_endpoint_protocol_switch(
                session,
                profile=profile,
                protocol_error=protocol_error,
                completion=completion,
            )
            return completion
        except LLMRuntimeError as runtime_error:
            runtime_error.attempted_urls = [
                *active_adaptation.endpoint_attempted_urls,
                *runtime_error.attempted_urls,
            ]
            raise

    if adaptation is not None:
        try:
            return await _request_completion_endpoint(
                profile,
                payload,
                endpoint_kind=adaptation.endpoint_kind,
                extra_body=adaptation.thinking_extra_body,
                allow_empty_content=allow_empty_content,
            )
        except LLMEndpointProtocolError:
            from .adaptation.endpoint import endpoint_candidates

            fallback_kind = endpoint_candidates(adaptation.endpoint_kind)[0]
            return await _request_completion_endpoint(
                profile,
                payload,
                endpoint_kind=fallback_kind,
                extra_body=adaptation.thinking_extra_body,
                allow_empty_content=allow_empty_content,
            )

    chat_error: LLMEndpointProtocolError | None = None
    try:
        return await _request_completion_endpoint(
            profile,
            payload,
            endpoint_kind="chat_completions",
            extra_body=extra_body,
            allow_empty_content=allow_empty_content,
        )
    except LLMEndpointProtocolError as exc:
        chat_error = exc

    assert chat_error is not None

    try:
        completion = await _request_completion_endpoint(
            profile,
            payload,
            endpoint_kind="responses",
            extra_body=extra_body,
            allow_empty_content=allow_empty_content,
        )
    except LLMRuntimeError as responses_error:
        responses_error.attempted_urls = [*chat_error.attempted_urls, *responses_error.attempted_urls]
        responses_error.args = (f"{responses_error}；此前已尝试：{chat_error}",)
        raise

    completion.attempted_urls = [*chat_error.attempted_urls, *completion.attempted_urls]
    return completion


async def request_structured_completion(
    profile: LLMProfile,
    payload: dict[str, object],
    result_model: type[StructuredResultT],
    *,
    extra_body: dict[str, object] | None = None,
    session: "AsyncSession | None" = None,
    adaptation: LLMRuntimeAdaptation | None = None,
    validation_error_message: str | None = None,
) -> tuple[ChatCompletionResult, StructuredResultT, str]:
    """Request and validate one typed JSON result without repair calls."""

    active_adaptation = adaptation
    mode = "prompt_only"
    request_payload = payload
    if session is not None:
        active_adaptation = active_adaptation or await ensure_llm_runtime_adaptation(
            session,
            profile,
        )
        from .adaptation.structured_output import (
            ensure_structured_output_adaptation,
            invalidate_structured_output_adaptation,
            is_structured_output_protocol_rejection,
        )

        mode = await ensure_structured_output_adaptation(
            session,
            profile,
            endpoint_kind=active_adaptation.endpoint_kind,
            thinking_extra_body=active_adaptation.thinking_extra_body,
        )
        if mode != "prompt_only":
            schema_name = re.sub(r"[^a-zA-Z0-9_-]", "_", result_model.__name__).lower()
            request_payload = with_structured_output(
                payload,
                mode=mode,
                schema=(
                    _prepare_strict_json_schema(result_model.model_json_schema())
                    if mode == "json_schema_strict"
                    else None
                ),
                schema_name=schema_name,
            )
        try:
            completion = await request_chat_completion(
                profile,
                request_payload,
                session=session,
                adaptation=active_adaptation,
            )
        except LLMRuntimeError as error:
            if mode != "prompt_only" and is_structured_output_protocol_rejection(error):
                await invalidate_structured_output_adaptation(
                    session,
                    api_base_url=resolve_base_url(profile.api_base_url),
                    model_name=profile.model_name,
                    endpoint_kind=active_adaptation.endpoint_kind,
                    expected_mode=mode,
                )
            raise
    else:
        completion = await request_chat_completion(
            profile,
            request_payload,
            extra_body=extra_body,
            adaptation=active_adaptation,
        )

    try:
        validation_context = {"structured_output_mode": mode}
        if mode == "prompt_only":
            result = parse_structured_result(
                completion.content,
                result_model,
                context=validation_context,
            )
        else:
            data = json.loads(completion.content)
            result = result_model.model_validate(data, context=validation_context)
    except LLMRuntimeError as error:
        raise LLMRuntimeError(
            validation_error_message or str(error),
            request_url=completion.request_url,
            attempted_urls=completion.attempted_urls,
            endpoint_kind=completion.endpoint_kind,
            status_code=completion.status_code,
            duration_ms=completion.duration_ms,
            usage=completion.usage,
            raw_content=completion.content,
        ) from error
    except (json.JSONDecodeError, ValidationError) as error:
        if session is not None and mode == "json_schema_strict" and active_adaptation is not None:
            from .adaptation.structured_output import (
                invalidate_structured_output_adaptation,
            )

            await invalidate_structured_output_adaptation(
                session,
                api_base_url=resolve_base_url(profile.api_base_url),
                model_name=profile.model_name,
                endpoint_kind=active_adaptation.endpoint_kind,
                expected_mode="json_schema_strict",
            )
        raise LLMRuntimeError(
            validation_error_message or f"模型返回的 JSON 结构无效: {error}",
            request_url=completion.request_url,
            attempted_urls=completion.attempted_urls,
            endpoint_kind=completion.endpoint_kind,
            status_code=completion.status_code,
            duration_ms=completion.duration_ms,
            usage=completion.usage,
            raw_content=completion.content,
        ) from error
    return completion, result, mode


def build_match_prompt(
    *,
    identity: IdentityProfile,
    primary_material: IdentityMaterial | None,
    professor: Professor,
    available_materials: list[IdentityMaterial],
    intended_research_direction: str | None = None,
) -> str:
    return build_match_prompt_parts(
        identity=identity,
        primary_material=primary_material,
        professor=professor,
        available_materials=available_materials,
        intended_research_direction=intended_research_direction,
    ).prompt


def build_match_prompt_parts(
    *,
    identity: IdentityProfile,
    primary_material: IdentityMaterial | None,
    professor: Professor,
    available_materials: list[IdentityMaterial],
    intended_research_direction: str | None = None,
    llm_profile: LLMProfile | None = None,
) -> MatchPromptParts:
    # Only the selected primary material is evidence for matching. Catalog
    # metadata is intentionally excluded so library growth cannot inflate prompts.
    del available_materials
    primary_material_text = (primary_material.extracted_text if primary_material else "") or ""
    if len(primary_material_text) > 5000:
        primary_material_text = f"{primary_material_text[:5000]}\n...(已截断)"

    intended_direction = _non_empty_text(intended_research_direction)
    intended_direction_block = intended_direction or "未填写"
    stable_prefix = dedent(
        f"""
        任务要求：
        1. 只判断匹配度，不要生成邮件草稿。
        2. match_reason 要简洁但具体。
        3. fit_points / risk_points / keywords 尽量聚焦，不要泛泛而谈。
        4. 如果用户意向研究方向与导师研究方向或近期论文明确相似，可以提高匹配度；不相似或未填写时不要额外扣分。

        当前发送身份：
        - 姓名：{_format_nullable(identity.name)}
        - 发件邮箱：{_format_nullable(identity.email_address)}
        - 默认语言：{_format_nullable(identity.default_language)}
        - 匹配阈值：{identity.match_threshold if identity.match_threshold is not None else "未设置"}

        默认材料：
        - 名称：{_format_nullable(primary_material.display_name if primary_material else None)}
        - 标签：{_format_nullable(primary_material.material_type if primary_material else None)}

        默认材料文本：
        {primary_material_text or "未上传可提取文本的默认材料"}

        用户意向研究方向：
        {intended_direction_block}

        意向方向评分参考：
        - 当用户意向研究方向与导师研究方向或近期论文相似时，请把它作为加分信号提高匹配度。
        - 加分必须基于可说明的相似点，并写入 match_reason 或 fit_points。
        - 该项不能替代默认材料证据；默认材料缺少支撑时仍需遵守上限规则。

        """
    ).strip()

    dynamic_suffix = _format_professor_info_block(professor)
    prompt = f"{stable_prefix}\n\n{dynamic_suffix}"
    return MatchPromptParts(
        prompt=prompt,
        stable_prefix=stable_prefix,
        prompt_hash=_hash_prompt(prompt),
        stable_prefix_hash=_hash_prompt(stable_prefix),
        prompt_cache_key=(
            _build_match_prompt_cache_key(
                identity=identity,
                primary_material=primary_material,
                llm_profile=llm_profile,
                intended_research_direction=intended_direction,
            )
            if llm_profile is not None
            else None
        ),
    )


def build_draft_prompt(
    *,
    identity: IdentityProfile,
    primary_material: IdentityMaterial | None,
    professor: Professor,
    available_materials: list[IdentityMaterial],
    custom_subject: str | None,
    custom_body: str | None,
    current_match: MatchEvaluationResult | None,
    custom_body_html: str | None = None,
    rewrite_preferences: DraftRewritePreferences | None = None,
) -> str:
    # Deprecated compatibility parameter: draft prompts must ignore match results.
    _ = current_match
    rewrite_preferences = rewrite_preferences or DraftRewritePreferences()
    rewrite_preferences_block = build_draft_rewrite_preferences(rewrite_preferences)
    rewrite_constraints_block = build_draft_rewrite_constraints(rewrite_preferences)

    return _build_base_generation_prompt(
        identity=identity,
        primary_material=primary_material,
        professor=professor,
        available_materials=available_materials,
        custom_subject=custom_subject,
        custom_body=custom_body,
        custom_body_html=custom_body_html,
        intended_research_direction=rewrite_preferences.intended_research_direction,
        extra_requirements=f"""
        {rewrite_preferences_block}

        {rewrite_constraints_block}

        任务要求：
        1. 必须以提供的套磁信模板为基础润色，不要从零重写。
        2. 用户补充要求在内容层面拥有最高优先级，必须完整执行；只有与 JSON wire 结构冲突的部分可以忽略。
        3. 用户未指定的部分，结合用户意向研究方向、学生材料与导师研究方向进行适度个性化。
        4. blocks 必须遵守系统提示中的受控富文本 wire 结构，并能渲染为邮件正文。
        5. 尽量保留可表达的富文本标记，例如加粗、斜体、链接和列表。
        6. 如果模板包含表格，保留表格中的信息顺序和语义，但不要输出 schema 不支持的表格节点。
        """,
    )


def _build_base_generation_prompt(
    *,
    identity: IdentityProfile,
    primary_material: IdentityMaterial | None,
    professor: Professor,
    available_materials: list[IdentityMaterial],
    custom_subject: str | None,
    custom_body: str | None,
    custom_body_html: str | None,
    intended_research_direction: str | None,
    extra_requirements: str,
) -> str:
    primary_material_text = (primary_material.extracted_text if primary_material else "") or ""
    if len(primary_material_text) > 5000:
        primary_material_text = f"{primary_material_text[:5000]}\n...(已截断)"
    # Draft generation uses the selected primary material text. Attachment and
    # catalog metadata are delivery concerns and must not affect the prompt.
    del available_materials

    template_body_text = resolve_template_text(custom_body, custom_body_html)
    payload: dict[str, object] = {
        "instructions": [
            "只返回 JSON 对象。",
            "不要输出解释、Markdown 代码块或多余文字。",
            "你要基于提供的套磁信模板生成邮件草稿，不要从零重写。",
            "用户补充要求在内容层面拥有最高优先级；只有与 JSON 输出协议冲突的部分可以忽略。",
            "尽量保留可表达的富文本标记，例如加粗、斜体、链接和列表。",
            "如果模板包含表格，保留表格中的信息顺序和语义，但不要输出 schema 不支持的表格节点。",
            "用户未指定的部分，结合用户意向研究方向、学生材料和导师研究方向做适度个性化。",
        ],
        "response_schema": {
            "subject": "邮件主题",
            "blocks": [
                {
                    "type": "paragraph",
                    "items": [
                        {
                            "runs": [
                                {
                                    "text": "李老师，您好：",
                                    "strong": False,
                                    "emphasis": False,
                                    "href": "",
                                    "line_break_after": False,
                                }
                            ]
                        }
                    ],
                }
            ],
        },
        "input": {
            "草稿改写要求": extra_requirements,
            "用户意向研究方向": _non_empty_text(intended_research_direction),
            "学生材料文本": primary_material_text,
            "套磁信模板主题": _non_empty_text(custom_subject),
            "套磁信模板正文": template_body_text,
        },
    }
    payload["input"]["导师信息"] = _build_draft_rewrite_professor_context(professor)
    return json.dumps(payload, ensure_ascii=False, indent=2)

def build_draft_rewrite_prompt(
    *,
    identity: IdentityProfile,
    primary_material: IdentityMaterial | None,
    professor: Professor,
    available_materials: list[IdentityMaterial],
    subject_template: str | None,
    source_blocks: list[DraftRewriteSourceBlock],
    current_match: MatchEvaluationResult | None,
    rewrite_preferences: DraftRewritePreferences | None,
    protected_tokens: list[DraftRewriteProtectedToken] | None = None,
) -> str:
    return build_draft_rewrite_prompt_parts(
        identity=identity,
        primary_material=primary_material,
        professor=professor,
        available_materials=available_materials,
        subject_template=subject_template,
        source_blocks=source_blocks,
        current_match=current_match,
        rewrite_preferences=rewrite_preferences,
        protected_tokens=protected_tokens,
    ).prompt


def build_draft_rewrite_prompt_parts(
    *,
    identity: IdentityProfile,
    primary_material: IdentityMaterial | None,
    professor: Professor,
    available_materials: list[IdentityMaterial],
    subject_template: str | None,
    source_blocks: list[DraftRewriteSourceBlock],
    current_match: MatchEvaluationResult | None,
    rewrite_preferences: DraftRewritePreferences | None,
    llm_profile: LLMProfile | None = None,
    protected_tokens: list[DraftRewriteProtectedToken] | None = None,
) -> DraftRewritePromptParts:
    # Deprecated compatibility parameter: draft rewrite prompts must ignore match results.
    _ = current_match, subject_template
    primary_material_text = (primary_material.extracted_text if primary_material else "") or ""
    if len(primary_material_text) > 5000:
        primary_material_text = f"{primary_material_text[:5000]}\n...(已截断)"
    del available_materials

    preferences = rewrite_preferences or DraftRewritePreferences()
    protected_tokens = protected_tokens or []
    instructions = [
        "只返回符合 response_schema 的 JSON 对象，不要输出解释、Markdown、HTML 或完整正文。",
        "不要返回 subject。",
        "replacements 只列需要修改的可编辑块（locked=false 且非 table），按原顺序；每项只含 segment_id 和完整连续段落 text，删除时 text 为空。",
        "保留全部成对、有序的 [[S数字]]...[[/S数字]] 样式标记和 [[P数字]] 占位符；不要合并、拆分或重排块。",
        "user_custom_instruction 是最高优先级的内容要求；未覆盖的内容必须执行 default_personalization_task。",
    ]
    response_schema: dict[str, object] = {
        "replacements": [
            {
                "segment_id": "seg_1",
                "text": "完整连续段落，[[S1]]可编辑样式区域[[/S1]]。",
            },
        ],
    }
    prompt_input: dict[str, object] = {
        "rewrite_preferences": _serialize_draft_rewrite_preferences(preferences),
        "user_custom_instruction": _serialize_draft_custom_instruction(
            preferences.draft_custom_instruction,
        ),
        "student_intended_research_direction": _non_empty_text(
            preferences.intended_research_direction,
        ),
        "student_material_text": primary_material_text,
    }

    payload: dict[str, object] = {
        "instructions": instructions,
        "response_schema": response_schema,
        "input": prompt_input,
    }
    if not prompt_input["rewrite_preferences"]:
        del prompt_input["rewrite_preferences"]
    if not prompt_input["user_custom_instruction"]:
        del prompt_input["user_custom_instruction"]
    if not prompt_input["student_intended_research_direction"]:
        del prompt_input["student_intended_research_direction"]

    stable_prefix = json.dumps(payload, ensure_ascii=False, indent=2)
    stable_prefix_hash = _hash_prompt(stable_prefix)

    prompt_input["source_blocks"] = [
        _serialize_draft_source_block(block)
        for block in source_blocks
    ]
    prompt_input["protected_tokens"] = [
        {"token": token.token, "value": token.value}
        for token in protected_tokens
    ]
    prompt_input["professor"] = _build_draft_rewrite_professor_context(professor)
    default_personalization_task = _build_draft_rewrite_default_personalization_task(professor)
    if default_personalization_task:
        prompt_input["default_personalization_task"] = default_personalization_task

    prompt = json.dumps(payload, ensure_ascii=False, indent=2)
    return DraftRewritePromptParts(
        prompt=prompt,
        stable_prefix=stable_prefix,
        prompt_hash=_hash_prompt(prompt),
        stable_prefix_hash=stable_prefix_hash,
        prompt_cache_key=(
            _build_draft_rewrite_prompt_cache_key(
                identity=identity,
                primary_material=primary_material,
                llm_profile=llm_profile,
                stable_prefix_hash=stable_prefix_hash,
            )
            if llm_profile is not None
            else None
        ),
    )


def _serialize_draft_source_block(block: DraftRewriteSourceBlock) -> dict[str, object]:
    if block.type == "table":
        return {
            "segment_id": block.segment_id,
            "type": block.type,
            "text": "表格块原样保留，不参与改写。",
            "locked": True,
        }

    return {
        "segment_id": block.segment_id,
        "type": block.type,
        "text": block.rewrite_text or block.text,
        "locked": block.locked,
        "style_regions": [
            {
                "style_id": region.style_id,
                "style": region.style,
            }
            for region in block.style_regions
        ],
    }


def build_draft_rewrite_preferences(preferences: DraftRewritePreferences | None) -> str:
    preferences = preferences or DraftRewritePreferences()
    return _build_draft_custom_instruction_block(preferences.draft_custom_instruction)

def _build_draft_custom_instruction_block(value: str | None) -> str:
    instruction = (value or "").strip()
    if not instruction:
        return ""
    return dedent(
        f"""

        用户补充要求：
        以下内容来自用户设置，是内容层面最高优先级的要求，必须完整执行。
        只有要求改变 JSON wire 结构或输出 JSON 以外内容的部分可以忽略；其余内容不得因真实性、原模板、日期、经历、导师信息或研究方向而受限。

        {instruction}
        """
    ).strip()

def _serialize_draft_custom_instruction(value: str | None) -> dict[str, str]:
    instruction = (value or "").strip()
    if not instruction:
        return {}
    return {
        "priority": (
            "内容层面最高优先级，必须完整执行。只有要求改变 JSON wire 结构、"
            "segment_id/样式标记/运行时占位符协议或输出 JSON 以外内容的部分可以忽略；"
            "不得因真实性、原模板、日期、经历、导师信息或研究方向而限制其余内容。"
        ),
        "content": instruction,
    }

def build_draft_rewrite_constraints(preferences: DraftRewritePreferences | None) -> str:
    _ = preferences
    return dedent(
        """
        草稿改写约束：
        - 用户补充要求在内容层面拥有最高优先级；只有 JSON wire 结构要求不可被覆盖。
        - 用户未指定的部分，默认在保留模板骨架的基础上优化表达、连接句和个性化内容。
        - 用户未指定的部分，结合用户意向研究方向、学生材料和导师研究方向适度个性化。

        用户未指定的部分才使用上述默认个性化策略。
        """
    ).strip()


def resolve_template_text(
    body_text: str | None,
    body_html: str | None,
) -> str | None:
    normalized_body_text = (body_text or "").strip()
    if normalized_body_text:
        return normalized_body_text

    normalized_body_html = (body_html or "").strip()
    if not normalized_body_html:
        return None

    extracted_text = html_to_text(normalized_body_html)
    return extracted_text or None


def _hash_prompt(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _format_nullable(value: object) -> str:
    if value is None:
        return "未知"
    if isinstance(value, str):
        return value.strip() or "未知"
    return str(value)

def _non_empty_text(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

def _build_professor_prompt_context(professor: Professor) -> dict[str, object]:
    context: dict[str, object] = {}
    for key, value in (
        ("name", professor.name),
        ("email", professor.email),
        ("title", professor.title),
        ("university", professor.university),
        ("school", professor.school),
        ("department", professor.department),
        ("research_direction", professor.research_direction),
        ("profile_url", professor.profile_url),
    ):
        text = _non_empty_text(value)
        if text is not None:
            context[key] = text

    recent_papers = [
        paper
        for paper in (_non_empty_text(item) for item in (professor.recent_papers or []))
        if paper is not None
    ]
    if recent_papers:
        context["recent_papers"] = recent_papers

    return context

def _build_draft_rewrite_professor_context(
    professor: Professor,
) -> dict[str, object]:
    context: dict[str, object] = {}
    for key, value in (("name", professor.name),):
        text = _non_empty_text(value)
        if text is not None:
            context[key] = text

    research_direction = _non_empty_text(professor.research_direction)
    if research_direction is not None:
        context["research_direction"] = research_direction

    recent_papers = [
        paper
        for paper in (_non_empty_text(item) for item in (professor.recent_papers or []))
        if paper is not None
    ]
    if recent_papers:
        context["recent_papers"] = recent_papers

    return context


def _build_draft_rewrite_default_personalization_task(
    professor: Professor,
) -> dict[str, object]:
    if _non_empty_text(professor.research_direction) is None:
        return {}

    return {
        "objective": "至少完成一处可见、实质的导师方向个性化，不能原样返回。",
        "professor_name": "导师称呼沿用 professor.name；仅可省略末尾职称括号。",
        "scope": "范围随原信，可概括或结合多个有依据的方向；位置、多少以自然为准。",
        "planning": "判断学生材料支持的契合点；多个点各放入唯一、最合适的 segment_id；不要输出规划。",
        "placement": "有直接经历时就地结合；无直接经历时在最自然处克制表达一次兴趣或学习意愿。",
        "sparse_professor_context": "professor 只有短标签或宽泛词时，只按字面呼应；仅一个 replacement 可新增该标签，其余不提，也不扩展子方向、技术问题或应用。",
        "fact_boundary": "学生事实只用输入中明说的内容；宽泛词重合不代表研究任务相关。不补工具、方法、结果或技术联系，不写“相通之处”“潜在联系”“高度契合”。",
        "direction_in_source": "如果 source_blocks 已展开 research_direction：短而自然则保留；长、多、像清单时，直接用自然研究重心替换列表文字；位于 [[S数字]] 内则保留标记。不要保留整表后只追加说明；先改列表，再补充学生联系。",
        "final_check": "检查方向段落：短而自然的不动；长、多或层级密集且仍像清单时，改写列表本身，不只在后面追加说明。再删去重复、无依据或无关内容，并确认有实质修改。",
    }

def _serialize_draft_rewrite_preferences(preferences: DraftRewritePreferences) -> dict[str, str]:
    _ = preferences
    return {}

def _format_professor_info_block(professor: Professor) -> str:
    context = _build_professor_prompt_context(professor)
    lines = ["导师信息："]
    field_labels = [
        ("name", "姓名"),
        ("email", "邮箱"),
        ("title", "职称"),
        ("university", "学校"),
        ("school", "学院"),
        ("department", "院系"),
        ("research_direction", "研究方向"),
        ("profile_url", "主页"),
    ]

    for key, label in field_labels:
        value = context.get(key)
        if isinstance(value, str):
            lines.append(f"- {label}：{value}")

    recent_papers = context.get("recent_papers")
    if isinstance(recent_papers, list) and recent_papers:
        lines.append("- 近期论文：")
        lines.extend(f"  - {paper}" for paper in recent_papers if isinstance(paper, str))

    if len(lines) == 1:
        lines.append("- 无可用导师信息")

    return "\n".join(lines)


def _is_official_openai_profile(profile: LLMProfile) -> bool:
    if profile.provider != "openai":
        return False
    return resolve_base_url(profile.api_base_url).rstrip("/") == DEFAULT_BASE_URL


def _build_match_prompt_cache_key(
    *,
    identity: IdentityProfile,
    primary_material: IdentityMaterial | None,
    llm_profile: LLMProfile,
    intended_research_direction: str | None,
) -> str | None:
    if not _is_official_openai_profile(llm_profile):
        return None
    material_id = primary_material.id if primary_material is not None else "none"
    direction_hash = hashlib.sha256((intended_research_direction or "").encode("utf-8")).hexdigest()[:12]
    return f"match:v2:{identity.id}:{material_id}:{llm_profile.id}:{direction_hash}"

def _build_draft_rewrite_prompt_cache_key(
    *,
    identity: IdentityProfile,
    primary_material: IdentityMaterial | None,
    llm_profile: LLMProfile,
    stable_prefix_hash: str,
) -> str | None:
    if not _is_official_openai_profile(llm_profile):
        return None
    identity_id = identity.id if identity.id is not None else "none"
    material_id = primary_material.id if primary_material is not None else "none"
    return (
        f"draft-rewrite:v5:{identity_id}:{material_id}:{llm_profile.id}:"
        f"{stable_prefix_hash[:16]}"
    )


def extract_json_object(raw_text: str) -> str:
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise LLMRuntimeError("模型未返回 JSON 对象")
    return text[start : end + 1]


def parse_structured_result(
    raw_text: str,
    result_model: type[StructuredResultT],
    *,
    context: dict[str, object] | None = None,
) -> StructuredResultT:
    try:
        data = json.loads(extract_json_object(raw_text))
        result = result_model.model_validate(data, context=context)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise LLMRuntimeError(f"模型返回的 JSON 结构无效: {exc}") from exc
    if result_model is DraftGenerationResult:
        return _normalize_draft_generation_result(result)
    return result

def resolve_base_url(api_base_url: str | None) -> str:
    return (api_base_url or DEFAULT_BASE_URL).strip().rstrip("/")


def with_structured_output(
    payload: dict[str, object],
    *,
    mode: Literal["json_schema_strict", "json_object", "prompt_only"],
    schema: dict[str, object] | None = None,
    schema_name: str = "structured_response",
) -> dict[str, object]:
    """Attach endpoint-neutral structured-output metadata to a request payload."""

    if mode == "json_schema_strict" and schema is None:
        raise ValueError("严格 JSON Schema 模式缺少 schema")
    result = dict(payload)
    result[STRUCTURED_OUTPUT_CONTROL_KEY] = {
        "mode": mode,
        "schema": dict(schema) if schema is not None else None,
        "schema_name": schema_name,
    }
    return result


def _prepare_strict_json_schema(schema: dict[str, object]) -> dict[str, object]:
    """Remove annotation-only Pydantic keywords from the wire schema."""

    def normalize(value: object) -> object:
        if isinstance(value, dict):
            normalized_items: dict[str, object] = {}
            for key, item in value.items():
                if key in {"title", "description", "default", "examples"}:
                    continue
                if key in {"properties", "$defs"} and isinstance(item, dict):
                    # Field/definition names are user-controlled keys.  A field
                    # named ``title`` is not the JSON Schema annotation keyword.
                    normalized_items[key] = {
                        child_key: normalize(child_value)
                        for child_key, child_value in item.items()
                    }
                else:
                    normalized_items[key] = normalize(item)
            return normalized_items
        if isinstance(value, list):
            return [normalize(item) for item in value]
        return value

    normalized = normalize(schema)
    if not isinstance(normalized, dict):
        raise ValueError("严格 JSON Schema 必须是对象")
    _validate_strict_json_schema_contract(normalized)
    return normalized


def _validate_strict_json_schema_contract(
    schema: dict[str, object],
    *,
    path: str = "$",
) -> None:
    if schema.get("type") == "object":
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            raise ValueError(f"严格 JSON Schema 的对象缺少 properties: {path}")
        if schema.get("additionalProperties") is not False:
            raise ValueError(
                f"严格 JSON Schema 的对象必须禁止额外字段: {path}"
            )
        required = schema.get("required")
        if not isinstance(required, list) or set(required) != set(properties):
            raise ValueError(
                f"严格 JSON Schema 的对象必须将全部属性标记为 required: {path}"
            )

    for key, value in schema.items():
        if isinstance(value, dict):
            _validate_strict_json_schema_contract(
                value,
                path=f"{path}.{key}",
            )
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, dict):
                    _validate_strict_json_schema_contract(
                        item,
                        path=f"{path}.{key}[{index}]",
                    )


def _extract_structured_output_control(
    payload: dict[str, object],
) -> tuple[dict[str, object], dict[str, object] | None]:
    request_payload = dict(payload)
    raw_control = request_payload.pop(STRUCTURED_OUTPUT_CONTROL_KEY, None)
    control = dict(raw_control) if isinstance(raw_control, dict) else None
    return request_payload, control


def _structured_output_format(control: dict[str, object]) -> dict[str, object] | None:
    mode = control.get("mode")
    if mode == "json_object":
        return {"type": "json_object"}
    if mode != "json_schema_strict":
        return None
    schema = control.get("schema")
    if not isinstance(schema, dict):
        raise ValueError("严格 JSON Schema 模式缺少有效 schema")
    return {
        "type": "json_schema",
        "name": str(control.get("schema_name") or "structured_response"),
        "strict": True,
        "schema": schema,
    }


def build_chat_completions_payload(payload: dict[str, object]) -> dict[str, object]:
    request_payload, control = _extract_structured_output_control(payload)
    if control is None:
        return request_payload
    output_format = _structured_output_format(control)
    if output_format is None:
        return request_payload
    if output_format.get("type") == "json_schema":
        request_payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                key: value
                for key, value in output_format.items()
                if key != "type"
            },
        }
    else:
        request_payload["response_format"] = output_format
    return request_payload


def is_deepseek_profile(profile: LLMProfile) -> bool:
    provider = (profile.provider or "").strip().lower()
    if provider == "deepseek":
        return True

    model_name = (profile.model_name or "").strip().lower()
    if model_name.startswith("deepseek"):
        return True

    base_url = resolve_base_url(profile.api_base_url).lower()
    return "deepseek" in base_url


def is_stepfun_profile(profile: LLMProfile) -> bool:
    """Return whether a profile targets one of StepFun's official OpenAI APIs."""

    return resolve_base_url(profile.api_base_url).lower() in _STEPFUN_OPENAI_BASE_URLS


def probe_max_tokens_for_profile(profile: LLMProfile, *, fallback: int) -> int:
    """Keep generic probe budgets unchanged while giving StepFun room to reason."""

    if not is_stepfun_profile(profile):
        return fallback
    configured_limit = profile.max_tokens or DEFAULT_LLM_MAX_TOKENS
    return min(configured_limit, STEPFUN_PROBE_MAX_TOKENS)


def _empty_content_error_message(
    profile: LLMProfile,
    data: dict[str, object],
    endpoint_kind: str,
) -> str:
    """Describe StepFun reasoning-only replies without treating them as success."""

    if endpoint_kind != "chat_completions" or not is_stepfun_profile(profile):
        return "模型返回了空内容"

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return "模型返回了空内容"
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict):
        return "模型返回了空内容"
    reasoning = message.get("reasoning") or message.get("reasoning_content")
    if not isinstance(reasoning, str) or not reasoning.strip():
        return "模型返回了空内容"
    if choice.get("finish_reason") == "length":
        return "StepFun 模型仅返回了推理内容，输出 Token 已耗尽，尚未返回最终文本"
    return "StepFun 模型仅返回了推理内容，尚未返回最终文本"


def build_endpoint_url(base_url: str, suffix: str) -> str:
    return f"{base_url.rstrip('/')}/{suffix.lstrip('/')}"


def compute_duration_ms(start: float) -> int:
    return max(int((perf_counter() - start) * 1000), 1)


def build_responses_payload(payload: dict[str, object]) -> dict[str, object]:
    payload, control = _extract_structured_output_control(payload)
    request_payload: dict[str, object] = {
        "model": payload["model"],
        "input": _build_responses_input(payload.get("messages", [])),
    }
    for key in ("thinking", "enable_thinking", "reasoning", "thinking_budget"):
        if key in payload:
            request_payload[key] = payload[key]
    if payload.get("reasoning_effort") is not None:
        request_payload["reasoning_effort"] = payload["reasoning_effort"]
    if payload.get("temperature") is not None:
        request_payload["temperature"] = payload["temperature"]
    if payload.get("max_tokens") is not None:
        request_payload["max_output_tokens"] = payload["max_tokens"]
    if payload.get("prompt_cache_key") is not None:
        request_payload["prompt_cache_key"] = payload["prompt_cache_key"]
    if payload.get("prompt_cache_retention") is not None:
        request_payload["prompt_cache_retention"] = payload["prompt_cache_retention"]
    if control is not None:
        output_format = _structured_output_format(control)
        if output_format is not None:
            request_payload["text"] = {"format": output_format}
    return request_payload


def _build_responses_input(messages: object) -> list[dict[str, object]]:
    if not isinstance(messages, list):
        return []

    input_items: list[dict[str, object]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        if not isinstance(role, str):
            continue
        input_items.append(
            {
                "type": "message",
                "role": role,
                "content": _build_responses_content_items(content),
            },
        )
    return input_items


def _build_responses_content_items(content: object) -> list[dict[str, str]]:
    if isinstance(content, str):
        return [{"type": "input_text", "text": content}]
    if not isinstance(content, list):
        return []

    content_items: list[dict[str, str]] = []
    for item in content:
        if isinstance(item, str):
            content_items.append({"type": "input_text", "text": item})
            continue
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str):
            content_items.append({"type": "input_text", "text": text})
    return content_items


def extract_chat_completion_content(data: dict[str, object]) -> str:
    content = data["choices"][0]["message"]["content"]
    if not isinstance(content, str):
        raise ValueError("choices[0].message.content 不是字符串")
    return content


def extract_responses_content(data: dict[str, object]) -> str:
    direct_output_text = data.get("output_text")
    if isinstance(direct_output_text, str) and direct_output_text.strip():
        return direct_output_text

    output_items = data.get("output")
    if not isinstance(output_items, list):
        raise ValueError("responses.output 不存在")

    chunks: list[str] = []
    for output_item in output_items:
        if not isinstance(output_item, dict):
            continue
        content_items = output_item.get("content")
        if not isinstance(content_items, list):
            continue
        for content_item in content_items:
            if not isinstance(content_item, dict):
                continue
            text_value = content_item.get("text")
            if isinstance(text_value, str) and text_value.strip():
                chunks.append(text_value)

    if not chunks:
        raise ValueError("responses.output 缺少文本内容")
    return "\n".join(chunks).strip()


def extract_model_ids(data: dict[str, object]) -> list[str]:
    raw_items = data.get("data", data.get("models"))
    if not isinstance(raw_items, list):
        raise ValueError("缺少 data/models 列表")

    model_ids: list[str] = []
    for item in raw_items:
        if isinstance(item, str) and item.strip():
            model_ids.append(item.strip())
            continue
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if isinstance(model_id, str) and model_id.strip():
            model_ids.append(model_id.strip())

    if not model_ids:
        raise ValueError("未解析到模型 ID")
    return model_ids


def format_http_error(status_code: int, response_text: str, request_url: str) -> str:
    return f"模型接口返回错误 {status_code}: {response_text[:300]} (请求 URL: {request_url})"


def parse_completion_usage(raw_usage: object) -> ChatCompletionUsage | None:
    if not isinstance(raw_usage, dict):
        return None
    cached_tokens = _coerce_token_count(raw_usage.get("prompt_cache_hit_tokens"))
    if cached_tokens is None:
        for details_key in ("prompt_tokens_details", "input_tokens_details"):
            details = raw_usage.get(details_key)
            if isinstance(details, dict):
                cached_tokens = _coerce_token_count(details.get("cached_tokens"))
                if cached_tokens is not None:
                    break
    reasoning_tokens = None
    for details_key in ("completion_tokens_details", "output_tokens_details"):
        details = raw_usage.get(details_key)
        if isinstance(details, dict):
            reasoning_tokens = _coerce_token_count(details.get("reasoning_tokens"))
            if reasoning_tokens is not None:
                break
    return ChatCompletionUsage(
        prompt_tokens=_coerce_token_count(
            raw_usage.get("prompt_tokens", raw_usage.get("input_tokens")),
        ),
        completion_tokens=_coerce_token_count(
            raw_usage.get("completion_tokens", raw_usage.get("output_tokens")),
        ),
        total_tokens=_coerce_token_count(raw_usage.get("total_tokens")),
        cached_tokens=cached_tokens,
        reasoning_tokens=reasoning_tokens,
    )


def _normalize_match_evaluation_result(result: MatchEvaluationResult) -> MatchEvaluationResult:
    result.match_reason = _normalize_text_field(result.match_reason, "match_reason")
    result.fit_points = _normalize_string_list(result.fit_points, 5)
    result.risk_points = _normalize_string_list(result.risk_points, 5)
    result.keywords = _normalize_string_list(result.keywords, 6)
    return result


def _draft_generation_wire_to_result(
    result: DraftGenerationWireResult,
) -> DraftGenerationResult:
    blocks: list[dict[str, object]] = []
    for block in result.blocks:
        if block.type == "paragraph":
            if len(block.items) != 1:
                raise LLMRuntimeError("模型返回的 paragraph 必须恰好包含一个 items 项")
            blocks.append(
                {
                    "type": "paragraph",
                    "children": _draft_body_item_to_nodes(block.items[0]),
                }
            )
            continue
        if not block.items:
            raise LLMRuntimeError("模型返回的列表正文不能为空")
        blocks.append(
            {
                "type": block.type,
                "items": [
                    _draft_body_item_to_nodes(item)
                    for item in block.items
                ],
            }
        )

    return _normalize_draft_generation_result(
        DraftGenerationResult(
            subject=result.subject,
            rich_body={"type": "doc", "blocks": blocks},
        )
    )


def _draft_body_item_to_nodes(item: DraftBodyItemWire) -> list[dict[str, object]]:
    if not item.runs:
        raise LLMRuntimeError("模型返回的富文本 items.runs 不能为空")

    nodes: list[dict[str, object]] = []
    for run in item.runs:
        node: dict[str, object] = {"type": "text", "text": run.text}
        href = run.href.strip()
        if href:
            if not href.startswith(("http://", "https://", "mailto:")):
                raise LLMRuntimeError("模型返回了不支持的富文本链接协议")
            node = {"type": "link", "href": href, "children": [node]}
        if run.emphasis:
            node = {"type": "emphasis", "children": [node]}
        if run.strong:
            node = {"type": "strong", "children": [node]}
        nodes.append(node)
        if run.line_break_after:
            nodes.append({"type": "line_break"})
    return nodes


def _normalize_draft_generation_result(result: DraftGenerationResult) -> DraftGenerationResult:
    result.subject = _normalize_text_field(result.subject, "subject")
    if result.rich_body is not None:
        rendered = render_rich_text_document(result.rich_body)
    elif result.body_html:
        rendered = normalize_email_html(result.body_html)
    elif result.body_text:
        rendered = text_to_email_html(result.body_text)
    else:
        raise LLMRuntimeError("模型返回的富文本正文为空")
    result.body_text = rendered.text
    result.body_html = rendered.html
    return result


def _normalize_text_field(value: str, field_name: str) -> str:
    cleaned = " ".join(value.split()) if field_name == "subject" else value.strip()
    if not cleaned:
        raise LLMRuntimeError(f"模型返回的 {field_name} 为空")
    return cleaned


def _normalize_html_field(value: str, fallback_text: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return text_to_html(fallback_text)
    if "<" not in cleaned or ">" not in cleaned:
        return text_to_html(cleaned)

    soup = parse_html(cleaned)
    if not soup.get_text(" ", strip=True):
        raise LLMRuntimeError("模型返回的 body_html 缺少可见正文")
    return str(soup)


def _normalize_string_list(values: list[str], max_items: int) -> list[str]:
    normalized: list[str] = []
    for value in values:
        cleaned = str(value).strip().strip("-•")
        cleaned = re.sub(r"\s+", " ", cleaned)
        if not cleaned or cleaned in normalized:
            continue
        normalized.append(cleaned)
        if len(normalized) >= max_items:
            break
    return normalized


def _normalize_integer_list(values: list[int]) -> list[int]:
    normalized: list[int] = []
    for value in values:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed in normalized:
            continue
        normalized.append(parsed)
    return normalized


def estimate_text_tokens(text: str) -> int:
    if not text.strip():
        return 0
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", text))
    ascii_count = len(re.findall(r"[A-Za-z0-9_]", text))
    other_count = max(len(text) - cjk_count - ascii_count, 0)
    return max(cjk_count + ceil(ascii_count / 4) + ceil(other_count / 3), 1)


def _coerce_token_count(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_LLM_MAX_TOKENS",
    "DEFAULT_LLM_TEMPERATURE",
    "STEPFUN_PROBE_MAX_TOKENS",
    "STRUCTURED_OUTPUT_CONTROL_KEY",
    "SYSTEM_DRAFT_PROMPT",
    "SYSTEM_DRAFT_REWRITE_PROMPT",
    "SYSTEM_MATCH_ONLY_PROMPT",
    "ChatCompletionResult",
    "ChatCompletionUsage",
    "DraftBodyBlockWire",
    "DraftBodyItemWire",
    "DraftBodyRunWire",
    "DraftGenerationResult",
    "DraftGenerationWireResult",
    "DraftRewritePreferences",
    "DraftRewritePromptParts",
    "DraftRewriteResult",
    "DraftRewriteSegmentReplacement",
    "DraftTokenEstimate",
    "GeneratedDraftContent",
    "GeneratedMatchEvaluation",
    "LLMEndpointProtocolError",
    "LLMModelCatalogResult",
    "LLMProbeResult",
    "LLMRuntimeAdaptation",
    "LLMRuntimeError",
    "MatchEvaluationResult",
    "MatchEvaluationWireResult",
    "MatchPromptParts",
    "StructuredResultT",
    "build_chat_completions_payload",
    "build_draft_prompt",
    "build_draft_rewrite_constraints",
    "build_draft_rewrite_preferences",
    "build_draft_rewrite_prompt",
    "build_draft_rewrite_prompt_parts",
    "build_endpoint_url",
    "build_match_prompt",
    "build_match_prompt_parts",
    "build_responses_payload",
    "compute_duration_ms",
    "ensure_llm_runtime_adaptation",
    "estimate_draft_content_tokens",
    "estimate_text_tokens",
    "extract_chat_completion_content",
    "extract_json_object",
    "extract_model_ids",
    "extract_responses_content",
    "fetch_llm_profile_models",
    "format_http_error",
    "format_llm_client_initialization_error",
    "format_llm_runtime_error_for_user",
    "generate_draft_content",
    "generate_match_evaluation",
    "is_deepseek_profile",
    "is_stepfun_profile",
    "parse_completion_usage",
    "parse_structured_result",
    "probe_llm_profile",
    "probe_max_tokens_for_profile",
    "request_chat_completion",
    "request_structured_completion",
    "resolve_base_url",
    "resolve_template_text",
    "with_structured_output",
]
