"""Compatibility exports for migrated structured-output adaptation."""

from app.modules.llm.adaptation import structured_output as _structured_output
from app.modules.llm.adaptation.structured_output import *  # noqa: F403

__all__ = _structured_output.__all__
