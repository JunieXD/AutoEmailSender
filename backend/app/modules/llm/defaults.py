from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.models import LLMProfile


async def set_default_llm_profile_record(
    session: AsyncSession, selected: LLMProfile, *, refresh_timestamps: bool = False
) -> None:
    """Select the default among active profiles in the caller's transaction."""
    profiles = await session.scalars(
        select(LLMProfile).where(LLMProfile.deleted_at.is_(None))
    )
    now = utc_now()
    for profile in profiles:
        is_default = profile.id == selected.id
        if profile.is_default != is_default or refresh_timestamps:
            profile.is_default = is_default
            profile.updated_at = now
