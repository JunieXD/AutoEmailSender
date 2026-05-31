from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from app.core.time import as_utc_aware, utc_now

from sqlalchemy import select

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
    CrawlCandidate,
    CrawlCandidateEnrichmentTask,
    CrawlCandidateEnrichmentTaskStatus,
    CrawlJob,
    CrawlWorkerKind,
    CrawlPageChunk,
    CrawlPageChunkStatus,
    CrawlPageTask,
    CrawlPageTaskStatus,
)
from pydantic import BaseModel, Field

from app.services.crawler_tools import ProfessorCandidatePayload
from app.services.crawler_v2_scheduler import ensure_job_active
from app.services.crawler_v2_token_usage import record_crawler_v2_token_usage
from app.services.crawler_v2_url_utils import is_same_domain, normalize_url
from app.services.crawl_job_runtime import build_faculty_crawler_model
from app.services.crawl_job_runs import extract_token_usage_from_llm_response
from app.services.llm_runtime import parse_structured_result

MAX_CHUNK_ATTEMPTS = 2


class V2ChunkAgentPayload(BaseModel):
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    discovered_urls: list[str] = Field(default_factory=list)
    chunk_status: str = "completed"


async def invoke_v2_chunk_agent(
    llm_profile: Any,
    *,
    university: str,
    school: str,
    source_url: str,
    chunk_content: str,
) -> tuple[dict[str, Any], dict[str, int | None] | None]:
    model = build_faculty_crawler_model(llm_profile)
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
    return payload.model_dump(), usage


def build_v2_chunk_prompt(*, university: str, school: str, source_url: str, chunk_content: str) -> str:
    return (
        "你是 AutoEmailSender 的 V2 Chunk Worker。只处理当前 chunk，不要请求新页面，不要引用历史对话。\n"
        "请从当前 chunk 中提取候选导师，并发现明确出现的新 URL。\n"
        "只输出一个 JSON 对象，字段为 candidates、discovered_urls、chunk_status。\n"
        "chunk_status 只能是 completed、no_candidates 或 split_required。\n"
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



async def run_crawler_v2_chunk_worker_once(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    chunk_id: int,
    worker_id: str,
) -> int:
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
            chunk.status = CrawlPageChunkStatus.FAILED_RETRYABLE.value
            chunk.last_error = "缺少可用的 LLM Profile"
            chunk.worker_id = None
            chunk.claimed_at = None
            chunk.lease_expires_at = None
            await session.commit()
            return 1
    try:
        chunk_agent_result = await invoke_v2_chunk_agent(
            llm_profile,
            university=job.university,
            school=job.school,
            source_url=chunk.source_url,
            chunk_content=chunk.content,
        )
        if isinstance(chunk_agent_result, tuple):
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
        candidates = [ProfessorCandidatePayload.model_validate(item) for item in payload.get("candidates", [])]
        await complete_current_chunk(
            session_factory,
            chunk_id=chunk_id,
            worker_id=worker_id,
            candidates=candidates,
            discovered_urls=[str(url) for url in payload.get("discovered_urls", [])],
            chunk_status=str(payload.get("chunk_status") or "completed"),
        )
        return 1
    except Exception as exc:
        async with session_factory() as session:
            chunk = await session.get(CrawlPageChunk, chunk_id)
            if chunk is not None and chunk.status == CrawlPageChunkStatus.PROCESSING.value and chunk.worker_id == worker_id and not _lease_expired(chunk.lease_expires_at) and await ensure_job_active(session, chunk.job_id):
                chunk.last_error = str(exc)
                chunk.status = (
                    CrawlPageChunkStatus.FAILED_TERMINAL.value
                    if int(chunk.attempt_count or 0) >= MAX_CHUNK_ATTEMPTS
                    else CrawlPageChunkStatus.FAILED_RETRYABLE.value
                )
                chunk.worker_id = None
                chunk.claimed_at = None
                chunk.lease_expires_at = None
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


def candidate_needs_enrichment(candidate: CrawlCandidate) -> bool:
    return bool(
        (candidate.profile_url or "").strip()
        and (
            not (candidate.email or "").strip()
            or not (candidate.department or "").strip()
            or not (candidate.research_direction or "").strip()
        )
    )


async def complete_current_chunk(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    chunk_id: int,
    worker_id: str,
    candidates: Sequence[ProfessorCandidatePayload],
    discovered_urls: Sequence[str],
    chunk_status: str,
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

        saved_candidates: list[CrawlCandidate] = []
        for payload in candidates:
            candidate = CrawlCandidate(
                job_id=chunk.job_id,
                name=payload.name.strip(),
                email=_clean(payload.email),
                title=_clean(payload.title),
                university=_clean(payload.university) or job.university,
                school=_clean(payload.school) or job.school,
                department=_clean(payload.department),
                research_direction=_clean(payload.research_direction),
                recent_papers=payload.recent_papers,
                profile_url=_clean(payload.profile_url),
                source_url=_clean(payload.source_url) or chunk.source_url,
                confidence=payload.confidence,
                field_confidence=payload.field_confidence,
                evidence=payload.evidence,
                source_chunk_id=chunk.chunk_id,
                source_kind="chunk",
                boundary_risk=payload.boundary_risk,
                identity_key=payload.identity_key,
                merge_history=payload.merge_history,
                field_sources=payload.field_sources,
                conflicts=payload.conflicts,
            )
            session.add(candidate)
            saved_candidates.append(candidate)
        await session.flush()

        enrichment_count = 0
        for candidate in saved_candidates:
            if not candidate_needs_enrichment(candidate):
                continue
            exists = await session.scalar(
                select(CrawlCandidateEnrichmentTask.id).where(
                    CrawlCandidateEnrichmentTask.job_id == chunk.job_id,
                    CrawlCandidateEnrichmentTask.candidate_id == candidate.id,
                )
            )
            if exists is not None:
                continue
            session.add(
                CrawlCandidateEnrichmentTask(
                    job_id=chunk.job_id,
                    candidate_id=candidate.id,
                    status=CrawlCandidateEnrichmentTaskStatus.PENDING.value,
                )
            )
            enrichment_count += 1

        url_count = 0
        seen_urls: set[str] = set()
        for url in discovered_urls:
            normalized = normalize_url(url, base_url=chunk.source_url)
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

        chunk.status = _normalize_chunk_status(chunk_status)
        chunk.worker_id = None
        chunk.claimed_at = None
        chunk.lease_expires_at = None
        await session.commit()
        return {
            "status": "saved",
            "saved_count": len(saved_candidates),
            "url_count": url_count,
            "enrichment_count": enrichment_count,
        }

def _lease_expired(lease_expires_at: datetime | None) -> bool:
    if lease_expires_at is None:
        return False
    return as_utc_aware(lease_expires_at) <= utc_now()


def _normalize_chunk_status(chunk_status: str) -> str:
    if chunk_status in {CrawlPageChunkStatus.COMPLETED.value, CrawlPageChunkStatus.NO_CANDIDATES.value, CrawlPageChunkStatus.SPLIT_REQUIRED.value}:
        return chunk_status
    return CrawlPageChunkStatus.COMPLETED.value


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
