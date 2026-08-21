"""add professor-triggered recent history queue state

Revision ID: 20260719_professor_history_queue
Revises: 20260716_llm_endpoint_adaptation
Create Date: 2026-07-19 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260719_professor_history_queue"
down_revision: Union[str, Sequence[str], None] = "20260716_llm_endpoint_adaptation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    professor_columns = _column_names("professors")
    if "communication_sync_version" not in professor_columns:
        op.add_column(
            "professors",
            sa.Column(
                "communication_sync_version",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
        )

    mailbox_columns = _column_names("imap_mailbox_sync_states")
    if "history_batch_id" not in mailbox_columns:
        op.add_column(
            "imap_mailbox_sync_states",
            sa.Column("history_batch_id", sa.String(length=64), nullable=True),
        )

    professor_state_columns = _column_names("imap_professor_sync_states")
    additions = (
        (
            "history_start_date",
            sa.Column("history_start_date", sa.Date(), nullable=True),
        ),
        (
            "trigger_reason",
            sa.Column("trigger_reason", sa.String(length=64), nullable=True),
        ),
        ("batch_id", sa.Column("batch_id", sa.String(length=64), nullable=True)),
        ("available_at", sa.Column("available_at", sa.DateTime(), nullable=True)),
        (
            "priority",
            sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        ),
        (
            "professor_sync_version",
            sa.Column(
                "professor_sync_version",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        ),
    )
    for name, column in additions:
        if name not in professor_state_columns:
            op.add_column("imap_professor_sync_states", column)

    op.create_index(
        "ix_imap_professor_sync_recent_due",
        "imap_professor_sync_states",
        [
            "identity_id",
            "history_strategy_version",
            "historical_scan_status",
            "available_at",
            "priority",
            "id",
        ],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_imap_professor_sync_recent_due",
        table_name="imap_professor_sync_states",
        if_exists=True,
    )
    professor_state_columns = _column_names("imap_professor_sync_states")
    for name in (
        "professor_sync_version",
        "priority",
        "available_at",
        "batch_id",
        "trigger_reason",
        "history_start_date",
    ):
        if name in professor_state_columns:
            op.drop_column("imap_professor_sync_states", name)
            professor_state_columns.remove(name)

    if "history_batch_id" in _column_names("imap_mailbox_sync_states"):
        op.drop_column("imap_mailbox_sync_states", "history_batch_id")
    if "communication_sync_version" in _column_names("professors"):
        op.drop_column("professors", "communication_sync_version")


def _column_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}
