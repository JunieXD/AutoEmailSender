"""preserve the published v2.6.0-beta.1 revision as a compatibility node

Revision ID: 20260812_merge_beta_master
Revises: 20260811_delivery_reconcile
Create Date: 2026-08-12 00:00:00.000000

The published beta used this revision to merge an abandoned runtime branch
with the formal delivery reconciliation branch. Its merge itself had no DDL.
Keeping the revision lets Alembic recognize beta databases without replaying
the abandoned branch for fresh formal installations.
"""

from __future__ import annotations

from collections.abc import Sequence


revision: str = "20260812_merge_beta_master"
down_revision: str | Sequence[str] | None = "20260811_delivery_reconcile"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """The published merge revision did not change the schema."""


def downgrade() -> None:
    """The published merge revision did not change the schema."""
