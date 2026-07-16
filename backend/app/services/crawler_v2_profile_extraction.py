from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from app.services.crawl_job_runs import extract_token_usage_from_llm_response
from app.services.crawl_job_runtime import build_faculty_crawler_model
from app.services.llm_runtime import LLMRuntimeAdaptation, LLMRuntimeError, parse_structured_result

DIRECT_LLM_STRUCTURED_MAX_ATTEMPTS = 3
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
    university: str,
    school: str,
    source_url: str,
    title: str | None,
    page_text: str,
    page_html_excerpt: str | None = None,
    adaptation: LLMRuntimeAdaptation,
    max_attempts: int = DIRECT_LLM_STRUCTURED_MAX_ATTEMPTS,
) -> V2ProfileExtractionResult:
    model = build_faculty_crawler_model(llm_profile, adaptation=adaptation)
    base_prompt = build_v2_profile_extraction_prompt(
        university=university,
        school=school,
        source_url=source_url,
        title=title,
        page_text=page_text,
        page_html_excerpt=page_html_excerpt,
    )
    attempts: list[V2ProfileExtractionAttempt] = []
    accumulated = {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0}
    last_error: Exception | None = None

    for attempt_number in range(1, max_attempts + 1):
        prompt = base_prompt if attempt_number == 1 else _build_structured_retry_prompt(
            base_prompt,
            str(last_error or "模型未返回有效 JSON"),
        )
        response = await model.ainvoke(prompt)
        raw_text = _extract_message_text(response)
        usage = extract_token_usage_from_llm_response(response)
        if usage is not None:
            accumulated["input_tokens"] += int(usage.get("input_tokens") or 0)
            accumulated["output_tokens"] += int(usage.get("output_tokens") or 0)
            accumulated["cached_tokens"] += int(usage.get("cached_tokens") or 0)
        try:
            parsed = parse_structured_result(raw_text, V2ProfileExtractionPayload).model_dump()
        except Exception as exc:
            last_error = exc
            attempts.append(
                V2ProfileExtractionAttempt(
                    attempt_number=attempt_number,
                    raw_model_text=raw_text,
                    raw_payload=None,
                    error=str(exc),
                    usage=usage,
                )
            )
            continue
        attempts.append(
            V2ProfileExtractionAttempt(
                attempt_number=attempt_number,
                raw_model_text=raw_text,
                raw_payload=parsed,
                error=None,
                usage=usage,
            )
        )
        return V2ProfileExtractionResult(
            payload=parsed,
            usage=accumulated,
            attempts=attempts,
            page_text_hash=_hash_text(page_text),
            page_text_length=len(page_text or ""),
        )

    raise LLMRuntimeError(f"详情页抽取结构化输出失败: {last_error}")


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
        "如果页面不是单个导师个人详情页，返回 {\"status\":\"no_candidate\",\"candidate\":null}。\n"
        "status=candidate 时 candidate 必须包含 name；缺少姓名时返回 no_candidate。\n"
        "candidate 字段使用英文键：name、email、title、university、school、department、research_direction、recent_papers、profile_url、source_url、confidence、field_confidence、evidence。\n"
        "学校和学院优先使用用户输入的上下文，不要因为页面缺失就留空。\n"
        "profile_url 和 source_url 应使用当前详情页 URL。\n"
        f"学校：{university}\n"
        f"学院/单位：{school}\n"
        f"当前详情页 URL：{source_url}\n"
        f"页面标题：{title or ''}\n"
        f"HTML 摘要：\n{html_excerpt}\n"
        f"整页文本：\n{text}"
    )


def _build_structured_retry_prompt(base_prompt: str, error: str) -> str:
    return (
        f"{base_prompt}\n\n"
        "上一次输出无法被系统解析。请严格只返回一个 JSON 对象，不要包含解释文字。\n"
        f"解析错误：{error}"
    )


def _extract_message_text(response: object) -> str:
    if isinstance(response, str):
        return response.strip()
    content = getattr(response, "content", None)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        pieces: list[str] = []
        for item in content:
            if isinstance(item, str):
                pieces.append(item)
            elif isinstance(item, dict):
                value = item.get("text") or item.get("content")
                if isinstance(value, str):
                    pieces.append(value)
        return "".join(pieces).strip()
    if isinstance(response, dict):
        value = response.get("content")
        return value.strip() if isinstance(value, str) else ""
    return ""


def _hash_text(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()
