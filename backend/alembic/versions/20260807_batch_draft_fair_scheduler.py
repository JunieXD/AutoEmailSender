"""add fair batch draft scheduler leases

Revision ID: 20260807_batch_draft_fair
Revises: 20260807_drop_crawler_runtime
Create Date: 2026-08-07 12:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260807_batch_draft_fair"
down_revision: Union[str, Sequence[str], None] = "20260807_drop_crawler_runtime"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    batch_columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("batch_tasks")
    }
    batch_indexes = {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes("batch_tasks")
    }
    if (
        "draft_last_dispatched_at" not in batch_columns
        or "ix_batch_tasks_draft_last_dispatched_at" not in batch_indexes
    ):
        with op.batch_alter_table("batch_tasks", schema=None) as batch_op:
            if "draft_last_dispatched_at" not in batch_columns:
                batch_op.add_column(
                    sa.Column("draft_last_dispatched_at", sa.DateTime(), nullable=True)
                )
            if "ix_batch_tasks_draft_last_dispatched_at" not in batch_indexes:
                batch_op.create_index(
                    batch_op.f("ix_batch_tasks_draft_last_dispatched_at"),
                    ["draft_last_dispatched_at"],
                    unique=False,
                )

    email_columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("email_tasks")
    }
    email_indexes = {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes("email_tasks")
    }
    required_email_columns = {
        "draft_claim_id": sa.Column(
            "draft_claim_id", sa.String(length=36), nullable=True
        ),
        "draft_claimed_at": sa.Column("draft_claimed_at", sa.DateTime(), nullable=True),
        "draft_lease_expires_at": sa.Column(
            "draft_lease_expires_at", sa.DateTime(), nullable=True
        ),
    }
    if (
        not set(required_email_columns).issubset(email_columns)
        or "ix_email_tasks_batch_draft_lease_recovery" not in email_indexes
    ):
        with op.batch_alter_table("email_tasks", schema=None) as batch_op:
            for column_name, column in required_email_columns.items():
                if column_name not in email_columns:
                    batch_op.add_column(column)
            if "ix_email_tasks_batch_draft_lease_recovery" not in email_indexes:
                batch_op.create_index(
                    "ix_email_tasks_batch_draft_lease_recovery",
                    ["draft_lease_expires_at"],
                    unique=False,
                    sqlite_where=sa.text(
                        "status = 'generating_draft' AND draft_claim_id IS NOT NULL"
                    ),
                    postgresql_where=sa.text(
                        "status = 'generating_draft' AND draft_claim_id IS NOT NULL"
                    ),
                )


def downgrade() -> None:
    email_columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("email_tasks")
    }
    email_indexes = {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes("email_tasks")
    }
    with op.batch_alter_table("email_tasks", schema=None) as batch_op:
        if "ix_email_tasks_batch_draft_lease_recovery" in email_indexes:
            batch_op.drop_index("ix_email_tasks_batch_draft_lease_recovery")
        for column_name in (
            "draft_lease_expires_at",
            "draft_claimed_at",
            "draft_claim_id",
        ):
            if column_name in email_columns:
                batch_op.drop_column(column_name)

    batch_columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("batch_tasks")
    }
    batch_indexes = {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes("batch_tasks")
    }
    with op.batch_alter_table("batch_tasks", schema=None) as batch_op:
        if "ix_batch_tasks_draft_last_dispatched_at" in batch_indexes:
            batch_op.drop_index(batch_op.f("ix_batch_tasks_draft_last_dispatched_at"))
        if "draft_last_dispatched_at" in batch_columns:
            batch_op.drop_column("draft_last_dispatched_at")
