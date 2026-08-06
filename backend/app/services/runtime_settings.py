"""Compatibility exports for the migrated runtime-settings service."""

from app.modules.system.public import (
    get_runtime_settings,
    serialize_runtime_settings,
    update_runtime_settings,
)

__all__ = [
    "get_runtime_settings",
    "serialize_runtime_settings",
    "update_runtime_settings",
]
