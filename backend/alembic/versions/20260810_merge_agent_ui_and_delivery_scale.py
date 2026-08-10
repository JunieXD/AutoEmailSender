"""merge Agent UI handoff and delivery/scale migration heads

Revision ID: 20260810_merge_agent_ui_delivery
Revises: 20260810_agent_ui_handoffs, 20260810_merge_delivery_scale
Create Date: 2026-08-10 00:00:01.000000
"""

from __future__ import annotations

from typing import Sequence


revision: str = "20260810_merge_agent_ui_delivery"
down_revision: str | Sequence[str] | None = (
    "20260810_agent_ui_handoffs",
    "20260810_merge_delivery_scale",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Join both already-applied schema branches without additional DDL."""


def downgrade() -> None:
    """Split the revision graph without reverting either parent migration."""
