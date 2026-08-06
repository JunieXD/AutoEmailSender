"""Compatibility exports for migrated matching helpers."""

from app.modules.matching.scoring import build_draft_email, estimate_match_score

__all__ = ["build_draft_email", "estimate_match_score"]
