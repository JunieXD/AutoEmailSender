"""Compatibility exports for the migrated runtime-settings HTTP adapter."""

from app.modules.system.runtime_settings.api import (
    patch_runtime_settings,
    read_runtime_settings,
    router,
)

__all__ = ["patch_runtime_settings", "read_runtime_settings", "router"]
