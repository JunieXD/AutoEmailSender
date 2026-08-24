from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IdentityProfile


RETIRED_IDENTITY_MESSAGE = "发件身份已删除，请选择其他发件身份"


def identity_profile_is_active(identity: IdentityProfile | None) -> bool:
    return identity is not None and identity.deleted_at is None


async def get_active_identity_profile(
    session: AsyncSession,
    identity_id: int,
) -> IdentityProfile | None:
    return await session.scalar(
        select(IdentityProfile).where(
            IdentityProfile.id == identity_id,
            IdentityProfile.deleted_at.is_(None),
        )
    )
