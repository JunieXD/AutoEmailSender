from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
import re
from typing import Any

from app.core.time import as_utc_aware, utc_now

from sqlalchemy import select

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
    CrawlCandidate,
    CrawlJob,
    CrawlWorkerKind,
    CrawlPageChunk,
    CrawlPageChunkStatus,
    CrawlPageTask,
    CrawlPageTaskStatus,
)
from pydantic import BaseModel, ConfigDict, Field

from app.services.crawler_tools import CrawlToolContext, ProfessorCandidatePayload, save_candidate_payloads_shared
from app.services.crawler_chunk_runtime import split_page_chunk_for_retry
from app.services.crawler_debug import append_crawler_v2_debug_event
from app.services.crawler_v2_retry import mark_crawler_v2_failed
from app.services.crawler_v2_scheduler import ensure_job_active
from app.services.crawler_v2_token_usage import record_crawler_v2_token_usage
from app.services.crawler_v2_url_utils import is_same_domain, normalize_url
from app.services.crawl_job_runtime import build_faculty_crawler_model
from app.services.thinking_adaptation import ensure_thinking_adaptation
from app.services.crawl_job_runs import extract_token_usage_from_llm_response
from app.services.llm_runtime import format_llm_runtime_error_for_user, parse_structured_result

MAX_CANDIDATES_PER_CHUNK_RESULT = 10
_MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


class V2ChunkAgentPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    candidate_count: int = Field(strict=True, ge=0)
    candidates: list[dict[str, Any]]
    discovered_urls: list[str]


async def invoke_v2_chunk_agent(
    llm_profile: Any,
    *,
    university: str,
    school: str,
    source_url: str,
    chunk_content: str,
    thinking_extra_body: dict[str, object] | None = None,
) -> tuple[dict[str, Any], dict[str, int | None] | None, str]:
    model = build_faculty_crawler_model(llm_profile, extra_body=thinking_extra_body)
    prompt = build_v2_chunk_prompt(
        university=university,
        school=school,
        source_url=source_url,
        chunk_content=chunk_content,
    )
    response = await model.ainvoke(prompt)
    content = _extract_message_text(response)
    payload = parse_structured_result(content, V2ChunkAgentPayload)
    usage = extract_token_usage_from_llm_response(response)
    return payload.model_dump(), usage, content


def build_v2_chunk_prompt(*, university: str, school: str, source_url: str, chunk_content: str) -> str:
    return (
        "你是 AutoEmailSender 的 V2 Chunk Worker。只处理当前 chunk，不要请求新页面，不要引用历史对话。\n"
        "只输出一个 JSON 对象，字段为 candidate_count、candidates、discovered_urls。不要输出解释文字，也不能输出 chunk_status。\n"
        "候选必须来自当前 chunk 内的明确证据，不能猜测，不能翻译、音译或拼音化页面原文。\n"
        "候选判定优先级：当前 chunk 中 Markdown 链接形如 [姓名](http/https URL)，且链接文本像人名、URL 像个人主页时，这就是明确的姓名 + profile_url 候选证据。\n"
        "即使没有 email、title、department、research_direction，只要有姓名 + profile_url，也必须视为候选，不是 no_candidates。\n"
        "candidate_count 是当前 chunk 内明确候选的总数。candidate_count 为 0 时 candidates 必须为空；1 到 10 时 candidates 数组长度必须与 candidate_count 相等；candidate_count 必须为 11 或更大时 candidates 必须为空。\n"
        "如果当前 chunk 内姓名 + profile_url 候选超过 10 个，candidate_count 必须为 11 或更大，不要输出前 10 个，也不要返回 0。\n"
        "页面较长、分类复杂、分页导航、详情页链接、不确定或刚好 10 个候选，都不能把 candidate_count 填为 11 或更大。\n"
        "缺少 email 且缺少 profile_url 的候选不可提交；但有姓名 + profile_url 的候选即使缺少 email 也可提交。\n"
        "no_candidates 只允许在当前 chunk 内没有任何姓名+邮箱、姓名+profile_url、教师卡片或教师表格行时使用。\n"
        "当前 chunk 中 Markdown 链接形如 [导师名](URL) 且与候选姓名匹配时，必须把 URL 写入该候选 profile_url。\n"
        "导师个人主页链接属于候选 profile_url，不能放入 discovered_urls。\n"
        "discovered_urls 只放候选列表页、分页页、教师目录页等继续抓取入口。\n"
        "每个候选字段使用英文键：name、email、title、university、school、department、research_direction、recent_papers、profile_url、source_url、confidence、field_confidence、evidence。\n"
        "confidence 和 field_confidence 必须是 0 到 1 的数字；evidence 只写简短摘要，不复制大段原文。\n"
        "输出示例（正常保存）：\n"
        '{"candidate_count": 1, "candidates": [{"name": "张三", "email": "zhang@example.edu", "title": "教授", "university": "示例大学", "school": "计算机学院", "department": "", "research_direction": "软件工程", "recent_papers": [], "profile_url": "https://example.edu/zhang.html", "source_url": "https://example.edu/faculty", "confidence": 0.9, "field_confidence": {"name": 0.95, "email": 0.9, "profile_url": 0.95}, "evidence": {"summary": "当前 chunk 中姓名链接和邮箱明确出现"}}], "discovered_urls": []}\n'
        "输出示例（当前 chunk 明确超过 10 个候选）：\n"
        '{"candidate_count": 11, "candidates": [], "discovered_urls": []}\n'
        "输出示例（无候选）：\n"
        '{"candidate_count": 0, "candidates": [], "discovered_urls": []}\n'
        f"学校：{university}\n"
        f"学院/单位：{school}\n"
        f"来源 URL：{source_url}\n"
        "当前 chunk 正文：\n"
        f"{chunk_content}"
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




def _validate_chunk_agent_payload(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Chunk Worker 返回结构不是 JSON 对象")
    required = {"candidate_count", "candidates", "discovered_urls"}
    missing = required.difference(payload)
    if missing:
        raise ValueError(f"Chunk Worker 返回缺少字段：{', '.join(sorted(missing))}")
    candidate_count = payload["candidate_count"]
    if isinstance(candidate_count, bool) or not isinstance(candidate_count, int) or candidate_count < 0:
        raise ValueError("Chunk Worker 返回的 candidate_count 必须是大于等于 0 的整数")
    candidates = payload["candidates"]
    if not isinstance(candidates, list):
        raise ValueError("Chunk Worker 返回的 candidates 必须是数组")
    if not isinstance(payload["discovered_urls"], list):
        raise ValueError("Chunk Worker 返回的 discovered_urls 必须是数组")
    if candidate_count <= MAX_CANDIDATES_PER_CHUNK_RESULT and len(candidates) != candidate_count:
        raise ValueError("Chunk Worker 返回的 candidate_count 与 candidates 数量不一致")
    return payload


def _derive_chunk_status(candidate_count: int) -> str:
    if candidate_count == 0:
        return CrawlPageChunkStatus.NO_CANDIDATES.value
    if candidate_count <= MAX_CANDIDATES_PER_CHUNK_RESULT:
        return CrawlPageChunkStatus.COMPLETED.value
    return CrawlPageChunkStatus.SPLIT_REQUIRED.value

async def run_crawler_v2_chunk_worker_once(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    chunk_id: int,
    worker_id: str,
) -> int:
    try:
        async with session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
            if chunk is None or chunk.status != CrawlPageChunkStatus.PROCESSING.value or chunk.worker_id != worker_id:
                return 0
            if not await ensure_job_active(session, chunk.job_id):
                return 0
            job = await session.get(CrawlJob, chunk.job_id)
            if job is None:
                return 0
            llm_profile = await _resolve_llm_profile(session, job)
            if llm_profile is None:
                mark_crawler_v2_failed(
                    chunk,
                    message="缺少可用的 LLM Profile",
                    retryable_status=CrawlPageChunkStatus.FAILED_RETRYABLE.value,
                    terminal_status=CrawlPageChunkStatus.FAILED_TERMINAL.value,
                )
                await session.commit()
                return 1
            thinking_extra_body = await ensure_thinking_adaptation(session, llm_profile)
            await session.commit()

        chunk_agent_result = await invoke_v2_chunk_agent(
            llm_profile,
            university=job.university,
            school=job.school,
            source_url=chunk.source_url,
            chunk_content=chunk.content,
            thinking_extra_body=thinking_extra_body,
        )
        raw_model_text = None
        if isinstance(chunk_agent_result, tuple):
            if len(chunk_agent_result) >= 3:
                payload, usage, raw_model_text = chunk_agent_result[:3]
            else:
                payload, usage = chunk_agent_result
        else:
            payload = chunk_agent_result
            usage = None
        if not await _chunk_task_can_commit(session_factory, chunk_id=chunk_id, worker_id=worker_id):
            return 0
        if usage is not None:
            await record_crawler_v2_token_usage(
                session_factory,
                job_id=job.id,
                worker_kind=CrawlWorkerKind.CHUNK,
                work_item_id=chunk_id,
                model_name=getattr(llm_profile, "model_name", None),
                input_tokens=usage.get("input_tokens") or 0,
                output_tokens=usage.get("output_tokens") or 0,
                cached_tokens=usage.get("cached_tokens") or 0,
                raw_usage=dict(usage),
            )
        append_crawler_v2_debug_event(
            job.id,
            worker_kind="chunk",
            event_name="llm_response",
            work_item_id=chunk_id,
            payload={
                "chunk_id": chunk.chunk_id,
                "source_url": chunk.source_url,
                "chunk_content": chunk.content,
                "raw_payload": payload,
                "raw_model_text": raw_model_text,
                "token_usage": dict(usage) if usage is not None else None,
            },
        )
        payload = _validate_chunk_agent_payload(payload)
        candidate_count = payload["candidate_count"]
        candidate_payload_count = len(payload["candidates"])
        derived_chunk_status = _derive_chunk_status(candidate_count)
        candidates: list[ProfessorCandidatePayload]
        if derived_chunk_status == CrawlPageChunkStatus.SPLIT_REQUIRED.value:
            candidates = []
        else:
            candidates = [ProfessorCandidatePayload.model_validate(item) for item in payload["candidates"]]
        save_result = await complete_current_chunk(
            session_factory,
            chunk_id=chunk_id,
            worker_id=worker_id,
            candidates=candidates,
            discovered_urls=[str(url) for url in payload["discovered_urls"]],
            candidate_count=candidate_count,
        )
        save_result["candidate_count"] = candidate_count
        save_result["candidate_payload_count"] = candidate_payload_count
        if derived_chunk_status == CrawlPageChunkStatus.SPLIT_REQUIRED.value and candidate_payload_count:
            save_result["contract_warning"] = "candidate_count_candidates_conflict"
        append_crawler_v2_debug_event(
            job.id,
            worker_kind="chunk",
            event_name="chunk_completed",
            work_item_id=chunk_id,
            payload={
                "chunk_id": chunk.chunk_id,
                "source_url": chunk.source_url,
                "parsed_payload": payload,
                "save_result": save_result,
            },
        )
        return 1
    except Exception as exc:
        async with session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
            if chunk is not None and chunk.status == CrawlPageChunkStatus.PROCESSING.value and chunk.worker_id == worker_id and not _lease_expired(chunk.lease_expires_at) and await ensure_job_active(session, chunk.job_id):
                mark_crawler_v2_failed(
                    chunk,
                    message=format_llm_runtime_error_for_user(exc),
                    retryable_status=CrawlPageChunkStatus.FAILED_RETRYABLE.value,
                    terminal_status=CrawlPageChunkStatus.FAILED_TERMINAL.value,
                )
            await session.commit()
        return 1


async def _chunk_task_can_commit(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    chunk_id: int,
    worker_id: str,
) -> bool:
    async with session_factory() as session:
        chunk = await session.get(CrawlPageChunk, chunk_id)
        if chunk is None:
            return False
        if chunk.status != CrawlPageChunkStatus.PROCESSING.value or chunk.worker_id != worker_id:
            return False
        if _lease_expired(chunk.lease_expires_at):
            return False
        return await ensure_job_active(session, chunk.job_id)


async def _resolve_llm_profile(session: AsyncSession, job: CrawlJob):
    from app.models import LLMProfile

    if job.llm_profile_id is not None:
        return await session.get(LLMProfile, job.llm_profile_id)
    return await session.scalar(
        select(LLMProfile)
        .where(LLMProfile.is_default.is_(True))
        .order_by(LLMProfile.id.asc())
        .limit(1)
    )



def _normalize_person_name_for_link_match(value: str | None) -> str:
    return "".join(str(value or "").split()).casefold()


def _extract_markdown_profile_links(chunk_content: str, *, base_url: str) -> dict[str, str]:
    links: dict[str, str] = {}
    for match in _MARKDOWN_LINK_PATTERN.finditer(chunk_content):
        key = _normalize_person_name_for_link_match(match.group(1))
        if not key:
            continue
        normalized = normalize_url(match.group(2), base_url=base_url)
        if normalized:
            links.setdefault(key, normalized)
    return links


def _fill_candidate_profile_urls_from_chunk(
    candidates: Sequence[ProfessorCandidatePayload],
    *,
    chunk_content: str,
    source_url: str,
) -> list[ProfessorCandidatePayload]:
    link_map = _extract_markdown_profile_links(chunk_content, base_url=source_url)
    filled: list[ProfessorCandidatePayload] = []
    for candidate in candidates:
        if candidate.profile_url:
            filled.append(candidate)
            continue
        profile_url = link_map.get(_normalize_person_name_for_link_match(candidate.name))
        if profile_url is None:
            filled.append(candidate)
            continue
        data = candidate.model_dump()
        data["profile_url"] = profile_url
        filled.append(ProfessorCandidatePayload.model_validate(data))
    return filled

async def complete_current_chunk(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    chunk_id: int,
    worker_id: str,
    candidates: Sequence[ProfessorCandidatePayload],
    discovered_urls: Sequence[str],
    candidate_count: int,
) -> dict[str, int | str]:
    async with session_factory() as session:
        chunk = await session.get(CrawlPageChunk, chunk_id)
        if chunk is None:
            return {"status": "missing", "saved_count": 0, "url_count": 0, "enrichment_count": 0}
        if chunk.status != CrawlPageChunkStatus.PROCESSING.value or chunk.worker_id != worker_id:
            return {"status": "not_claimed", "saved_count": 0, "url_count": 0, "enrichment_count": 0}
        if _lease_expired(chunk.lease_expires_at):
            return {"status": "lease_expired", "saved_count": 0, "url_count": 0, "enrichment_count": 0}
        if not await ensure_job_active(session, chunk.job_id):
            return {"status": "inactive", "saved_count": 0, "url_count": 0, "enrichment_count": 0}
        job = await session.get(CrawlJob, chunk.job_id)
        if job is None:
            return {"status": "missing_job", "saved_count": 0, "url_count": 0, "enrichment_count": 0}

        derived_chunk_status = _derive_chunk_status(candidate_count)
        if derived_chunk_status == CrawlPageChunkStatus.SPLIT_REQUIRED.value:
            split_result = await split_page_chunk_for_retry(
                session_factory,
                job_id=chunk.job_id,
                chunk_pk=chunk.id,
                reason="candidate_count_exceeded",
            )
            return {
                "status": split_result["status"],
                "saved_count": 0,
                "url_count": 0,
                "enrichment_count": 0,
                "rejected_count": 0,
                "child_count": split_result["child_count"],
                "derived_chunk_status": derived_chunk_status,
            }
        discovered_listing_urls = {
            normalized
            for url in discovered_urls
            if (normalized := normalize_url(url, base_url=chunk.source_url))
            if is_same_domain(normalized, job.start_url)
        }
        ctx = CrawlToolContext(
            job_id=chunk.job_id,
            start_url=job.start_url,
            university=job.university,
            school=job.school,
            session_factory=session_factory,
            known_listing_urls=discovered_listing_urls,
        )
        enriched_candidates = _fill_candidate_profile_urls_from_chunk(
            candidates,
            chunk_content=chunk.content,
            source_url=chunk.source_url,
        )
        save_result = await save_candidate_payloads_shared(ctx, enriched_candidates)
        enrichment_count = 0
        candidate_profile_urls = {
            normalized
            for candidate in enriched_candidates
            if candidate.profile_url
            if (normalized := normalize_url(candidate.profile_url, base_url=chunk.source_url))
            if normalized not in discovered_listing_urls
        }
        url_count = 0
        seen_urls: set[str] = set()
        for url in discovered_urls:
            normalized = normalize_url(url, base_url=chunk.source_url)
            if normalized in candidate_profile_urls:
                continue
            if normalized in seen_urls or not is_same_domain(normalized, job.start_url):
                continue
            seen_urls.add(normalized)
            exists = await session.scalar(
                select(CrawlPageTask.id).where(
                    CrawlPageTask.job_id == chunk.job_id,
                    CrawlPageTask.normalized_url == normalized,
                )
            )
            if exists is not None:
                continue
            try:
                async with session.begin_nested():
                    session.add(
                        CrawlPageTask(
                            job_id=chunk.job_id,
                            normalized_url=normalized,
                            original_url=url,
                            status=CrawlPageTaskStatus.PENDING.value,
                        )
                    )
                    await session.flush()
            except IntegrityError:
                continue
            url_count += 1

        chunk.status = derived_chunk_status
        chunk.worker_id = None
        chunk.claimed_at = None
        chunk.lease_expires_at = None
        await session.commit()
        return {
            "status": "saved",
            "saved_count": save_result["saved_count"],
            "url_count": url_count,
            "enrichment_count": enrichment_count,
            "rejected_count": save_result["rejected_count"],
            "merged_count": save_result["merged_count"],
            "skipped_duplicate_count": save_result["skipped_duplicate_count"],
            "derived_chunk_status": derived_chunk_status,
        }


def _lease_expired(lease_expires_at: datetime | None) -> bool:
    if lease_expires_at is None:
        return False
    return as_utc_aware(lease_expires_at) <= utc_now()


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
