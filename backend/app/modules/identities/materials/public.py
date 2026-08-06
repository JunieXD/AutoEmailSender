"""Stable facade for identity-material capabilities."""

from .schemas import IdentityMaterialRead, IdentityMaterialTypeRead
from .serializer import serialize_material
from .service import (
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
from .support import (
    MATERIAL_REFERENCE_BLOCKING_STATUSES,
    MATERIAL_REFERENCE_DETACHABLE_STATUSES,
    MATERIAL_REFERENCE_RESET_DRAFT_STATUSES,
    build_material_download_name,
    ensure_material_extracted_text,
    material_can_be_primary,
    material_reference_fallback_status,
)

__all__ = [
    "IdentityMaterialRead",
    "IdentityMaterialTypeRead",
    "MATERIAL_REFERENCE_BLOCKING_STATUSES",
    "MATERIAL_REFERENCE_DETACHABLE_STATUSES",
    "MATERIAL_REFERENCE_RESET_DRAFT_STATUSES",
    "MaterialDeletionResult",
    "MaterialMutationError",
    "build_material_download_name",
    "delete_identity_material_record",
    "ensure_material_extracted_text",
    "get_identity_for_materials_or_raise",
    "get_material_with_identity_or_raise",
    "material_can_be_primary",
    "material_reference_fallback_status",
    "normalize_material_type",
    "prepare_material_deletion_snapshot",
    "record_material_event",
    "set_primary_material_record",
    "serialize_material",
    "upload_identity_material_record",
]
