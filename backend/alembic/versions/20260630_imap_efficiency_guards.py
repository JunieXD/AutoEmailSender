"""imap efficiency guards

Revision ID: 20260630_imap_efficiency_guards
Revises: 20260630_unified_email_history_sync
Create Date: 2026-06-30 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260630_imap_efficiency_guards"
down_revision: Union[str, Sequence[str], None] = "20260630_unified_email_history_sync"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("imap_mailbox_sync_states", schema=None) as batch_op:
        batch_op.add_column(sa.Column("discovered_sent_folder", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("sent_folder_discovered_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(
            sa.Column("sent_folder_discovery_failed_at", sa.DateTime(timezone=True), nullable=True),
        )
        batch_op.add_column(sa.Column("sent_folder_discovery_error", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("throttle_paused_until", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("throttle_reason", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("last_professor_state_ensure_at", sa.DateTime(timezone=True), nullable=True),
        )
        batch_op.add_column(sa.Column("professor_state_fingerprint", sa.String(length=255), nullable=True))

    op.create_index(
        "ix_imap_professor_sync_identity_status_updated",
        "imap_professor_sync_states",
        ["identity_id", "historical_scan_status", "updated_at", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_imap_professor_sync_identity_status_updated",
        table_name="imap_professor_sync_states",
    )
    with op.batch_alter_table("imap_mailbox_sync_states", schema=None) as batch_op:
        batch_op.drop_column("professor_state_fingerprint")
        batch_op.drop_column("last_professor_state_ensure_at")
        batch_op.drop_column("throttle_reason")
        batch_op.drop_column("throttle_paused_until")
        batch_op.drop_column("sent_folder_discovery_error")
        batch_op.drop_column("sent_folder_discovery_failed_at")
        batch_op.drop_column("sent_folder_discovered_at")
        batch_op.drop_column("discovered_sent_folder")
