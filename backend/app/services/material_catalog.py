from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only, selectinload

from app.core.query_chunks import chunked_values
from app.models import IdentityMaterial

# Every column except ``extracted_text``; attachment/draft flows that need the
# document body must load it separately instead of hydrating the whole catalog.
MATERIAL_METADATA_LOAD_ONLY = (
    IdentityMaterial.id,
    IdentityMaterial.identity_id,
    IdentityMaterial.display_name,
    IdentityMaterial.original_filename,
    IdentityMaterial.file_path,
    IdentityMaterial.mime_type,
    IdentityMaterial.size_bytes,
    IdentityMaterial.sha256,
    IdentityMaterial.material_type,
    IdentityMaterial.created_at,
)


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


async def list_global_material_metadata(
    session: AsyncSession,
) -> list[IdentityMaterial]:
    """Return the material catalog without loading extracted text bodies."""
    return list(
        await session.scalars(
            select(IdentityMaterial)
            .options(
                load_only(*MATERIAL_METADATA_LOAD_ONLY),
                selectinload(IdentityMaterial.default_for_identities),
            )
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
