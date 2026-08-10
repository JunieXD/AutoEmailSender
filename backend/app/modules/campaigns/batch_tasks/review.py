from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.time import utc_now
from app.models import (
    EmailDirection,
    EmailLog,
    EmailTask,
    EmailTaskSource,
    EmailTaskStatus,
)
from app.modules.communications.public import (
    load_email_task,
    record_email_task_log,
)

from ..drafts.fallback import (
    DRAFT_FALLBACK_REASON_MISSING_RESEARCH_DIRECTION,
    DRAFT_GENERATION_SOURCE_TEMPLATE,
    DRAFT_GENERATION_SOURCE_TEMPLATE_FALLBACK,
    professor_has_research_direction,
)
from ..templates.library import get_outreach_template
from ..templates.rendering import (
    render_outreach_template,
    resolve_outreach_template_config,
)


async def apply_batch_review_outreach_template(
    session_factory: async_sessionmaker[AsyncSession],
    batch_task_id: int,
    task_id: int,
    *,
    outreach_template_id: int | None,
) -> tuple[int, int, int]:
    """Replace one review draft with a rendered template without leaving review."""

    if outreach_template_id is None:
        raise ValueError("请选择要套用的模板")

    async with session_factory() as session:
        task = await load_email_task(session, task_id)
        if (
            task is None
            or task.source != EmailTaskSource.BATCH.value
            or task.batch_task_id != batch_task_id
        ):
            raise ValueError("批量任务项不存在")
        if task.batch_send_canceled_at is not None:
            raise ValueError("该导师已取消发送，请先恢复发送")
        if task.status != EmailTaskStatus.REVIEW_REQUIRED.value:
            raise ValueError("草稿状态已发生变化，请刷新后重试")

        selected_template = await get_outreach_template(
            session,
            outreach_template_id,
        )
        outreach_config = resolve_outreach_template_config(
            task.identity,
            template=selected_template,
        )
        rendered = render_outreach_template(
            task.identity,
            task.professor,
            subject_template=outreach_config.subject_template,
            body_text_template=outreach_config.body_text_template,
            body_html_template=outreach_config.body_html_template,
        )

        keeps_missing_direction_fallback = not professor_has_research_direction(
            task.professor
        ) and (
            task.draft_generation_source == DRAFT_GENERATION_SOURCE_TEMPLATE_FALLBACK
            or task.draft_fallback_reason
            == DRAFT_FALLBACK_REASON_MISSING_RESEARCH_DIRECTION
        )
        generation_source = (
            DRAFT_GENERATION_SOURCE_TEMPLATE_FALLBACK
            if keeps_missing_direction_fallback
            else DRAFT_GENERATION_SOURCE_TEMPLATE
        )
        fallback_reason = (
            DRAFT_FALLBACK_REASON_MISSING_RESEARCH_DIRECTION
            if keeps_missing_direction_fallback
            else None
        )
        now = utc_now()
        update_result = await session.execute(
            update(EmailTask)
            .where(
                EmailTask.id == task_id,
                EmailTask.batch_task_id == batch_task_id,
                EmailTask.source == EmailTaskSource.BATCH.value,
                EmailTask.status == EmailTaskStatus.REVIEW_REQUIRED.value,
                EmailTask.batch_send_canceled_at.is_(None),
            )
            .values(
                outreach_template_id=selected_template.id,
                outreach_template_snapshot_version=1,
                outreach_generation_mode=outreach_config.generation_mode,
                outreach_template_subject=_normalize_nullable_text(
                    outreach_config.subject_template,
                ),
                outreach_template_body_text=_normalize_nullable_text(
                    outreach_config.body_text_template,
                ),
                outreach_template_body_html=_normalize_nullable_text(
                    outreach_config.body_html_template,
                ),
                generated_subject=rendered.subject,
                generated_content_text=rendered.body_text,
                generated_content_html=rendered.body_html,
                draft_generation_source=generation_source,
                draft_fallback_reason=fallback_reason,
                approved_subject=None,
                approved_body_text=None,
                approved_body_html=None,
                approved_at=None,
                draft_rewrite_source_subject=None,
                draft_rewrite_source_body_text=None,
                draft_rewrite_source_body_html=None,
                draft_rewrite_source_selected_material_ids=None,
                draft_generation_previous_status=None,
                draft_generation_started_at=None,
                draft_claim_id=None,
                draft_claimed_at=None,
                draft_lease_expires_at=None,
                status=EmailTaskStatus.REVIEW_REQUIRED.value,
                last_error=None,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if update_result.rowcount != 1:
            await session.rollback()
            raise ValueError("草稿状态已发生变化，请刷新后重试")

        await session.refresh(task)
        session.add(
            EmailLog(
                email_task_id=task.id,
                identity_id=task.identity_id,
                llm_profile_id=task.llm_profile_id,
                professor_id=task.professor_id,
                direction=EmailDirection.DRAFT.value,
                subject=rendered.subject,
                content=rendered.body_text,
                content_html=rendered.body_html,
                provider_payload={
                    "source": "batch_review_template_apply",
                    "outreach_template_id": selected_template.id,
                },
            ),
        )
        await record_email_task_log(
            session,
            task,
            "email_task.batch_review_template_applied",
            metadata={
                "outreach_template_id": selected_template.id,
                "outreach_generation_mode": outreach_config.generation_mode,
                "draft_generation_source": generation_source,
                "draft_fallback_reason": fallback_reason,
            },
        )
        await session.commit()
        return task.professor_id, task.identity_id, task.llm_profile_id


def _normalize_nullable_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None
