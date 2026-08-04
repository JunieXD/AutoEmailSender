"""merge Agent change-plan and recent-paper migration heads

Revision ID: 20260804_merge_agent_change_recent_papers
Revises: 20260803_agent_change_plans, 20260804_cap_recent_papers
Create Date: 2026-08-04 00:00:01.000000
"""

from __future__ import annotations

from typing import Sequence, Union


revision: str = "20260804_merge_agent_change_recent_papers"
down_revision: Union[str, Sequence[str], None] = (
    "20260803_agent_change_plans",
    "20260804_cap_recent_papers",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Join two already-applied branches without changing data."""


def downgrade() -> None:
    """Split the graph back into its two parent revisions without changing data."""
