"""merge delivery safety and professor scale migration branches

Revision ID: 20260810_merge_delivery_scale
Revises: 20260809_delivery_at_most_once, 20260809_professor_scale_search
Create Date: 2026-08-10 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence


revision: str = "20260810_merge_delivery_scale"
down_revision: str | Sequence[str] | None = (
    "20260809_delivery_at_most_once",
    "20260809_professor_scale_search",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Join both already-applied schema branches without additional DDL."""


def downgrade() -> None:
    """Split the revision graph without reverting either parent migration."""
