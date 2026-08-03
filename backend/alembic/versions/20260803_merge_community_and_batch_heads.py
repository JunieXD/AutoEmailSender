"""merge community mentor and batch task migration heads

Revision ID: 20260803_merge_community_batch
Revises: 20260802_batch_send_cancel, 20260803_community_links
Create Date: 2026-08-03 00:00:01.000000
"""

from __future__ import annotations

from typing import Sequence, Union


revision: str = "20260803_merge_community_batch"
down_revision: Union[str, Sequence[str], None] = (
    "20260802_batch_send_cancel",
    "20260803_community_links",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Join the two already-applied schema branches without changing data."""


def downgrade() -> None:
    """Split the graph back into its two parent revisions without changing data."""
