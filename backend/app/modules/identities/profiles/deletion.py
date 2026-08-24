from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.models import (
    AgentChangePlan,
    BatchTask,
    BatchTaskStatus,
    EmailDeliveryAttempt,
    EmailLog,
    EmailObservation,
    EmailTask,
    EmailTaskCancellationReason,
    EmailTaskStatus,
    IdentityCommunicationGroup,
    IdentityMaterial,
    IdentityProfessorMatchResult,
    IdentityProfile,
    ImapIdentitySyncLease,
    ImapMailboxSyncState,
    ImapProfessorSyncState,
    MatchAnalysisJob,
    MatchAnalysisJobStatus,
    MatchAnalysisRun,
    TestComposeMessage,
    TestComposeSession,
)
from app.modules.identities.communication_groups.public import (
    cleanup_communication_group_after_identity_delete,
)
from app.modules.matching.public import request_match_analysis_job_cancel_record
from app.services.operation_logs import record_operation_log

from .schemas import (
    IdentityDeletionAutomaticActions,
    IdentityDeletionBlocker,
    IdentityDeletionImpact,
    IdentityReferenceCounts,
)
from .usage import (
    begin_identity_profile_retirement,
    end_identity_profile_retirement,
    get_identity_profile_usage_counts,
    identity_profile_retirement_in_progress,
)


MAX_BLOCKER_IDS = 20
INTERACTIVE_USAGE_LABELS = {
    "imap_test": "正在进行的 IMAP 连接测试",
    "smtp_test": "正在进行的 SMTP 连接测试",
    "test_compose_draft": "正在生成的测试写信草稿",
    "test_compose_send": "正在发送的测试邮件",
}
EMAIL_TASK_CANCEL_STATUSES = {
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
ACTIVE_BATCH_TASK_STATUSES = {
    BatchTaskStatus.RUNNING.value,
    BatchTaskStatus.PAUSED.value,
}
ACTIVE_MATCH_JOB_STATUSES = {
    MatchAnalysisJobStatus.QUEUED.value,
    MatchAnalysisJobStatus.RUNNING.value,
}


_REFERENCE_MODELS = {
    "email_tasks": (EmailTask, EmailTask.identity_id),
    "email_logs": (EmailLog, EmailLog.identity_id),
    "batch_tasks": (BatchTask, BatchTask.identity_id),
    "test_compose_sessions": (TestComposeSession, TestComposeSession.identity_id),
    "test_compose_messages": (TestComposeMessage, TestComposeMessage.identity_id),
    "match_analysis_runs": (MatchAnalysisRun, MatchAnalysisRun.identity_id),
    "match_results": (
        IdentityProfessorMatchResult,
        IdentityProfessorMatchResult.identity_id,
    ),
    "delivery_attempts": (EmailDeliveryAttempt, EmailDeliveryAttempt.identity_id),
    "email_observations": (EmailObservation, EmailObservation.identity_id),
}


@dataclass(frozen=True, slots=True)
class IdentityRetirementResult:
    stopped_batch_task_ids: tuple[int, ...]
    canceled_email_task_ids: tuple[int, ...]
    canceled_match_analysis_job_ids: tuple[int, ...]
    invalidated_agent_change_plan_ids: tuple[str, ...]


class IdentityDeletionError(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        impact: IdentityDeletionImpact,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.impact = impact


async def build_identity_deletion_impact(
    session: AsyncSession,
    identity: IdentityProfile,
    *,
    include_retirement_in_progress: bool = True,
) -> IdentityDeletionImpact:
    references = await _reference_counts(session, identity.id)
    blockers = await _deletion_blockers(
        session,
        identity.id,
        include_retirement_in_progress=include_retirement_in_progress,
    )
    automatic_actions = await _automatic_actions(session, identity.id)
    preserved_material_count = int(
        await session.scalar(
            select(func.count())
            .select_from(IdentityMaterial)
            .where(IdentityMaterial.identity_id == identity.id)
        )
        or 0
    )
    warnings = [
        "身份会从可选列表移除，SMTP/IMAP 密码会从本地数据库清除。",
        "邮件、通信、投递、匹配、测试写信和任务历史都会保留。",
        "独立发信模板与该身份上传的材料会继续保留在材料库中。",
    ]
    if automatic_actions.cancel_email_task_ids:
        warnings.append(
            f"会取消 {len(automatic_actions.cancel_email_task_ids)} 个尚未开始发送的邮件任务。"
        )
    if automatic_actions.stop_batch_task_ids:
        warnings.append(
            f"会停止 {len(automatic_actions.stop_batch_task_ids)} 个仍可继续的批量任务。"
        )
    if automatic_actions.cancel_match_analysis_job_ids:
        warnings.append(
            f"会取消 {len(automatic_actions.cancel_match_analysis_job_ids)} 个排队或运行中的匹配任务。"
        )
    if automatic_actions.invalidate_agent_change_plan_ids:
        warnings.append(
            f"会作废 {len(automatic_actions.invalidate_agent_change_plan_ids)} 个尚未确认的 Agent 操作计划。"
        )
    if identity.communication_group_id is not None:
        warnings.append("该身份会退出通信共享组；不足两个成员的共享组会自动解散。")

    revision_payload = {
        "identity_id": identity.id,
        "updated_at": _serialize_datetime(identity.updated_at),
        "references": references.model_dump(mode="json"),
        "blockers": [item.model_dump(mode="json") for item in blockers],
        "automatic_actions": automatic_actions.model_dump(mode="json"),
        "preserved_material_count": preserved_material_count,
        "communication_group_id": identity.communication_group_id,
    }
    revision = sha256(
        json.dumps(
            revision_payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return IdentityDeletionImpact(
        identity_id=identity.id,
        identity_name=identity.profile_name or identity.name,
        email_address=identity.email_address,
        is_default=identity.is_default,
        can_delete=not blockers,
        revision=revision,
        references=references,
        blockers=blockers,
        automatic_actions=automatic_actions,
        preserved_material_count=preserved_material_count,
        communication_group_id=identity.communication_group_id,
        warnings=warnings,
    )


async def retire_identity_profile(
    session: AsyncSession,
    identity: IdentityProfile,
    *,
    expected_revision: str,
) -> IdentityRetirementResult:
    impact = await build_identity_deletion_impact(session, identity)
    if impact.revision != expected_revision:
        raise IdentityDeletionError(
            code="IDENTITY_DELETE_PLAN_STALE",
            message="身份配置或关联状态已发生变化，请重新确认退役影响。",
            impact=impact,
        )
    if impact.blockers:
        raise IdentityDeletionError(
            code="IDENTITY_DELETE_BLOCKED",
            message=_blocker_message(identity.profile_name or identity.name, impact.blockers),
            impact=impact,
        )
    if not begin_identity_profile_retirement(identity.id):
        refreshed = await build_identity_deletion_impact(session, identity)
        raise IdentityDeletionError(
            code="IDENTITY_DELETE_BLOCKED",
            message=_blocker_message(identity.profile_name or identity.name, refreshed.blockers),
            impact=refreshed,
        )

    try:
        locked_impact = await build_identity_deletion_impact(
            session,
            identity,
            include_retirement_in_progress=False,
        )
        if locked_impact.revision != impact.revision:
            if locked_impact.blockers:
                raise IdentityDeletionError(
                    code="IDENTITY_DELETE_BLOCKED",
                    message=_blocker_message(
                        identity.profile_name or identity.name,
                        locked_impact.blockers,
                    ),
                    impact=locked_impact,
                )
            raise IdentityDeletionError(
                code="IDENTITY_DELETE_PLAN_STALE",
                message="身份配置或关联状态已发生变化，请重新确认退役影响。",
                impact=locked_impact,
            )

        now = utc_now()
        actions = locked_impact.automatic_actions
        await _cancel_email_tasks(session, actions.cancel_email_task_ids, now)
        await _stop_batch_tasks(session, actions.stop_batch_task_ids, now)
        for job_id in actions.cancel_match_analysis_job_ids:
            await request_match_analysis_job_cancel_record(
                session,
                job_id,
                event_name="match_analysis_job.identity_retired_cancel_requested",
                actor="desktop_ui",
            )
        invalidated_plan_ids = await _invalidate_pending_change_plans(
            session,
            identity_id=identity.id,
            now=now,
        )

        communication_group_id = identity.communication_group_id
        cleared_match_source = False
        if communication_group_id is not None:
            group = await session.get(IdentityCommunicationGroup, communication_group_id)
            if group is not None and group.match_source_identity_id == identity.id:
                group.match_source_identity_id = None
                group.updated_at = now
                cleared_match_source = True

        await _delete_identity_sync_runtime(session, identity.id)
        was_default = identity.is_default
        identity.smtp_password = ""
        identity.imap_password = None
        identity.current_primary_material_id = None
        identity.default_outreach_template_id = None
        identity.communication_group_id = None
        identity.next_send_after = None
        identity.is_default = False
        identity.deleted_at = now
        identity.updated_at = now

        if was_default:
            replacement = await session.scalar(
                select(IdentityProfile)
                .where(
                    IdentityProfile.id != identity.id,
                    IdentityProfile.deleted_at.is_(None),
                )
                .order_by(IdentityProfile.created_at.asc(), IdentityProfile.id.asc())
                .limit(1)
            )
            if replacement is not None:
                replacement.is_default = True
                replacement.updated_at = now

        await record_operation_log(
            session,
            category="user_action",
            event_name="identity.retired",
            entity_type="identity",
            entity_id=str(identity.id),
            metadata={
                "actor": "desktop_ui",
                "profile_name": identity.profile_name or identity.name,
                "email_address": identity.email_address,
                "was_default": was_default,
                "cleared_group_match_source": cleared_match_source,
                "stopped_batch_task_ids": actions.stop_batch_task_ids,
                "canceled_email_task_ids": actions.cancel_email_task_ids,
                "canceled_match_analysis_job_ids": actions.cancel_match_analysis_job_ids,
                "invalidated_agent_change_plan_ids": invalidated_plan_ids,
                "deletion_impact_revision": impact.revision,
            },
        )
        await session.flush()

        if communication_group_id is not None:
            group_cleanup = await cleanup_communication_group_after_identity_delete(
                session,
                group_id=communication_group_id,
                removed_identity_id=identity.id,
            )
            if group_cleanup is not None:
                await record_operation_log(
                    session,
                    category="identity",
                    event_name=(
                        "communication_group.deleted"
                        if group_cleanup.dissolved
                        else "communication_group.updated"
                    ),
                    entity_type="identity_communication_group",
                    entity_id=str(group_cleanup.group_id),
                    metadata={
                        "before_member_ids": list(group_cleanup.previous_member_ids),
                        "after_member_ids": (
                            []
                            if group_cleanup.dissolved
                            else list(group_cleanup.member_ids)
                        ),
                        "removed_identity_id": identity.id,
                        "cleared_match_source": cleared_match_source,
                    },
                )

        return IdentityRetirementResult(
            stopped_batch_task_ids=tuple(actions.stop_batch_task_ids),
            canceled_email_task_ids=tuple(actions.cancel_email_task_ids),
            canceled_match_analysis_job_ids=tuple(
                actions.cancel_match_analysis_job_ids
            ),
            invalidated_agent_change_plan_ids=tuple(invalidated_plan_ids),
        )
    except BaseException:
        end_identity_profile_retirement(identity.id)
        raise


async def _reference_counts(
    session: AsyncSession,
    identity_id: int,
) -> IdentityReferenceCounts:
    values: dict[str, int] = {}
    for key, (model, identity_column) in _REFERENCE_MODELS.items():
        values[key] = int(
            await session.scalar(
                select(func.count()).select_from(model).where(identity_column == identity_id)
            )
            or 0
        )
    match_job_condition = or_(
        MatchAnalysisJob.identity_id == identity_id,
        MatchAnalysisJob.match_source_identity_id == identity_id,
    )
    values["match_analysis_jobs"] = int(
        await session.scalar(
            select(func.count()).select_from(MatchAnalysisJob).where(match_job_condition)
        )
        or 0
    )
    plans = list(await session.scalars(select(AgentChangePlan.snapshot)))
    values["agent_change_plans"] = sum(
        1 for snapshot in plans if _snapshot_references_identity(snapshot, identity_id)
    )
    return IdentityReferenceCounts(**values)


async def _deletion_blockers(
    session: AsyncSession,
    identity_id: int,
    *,
    include_retirement_in_progress: bool,
) -> list[IdentityDeletionBlocker]:
    blockers: list[IdentityDeletionBlocker] = []
    sending_ids = list(
        await session.scalars(
            select(EmailTask.id)
            .where(
                EmailTask.identity_id == identity_id,
                EmailTask.status == EmailTaskStatus.SENDING.value,
            )
            .order_by(EmailTask.id.asc())
        )
    )
    _append_blocker(
        blockers,
        kind="sending_email_tasks",
        label="已进入发送中的邮件任务",
        ids=sending_ids,
        surface="任务中心 > 发送计划",
    )
    for kind, count in sorted(get_identity_profile_usage_counts(identity_id).items()):
        if count <= 0:
            continue
        blockers.append(
            IdentityDeletionBlocker(
                kind=f"interactive_{kind}",
                label=INTERACTIVE_USAGE_LABELS.get(kind, "正在执行的身份操作"),
                count=count,
                entity_ids=[],
                surface="当前操作，无需另行定位；等待操作结束后重试",
            )
        )
    if include_retirement_in_progress and identity_profile_retirement_in_progress(
        identity_id
    ):
        blockers.append(
            IdentityDeletionBlocker(
                kind="retirement_in_progress",
                label="另一个退役请求正在处理此身份",
                count=1,
                entity_ids=[],
                surface="身份配置页面；等待当前退役请求结束后刷新",
            )
        )
    return blockers


async def _automatic_actions(
    session: AsyncSession,
    identity_id: int,
) -> IdentityDeletionAutomaticActions:
    cancel_email_task_ids = list(
        await session.scalars(
            select(EmailTask.id)
            .where(
                EmailTask.identity_id == identity_id,
                EmailTask.status.in_(EMAIL_TASK_CANCEL_STATUSES),
            )
            .order_by(EmailTask.id.asc())
        )
    )
    stop_batch_task_ids = list(
        await session.scalars(
            select(BatchTask.id)
            .where(
                BatchTask.identity_id == identity_id,
                BatchTask.deleted_at.is_(None),
                BatchTask.status.in_(ACTIVE_BATCH_TASK_STATUSES),
            )
            .order_by(BatchTask.id.asc())
        )
    )
    cancel_match_analysis_job_ids = list(
        await session.scalars(
            select(MatchAnalysisJob.id)
            .where(
                or_(
                    MatchAnalysisJob.identity_id == identity_id,
                    MatchAnalysisJob.match_source_identity_id == identity_id,
                ),
                MatchAnalysisJob.deleted_at.is_(None),
                MatchAnalysisJob.status.in_(ACTIVE_MATCH_JOB_STATUSES),
            )
            .order_by(MatchAnalysisJob.id.asc())
        )
    )
    pending_plans = list(
        await session.execute(
            select(AgentChangePlan.id, AgentChangePlan.snapshot)
            .where(AgentChangePlan.status == "awaiting_confirmation")
            .order_by(AgentChangePlan.id.asc())
        )
    )
    invalidate_plan_ids = [
        plan_id
        for plan_id, snapshot in pending_plans
        if _snapshot_references_identity(snapshot, identity_id)
    ]
    return IdentityDeletionAutomaticActions(
        cancel_email_task_ids=cancel_email_task_ids,
        stop_batch_task_ids=stop_batch_task_ids,
        cancel_match_analysis_job_ids=cancel_match_analysis_job_ids,
        invalidate_agent_change_plan_ids=invalidate_plan_ids,
    )


async def _cancel_email_tasks(
    session: AsyncSession,
    task_ids: list[int],
    now: datetime,
) -> None:
    if not task_ids:
        return
    await session.execute(
        update(EmailTask)
        .where(
            EmailTask.id.in_(task_ids),
            EmailTask.status.in_(EMAIL_TASK_CANCEL_STATUSES),
        )
        .values(
            status=EmailTaskStatus.CANCELED.value,
            cancellation_reason=EmailTaskCancellationReason.IDENTITY_RETIRED.value,
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


async def _stop_batch_tasks(
    session: AsyncSession,
    batch_task_ids: list[int],
    now: datetime,
) -> None:
    if not batch_task_ids:
        return
    await session.execute(
        update(BatchTask)
        .where(
            BatchTask.id.in_(batch_task_ids),
            BatchTask.status.in_(ACTIVE_BATCH_TASK_STATUSES),
        )
        .values(status=BatchTaskStatus.STOPPED.value, updated_at=now)
        .execution_options(synchronize_session=False)
    )


async def _invalidate_pending_change_plans(
    session: AsyncSession,
    *,
    identity_id: int,
    now: datetime,
) -> list[str]:
    plans = list(
        await session.scalars(
            select(AgentChangePlan).where(
                AgentChangePlan.status == "awaiting_confirmation"
            )
        )
    )
    invalidated: list[str] = []
    for plan in plans:
        if not _snapshot_references_identity(plan.snapshot, identity_id):
            continue
        plan.status = "canceled"
        plan.canceled_at = now
        plan.failure_message = "关联发件身份已退役，请重新发起操作"
        plan.updated_at = now
        invalidated.append(plan.id)
    return sorted(invalidated)


async def _delete_identity_sync_runtime(
    session: AsyncSession,
    identity_id: int,
) -> None:
    for model in (ImapProfessorSyncState, ImapMailboxSyncState, ImapIdentitySyncLease):
        await session.execute(delete(model).where(model.identity_id == identity_id))


def _snapshot_references_identity(value: object, identity_id: int) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {
                "identity_id",
                "target_identity_id",
                "match_source_identity_id",
            } and child == identity_id:
                return True
            if _snapshot_references_identity(child, identity_id):
                return True
    elif isinstance(value, list):
        return any(_snapshot_references_identity(child, identity_id) for child in value)
    return False


def _append_blocker(
    blockers: list[IdentityDeletionBlocker],
    *,
    kind: str,
    label: str,
    ids: list[int],
    surface: str,
) -> None:
    if not ids:
        return
    blockers.append(
        IdentityDeletionBlocker(
            kind=kind,
            label=label,
            count=len(ids),
            entity_ids=ids[:MAX_BLOCKER_IDS],
            surface=surface,
        )
    )


def _blocker_message(
    identity_name: str,
    blockers: list[IdentityDeletionBlocker],
) -> str:
    if not blockers:
        return f"发件身份“{identity_name}”正在被其他操作使用，请稍后重试。"
    summaries: list[str] = []
    for blocker in blockers:
        ids = (
            f"（ID：{'、'.join(str(item) for item in blocker.entity_ids)}"
            f"{' 等' if blocker.count > len(blocker.entity_ids) else ''}）"
            if blocker.entity_ids
            else ""
        )
        summaries.append(f"{blocker.label} {blocker.count} 项{ids}")
    return (
        f"发件身份“{identity_name}”暂时无法退役：{'；'.join(summaries)}。"
        "请在任务中心定位上述任务，等待发送/生成结束或先取消对应操作。"
    )


def _serialize_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
