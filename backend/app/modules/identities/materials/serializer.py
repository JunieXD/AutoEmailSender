from __future__ import annotations

from app.models import IdentityMaterial

from .schemas import IdentityMaterialRead


def serialize_material(
    material: IdentityMaterial,
    current_primary_material_id: int | None = None,
    *,
    default_for_identity_ids: list[int] | None = None,
) -> IdentityMaterialRead:
    resolved_default_identity_ids = (
        sorted(set(default_for_identity_ids))
        if default_for_identity_ids is not None
        else []
    )
    return IdentityMaterialRead(
        id=material.id,
        source_identity_id=material.identity_id,
        display_name=material.display_name,
        original_filename=material.original_filename,
        mime_type=material.mime_type,
        size_bytes=material.size_bytes,
        material_type=material.material_type,
        is_primary=material.id == current_primary_material_id,
        default_for_identity_ids=resolved_default_identity_ids,
        created_at=material.created_at,
    )
