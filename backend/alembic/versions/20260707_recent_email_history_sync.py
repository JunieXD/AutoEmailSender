"""add recent email history strategy version

Revision ID: 20260707_recent_email_history_sync
Revises: 20260703_imap_folder_history_scan
Create Date: 2026-07-07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260707_recent_email_history_sync"
down_revision = "20260703_imap_folder_history_scan"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "imap_professor_sync_states",
        sa.Column(
            "history_strategy_version",
            sa.String(length=32),
            nullable=False,
            server_default="legacy",
        ),
    )


def downgrade() -> None:
    op.drop_column("imap_professor_sync_states", "history_strategy_version")
