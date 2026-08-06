"""Compatibility exports for migrated professor field normalization."""

from app.modules.professors.normalization import (
    RECENT_PAPERS_MAX_ITEMS,
    normalize_recent_papers,
    normalize_research_direction,
)

__all__ = [
    "RECENT_PAPERS_MAX_ITEMS",
    "normalize_recent_papers",
    "normalize_research_direction",
]
