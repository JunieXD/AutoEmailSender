from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

from app.core.time import as_utc_aware, utc_now

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import lazyload, load_only, selectinload

from app.models import EmailDirection, EmailLog, EmailTask, EmailTaskStatus, IdentityProfile, Professor
from app.schemas.dashboard import (
    DashboardEmailFunnelBucketRead,
    DashboardEmailFollowUpRead,
    DashboardEmailSectionRead,
    DashboardEmailStatusBucketRead,
    DashboardEmailSummaryRead,
    DashboardEmailTrendBucketRead,
    DashboardMentorActionItemRead,
    DashboardMentorFilterRead,
    DashboardMentorMatchBucketRead,
    DashboardMatchContextRead,
    DashboardProfileCompletenessBucketRead,
    DashboardProfileCompletenessRead,
    DashboardMentorSectionRead,
    DashboardMentorSummaryRead,
    DashboardOverviewRead,
    DashboardOutreachCoverageItemRead,
    DashboardOutreachCoverageRead,
    DashboardReplyWaitBucketRead,
    DashboardReplyWaitRead,
    DashboardSchoolDistributionRead,
    DashboardSchoolFilterRead,
    DashboardSchoolFilterSchoolRead,
)
from app.services.contact_status import build_contact_status_by_professor
from app.modules.communications.public import CommunicationEvent, load_communication_events
from app.modules.identities.public import resolve_identity_communication_scope
from app.services.match_results import (
    MatchResultView,
    ResolvedMatchResults,
    load_resolved_match_results,
    match_result_is_stale,
)


HIGH_SCORE_DEFAULT = 80
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

    professors = list(
        await session.scalars(
            select(Professor)
            .options(lazyload(Professor.tags))
            .where(Professor.archived_at.is_(None))
            .order_by(Professor.updated_at.desc(), Professor.created_at.desc()),
        ),
    )

    tasks = list(
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
                selectinload(EmailTask.professor)
                .load_only(
                    Professor.id,
                    Professor.name,
                    Professor.university,
                    Professor.school,
                    Professor.department,
                )
                .lazyload(Professor.tags),
            )
            .where(
                EmailTask.identity_id == identity_id,
                EmailTask.parent_task_id.is_(None),
                EmailTask.batch_send_canceled_at.is_(None),
            )
            .order_by(EmailTask.professor_id.asc(), EmailTask.created_at.desc(), EmailTask.id.desc()),
        ),
    )

    tasks_by_professor: dict[int, list[EmailTask]] = defaultdict(list)
    for task in tasks:
        tasks_by_professor[task.professor_id].append(task)

    latest_task_by_professor = {
        professor_id: professor_tasks[0]
        for professor_id, professor_tasks in tasks_by_professor.items()
        if professor_tasks
    }
    professor_ids = [professor.id for professor in professors]
    resolved_matches = await load_resolved_match_results(
        session,
        active_identity_id=identity_id,
        professor_ids=professor_ids,
    )
    latest_match_score_by_professor = {
        professor_id: result.match_score
        for professor_id, result in resolved_matches.by_professor_id.items()
    }
    contact_status_by_professor = await build_contact_status_by_professor(
        session,
        identity_id=identity_id,
        professor_ids=professor_ids,
        communication_identity_ids=communication_scope.identity_ids,
    )
    professor_status_by_id = {
        professor.id: contact_status_by_professor[professor.id].status
        for professor in professors
    }
    filtered_professors = _filter_professors_for_mentor_analysis(
        professors,
        university=university,
        school=school,
    )

    mentor_section = _build_mentor_section(
        professors=professors,
        filtered_professors=filtered_professors,
        latest_task_by_professor=latest_task_by_professor,
        latest_match_score_by_professor=latest_match_score_by_professor,
        resolved_matches=resolved_matches,
        professor_status_by_id=professor_status_by_id,
        threshold=identity.match_threshold or HIGH_SCORE_DEFAULT,
        active_university=_normalize_filter_value(university),
        active_school=_normalize_filter_value(school),
    )

    email_section = await _build_email_section(
        session,
        tasks=tasks,
        identity_id=identity_id,
        communication_identity_ids=communication_scope.identity_ids,
        professors=professors,
        professor_status_by_id=professor_status_by_id,
        latest_task_by_professor=latest_task_by_professor,
        match_results_by_professor=resolved_matches.by_professor_id,
        threshold=identity.match_threshold or HIGH_SCORE_DEFAULT,
        email_university=email_university,
        email_school=email_school,
        start_date=start_date,
        end_date=end_date,
    )

    return DashboardOverviewRead(mentor=mentor_section, email=email_section)


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
    professor: Professor | None,
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
    identity_id: int,
    communication_identity_ids: tuple[int, ...],
    professors: list[Professor],
    professor_status_by_id: dict[int, str],
    latest_task_by_professor: dict[int, EmailTask],
    match_results_by_professor: dict[int, MatchResultView],
    threshold: int,
    email_university: str | None,
    email_school: str | None,
    start_date: str | None,
    end_date: str | None,
) -> DashboardEmailSectionRead:
    start_at = _parse_date_filter(start_date, field_name="start_date")
    end_at = _end_of_day(_parse_date_filter(end_date, field_name="end_date"))
    if start_at is not None and end_at is not None and start_at > end_at:
        raise ValueError("start_date 不能晚于 end_date")

    professor_by_id = {professor.id: professor for professor in professors}
    professor_ids = list(professor_by_id)
    active_professor_ids = set(professor_ids)
    communication_events = await load_communication_events(
        session,
        identity_ids=communication_identity_ids,
        professor_ids=professor_ids,
        include_message_content=False,
        include_source_identities=False,
        include_professors=False,
    )
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

    failed_tasks = [task for task in tasks if task.status == EmailTaskStatus.SEND_FAILED.value]
    review_required_count = sum(1 for task in tasks if task.status == EmailTaskStatus.REVIEW_REQUIRED.value)
    scheduled_count = sum(1 for task in tasks if task.status == EmailTaskStatus.SCHEDULED.value)
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

    scoped_professor_ids = {
        professor.id
        for professor in professors
        if _professor_matches_school_filters(
            professor,
            university=email_university,
            school=email_school,
        )
    }
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
    total_professor_count = len(scoped_professor_ids)
    sent_professor_rate = (
        sent_professor_count / total_professor_count
        if total_professor_count
        else 0.0
    )
    contacted_professor_count = len(contacted_professor_ids)
    replied_count = len(replied_professor_ids)
    send_failed_count = len(failed_tasks)
    reply_rate = (replied_count / contacted_professor_count) if contacted_professor_count else 0.0
    send_failed_rate = (
        send_failed_count / max(active_sent_count + send_failed_count, 1)
        if tasks
        else 0.0
    )

    trend_30_days = _build_email_trend(
        sent_events,
        received_trend_events,
        replied_fallback_tasks=replied_fallback_tasks,
        start_at=start_at,
        end_at=end_at,
    )
    outreach_coverage = _build_outreach_coverage(
        professors=professors,
        sent_professor_ids=all_sent_professor_ids,
        contacted_professor_ids=all_contacted_professor_ids,
        replied_professor_ids=all_replied_professor_ids,
    )
    reply_wait = _build_reply_wait(
        professors=professors,
        tasks=tasks,
        communication_events=communication_events,
        university=email_university,
        school=email_school,
        start_at=start_at,
        end_at=end_at,
    )
    funnel = _build_email_funnel(tasks)
    status_distribution = _build_email_status_distribution(tasks)
    follow_ups = _build_email_follow_ups(
        latest_task_by_professor=latest_task_by_professor,
        match_results_by_professor=match_results_by_professor,
        professor_status_by_id=professor_status_by_id,
        threshold=threshold,
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
) -> list[DashboardEmailTrendBucketRead]:
    if start_at is not None and end_at is not None:
        start_day = _floor_day(start_at)
        current_day = _floor_day(end_at)
    else:
        current_day = _floor_day(utc_now())
        start_day = current_day - timedelta(days=29)

    buckets: dict[str, DashboardEmailTrendBucketRead] = {}
    current = start_day
    while current <= current_day:
        key = current.date().isoformat()
        buckets[key] = DashboardEmailTrendBucketRead(date=key, label=current.strftime("%m/%d"))
        current += timedelta(days=1)

    for _, _, event_time in sent_events:
        key = _floor_day(event_time).date().isoformat()
        if key in buckets:
            buckets[key].sent_count += 1

    replied_professors_by_bucket: dict[str, set[int]] = defaultdict(set)
    for professor_id, event_time in received_events:
        key = _floor_day(event_time).date().isoformat()
        if key in buckets:
            replied_professors_by_bucket[key].add(professor_id)

    for task in replied_fallback_tasks:
        key = _floor_day(task.updated_at).date().isoformat()
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


def _parse_date_filter(value: str | None, *, field_name: str) -> datetime | None:
    normalized = value.strip() if value else None
    if not normalized:
        return None
    try:
        parsed = datetime.strptime(normalized, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"{field_name} 日期格式应为 YYYY-MM-DD") from exc
    return as_utc_aware(parsed)


def _end_of_day(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(hour=23, minute=59, second=59, microsecond=999999)


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


def _floor_day(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = as_utc_aware(value)
    return value.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
