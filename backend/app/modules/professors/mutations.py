from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import case, delete, func, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.query_chunks import chunked_values, unique_positive_ids
from app.core.time import serialize_api_datetime, utc_now
from app.models import (
    CrawlCandidateEnrichmentTask,
    CrawlCandidateEnrichmentTaskStatus,
    CrawlJob,
    CrawlJobKind,
    EmailTask,
    EmailTaskCancellationReason,
    EmailTaskStatus,
    MatchAnalysisJobItem,
    MatchAnalysisJobItemStatus,
    Professor,
    ProfessorTag,
    ProfessorTagLink,
)
from .schemas import (
    ProfessorBulkTagsPayload,
    ProfessorTagPayload,
    ProfessorTagUpdatePayload,
    ProfessorUpsertPayload,
)
from app.services.operation_logs import record_operation_log
from .management import (
    ParsedProfessorImport,
    is_valid_professor_email,
    normalize_professor_payload,
)


DEFAULT_IMPORTED_TAG_TEXT_COLOR = "#166534"
DEFAULT_IMPORTED_TAG_BACKGROUND_COLOR = "#dcfce7"


@dataclass(slots=True)
class ProfessorMutationError(ValueError):
    status_code: int
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class ProfessorImportMutationResult:
    inserted_count: int
    updated_count: int
    created_tag_count: int
    failed_count: int


@dataclass(frozen=True, slots=True)
class ProfessorBulkTagsMutationResult:
    professor_ids: list[int]
    affected_count: int


@dataclass(frozen=True, slots=True)
class _ProfessorDeliveryImpact:
    cancel_task_ids: list[int]
    blocking_rows: list[tuple[int, int, str]]


@dataclass(frozen=True, slots=True)
class _ProfessorBackgroundWorkImpact:
    match_analysis_item_ids: list[int]
    information_enrichment_task_ids: list[int]


PROFESSOR_ARCHIVE_CANCEL_STATUSES = {
    EmailTaskStatus.DISCOVERED.value,
    EmailTaskStatus.MATCHED.value,
    EmailTaskStatus.GENERATING_DRAFT.value,
    EmailTaskStatus.DRAFT_FAILED.value,
    EmailTaskStatus.REVIEW_REQUIRED.value,
    EmailTaskStatus.APPROVED.value,
    EmailTaskStatus.SCHEDULED.value,
    EmailTaskStatus.SCHEDULE_MISSED.value,
    EmailTaskStatus.SEND_FAILED.value,
}


async def get_or_create_professor_by_email(
    session: AsyncSession,
    email: str,
    *,
    name: str,
) -> tuple[Professor, bool]:
    professor = await session.scalar(
        select(Professor).where(Professor.email == email),
    )
    if professor is not None:
        return professor, False

    try:
        async with session.begin_nested():
            professor = Professor(name=name, email=email)
            session.add(professor)
            await session.flush()
    except IntegrityError:
        professor = await session.scalar(
            select(Professor).where(Professor.email == email),
        )
        if professor is None:
            raise
        return professor, False
    return professor, True


async def create_professor_record(
    session: AsyncSession,
    payload: ProfessorUpsertPayload,
    *,
    event_name: str,
    actor: str,
) -> Professor:
    professor_data = normalize_professor_payload(payload)
    ensure_professor_email_valid(professor_data["email"])
    existing = await session.scalar(
        select(Professor).where(Professor.email == professor_data["email"]),
    )
    if existing is not None:
        raise ProfessorMutationError(
            409,
            "PROFESSOR_EMAIL_CONFLICT",
            "该邮箱的导师已存在",
        )

    professor = Professor(**professor_data)
    session.add(professor)
    await session.flush()
    await sync_professor_tags(session, professor, payload.tag_ids)
    await record_professor_event(session, professor, event_name, actor=actor)
    return await get_professor_with_tags_or_raise(session, professor.id)


async def update_professor_record(
    session: AsyncSession,
    professor_id: int,
    payload: ProfessorUpsertPayload,
    *,
    event_name: str,
    actor: str,
) -> Professor:
    professor = await get_professor_with_tags_or_raise(session, professor_id)
    professor_data = normalize_professor_payload(payload)
    ensure_professor_email_valid(professor_data["email"])
    existing = await session.scalar(
        select(Professor).where(
            Professor.email == professor_data["email"],
            Professor.id != professor_id,
        ),
    )
    if existing is not None:
        raise ProfessorMutationError(
            409,
            "PROFESSOR_EMAIL_CONFLICT",
            "该邮箱已被其他导师使用",
        )

    professor.name = professor_data["name"]
    professor.email = professor_data["email"]
    professor.title = professor_data["title"]
    professor.university = professor_data["university"]
    professor.school = professor_data["school"]
    professor.department = professor_data["department"]
    professor.research_direction = professor_data["research_direction"]
    professor.recent_papers = professor_data["recent_papers"]
    professor.profile_url = professor_data["profile_url"]
    professor.source_url = professor_data["source_url"]
    if "personal_note" in payload.model_fields_set:
        professor.personal_note = professor_data["personal_note"]
    await sync_professor_tags(session, professor, payload.tag_ids)
    professor.updated_at = utc_now()
    await record_professor_event(session, professor, event_name, actor=actor)
    return await get_professor_with_tags_or_raise(session, professor.id)


async def archive_professor_record(
    session: AsyncSession,
    professor_id: int,
    *,
    event_name: str,
    actor: str,
) -> tuple[Professor, int, list[int], list[int], list[int]]:
    professor = await get_professor_with_tags_or_raise(session, professor_id)
    await _lock_professors_for_archive(session, [professor.id])
    await session.refresh(professor, attribute_names=["archived_at"])
    affected_count = 0
    canceled_email_task_ids: list[int] = []
    canceled_match_analysis_item_ids: list[int] = []
    canceled_information_enrichment_task_ids: list[int] = []
    if professor.archived_at is None:
        now = utc_now()
        canceled_email_task_ids = await _cancel_pending_professor_deliveries(
            session,
            [professor.id],
            now=now,
        )
        professor.archived_at = now
        professor.updated_at = now
        (
            canceled_match_analysis_item_ids,
            canceled_information_enrichment_task_ids,
        ) = await _stop_pending_professor_background_work(
            session,
            [professor.id],
            now=now,
        )
        affected_count = 1
    await record_professor_event(
        session,
        professor,
        event_name,
        actor=actor,
        metadata={
            "affected_count": affected_count,
            "canceled_email_task_ids": canceled_email_task_ids,
            "canceled_match_analysis_item_ids": canceled_match_analysis_item_ids,
            "canceled_information_enrichment_task_ids": (
                canceled_information_enrichment_task_ids
            ),
        },
    )
    return (
        professor,
        affected_count,
        canceled_email_task_ids,
        canceled_match_analysis_item_ids,
        canceled_information_enrichment_task_ids,
    )


async def restore_professor_record(
    session: AsyncSession,
    professor_id: int,
    *,
    event_name: str,
    actor: str,
) -> tuple[Professor, int]:
    professor = await get_professor_with_tags_or_raise(session, professor_id)
    affected_count = 0
    if professor.archived_at is not None:
        professor.archived_at = None
        professor.updated_at = utc_now()
        affected_count = 1
    await record_professor_event(
        session,
        professor,
        event_name,
        actor=actor,
        metadata={"affected_count": affected_count},
    )
    return professor, affected_count


async def get_professor_tag_usage_snapshot(
    session: AsyncSession,
    tag_id: int,
) -> dict[str, object]:
    tag, professors = await _load_professor_tag_with_usage(session, tag_id)
    return {
        "tag": _serialize_tag_snapshot(tag),
        "professors": [
            {
                "id": professor.id,
                "name": professor.name,
                "email": professor.email,
                "university": professor.university,
                "school": professor.school,
            }
            for professor in professors
        ],
    }


async def prepare_professor_tag_delete_snapshot(
    session: AsyncSession,
    tag_id: int,
) -> dict[str, object]:
    tag, professors = await _load_professor_tag_with_usage(session, tag_id)
    usage = {
        "tag": _serialize_tag_snapshot(tag),
        "professors": [
            {
                "id": professor.id,
                "name": professor.name,
                "email": professor.email,
                "university": professor.university,
                "school": professor.school,
            }
            for professor in professors
        ],
    }
    warnings = [
        "删除后该标签会从所有已关联导师中移除，无法自动恢复。",
    ]
    if professors:
        warnings.append(f"该标签当前关联 {len(professors)} 位导师。")
    return {
        "snapshot_version": "1",
        "request": {"tag_id": tag.id},
        "summary": {
            "tag": usage["tag"],
            "professor_count": len(professors),
            "professors": usage["professors"],
        },
        "warnings": warnings,
        "state": {
            "tag": _serialize_tag_snapshot(tag),
            "professors": [
                {
                    "id": professor.id,
                    "name": professor.name,
                    "email": professor.email,
                    "archived_at": serialize_api_datetime(professor.archived_at)
                    if professor.archived_at is not None
                    else None,
                    "updated_at": serialize_api_datetime(professor.updated_at),
                    "tags": [_serialize_tag_snapshot(item) for item in professor.tags],
                }
                for professor in professors
            ],
        },
    }


async def delete_professor_tag_record(
    session: AsyncSession,
    tag_id: int,
    *,
    event_name: str,
    actor: str,
) -> dict[str, object]:
    tag, professors = await _load_professor_tag_with_usage(session, tag_id)
    tag_name = tag.name
    professor_ids = [professor.id for professor in professors]
    await session.execute(
        delete(ProfessorTagLink).where(ProfessorTagLink.tag_id == tag.id)
    )
    await session.delete(tag)
    await record_operation_log(
        session,
        category="user_action",
        event_name=event_name,
        entity_type="professor_tag",
        entity_id=str(tag_id),
        metadata={
            "actor": actor,
            "tag_name": tag_name,
            "affected_professor_count": len(professor_ids),
            "professor_ids": professor_ids,
        },
    )
    return {
        "tag_id": tag_id,
        "tag_name": tag_name,
        "affected_professor_count": len(professor_ids),
    }


async def lock_professor_tag_for_delete(
    session: AsyncSession,
    tag_id: int,
) -> None:
    """Block new tag links while a confirmed deletion is revalidated."""

    result = await session.execute(
        update(ProfessorTag)
        .where(ProfessorTag.id == tag_id)
        .values(id=ProfessorTag.id)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise ProfessorMutationError(
            404,
            "PROFESSOR_TAG_NOT_FOUND",
            "未找到标签",
        )
    await session.scalar(
        select(ProfessorTag.id)
        .where(ProfessorTag.id == tag_id)
        .with_for_update()
    )


async def prepare_bulk_professor_archive_snapshot(
    session: AsyncSession,
    professor_ids: list[int],
) -> dict[str, object]:
    ordered_ids, professors = await _resolve_bulk_professor_archive(
        session, professor_ids
    )
    professors_by_id = {professor.id: professor for professor in professors}
    items = [
        {
            "id": professors_by_id[professor_id].id,
            "name": professors_by_id[professor_id].name,
            "email": professors_by_id[professor_id].email,
            "archived_at": serialize_api_datetime(
                professors_by_id[professor_id].archived_at,
            )
            if professors_by_id[professor_id].archived_at is not None
            else None,
            "will_archive": professors_by_id[professor_id].archived_at is None,
        }
        for professor_id in ordered_ids
    ]
    affected_count = sum(item["will_archive"] for item in items)
    already_archived_count = len(items) - affected_count
    affected_ids = [
        int(item["id"]) for item in items if bool(item["will_archive"])
    ]
    delivery_impact = await _professor_delivery_impact(session, affected_ids)
    background_work_impact = await _professor_background_work_impact(
        session,
        affected_ids,
    )
    _raise_professor_archive_blockers(delivery_impact.blocking_rows)
    warnings = []
    if already_archived_count:
        warnings.append(
            f"其中 {already_archived_count} 位导师已在回收站中，不会重复移动。"
        )
    if delivery_impact.cancel_task_ids:
        warnings.append(
            f"归档时会自动取消 {len(delivery_impact.cancel_task_ids)} 个尚未开始发送的邮件任务。"
        )
    if background_work_impact.match_analysis_item_ids:
        warnings.append(
            "归档时会自动取消 "
            f"{len(background_work_impact.match_analysis_item_ids)} 个尚未完成的匹配分析项。"
        )
    if background_work_impact.information_enrichment_task_ids:
        warnings.append(
            "归档时会自动取消 "
            f"{len(background_work_impact.information_enrichment_task_ids)} 个尚未完成的信息补全项。"
        )
    return {
        "snapshot_version": "1",
        "request": {"professor_ids": ordered_ids},
        "summary": {
            "professor_count": len(items),
            "affected_count": affected_count,
            "already_archived_count": already_archived_count,
            "professors": items,
            "automatic_actions": {
                "cancel_email_task_ids": delivery_impact.cancel_task_ids,
                "cancel_match_analysis_item_ids": (
                    background_work_impact.match_analysis_item_ids
                ),
                "cancel_information_enrichment_task_ids": (
                    background_work_impact.information_enrichment_task_ids
                ),
            },
        },
        "warnings": warnings,
        "state": {
            "professors": [
                {
                    "id": professor.id,
                    "archived_at": serialize_api_datetime(professor.archived_at)
                    if professor.archived_at is not None
                    else None,
                    "updated_at": serialize_api_datetime(professor.updated_at),
                }
                for professor in sorted(professors, key=lambda item: item.id)
            ],
        },
    }


async def bulk_archive_professor_records(
    session: AsyncSession,
    professor_ids: list[int],
    *,
    event_name: str,
    actor: str,
) -> dict[str, object]:
    ordered_ids, professors = await _resolve_bulk_professor_archive(
        session, professor_ids
    )
    await _lock_professors_for_archive(session, ordered_ids)
    for professor in professors:
        await session.refresh(professor, attribute_names=["archived_at"])
    now = utc_now()
    affected_ids = [
        professor.id for professor in professors if professor.archived_at is None
    ]
    canceled_email_task_ids = await _cancel_pending_professor_deliveries(
        session,
        affected_ids,
        now=now,
    )
    for professor in professors:
        if professor.id not in affected_ids:
            continue
        professor.archived_at = now
        professor.updated_at = now
    (
        canceled_match_analysis_item_ids,
        canceled_information_enrichment_task_ids,
    ) = await _stop_pending_professor_background_work(
        session,
        affected_ids,
        now=now,
    )
    await record_operation_log(
        session,
        category="user_action",
        event_name=event_name,
        entity_type="professor",
        metadata={
            "actor": actor,
            "requested_count": len(ordered_ids),
            "affected_count": len(affected_ids),
            "canceled_email_task_ids": canceled_email_task_ids,
            "canceled_match_analysis_item_ids": canceled_match_analysis_item_ids,
            "canceled_information_enrichment_task_ids": (
                canceled_information_enrichment_task_ids
            ),
            **_ids_metadata(ordered_ids),
        },
    )
    return {
        "professor_ids": ordered_ids,
        "affected_count": len(affected_ids),
        "canceled_email_task_ids": canceled_email_task_ids,
        "canceled_match_analysis_item_ids": canceled_match_analysis_item_ids,
        "canceled_information_enrichment_task_ids": (
            canceled_information_enrichment_task_ids
        ),
        "post_state": {
            "professors": [
                {
                    "id": professor.id,
                    "archived_at": serialize_api_datetime(professor.archived_at),
                }
                for professor in sorted(professors, key=lambda item: item.id)
            ],
        },
    }


async def _lock_professors_for_archive(
    session: AsyncSession,
    professor_ids: list[int],
) -> None:
    """Serialize archive decisions with delivery claims for these professors."""

    for professor_id_chunk in chunked_values(sorted(set(professor_ids))):
        await session.execute(
            update(Professor)
            .where(Professor.id.in_(professor_id_chunk))
            .values(updated_at=Professor.updated_at)
            .execution_options(synchronize_session=False)
        )


async def _professor_delivery_impact(
    session: AsyncSession,
    professor_ids: list[int],
) -> _ProfessorDeliveryImpact:
    if not professor_ids:
        return _ProfessorDeliveryImpact(cancel_task_ids=[], blocking_rows=[])
    pending: list[tuple[int, int, str]] = []
    for professor_id_chunk in chunked_values(professor_ids):
        pending.extend(
            await session.execute(
                select(EmailTask.professor_id, EmailTask.id, EmailTask.status)
                .where(
                    EmailTask.professor_id.in_(professor_id_chunk),
                    EmailTask.batch_send_canceled_at.is_(None),
                    EmailTask.status.in_(
                        [
                            *PROFESSOR_ARCHIVE_CANCEL_STATUSES,
                            EmailTaskStatus.SENDING.value,
                        ]
                    ),
                )
                .order_by(EmailTask.professor_id.asc(), EmailTask.id.asc())
            )
        )
    return _ProfessorDeliveryImpact(
        cancel_task_ids=[
            task_id
            for _, task_id, task_status in pending
            if task_status in PROFESSOR_ARCHIVE_CANCEL_STATUSES
        ],
        blocking_rows=[
            row for row in pending if row[2] == EmailTaskStatus.SENDING.value
        ],
    )


async def _cancel_pending_professor_deliveries(
    session: AsyncSession,
    professor_ids: list[int],
    *,
    now: datetime,
) -> list[int]:
    impact = await _professor_delivery_impact(session, professor_ids)
    _raise_professor_archive_blockers(impact.blocking_rows)

    cancel_task_ids = impact.cancel_task_ids
    if cancel_task_ids:
        await session.execute(
            update(EmailTask)
            .where(
                EmailTask.id.in_(cancel_task_ids),
                EmailTask.status.in_(PROFESSOR_ARCHIVE_CANCEL_STATUSES),
            )
            .values(
                status=EmailTaskStatus.CANCELED.value,
                cancellation_reason=(
                    EmailTaskCancellationReason.PROFESSOR_ARCHIVED.value
                ),
                scheduled_at=None,
                draft_generation_previous_status=None,
                draft_generation_started_at=None,
                draft_claim_id=None,
                draft_claimed_at=None,
                draft_lease_expires_at=None,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
    return cancel_task_ids


async def _professor_background_work_impact(
    session: AsyncSession,
    professor_ids: list[int],
) -> _ProfessorBackgroundWorkImpact:
    if not professor_ids:
        return _ProfessorBackgroundWorkImpact([], [])
    match_analysis_item_ids = list(
        await session.scalars(
            select(MatchAnalysisJobItem.id)
            .where(
                MatchAnalysisJobItem.professor_id.in_(professor_ids),
                MatchAnalysisJobItem.status.in_(
                    [
                        MatchAnalysisJobItemStatus.QUEUED.value,
                        MatchAnalysisJobItemStatus.RUNNING.value,
                    ]
                ),
            )
            .order_by(MatchAnalysisJobItem.id.asc())
        )
    )
    information_enrichment_task_ids = list(
        await session.scalars(
            select(CrawlCandidateEnrichmentTask.id)
            .where(
                CrawlCandidateEnrichmentTask.professor_id.in_(professor_ids),
                CrawlCandidateEnrichmentTask.status.in_(
                    [
                        CrawlCandidateEnrichmentTaskStatus.PENDING.value,
                        CrawlCandidateEnrichmentTaskStatus.PROCESSING.value,
                        CrawlCandidateEnrichmentTaskStatus.FAILED_RETRYABLE.value,
                    ]
                ),
            )
            .order_by(CrawlCandidateEnrichmentTask.id.asc())
        )
    )
    return _ProfessorBackgroundWorkImpact(
        match_analysis_item_ids=[int(item) for item in match_analysis_item_ids],
        information_enrichment_task_ids=[
            int(item) for item in information_enrichment_task_ids
        ],
    )


async def _stop_pending_professor_background_work(
    session: AsyncSession,
    professor_ids: list[int],
    *,
    now: datetime,
) -> tuple[list[int], list[int]]:
    if not professor_ids:
        return [], []

    from app.modules.matching.public import (
        skip_professor_match_analysis_items_record,
    )

    match_analysis_item_ids = await skip_professor_match_analysis_items_record(
        session,
        professor_ids,
        now=now,
    )
    enrichment_rows = list(
        await session.execute(
            select(
                CrawlCandidateEnrichmentTask.id,
                CrawlCandidateEnrichmentTask.job_id,
            )
            .where(
                CrawlCandidateEnrichmentTask.professor_id.in_(professor_ids),
                CrawlCandidateEnrichmentTask.status.in_(
                    [
                        CrawlCandidateEnrichmentTaskStatus.PENDING.value,
                        CrawlCandidateEnrichmentTaskStatus.PROCESSING.value,
                        CrawlCandidateEnrichmentTaskStatus.FAILED_RETRYABLE.value,
                    ]
                ),
            )
            .order_by(CrawlCandidateEnrichmentTask.id.asc())
        )
    )
    enrichment_task_ids = [int(row.id) for row in enrichment_rows]
    if enrichment_task_ids:
        for task_id_chunk in chunked_values(enrichment_task_ids):
            await session.execute(
                update(CrawlCandidateEnrichmentTask)
                .where(
                    CrawlCandidateEnrichmentTask.id.in_(task_id_chunk),
                    CrawlCandidateEnrichmentTask.status.in_(
                        [
                            CrawlCandidateEnrichmentTaskStatus.PENDING.value,
                            CrawlCandidateEnrichmentTaskStatus.PROCESSING.value,
                            CrawlCandidateEnrichmentTaskStatus.FAILED_RETRYABLE.value,
                        ]
                    ),
                )
                .values(
                    status=CrawlCandidateEnrichmentTaskStatus.SKIPPED.value,
                    skip_reason="导师已移入回收站",
                    last_error=None,
                    worker_id=None,
                    claimed_at=None,
                    lease_expires_at=None,
                    finished_at=now,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )

        from .enrichment.service import finalize_professor_information_enrichment_job

        for job_id in dict.fromkeys(int(row.job_id) for row in enrichment_rows):
            job = await session.get(CrawlJob, job_id)
            if job is None or job.job_kind != CrawlJobKind.PROFESSOR_ENRICHMENT.value:
                continue
            active_count = await session.scalar(
                select(func.count())
                .select_from(CrawlCandidateEnrichmentTask)
                .where(
                    CrawlCandidateEnrichmentTask.job_id == job_id,
                    CrawlCandidateEnrichmentTask.status.in_(
                        [
                            CrawlCandidateEnrichmentTaskStatus.PENDING.value,
                            CrawlCandidateEnrichmentTaskStatus.PROCESSING.value,
                            CrawlCandidateEnrichmentTaskStatus.FAILED_RETRYABLE.value,
                        ]
                    ),
                )
            )
            if int(active_count or 0) == 0:
                await finalize_professor_information_enrichment_job(
                    session,
                    job,
                    now=now,
                )
    return match_analysis_item_ids, enrichment_task_ids


def _raise_professor_archive_blockers(
    blocking_rows: list[tuple[int, int, str]],
) -> None:
    if blocking_rows:
        shown = blocking_rows[:10]
        details = "、".join(
            f"导师 #{professor_id} 的邮件任务 #{task_id}（{task_status}）"
            for professor_id, task_id, task_status in shown
        )
        suffix = " 等" if len(blocking_rows) > len(shown) else ""
        raise ProfessorMutationError(
            409,
            "PROFESSOR_ARCHIVE_SENDING_DELIVERY",
            (
                f"暂时无法移入回收站：{details}{suffix} 已进入发送流程。"
                "请在任务中心等待这些任务完成或确认发送结果后再试。"
            ),
        )


async def set_professor_tags_record(
    session: AsyncSession,
    professor_id: int,
    payload: ProfessorTagUpdatePayload,
    *,
    event_name: str,
    actor: str,
) -> Professor:
    professor = await get_professor_with_tags_or_raise(session, professor_id)
    await sync_professor_tags(session, professor, payload.tag_ids)
    professor.updated_at = utc_now()
    await record_professor_event(session, professor, event_name, actor=actor)
    return await get_professor_with_tags_or_raise(session, professor.id)


async def prepare_bulk_professor_tags_snapshot(
    session: AsyncSession,
    payload: ProfessorBulkTagsPayload,
) -> dict[str, object]:
    """Build a user-visible, non-mutating preview for a batch tag update."""

    professor_ids, professors, target_tags = await _resolve_bulk_tag_update(
        session,
        payload,
    )
    target_tag_ids = [tag.id for tag in target_tags]
    professors_by_id = {professor.id: professor for professor in professors}
    preview_professors: list[dict[str, object]] = []
    changed_count = 0
    archived_count = 0
    for professor_id in professor_ids:
        professor = professors_by_id[professor_id]
        current_tags = [_serialize_tag_snapshot(tag) for tag in professor.tags]
        next_tag_ids = _next_bulk_tag_ids(
            [tag.id for tag in professor.tags],
            mode=payload.mode,
            target_tag_ids=target_tag_ids,
        )
        next_tags = _tags_for_ids(next_tag_ids, professor.tags, target_tags)
        will_change = [tag["id"] for tag in current_tags] != next_tag_ids
        if will_change:
            changed_count += 1
        if professor.archived_at is not None:
            archived_count += 1
        preview_professors.append(
            {
                "id": professor.id,
                "name": professor.name,
                "email": professor.email,
                "archived_at": serialize_api_datetime(professor.archived_at)
                if professor.archived_at is not None
                else None,
                "current_tags": current_tags,
                "next_tags": next_tags,
                "will_change": will_change,
            },
        )

    warnings: list[str] = []
    if payload.mode == "replace" and not target_tags:
        warnings.append(f"这会清空 {len(professor_ids)} 位导师的全部标签。")
    if archived_count:
        warnings.append(f"其中 {archived_count} 位导师已归档，其标签也会被修改。")
    unchanged_count = len(professor_ids) - changed_count
    if unchanged_count:
        warnings.append(f"其中 {unchanged_count} 位导师的标签不会发生变化。")

    return {
        "snapshot_version": "1",
        "request": {
            "professor_ids": professor_ids,
            "mode": payload.mode,
            "tag_ids": target_tag_ids,
        },
        "summary": {
            "mode": payload.mode,
            "professor_count": len(professor_ids),
            "changed_count": changed_count,
            "unchanged_count": unchanged_count,
            "target_tags": [_serialize_tag_snapshot(tag) for tag in target_tags],
            "professors": preview_professors,
        },
        "warnings": warnings,
    }


async def bulk_update_professor_tags_record(
    session: AsyncSession,
    payload: ProfessorBulkTagsPayload,
    *,
    event_name: str,
    actor: str,
) -> ProfessorBulkTagsMutationResult:
    """Apply a validated bulk tag update without committing the transaction."""

    professor_ids, professors, target_tags = await _resolve_bulk_tag_update(
        session,
        payload,
    )
    target_tag_ids = [tag.id for tag in target_tags]
    professors_by_id = {professor.id: professor for professor in professors}
    now = utc_now()
    next_tag_ids_by_professor: dict[int, list[int]] = {}
    for professor_id in professor_ids:
        professor = professors_by_id[professor_id]
        next_tag_ids_by_professor[professor_id] = _next_bulk_tag_ids(
            [tag.id for tag in professor.tags],
            mode=payload.mode,
            target_tag_ids=target_tag_ids,
        )
        professor.updated_at = now

    for professor_id_chunk in chunked_values(professor_ids):
        await session.execute(
            delete(ProfessorTagLink).where(
                ProfessorTagLink.professor_id.in_(professor_id_chunk),
            ),
        )
    link_rows = [
        {
            "professor_id": professor_id,
            "tag_id": tag_id,
            "sort_order": sort_order,
        }
        for professor_id in professor_ids
        for sort_order, tag_id in enumerate(
            next_tag_ids_by_professor[professor_id],
        )
    ]
    for row_chunk in chunked_values(link_rows):
        await session.execute(insert(ProfessorTagLink), list(row_chunk))

    await session.flush()
    for professor in professors:
        session.expire(professor, ["tags"])
    await record_operation_log(
        session,
        category="user_action",
        event_name=event_name,
        entity_type="professor",
        metadata={
            "actor": actor,
            "requested_count": len(professor_ids),
            "affected_count": len(professor_ids),
            **_ids_metadata(professor_ids),
            "mode": payload.mode,
            "tag_ids": target_tag_ids,
        },
    )
    return ProfessorBulkTagsMutationResult(
        professor_ids=professor_ids,
        affected_count=len(professor_ids),
    )


async def prepare_professor_import_snapshot(
    session: AsyncSession,
    parsed: ParsedProfessorImport,
    *,
    filename: str,
) -> dict[str, object]:
    """Build a stable, user-visible preview for a parsed spreadsheet import."""

    existing_by_email = await _load_existing_import_professors(session, parsed)
    tag_names = _collect_import_tag_names(parsed)
    existing_tags: list[ProfessorTag] = []
    for name_chunk in chunked_values(tag_names):
        existing_tags.extend(
            await session.scalars(
                select(ProfessorTag).where(ProfessorTag.name.in_(name_chunk)),
            ),
        )
    existing_tag_names = {tag.name for tag in existing_tags}

    inserted_count = 0
    updated_count = 0
    restored_count = 0
    items: list[dict[str, object]] = []
    state: list[dict[str, object]] = []
    for email in sorted(parsed.data):
        item = parsed.data[email]
        existing = existing_by_email.get(email)
        operation = "update" if existing is not None else "insert"
        if existing is None:
            inserted_count += 1
        else:
            updated_count += 1
            if existing.archived_at is not None:
                restored_count += 1
        current_tags = (
            [_serialize_tag_snapshot(tag) for tag in existing.tags]
            if existing is not None
            else []
        )
        item_tag_names = [str(tag_name) for tag_name in item.get("tag_names", [])]
        items.append(
            {
                "id": existing.id if existing is not None else None,
                "name": item["name"],
                "email": email,
                "operation": operation,
                "current_tags": current_tags,
                "imported_tag_names": item_tag_names,
                "will_restore": bool(existing and existing.archived_at is not None),
            },
        )
        state.append(
            {
                "email": email,
                "professor": _serialize_import_professor_state(existing),
            },
        )

    warnings: list[str] = []
    if updated_count:
        warnings.append(f"导入会覆盖 {updated_count} 位已有导师的表格字段。")
    if restored_count:
        warnings.append(f"其中 {restored_count} 位已归档导师会恢复到正常列表。")
    if parsed.failed_count:
        warnings.append(f"文件中有 {parsed.failed_count} 行无效数据会被跳过。")
    if tag_names and len(tag_names) > len(existing_tag_names):
        warnings.append(
            f"会新建 {len(tag_names) - len(existing_tag_names)} 个导师标签。"
        )

    return {
        "snapshot_version": "1",
        "request": {
            "filename": filename,
            "data": parsed.data,
            "failed_count": parsed.failed_count,
        },
        "summary": {
            "filename": filename,
            "total_count": len(parsed.data),
            "inserted_count": inserted_count,
            "updated_count": updated_count,
            "restored_count": restored_count,
            "created_tag_count": len(tag_names) - len(existing_tag_names),
            "failed_count": parsed.failed_count,
            "items": items,
        },
        "warnings": warnings,
        "state": {
            "professors": state,
            "tags": [
                _serialize_tag_snapshot(tag)
                for tag in sorted(existing_tags, key=lambda item: item.id)
            ],
        },
    }


async def import_professor_records(
    session: AsyncSession,
    parsed: ParsedProfessorImport,
    *,
    filename: str,
    event_name: str,
    actor: str,
) -> ProfessorImportMutationResult:
    """Apply parsed import data with the same merge rules used by the desktop UI."""

    existing_professor_ids_by_email = await _load_existing_import_professor_ids(
        session,
        parsed.data.keys(),
    )
    inserted_count = len(parsed.data) - len(existing_professor_ids_by_email)
    updated_count = len(existing_professor_ids_by_email)
    tag_names = _collect_import_tag_names(parsed)
    tags_by_name, created_tag_count = await _ensure_import_tags(
        session,
        tag_names,
    )
    personal_note_column_present = any(
        bool(payload.get("has_personal_note_column"))
        for payload in parsed.data.values()
    )
    now = utc_now()
    rows_by_personal_note_presence: dict[bool, list[dict[str, object]]] = {
        False: [],
        True: [],
    }
    for payload in parsed.data.values():
        has_personal_note_column = bool(payload.get("has_personal_note_column"))
        row = {
            key: value
            for key, value in payload.items()
            if key not in {"tag_names", "has_personal_note_column"}
            and (key != "personal_note" or has_personal_note_column)
        }
        row.update(
            {
                "archived_at": None,
                "updated_at": now,
            },
        )
        rows_by_personal_note_presence[has_personal_note_column].append(row)

    for update_personal_note, rows in rows_by_personal_note_presence.items():
        if not rows:
            continue
        statement = _professor_import_upsert_statement(
            update_personal_note=update_personal_note,
        )
        for row_chunk in chunked_values(rows):
            await session.execute(statement, list(row_chunk))

    professor_ids_by_email = await _load_existing_import_professor_ids(
        session,
        parsed.data.keys(),
    )
    replace_tags_by_professor_id: dict[int, list[int]] = {}
    for email, payload in parsed.data.items():
        imported_tag_names = [
            str(tag_name).strip()
            for tag_name in payload["tag_names"]
            if str(tag_name).strip()
        ]
        if email in existing_professor_ids_by_email and not imported_tag_names:
            continue
        replace_tags_by_professor_id[professor_ids_by_email[email]] = [
            tags_by_name[tag_name].id for tag_name in dict.fromkeys(imported_tag_names)
        ]
    await _replace_professor_tag_links(
        session,
        replace_tags_by_professor_id,
    )
    for instance in list(session.identity_map.values()):
        if isinstance(instance, Professor):
            session.expire(instance)

    await record_operation_log(
        session,
        category="user_action",
        event_name=event_name,
        entity_type="professor",
        metadata={
            "actor": actor,
            "filename": filename,
            "inserted_count": inserted_count,
            "updated_count": updated_count,
            "created_tag_count": created_tag_count,
            "failed_count": parsed.failed_count,
            "row_count": len(parsed.data),
            "personal_note_column_present": personal_note_column_present,
        },
    )
    return ProfessorImportMutationResult(
        inserted_count=inserted_count,
        updated_count=updated_count,
        created_tag_count=created_tag_count,
        failed_count=parsed.failed_count,
    )


def _professor_import_upsert_statement(*, update_personal_note: bool):
    statement = sqlite_insert(Professor)
    excluded = statement.excluded
    update_values: dict[str, object] = {
        "name": excluded.name,
        "email": excluded.email,
        "title": excluded.title,
        "university": excluded.university,
        "school": excluded.school,
        "department": excluded.department,
        "research_direction": excluded.research_direction,
        "recent_papers": excluded.recent_papers,
        "profile_url": excluded.profile_url,
        "source_url": excluded.source_url,
        "archived_at": None,
        "updated_at": excluded.updated_at,
        "communication_sync_version": case(
            (
                Professor.archived_at.is_not(None),
                Professor.communication_sync_version + 1,
            ),
            else_=Professor.communication_sync_version,
        ),
    }
    if update_personal_note:
        update_values["personal_note"] = excluded.personal_note
    return statement.on_conflict_do_update(
        index_elements=[Professor.email],
        set_=update_values,
    )


async def _ensure_import_tags(
    session: AsyncSession,
    tag_names: list[str],
) -> tuple[dict[str, ProfessorTag], int]:
    if not tag_names:
        return {}, 0
    existing_tags: list[ProfessorTag] = []
    for name_chunk in chunked_values(tag_names):
        existing_tags.extend(
            await session.scalars(
                select(ProfessorTag).where(ProfessorTag.name.in_(name_chunk)),
            ),
        )
    existing_names = {tag.name for tag in existing_tags}
    missing_names = [name for name in tag_names if name not in existing_names]
    if missing_names:
        statement = sqlite_insert(ProfessorTag).on_conflict_do_nothing(
            index_elements=[ProfessorTag.name],
        )
        rows = [
            {
                "name": name,
                "text_color": DEFAULT_IMPORTED_TAG_TEXT_COLOR,
                "background_color": DEFAULT_IMPORTED_TAG_BACKGROUND_COLOR,
            }
            for name in missing_names
        ]
        for row_chunk in chunked_values(rows):
            await session.execute(statement, list(row_chunk))
    loaded_tags: list[ProfessorTag] = []
    for name_chunk in chunked_values(tag_names):
        loaded_tags.extend(
            await session.scalars(
                select(ProfessorTag).where(ProfessorTag.name.in_(name_chunk)),
            ),
        )
    return {tag.name: tag for tag in loaded_tags}, len(missing_names)


async def _replace_professor_tag_links(
    session: AsyncSession,
    tag_ids_by_professor_id: dict[int, list[int]],
) -> None:
    if not tag_ids_by_professor_id:
        return
    professor_ids = list(tag_ids_by_professor_id)
    for professor_id_chunk in chunked_values(professor_ids):
        await session.execute(
            delete(ProfessorTagLink).where(
                ProfessorTagLink.professor_id.in_(professor_id_chunk),
            ),
        )
    rows = [
        {
            "professor_id": professor_id,
            "tag_id": tag_id,
            "sort_order": sort_order,
        }
        for professor_id, tag_ids in tag_ids_by_professor_id.items()
        for sort_order, tag_id in enumerate(tag_ids)
    ]
    for row_chunk in chunked_values(rows):
        await session.execute(insert(ProfessorTagLink), list(row_chunk))


async def create_professor_tag_record(
    session: AsyncSession,
    payload: ProfessorTagPayload,
    *,
    event_name: str,
    actor: str,
) -> ProfessorTag:
    name = payload.name.strip()
    if not name:
        raise ProfessorMutationError(400, "TAG_NAME_REQUIRED", "标签名不能为空")
    existing = await session.scalar(
        select(ProfessorTag).where(ProfessorTag.name == name)
    )
    if existing is not None:
        raise ProfessorMutationError(409, "PROFESSOR_TAG_EXISTS", "标签已存在")
    tag = ProfessorTag(
        name=name,
        text_color=payload.text_color,
        background_color=payload.background_color,
    )
    session.add(tag)
    await session.flush()
    await record_operation_log(
        session,
        category="user_action",
        event_name=event_name,
        entity_type="professor_tag",
        entity_id=str(tag.id),
        metadata={"actor": actor, "name": tag.name},
    )
    return tag


async def get_professor_with_tags_or_raise(
    session: AsyncSession,
    professor_id: int,
) -> Professor:
    professor = await session.scalar(
        select(Professor)
        .options(selectinload(Professor.tags))
        .where(Professor.id == professor_id),
    )
    if professor is None:
        raise ProfessorMutationError(404, "PROFESSOR_NOT_FOUND", "未找到导师")
    return professor


async def sync_professor_tags(
    session: AsyncSession,
    professor: Professor,
    tag_ids: list[int],
) -> None:
    tags = await load_tags_by_ids(session, tag_ids)
    await session.execute(
        delete(ProfessorTagLink).where(ProfessorTagLink.professor_id == professor.id),
    )
    for sort_order, tag in enumerate(tags):
        session.add(
            ProfessorTagLink(
                professor_id=professor.id,
                tag_id=tag.id,
                sort_order=sort_order,
            ),
        )
    session.expire(professor, ["tags"])


async def load_tags_by_ids(
    session: AsyncSession,
    tag_ids: list[int],
) -> list[ProfessorTag]:
    if not tag_ids:
        return []
    if len(set(tag_ids)) != len(tag_ids):
        raise ProfessorMutationError(400, "TAG_IDS_DUPLICATE", "标签不能重复")
    tags: list[ProfessorTag] = []
    for tag_id_chunk in chunked_values(tag_ids):
        tags.extend(
            await session.scalars(
                select(ProfessorTag).where(ProfessorTag.id.in_(tag_id_chunk)),
            ),
        )
    tags_by_id = {tag.id: tag for tag in tags}
    missing = [tag_id for tag_id in tag_ids if tag_id not in tags_by_id]
    if missing:
        raise ProfessorMutationError(400, "TAG_NOT_FOUND", "标签不存在")
    return [tags_by_id[tag_id] for tag_id in tag_ids]


async def _load_professor_tag_with_usage(
    session: AsyncSession,
    tag_id: int,
) -> tuple[ProfessorTag, list[Professor]]:
    tag = await session.get(ProfessorTag, tag_id)
    if tag is None:
        raise ProfessorMutationError(404, "PROFESSOR_TAG_NOT_FOUND", "未找到标签")
    professors = list(
        (
            await session.scalars(
                select(Professor)
                .options(selectinload(Professor.tags))
                .join(
                    ProfessorTagLink,
                    ProfessorTagLink.professor_id == Professor.id,
                )
                .where(ProfessorTagLink.tag_id == tag_id)
                .order_by(Professor.name.asc(), Professor.id.asc()),
            )
        ).unique(),
    )
    return tag, professors


async def _resolve_bulk_professor_archive(
    session: AsyncSession,
    professor_ids: list[int],
) -> tuple[list[int], list[Professor]]:
    if not professor_ids:
        raise ProfessorMutationError(
            400, "PROFESSOR_IDS_REQUIRED", "请至少选择一位导师"
        )
    if len(set(professor_ids)) != len(professor_ids):
        raise ProfessorMutationError(400, "PROFESSOR_IDS_DUPLICATE", "导师 ID 不能重复")
    ordered_ids = list(professor_ids)
    professors = await _load_professors_by_ids(
        session,
        ordered_ids,
    )
    found_ids = {professor.id for professor in professors}
    if any(professor_id not in found_ids for professor_id in ordered_ids):
        raise ProfessorMutationError(404, "PROFESSOR_NOT_FOUND", "导师不存在")
    return ordered_ids, professors


async def _resolve_bulk_tag_update(
    session: AsyncSession,
    payload: ProfessorBulkTagsPayload,
) -> tuple[list[int], list[Professor], list[ProfessorTag]]:
    if not payload.professor_ids:
        raise ProfessorMutationError(
            400, "PROFESSOR_IDS_REQUIRED", "请至少选择一位导师"
        )
    if payload.mode in {"add", "remove"} and not payload.tag_ids:
        raise ProfessorMutationError(
            400, "TAG_IDS_REQUIRED", "请选择要追加或移除的标签"
        )

    professor_ids = list(dict.fromkeys(payload.professor_ids))
    professors = await _load_professors_by_ids(
        session,
        professor_ids,
        include_tags=True,
    )
    professors_by_id = {professor.id: professor for professor in professors}
    if any(professor_id not in professors_by_id for professor_id in professor_ids):
        raise ProfessorMutationError(404, "PROFESSOR_NOT_FOUND", "导师不存在")
    return professor_ids, professors, await load_tags_by_ids(session, payload.tag_ids)


def _next_bulk_tag_ids(
    current_tag_ids: list[int],
    *,
    mode: str,
    target_tag_ids: list[int],
) -> list[int]:
    if mode == "add":
        return current_tag_ids + [
            tag_id for tag_id in target_tag_ids if tag_id not in current_tag_ids
        ]
    if mode == "remove":
        target_tag_id_set = set(target_tag_ids)
        return [tag_id for tag_id in current_tag_ids if tag_id not in target_tag_id_set]
    return target_tag_ids


def _tags_for_ids(
    tag_ids: list[int],
    current_tags: list[ProfessorTag],
    target_tags: list[ProfessorTag],
) -> list[dict[str, object]]:
    tags_by_id = {tag.id: tag for tag in [*current_tags, *target_tags]}
    return [_serialize_tag_snapshot(tags_by_id[tag_id]) for tag_id in tag_ids]


def _serialize_tag_snapshot(tag: ProfessorTag) -> dict[str, object]:
    return {
        "id": tag.id,
        "name": tag.name,
        "text_color": tag.text_color,
        "background_color": tag.background_color,
    }


async def sync_professor_tags_by_names(
    session: AsyncSession,
    professor: Professor,
    tag_names: list[str],
) -> int:
    tags, created_count = await load_or_create_tags_by_names(session, tag_names)
    await sync_professor_tags(session, professor, [tag.id for tag in tags])
    return created_count


async def load_or_create_tags_by_names(
    session: AsyncSession,
    tag_names: list[str],
) -> tuple[list[ProfessorTag], int]:
    normalized_names = list(
        dict.fromkeys(name.strip() for name in tag_names if name.strip())
    )
    if not normalized_names:
        return [], 0
    existing_tags: list[ProfessorTag] = []
    for name_chunk in chunked_values(normalized_names):
        existing_tags.extend(
            await session.scalars(
                select(ProfessorTag).where(ProfessorTag.name.in_(name_chunk)),
            ),
        )
    tags_by_name = {tag.name: tag for tag in existing_tags}
    created_count = 0
    for name in normalized_names:
        if name in tags_by_name:
            continue
        tag = ProfessorTag(
            name=name,
            text_color=DEFAULT_IMPORTED_TAG_TEXT_COLOR,
            background_color=DEFAULT_IMPORTED_TAG_BACKGROUND_COLOR,
        )
        session.add(tag)
        await session.flush()
        tags_by_name[name] = tag
        created_count += 1
    return [tags_by_name[name] for name in normalized_names], created_count


async def _load_existing_import_professors(
    session: AsyncSession,
    parsed: ParsedProfessorImport,
) -> dict[str, Professor]:
    if not parsed.data:
        return {}
    professors: list[Professor] = []
    for email_chunk in chunked_values(parsed.data.keys()):
        professors.extend(
            (
                await session.scalars(
                    select(Professor)
                    .options(selectinload(Professor.tags))
                    .where(Professor.email.in_(email_chunk)),
                )
            ).unique(),
        )
    return {
        professor.email.lower(): professor
        for professor in professors
        if professor.email
    }


async def _load_existing_import_professor_ids(
    session: AsyncSession,
    emails: Iterable[object],
) -> dict[str, int]:
    normalized_emails = list(
        dict.fromkeys(
            str(email).strip().lower() for email in emails if str(email).strip()
        ),
    )
    professor_ids_by_email: dict[str, int] = {}
    for email_chunk in chunked_values(normalized_emails):
        rows = await session.execute(
            select(Professor.email, Professor.id).where(
                Professor.email.in_(email_chunk),
            ),
        )
        professor_ids_by_email.update(
            {
                str(email).lower(): int(professor_id)
                for email, professor_id in rows
                if email
            },
        )
    return professor_ids_by_email


def _collect_import_tag_names(parsed: ParsedProfessorImport) -> list[str]:
    return sorted(
        {
            str(tag_name).strip()
            for payload in parsed.data.values()
            for tag_name in payload.get("tag_names", [])
            if str(tag_name).strip()
        },
    )


async def _load_professors_by_ids(
    session: AsyncSession,
    professor_ids: list[int],
    *,
    include_tags: bool = False,
) -> list[Professor]:
    professors: list[Professor] = []
    for professor_id_chunk in chunked_values(unique_positive_ids(professor_ids)):
        statement = select(Professor).where(Professor.id.in_(professor_id_chunk))
        if include_tags:
            statement = statement.options(selectinload(Professor.tags))
        professors.extend((await session.scalars(statement)).unique())
    return professors


def _ids_metadata(professor_ids: list[int]) -> dict[str, object]:
    metadata_limit = 1_000
    return {
        "ids": professor_ids[:metadata_limit],
        "ids_truncated": len(professor_ids) > metadata_limit,
    }


def _serialize_import_professor_state(
    professor: Professor | None,
) -> dict[str, object] | None:
    if professor is None:
        return None
    return {
        "id": professor.id,
        "name": professor.name,
        "email": professor.email,
        "title": professor.title,
        "university": professor.university,
        "school": professor.school,
        "department": professor.department,
        "research_direction": professor.research_direction,
        "recent_papers": professor.recent_papers or [],
        "profile_url": professor.profile_url,
        "source_url": professor.source_url,
        "personal_note": professor.personal_note,
        "archived_at": serialize_api_datetime(professor.archived_at)
        if professor.archived_at is not None
        else None,
        "tags": [_serialize_tag_snapshot(tag) for tag in professor.tags],
    }


def ensure_professor_email_valid(email: str) -> None:
    if not is_valid_professor_email(email):
        raise ProfessorMutationError(400, "PROFESSOR_EMAIL_INVALID", "邮箱格式不正确")


async def record_professor_event(
    session: AsyncSession,
    professor: Professor,
    event_name: str,
    *,
    actor: str,
    metadata: dict[str, object] | None = None,
) -> None:
    event_metadata: dict[str, object] = {
        "actor": actor,
        "name": professor.name,
        "email": professor.email,
        "university": professor.university,
        "school": professor.school,
        "archived": professor.archived_at is not None,
    }
    if metadata:
        event_metadata.update(metadata)
    await record_operation_log(
        session,
        category="user_action",
        event_name=event_name,
        entity_type="professor",
        entity_id=str(professor.id),
        metadata=event_metadata,
    )
