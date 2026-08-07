"""add fair scheduler leases for match analysis and IMAP

Revision ID: 20260807_scheduler_leases
Revises: 20260807_batch_draft_fair
Create Date: 2026-08-07 18:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260807_scheduler_leases"
down_revision: Union[str, Sequence[str], None] = "20260807_batch_draft_fair"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table_name: str) -> set[str]:
    return {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def _indexes(table_name: str) -> set[str]:
    return {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)
    }


def upgrade() -> None:
    job_columns = _columns("match_analysis_jobs")
    job_indexes = _indexes("match_analysis_jobs")
    if "item_last_dispatched_at" not in job_columns:
        with op.batch_alter_table("match_analysis_jobs") as batch_op:
            batch_op.add_column(
                sa.Column("item_last_dispatched_at", sa.DateTime(), nullable=True)
            )
    if "ix_match_analysis_jobs_item_last_dispatched_at" not in job_indexes:
        with op.batch_alter_table("match_analysis_jobs") as batch_op:
            batch_op.create_index(
                batch_op.f("ix_match_analysis_jobs_item_last_dispatched_at"),
                ["item_last_dispatched_at"],
                unique=False,
            )

    item_columns = _columns("match_analysis_job_items")
    item_indexes = _indexes("match_analysis_job_items")
    with op.batch_alter_table("match_analysis_job_items") as batch_op:
        if "claim_id" not in item_columns:
            batch_op.add_column(sa.Column("claim_id", sa.String(36), nullable=True))
        if "claimed_at" not in item_columns:
            batch_op.add_column(sa.Column("claimed_at", sa.DateTime(), nullable=True))
        if "lease_expires_at" not in item_columns:
            batch_op.add_column(
                sa.Column("lease_expires_at", sa.DateTime(), nullable=True)
            )
        if "attempt_count" not in item_columns:
            batch_op.add_column(
                sa.Column(
                    "attempt_count",
                    sa.Integer(),
                    nullable=False,
                    server_default=sa.text("0"),
                )
            )
        if "ix_match_analysis_job_items_lease_recovery" not in item_indexes:
            batch_op.create_index(
                "ix_match_analysis_job_items_lease_recovery",
                ["lease_expires_at"],
                unique=False,
            )

    if not sa.inspect(op.get_bind()).has_table("imap_identity_sync_leases"):
        op.create_table(
            "imap_identity_sync_leases",
            sa.Column("identity_id", sa.Integer(), nullable=False),
            sa.Column("claim_id", sa.String(36), nullable=True),
            sa.Column("claim_kind", sa.String(32), nullable=True),
            sa.Column("claimed_at", sa.DateTime(), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.ForeignKeyConstraint(
                ["identity_id"],
                ["identity_profiles.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("identity_id"),
        )
        op.create_index(
            "ix_imap_identity_sync_lease_expires",
            "imap_identity_sync_leases",
            ["lease_expires_at"],
            unique=False,
        )

    _add_imap_work_lease_columns(
        "imap_mailbox_sync_states",
        "ix_imap_mailbox_history_lease_recovery",
    )
    _add_imap_work_lease_columns(
        "imap_professor_sync_states",
        "ix_imap_professor_history_lease_recovery",
    )


def _add_imap_work_lease_columns(table_name: str, index_name: str) -> None:
    columns = _columns(table_name)
    indexes = _indexes(table_name)
    with op.batch_alter_table(table_name) as batch_op:
        if "history_claim_id" not in columns:
            batch_op.add_column(
                sa.Column("history_claim_id", sa.String(36), nullable=True)
            )
        if "history_lease_expires_at" not in columns:
            batch_op.add_column(
                sa.Column("history_lease_expires_at", sa.DateTime(), nullable=True)
            )
        if index_name not in indexes:
            batch_op.create_index(
                index_name,
                ["history_lease_expires_at"],
                unique=False,
            )


def downgrade() -> None:
    for table_name, index_name in (
        ("imap_professor_sync_states", "ix_imap_professor_history_lease_recovery"),
        ("imap_mailbox_sync_states", "ix_imap_mailbox_history_lease_recovery"),
    ):
        columns = _columns(table_name)
        indexes = _indexes(table_name)
        with op.batch_alter_table(table_name) as batch_op:
            if index_name in indexes:
                batch_op.drop_index(index_name)
            for column_name in ("history_lease_expires_at", "history_claim_id"):
                if column_name in columns:
                    batch_op.drop_column(column_name)

    if sa.inspect(op.get_bind()).has_table("imap_identity_sync_leases"):
        op.drop_index(
            "ix_imap_identity_sync_lease_expires",
            table_name="imap_identity_sync_leases",
        )
        op.drop_table("imap_identity_sync_leases")

    item_columns = _columns("match_analysis_job_items")
    item_indexes = _indexes("match_analysis_job_items")
    with op.batch_alter_table("match_analysis_job_items") as batch_op:
        if "ix_match_analysis_job_items_lease_recovery" in item_indexes:
            batch_op.drop_index("ix_match_analysis_job_items_lease_recovery")
        for column_name in (
            "attempt_count",
            "lease_expires_at",
            "claimed_at",
            "claim_id",
        ):
            if column_name in item_columns:
                batch_op.drop_column(column_name)

    job_columns = _columns("match_analysis_jobs")
    job_indexes = _indexes("match_analysis_jobs")
    with op.batch_alter_table("match_analysis_jobs") as batch_op:
        if "ix_match_analysis_jobs_item_last_dispatched_at" in job_indexes:
            batch_op.drop_index(
                batch_op.f("ix_match_analysis_jobs_item_last_dispatched_at")
            )
        if "item_last_dispatched_at" in job_columns:
            batch_op.drop_column("item_last_dispatched_at")
