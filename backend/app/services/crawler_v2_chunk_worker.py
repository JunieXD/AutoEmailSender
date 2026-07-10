from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
import re
from typing import Any
from urllib.parse import urlsplit

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
from pydantic import BaseModel, Field

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
_CJK_PERSON_NAME_PATTERN = re.compile(r"[\u3400-\u9fff]{2,6}(?:·[\u3400-\u9fff]{1,6})?")
_COMMON_CHINESE_SURNAMES = frozenset(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄穆萧尹姚邵汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯管卢莫房解应宗丁宣邓洪包左石崔吉龚程邢荣翁羊惠家储段焦侯秋宁甘武刘龙叶白蒲卓谭冉牛边尚温庄柴瞿阎慕连艾容向古易廖耿满匡文寇广欧利蔚师聂辛阚简饶曾沙关查游权盖益桓班付辜陆涂肖阳"
)
_COMPOUND_CHINESE_SURNAMES = ("欧阳", "司马", "上官", "诸葛", "夏侯", "东方", "皇甫", "尉迟", "公孙", "慕容", "司徒", "司空", "长孙", "宇文", "令狐")
_NON_PERSON_LABEL_MARKERS = (
    "首页", "更多", "学院", "学校", "学科", "专业", "教师", "教授", "师资", "团队", "中心", "研究所", "名录", "列表",
    "主页", "详情", "动态", "信息", "招生", "新闻", "介绍", "简介", "科研", "教学", "学术", "个人",
    "论坛", "成果", "展示", "路线", "风范", "展览", "通知", "公告", "活动", "项目", "课程", "下载", "联系",
)
_HUST_DIRECTORY_HOSTS = {"cs.hust.edu.cn", "www.cs.hust.edu.cn"}
_HUST_PROFILE_HOST = "faculty.hust.edu.cn"
_HUST_SHORT_PROFILE_PATHS = {"/xex/"}


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
        "只输出一个 JSON 对象，字段为 candidates、discovered_urls、chunk_status。不要输出解释文字。\n"
        "chunk_status 只能是 completed、no_candidates 或 too_many_candidates。\n"
        "候选必须来自当前 chunk 内的明确证据，不能猜测，不能翻译、音译或拼音化页面原文。\n"
        "候选判定优先级：当前 chunk 中 Markdown 链接形如 [姓名](http/https URL)，且链接文本像人名、URL 像个人主页时，这就是明确的姓名 + profile_url 候选证据。\n"
        "即使没有 email、title、department、research_direction，只要有姓名 + profile_url，也必须视为候选，不是 no_candidates。\n"
        "candidates 最多 10 个候选；只有当前 chunk 正文内明确可见超过 10 个导师候选时，chunk_status 才能是 too_many_candidates，且 candidates 必须为空。\n"
        "如果当前 chunk 内姓名 + profile_url 候选超过 10 个，必须返回 too_many_candidates，不要输出前 10 个，也不要返回 no_candidates。\n"
        "页面较长、分类复杂、分页导航、详情页链接、不确定或刚好 10 个候选，都不能使用 too_many_candidates。\n"
        "缺少 email 且缺少 profile_url 的候选不可提交；但有姓名 + profile_url 的候选即使缺少 email 也可提交。\n"
        "no_candidates 只允许在当前 chunk 内没有任何姓名+邮箱、姓名+profile_url、教师卡片或教师表格行时使用。\n"
        "当前 chunk 中 Markdown 链接形如 [导师名](URL) 且与候选姓名匹配时，必须把 URL 写入该候选 profile_url。\n"
        "导师个人主页链接属于候选 profile_url，不能放入 discovered_urls。\n"
        "discovered_urls 只放候选列表页、分页页、教师目录页等继续抓取入口。\n"
        "每个候选字段使用英文键：name、email、title、university、school、department、research_direction、recent_papers、profile_url、source_url、confidence、field_confidence、evidence。\n"
        "confidence 和 field_confidence 必须是 0 到 1 的数字；evidence 只写简短摘要，不复制大段原文。\n"
        "输出示例（正常保存）：\n"
        '{"chunk_status": "completed", "candidates": [{"name": "张三", "email": "zhang@example.edu", "title": "教授", "university": "示例大学", "school": "计算机学院", "department": "", "research_direction": "软件工程", "recent_papers": [], "profile_url": "https://example.edu/zhang.html", "source_url": "https://example.edu/faculty", "confidence": 0.9, "field_confidence": {"name": 0.95, "email": 0.9, "profile_url": 0.95}, "evidence": {"summary": "当前 chunk 中姓名链接和邮箱明确出现"}}], "discovered_urls": []}\n'
        "输出示例（当前 chunk 明确超过 10 个候选）：\n"
        '{"chunk_status": "too_many_candidates", "candidates": [], "discovered_urls": []}\n'
        "输出示例（无候选）：\n"
        '{"chunk_status": "no_candidates", "candidates": [], "discovered_urls": []}\n'
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
    required = {"candidates", "discovered_urls", "chunk_status"}
    missing = required.difference(payload)
    if missing:
        raise ValueError(f"Chunk Worker 返回缺少字段：{', '.join(sorted(missing))}")
    chunk_status = str(payload.get("chunk_status") or "")
    if chunk_status == CrawlPageChunkStatus.SPLIT_REQUIRED.value:
        raise ValueError("Chunk Worker 返回了已废弃的 split_required；只有当前 chunk 明确超过 10 个候选时才能返回 too_many_candidates")
    if chunk_status not in {CrawlPageChunkStatus.COMPLETED.value, CrawlPageChunkStatus.NO_CANDIDATES.value, "too_many_candidates"}:
        raise ValueError(f"Chunk Worker 返回了不支持的 chunk_status：{chunk_status}")
    return payload

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
        candidates = [ProfessorCandidatePayload.model_validate(item) for item in payload.get("candidates", [])]
        candidates, chunk_status, recovered_count = _recover_hust_directory_candidates(
            candidates,
            chunk_status=str(payload.get("chunk_status") or "completed"),
            chunk_content=chunk.content,
            source_url=chunk.source_url,
        )
        save_result = await complete_current_chunk(
            session_factory,
            chunk_id=chunk_id,
            worker_id=worker_id,
            candidates=candidates,
            discovered_urls=[str(url) for url in payload.get("discovered_urls", [])],
            chunk_status=chunk_status,
        )
        effective_payload = {
            **payload,
            "candidates": [candidate.model_dump() for candidate in candidates],
            "chunk_status": chunk_status,
        }
        append_crawler_v2_debug_event(
            job.id,
            worker_kind="chunk",
            event_name="chunk_completed",
            work_item_id=chunk_id,
            payload={
                "chunk_id": chunk.chunk_id,
                "source_url": chunk.source_url,
                "parsed_payload": effective_payload,
                "recovered_profile_link_count": recovered_count,
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
        normalized = _normalize_url_or_none(match.group(2), base_url=base_url)
        if normalized is not None:
            links.setdefault(key, normalized)
    return links


def _recover_hust_directory_candidates(
    candidates: Sequence[ProfessorCandidatePayload],
    *,
    chunk_status: str,
    chunk_content: str,
    source_url: str,
) -> tuple[list[ProfessorCandidatePayload], str, int]:
    evident = _extract_hust_directory_candidates(chunk_content, base_url=source_url)
    if len(evident) > MAX_CANDIDATES_PER_CHUNK_RESULT:
        return [], "too_many_candidates", 0

    merged = list(candidates)
    seen_urls = {
        normalized
        for candidate in merged
        if candidate.profile_url
        if (normalized := _normalize_url_or_none(candidate.profile_url, base_url=source_url))
    }
    recovered_count = 0
    for candidate in evident:
        normalized_name = _normalize_person_name_for_link_match(candidate.name)
        normalized_url = _normalize_url_or_none(candidate.profile_url or "", base_url=source_url)
        if normalized_url is None:
            continue
        if normalized_url in seen_urls:
            continue
        matching_without_url = next(
            (
                candidate
                for candidate in merged
                if _normalize_person_name_for_link_match(candidate.name) == normalized_name
                and not candidate.profile_url
            ),
            None,
        )
        if matching_without_url is not None:
            matching_without_url.profile_url = normalized_url
            seen_urls.add(normalized_url)
            recovered_count += 1
            continue
        merged.append(candidate)
        seen_urls.add(normalized_url)
        recovered_count += 1

    effective_status = chunk_status
    if merged and effective_status == CrawlPageChunkStatus.NO_CANDIDATES.value:
        effective_status = CrawlPageChunkStatus.COMPLETED.value
    return merged, effective_status, recovered_count


def _extract_hust_directory_candidates(
    chunk_content: str,
    *,
    base_url: str,
) -> list[ProfessorCandidatePayload]:
    if not _is_hust_teacher_directory_url(base_url):
        return []
    candidates: list[ProfessorCandidatePayload] = []
    seen_urls: set[str] = set()
    for match in _MARKDOWN_LINK_PATTERN.finditer(chunk_content):
        name = "".join(match.group(1).split())
        if not _looks_like_person_name(name):
            continue
        profile_url = _normalize_url_or_none(match.group(2), base_url=base_url)
        if profile_url is None or not _looks_like_hust_profile_page_url(profile_url):
            continue
        if profile_url in seen_urls:
            continue
        seen_urls.add(profile_url)
        candidates.append(
            ProfessorCandidatePayload(
                name=name,
                profile_url=profile_url,
                source_url=base_url,
                confidence=0.9,
                field_confidence={"name": 0.95, "profile_url": 0.95},
                evidence={"summary": "页面片段中存在明确的姓名与教师主页链接"},
            )
        )
    return candidates


def _looks_like_person_name(value: str) -> bool:
    if not _CJK_PERSON_NAME_PATTERN.fullmatch(value):
        return False
    compact = value.replace("·", "")
    if len(compact) < 2 or len(compact) > 4:
        return False
    if value.endswith("班") or any(marker in value for marker in _NON_PERSON_LABEL_MARKERS):
        return False
    return value[0] in _COMMON_CHINESE_SURNAMES or value.startswith(_COMPOUND_CHINESE_SURNAMES)


def _looks_like_hust_profile_page_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or hostname.casefold() != _HUST_PROFILE_HOST
        or parsed.path in {"", "/"}
    ):
        return False
    path = f"/{parsed.path.strip('/').casefold()}/"
    return "/zh_cn/" in path or path in _HUST_SHORT_PROFILE_PATHS


def _is_hust_teacher_directory_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme in {"http", "https"}
        and hostname
        and hostname.casefold() in _HUST_DIRECTORY_HOSTS
        and parsed.path.casefold().startswith("/szdw/jsml/")
    )


def _normalize_url_or_none(value: str, *, base_url: str) -> str | None:
    try:
        return normalize_url(value, base_url=base_url)
    except ValueError:
        return None


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

        if chunk_status == "too_many_candidates" or len(candidates) > MAX_CANDIDATES_PER_CHUNK_RESULT:
            reason = "candidate_count_exceeded" if len(candidates) > MAX_CANDIDATES_PER_CHUNK_RESULT else "too_many_candidates"
            split_result = await split_page_chunk_for_retry(
                session_factory,
                job_id=chunk.job_id,
                chunk_pk=chunk.id,
                reason=reason,
            )
            return {
                "status": split_result["status"],
                "saved_count": 0,
                "url_count": 0,
                "enrichment_count": 0,
                "rejected_count": 0,
                "child_count": split_result["child_count"],
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

        chunk.status = _normalize_chunk_status(chunk_status)
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
        }


def _lease_expired(lease_expires_at: datetime | None) -> bool:
    if lease_expires_at is None:
        return False
    return as_utc_aware(lease_expires_at) <= utc_now()


def _normalize_chunk_status(chunk_status: str) -> str:
    if chunk_status in {CrawlPageChunkStatus.COMPLETED.value, CrawlPageChunkStatus.NO_CANDIDATES.value}:
        return chunk_status
    return CrawlPageChunkStatus.COMPLETED.value


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
