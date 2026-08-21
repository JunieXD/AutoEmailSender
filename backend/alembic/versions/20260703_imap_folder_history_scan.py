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
    existing_columns = _mailbox_columns()
    history_columns = {
        "history_scan_status",
        "history_high_water_uid",
        "history_next_before_uid",
        "history_scan_started_at",
        "history_scan_completed_at",
        "history_last_error",
        "history_scanned_count",
        "history_matched_count",
        "history_strategy_version",
    }
    if history_columns - existing_columns:
        with op.batch_alter_table("imap_mailbox_sync_states", schema=None) as batch_op:
            if "history_scan_status" not in existing_columns:
                batch_op.add_column(
                    sa.Column(
                        "history_scan_status",
                        sa.String(length=32),
                        server_default="pending",
                        nullable=False,
                    ),
                )
            if "history_high_water_uid" not in existing_columns:
                batch_op.add_column(
                    sa.Column("history_high_water_uid", sa.Integer(), nullable=True)
                )
            if "history_next_before_uid" not in existing_columns:
                batch_op.add_column(
                    sa.Column("history_next_before_uid", sa.Integer(), nullable=True)
                )
            if "history_scan_started_at" not in existing_columns:
                batch_op.add_column(
                    sa.Column(
                        "history_scan_started_at",
                        sa.DateTime(timezone=True),
                        nullable=True,
                    ),
                )
            if "history_scan_completed_at" not in existing_columns:
                batch_op.add_column(
                    sa.Column(
                        "history_scan_completed_at",
                        sa.DateTime(timezone=True),
                        nullable=True,
                    ),
                )
            if "history_last_error" not in existing_columns:
                batch_op.add_column(
                    sa.Column("history_last_error", sa.Text(), nullable=True)
                )
            if "history_scanned_count" not in existing_columns:
                batch_op.add_column(
                    sa.Column(
                        "history_scanned_count",
                        sa.Integer(),
                        server_default="0",
                        nullable=False,
                    ),
                )
            if "history_matched_count" not in existing_columns:
                batch_op.add_column(
                    sa.Column(
                        "history_matched_count",
                        sa.Integer(),
                        server_default="0",
                        nullable=False,
                    ),
                )
            if "history_strategy_version" not in existing_columns:
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
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_imap_mailbox_sync_identity_history_status_updated",
        table_name="imap_mailbox_sync_states",
        if_exists=True,
    )
    existing_columns = _mailbox_columns()
    history_columns = {
        "history_strategy_version",
        "history_matched_count",
        "history_scanned_count",
        "history_last_error",
        "history_scan_completed_at",
        "history_scan_started_at",
        "history_next_before_uid",
        "history_high_water_uid",
        "history_scan_status",
    }
    if history_columns & existing_columns:
        with op.batch_alter_table("imap_mailbox_sync_states", schema=None) as batch_op:
            if "history_strategy_version" in existing_columns:
                batch_op.drop_column("history_strategy_version")
            if "history_matched_count" in existing_columns:
                batch_op.drop_column("history_matched_count")
            if "history_scanned_count" in existing_columns:
                batch_op.drop_column("history_scanned_count")
            if "history_last_error" in existing_columns:
                batch_op.drop_column("history_last_error")
            if "history_scan_completed_at" in existing_columns:
                batch_op.drop_column("history_scan_completed_at")
            if "history_scan_started_at" in existing_columns:
                batch_op.drop_column("history_scan_started_at")
            if "history_next_before_uid" in existing_columns:
                batch_op.drop_column("history_next_before_uid")
            if "history_high_water_uid" in existing_columns:
                batch_op.drop_column("history_high_water_uid")
            if "history_scan_status" in existing_columns:
                batch_op.drop_column("history_scan_status")


def _mailbox_columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {
        column["name"] for column in inspector.get_columns("imap_mailbox_sync_states")
    }
