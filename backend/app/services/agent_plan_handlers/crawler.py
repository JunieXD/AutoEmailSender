from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_api_errors import AgentApiError
from app.core.query_chunks import chunked_values
from app.core.time import serialize_api_datetime, utc_now
from app.models import (
    AgentChangePlan,
    CrawlCandidate,
    CrawlCandidateEnrichmentTask,
    CrawlCandidateReviewStatus,
    CrawlJob,
    CrawlJobKind,
    CrawlJobStatus,
    CrawlPage,
    CrawlPageChunk,
    CrawlPageTask,
    CrawlWorkerTokenUsage,
    LLMProfile,
    Professor,
)
from app.modules.crawler.public import (
    CrawlJobRecordError,
    CrawlJobRetryPayload,
    canonical_candidate_clause,
    canonicalize_candidate_ids,
    retry_faculty_crawl_job_record,
)
from app.modules.professors.public import (
    get_or_create_professor_by_email,
    is_valid_professor_email,
    normalize_professor_email,
)
from app.services.agent_mutations import fingerprint
from app.services.operation_logs import record_operation_log

from .shared import (
    _invalid_change_plan_snapshot_error,
    _request_state_fingerprint,
)


async def _execute_crawl_candidate_approval(
    session: AsyncSession,
    plan: AgentChangePlan,
) -> dict[str, object]:
    snapshot = plan.snapshot
    expected_fingerprint = snapshot.get("approval_fingerprint")
    if not isinstance(expected_fingerprint, str):
        raise AgentApiError(
            status_code=500,
            code="INVALID_CHANGE_PLAN_SNAPSHOT",
            message="变更计划快照无效，请重新生成计划。",
        )
    job_id, candidate_ids = _crawl_candidate_approval_request_from_snapshot(snapshot)
    try:
        current_snapshot = await _prepare_crawl_candidate_approval_snapshot(
            session,
            job_id,
            candidate_ids,
        )
    except AgentApiError as exc:
        raise _crawl_candidate_approval_plan_stale_error() from exc
    if expected_fingerprint != _crawl_candidate_approval_snapshot_fingerprint(
        current_snapshot
    ):
        raise _crawl_candidate_approval_plan_stale_error()

    job = await session.scalar(
        select(CrawlJob).where(
            CrawlJob.id == job_id,
            CrawlJob.job_kind == CrawlJobKind.FACULTY_CRAWL.value,
        ),
    )
    candidates, missing_candidate_ids = await canonicalize_candidate_ids(
        session,
        job_id=job_id,
        candidate_ids=candidate_ids,
    )
    if job is None or missing_candidate_ids:
        raise _crawl_candidate_approval_plan_stale_error()

    inserted_count = 0
    updated_count = 0
    skipped_count = 0
    professor_ids: list[int] = []
    now = utc_now()
    for candidate in candidates:
        email = normalize_professor_email(candidate.email)
        if email is None or not is_valid_professor_email(email):
            skipped_count += 1
            continue

        professor, inserted = await get_or_create_professor_by_email(
            session,
            email,
            name=candidate.name,
        )
        if inserted:
            inserted_count += 1
        else:
            updated_count += 1

        _apply_crawl_candidate_to_professor(professor, candidate, email=email, now=now)
        await session.flush()
        candidate.professor_id = professor.id
        candidate.review_status = CrawlCandidateReviewStatus.ACCEPTED.value
        candidate.updated_at = now
        if professor.id not in professor_ids:
            professor_ids.append(professor.id)

    await session.flush()
    if job.status in {
        CrawlJobStatus.NEEDS_REVIEW.value,
        CrawlJobStatus.PARTIALLY_COMPLETED.value,
    }:
        remaining_pending_count = await session.scalar(
            select(func.count())
            .select_from(CrawlCandidate)
            .where(
                CrawlCandidate.job_id == job_id,
                canonical_candidate_clause(),
                CrawlCandidate.review_status
                == CrawlCandidateReviewStatus.PENDING.value,
            ),
        )
        job.status = (
            CrawlJobStatus.PARTIALLY_COMPLETED.value
            if int(remaining_pending_count or 0) > 0
            else CrawlJobStatus.COMPLETED.value
        )
    job.updated_at = now
    await record_operation_log(
        session,
        category="crawler",
        event_name="agent_cli.crawl_candidates.approved",
        entity_type="crawl_job",
        entity_id=str(job.id),
        metadata={
            "actor": "agent_cli",
            "inserted_count": inserted_count,
            "updated_count": updated_count,
            "skipped_count": skipped_count,
            "candidate_count": len(candidates),
        },
    )
    return {
        "outcome": "crawl_candidates_approved",
        "job_id": job.id,
        "job_status": job.status,
        "inserted_count": inserted_count,
        "updated_count": updated_count,
        "skipped_count": skipped_count,
        "candidate_count": len(candidates),
        "professor_ids": professor_ids,
    }


async def _execute_crawl_job_retry(
    session: AsyncSession,
    plan: AgentChangePlan,
) -> dict[str, object]:
    snapshot = plan.snapshot
    expected_fingerprint = snapshot.get("retry_fingerprint")
    if not isinstance(expected_fingerprint, str):
        raise _invalid_change_plan_snapshot_error()
    job_id, payload = _crawl_job_retry_request_from_snapshot(snapshot)
    try:
        current_snapshot = await _prepare_crawl_job_retry_snapshot(
            session, job_id, payload
        )
    except AgentApiError as exc:
        raise _crawl_job_retry_plan_stale_error() from exc
    if expected_fingerprint != _request_state_fingerprint(current_snapshot):
        raise _crawl_job_retry_plan_stale_error()
    try:
        job = await retry_faculty_crawl_job_record(
            session,
            job_id,
            payload,
            event_name="agent_cli.crawl_job.retried",
            actor="agent_cli",
        )
    except CrawlJobRecordError as exc:
        raise _crawl_job_retry_plan_stale_error() from exc
    return {
        "outcome": "crawl_job_retry_queued",
        "job_id": job.id,
        "status": job.status,
        "clear_existing_data": payload.clear_existing_data,
        "llm_profile_id": job.llm_profile_id,
    }


async def _prepare_crawl_job_retry_snapshot(
    session: AsyncSession,
    job_id: int,
    payload: CrawlJobRetryPayload,
) -> dict[str, object]:
    job = await session.scalar(
        select(CrawlJob).where(
            CrawlJob.id == job_id,
            CrawlJob.job_kind == CrawlJobKind.FACULTY_CRAWL.value,
        ),
    )
    if job is None:
        raise AgentApiError(
            status_code=404,
            code="CRAWL_JOB_NOT_FOUND",
            message="未找到导师抓取任务。",
        )
    if job.status not in {CrawlJobStatus.FAILED.value, CrawlJobStatus.CANCELED.value}:
        raise AgentApiError(
            status_code=409,
            code="CRAWL_JOB_NOT_RETRYABLE",
            message="仅允许重试状态为“失败”或“已取消”的抓取任务。",
        )
    llm_profile = await _resolve_crawl_job_retry_llm_profile(session, job, payload)
    record_counts = await _crawl_job_retry_record_counts(session, job.id)
    state = {
        "job": {
            "id": job.id,
            "status": job.status,
            "llm_profile_id": job.llm_profile_id,
            "start_urls": job.start_urls or [job.start_url],
            "entry_type": job.entry_type,
            "deleted_at": _serialize_optional_datetime(job.deleted_at),
            "updated_at": _serialize_optional_datetime(job.updated_at),
        },
        "effective_llm_profile": _crawl_job_retry_llm_profile_state(llm_profile),
        "records": record_counts,
    }
    warnings = [
        "确认后会重新访问该抓取任务的公开网页并调用模型；这不会发送邮件，但可能产生 Token 费用。",
    ]
    if payload.clear_existing_data:
        warnings.append(
            "确认后会永久清空本任务现有的候选、网页、网页分块、运行轨迹和 Token 用量。",
        )
    else:
        warnings.append(
            "本次保留已抓取的候选和网页，但会重建抓取工作项，并清除候选补全工作项。",
        )
    return {
        "snapshot_version": "1",
        "request": {
            "job_id": job_id,
            "clear_existing_data": payload.clear_existing_data,
            "llm_profile_id": payload.llm_profile_id,
        },
        "state": state,
        "summary": {
            "job": {
                "id": job.id,
                "university": job.university,
                "school": job.school,
                "status": job.status,
            },
            "clear_existing_data": payload.clear_existing_data,
            "llm_profile": {
                "id": llm_profile.id,
                "name": llm_profile.name,
                "model_name": llm_profile.model_name,
            },
            "affected_records": record_counts,
        },
        "warnings": warnings,
    }


async def _resolve_crawl_job_retry_llm_profile(
    session: AsyncSession,
    job: CrawlJob,
    payload: CrawlJobRetryPayload,
) -> LLMProfile:
    from app.modules.llm.public import (
        get_active_llm_profile,
        get_default_active_llm_profile,
    )

    profile_id = payload.llm_profile_id or job.llm_profile_id
    if profile_id is not None:
        profile = await get_active_llm_profile(session, profile_id)
        if profile is None:
            raise AgentApiError(
                status_code=409,
                code="CRAWL_LLM_PROFILE_REPLACEMENT_REQUIRED",
                message="原模型配置已删除，请为本次重试明确选择新的模型。",
            )
        return profile
    profile = await get_default_active_llm_profile(session)
    if profile is None:
        raise AgentApiError(
            status_code=409,
            code="CRAWL_LLM_PROFILE_REQUIRED",
            message="请先在桌面端配置可用的默认模型，再重试抓取任务。",
        )
    return profile


async def _crawl_job_retry_record_counts(
    session: AsyncSession,
    job_id: int,
) -> dict[str, int]:
    async def count_for(model: object) -> int:
        job_id_column = getattr(model, "job_id")
        return int(
            await session.scalar(
                select(func.count()).select_from(model).where(job_id_column == job_id),
            )
            or 0,
        )

    return {
        "candidate_count": await count_for(CrawlCandidate),
        "page_count": await count_for(CrawlPage),
        "page_chunk_count": await count_for(CrawlPageChunk),
        "page_task_count": await count_for(CrawlPageTask),
        "candidate_enrichment_task_count": await count_for(
            CrawlCandidateEnrichmentTask
        ),
        "token_usage_count": await count_for(CrawlWorkerTokenUsage),
    }


def _crawl_job_retry_llm_profile_state(profile: LLMProfile) -> dict[str, object]:
    return {
        "id": profile.id,
        "name": profile.name,
        "model_name": profile.model_name,
        "updated_at": _serialize_optional_datetime(profile.updated_at),
    }


def _crawl_job_retry_request_from_snapshot(
    snapshot: dict[str, object],
) -> tuple[int, CrawlJobRetryPayload]:
    request_data = snapshot.get("request")
    if not isinstance(request_data, dict):
        raise _invalid_change_plan_snapshot_error()
    job_id = request_data.get("job_id")
    clear_existing_data = request_data.get("clear_existing_data")
    llm_profile_id = request_data.get("llm_profile_id")
    if (
        not isinstance(job_id, int)
        or isinstance(job_id, bool)
        or job_id < 1
        or not isinstance(clear_existing_data, bool)
        or (
            llm_profile_id is not None
            and (
                not isinstance(llm_profile_id, int)
                or isinstance(llm_profile_id, bool)
                or llm_profile_id < 1
            )
        )
    ):
        raise _invalid_change_plan_snapshot_error()
    return job_id, CrawlJobRetryPayload(
        clear_existing_data=clear_existing_data,
        llm_profile_id=llm_profile_id,
    )


async def _prepare_crawl_candidate_approval_snapshot(
    session: AsyncSession,
    job_id: int,
    candidate_ids: list[int],
) -> dict[str, object]:
    job = await _load_approvable_crawl_job(session, job_id)

    candidates, missing_candidate_ids = await canonicalize_candidate_ids(
        session,
        job_id=job_id,
        candidate_ids=candidate_ids,
    )
    if missing_candidate_ids:
        raise AgentApiError(
            status_code=404,
            code="CRAWL_CANDIDATES_NOT_FOUND",
            message="部分候选导师不存在或不属于该抓取任务。",
            details={"candidate_ids": missing_candidate_ids},
        )
    candidate_ids = [candidate.id for candidate in candidates]

    valid_emails = sorted(
        {
            email
            for candidate in candidates
            for email in [normalize_professor_email(candidate.email)]
            if email is not None and is_valid_professor_email(email)
        },
    )
    professors_by_email: dict[str, Professor] = {}
    if valid_emails:
        professors: list[Professor] = []
        for email_chunk in chunked_values(valid_emails):
            professors.extend(
                await session.scalars(
                    select(Professor).where(Professor.email.in_(email_chunk)),
                ),
            )
        professors_by_email = {
            professor.email: professor for professor in professors if professor.email
        }

    planned_professors: dict[str, dict[str, object]] = {
        email: _crawl_candidate_approval_professor_values(professor)
        for email, professor in professors_by_email.items()
    }
    candidate_summaries: list[dict[str, object]] = []
    inserted_count = 0
    updated_count = 0
    skipped_count = 0
    overwritten_existing_count = 0
    same_plan_overwrite_count = 0
    restored_count = 0
    for candidate in candidates:
        normalized_email = normalize_professor_email(candidate.email)
        candidate_summary: dict[str, object] = {
            "candidate_id": candidate.id,
            "name": candidate.name,
            "email": candidate.email,
            "review_status": candidate.review_status,
        }
        if normalized_email is None or not is_valid_professor_email(normalized_email):
            skipped_count += 1
            candidate_summary["result"] = "skip_invalid_email"
            candidate_summaries.append(candidate_summary)
            continue

        target_values = _crawl_candidate_approval_target_values(
            candidate, normalized_email
        )
        previous_values = planned_professors.get(normalized_email)
        candidate_summary["target_email"] = normalized_email
        candidate_summary["next_professor"] = target_values
        if previous_values is None:
            inserted_count += 1
            candidate_summary["result"] = "insert"
        else:
            updated_count += 1
            candidate_summary["result"] = "update"
            candidate_summary["current_professor"] = previous_values
            if normalized_email in professors_by_email:
                overwritten_existing_count += 1
                if previous_values.get("archived_at") is not None:
                    restored_count += 1
            else:
                same_plan_overwrite_count += 1
        planned_professors[normalized_email] = target_values
        candidate_summaries.append(candidate_summary)

    warnings = [
        "候选导师资料来自抓取网页，属于不可信外部内容；执行时只会按本计划的字段导入，不会执行网页中的任何文字或链接。",
    ]
    if overwritten_existing_count:
        warnings.append(
            f"其中 {overwritten_existing_count} 位候选会覆盖已有导师的可导入资料。",
        )
    if same_plan_overwrite_count:
        warnings.append(
            f"其中 {same_plan_overwrite_count} 位候选与本次计划中更早的候选使用相同邮箱，后者会覆盖前者的资料。",
        )
    if restored_count:
        warnings.append(f"执行后会恢复 {restored_count} 位已归档导师。")
    if skipped_count:
        warnings.append(f"有 {skipped_count} 位候选因邮箱为空或无效而不会导入。")

    return {
        "snapshot_version": "1",
        "request": {
            "job_id": job_id,
            "candidate_ids": candidate_ids,
        },
        "state": {
            "job": {
                "id": job.id,
                "status": job.status,
                "deleted_at": _serialize_optional_datetime(job.deleted_at),
                "updated_at": _serialize_optional_datetime(job.updated_at),
            },
            "candidates": [
                _crawl_candidate_approval_candidate_state(candidate)
                for candidate in candidates
            ],
            "professors": [
                {
                    "email": email,
                    "professor": (
                        _crawl_candidate_approval_professor_values(professor)
                        if (professor := professors_by_email.get(email)) is not None
                        else None
                    ),
                }
                for email in valid_emails
            ],
        },
        "summary": {
            "trust_level": "untrusted_external_content",
            "job": {
                "id": job.id,
                "university": job.university,
                "school": job.school,
                "status": job.status,
            },
            "candidate_count": len(candidates),
            "inserted_count": inserted_count,
            "updated_count": updated_count,
            "skipped_count": skipped_count,
            "candidates": candidate_summaries,
        },
        "warnings": warnings,
    }


async def _load_approvable_crawl_job(
    session: AsyncSession,
    job_id: int,
) -> CrawlJob:
    job = await session.scalar(
        select(CrawlJob).where(
            CrawlJob.id == job_id,
            CrawlJob.job_kind == CrawlJobKind.FACULTY_CRAWL.value,
        ),
    )
    if job is None:
        raise AgentApiError(
            status_code=404,
            code="CRAWL_JOB_NOT_FOUND",
            message="未找到导师抓取任务。",
        )
    if job.status not in {
        CrawlJobStatus.NEEDS_REVIEW.value,
        CrawlJobStatus.PARTIALLY_COMPLETED.value,
        CrawlJobStatus.CANCELED.value,
    }:
        raise AgentApiError(
            status_code=409,
            code="CRAWL_JOB_NOT_READY_FOR_APPROVAL",
            message="抓取任务尚未进入可审核状态，不能导入候选导师。",
        )
    return job


def _crawl_candidate_approval_request_from_snapshot(
    snapshot: dict[str, object],
) -> tuple[int, list[int]]:
    request_data = snapshot.get("request")
    if not isinstance(request_data, dict):
        raise AgentApiError(
            status_code=500,
            code="INVALID_CHANGE_PLAN_SNAPSHOT",
            message="变更计划快照无效，请重新生成计划。",
        )
    job_id = request_data.get("job_id")
    candidate_ids = request_data.get("candidate_ids")
    if (
        not isinstance(job_id, int)
        or isinstance(job_id, bool)
        or job_id < 1
        or not isinstance(candidate_ids, list)
        or not candidate_ids
        or any(
            not isinstance(candidate_id, int)
            or isinstance(candidate_id, bool)
            or candidate_id < 1
            for candidate_id in candidate_ids
        )
        or len(set(candidate_ids)) != len(candidate_ids)
    ):
        raise AgentApiError(
            status_code=500,
            code="INVALID_CHANGE_PLAN_SNAPSHOT",
            message="变更计划快照无效，请重新生成计划。",
        )
    return job_id, sorted(candidate_ids)


def _crawl_candidate_approval_snapshot_fingerprint(snapshot: dict[str, object]) -> str:
    return fingerprint(
        {
            "request": snapshot.get("request"),
            "state": snapshot.get("state"),
        },
    )


def _crawl_candidate_approval_candidate_state(
    candidate: CrawlCandidate,
) -> dict[str, object]:
    return {
        "id": candidate.id,
        "job_id": candidate.job_id,
        "professor_id": candidate.professor_id,
        "name": candidate.name,
        "email": candidate.email,
        "title": candidate.title,
        "university": candidate.university,
        "school": candidate.school,
        "department": candidate.department,
        "research_direction": candidate.research_direction,
        "recent_papers": candidate.recent_papers,
        "profile_url": candidate.profile_url,
        "source_url": candidate.source_url,
        "review_status": candidate.review_status,
        "updated_at": _serialize_optional_datetime(candidate.updated_at),
    }


def _crawl_candidate_approval_professor_values(
    professor: Professor,
) -> dict[str, object]:
    return {
        "id": professor.id,
        "name": professor.name,
        "email": professor.email,
        "title": professor.title,
        "university": professor.university,
        "school": professor.school,
        "department": professor.department,
        "research_direction": professor.research_direction,
        "recent_papers": professor.recent_papers,
        "profile_url": professor.profile_url,
        "source_url": professor.source_url,
        "archived_at": _serialize_optional_datetime(professor.archived_at),
        "updated_at": _serialize_optional_datetime(professor.updated_at),
    }


def _crawl_candidate_approval_target_values(
    candidate: CrawlCandidate,
    email: str,
) -> dict[str, object]:
    return {
        "name": candidate.name,
        "email": email,
        "title": candidate.title,
        "university": candidate.university,
        "school": candidate.school,
        "department": candidate.department,
        "research_direction": candidate.research_direction,
        "recent_papers": candidate.recent_papers or [],
        "profile_url": candidate.profile_url,
        "source_url": candidate.source_url,
        "archived_at": None,
    }


def _apply_crawl_candidate_to_professor(
    professor: Professor,
    candidate: CrawlCandidate,
    *,
    email: str,
    now: datetime,
) -> None:
    professor.name = candidate.name
    professor.email = email
    professor.title = candidate.title
    professor.university = candidate.university
    professor.school = candidate.school
    professor.department = candidate.department
    professor.research_direction = candidate.research_direction
    professor.recent_papers = candidate.recent_papers or []
    professor.profile_url = candidate.profile_url
    professor.source_url = candidate.source_url
    professor.archived_at = None
    professor.updated_at = now


def _serialize_optional_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return serialize_api_datetime(value)


def _crawl_candidate_approval_plan_stale_error() -> AgentApiError:
    return AgentApiError(
        status_code=409,
        code="PLAN_STALE",
        message="抓取任务、候选导师或将被覆盖的导师资料已发生变化，请重新生成导入预览。",
        details={"changed_fields": ["crawl_job", "crawl_candidates", "professors"]},
    )


def _crawl_job_retry_plan_stale_error() -> AgentApiError:
    return AgentApiError(
        status_code=409,
        code="PLAN_STALE",
        message="抓取任务、受影响记录或模型配置已发生变化，请重新生成重试预览。",
        details={"changed_fields": ["crawl_job", "crawl_records", "llm_profile"]},
    )
