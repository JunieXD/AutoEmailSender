from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.query_chunks import chunked_values
from app.models import IdentityMaterial


async def list_global_materials(session: AsyncSession) -> list[IdentityMaterial]:
    """Return the shared material catalog in stable UI order."""
    return list(
        await session.scalars(
            select(IdentityMaterial)
            .options(selectinload(IdentityMaterial.default_for_identities))
            .order_by(
                IdentityMaterial.created_at.desc(),
                IdentityMaterial.id.desc(),
            ),
        ),
    )


async def get_global_materials_by_id(
    session: AsyncSession,
    material_ids: Iterable[int],
) -> dict[int, IdentityMaterial]:
    unique_ids = set(material_ids)
    if not unique_ids:
        return {}

    materials: dict[int, IdentityMaterial] = {}
    for material_id_chunk in chunked_values(unique_ids):
        rows = await session.scalars(
            select(IdentityMaterial).where(IdentityMaterial.id.in_(material_id_chunk)),
        )
        materials.update({material.id: material for material in rows})
    return materials
