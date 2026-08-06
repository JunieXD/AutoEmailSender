"""Compatibility exports for migrated LLM thinking adaptation."""

from app.modules.llm.adaptation import thinking as _thinking
from app.modules.llm.adaptation.thinking import *  # noqa: F403

__all__ = _thinking.__all__
