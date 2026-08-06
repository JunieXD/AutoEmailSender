"""Compatibility export for runtime-settings persistence initialization."""

from app.modules.system.runtime_settings.service import get_or_create_app_settings

__all__ = ["get_or_create_app_settings"]
