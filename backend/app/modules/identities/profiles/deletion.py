from __future__ import annotations

from hashlib import sha256
import json

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BatchTask,
    EmailDeliveryAttempt,
    EmailLog,
    EmailObservation,
    EmailTask,
    IdentityMaterial,
    IdentityProfessorMatchResult,
    IdentityProfile,
    MatchAnalysisJob,
    MatchAnalysisRun,
    TestComposeMessage,
    TestComposeSession,
)

from .schemas import (
    IdentityDeletionBlocker,
    IdentityDeletionImpact,
    IdentityReferenceCounts,
)


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

_REFERENCE_LABELS = {
    "email_tasks": "邮件任务",
    "email_logs": "邮件与通信记录",
    "batch_tasks": "批量任务",
    "test_compose_sessions": "测试写信会话",
    "test_compose_messages": "测试邮件记录",
    "match_analysis_jobs": "匹配分析任务",
    "match_analysis_runs": "匹配运行记录",
    "match_results": "导师匹配结果",
    "delivery_attempts": "邮件投递尝试",
    "email_observations": "邮件投递观测记录",
}


async def build_identity_deletion_impact(
    session: AsyncSession,
    identity: IdentityProfile,
) -> IdentityDeletionImpact:
    reference_values: dict[str, int] = {}
    reference_ids: dict[str, list[int | str]] = {}
    for key, (model, identity_column) in _REFERENCE_MODELS.items():
        reference_values[key] = int(
            await session.scalar(
                select(func.count()).select_from(model).where(identity_column == identity.id)
            )
            or 0
        )
        reference_ids[key] = list(
            await session.scalars(
                select(model.id).where(identity_column == identity.id).order_by(model.id).limit(5)
            )
        )

    match_job_condition = or_(
        MatchAnalysisJob.identity_id == identity.id,
        MatchAnalysisJob.match_source_identity_id == identity.id,
    )
    reference_values["match_analysis_jobs"] = int(
        await session.scalar(
            select(func.count()).select_from(MatchAnalysisJob).where(match_job_condition)
        )
        or 0
    )
    reference_ids["match_analysis_jobs"] = list(
        await session.scalars(
            select(MatchAnalysisJob.id)
            .where(match_job_condition)
            .order_by(MatchAnalysisJob.id)
            .limit(5)
        )
    )

    references = IdentityReferenceCounts(**reference_values)
    blockers = [
        IdentityDeletionBlocker(
            kind=key,
            label=_REFERENCE_LABELS[key],
            count=count,
            entity_ids=reference_ids[key],
        )
        for key, count in reference_values.items()
        if count > 0
    ]
    preserved_material_count = int(
        await session.scalar(
            select(func.count())
            .select_from(IdentityMaterial)
            .where(IdentityMaterial.identity_id == identity.id)
        )
        or 0
    )
    warnings = ["删除身份只会删除发信配置，不会删除独立的邮件模板。"]
    if preserved_material_count:
        warnings.append(
            f"该身份上传的 {preserved_material_count} 份材料会保留在材料库中。"
        )
    if identity.communication_group_id is not None:
        warnings.append("该身份会退出通信共享组；不足两个成员的共享组会自动解散。")
    if blockers:
        warnings.insert(0, "为避免破坏历史记录，存在业务历史的身份不能物理删除。")

    revision_payload = {
        "identity_id": identity.id,
        "updated_at": identity.updated_at.isoformat(),
        "references": references.model_dump(),
        "preserved_material_count": preserved_material_count,
        "communication_group_id": identity.communication_group_id,
    }
    revision = sha256(
        json.dumps(
            revision_payload,
            ensure_ascii=False,
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
        preserved_material_count=preserved_material_count,
        communication_group_id=identity.communication_group_id,
        warnings=warnings,
    )
