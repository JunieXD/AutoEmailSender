from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_api_errors import AgentApiError
from app.core.time import serialize_api_datetime
from app.models import AgentChangePlan, IdentityProfile, OutreachTemplate
from app.modules.campaigns.public import (
    OutreachTemplateMutationError,
    archive_outreach_template_record,
    get_outreach_template_or_raise,
)
from app.services.agent_mutations import fingerprint


async def _execute_template_archive(
    session: AsyncSession,
    plan: AgentChangePlan,
) -> dict[str, object]:
    snapshot = plan.snapshot
    template_id = snapshot.get("template_id")
    if not isinstance(template_id, int):
        raise AgentApiError(
            status_code=500,
            code="INVALID_CHANGE_PLAN_SNAPSHOT",
            message="变更计划快照无效，请重新生成计划。",
        )
    try:
        template = await get_outreach_template_or_raise(
            session,
            template_id,
            include_archived=True,
        )
    except OutreachTemplateMutationError as exc:
        raise AgentApiError(
            status_code=409,
            code="PLAN_STALE",
            message="计划对应的模板已不存在，请重新生成归档计划。",
            details={"changed_fields": ["template"]},
        ) from exc
    current_default_identity_count = int(
        await session.scalar(
            select(func.count(IdentityProfile.id)).where(
                IdentityProfile.default_outreach_template_id == template.id,
            ),
        )
        or 0,
    )
    expected_fingerprint = snapshot.get("template_fingerprint")
    current_fingerprint = _template_archive_snapshot_fingerprint(
        template,
        current_default_identity_count,
    )
    if template.archived_at is not None or expected_fingerprint != current_fingerprint:
        raise AgentApiError(
            status_code=409,
            code="PLAN_STALE",
            message="模板内容或状态已发生变化，请重新生成归档预览。",
            details={"changed_fields": ["template"]},
            suggested_command=f"auto-email-sender templates prepare-archive {template_id}",
        )
    await archive_outreach_template_record(
        session,
        template_id,
        event_name="agent_cli.template.archived",
        actor="agent_cli",
    )
    return {
        "outcome": "archived",
        "template_id": template_id,
        "template_name": template.name,
    }


def _build_template_archive_snapshot(
    template: OutreachTemplate,
    default_identity_count: int,
) -> dict[str, object]:
    warnings: list[str] = []
    if template.is_default:
        warnings.append("该模板当前是全局默认模板，归档后将不再作为全局默认模板。")
    if default_identity_count:
        warnings.append(
            f"归档后将解除 {default_identity_count} 个发件身份对该模板的默认关联。",
        )
    return {
        "snapshot_version": "1",
        "template_id": template.id,
        "template_updated_at": serialize_api_datetime(template.updated_at),
        "template_fingerprint": _template_archive_snapshot_fingerprint(
            template,
            default_identity_count,
        ),
        "summary": {
            "template": {"id": template.id, "name": template.name},
            "is_default": template.is_default,
            "default_identity_count": default_identity_count,
        },
        "warnings": warnings,
    }


def _template_archive_snapshot_fingerprint(
    template: OutreachTemplate,
    default_identity_count: int,
) -> str:
    """Capture all state that can change a template-archive plan's impact."""
    return fingerprint(
        {
            "template_id": template.id,
            "name": template.name,
            "recommended_generation_mode": template.recommended_generation_mode,
            "subject": template.subject,
            "body_text": template.body_text,
            "body_html": template.body_html,
            "is_default": template.is_default,
            "archived_at": (
                serialize_api_datetime(template.archived_at)
                if template.archived_at is not None
                else None
            ),
            "default_identity_count": default_identity_count,
        },
    )


def _template_error(error: OutreachTemplateMutationError) -> AgentApiError:
    return AgentApiError(
        status_code=error.status_code,
        code=error.code,
        message=error.message,
    )
