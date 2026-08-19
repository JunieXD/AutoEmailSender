"""remove schema fields retained by the abandoned public beta runtime

Revision ID: 20260819_cleanup_public_beta
Revises: 20260817_crawl_enrichment_skip
Create Date: 2026-08-19 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260819_cleanup_public_beta"
down_revision: str | Sequence[str] | None = "20260817_crawl_enrichment_skip"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_BETA_DELIVERY_ATTEMPT_COLUMNS = (
    "owner_role",
    "runtime_id",
    "owner_generation",
    "owner_pid",
    "outcome",
    "finalized_at",
    "smtp_accepted_at",
    "prepared_rfc_message_id",
    "subject",
    "content",
    "content_html",
    "attachment_count",
    "provider_payload",
    "error_summary",
)
_BETA_EMAIL_TASK_COLUMNS = (
    "delivery_attempt_id",
    "delivery_outcome",
    "delivery_outcome_at",
)


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _column_names(table_name: str) -> set[str]:
    return {
        str(column["name"])
        for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def _index_names(table_name: str) -> set[str]:
    return {
        str(index["name"])
        for index in sa.inspect(op.get_bind()).get_indexes(table_name)
        if index.get("name")
    }


def upgrade() -> None:
    tables = _table_names()
    if "email_delivery_attempts" in tables:
        indexes = _index_names("email_delivery_attempts")
        if "ix_email_delivery_attempts_outcome_finalized" in indexes:
            op.drop_index(
                "ix_email_delivery_attempts_outcome_finalized",
                table_name="email_delivery_attempts",
            )
        columns = _column_names("email_delivery_attempts")
        columns_to_drop = [
            column_name
            for column_name in _BETA_DELIVERY_ATTEMPT_COLUMNS
            if column_name in columns
        ]
        if columns_to_drop:
            with op.batch_alter_table("email_delivery_attempts") as batch_op:
                for column_name in columns_to_drop:
                    batch_op.drop_column(column_name)

    if "email_tasks" in tables:
        indexes = _index_names("email_tasks")
        if "ix_email_tasks_delivery_sending_attempt" in indexes:
            op.drop_index(
                "ix_email_tasks_delivery_sending_attempt",
                table_name="email_tasks",
            )
        columns = _column_names("email_tasks")
        columns_to_drop = [
            column_name
            for column_name in _BETA_EMAIL_TASK_COLUMNS
            if column_name in columns
        ]
        if columns_to_drop:
            with op.batch_alter_table("email_tasks") as batch_op:
                for column_name in columns_to_drop:
                    batch_op.drop_column(column_name)


def downgrade() -> None:
    """The formal release never uses the abandoned beta-only fields."""
