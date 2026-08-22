"""add thinking adaptation probe version

Revision ID: 20260822_thinking_probe_v2
Revises: 20260822_enrichment_task_op
Create Date: 2026-08-22 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260822_thinking_probe_v2"
down_revision: str | Sequence[str] | None = "20260822_enrichment_task_op"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _thinking_cache_columns() -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {
        column["name"]
        for column in inspector.get_columns("thinking_adaptation_cache")
    }


def upgrade() -> None:
    if "probe_version" in _thinking_cache_columns():
        return

    op.add_column(
        "thinking_adaptation_cache",
        sa.Column(
            "probe_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )


def downgrade() -> None:
    if "probe_version" not in _thinking_cache_columns():
        return

    op.drop_column("thinking_adaptation_cache", "probe_version")
