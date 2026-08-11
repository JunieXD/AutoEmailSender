"""merge desktop split safety and master business migration heads

Revision ID: 20260812_merge_beta_master
Revises: 20260810_merge_agent_ui_delivery, 20260811_delivery_reconcile
Create Date: 2026-08-12 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence


revision: str = "20260812_merge_beta_master"
down_revision: str | Sequence[str] | None = (
    "20260810_merge_agent_ui_delivery",
    "20260811_delivery_reconcile",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Join both already-applied schema branches without additional DDL."""


def downgrade() -> None:
    """Split the revision graph without reverting either parent migration."""
