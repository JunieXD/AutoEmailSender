from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Iterable, Sequence
import json
import re
import time
from datetime import datetime, timedelta

from app.core.time import as_utc_aware, utc_now

from sqlalchemy import select

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
    CrawlCandidate,
    CrawlCandidateEnrichmentTask,
    CrawlCandidateEnrichmentTaskStatus,
    CrawlCandidateReviewStatus,
    CrawlJob,
    CrawlJobKind,
    CrawlJobStatus,
    CrawlPage,
    CrawlPageChunk,
    CrawlWorkerKind,
    LLMProfile,
)
from ..pages.tools import (
    CandidateEnrichmentPayload,
    CrawlToolContext,
    MAX_TEXT_CHARS,
    PageSnapshot,
    build_candidate_enrichment_prompt,
    crawl_page_with_browser_fallback,
    looks_like_unavailable_profile_page,
    profile_text_has_meaningful_content,
    validate_safe_public_crawl_url,
)
from ..jobs.llm_context import resolve_crawl_job_runtime_profile
from ..pages.debug import append_crawler_v2_debug_event
from .profile_url_policy import (
    CandidateProfileUrlPolicyError,
    has_explicit_markdown_link,
    normalize_profile_url,
)
from .retry import mark_crawler_v2_failed
from .profile_text_cache import profile_text_cache
from .scheduler import ensure_job_active
from .token_usage import record_crawler_v2_token_usage
from .lease import CrawlerV2ClaimFence, fence_crawler_v2_claim
from .models import CrawlerV2WorkKind
from .url_utils import is_same_domain
from ..jobs.runs import extract_token_usage_from_llm_response
from app.modules.llm.public import LLMRuntimeError, ensure_llm_runtime_adaptation
from ..llm.structured_output import (
    CandidateEmailSelectionWirePayload,
    CandidateEnrichmentWirePayload,
    ProfileLinkSelectionWirePayload,
    request_crawler_structured_completion,
)
from app.services.operation_logs import record_operation_log, sanitize_user_visible_error
from app.modules.professors.public import (
    MISSING_PROFILE_URL_SKIP_REASON,
    NO_NEW_INFORMATION_SKIP_REASON,
    apply_enrichment_to_professor,
)
from app.modules.crawler.candidate_identity import (
    apply_candidate_enrichment_values,
    candidate_identity_values,
    consolidate_candidate_identity,
    rebuild_candidate_identity_keys,
)
from app.modules.professors.public import (
    is_valid_professor_email,
    normalize_professor_email,
)
from .native_ocr import extract_ocr_email_evidence
from .profile_fallbacks import (
    EmailEvidence,
    ProfileLinkEvidence,
    extract_email_evidence,
    extract_profile_link_evidence,
)
from .profile_documents import (
    extract_primary_embedded_profile_pdf_text,
    merge_profile_text_with_embedded_pdf,
)


_PROFILE_TEXT_CACHE = profile_text_cache
_PROFILE_TEXT_DATABASE_CACHE_TTL = timedelta(hours=1)
_PROFILE_CHILD_SNAPSHOT_CACHE_TTL_SECONDS = 60 * 60
_PROFILE_CHILD_SNAPSHOT_CACHE_MAX_ENTRIES = 128
_PROFILE_CHILD_SNAPSHOT_CACHE_MAX_CHARACTERS = 16 * 1024 * 1024
_MAX_EMAIL_EVIDENCE_ITEMS = 16
_MAX_PROFILE_LINK_EVIDENCE_ITEMS = 40
_HTML_TAG_REMNANT_PATTERN = re.compile(
    r"</?(?:a|div|li|nav|ol|p|span|table|tbody|td|th|tr|ul)\b[^>]*>",
    re.IGNORECASE,
)
_ACTIVE_JOB_STATUSES = {
    CrawlJobStatus.QUEUED.value,
    CrawlJobStatus.RUNNING.value,
}
_TERMINAL_ENRICHMENT_TASK_STATUSES = {
    CrawlCandidateEnrichmentTaskStatus.SUCCEEDED.value,
    CrawlCandidateEnrichmentTaskStatus.SKIPPED.value,
    CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value,
    CrawlCandidateEnrichmentTaskStatus.CANCELED.value,
}
_TERMINAL_JOB_STATUSES = {
    CrawlJobStatus.NEEDS_REVIEW.value,
    CrawlJobStatus.PARTIALLY_COMPLETED.value,
    CrawlJobStatus.COMPLETED.value,
    CrawlJobStatus.FAILED.value,
    CrawlJobStatus.CANCELED.value,
}


class CandidateProfileUnavailableError(ValueError):
    pass


class ProfileChildSnapshotCache:
    def __init__(self) -> None:
        self._entries: OrderedDict[
            tuple[object, str, int, str],
            tuple[float, int, PageSnapshot],
        ] = OrderedDict()
        self._total_characters = 0

    def get(self, key: tuple[object, str, int, str]) -> PageSnapshot | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        stored_at, size, snapshot = entry
        if time.monotonic() - stored_at >= _PROFILE_CHILD_SNAPSHOT_CACHE_TTL_SECONDS:
            self._entries.pop(key, None)
            self._total_characters -= size
            return None
        self._entries.move_to_end(key)
        return snapshot.model_copy(deep=True)

    def put(self, key: tuple[object, str, int, str], snapshot: PageSnapshot) -> None:
        size = len(snapshot.text or "") + len(snapshot.html or "")
        if size > _PROFILE_CHILD_SNAPSHOT_CACHE_MAX_CHARACTERS:
            return
        previous = self._entries.pop(key, None)
        if previous is not None:
            self._total_characters -= previous[1]
        while self._entries and (
            len(self._entries) >= _PROFILE_CHILD_SNAPSHOT_CACHE_MAX_ENTRIES
            or self._total_characters + size
            > _PROFILE_CHILD_SNAPSHOT_CACHE_MAX_CHARACTERS
        ):
            _, (_, evicted_size, _) = self._entries.popitem(last=False)
            self._total_characters -= evicted_size
        self._entries[key] = (time.monotonic(), size, snapshot.model_copy(deep=True))
        self._total_characters += size

    def clear(self) -> None:
        self._entries.clear()
        self._total_characters = 0


_PROFILE_CHILD_SNAPSHOT_CACHE = ProfileChildSnapshotCache()
_PROFILE_CHILD_SNAPSHOT_INFLIGHT: dict[
    tuple[int, object, str, int, str],
    asyncio.Task[PageSnapshot],
] = {}


async def run_crawler_v2_enrichment_worker_once(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    task_id: int,
    worker_id: str,
) -> int:
    async with session_factory() as session:
        task = await session.get(CrawlCandidateEnrichmentTask, task_id)
        if task is None or not _enrichment_task_owned_by_worker(task, worker_id):
            return 0
        if not await ensure_job_active(session, task.job_id):
            return 0
        candidate = await session.get(CrawlCandidate, task.candidate_id)
        if candidate is None:
            job_id = task.job_id
            candidate_id = task.candidate_id
            task.status = CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value
            task.last_error = "candidate_missing"
            task.finished_at = utc_now()
            await session.commit()
            _discard_cached_profile_text(
                session_factory,
                job_id=job_id,
                candidate_id=candidate_id,
            )
            return 1
        job = await session.get(CrawlJob, task.job_id)
        model_name = None
        if job is not None:
            profile = await _resolve_llm_profile(session, job)
            model_name = getattr(profile, "model_name", None) if profile is not None else None
        job_id = task.job_id
        candidate_id = candidate.id
        enrichment_started_at = task.started_at
        if not (candidate.profile_url or "").strip():
            task.status = CrawlCandidateEnrichmentTaskStatus.SKIPPED.value
            task.skip_reason = MISSING_PROFILE_URL_SKIP_REASON.legacy_message
            task.finished_at = utc_now()
            task.worker_id = None
            task.claimed_at = None
            task.lease_expires_at = None
            await session.commit()
            _discard_cached_profile_text(
                session_factory,
                job_id=job_id,
                candidate_id=candidate_id,
            )
            return 1

    try:
        enrichment_result = await enrich_candidate_once_with_usage(
            session_factory,
            candidate_id=candidate_id,
            fresh_after=enrichment_started_at,
        )
        raw_model_text = None
        if isinstance(enrichment_result, tuple):
            if len(enrichment_result) >= 3:
                payload, usage, raw_model_text = enrichment_result[:3]
            else:
                payload, usage = enrichment_result
        else:
            payload = enrichment_result
            usage = None
        if not await _enrichment_task_can_commit(session_factory, task_id=task_id, worker_id=worker_id):
            await _discard_cached_profile_text_if_terminal(
                session_factory,
                task_id=task_id,
                job_id=job_id,
                candidate_id=candidate_id,
            )
            return 0
        append_crawler_v2_debug_event(
            job_id,
            worker_kind="enrichment",
            event_name="llm_response",
            work_item_id=task_id,
            payload={
                "candidate_id": candidate.id,
                "profile_url": candidate.profile_url,
                "raw_payload": payload.model_dump() if hasattr(payload, "model_dump") else payload,
                "raw_model_text": raw_model_text,
                "token_usage": dict(usage) if usage is not None else None,
            },
        )
        if usage is not None:
            await record_crawler_v2_token_usage(
                session_factory,
                job_id=job_id,
                worker_kind=CrawlWorkerKind.ENRICHMENT,
                work_item_id=task_id,
                model_name=model_name,
                input_tokens=usage.get("input_tokens") or 0,
                output_tokens=usage.get("output_tokens") or 0,
                cached_tokens=usage.get("cached_tokens") or 0,
                raw_usage=dict(usage),
                claim=CrawlerV2ClaimFence(
                    kind=CrawlerV2WorkKind.ENRICHMENT,
                    work_item_id=task_id,
                    worker_id=worker_id,
                ),
            )
        async with session_factory() as session:
            if not await fence_crawler_v2_claim(
                session,
                CrawlerV2ClaimFence(
                    kind=CrawlerV2WorkKind.ENRICHMENT,
                    work_item_id=task_id,
                    worker_id=worker_id,
                ),
            ):
                await session.rollback()
                await _discard_cached_profile_text_if_terminal(
                    session_factory,
                    task_id=task_id,
                    job_id=job_id,
                    candidate_id=candidate_id,
                )
                return 0
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            current_candidate = await session.get(CrawlCandidate, candidate_id)
            if task is None or current_candidate is None:
                _discard_cached_profile_text(
                    session_factory,
                    job_id=job_id,
                    candidate_id=candidate_id,
                )
                return 0
            job = await session.get(CrawlJob, task.job_id)
            if (
                not _enrichment_task_owned_by_worker(task, worker_id)
                or job is None
                or job.status not in _ACTIVE_JOB_STATUSES
            ):
                if (
                    task.status in _TERMINAL_ENRICHMENT_TASK_STATUSES
                    or job is None
                    or job.status in _TERMINAL_JOB_STATUSES
                ):
                    _discard_cached_profile_text(
                        session_factory,
                        job_id=task.job_id,
                        candidate_id=task.candidate_id,
                    )
                return 0
            candidate = current_candidate
            removed_profile_identities: tuple[tuple[str, str], ...] = ()
            corrected_profile_fields: list[str] = []
            if payload.page_relation == "mismatched" and candidate.profile_url:
                removed_profile_url = candidate.profile_url
                removed_profile_identities = candidate_identity_values(
                    name=candidate.name,
                    profile_url=removed_profile_url,
                )
                candidate.profile_url = None
                evidence = dict(candidate.evidence or {})
                evidence["profile_url_removed_reason"] = "confirmed_profile_page_mismatch"
                evidence["removed_profile_url"] = removed_profile_url
                candidate.evidence = evidence
                corrected_profile_fields.append("profile_url")
                if (
                    job.job_kind != CrawlJobKind.PROFESSOR_ENRICHMENT.value
                    and _valid_email(candidate.email) is None
                ):
                    candidate.review_status = CrawlCandidateReviewStatus.REJECTED.value
            effective_payload = (
                CandidateEnrichmentPayload(page_relation="mismatched")
                if payload.page_relation == "mismatched"
                else payload
            )
            candidate_enriched_fields = corrected_profile_fields + _apply_enrichment(
                candidate,
                effective_payload,
            )
            if "email" in candidate_enriched_fields or corrected_profile_fields:
                candidate = await rebuild_candidate_identity_keys(
                    session,
                    candidate,
                    exclude_identities=removed_profile_identities,
                )
            else:
                candidate = await consolidate_candidate_identity(session, candidate)
            enriched_fields: list[str] = []
            skip_reason = None
            if job is not None and job.job_kind == CrawlJobKind.PROFESSOR_ENRICHMENT.value:
                enriched_fields, skip_reason = await apply_enrichment_to_professor(
                    session,
                    task=task,
                    candidate=candidate,
                )
                if not enriched_fields and skip_reason is None:
                    skip_reason = NO_NEW_INFORMATION_SKIP_REASON.legacy_message
            else:
                enriched_fields = candidate_enriched_fields
                if not enriched_fields:
                    skip_reason = NO_NEW_INFORMATION_SKIP_REASON.legacy_message
            append_crawler_v2_debug_event(
                task.job_id,
                worker_kind="enrichment",
                event_name="enrichment_completed",
                work_item_id=task_id,
                payload={
                    "candidate_id": candidate.id,
                    "professor_id": task.professor_id,
                    "email": candidate.email,
                    "title": candidate.title,
                    "department": candidate.department,
                    "enriched_fields": enriched_fields,
                    "skip_reason": skip_reason,
                },
            )
            task.status = (
                CrawlCandidateEnrichmentTaskStatus.SKIPPED.value
                if skip_reason is not None
                else CrawlCandidateEnrichmentTaskStatus.SUCCEEDED.value
            )
            task.worker_id = None
            task.claimed_at = None
            task.lease_expires_at = None
            task.last_error = None
            task.skip_reason = skip_reason
            task.enriched_fields = enriched_fields
            task.finished_at = utc_now()
            if skip_reason is None:
                await _append_enrichment_success_event(session, task=task, candidate=candidate)
            else:
                await _append_enrichment_unchanged_event(
                    session,
                    task=task,
                    candidate=candidate,
                    reason=skip_reason,
                )
            terminal_job_id = task.job_id
            terminal_candidate_id = task.candidate_id
            await session.commit()
        _discard_cached_profile_text(
            session_factory,
            job_id=terminal_job_id,
            candidate_id=terminal_candidate_id,
        )
        return 1
    except Exception as exc:
        terminal_cache_identity: tuple[int, int] | None = None
        async with session_factory() as session:
            task = await session.get(CrawlCandidateEnrichmentTask, task_id)
            candidate = await session.get(CrawlCandidate, task.candidate_id) if task is not None else None
            if task is not None and _enrichment_task_owned_by_worker(task, worker_id) and await ensure_job_active(session, task.job_id):
                job = await session.get(CrawlJob, task.job_id)
                error_message = (
                    sanitize_user_visible_error(exc)
                    if job is not None
                    and job.job_kind == CrawlJobKind.PROFESSOR_ENRICHMENT.value
                    else str(exc)
                )
                mark_crawler_v2_failed(
                    task,
                    message=error_message,
                    retryable_status=CrawlCandidateEnrichmentTaskStatus.FAILED_RETRYABLE.value,
                    terminal_status=CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value,
                    max_attempts=(
                        1
                        if isinstance(
                            exc,
                            (
                                CandidateProfileUrlPolicyError,
                                CandidateProfileUnavailableError,
                            ),
                        )
                        else None
                    ),
                )
                if task.status == CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value:
                    task.finished_at = utc_now()
                    terminal_cache_identity = (task.job_id, task.candidate_id)
                await _append_enrichment_failure_event(
                    session,
                    task=task,
                    candidate=candidate,
                    error_message=error_message,
                )
                if job is not None and job.job_kind == CrawlJobKind.PROFESSOR_ENRICHMENT.value:
                    append_crawler_v2_debug_event(
                        task.job_id,
                        worker_kind="enrichment",
                        event_name="information_enrichment_failed",
                        work_item_id=task.id,
                        payload={
                            "candidate_id": task.candidate_id,
                            "professor_id": task.professor_id,
                            "task_status": task.status,
                            "attempt_count": int(task.attempt_count or 0),
                            "error_message": error_message,
                        },
                    )
                    await record_operation_log(
                        session,
                        category="professor_information_enrichment",
                        event_name="professor_information_enrichment.item_failed",
                        level=(
                            "error"
                            if task.status == CrawlCandidateEnrichmentTaskStatus.FAILED_TERMINAL.value
                            else "warning"
                        ),
                        message=error_message,
                        entity_type="professor",
                        entity_id=str(task.professor_id) if task.professor_id is not None else None,
                        metadata={
                            "job_id": task.job_id,
                            "task_id": task.id,
                            "task_status": task.status,
                            "attempt_count": int(task.attempt_count or 0),
                        },
                    )
            await session.commit()
        if terminal_cache_identity is not None:
            terminal_job_id, terminal_candidate_id = terminal_cache_identity
            _discard_cached_profile_text(
                session_factory,
                job_id=terminal_job_id,
                candidate_id=terminal_candidate_id,
            )
        else:
            await _discard_cached_profile_text_if_terminal(
                session_factory,
                task_id=task_id,
                job_id=job_id,
                candidate_id=candidate_id,
            )
        return 1


async def enrich_candidate_once(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    candidate_id: int,
) -> CandidateEnrichmentPayload:
    result = await enrich_candidate_once_with_usage(session_factory, candidate_id=candidate_id)
    return result[0]

async def _enrichment_task_can_commit(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    task_id: int,
    worker_id: str,
) -> bool:
    async with session_factory() as session:
        task = await session.get(CrawlCandidateEnrichmentTask, task_id)
        if task is None or not _enrichment_task_owned_by_worker(task, worker_id):
            return False
        return await ensure_job_active(session, task.job_id)


def _enrichment_task_owned_by_worker(task: CrawlCandidateEnrichmentTask, worker_id: str) -> bool:
    if task.status != CrawlCandidateEnrichmentTaskStatus.PROCESSING.value or task.worker_id != worker_id:
        return False
    if task.lease_expires_at is None:
        return True
    return as_utc_aware(task.lease_expires_at) > utc_now()

async def enrich_candidate_once_with_usage(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    candidate_id: int,
    fresh_after: datetime | None = None,
) -> tuple[CandidateEnrichmentPayload, dict[str, int | None] | None, str | None]:
    async with session_factory() as session:
        candidate = await session.get(CrawlCandidate, candidate_id)
        if candidate is None:
            raise ValueError("candidate_missing")
        job = await session.get(CrawlJob, candidate.job_id)
        if job is None:
            raise ValueError("job_missing")
        profile_url = (candidate.profile_url or "").strip()
        profile_crawl_root = await _resolve_profile_crawl_root(
            session,
            candidate=candidate,
            job=job,
            profile_url=profile_url,
        )
        llm_profile = await _resolve_llm_profile(session, job)
        if llm_profile is None:
            raise ValueError("缺少可用的 LLM Profile")
        adaptation = await ensure_llm_runtime_adaptation(session, llm_profile)
        known_listing_urls: set[str] = set()
        if job.entry_type == "list":
            known_listing_urls = set(
                await session.scalars(
                    select(CrawlPageChunk.source_url).where(
                        CrawlPageChunk.job_id == job.id,
                    )
                )
            )
        await session.commit()
        ctx = CrawlToolContext(
            session_factory=session_factory,
            job_id=job.id,
            university=job.university,
            school=job.school,
            start_url=profile_crawl_root,
            llm_adaptation=adaptation,
            allow_public_dns_fallback=True,
            profile_entry_url=profile_url,
            crawl_run_id=job.current_run_id,
        )
        ctx.known_listing_urls.update(
            normalize_profile_url(url, base_url=profile_crawl_root)
            for url in known_listing_urls
            if url and url.strip()
        )
    page_text = await get_or_fetch_profile_text(
        ctx,
        candidate.id,
        profile_url,
        fresh_after=fresh_after,
    )
    profile_snapshot = ctx.get_cached_page_snapshot(profile_url)
    return await enrich_candidate_profile_with_llm_with_usage(
        ctx,
        llm_profile,
        candidate,
        page_text,
        page_snapshot=profile_snapshot,
    )


async def _resolve_profile_crawl_root(
    session: AsyncSession,
    *,
    candidate: CrawlCandidate,
    job: CrawlJob,
    profile_url: str,
) -> str:
    try:
        validate_safe_public_crawl_url(profile_url)
    except ValueError as exc:
        raise CandidateProfileUrlPolicyError(str(exc)) from exc

    if job.job_kind == CrawlJobKind.PROFESSOR_ENRICHMENT.value:
        return profile_url
    if is_same_domain(profile_url, job.start_url):
        return job.start_url

    chunks = list(
        await session.scalars(
            select(CrawlPageChunk).where(CrawlPageChunk.job_id == candidate.job_id)
        )
    )
    if any(
        has_explicit_markdown_link(
            chunk.content,
            base_url=chunk.source_url,
            target_url=profile_url,
        )
        for chunk in chunks
    ):
        return profile_url

    raise CandidateProfileUrlPolicyError(
        "跨主域导师主页未在来源列表原文中出现，已拒绝补全"
    )

async def enrich_candidate_profile_with_llm_with_usage(
    ctx: CrawlToolContext,
    llm_profile: LLMProfile,
    candidate: CrawlCandidate,
    page_text: str,
    *,
    page_snapshot: PageSnapshot | None = None,
) -> tuple[CandidateEnrichmentPayload, dict[str, int | None] | None, str | None]:
    prompt = build_candidate_enrichment_prompt(candidate, page_text)
    completion, wire_payload, _structured_mode = await request_crawler_structured_completion(
        ctx.session_factory,
        llm_profile,
        ctx.llm_adaptation,
        prompt=prompt,
        result_model=CandidateEnrichmentWirePayload,
    )
    payload = CandidateEnrichmentPayload.model_validate(wire_payload.model_dump())
    payload.page_relation = _guard_page_relation(
        payload.page_relation,
        candidate_name=candidate.name,
        page_text=page_text,
    )
    usage = extract_token_usage_from_llm_response(completion)
    raw_model_texts = [completion.content]

    if payload.page_relation == "mismatched":
        return (
            CandidateEnrichmentPayload(page_relation="mismatched"),
            usage,
            _join_raw_model_texts(raw_model_texts),
        )

    if (
        payload.page_relation != "matched"
        or _valid_email(candidate.email) is not None
        or _valid_email(payload.email) is not None
    ):
        return payload, usage, _join_raw_model_texts(raw_model_texts)

    selected_email, auxiliary_usage, auxiliary_raw = await _select_email_from_evidence(
        ctx,
        llm_profile,
        candidate,
        extract_email_evidence(
            page_text,
            source_url=(candidate.profile_url or "").strip(),
            source_kind="profile_text",
        ),
    )
    usage = _merge_token_usage(usage, auxiliary_usage)
    raw_model_texts.append(auxiliary_raw)
    if selected_email:
        payload.email = selected_email
        return payload, usage, _join_raw_model_texts(raw_model_texts)

    profile_url = (candidate.profile_url or "").strip()
    snapshot = page_snapshot
    if snapshot is None:
        snapshot = await crawl_page_with_browser_fallback(
            ctx,
            profile_url,
            intent="profile",
            force_fetch=True,
        )
    if snapshot.status != "succeeded":
        return payload, usage, _join_raw_model_texts(raw_model_texts)

    selected_email, auxiliary_usage, auxiliary_raw = await _select_email_from_evidence(
        ctx,
        llm_profile,
        candidate,
        await extract_ocr_email_evidence(ctx, snapshot),
    )
    usage = _merge_token_usage(usage, auxiliary_usage)
    raw_model_texts.append(auxiliary_raw)
    if selected_email:
        payload.email = selected_email
        return payload, usage, _join_raw_model_texts(raw_model_texts)

    links = tuple(
        link
        for link in extract_profile_link_evidence(
            snapshot,
            max_links=_MAX_PROFILE_LINK_EVIDENCE_ITEMS * 2,
        )
        if ctx.allows_url(link.url) and not _is_known_listing_url(ctx, link.url)
    )[:_MAX_PROFILE_LINK_EVIDENCE_ITEMS]
    selected_links, auxiliary_usage, auxiliary_raw = await _select_profile_links(
        ctx,
        llm_profile,
        candidate,
        links,
    )
    usage = _merge_token_usage(usage, auxiliary_usage)
    raw_model_texts.append(auxiliary_raw)
    if not selected_links:
        return payload, usage, _join_raw_model_texts(raw_model_texts)

    child_snapshots: list[PageSnapshot] = []
    for link in selected_links:
        child_snapshot = await _fetch_profile_child_snapshot(ctx, link.url)
        if child_snapshot.status == "succeeded":
            child_snapshots.append(child_snapshot)

    child_text_evidence = _deduplicate_email_evidence(
        evidence
        for child_snapshot in child_snapshots
        for evidence in extract_email_evidence(
            child_snapshot.text,
            source_url=child_snapshot.url,
            source_kind="child_text",
        )
    )
    selected_email, auxiliary_usage, auxiliary_raw = await _select_email_from_evidence(
        ctx,
        llm_profile,
        candidate,
        child_text_evidence,
    )
    usage = _merge_token_usage(usage, auxiliary_usage)
    raw_model_texts.append(auxiliary_raw)
    if selected_email:
        payload.email = selected_email
        return payload, usage, _join_raw_model_texts(raw_model_texts)

    child_ocr_evidence: list[EmailEvidence] = []
    for child_snapshot in child_snapshots:
        child_ocr_evidence.extend(await extract_ocr_email_evidence(ctx, child_snapshot))
    selected_email, auxiliary_usage, auxiliary_raw = await _select_email_from_evidence(
        ctx,
        llm_profile,
        candidate,
        _deduplicate_email_evidence(child_ocr_evidence),
    )
    usage = _merge_token_usage(usage, auxiliary_usage)
    raw_model_texts.append(auxiliary_raw)
    if selected_email:
        payload.email = selected_email
    return (
        payload,
        usage,
        _join_raw_model_texts(raw_model_texts),
    )


async def _select_email_from_evidence(
    ctx: CrawlToolContext,
    llm_profile: LLMProfile,
    candidate: CrawlCandidate,
    evidence: Sequence[EmailEvidence],
) -> tuple[str | None, dict[str, int | None] | None, str | None]:
    bounded_evidence = tuple(evidence[:_MAX_EMAIL_EVIDENCE_ITEMS])
    if not bounded_evidence:
        return None, None, None
    prompt = (
        "你只需判断候选邮箱中哪个明确属于当前教师。只能原样选择一个候选邮箱；"
        "学院公共邮箱、行政人员或其他人的邮箱不能选，无法确定就返回空字符串。\n"
        f"当前教师：{candidate.name}；学校：{candidate.university or ''}；学院：{candidate.school or ''}\n"
        "候选邮箱及其页面上下文：\n"
        f"{json.dumps([_email_evidence_dict(item) for item in bounded_evidence], ensure_ascii=False)}\n"
        '只输出 JSON，例如 {"email":"zhang@example.edu"}；不确定时输出 {"email":""}。'
    )
    try:
        completion, selection, _structured_mode = await request_crawler_structured_completion(
            ctx.session_factory,
            llm_profile,
            ctx.llm_adaptation,
            prompt=prompt,
            result_model=CandidateEmailSelectionWirePayload,
        )
    except (LLMRuntimeError, ValueError):
        return None, None, None
    available = {
        normalized: evidence_item.email
        for evidence_item in bounded_evidence
        if (normalized := _valid_email(evidence_item.email)) is not None
    }
    selected = _valid_email(selection.email)
    return (
        available.get(selected) if selected is not None else None,
        extract_token_usage_from_llm_response(completion),
        completion.content,
    )


async def _select_profile_links(
    ctx: CrawlToolContext,
    llm_profile: LLMProfile,
    candidate: CrawlCandidate,
    links: Sequence[ProfileLinkEvidence],
) -> tuple[tuple[ProfileLinkEvidence, ...], dict[str, int | None] | None, str | None]:
    if not links:
        return (), None, None
    prompt = (
        "当前页已确认是这位教师的资料页，但没有找到邮箱。请从真实链接中选择最可能继续显示"
        "同一位教师详细信息或联系方式的链接，最多选 2 个。不要选导航、学院首页、教师名单、"
        "论文站点或无关外部页面；没有合适链接就返回空数组。只返回 link_ids。\n"
        f"当前教师：{candidate.name}\n"
        f"链接：{json.dumps([_profile_link_evidence_dict(link) for link in links], ensure_ascii=False)}\n"
        '只输出 JSON，例如 {"link_ids":[2]}；没有合适链接时输出 {"link_ids":[]}。'
    )
    try:
        completion, selection, _structured_mode = await request_crawler_structured_completion(
            ctx.session_factory,
            llm_profile,
            ctx.llm_adaptation,
            prompt=prompt,
            result_model=ProfileLinkSelectionWirePayload,
        )
    except (LLMRuntimeError, ValueError):
        return (), None, None
    available = {link.link_id: link for link in links}
    selected: list[ProfileLinkEvidence] = []
    seen_ids: set[int] = set()
    for link_id in selection.link_ids:
        if isinstance(link_id, bool) or link_id in seen_ids or link_id not in available:
            continue
        seen_ids.add(link_id)
        selected.append(available[link_id])
        if len(selected) >= 2:
            break
    return (
        tuple(selected),
        extract_token_usage_from_llm_response(completion),
        completion.content,
    )


def _guard_page_relation(
    relation: str,
    *,
    candidate_name: str,
    page_text: str,
) -> str:
    if relation != "mismatched" or not _page_mentions_candidate_name(candidate_name, page_text):
        return relation
    return "uncertain"


def _page_mentions_candidate_name(candidate_name: str, page_text: str) -> bool:
    name = " ".join((candidate_name or "").split()).casefold()
    text = " ".join((page_text or "").split()).casefold()
    if len(name) < 2 or not text:
        return False
    if any(ord(character) > 127 for character in name):
        return name in text
    return re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text) is not None


def _valid_email(value: object) -> str | None:
    normalized = normalize_professor_email(str(value) if value is not None else None)
    return normalized if normalized and is_valid_professor_email(normalized) else None


def _email_evidence_dict(value: EmailEvidence) -> dict[str, str]:
    return {
        "email": value.email,
        "context": value.context,
        "source_url": value.source_url,
        "source_kind": value.source_kind,
    }


def _profile_link_evidence_dict(value: ProfileLinkEvidence) -> dict[str, str | int]:
    return {
        "link_id": value.link_id,
        "url": value.url,
        "label": value.label,
        "context": value.context,
    }


def _is_known_listing_url(ctx: CrawlToolContext, url: str) -> bool:
    normalized_url = normalize_profile_url(url, base_url=ctx.start_url)
    normalized_start_url = normalize_profile_url(ctx.start_url, base_url=ctx.start_url)
    return normalized_url == normalized_start_url or normalized_url in ctx.known_listing_urls


def _profile_child_snapshot_cache_key(
    ctx: CrawlToolContext,
    url: str,
) -> tuple[object, str, int, str]:
    scope_kind, scope_id = ctx.browser_session_scope
    return (
        ctx.session_factory,
        scope_kind,
        scope_id,
        normalize_profile_url(url, base_url=ctx.start_url),
    )


async def _fetch_profile_child_snapshot(
    ctx: CrawlToolContext,
    url: str,
) -> PageSnapshot:
    cache_key = _profile_child_snapshot_cache_key(ctx, url)
    cached = _PROFILE_CHILD_SNAPSHOT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    in_flight_key = (id(asyncio.get_running_loop()), *cache_key)
    task = _PROFILE_CHILD_SNAPSHOT_INFLIGHT.get(in_flight_key)
    if task is None:
        task = asyncio.create_task(
            _load_profile_child_snapshot(ctx, url, cache_key),
        )
        _PROFILE_CHILD_SNAPSHOT_INFLIGHT[in_flight_key] = task
        task.add_done_callback(
            lambda completed, key=in_flight_key: _finish_profile_child_snapshot_fetch(
                key,
                completed,
            )
        )
    snapshot = await asyncio.shield(task)
    return snapshot.model_copy(deep=True)


async def _load_profile_child_snapshot(
    ctx: CrawlToolContext,
    url: str,
    cache_key: tuple[object, str, int, str],
) -> PageSnapshot:
    snapshot = await crawl_page_with_browser_fallback(
        ctx,
        url,
        intent="profile",
        force_fetch=True,
    )
    if snapshot.status == "succeeded":
        _PROFILE_CHILD_SNAPSHOT_CACHE.put(cache_key, snapshot)
    return snapshot


def _finish_profile_child_snapshot_fetch(
    key: tuple[int, object, str, int, str],
    task: asyncio.Task[PageSnapshot],
) -> None:
    if _PROFILE_CHILD_SNAPSHOT_INFLIGHT.get(key) is task:
        _PROFILE_CHILD_SNAPSHOT_INFLIGHT.pop(key, None)
    if not task.cancelled():
        task.exception()


def _deduplicate_email_evidence(values: Iterable[EmailEvidence]) -> tuple[EmailEvidence, ...]:
    deduplicated: list[EmailEvidence] = []
    seen: set[str] = set()
    for value in values:
        if value.email in seen:
            continue
        seen.add(value.email)
        deduplicated.append(value)
    return tuple(deduplicated)


def _merge_token_usage(
    current: dict[str, int | None] | None,
    incoming: dict[str, int | None] | None,
) -> dict[str, int | None] | None:
    if current is None:
        return dict(incoming) if incoming is not None else None
    if incoming is None:
        return current
    merged = dict(current)
    for key, value in incoming.items():
        if isinstance(value, int):
            merged[key] = int(merged.get(key) or 0) + value
        elif key not in merged:
            merged[key] = value
    return merged


def _join_raw_model_texts(values: Sequence[str | None]) -> str | None:
    parts = [value.strip() for value in values if value and value.strip()]
    return "\n\n".join(parts) or None


async def fetch_profile_text(ctx: CrawlToolContext, profile_url: str) -> str:
    snapshot: PageSnapshot = await crawl_page_with_browser_fallback(
        ctx,
        profile_url,
        intent="profile",
        force_fetch=True,
    )
    if looks_like_unavailable_profile_page(snapshot):
        raise CandidateProfileUnavailableError("个人资料页不存在或已失效")
    if snapshot.status != "succeeded":
        raise ValueError(snapshot.error_message or "详情页抓取失败")
    page_text = (snapshot.text or "").strip()
    embedded_pdf = await extract_primary_embedded_profile_pdf_text(ctx, snapshot)
    if embedded_pdf is not None:
        page_text = merge_profile_text_with_embedded_pdf(
            page_text,
            embedded_pdf.text,
            max_chars=MAX_TEXT_CHARS,
        )
        snapshot.text = page_text
        snapshot.suspicious_empty = not bool(page_text)
        await _update_saved_profile_text(ctx, snapshot, page_text)
    if not page_text:
        raise ValueError("详情页未提供可见正文")
    return page_text


async def _update_saved_profile_text(
    ctx: CrawlToolContext,
    snapshot: PageSnapshot,
    page_text: str,
) -> None:
    if snapshot.page_id is None:
        return
    async with ctx.session_factory() as session:
        page = await session.get(CrawlPage, snapshot.page_id)
        if page is None or page.job_id != ctx.job_id or page.status != "succeeded":
            return
        page.text_excerpt = page_text[:MAX_TEXT_CHARS] or None
        await session.commit()


async def get_or_fetch_profile_text(
    ctx: CrawlToolContext,
    candidate_id: int,
    profile_url: str,
    *,
    fresh_after: datetime | None = None,
) -> str:
    normalized_profile_url = normalize_profile_url(
        profile_url,
        base_url=ctx.start_url,
    )
    base_cache_key = (id(ctx.session_factory), ctx.job_id, candidate_id, normalized_profile_url)
    cache_key = (
        (*base_cache_key, as_utc_aware(fresh_after).isoformat())
        if fresh_after is not None
        else base_cache_key
    )
    cached = _PROFILE_TEXT_CACHE.get(cache_key)
    if cached is not None:
        return cached
    stored = await _load_successful_profile_text(
        ctx,
        profile_url,
        fresh_after=fresh_after,
    )
    if stored:
        _PROFILE_TEXT_CACHE.put(cache_key, stored)
        return stored
    page_text = await fetch_profile_text(ctx, profile_url)
    _PROFILE_TEXT_CACHE.put(cache_key, page_text)
    return page_text


def _discard_cached_profile_text(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: int,
    candidate_id: int,
) -> None:
    _PROFILE_TEXT_CACHE.discard_candidate(
        session_factory_id=id(session_factory),
        job_id=job_id,
        candidate_id=candidate_id,
    )


async def _discard_cached_profile_text_if_terminal(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    task_id: int,
    job_id: int,
    candidate_id: int,
) -> None:
    async with session_factory() as session:
        task = await session.get(CrawlCandidateEnrichmentTask, task_id)
        job = await session.get(CrawlJob, job_id)
        should_discard = (
            task is None
            or task.status in _TERMINAL_ENRICHMENT_TASK_STATUSES
            or job is None
            or job.status in _TERMINAL_JOB_STATUSES
        )
    if should_discard:
        _discard_cached_profile_text(
            session_factory,
            job_id=job_id,
            candidate_id=candidate_id,
        )


async def _load_successful_profile_text(
    ctx: CrawlToolContext,
    profile_url: str,
    *,
    fresh_after: datetime | None = None,
) -> str | None:
    if not profile_url.strip():
        return None
    normalized_profile_url = normalize_profile_url(
        profile_url,
        base_url=ctx.start_url,
    )
    url_variants = {
        profile_url.strip(),
        normalized_profile_url,
        normalized_profile_url + "/"
        if not normalized_profile_url.endswith("/")
        else normalized_profile_url.rstrip("/"),
    }
    async with ctx.session_factory() as session:
        pages = list(
            await session.scalars(
                select(CrawlPage)
                .where(
                    CrawlPage.job_id == ctx.job_id,
                    CrawlPage.url.in_(url_variants),
                    CrawlPage.status == "succeeded",
                    CrawlPage.text_excerpt.is_not(None),
                )
                .order_by(CrawlPage.created_at.desc(), CrawlPage.id.desc())
                .limit(8)
            )
        )
    page = next(
        (
            candidate_page
            for candidate_page in pages
            if normalize_profile_url(
                candidate_page.url,
                base_url=ctx.start_url,
            )
            == normalized_profile_url
        ),
        None,
    )
    if (
        page is None
        or not page.text_excerpt
        or (
            fresh_after is not None
            and as_utc_aware(page.created_at) < as_utc_aware(fresh_after)
        )
        or as_utc_aware(page.created_at) < utc_now() - _PROFILE_TEXT_DATABASE_CACHE_TTL
        or not profile_text_has_meaningful_content(page.text_excerpt)
        or not _stored_profile_text_has_acceptable_quality(page.text_excerpt)
    ):
        return None
    return page.text_excerpt


def _stored_profile_text_has_acceptable_quality(text: str) -> bool:
    if len(text) < MAX_TEXT_CHARS:
        return True
    return len(_HTML_TAG_REMNANT_PATTERN.findall(text)) < 3


async def _append_enrichment_failure_event(
    session: AsyncSession,
    *,
    task: CrawlCandidateEnrichmentTask,
    candidate: CrawlCandidate | None,
    error_message: str,
) -> None:
    job = await session.get(CrawlJob, task.job_id)
    if job is None:
        return
    candidate_name = candidate.name if candidate is not None and candidate.name else "未知导师"
    trace = list(job.agent_trace or [])
    trace.append(
        {
            "event_type": "enrichment",
            "message": f"候选导师详情补全失败：{candidate_name}",
            "created_at": utc_now().isoformat(),
            "raw": {
                "candidate_id": task.candidate_id,
                "task_id": task.id,
                "status": "failed",
                "task_status": task.status,
                "attempt_count": int(task.attempt_count or 0),
                "error_message": error_message,
            },
        }
    )
    job.agent_trace = trace[-100:]


async def _append_enrichment_success_event(
    session: AsyncSession,
    *,
    task: CrawlCandidateEnrichmentTask,
    candidate: CrawlCandidate,
) -> None:
    job = await session.get(CrawlJob, task.job_id)
    if job is None:
        return
    candidate_name = candidate.name if candidate.name else "未知导师"
    trace = [
        item
        for item in list(job.agent_trace or [])
        if not _is_previous_failed_enrichment_event(item, task=task, candidate_name=candidate_name)
    ]
    trace.append(
        {
            "event_type": "enrichment",
            "message": f"候选导师详情补全成功：{candidate_name}",
            "created_at": utc_now().isoformat(),
            "raw": {
                "candidate_id": task.candidate_id,
                "task_id": task.id,
                "status": "succeeded",
                "task_status": task.status,
            },
        }
    )
    job.agent_trace = trace[-100:]


async def _append_enrichment_unchanged_event(
    session: AsyncSession,
    *,
    task: CrawlCandidateEnrichmentTask,
    candidate: CrawlCandidate,
    reason: str,
) -> None:
    job = await session.get(CrawlJob, task.job_id)
    if job is None:
        return
    candidate_name = candidate.name if candidate.name else "未知导师"
    trace = list(job.agent_trace or [])
    trace.append(
        {
            "event_type": "enrichment",
            "message": f"候选导师详情未发现新信息：{candidate_name}",
            "created_at": utc_now().isoformat(),
            "raw": {
                "candidate_id": task.candidate_id,
                "task_id": task.id,
                "status": "skipped",
                "task_status": CrawlCandidateEnrichmentTaskStatus.SKIPPED.value,
                "reason": reason,
            },
        }
    )
    job.agent_trace = trace[-100:]


def _is_previous_failed_enrichment_event(
    event: object,
    *,
    task: CrawlCandidateEnrichmentTask,
    candidate_name: str,
) -> bool:
    if not isinstance(event, dict):
        return False
    if event.get("event_type") != "enrichment":
        return False
    raw = event.get("raw")
    if isinstance(raw, dict) and raw.get("status") == "failed":
        if raw.get("task_id") == task.id:
            return True
        if raw.get("candidate_id") == task.candidate_id:
            return True
    return event.get("message") == f"候选导师详情补全失败：{candidate_name}"


async def _resolve_llm_profile(session: AsyncSession, job: CrawlJob) -> LLMProfile | None:
    return await resolve_crawl_job_runtime_profile(session, job)  # type: ignore[return-value]


def _apply_enrichment(
    candidate: CrawlCandidate,
    payload: CandidateEnrichmentPayload,
) -> list[str]:
    field_names = (
        "email",
        "title",
        "department",
        "research_direction",
        "recent_papers",
    )
    before = {field_name: getattr(candidate, field_name) for field_name in field_names}
    apply_candidate_enrichment_values(candidate, payload.model_dump())
    return [
        field_name
        for field_name in field_names
        if before[field_name] != getattr(candidate, field_name)
    ]
