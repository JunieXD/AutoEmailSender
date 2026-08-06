"""Compatibility exports for migrated identity-material support rules."""

from app.modules.identities.materials.support import (
    MATERIAL_REFERENCE_BLOCKING_STATUSES,
    MATERIAL_REFERENCE_DETACHABLE_STATUSES,
    MATERIAL_REFERENCE_RESET_DRAFT_STATUSES,
    build_material_download_name,
    ensure_material_extracted_text,
    material_can_be_primary,
    material_reference_fallback_status,
)

__all__ = [
    "MATERIAL_REFERENCE_BLOCKING_STATUSES",
    "MATERIAL_REFERENCE_DETACHABLE_STATUSES",
    "MATERIAL_REFERENCE_RESET_DRAFT_STATUSES",
    "build_material_download_name",
    "ensure_material_extracted_text",
    "material_can_be_primary",
    "material_reference_fallback_status",
]
