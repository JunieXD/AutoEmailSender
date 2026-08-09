from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any

from sqlalchemy import and_, case, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import lazyload, load_only, selectinload

from app.core.time import as_utc_aware, local_now, utc_now
from app.core.query_chunks import chunked_values, unique_positive_ids
from app.models import (
    EmailDirection,
    EmailLog,
    EmailTask,
    EmailTaskStatus,
    IdentityProfile,
    Professor,
)
from app.modules.communications.public import (
    CommunicationEvent,
    load_communication_events,
)
from app.modules.identities.public import resolve_identity_communication_scope
from app.modules.professors.query import (
    _dashboard_summary_expressions,
    _join_dashboard_summaries,
)
from app.schemas.dashboard import (
    DashboardEmailFollowUpRead,
    DashboardEmailFunnelBucketRead,
    DashboardEmailSectionRead,
    DashboardEmailStatusBucketRead,
    DashboardEmailSummaryRead,
    DashboardEmailTrendBucketRead,
    DashboardMatchContextRead,
    DashboardMentorActionItemRead,
    DashboardMentorFilterRead,
    DashboardMentorMatchBucketRead,
    DashboardMentorSectionRead,
    DashboardMentorSummaryRead,
    DashboardOutreachCoverageItemRead,
    DashboardOutreachCoverageRead,
    DashboardOverviewRead,
    DashboardProfileCompletenessBucketRead,
    DashboardProfileCompletenessRead,
    DashboardReplyWaitBucketRead,
    DashboardReplyWaitRead,
    DashboardSchoolDistributionRead,
    DashboardSchoolFilterRead,
    DashboardSchoolFilterSchoolRead,
)
from app.services.contact_status import build_contact_status_by_professor
from app.services.match_results import (
    MatchResultView,
    ResolvedMatchResults,
    load_resolved_match_results,
    match_result_is_stale,
    resolve_identity_match_scope,
)

HIGH_SCORE_DEFAULT = 80
DASHBOARD_DISTRIBUTION_LIMIT = 50
EmailTrendEvent = tuple[int | None, int, datetime]

PROFESSOR_STATUS_LABELS: dict[str, str] = {
    "not_contacted": "未联系",
    "preparing": "准备中",
    "ready_to_send": "待发送",
    "contacted": "已联系",
    "replied": "已回复",
    "failed": "失败",
}

EMAIL_TASK_STATUS_LABELS: dict[str, str] = {
    EmailTaskStatus.DISCOVERED.value: "已发现",
    EmailTaskStatus.MATCHED.value: "已匹配",
    EmailTaskStatus.GENERATING_DRAFT.value: "草稿生成中",
    EmailTaskStatus.DRAFT_FAILED.value: "草稿失败",
    EmailTaskStatus.REVIEW_REQUIRED.value: "待审核",
    EmailTaskStatus.APPROVED.value: "已批准",
    EmailTaskStatus.SCHEDULED.value: "已排程",
    EmailTaskStatus.SENDING.value: "发送中",
    EmailTaskStatus.SENT.value: "已发送",
    EmailTaskStatus.SEND_FAILED.value: "发送失败",
    EmailTaskStatus.REPLY_DETECTED.value: "已回复",
    EmailTaskStatus.CANCELED.value: "已取消",
}


async def build_dashboard_overview(
    session: AsyncSession,
    *,
    identity_id: int,
    llm_profile_id: int | None = None,
    university: str | None = None,
    school: str | None = None,
    email_university: str | None = None,
    email_school: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> DashboardOverviewRead:
    identity = await session.get(IdentityProfile, identity_id)
    if identity is None:
        raise ValueError("未找到身份")
    communication_scope = await resolve_identity_communication_scope(
        session,
        active_identity_id=identity_id,
    )
    match_scope = await resolve_identity_match_scope(
        session,
        active_identity_id=identity_id,
    )
    dashboard_expressions = _dashboard_summary_expressions(
        identity_id=identity_id,
        communication_identity_ids=communication_scope.identity_ids,
        match_source_identity_id=match_scope.source_identity_id,
    )
    mentor_section = await _build_mentor_section_from_database(
        session,
        identity=identity,
        match_scope=match_scope,
        expressions=dashboard_expressions,
        university=university,
        school=school,
    )

    task_status_counts, task_total_count, funnel, status_distribution = (
        await _load_dashboard_task_metrics(
            session,
            identity_id=identity_id,
        )
    )
    fallback_tasks = list(
        await session.scalars(
            select(EmailTask)
            .options(
                load_only(
                    EmailTask.id,
                    EmailTask.professor_id,
                    EmailTask.status,
                    EmailTask.created_at,
                    EmailTask.sent_at,
                    EmailTask.is_replied,
                    EmailTask.updated_at,
                ),
            )
            .where(
                EmailTask.identity_id == identity_id,
                EmailTask.parent_task_id.is_(None),
                EmailTask.batch_send_canceled_at.is_(None),
                or_(
                    EmailTask.sent_at.is_not(None),
                    EmailTask.is_replied.is_(True),
                    EmailTask.status.in_(
                        [
                            EmailTaskStatus.SENT.value,
                            EmailTaskStatus.REPLY_DETECTED.value,
                        ],
                    ),
                ),
            )
            .order_by(
                EmailTask.professor_id.asc(),
                EmailTask.created_at.desc(),
                EmailTask.id.desc(),
            ),
        ),
    )
    follow_ups = await _build_email_follow_ups_from_database(
        session,
        identity_id=identity_id,
        expressions=dashboard_expressions,
        threshold=identity.match_threshold or HIGH_SCORE_DEFAULT,
    )

    email_section = await _build_email_section(
        session,
        tasks=fallback_tasks,
        task_status_counts=task_status_counts,
        task_total_count=task_total_count,
        funnel=funnel,
        status_distribution=status_distribution,
        follow_ups=follow_ups,
        identity_id=identity_id,
        communication_identity_ids=communication_scope.identity_ids,
        email_university=email_university,
        email_school=email_school,
        start_date=start_date,
        end_date=end_date,
    )

    return DashboardOverviewRead(mentor=mentor_section, email=email_section)


def _sql_text_present(column: Any):
    return func.trim(func.coalesce(column, "")) != ""


def _sql_recent_papers_present():
    return func.json_array_length(func.coalesce(Professor.recent_papers, "[]")) > 0


def _sql_professor_label(column: Any, missing_label: str):
    return func.coalesce(func.nullif(func.trim(column), ""), missing_label)


def _mentor_database_conditions(
    *,
    university: str | None,
    school: str | None,
) -> list[Any]:
    conditions: list[Any] = [Professor.archived_at.is_(None)]
    normalized_university = _normalize_filter_value(university)
    normalized_school = _normalize_filter_value(school)
    if normalized_university is not None:
        conditions.append(
            _sql_professor_label(Professor.university, "学校未填写")
            == normalized_university,
        )
    if normalized_school is not None:
        conditions.append(
            _sql_professor_label(Professor.school, "学院未填写")
            == normalized_school,
        )
    return conditions


def _dashboard_root_latest_task(identity_id: int):
    ranked = (
        select(
            EmailTask.id.label("task_id"),
            EmailTask.professor_id.label("professor_id"),
            EmailTask.status.label("status"),
            EmailTask.updated_at.label("updated_at"),
            func.row_number()
            .over(
                partition_by=EmailTask.professor_id,
                order_by=(EmailTask.created_at.desc(), EmailTask.id.desc()),
            )
            .label("row_number"),
        )
        .where(
            EmailTask.identity_id == identity_id,
            EmailTask.parent_task_id.is_(None),
            EmailTask.batch_send_canceled_at.is_(None),
        )
        .subquery("dashboard_root_ranked_tasks")
    )
    return (
        select(
            ranked.c.task_id,
            ranked.c.professor_id,
            ranked.c.status,
            ranked.c.updated_at,
        )
        .where(ranked.c.row_number == 1)
        .subquery("dashboard_root_latest_task")
    )


async def _build_mentor_section_from_database(
    session: AsyncSession,
    *,
    identity: IdentityProfile,
    match_scope: Any,
    expressions: dict[str, Any],
    university: str | None,
    school: str | None,
) -> DashboardMentorSectionRead:
    threshold = identity.match_threshold or HIGH_SCORE_DEFAULT
    conditions = _mentor_database_conditions(
        university=university,
        school=school,
    )
    match_score = expressions["match_score"]
    status = expressions["status"]
    has_email = _sql_text_present(Professor.email)
    has_research = _sql_text_present(Professor.research_direction)
    has_papers = _sql_recent_papers_present()
    has_profile = _sql_text_present(Professor.profile_url)
    missing_count = (
        case((has_email, 0), else_=1)
        + case((has_research, 0), else_=1)
        + case((has_papers, 0), else_=1)
        + case((has_profile, 0), else_=1)
    )
    high_score_uncontacted_condition = and_(
        match_score >= threshold,
        status.in_(["not_contacted", "preparing", "ready_to_send"]),
    )

    aggregate_statement = _join_dashboard_summaries(
        select(
            func.count(Professor.id).label("total"),
            func.sum(case((match_score.is_not(None), 1), else_=0)).label("matched"),
            func.sum(case((match_score >= threshold, 1), else_=0)).label("high_match"),
            func.sum(
                case((high_score_uncontacted_condition, 1), else_=0),
            ).label("high_uncontacted"),
            func.sum(case((match_score.is_(None), 1), else_=0)).label("unmatched"),
            func.sum(case((match_score < 60, 1), else_=0)).label("score_0_59"),
            func.sum(
                case((and_(match_score >= 60, match_score < 70), 1), else_=0),
            ).label("score_60_69"),
            func.sum(
                case((and_(match_score >= 70, match_score < 80), 1), else_=0),
            ).label("score_70_79"),
            func.sum(
                case((and_(match_score >= 80, match_score < 90), 1), else_=0),
            ).label("score_80_89"),
            func.sum(case((match_score >= 90, 1), else_=0)).label("score_90_100"),
            func.sum(case((has_email, 1), else_=0)).label("has_email"),
            func.sum(case((has_research, 1), else_=0)).label("has_research"),
            func.sum(case((has_papers, 1), else_=0)).label("has_papers"),
            func.sum(case((has_profile, 1), else_=0)).label("has_profile"),
            func.sum(
                case(
                    (and_(has_email, has_research, or_(has_papers, has_profile)), 1),
                    else_=0,
                ),
            ).label("complete_metric"),
            func.sum(case((missing_count == 0, 1), else_=0)).label("bucket_complete"),
            func.sum(
                case((and_(missing_count == 1, ~has_email), 1), else_=0),
            ).label("bucket_missing_email"),
            func.sum(
                case((and_(missing_count == 1, ~has_research), 1), else_=0),
            ).label("bucket_missing_research"),
            func.sum(
                case((and_(missing_count == 1, ~has_papers), 1), else_=0),
            ).label("bucket_missing_papers"),
            func.sum(
                case((and_(missing_count == 1, ~has_profile), 1), else_=0),
            ).label("bucket_missing_profile"),
            func.sum(case((missing_count > 1, 1), else_=0)).label("bucket_multiple"),
        ).select_from(Professor),
        expressions["joins"],
    ).where(*conditions)
    aggregate = (await session.execute(aggregate_statement)).one()._mapping

    root_latest_task = _dashboard_root_latest_task(identity.id)
    action_updated_at = func.coalesce(
        expressions["match_updated_at"],
        root_latest_task.c.updated_at,
        Professor.updated_at,
    )
    high_score_statement = _join_dashboard_summaries(
        select(
            Professor.id.label("professor_id"),
            Professor.name,
            Professor.university,
            Professor.school,
            Professor.department,
            match_score.label("match_score"),
            status.label("status"),
            action_updated_at.label("action_updated_at"),
        ).select_from(Professor),
        expressions["joins"],
    ).outerjoin(
        root_latest_task,
        root_latest_task.c.professor_id == Professor.id,
    ).where(
        *conditions,
        high_score_uncontacted_condition,
    ).order_by(
        match_score.desc(),
        action_updated_at.desc(),
        Professor.name.asc(),
    ).limit(8)
    high_score_rows = (await session.execute(high_score_statement)).mappings().all()

    incomplete_statement = _join_dashboard_summaries(
        select(
            Professor.id.label("professor_id"),
            Professor.name,
            Professor.email,
            Professor.university,
            Professor.school,
            Professor.department,
            Professor.research_direction,
            Professor.recent_papers,
            Professor.profile_url,
            match_score.label("match_score"),
            status.label("status"),
            action_updated_at.label("action_updated_at"),
            missing_count.label("missing_count"),
        ).select_from(Professor),
        expressions["joins"],
    ).outerjoin(
        root_latest_task,
        root_latest_task.c.professor_id == Professor.id,
    ).where(
        *conditions,
        missing_count > 0,
    ).order_by(
        missing_count.desc(),
        Professor.updated_at.desc(),
        Professor.name.asc(),
    ).limit(8)
    incomplete_rows = (await session.execute(incomplete_statement)).mappings().all()

    university_label = _sql_professor_label(Professor.university, "学校未填写")
    school_label = _sql_professor_label(Professor.school, "学院未填写")
    distribution_rows = (
        await session.execute(
            select(
                university_label.label("university"),
                func.count(Professor.id).label("count"),
            )
            .where(Professor.archived_at.is_(None))
            .group_by(university_label)
            .order_by(func.count(Professor.id).desc(), university_label.asc())
            .limit(DASHBOARD_DISTRIBUTION_LIMIT),
        )
    ).all()
    school_filter_rows = (
        await session.execute(
            select(
                university_label.label("university"),
                school_label.label("school"),
                func.count(Professor.id).label("count"),
            )
            .where(Professor.archived_at.is_(None))
            .group_by(university_label, school_label),
        )
    ).all()
    schools_by_university: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for row in school_filter_rows:
        schools_by_university[str(row.university)].append(
            (str(row.school), int(row.count)),
        )
    school_filters = [
        DashboardSchoolFilterRead(
            university=university_name,
            count=sum(count for _, count in schools),
            schools=[
                DashboardSchoolFilterSchoolRead(school_name=school_name, count=count)
                for school_name, count in sorted(schools, key=lambda item: (-item[1], item[0]))
            ],
        )
        for university_name, schools in schools_by_university.items()
    ]
    school_filters.sort(key=lambda item: (-item.count, item.university))

    source_material_id = match_scope.source_identity.current_primary_material_id
    stale_condition = match_score.is_not(None)
    if source_material_id is not None:
        stale_condition = and_(
            stale_condition,
            or_(
                expressions["match_primary_material_id"].is_(None),
                expressions["match_primary_material_id"] != source_material_id,
            ),
        )
    stale_statement = _join_dashboard_summaries(
        select(
            func.sum(case((stale_condition, 1), else_=0)),
        ).select_from(Professor),
        expressions["joins"],
    ).where(Professor.archived_at.is_(None))
    stale_result_count = int((await session.scalar(stale_statement)) or 0)

    total = int(aggregate["total"] or 0)
    matched = int(aggregate["matched"] or 0)
    profile_counts = {
        "email": int(aggregate["has_email"] or 0),
        "research_direction": int(aggregate["has_research"] or 0),
        "recent_papers": int(aggregate["has_papers"] or 0),
        "profile_url": int(aggregate["has_profile"] or 0),
        "complete": int(aggregate["complete_metric"] or 0),
    }
    profile_labels = {
        "email": "有邮箱",
        "research_direction": "有研究方向",
        "recent_papers": "有近期论文",
        "profile_url": "有主页链接",
        "complete": "完整资料",
    }
    bucket_counts = {
        "complete": int(aggregate["bucket_complete"] or 0),
        "missing_email": int(aggregate["bucket_missing_email"] or 0),
        "missing_research_direction": int(aggregate["bucket_missing_research"] or 0),
        "missing_recent_papers": int(aggregate["bucket_missing_papers"] or 0),
        "missing_profile_url": int(aggregate["bucket_missing_profile"] or 0),
        "multiple_missing": int(aggregate["bucket_multiple"] or 0),
    }
    match_buckets = [
        ("unmatched", "未分析", "unmatched"),
        ("0_59", "0-59", "score_0_59"),
        ("60_69", "60-69", "score_60_69"),
        ("70_79", "70-79", "score_70_79"),
        ("80_89", "80-89", "score_80_89"),
        ("90_100", "90-100", "score_90_100"),
    ]

    return DashboardMentorSectionRead(
        summary=DashboardMentorSummaryRead(
            total_professors=total,
            matched_professors=matched,
            matched_rate=(matched / total) if total else 0.0,
            high_match_professors=int(aggregate["high_match"] or 0),
            high_score_uncontacted_count=int(aggregate["high_uncontacted"] or 0),
            high_score_threshold=threshold,
        ),
        match_context=DashboardMatchContextRead(
            source_identity_id=match_scope.source_identity_id,
            source_identity_name=(
                match_scope.source_identity.profile_name
                or match_scope.source_identity.name
            ),
            source_identity_email=match_scope.source_identity.email_address,
            source_material_id=source_material_id,
            source_material_name=(
                match_scope.source_identity.current_primary_material.display_name
                if match_scope.source_identity.current_primary_material is not None
                else None
            ),
            uses_group_match_source=match_scope.uses_group_match_source,
            stale_result_count=stale_result_count,
        ),
        match_score_distribution=[
            DashboardMentorMatchBucketRead(
                bucket=bucket,
                label=label,
                count=int(aggregate[column] or 0),
            )
            for bucket, label, column in match_buckets
        ],
        profile_completeness=[
            DashboardProfileCompletenessRead(
                key=key,
                label=profile_labels[key],
                count=count,
                total=total,
                rate=(count / total) if total else 0.0,
            )
            for key, count in profile_counts.items()
        ],
        profile_completeness_distribution=[
            DashboardProfileCompletenessBucketRead(
                key=key,
                label=label,
                count=bucket_counts[key],
                total=total,
                rate=(bucket_counts[key] / total) if total else 0.0,
            )
            for key, label in PROFILE_COMPLETENESS_BUCKET_LABELS.items()
        ],
        school_distribution=[
            DashboardSchoolDistributionRead(
                school_name=str(row.university),
                count=int(row.count),
            )
            for row in distribution_rows
        ],
        school_filters=school_filters,
        active_filter=DashboardMentorFilterRead(
            university=_normalize_filter_value(university),
            school=_normalize_filter_value(school),
        ),
        high_score_uncontacted=[
            DashboardMentorActionItemRead(
                professor_id=int(row["professor_id"]),
                name=str(row["name"]),
                university=row["university"],
                school=row["school"],
                department=row["department"],
                match_score=int(row["match_score"]),
                status=str(row["status"]),
                status_label=PROFESSOR_STATUS_LABELS.get(str(row["status"]), str(row["status"])),
                reason=_build_mentor_follow_up_reason(status=str(row["status"])),
                updated_at=row["action_updated_at"],
            )
            for row in high_score_rows
        ],
        incomplete_professors=[
            DashboardMentorActionItemRead(
                professor_id=int(row["professor_id"]),
                name=str(row["name"]),
                university=row["university"],
                school=row["school"],
                department=row["department"],
                match_score=(
                    int(row["match_score"])
                    if row["match_score"] is not None
                    else None
                ),
                status=str(row["status"]),
                status_label=PROFESSOR_STATUS_LABELS.get(str(row["status"]), str(row["status"])),
                reason="资料待补全",
                updated_at=row["action_updated_at"],
                missing_fields=_build_missing_fields_from_values(
                    email=row["email"],
                    research_direction=row["research_direction"],
                    recent_papers=row["recent_papers"],
                    profile_url=row["profile_url"],
                ),
            )
            for row in incomplete_rows
        ],
    )


def _build_missing_fields_from_values(
    *,
    email: str | None,
    research_direction: str | None,
    recent_papers: object,
    profile_url: str | None,
) -> list[str]:
    missing_fields: list[str] = []
    if not _has_text(email):
        missing_fields.append("邮箱")
    if not _has_text(research_direction):
        missing_fields.append("研究方向")
    if not (
        isinstance(recent_papers, list)
        and any(_has_text(item) for item in recent_papers)
    ):
        missing_fields.append("近期论文")
    if not _has_text(profile_url):
        missing_fields.append("主页链接")
    return missing_fields


async def _load_dashboard_task_metrics(
    session: AsyncSession,
    *,
    identity_id: int,
) -> tuple[
    dict[str, int],
    int,
    list[DashboardEmailFunnelBucketRead],
    list[DashboardEmailStatusBucketRead],
]:
    rows = (
        await session.execute(
            select(
                EmailTask.status,
                func.count(EmailTask.id).label("count"),
                func.sum(
                    case(
                        (
                            or_(
                                EmailTask.is_replied.is_(True),
                                EmailTask.status
                                == EmailTaskStatus.REPLY_DETECTED.value,
                            ),
                            1,
                        ),
                        else_=0,
                    ),
                ).label("replied_count"),
            )
            .where(
                EmailTask.identity_id == identity_id,
                EmailTask.parent_task_id.is_(None),
                EmailTask.batch_send_canceled_at.is_(None),
            )
            .group_by(EmailTask.status),
        )
    ).all()
    status_counts = {str(row.status): int(row.count) for row in rows}
    replied_count = sum(int(row.replied_count or 0) for row in rows)
    total_count = sum(status_counts.values())

    funnel_statuses = {
        "matched": {
            EmailTaskStatus.MATCHED.value,
            EmailTaskStatus.GENERATING_DRAFT.value,
            EmailTaskStatus.DRAFT_FAILED.value,
            EmailTaskStatus.REVIEW_REQUIRED.value,
            EmailTaskStatus.APPROVED.value,
            EmailTaskStatus.SCHEDULED.value,
            EmailTaskStatus.SENDING.value,
            EmailTaskStatus.SENT.value,
            EmailTaskStatus.SEND_FAILED.value,
            EmailTaskStatus.REPLY_DETECTED.value,
        },
        "generating_draft": {
            EmailTaskStatus.GENERATING_DRAFT.value,
            EmailTaskStatus.DRAFT_FAILED.value,
            EmailTaskStatus.REVIEW_REQUIRED.value,
            EmailTaskStatus.APPROVED.value,
            EmailTaskStatus.SCHEDULED.value,
            EmailTaskStatus.SENDING.value,
            EmailTaskStatus.SENT.value,
            EmailTaskStatus.SEND_FAILED.value,
            EmailTaskStatus.REPLY_DETECTED.value,
        },
        "review_required": {
            EmailTaskStatus.REVIEW_REQUIRED.value,
            EmailTaskStatus.APPROVED.value,
            EmailTaskStatus.SCHEDULED.value,
            EmailTaskStatus.SENDING.value,
            EmailTaskStatus.SENT.value,
            EmailTaskStatus.SEND_FAILED.value,
            EmailTaskStatus.REPLY_DETECTED.value,
        },
        "approved": {
            EmailTaskStatus.APPROVED.value,
            EmailTaskStatus.SCHEDULED.value,
            EmailTaskStatus.SENDING.value,
            EmailTaskStatus.SENT.value,
            EmailTaskStatus.SEND_FAILED.value,
            EmailTaskStatus.REPLY_DETECTED.value,
        },
        "scheduled": {
            EmailTaskStatus.SCHEDULED.value,
            EmailTaskStatus.SENDING.value,
            EmailTaskStatus.SENT.value,
            EmailTaskStatus.REPLY_DETECTED.value,
        },
        "sent": {
            EmailTaskStatus.SENT.value,
            EmailTaskStatus.REPLY_DETECTED.value,
        },
    }
    funnel_labels = {
        "matched": "已匹配",
        "generating_draft": "草稿生成中",
        "review_required": "待审核",
        "approved": "已批准",
        "scheduled": "已排程",
        "sent": "已发送",
        "replied": "已回复",
    }
    funnel_counts = {
        key: sum(status_counts.get(status, 0) for status in statuses)
        for key, statuses in funnel_statuses.items()
    }
    funnel_counts["replied"] = replied_count
    funnel = [
        DashboardEmailFunnelBucketRead(
            key=key,
            label=funnel_labels[key],
            count=funnel_counts[key],
        )
        for key in (
            "matched",
            "generating_draft",
            "review_required",
            "approved",
            "scheduled",
            "sent",
            "replied",
        )
    ]
    ordered_statuses = [
        EmailTaskStatus.MATCHED.value,
        EmailTaskStatus.GENERATING_DRAFT.value,
        EmailTaskStatus.DRAFT_FAILED.value,
        EmailTaskStatus.REVIEW_REQUIRED.value,
        EmailTaskStatus.APPROVED.value,
        EmailTaskStatus.SCHEDULED.value,
        EmailTaskStatus.SENDING.value,
        EmailTaskStatus.SENT.value,
        EmailTaskStatus.SEND_FAILED.value,
        EmailTaskStatus.REPLY_DETECTED.value,
        EmailTaskStatus.CANCELED.value,
    ]
    status_distribution = [
        DashboardEmailStatusBucketRead(
            status=status,
            label=EMAIL_TASK_STATUS_LABELS.get(status, status),
            count=status_counts.get(status, 0),
        )
        for status in ordered_statuses
    ]
    return status_counts, total_count, funnel, status_distribution


async def _build_email_follow_ups_from_database(
    session: AsyncSession,
    *,
    identity_id: int,
    expressions: dict[str, Any],
    threshold: int,
) -> list[DashboardEmailFollowUpRead]:
    root_latest_task = _dashboard_root_latest_task(identity_id)
    status = expressions["status"]
    match_score = expressions["match_score"]
    updated_at = func.coalesce(
        expressions["match_updated_at"],
        root_latest_task.c.updated_at,
    )
    priority = case(
        (status == "failed", 0),
        (status == "contacted", 1),
        (status == "ready_to_send", 2),
        (status == "preparing", 3),
        (status == "not_contacted", 4),
        else_=5,
    )
    statement = _join_dashboard_summaries(
        select(
            Professor.id.label("professor_id"),
            Professor.name,
            Professor.university,
            Professor.school,
            Professor.department,
            root_latest_task.c.task_id,
            match_score.label("match_score"),
            status.label("status"),
            updated_at.label("action_updated_at"),
        ).select_from(Professor),
        expressions["joins"],
    ).join(
        root_latest_task,
        root_latest_task.c.professor_id == Professor.id,
    ).where(
        Professor.archived_at.is_(None),
        match_score >= threshold,
        status != "replied",
    ).order_by(
        priority.asc(),
        match_score.desc(),
        updated_at.desc(),
        Professor.name.asc(),
    ).limit(8)
    rows = (await session.execute(statement)).mappings().all()
    return [
        DashboardEmailFollowUpRead(
            professor_id=int(row["professor_id"]),
            task_id=int(row["task_id"]),
            name=str(row["name"]),
            university=row["university"],
            school=row["school"],
            department=row["department"],
            match_score=int(row["match_score"]),
            status=str(row["status"]),
            status_label=PROFESSOR_STATUS_LABELS.get(
                str(row["status"]),
                str(row["status"]),
            ),
            reason=_build_email_follow_up_reason(status=str(row["status"])),
            updated_at=row["action_updated_at"],
        )
        for row in rows
    ]


def _build_mentor_section(
    *,
    professors: list[Professor],
    filtered_professors: list[Professor],
    latest_task_by_professor: dict[int, EmailTask],
    latest_match_score_by_professor: dict[int, int],
    resolved_matches: ResolvedMatchResults,
    professor_status_by_id: dict[int, str],
    threshold: int,
    active_university: str | None,
    active_school: str | None,
) -> DashboardMentorSectionRead:
    filtered_professor_ids = {professor.id for professor in filtered_professors}
    total_professors = len(filtered_professors)
    matched_professors = sum(1 for professor in filtered_professors if professor.id in latest_match_score_by_professor)
    high_match_professors = sum(
        1
        for professor in filtered_professors
        if latest_match_score_by_professor.get(professor.id) is not None
        and latest_match_score_by_professor[professor.id] >= threshold
    )

    high_score_uncontacted = [
        _serialize_professor_action_item(
            professor,
            task=latest_task_by_professor.get(professor.id),
            match_result=resolved_matches.get(professor.id),
            status=professor_status_by_id.get(professor.id, "not_contacted"),
            reason=_build_mentor_follow_up_reason(
                status=professor_status_by_id.get(professor.id, "not_contacted"),
            ),
            threshold=threshold,
        )
        for professor in filtered_professors
        if latest_match_score_by_professor.get(professor.id) is not None
        and latest_match_score_by_professor[professor.id] >= threshold
        and professor_status_by_id.get(professor.id) in {"not_contacted", "preparing", "ready_to_send"}
    ]
    high_score_uncontacted.sort(
        key=lambda item: (
            -(item.match_score or 0),
            -item.updated_at.timestamp(),
            item.name,
        ),
    )

    incomplete_professors = [
        _serialize_professor_action_item(
            professor,
            task=latest_task_by_professor.get(professor.id),
            match_result=resolved_matches.get(professor.id),
            status=professor_status_by_id.get(professor.id, "not_contacted"),
            reason="资料待补全",
            threshold=threshold,
            include_missing_fields=True,
        )
        for professor in filtered_professors
        if _build_missing_fields(professor)
    ]
    incomplete_professors.sort(
        key=lambda item: (
            -(len(item.missing_fields)),
            -item.updated_at.timestamp(),
            item.name,
        ),
    )

    return DashboardMentorSectionRead(
        summary=DashboardMentorSummaryRead(
            total_professors=total_professors,
            matched_professors=matched_professors,
            matched_rate=(matched_professors / total_professors) if total_professors else 0.0,
            high_match_professors=high_match_professors,
            high_score_uncontacted_count=len(
                [
                    professor_id
                    for professor_id in filtered_professor_ids
                    if latest_match_score_by_professor.get(professor_id) is not None
                    and latest_match_score_by_professor[professor_id] >= threshold
                    and professor_status_by_id.get(professor_id) in {"not_contacted", "preparing", "ready_to_send"}
                ]
            ),
            high_score_threshold=threshold,
        ),
        match_context=DashboardMatchContextRead(
            source_identity_id=resolved_matches.scope.source_identity_id,
            source_identity_name=(
                resolved_matches.scope.source_identity.profile_name
                or resolved_matches.scope.source_identity.name
            ),
            source_identity_email=resolved_matches.scope.source_identity.email_address,
            source_material_id=(
                resolved_matches.scope.source_identity.current_primary_material_id
            ),
            source_material_name=(
                resolved_matches.scope.source_identity.current_primary_material.display_name
                if resolved_matches.scope.source_identity.current_primary_material is not None
                else None
            ),
            uses_group_match_source=(
                resolved_matches.scope.uses_group_match_source
            ),
            stale_result_count=sum(
                1
                for result in resolved_matches.by_professor_id.values()
                if match_result_is_stale(
                    result,
                    resolved_matches.scope.source_identity,
                )
            ),
        ),
        match_score_distribution=_build_match_score_distribution(
            professors=filtered_professors,
            latest_match_score_by_professor=latest_match_score_by_professor,
        ),
        profile_completeness=_build_profile_completeness(filtered_professors),
        profile_completeness_distribution=_build_profile_completeness_distribution(filtered_professors),
        school_distribution=_build_school_distribution(professors),
        school_filters=_build_school_filters(professors),
        active_filter=DashboardMentorFilterRead(
            university=active_university,
            school=active_school,
        ),
        high_score_uncontacted=high_score_uncontacted[:8],
        incomplete_professors=incomplete_professors[:8],
    )


def _normalize_filter_value(value: str | None) -> str | None:
    normalized = value.strip() if value else None
    return normalized or None


def _normalize_school_label(value: str | None) -> str:
    return value.strip() if value and value.strip() else "学校未填写"


def _normalize_college_label(value: str | None) -> str:
    return value.strip() if value and value.strip() else "学院未填写"


@dataclass(frozen=True, slots=True)
class _ProfessorSchoolRef:
    id: int
    university: str | None
    school: str | None


async def _load_active_professor_school_refs(
    session: AsyncSession,
    professor_ids: list[int],
) -> dict[int, _ProfessorSchoolRef]:
    refs: dict[int, _ProfessorSchoolRef] = {}
    for id_chunk in chunked_values(unique_positive_ids(professor_ids)):
        rows = await session.execute(
            select(Professor.id, Professor.university, Professor.school).where(
                Professor.archived_at.is_(None),
                Professor.id.in_(id_chunk),
            ),
        )
        for professor_id, university, school in rows:
            refs[int(professor_id)] = _ProfessorSchoolRef(
                id=int(professor_id),
                university=university,
                school=school,
            )
    return refs


async def _count_active_professors_for_school_filter(
    session: AsyncSession,
    *,
    university: str | None,
    school: str | None,
) -> int:
    return int(
        (
            await session.scalar(
                select(func.count(Professor.id)).where(
                    *_mentor_database_conditions(
                        university=university,
                        school=school,
                    ),
                ),
            )
        )
        or 0,
    )


def _filter_professors_for_mentor_analysis(
    professors: list[Professor],
    *,
    university: str | None,
    school: str | None,
) -> list[Professor]:
    normalized_university = _normalize_filter_value(university)
    normalized_school = _normalize_filter_value(school)

    filtered = professors
    if normalized_university is not None:
        filtered = [
            professor
            for professor in filtered
            if _normalize_school_label(professor.university) == normalized_university
        ]
    if normalized_school is not None:
        filtered = [
            professor
            for professor in filtered
            if _normalize_college_label(professor.school) == normalized_school
        ]
    return filtered


def _professor_matches_school_filters(
    professor: Professor | _ProfessorSchoolRef | None,
    *,
    university: str | None,
    school: str | None,
) -> bool:
    if professor is None:
        return False
    normalized_university = _normalize_filter_value(university)
    normalized_school = _normalize_filter_value(school)
    if normalized_university is not None and _normalize_school_label(professor.university) != normalized_university:
        return False
    if normalized_school is not None and _normalize_college_label(professor.school) != normalized_school:
        return False
    return True


async def _build_email_section(
    session: AsyncSession,
    *,
    tasks: list[EmailTask],
    task_status_counts: dict[str, int],
    task_total_count: int,
    funnel: list[DashboardEmailFunnelBucketRead],
    status_distribution: list[DashboardEmailStatusBucketRead],
    follow_ups: list[DashboardEmailFollowUpRead],
    identity_id: int,
    communication_identity_ids: tuple[int, ...],
    email_university: str | None,
    email_school: str | None,
    start_date: str | None,
    end_date: str | None,
) -> DashboardEmailSectionRead:
    local_timezone = _local_timezone()
    start_at = _parse_date_filter(
        start_date,
        field_name="start_date",
        local_timezone=local_timezone,
    )
    end_at = _end_of_day(
        _parse_date_filter(
            end_date,
            field_name="end_date",
            local_timezone=local_timezone,
        ),
        local_timezone=local_timezone,
    )
    if start_at is not None and end_at is not None and start_at > end_at:
        raise ValueError("start_date 不能晚于 end_date")

    communication_events = await load_communication_events(
        session,
        identity_ids=communication_identity_ids,
        professor_ids=None,
        include_message_content=False,
        include_source_identities=False,
        include_professors=False,
    )
    relevant_professor_ids = [task.professor_id for task in tasks]
    relevant_professor_ids.extend(
        event.log.professor_id
        for event in communication_events
        if event.log.professor_id is not None
    )
    professor_by_id = await _load_active_professor_school_refs(
        session,
        relevant_professor_ids,
    )
    active_professor_ids = set(professor_by_id)
    sent_events_from_logs = [
        event
        for event in communication_events
        if event.log.direction == EmailDirection.SENT.value and event.successful
    ]
    received_events = [
        event
        for event in communication_events
        if event.log.direction == EmailDirection.RECEIVED.value
    ]

    review_required_count = task_status_counts.get(
        EmailTaskStatus.REVIEW_REQUIRED.value,
        0,
    )
    scheduled_count = task_status_counts.get(EmailTaskStatus.SCHEDULED.value, 0)
    sent_log_task_ids = {
        log.email_task_id
        for event in sent_events_from_logs
        for log in event.logs
        if log.email_task_id is not None
    }
    received_log_task_ids = {
        log.email_task_id
        for event in received_events
        for log in event.logs
        if log.email_task_id is not None
    }

    all_sent_tasks = [
        task
        for task in tasks
        if task.id in sent_log_task_ids
        or task.sent_at is not None
        or task.status in {EmailTaskStatus.SENT.value, EmailTaskStatus.REPLY_DETECTED.value}
    ]
    all_replied_tasks = [
        task
        for task in tasks
        if task.id in received_log_task_ids or task.is_replied or task.status == EmailTaskStatus.REPLY_DETECTED.value
    ]

    all_sent_events: list[EmailTrendEvent] = []
    seen_sent_log_task_ids: set[int] = set()
    active_sent_count = 0
    for event in sent_events_from_logs:
        log = event.log
        if log.professor_id is None or log.professor_id not in active_professor_ids:
            continue
        sent_at = _successful_sent_event_timestamp(event)
        if not _datetime_in_range(sent_at, start_at=start_at, end_at=end_at):
            continue
        all_sent_events.append((log.email_task_id, log.professor_id, sent_at))
        event_task_ids = {
            event_log.email_task_id
            for event_log in event.logs
            if event_log.email_task_id is not None
        }
        seen_sent_log_task_ids.update(event_task_ids)
        if (
            _professor_matches_school_filters(
                professor_by_id[log.professor_id],
                university=email_university,
                school=email_school,
            )
            and any(
                event_log.identity_id == identity_id
                and not (event_log.failure_summary or "").strip()
                for event_log in event.logs
            )
        ):
            active_sent_count += 1

    for task in all_sent_tasks:
        if task.id in seen_sent_log_task_ids or task.professor_id not in active_professor_ids:
            continue
        source_time = task.sent_at or task.updated_at
        if not _datetime_in_range(source_time, start_at=start_at, end_at=end_at):
            continue
        all_sent_events.append((task.id, task.professor_id, source_time))
        if _professor_matches_school_filters(
            professor_by_id[task.professor_id],
            university=email_university,
            school=email_school,
        ):
            active_sent_count += 1

    sent_events = [
        event
        for event in all_sent_events
        if _professor_matches_school_filters(
            professor_by_id[event[1]],
            university=email_university,
            school=email_school,
        )
    ]

    sent_professor_ids = {professor_id for _, professor_id, _ in sent_events}
    all_sent_professor_ids = {professor_id for _, professor_id, _ in all_sent_events}
    contacted_professor_ids = {professor_id for _, professor_id, _ in sent_events}
    all_contacted_professor_ids = set(all_sent_professor_ids)
    replied_professor_ids: set[int] = set()
    all_replied_professor_ids: set[int] = set()
    received_trend_events: list[tuple[int, datetime]] = []
    for event in received_events:
        log = event.log
        if log.professor_id is None or log.professor_id not in professor_by_id:
            continue
        if not _datetime_in_range(event.created_at, start_at=start_at, end_at=end_at):
            continue
        all_contacted_professor_ids.add(log.professor_id)
        all_replied_professor_ids.add(log.professor_id)
        if not _professor_matches_school_filters(
            professor_by_id[log.professor_id],
            university=email_university,
            school=email_school,
        ):
            continue
        contacted_professor_ids.add(log.professor_id)
        replied_professor_ids.add(log.professor_id)
        received_trend_events.append((log.professor_id, event.created_at))

    replied_fallback_tasks: list[EmailTask] = []
    for task in all_replied_tasks:
        if task.id in received_log_task_ids:
            continue
        if not _datetime_in_range(task.updated_at, start_at=start_at, end_at=end_at):
            continue
        if task.professor_id in all_contacted_professor_ids:
            all_replied_professor_ids.add(task.professor_id)
        if task.professor_id not in contacted_professor_ids:
            continue
        replied_professor_ids.add(task.professor_id)
        replied_fallback_tasks.append(task)

    sent_count = len(sent_events)
    sent_professor_count = len(sent_professor_ids)
    total_professor_count = await _count_active_professors_for_school_filter(
        session,
        university=email_university,
        school=email_school,
    )
    sent_professor_rate = (
        sent_professor_count / total_professor_count
        if total_professor_count
        else 0.0
    )
    contacted_professor_count = len(contacted_professor_ids)
    replied_count = len(replied_professor_ids)
    send_failed_count = task_status_counts.get(
        EmailTaskStatus.SEND_FAILED.value,
        0,
    )
    reply_rate = (replied_count / contacted_professor_count) if contacted_professor_count else 0.0
    send_failed_rate = (
        send_failed_count / max(active_sent_count + send_failed_count, 1)
        if task_total_count
        else 0.0
    )

    trend_30_days = _build_email_trend(
        sent_events,
        received_trend_events,
        replied_fallback_tasks=replied_fallback_tasks,
        start_at=start_at,
        end_at=end_at,
        local_timezone=local_timezone,
    )
    outreach_coverage = await _build_outreach_coverage_from_database(
        session,
        professor_by_id=professor_by_id,
        sent_professor_ids=all_sent_professor_ids,
        contacted_professor_ids=all_contacted_professor_ids,
        replied_professor_ids=all_replied_professor_ids,
    )
    reply_wait = _build_reply_wait(
        professors=list(professor_by_id.values()),
        tasks=tasks,
        communication_events=communication_events,
        university=email_university,
        school=email_school,
        start_at=start_at,
        end_at=end_at,
    )
    return DashboardEmailSectionRead(
        summary=DashboardEmailSummaryRead(
            sent_count=sent_count,
            sent_professor_count=sent_professor_count,
            total_professor_count=total_professor_count,
            sent_professor_rate=sent_professor_rate,
            contacted_professor_count=contacted_professor_count,
            replied_count=replied_count,
            reply_rate=reply_rate,
            send_failed_count=send_failed_count,
            send_failed_rate=send_failed_rate,
            review_required_count=review_required_count,
            scheduled_count=scheduled_count,
        ),
        trend_30_days=trend_30_days,
        outreach_coverage=outreach_coverage,
        reply_wait=reply_wait,
        funnel=funnel,
        status_distribution=status_distribution,
        follow_ups=follow_ups,
    )


def _build_match_score_distribution(
    *,
    professors: list[Professor],
    latest_match_score_by_professor: dict[int, int],
) -> list[DashboardMentorMatchBucketRead]:
    buckets = Counter({"unmatched": 0, "0_59": 0, "60_69": 0, "70_79": 0, "80_89": 0, "90_100": 0})
    for professor in professors:
        score = latest_match_score_by_professor.get(professor.id)
        if score is None:
            buckets["unmatched"] += 1
        elif score < 60:
            buckets["0_59"] += 1
        elif score < 70:
            buckets["60_69"] += 1
        elif score < 80:
            buckets["70_79"] += 1
        elif score < 90:
            buckets["80_89"] += 1
        else:
            buckets["90_100"] += 1

    return [
        DashboardMentorMatchBucketRead(bucket="unmatched", label="未分析", count=buckets["unmatched"]),
        DashboardMentorMatchBucketRead(bucket="0_59", label="0-59", count=buckets["0_59"]),
        DashboardMentorMatchBucketRead(bucket="60_69", label="60-69", count=buckets["60_69"]),
        DashboardMentorMatchBucketRead(bucket="70_79", label="70-79", count=buckets["70_79"]),
        DashboardMentorMatchBucketRead(bucket="80_89", label="80-89", count=buckets["80_89"]),
        DashboardMentorMatchBucketRead(bucket="90_100", label="90-100", count=buckets["90_100"]),
    ]


def _build_profile_completeness(professors: list[Professor]) -> list[DashboardProfileCompletenessRead]:
    total = len(professors)
    email_count = sum(1 for professor in professors if _has_text(professor.email))
    research_direction_count = sum(1 for professor in professors if _has_text(professor.research_direction))
    recent_papers_count = sum(
        1
        for professor in professors
        if isinstance(professor.recent_papers, list) and any(_has_text(item) for item in professor.recent_papers)
    )
    profile_url_count = sum(1 for professor in professors if _has_text(professor.profile_url))
    complete_count = sum(
        1
        for professor in professors
        if _has_text(professor.email)
        and _has_text(professor.research_direction)
        and (
            (isinstance(professor.recent_papers, list) and any(_has_text(item) for item in professor.recent_papers))
            or _has_text(professor.profile_url)
        )
    )

    return [
        _profile_completeness_item("email", "有邮箱", email_count, total),
        _profile_completeness_item("research_direction", "有研究方向", research_direction_count, total),
        _profile_completeness_item("recent_papers", "有近期论文", recent_papers_count, total),
        _profile_completeness_item("profile_url", "有主页链接", profile_url_count, total),
        _profile_completeness_item("complete", "完整资料", complete_count, total),
    ]


PROFILE_COMPLETENESS_BUCKET_LABELS = {
    "complete": "完整资料",
    "missing_email": "缺邮箱",
    "missing_research_direction": "缺研究方向",
    "missing_recent_papers": "缺近期论文",
    "missing_profile_url": "缺主页链接",
    "multiple_missing": "多项缺失",
}


def _build_profile_completeness_distribution(
    professors: list[Professor],
) -> list[DashboardProfileCompletenessBucketRead]:
    total = len(professors)
    counts: Counter[str] = Counter()
    for professor in professors:
        missing_fields = _build_missing_fields(professor)
        if not missing_fields:
            counts["complete"] += 1
        elif len(missing_fields) > 1:
            counts["multiple_missing"] += 1
        else:
            field = missing_fields[0]
            if field == "邮箱":
                counts["missing_email"] += 1
            elif field == "研究方向":
                counts["missing_research_direction"] += 1
            elif field == "近期论文":
                counts["missing_recent_papers"] += 1
            else:
                counts["missing_profile_url"] += 1

    return [
        DashboardProfileCompletenessBucketRead(
            key=key,
            label=label,
            count=counts[key],
            total=total,
            rate=(counts[key] / total) if total else 0.0,
        )
        for key, label in PROFILE_COMPLETENESS_BUCKET_LABELS.items()
    ]


def _profile_completeness_item(
    key: str,
    label: str,
    count: int,
    total: int,
) -> DashboardProfileCompletenessRead:
    rate = (count / total) if total else 0.0
    return DashboardProfileCompletenessRead(
        key=key,
        label=label,
        count=count,
        total=total,
        rate=rate,
    )


def _build_school_distribution(professors: list[Professor]) -> list[DashboardSchoolDistributionRead]:
    school_counter: Counter[str] = Counter()
    for professor in professors:
        school_name = _normalize_school_label(professor.university)
        school_counter[school_name] += 1

    top_items = sorted(school_counter.items(), key=lambda item: (-item[1], item[0]))
    return [
        DashboardSchoolDistributionRead(school_name=school_name, count=count)
        for school_name, count in top_items
    ]


def _build_school_filters(professors: list[Professor]) -> list[DashboardSchoolFilterRead]:
    by_university: dict[str, Counter[str]] = defaultdict(Counter)
    for professor in professors:
        university = _normalize_school_label(professor.university)
        school = _normalize_college_label(professor.school)
        by_university[university][school] += 1

    filters: list[DashboardSchoolFilterRead] = []
    for university, schools in by_university.items():
        school_items = [
            DashboardSchoolFilterSchoolRead(school_name=school_name, count=count)
            for school_name, count in sorted(schools.items(), key=lambda item: (-item[1], item[0]))
        ]
        filters.append(
            DashboardSchoolFilterRead(
                university=university,
                count=sum(schools.values()),
                schools=school_items,
            )
        )

    filters.sort(key=lambda item: (-item.count, item.university))
    return filters


def _serialize_professor_action_item(
    professor: Professor,
    *,
    task: EmailTask | None,
    match_result: MatchResultView | None,
    status: str,
    reason: str,
    threshold: int,
    include_missing_fields: bool = False,
) -> DashboardMentorActionItemRead:
    missing_fields = _build_missing_fields(professor) if include_missing_fields else []
    return DashboardMentorActionItemRead(
        professor_id=professor.id,
        name=professor.name,
        university=professor.university,
        school=professor.school,
        department=professor.department,
        match_score=(
            match_result.match_score if match_result is not None else None
        ),
        status=status,
        status_label=PROFESSOR_STATUS_LABELS.get(status, status),
        updated_at=(
            match_result.updated_at
            if match_result is not None
            else task.updated_at if task is not None else professor.updated_at
        ),
        reason=reason,
        missing_fields=missing_fields,
    )


def _build_missing_fields(professor: Professor) -> list[str]:
    missing_fields: list[str] = []
    if not _has_text(professor.email):
        missing_fields.append("邮箱")
    if not _has_text(professor.research_direction):
        missing_fields.append("研究方向")
    if not (isinstance(professor.recent_papers, list) and any(_has_text(item) for item in professor.recent_papers)):
        missing_fields.append("近期论文")
    if not _has_text(professor.profile_url):
        missing_fields.append("主页链接")
    return missing_fields


def _build_mentor_follow_up_reason(*, status: str) -> str:
    return {
        "not_contacted": "高分但尚未联系",
        "preparing": "草稿或匹配处理中",
        "ready_to_send": "已准备发送",
        "contacted": "已发送未回复",
        "replied": "已回复",
        "failed": "发送失败",
    }.get(status, "待处理")


async def _build_outreach_coverage_from_database(
    session: AsyncSession,
    *,
    professor_by_id: dict[int, _ProfessorSchoolRef],
    sent_professor_ids: set[int],
    contacted_professor_ids: set[int],
    replied_professor_ids: set[int],
) -> DashboardOutreachCoverageRead:
    university_label = _sql_professor_label(Professor.university, "学校未填写")
    school_label = _sql_professor_label(Professor.school, "学院未填写")
    rows = (
        await session.execute(
            select(
                university_label.label("university"),
                school_label.label("school"),
                func.count(Professor.id).label("count"),
            )
            .where(Professor.archived_at.is_(None))
            .group_by(university_label, school_label),
        )
    ).mappings().all()

    university_totals: Counter[str] = Counter()
    school_totals: Counter[tuple[str, str]] = Counter()
    for row in rows:
        university = str(row["university"])
        school = str(row["school"])
        count = int(row["count"])
        university_totals[university] += count
        school_totals[(university, school)] += count

    university_sent: Counter[str] = Counter()
    university_contacted: Counter[str] = Counter()
    university_replied: Counter[str] = Counter()
    school_sent: Counter[tuple[str, str]] = Counter()
    school_contacted: Counter[tuple[str, str]] = Counter()
    school_replied: Counter[tuple[str, str]] = Counter()

    def increment_for_professors(
        professor_ids: set[int],
        university_counts: Counter[str],
        school_counts: Counter[tuple[str, str]],
    ) -> None:
        for professor_id in professor_ids:
            professor = professor_by_id.get(professor_id)
            if professor is None:
                continue
            university = _normalize_school_label(professor.university)
            school_key = (
                university,
                _normalize_college_label(professor.school),
            )
            university_counts[university] += 1
            school_counts[school_key] += 1

    increment_for_professors(
        sent_professor_ids,
        university_sent,
        school_sent,
    )
    increment_for_professors(
        contacted_professor_ids,
        university_contacted,
        school_contacted,
    )
    increment_for_professors(
        replied_professor_ids,
        university_replied,
        school_replied,
    )

    universities = [
        _build_outreach_coverage_item(
            university=university,
            school=None,
            label=university,
            sent_count=university_sent[university],
            total_count=total_count,
            contacted_count=university_contacted[university],
            replied_count=university_replied[university],
        )
        for university, total_count in university_totals.items()
    ]
    schools = [
        _build_outreach_coverage_item(
            university=university,
            school=school,
            label=school,
            sent_count=school_sent[(university, school)],
            total_count=total_count,
            contacted_count=school_contacted[(university, school)],
            replied_count=school_replied[(university, school)],
        )
        for (university, school), total_count in school_totals.items()
    ]
    universities.sort(key=_outreach_coverage_sort_key)
    schools.sort(key=_outreach_coverage_sort_key)
    return DashboardOutreachCoverageRead(universities=universities, schools=schools)


def _build_outreach_coverage(
    *,
    professors: list[Professor],
    sent_professor_ids: set[int],
    contacted_professor_ids: set[int],
    replied_professor_ids: set[int],
) -> DashboardOutreachCoverageRead:
    university_totals: Counter[str] = Counter()
    university_sent: Counter[str] = Counter()
    university_contacted: Counter[str] = Counter()
    university_replied: Counter[str] = Counter()
    school_totals: Counter[tuple[str, str]] = Counter()
    school_sent: Counter[tuple[str, str]] = Counter()
    school_contacted: Counter[tuple[str, str]] = Counter()
    school_replied: Counter[tuple[str, str]] = Counter()

    for professor in professors:
        university = _normalize_school_label(professor.university)
        school = _normalize_college_label(professor.school)
        school_key = (university, school)
        university_totals[university] += 1
        school_totals[school_key] += 1
        if professor.id in sent_professor_ids:
            university_sent[university] += 1
            school_sent[school_key] += 1
        if professor.id in contacted_professor_ids:
            university_contacted[university] += 1
            school_contacted[school_key] += 1
        if professor.id in replied_professor_ids:
            university_replied[university] += 1
            school_replied[school_key] += 1

    universities = [
        _build_outreach_coverage_item(
            university=university,
            school=None,
            label=university,
            sent_count=university_sent[university],
            total_count=total_count,
            contacted_count=university_contacted[university],
            replied_count=university_replied[university],
        )
        for university, total_count in university_totals.items()
    ]
    schools = [
        _build_outreach_coverage_item(
            university=university,
            school=school,
            label=school,
            sent_count=school_sent[(university, school)],
            total_count=total_count,
            contacted_count=school_contacted[(university, school)],
            replied_count=school_replied[(university, school)],
        )
        for (university, school), total_count in school_totals.items()
    ]
    universities.sort(key=_outreach_coverage_sort_key)
    schools.sort(key=_outreach_coverage_sort_key)
    return DashboardOutreachCoverageRead(universities=universities, schools=schools)


def _build_outreach_coverage_item(
    *,
    university: str,
    school: str | None,
    label: str,
    sent_count: int,
    total_count: int,
    contacted_count: int,
    replied_count: int,
) -> DashboardOutreachCoverageItemRead:
    return DashboardOutreachCoverageItemRead(
        university=university,
        school=school,
        label=label,
        sent_professor_count=sent_count,
        total_professor_count=total_count,
        unsent_professor_count=total_count - sent_count,
        sent_professor_rate=(sent_count / total_count) if total_count else 0.0,
        contacted_professor_count=contacted_count,
        replied_professor_count=replied_count,
        reply_rate=(replied_count / contacted_count) if contacted_count else 0.0,
    )


def _outreach_coverage_sort_key(item: DashboardOutreachCoverageItemRead) -> tuple[float, int, int, str, str]:
    return (
        item.sent_professor_rate,
        -item.unsent_professor_count,
        -item.total_professor_count,
        item.university,
        item.school or "",
    )


REPLY_WAIT_BUCKETS: tuple[tuple[str, str, float | None], ...] = (
    ("within_24h", "24 小时内", 24.0),
    ("1_3_days", "1–3 天", 72.0),
    ("3_7_days", "3–7 天", 168.0),
    ("7_14_days", "7–14 天", 336.0),
    ("over_14_days", "14 天以上", None),
)


def _build_reply_wait(
    *,
    professors: list[Professor],
    tasks: list[EmailTask],
    communication_events: list[CommunicationEvent],
    university: str | None,
    school: str | None,
    start_at: datetime | None,
    end_at: datetime | None,
) -> DashboardReplyWaitRead:
    scoped_professor_ids = {
        professor.id
        for professor in professors
        if _professor_matches_school_filters(
            professor,
            university=university,
            school=school,
        )
    }
    first_sent_at_by_professor: dict[int, datetime] = {}
    received_at_by_professor: dict[int, list[datetime]] = defaultdict(list)

    for event in communication_events:
        professor_id = event.log.professor_id
        if professor_id is None or professor_id not in scoped_professor_ids:
            continue
        if event.log.direction == EmailDirection.SENT.value and event.successful:
            _keep_earliest_timestamp(
                first_sent_at_by_professor,
                professor_id,
                _successful_sent_event_timestamp(event),
            )
        elif event.log.direction == EmailDirection.RECEIVED.value:
            received_at_by_professor[professor_id].append(_as_utc_datetime(event.created_at))

    # Legacy task rows can contain an exact send timestamp even when their send log is unavailable.
    for task in tasks:
        if task.professor_id not in scoped_professor_ids or task.sent_at is None:
            continue
        _keep_earliest_timestamp(
            first_sent_at_by_professor,
            task.professor_id,
            task.sent_at,
        )

    wait_hours: list[float] = []
    for professor_id, first_sent_at in first_sent_at_by_professor.items():
        first_sent_utc = _as_utc_datetime(first_sent_at)
        eligible_replies = [
            reply_at
            for reply_at in received_at_by_professor.get(professor_id, [])
            if reply_at >= first_sent_utc
        ]
        if not eligible_replies:
            continue
        first_reply_at = min(eligible_replies)
        if not _datetime_in_range(first_reply_at, start_at=start_at, end_at=end_at):
            continue
        wait_hours.append((first_reply_at - first_sent_utc).total_seconds() / 3600)

    wait_hours.sort()
    sample_count = len(wait_hours)
    counts: Counter[str] = Counter(_reply_wait_bucket_key(value) for value in wait_hours)
    distribution = [
        DashboardReplyWaitBucketRead(
            key=key,
            label=label,
            count=counts[key],
            rate=(counts[key] / sample_count) if sample_count else 0.0,
        )
        for key, label, _ in REPLY_WAIT_BUCKETS
    ]
    return DashboardReplyWaitRead(
        sample_count=sample_count,
        median_hours=_percentile(wait_hours, 0.5),
        p75_hours=_percentile(wait_hours, 0.75),
        distribution=distribution,
    )


def _successful_sent_event_timestamp(event: CommunicationEvent) -> datetime:
    successful_sent_timestamps = [
        log.created_at
        for log in event.logs
        if log.direction == EmailDirection.SENT.value
        and not (log.failure_summary or "").strip()
    ]
    if not successful_sent_timestamps:
        return event.created_at
    return min(successful_sent_timestamps)


def _keep_earliest_timestamp(
    values: dict[int, datetime],
    professor_id: int,
    timestamp: datetime,
) -> None:
    normalized = _as_utc_datetime(timestamp)
    current = values.get(professor_id)
    if current is None or normalized < _as_utc_datetime(current):
        values[professor_id] = normalized


def _reply_wait_bucket_key(wait_hours: float) -> str:
    for key, _, upper_bound in REPLY_WAIT_BUCKETS:
        if upper_bound is None or wait_hours < upper_bound:
            return key
    return "over_14_days"


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    position = (len(values) - 1) * percentile
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(values) - 1)
    fraction = position - lower_index
    return values[lower_index] + (values[upper_index] - values[lower_index]) * fraction


def _build_email_trend(
    sent_events: list[EmailTrendEvent],
    received_events: list[tuple[int, datetime]],
    *,
    replied_fallback_tasks: list[EmailTask],
    start_at: datetime | None,
    end_at: datetime | None,
    local_timezone: tzinfo,
) -> list[DashboardEmailTrendBucketRead]:
    if start_at is not None and end_at is not None:
        start_day = _floor_day(start_at, local_timezone=local_timezone)
        current_day = _floor_day(end_at, local_timezone=local_timezone)
    else:
        current_day = _floor_day(utc_now(), local_timezone=local_timezone)
        start_day = current_day - timedelta(days=29)

    buckets: dict[str, DashboardEmailTrendBucketRead] = {}
    current = start_day
    while current <= current_day:
        key = current.date().isoformat()
        buckets[key] = DashboardEmailTrendBucketRead(date=key, label=current.strftime("%m/%d"))
        current += timedelta(days=1)

    for _, _, event_time in sent_events:
        key = _floor_day(event_time, local_timezone=local_timezone).date().isoformat()
        if key in buckets:
            buckets[key].sent_count += 1

    replied_professors_by_bucket: dict[str, set[int]] = defaultdict(set)
    for professor_id, event_time in received_events:
        key = _floor_day(event_time, local_timezone=local_timezone).date().isoformat()
        if key in buckets:
            replied_professors_by_bucket[key].add(professor_id)

    for task in replied_fallback_tasks:
        key = _floor_day(task.updated_at, local_timezone=local_timezone).date().isoformat()
        if key in buckets:
            replied_professors_by_bucket[key].add(task.professor_id)

    for key, professor_ids in replied_professors_by_bucket.items():
        buckets[key].replied_count = len(professor_ids)

    return [buckets[key] for key in sorted(buckets.keys())]


def _build_email_funnel(tasks: list[EmailTask]) -> list[DashboardEmailFunnelBucketRead]:
    counts = {
        "matched": 0,
        "generating_draft": 0,
        "review_required": 0,
        "approved": 0,
        "scheduled": 0,
        "sent": 0,
        "replied": 0,
    }
    for task in tasks:
        if task.status in {
            EmailTaskStatus.MATCHED.value,
            EmailTaskStatus.GENERATING_DRAFT.value,
            EmailTaskStatus.DRAFT_FAILED.value,
            EmailTaskStatus.REVIEW_REQUIRED.value,
            EmailTaskStatus.APPROVED.value,
            EmailTaskStatus.SCHEDULED.value,
            EmailTaskStatus.SENDING.value,
            EmailTaskStatus.SENT.value,
            EmailTaskStatus.SEND_FAILED.value,
            EmailTaskStatus.REPLY_DETECTED.value,
        }:
            counts["matched"] += 1
        if task.status in {
            EmailTaskStatus.GENERATING_DRAFT.value,
            EmailTaskStatus.DRAFT_FAILED.value,
            EmailTaskStatus.REVIEW_REQUIRED.value,
            EmailTaskStatus.APPROVED.value,
            EmailTaskStatus.SCHEDULED.value,
            EmailTaskStatus.SENDING.value,
            EmailTaskStatus.SENT.value,
            EmailTaskStatus.SEND_FAILED.value,
            EmailTaskStatus.REPLY_DETECTED.value,
        }:
            counts["generating_draft"] += 1
        if task.status in {
            EmailTaskStatus.REVIEW_REQUIRED.value,
            EmailTaskStatus.APPROVED.value,
            EmailTaskStatus.SCHEDULED.value,
            EmailTaskStatus.SENDING.value,
            EmailTaskStatus.SENT.value,
            EmailTaskStatus.SEND_FAILED.value,
            EmailTaskStatus.REPLY_DETECTED.value,
        }:
            counts["review_required"] += 1
        if task.status in {
            EmailTaskStatus.APPROVED.value,
            EmailTaskStatus.SCHEDULED.value,
            EmailTaskStatus.SENDING.value,
            EmailTaskStatus.SENT.value,
            EmailTaskStatus.SEND_FAILED.value,
            EmailTaskStatus.REPLY_DETECTED.value,
        }:
            counts["approved"] += 1
        if task.status in {
            EmailTaskStatus.SCHEDULED.value,
            EmailTaskStatus.SENDING.value,
            EmailTaskStatus.SENT.value,
            EmailTaskStatus.REPLY_DETECTED.value,
        }:
            counts["scheduled"] += 1
        if task.status in {EmailTaskStatus.SENT.value, EmailTaskStatus.REPLY_DETECTED.value}:
            counts["sent"] += 1
        if task.status == EmailTaskStatus.REPLY_DETECTED.value or task.is_replied:
            counts["replied"] += 1

    return [
        DashboardEmailFunnelBucketRead(key="matched", label="已匹配", count=counts["matched"]),
        DashboardEmailFunnelBucketRead(key="generating_draft", label="草稿生成中", count=counts["generating_draft"]),
        DashboardEmailFunnelBucketRead(key="review_required", label="待审核", count=counts["review_required"]),
        DashboardEmailFunnelBucketRead(key="approved", label="已批准", count=counts["approved"]),
        DashboardEmailFunnelBucketRead(key="scheduled", label="已排程", count=counts["scheduled"]),
        DashboardEmailFunnelBucketRead(key="sent", label="已发送", count=counts["sent"]),
        DashboardEmailFunnelBucketRead(key="replied", label="已回复", count=counts["replied"]),
    ]


def _build_email_status_distribution(tasks: list[EmailTask]) -> list[DashboardEmailStatusBucketRead]:
    counter = Counter(task.status for task in tasks)
    ordered_statuses = [
        EmailTaskStatus.MATCHED.value,
        EmailTaskStatus.GENERATING_DRAFT.value,
        EmailTaskStatus.DRAFT_FAILED.value,
        EmailTaskStatus.REVIEW_REQUIRED.value,
        EmailTaskStatus.APPROVED.value,
        EmailTaskStatus.SCHEDULED.value,
        EmailTaskStatus.SENDING.value,
        EmailTaskStatus.SENT.value,
        EmailTaskStatus.SEND_FAILED.value,
        EmailTaskStatus.REPLY_DETECTED.value,
        EmailTaskStatus.CANCELED.value,
    ]
    return [
        DashboardEmailStatusBucketRead(
            status=status,
            label=EMAIL_TASK_STATUS_LABELS.get(status, status),
            count=counter.get(status, 0),
        )
        for status in ordered_statuses
    ]


def _build_email_follow_ups(
    *,
    latest_task_by_professor: dict[int, EmailTask],
    match_results_by_professor: dict[int, MatchResultView],
    professor_status_by_id: dict[int, str],
    threshold: int,
) -> list[DashboardEmailFollowUpRead]:
    items: list[DashboardEmailFollowUpRead] = []
    for professor_id, task in latest_task_by_professor.items():
        professor = task.professor
        if professor is None:
            continue
        match_result = match_results_by_professor.get(professor_id)
        score = match_result.match_score if match_result is not None else None
        if score is None or score < threshold:
            continue
        status = professor_status_by_id.get(professor_id, "not_contacted")
        if status == "replied":
            continue

        reason = _build_email_follow_up_reason(status=status)
        items.append(
            DashboardEmailFollowUpRead(
                task_id=task.id,
                professor_id=professor.id,
                name=professor.name,
                university=professor.university,
                school=professor.school,
                department=professor.department,
                match_score=score,
                status=status,
                status_label=PROFESSOR_STATUS_LABELS.get(status, status),
                reason=reason,
                updated_at=(
                    match_result.updated_at
                    if match_result is not None
                    else task.updated_at
                ),
            ),
        )

    items.sort(
        key=lambda item: (
            _email_follow_up_priority(item.status),
            -(item.match_score or 0),
            -item.updated_at.timestamp(),
            item.name,
        ),
    )
    return items[:8]


def _email_follow_up_priority(status: str) -> int:
    return {
        "failed": 0,
        "contacted": 1,
        "ready_to_send": 2,
        "preparing": 3,
        "not_contacted": 4,
    }.get(status, 5)


def _build_email_follow_up_reason(*, status: str) -> str:
    return {
        "failed": "发送失败",
        "contacted": "已发送未回复",
        "ready_to_send": "已准备发送",
        "preparing": "草稿处理中",
        "not_contacted": "尚未联系",
    }.get(status, "待跟进")


def _has_text(value: str | None) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _parse_date_filter(
    value: str | None,
    *,
    field_name: str,
    local_timezone: tzinfo,
) -> datetime | None:
    normalized = value.strip() if value else None
    if not normalized:
        return None
    try:
        parsed = date.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field_name} 日期格式应为 YYYY-MM-DD") from exc
    return datetime.combine(parsed, time.min, tzinfo=local_timezone).astimezone(UTC)


def _end_of_day(value: datetime | None, *, local_timezone: tzinfo) -> datetime | None:
    if value is None:
        return None
    return (
        value.astimezone(local_timezone)
        .replace(hour=23, minute=59, second=59, microsecond=999999)
        .astimezone(UTC)
    )


def _as_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = as_utc_aware(value)
    return value.astimezone(UTC)


def _datetime_in_range(value: datetime, *, start_at: datetime | None, end_at: datetime | None) -> bool:
    value = _as_utc_datetime(value)
    if start_at is not None and value < start_at:
        return False
    if end_at is not None and value > end_at:
        return False
    return True


def _floor_day(value: datetime, *, local_timezone: tzinfo) -> datetime:
    if value.tzinfo is None:
        value = as_utc_aware(value)
    return value.astimezone(local_timezone).replace(hour=0, minute=0, second=0, microsecond=0)


def _local_timezone() -> tzinfo:
    return local_now().tzinfo or UTC
