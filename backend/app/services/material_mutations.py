"""Compatibility exports for migrated identity-material lifecycle services."""

from app.modules.identities.materials.service import (
    MaterialDeletionResult,
    MaterialMutationError,
    delete_identity_material_record,
    get_identity_for_materials_or_raise,
    get_material_with_identity_or_raise,
    normalize_material_type,
    prepare_material_deletion_snapshot,
    record_material_event,
    set_primary_material_record,
    upload_identity_material_record,
)

__all__ = [
    "MaterialDeletionResult",
    "MaterialMutationError",
    "delete_identity_material_record",
    "get_identity_for_materials_or_raise",
    "get_material_with_identity_or_raise",
    "normalize_material_type",
    "prepare_material_deletion_snapshot",
    "record_material_event",
    "set_primary_material_record",
    "upload_identity_material_record",
]
