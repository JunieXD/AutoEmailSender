from __future__ import annotations

from app.models import IdentityMaterial

from .schemas import IdentityMaterialRead


def serialize_material(
    material: IdentityMaterial,
    current_primary_material_id: int | None,
) -> IdentityMaterialRead:
    return IdentityMaterialRead(
        id=material.id,
        display_name=material.display_name,
        original_filename=material.original_filename,
        mime_type=material.mime_type,
        size_bytes=material.size_bytes,
        material_type=material.material_type,
        is_primary=material.id == current_primary_material_id,
        created_at=material.created_at,
    )
