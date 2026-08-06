from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..jobs.runs import extract_token_usage_from_llm_response
from app.modules.llm.public import LLMRuntimeAdaptation
from ..llm.structured_output import (
    CANDIDATE_WIRE_PROMPT_CONTRACT,
    EMPTY_CANDIDATE_WIRE_JSON,
    V2ProfileExtractionWirePayload,
    request_crawler_structured_completion,
    v2_profile_wire_to_dict,
)

MAX_PROFILE_PAGE_TEXT_CHARS = 12000
MAX_PROFILE_HTML_EXCERPT_CHARS = 2000


class V2ProfileExtractionPayload(BaseModel):
    status: str = "no_candidate"
    candidate: dict[str, Any] | None = None


@dataclass(frozen=True)
class V2ProfileExtractionAttempt:
    attempt_number: int
    raw_model_text: str
    raw_payload: dict[str, Any] | None = None
    error: str | None = None
    usage: dict[str, int | None] | None = None


@dataclass(frozen=True)
class V2ProfileExtractionResult:
    payload: dict[str, Any]
    usage: dict[str, int] | None
    attempts: list[V2ProfileExtractionAttempt] = field(default_factory=list)
    page_text_hash: str | None = None
    page_text_length: int = 0


async def invoke_v2_profile_extraction_agent(
    llm_profile: Any,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    university: str,
    school: str,
    source_url: str,
    title: str | None,
    page_text: str,
    page_html_excerpt: str | None = None,
    adaptation: LLMRuntimeAdaptation,
) -> V2ProfileExtractionResult:
    base_prompt = build_v2_profile_extraction_prompt(
        university=university,
        school=school,
        source_url=source_url,
        title=title,
        page_text=page_text,
        page_html_excerpt=page_html_excerpt,
    )
    completion, wire_payload, _structured_mode = await request_crawler_structured_completion(
        session_factory,
        llm_profile,
        adaptation,
        prompt=base_prompt,
        result_model=V2ProfileExtractionWirePayload,
    )
    parsed = V2ProfileExtractionPayload.model_validate(
        v2_profile_wire_to_dict(wire_payload)
    ).model_dump()
    usage = extract_token_usage_from_llm_response(completion)
    return V2ProfileExtractionResult(
        payload=parsed,
        usage=usage,
        attempts=[
            V2ProfileExtractionAttempt(
                attempt_number=1,
                raw_model_text=completion.content,
                raw_payload=parsed,
                error=None,
                usage=usage,
            )
        ],
        page_text_hash=_hash_text(page_text),
        page_text_length=len(page_text or ""),
    )


def build_v2_profile_extraction_prompt(
    *,
    university: str,
    school: str,
    source_url: str,
    title: str | None,
    page_text: str,
    page_html_excerpt: str | None = None,
) -> str:
    text = (page_text or "")[:MAX_PROFILE_PAGE_TEXT_CHARS]
    html_excerpt = (page_html_excerpt or "")[:MAX_PROFILE_HTML_EXCERPT_CHARS]
    return (
        "你是 AutoEmailSender 的 V2 详情页整页抽取 Worker。只处理当前页面，不要发现新 URL，不要请求新页面。\n"
        "只输出一个 JSON 对象，不要输出解释文字、Markdown 或代码块。\n"
        "JSON 字段必须为 \"status\" 和 \"candidate\"。status 只能是 candidate 或 no_candidate。\n"
        f"如果页面不是单个导师个人详情页，返回 {{\"status\":\"no_candidate\",\"candidate\":{EMPTY_CANDIDATE_WIRE_JSON}}}。\n"
        "status=candidate 时 candidate 必须包含 name；缺少姓名时返回 no_candidate。\n"
        "无论 status 为何，candidate 都必须是完整对象，不能返回 null。status=no_candidate 时必须使用上面的空候选对象。\n"
        f"{CANDIDATE_WIRE_PROMPT_CONTRACT}\n"
        "学校和学院优先使用用户输入的上下文，不要因为页面缺失就留空。\n"
        "profile_url 和 source_url 应使用当前详情页 URL。\n"
        f"学校：{university}\n"
        f"学院/单位：{school}\n"
        f"当前详情页 URL：{source_url}\n"
        f"页面标题：{title or ''}\n"
        f"HTML 摘要：\n{html_excerpt}\n"
        f"整页文本：\n{text}"
    )


def _hash_text(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()
