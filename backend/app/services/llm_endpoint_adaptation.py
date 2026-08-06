"""Compatibility exports for migrated LLM endpoint adaptation."""

from app.modules.llm.adaptation import endpoint as _endpoint
from app.modules.llm.adaptation.endpoint import *  # noqa: F403

__all__ = _endpoint.__all__
