"""merge identity match result and batch draft fallback heads

Revision ID: 20260805_merge_match_fallback
Revises: 20260805_batch_draft_fallback, 20260805_identity_match_results
Create Date: 2026-08-05 00:10:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union


revision: str = "20260805_merge_match_fallback"
down_revision: Union[str, Sequence[str], None] = (
    "20260805_batch_draft_fallback",
    "20260805_identity_match_results",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
