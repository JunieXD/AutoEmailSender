from __future__ import annotations

from dataclasses import dataclass

from app.models import EmailTask, IdentityProfile, Professor
from app.services.outreach_templates import (
    OUTREACH_GENERATION_MODE_LLM,
    OUTREACH_GENERATION_MODE_TEMPLATE,
    OutreachTemplateConfig,
    build_outreach_template_snapshot_config,
    has_outreach_template_snapshot,
    render_outreach_template,
    resolve_outreach_template_config,
)


DRAFT_GENERATION_SOURCE_LLM = "llm"
DRAFT_GENERATION_SOURCE_TEMPLATE = "template"
DRAFT_GENERATION_SOURCE_TEMPLATE_FALLBACK = "template_fallback"
DRAFT_FALLBACK_REASON_MISSING_RESEARCH_DIRECTION = "missing_research_direction"


@dataclass(frozen=True, slots=True)
class InitialBatchDraft:
    subject: str
    body_text: str
    body_html: str
    generation_source: str
    fallback_reason: str | None = None


def professor_has_research_direction(professor: Professor) -> bool:
    return bool((professor.research_direction or "").strip())


def build_initial_batch_draft(
    identity: IdentityProfile,
    professor: Professor,
    outreach_config: OutreachTemplateConfig,
    *,
    primary_material_available: bool,
) -> InitialBatchDraft | None:
    generation_source: str
    fallback_reason: str | None = None
    if outreach_config.generation_mode == OUTREACH_GENERATION_MODE_TEMPLATE:
        generation_source = DRAFT_GENERATION_SOURCE_TEMPLATE
    elif (
        outreach_config.generation_mode == OUTREACH_GENERATION_MODE_LLM
        and primary_material_available
        and not professor_has_research_direction(professor)
    ):
        generation_source = DRAFT_GENERATION_SOURCE_TEMPLATE_FALLBACK
        fallback_reason = DRAFT_FALLBACK_REASON_MISSING_RESEARCH_DIRECTION
    else:
        return None

    rendered = render_outreach_template(
        identity,
        professor,
        subject_template=outreach_config.subject_template,
        body_text_template=outreach_config.body_text_template,
        body_html_template=outreach_config.body_html_template,
    )
    return InitialBatchDraft(
        subject=rendered.subject,
        body_text=rendered.body_text,
        body_html=rendered.body_html,
        generation_source=generation_source,
        fallback_reason=fallback_reason,
    )


def build_missing_research_fallback_for_task(
    task: EmailTask,
) -> InitialBatchDraft | None:
    if task.professor is None or task.identity is None:
        return None
    config = _resolve_task_template_config(task)
    return build_initial_batch_draft(
        task.identity,
        task.professor,
        config,
        primary_material_available=task.primary_material_id is not None,
    )


def _resolve_task_template_config(task: EmailTask) -> OutreachTemplateConfig:
    if has_outreach_template_snapshot(
        snapshot_version=task.outreach_template_snapshot_version,
        template_id=task.outreach_template_id,
        subject_template=task.outreach_template_subject,
        body_text_template=task.outreach_template_body_text,
        body_html_template=task.outreach_template_body_html,
    ):
        return build_outreach_template_snapshot_config(
            generation_mode=task.outreach_generation_mode,
            subject_template=task.outreach_template_subject,
            body_text_template=task.outreach_template_body_text,
            body_html_template=task.outreach_template_body_html,
        )

    batch_task = task.batch_task
    if batch_task is not None and has_outreach_template_snapshot(
        snapshot_version=batch_task.outreach_template_snapshot_version,
        template_id=batch_task.outreach_template_id,
        subject_template=batch_task.outreach_template_subject,
        body_text_template=batch_task.outreach_template_body_text,
        body_html_template=batch_task.outreach_template_body_html,
    ):
        return build_outreach_template_snapshot_config(
            generation_mode=batch_task.outreach_generation_mode,
            subject_template=batch_task.outreach_template_subject,
            body_text_template=batch_task.outreach_template_body_text,
            body_html_template=batch_task.outreach_template_body_html,
        )

    return resolve_outreach_template_config(
        task.identity,
        generation_mode=OUTREACH_GENERATION_MODE_LLM,
        subject_template=(batch_task.email_subject if batch_task is not None else None),
        body_text_template=(batch_task.email_body if batch_task is not None else None),
    )
