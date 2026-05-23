from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppSetting
from app.schemas.runtime_settings import RuntimeSettingsRead, RuntimeSettingsUpdate
from app.services.operation_logs import record_operation_log
from app.services.system_settings import get_or_create_app_settings


def serialize_runtime_settings(settings: AppSetting) -> RuntimeSettingsRead:
    return RuntimeSettingsRead(
        match_analysis_job_worker_count=settings.match_analysis_job_worker_count,
        match_analysis_job_item_concurrency=settings.match_analysis_job_item_concurrency,
        match_analysis_job_interval_seconds=settings.match_analysis_job_interval_seconds,
        crawler_worker_count=settings.crawler_worker_count,
        crawler_profile_enrichment_concurrency=settings.crawler_profile_enrichment_concurrency,
        crawler_host_concurrency=settings.crawler_host_concurrency,
        draft_max_tokens=settings.draft_max_tokens,
        batch_draft_generation_concurrency=settings.batch_draft_generation_concurrency,
        draft_rewrite_intensity=settings.draft_rewrite_intensity,
        draft_rewrite_tone=settings.draft_rewrite_tone,
        draft_rewrite_formality=settings.draft_rewrite_formality,
        draft_rewrite_length=settings.draft_rewrite_length,
        draft_rewrite_specificity=settings.draft_rewrite_specificity,
        draft_template_preservation=settings.draft_template_preservation,
        draft_custom_instruction=settings.draft_custom_instruction,
        updated_at=settings.updated_at,
    )


async def get_runtime_settings(session: AsyncSession) -> AppSetting:
    return await get_or_create_app_settings(session)


async def update_runtime_settings(
    session: AsyncSession,
    payload: RuntimeSettingsUpdate,
) -> AppSetting:
    settings = await get_or_create_app_settings(session)
    previous = serialize_runtime_settings(settings).model_dump(mode="json")
    next_values = payload.model_dump()

    for key, value in next_values.items():
        setattr(settings, key, value)
    settings.updated_at = datetime.now(UTC)

    await record_operation_log(
        session,
        category="backend",
        event_name="runtime_settings.updated",
        message="运行时设置已更新",
        entity_type="runtime_settings",
        entity_id="1",
        metadata={
            "previous": previous,
            "next": next_values,
        },
    )
    return settings
