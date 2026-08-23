from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LLMProfile


DELETED_LLM_PROFILE_MESSAGE = "原模型配置已删除，请选择新的模型配置"


def llm_profile_is_active(profile: LLMProfile | None) -> bool:
    return profile is not None and profile.deleted_at is None


async def get_active_llm_profile(
    session: AsyncSession,
    profile_id: int,
) -> LLMProfile | None:
    return await session.scalar(
        select(LLMProfile).where(
            LLMProfile.id == profile_id,
            LLMProfile.deleted_at.is_(None),
        )
    )


async def get_default_active_llm_profile(
    session: AsyncSession,
) -> LLMProfile | None:
    return await session.scalar(
        select(LLMProfile)
        .where(
            LLMProfile.deleted_at.is_(None),
            LLMProfile.is_default.is_(True),
        )
        .order_by(LLMProfile.created_at.asc(), LLMProfile.id.asc())
        .limit(1)
    )
