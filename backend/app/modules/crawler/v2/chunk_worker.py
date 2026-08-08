from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from app.core.time import as_utc_aware, utc_now

from sqlalchemy import select

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
    CrawlCandidate,
    CrawlJob,
    CrawlWorkerKind,
    CrawlPageChunk,
    CrawlPageChunkStatus,
)
from ..pages.tools import CrawlToolContext, ProfessorCandidatePayload, save_candidate_payloads_shared
from ..pages.chunk_runtime import split_page_chunk_for_retry
from ..pages.debug import append_crawler_v2_debug_event
from .retry import mark_crawler_v2_failed
from .scheduler import ensure_job_active
from .token_usage import record_crawler_v2_token_usage
from .lease import CrawlerV2ClaimFence, fence_crawler_v2_claim
from .models import CrawlerV2WorkKind
from .profile_url_policy import extract_normalized_markdown_links
from .url_utils import is_same_domain, normalize_url
from ..jobs.runs import extract_token_usage_from_llm_response
from ..jobs.llm_context import resolve_crawl_job_runtime_profile
from app.modules.llm.public import (
    LLMRuntimeAdaptation,
    ensure_llm_runtime_adaptation,
    format_llm_runtime_error_for_user,
)
from ..llm.structured_output import (
    CANDIDATE_WIRE_PROMPT_CONTRACT,
    V2ChunkWirePayload,
    professor_candidate_wire_to_dict,
    request_crawler_structured_completion,
)

MAX_CANDIDATES_PER_CHUNK_RESULT = 10


async def invoke_v2_chunk_agent(
    llm_profile: Any,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    university: str,
    school: str,
    source_url: str,
    chunk_content: str,
    adaptation: LLMRuntimeAdaptation,
) -> tuple[dict[str, Any], dict[str, int | None] | None, str]:
    prompt = build_v2_chunk_prompt(
        university=university,
        school=school,
        source_url=source_url,
        chunk_content=chunk_content,
    )
    completion, wire_payload, _structured_mode = await request_crawler_structured_completion(
        session_factory,
        llm_profile,
        adaptation,
        prompt=prompt,
        result_model=V2ChunkWirePayload,
    )
    payload = {
        "candidate_count": wire_payload.candidate_count,
        "candidates": [
            professor_candidate_wire_to_dict(candidate)
            for candidate in wire_payload.candidates
        ],
    }
    usage = extract_token_usage_from_llm_response(completion)
    return payload, usage, completion.content


def build_v2_chunk_prompt(*, university: str, school: str, source_url: str, chunk_content: str) -> str:
    return (
        "你是 AutoEmailSender 的 V2 Chunk Worker。只处理当前 chunk，不要请求新页面，不要引用历史对话。\n"
        "只输出一个 JSON 对象，字段为 candidate_count、candidates。不要输出解释文字，也不能输出 chunk_status 或任何 URL 扩展决策。\n"
        "候选必须来自当前 chunk 内的明确证据，不能猜测，不能翻译、音译或拼音化页面原文。\n"
        "候选判定优先级：当前 chunk 中 Markdown 链接形如 [姓名](http/https URL)，且周围内容明确表示某一人员的详情或资料时，这就是明确的姓名 + profile_url 候选证据；不得仅因 URL 路径、文件名或参数看起来普通而排除。\n"
        "candidates 中的 name 只填写这个人的姓名本身。链接文字可能把姓名与职称、岗位或介绍连在一起；这些内容不能写进 name。\n"
        "即使没有 email、title、department、research_direction，只要有姓名 + profile_url，也必须视为候选，不是 no_candidates。\n"
        "candidate_count 必须是非负整数，禁止浮点数、字符串和布尔值。\n"
        "candidate_count 是当前 chunk 内明确候选的总数。candidate_count 为 0 时 candidates 必须为空；1 到 10 时 candidates 数组长度必须与 candidate_count 相等；candidate_count 必须为 11 或更大时 candidates 必须为空。\n"
        "如果当前 chunk 内姓名 + profile_url 候选超过 10 个，candidate_count 必须为 11 或更大，不要输出前 10 个，也不要返回 0。\n"
        "除明确超过 10 个的情况外，先完整填写 candidates，最后把 candidates 数组的实际长度写入 candidate_count。\n"
        "页面较长、分类复杂、分页导航、详情页链接、不确定或刚好 10 个候选，都不能把 candidate_count 填为 11 或更大。\n"
        "candidate_count 只数当前 chunk 中逐条出现、能够分别指出姓名的人员；页面中表示整份名单或分页规模的汇总数字一律不参与计数。\n"
        "无法在当前 chunk 中指出第 11 个不同人员时，candidate_count 不得大于 10。\n"
        "缺少 email 且缺少 profile_url 的候选不可提交；但有姓名 + profile_url 的候选即使缺少 email 也可提交。\n"
        "no_candidates 只允许在当前 chunk 内没有任何姓名+邮箱、姓名+profile_url、教师卡片或教师表格行时使用。\n"
        "当前 chunk 中 Markdown 链接形如 [导师名](URL) 且与候选姓名匹配时，必须把 URL 写入该候选 profile_url。\n"
        "同一条人员卡片或表格行内的 [无文字链接](URL) 可以作为该人员的 profile_url；不得跨卡片或跨表格行配对，也不得把导航或装饰链接当作个人页。\n"
        "导师个人主页链接只属于候选 profile_url；当前 Worker 不发现、不选择、也不访问任何后续页面。\n"
        f"{CANDIDATE_WIRE_PROMPT_CONTRACT}\n"
        "输出示例（正常保存）：\n"
        '{"candidate_count": 1, "candidates": [{"name": "张三", "email": "zhang@example.edu", "title": "教授", "university": "示例大学", "school": "计算机学院", "department": "", "research_direction": "软件工程", "recent_papers": [], "profile_url": "https://example.edu/zhang.html", "source_url": "https://example.edu/faculty", "confidence": 0.9, "field_confidence": [{"field": "name", "confidence": 0.95}, {"field": "email", "confidence": 0.9}, {"field": "profile_url", "confidence": 0.95}], "evidence_summary": "当前 chunk 中姓名链接和邮箱明确出现"}]}\n'
        "输出示例（当前 chunk 明确超过 10 个候选）：\n"
        '{"candidate_count": 11, "candidates": []}\n'
        "输出示例（无候选）：\n"
        '{"candidate_count": 0, "candidates": []}\n'
        f"学校：{university}\n"
        f"学院/单位：{school}\n"
        f"来源 URL：{source_url}\n"
        "当前 chunk 正文：\n"
        f"{chunk_content}"
    )


def _validate_chunk_agent_payload(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Chunk Worker 返回结构不是 JSON 对象")
    required = {"candidate_count", "candidates"}
    missing = required.difference(payload)
    if missing:
        raise ValueError(f"Chunk Worker 返回缺少字段：{', '.join(sorted(missing))}")
    candidate_count = payload["candidate_count"]
    if isinstance(candidate_count, bool) or not isinstance(candidate_count, int) or candidate_count < 0:
        raise ValueError("Chunk Worker 返回的 candidate_count 必须是大于等于 0 的整数")
    candidates = payload["candidates"]
    if not isinstance(candidates, list):
        raise ValueError("Chunk Worker 返回的 candidates 必须是数组")
    if not candidates and 0 < candidate_count <= MAX_CANDIDATES_PER_CHUNK_RESULT:
        raise ValueError("Chunk Worker 返回的 candidate_count 与 candidates 数量不一致")
    return payload


def _resolve_effective_candidate_count(payload: dict[str, Any]) -> tuple[int, str | None]:
    reported_count = payload["candidate_count"]
    payload_count = len(payload["candidates"])

    if reported_count > MAX_CANDIDATES_PER_CHUNK_RESULT or payload_count > MAX_CANDIDATES_PER_CHUNK_RESULT:
        warning = None
        if payload_count:
            warning = "candidate_count_candidates_conflict"
        return max(reported_count, MAX_CANDIDATES_PER_CHUNK_RESULT + 1), warning

    if payload_count:
        warning = None
        if payload_count != reported_count:
            warning = "candidate_count_normalized_to_candidates"
        return payload_count, warning

    if reported_count:
        raise ValueError("Chunk Worker 返回的 candidate_count 与 candidates 数量不一致")
    return 0, None


def _parse_candidate_payloads(
    raw_candidates: Sequence[object],
    *,
    tolerate_invalid: bool,
) -> tuple[list[ProfessorCandidatePayload], int]:
    candidates: list[ProfessorCandidatePayload] = []
    invalid_count = 0
    for item in raw_candidates:
        try:
            candidates.append(ProfessorCandidatePayload.model_validate(item))
        except (TypeError, ValueError):
            if not tolerate_invalid:
                raise
            invalid_count += 1
    return candidates, invalid_count


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
            adaptation = await ensure_llm_runtime_adaptation(session, llm_profile)
            await session.commit()

        chunk_agent_result = await invoke_v2_chunk_agent(
            llm_profile,
            session_factory=session_factory,
            university=job.university,
            school=job.school,
            source_url=chunk.source_url,
            chunk_content=chunk.content,
            adaptation=adaptation,
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
                claim=CrawlerV2ClaimFence(
                    kind=CrawlerV2WorkKind.CHUNK,
                    work_item_id=chunk_id,
                    worker_id=worker_id,
                ),
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
        reported_candidate_count = payload["candidate_count"]
        candidate_payload_count = len(payload["candidates"])
        candidate_count, contract_warning = _resolve_effective_candidate_count(payload)
        derived_chunk_status = _derive_chunk_status(candidate_count)
        candidates, invalid_candidate_payload_count = _parse_candidate_payloads(
            payload["candidates"],
            tolerate_invalid=derived_chunk_status == CrawlPageChunkStatus.SPLIT_REQUIRED.value,
        )
        save_result = await complete_current_chunk(
            session_factory,
            chunk_id=chunk_id,
            worker_id=worker_id,
            candidates=candidates,
            candidate_count=candidate_count,
        )
        save_result["candidate_count"] = candidate_count
        save_result["reported_candidate_count"] = reported_candidate_count
        save_result["candidate_payload_count"] = candidate_payload_count
        if invalid_candidate_payload_count:
            invalid_warning = f"invalid_candidate_payloads_ignored:{invalid_candidate_payload_count}"
            contract_warning = f"{contract_warning};{invalid_warning}" if contract_warning else invalid_warning
        if contract_warning:
            save_result["contract_warning"] = contract_warning
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
    return await resolve_crawl_job_runtime_profile(session, job)



def _normalize_person_name_for_link_match(value: str | None) -> str:
    return "".join(str(value or "").split()).casefold()


def _extract_markdown_profile_links(chunk_content: str, *, base_url: str) -> dict[str, str]:
    links: dict[str, str] = {}
    for label, normalized in extract_normalized_markdown_links(
        chunk_content,
        base_url=base_url,
    ):
        key = _normalize_person_name_for_link_match(label)
        if not key:
            continue
        links.setdefault(key, normalized)
    return links


def _fill_candidate_profile_urls_from_chunk(
    candidates: Sequence[ProfessorCandidatePayload],
    *,
    chunk_content: str,
    source_url: str,
) -> list[ProfessorCandidatePayload]:
    link_map = _extract_markdown_profile_links(chunk_content, base_url=source_url)
    explicit_link_urls = set(link_map.values())
    explicit_link_urls.update(
        link_url
        for _label, link_url in extract_normalized_markdown_links(
            chunk_content,
            base_url=source_url,
        )
    )
    filled: list[ProfessorCandidatePayload] = []
    for candidate in candidates:
        if candidate.profile_url:
            normalized_profile_url = normalize_url(candidate.profile_url, base_url=source_url)
            if (
                is_same_domain(normalized_profile_url, source_url)
                or normalized_profile_url in explicit_link_urls
            ):
                filled.append(candidate)
                continue
            data = candidate.model_dump()
            data["profile_url"] = None
            filled.append(ProfessorCandidatePayload.model_validate(data))
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
    candidate_count: int,
    discovered_urls: Sequence[str] = (),
) -> dict[str, int | str]:
    # Kept as a compatibility-only argument for older callers. Page expansion is
    # now decided exclusively by the page routing worker.
    _ = discovered_urls
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
        job_id = chunk.job_id
        chunk_content = chunk.content
        source_url = chunk.source_url
        start_url = job.start_url
        university = job.university
        school = job.school

    derived_chunk_status = _derive_chunk_status(candidate_count)
    claim_fence = CrawlerV2ClaimFence(
        kind=CrawlerV2WorkKind.CHUNK,
        work_item_id=chunk_id,
        worker_id=worker_id,
    )
    ctx = CrawlToolContext(
        job_id=job_id,
        start_url=start_url,
        university=university,
        school=school,
        session_factory=session_factory,
        claim_fence=claim_fence,
    )
    enriched_candidates = _fill_candidate_profile_urls_from_chunk(
        candidates,
        chunk_content=chunk_content,
        source_url=source_url,
    )
    save_result = await save_candidate_payloads_shared(ctx, enriched_candidates)
    enrichment_count = 0
    url_count = 0

    if derived_chunk_status == CrawlPageChunkStatus.SPLIT_REQUIRED.value:
        split_result = await split_page_chunk_for_retry(
            session_factory,
            job_id=job_id,
            chunk_pk=chunk_id,
            reason="candidate_count_exceeded",
            claim_fence=claim_fence,
        )
        return {
            "status": split_result["status"],
            "saved_count": save_result["saved_count"],
            "url_count": 0,
            "enrichment_count": 0,
            "rejected_count": save_result["rejected_count"],
            "merged_count": save_result["merged_count"],
            "skipped_duplicate_count": save_result["skipped_duplicate_count"],
            "child_count": split_result["child_count"],
            "derived_chunk_status": derived_chunk_status,
        }

    async with session_factory() as session:
        if not await fence_crawler_v2_claim(
            session,
            claim_fence,
        ):
            await session.rollback()
            return {
                "status": "claim_lost",
                "saved_count": save_result["saved_count"],
                "url_count": 0,
                "enrichment_count": 0,
            }
        chunk = await session.get(CrawlPageChunk, chunk_id)
        if chunk is None:
            await session.rollback()
            return {
                "status": "missing",
                "saved_count": save_result["saved_count"],
                "url_count": 0,
                "enrichment_count": 0,
            }
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
