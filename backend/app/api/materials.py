"""Compatibility exports for the migrated identity-material HTTP adapter."""

from app.modules.identities.materials.api import (
    delete_material,
    download_material,
    open_material,
    router,
    set_primary_material,
    upload_identity_material,
)

__all__ = [
    "delete_material",
    "download_material",
    "open_material",
    "router",
    "set_primary_material",
    "upload_identity_material",
]
