"""Compatibility exports for migrated match-analysis schemas."""

from app.modules.matching.schemas import (
    CreateMatchAnalysisJobRequest,
    MatchAnalysisJobActionResponse,
    MatchAnalysisJobItemRead,
    MatchAnalysisJobRead,
)

__all__ = [
    "CreateMatchAnalysisJobRequest",
    "MatchAnalysisJobActionResponse",
    "MatchAnalysisJobItemRead",
    "MatchAnalysisJobRead",
]
