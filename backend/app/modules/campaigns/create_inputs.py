"""Shared resource resolution for desktop and Agent campaign creation."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.query_chunks import chunked_values, unique_positive_ids
from app.models import IdentityProfile, Professor


async def load_campaign_identity(
    session: AsyncSession, identity_id: int
) -> IdentityProfile | None:
    return await session.scalar(
        select(IdentityProfile)
        .options(selectinload(IdentityProfile.current_primary_material))
        .where(IdentityProfile.id == identity_id, IdentityProfile.deleted_at.is_(None))
    )


async def load_campaign_professors(
    session: AsyncSession, professor_ids: list[int]
) -> list[Professor]:
    professors: list[Professor] = []
    for chunk in chunked_values(unique_positive_ids(professor_ids)):
        professors.extend(
            await session.scalars(
                select(Professor).where(
                    Professor.id.in_(chunk), Professor.archived_at.is_(None)
                )
            )
        )
    professors.sort(key=lambda professor: professor.id)
    return professors
