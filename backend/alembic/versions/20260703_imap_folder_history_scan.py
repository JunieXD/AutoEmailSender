"""imap folder history scan

Revision ID: 20260703_imap_folder_history_scan
Revises: 20260702_identity_next_send_after
Create Date: 2026-07-03 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260703_imap_folder_history_scan"
down_revision: Union[str, Sequence[str], None] = "20260702_identity_next_send_after"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("imap_mailbox_sync_states", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "history_scan_status",
                sa.String(length=32),
                server_default="pending",
                nullable=False,
            ),
        )
        batch_op.add_column(sa.Column("history_high_water_uid", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("history_next_before_uid", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("history_scan_started_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("history_scan_completed_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("history_last_error", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("history_scanned_count", sa.Integer(), server_default="0", nullable=False),
        )
        batch_op.add_column(
            sa.Column("history_matched_count", sa.Integer(), server_default="0", nullable=False),
        )
        batch_op.add_column(
            sa.Column(
                "history_strategy_version",
                sa.String(length=32),
                server_default="folder-v1",
                nullable=False,
            ),
        )

    op.create_index(
        "ix_imap_mailbox_sync_identity_history_status_updated",
        "imap_mailbox_sync_states",
        ["identity_id", "history_scan_status", "updated_at", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_imap_mailbox_sync_identity_history_status_updated",
        table_name="imap_mailbox_sync_states",
    )
    with op.batch_alter_table("imap_mailbox_sync_states", schema=None) as batch_op:
        batch_op.drop_column("history_strategy_version")
        batch_op.drop_column("history_matched_count")
        batch_op.drop_column("history_scanned_count")
        batch_op.drop_column("history_last_error")
        batch_op.drop_column("history_scan_completed_at")
        batch_op.drop_column("history_scan_started_at")
        batch_op.drop_column("history_next_before_uid")
        batch_op.drop_column("history_high_water_uid")
        batch_op.drop_column("history_scan_status")
