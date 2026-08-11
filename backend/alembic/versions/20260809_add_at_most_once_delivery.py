"""add at-most-once delivery attempts

Revision ID: 20260809_delivery_at_most_once
Revises: 20260808_crawl_llm_snapshot
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260809_delivery_at_most_once"
down_revision: str | Sequence[str] | None = "20260808_crawl_llm_snapshot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def _indexes(table_name: str) -> set[str]:
    return {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes(table_name)
    }


def upgrade() -> None:
    if "email_delivery_attempts" not in _table_names():
        op.create_table(
            "email_delivery_attempts",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("email_task_id", sa.Integer(), nullable=True),
            sa.Column("identity_id", sa.Integer(), nullable=False),
            sa.Column("professor_id", sa.Integer(), nullable=False),
            sa.Column("attempt_number", sa.Integer(), nullable=False),
            sa.Column("recipient_email", sa.String(length=255), nullable=False),
            sa.Column("subject_fingerprint", sa.String(length=71), nullable=False),
            sa.Column("content_fingerprint", sa.String(length=71), nullable=False),
            sa.Column("app_message_id", sa.String(length=255), nullable=True),
            sa.Column("normalized_app_message_id", sa.String(length=255), nullable=True),
            sa.Column(
                "status",
                sa.String(length=20),
                server_default=sa.text("'prepared'"),
                nullable=False,
            ),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            ),
            sa.Column("owner_role", sa.String(length=16), server_default="legacy", nullable=False),
            sa.Column("runtime_id", sa.String(length=128), server_default="legacy", nullable=False),
            sa.Column(
                "owner_generation",
                sa.String(length=128),
                server_default="pre-split",
                nullable=False,
            ),
            sa.Column("owner_pid", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column(
                "outcome",
                sa.String(length=48),
                server_default=sa.text("'claimed'"),
                nullable=False,
            ),
            sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("smtp_accepted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("prepared_rfc_message_id", sa.String(length=255), nullable=True),
            sa.Column("subject", sa.Text(), server_default="", nullable=False),
            sa.Column("content", sa.Text(), server_default="", nullable=False),
            sa.Column("content_html", sa.Text(), nullable=True),
            sa.Column(
                "attachment_count",
                sa.Integer(),
                server_default=sa.text("0"),
                nullable=False,
            ),
            sa.Column("provider_payload", sa.JSON(), nullable=True),
            sa.Column("error_summary", sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(
                ["email_task_id"],
                ["email_tasks.id"],
                name="fk_email_delivery_attempts_email_task_id_email_tasks",
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["identity_id"],
                ["identity_profiles.id"],
                name="fk_email_delivery_attempts_identity_id_identity_profiles",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["professor_id"],
                ["professors.id"],
                name="fk_email_delivery_attempts_professor_id_professors",
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name="pk_email_delivery_attempts"),
            sa.UniqueConstraint(
                "email_task_id",
                "attempt_number",
                name="uq_email_delivery_attempts_task_number",
            ),
        )
        op.create_index(
            "ix_email_delivery_attempts_email_task_id",
            "email_delivery_attempts",
            ["email_task_id"],
            unique=False,
        )
        op.create_index(
            "ix_email_delivery_attempts_identity_id",
            "email_delivery_attempts",
            ["identity_id"],
        )
        op.create_index(
            "ix_email_delivery_attempts_professor_id",
            "email_delivery_attempts",
            ["professor_id"],
        )
        op.create_index(
            "ix_email_delivery_attempts_identity_professor_started",
            "email_delivery_attempts",
            ["identity_id", "professor_id", "started_at", "id"],
        )
        op.create_index(
            "ix_email_delivery_attempts_message_id",
            "email_delivery_attempts",
            ["identity_id", "normalized_app_message_id"],
        )
        op.create_index(
            "ix_email_delivery_attempts_outcome_finalized",
            "email_delivery_attempts",
            ["outcome", "finalized_at"],
            unique=False,
        )

    task_columns = _columns("email_tasks")
    missing_task_columns = {
        "delivery_attempt_id",
        "delivery_outcome",
        "delivery_outcome_at",
    } - task_columns
    if missing_task_columns:
        with op.batch_alter_table("email_tasks", schema=None) as batch_op:
            if "delivery_attempt_id" in missing_task_columns:
                batch_op.add_column(
                    sa.Column("delivery_attempt_id", sa.String(length=36), nullable=True)
                )
            if "delivery_outcome" in missing_task_columns:
                batch_op.add_column(
                    sa.Column("delivery_outcome", sa.String(length=48), nullable=True)
                )
            if "delivery_outcome_at" in missing_task_columns:
                batch_op.add_column(
                    sa.Column("delivery_outcome_at", sa.DateTime(timezone=True), nullable=True)
                )

    if "delivery_attempt_id" not in _columns("email_logs"):
        with op.batch_alter_table("email_logs", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("delivery_attempt_id", sa.String(length=36), nullable=True)
            )
            batch_op.create_foreign_key(
                "fk_email_logs_delivery_attempt_id",
                "email_delivery_attempts",
                ["delivery_attempt_id"],
                ["id"],
                ondelete="SET NULL",
            )

    if "ix_email_tasks_delivery_sending_attempt" not in _indexes("email_tasks"):
        op.create_index(
            "ix_email_tasks_delivery_sending_attempt",
            "email_tasks",
            ["delivery_attempt_id"],
            unique=False,
            sqlite_where=sa.text("status = 'sending'"),
            postgresql_where=sa.text("status = 'sending'"),
        )
    if "uq_email_logs_delivery_attempt_id" not in _indexes("email_logs"):
        op.create_index(
            "uq_email_logs_delivery_attempt_id",
            "email_logs",
            ["delivery_attempt_id"],
            unique=True,
            sqlite_where=sa.text("delivery_attempt_id IS NOT NULL"),
            postgresql_where=sa.text("delivery_attempt_id IS NOT NULL"),
        )

    # A pre-upgrade `sending` row has already crossed the irreversible boundary.
    # Conservatively mark it sent instead of making it dispatchable again.
    op.execute(
        sa.text(
            """
            INSERT INTO email_delivery_attempts (
                id, email_task_id, identity_id, professor_id, attempt_number,
                recipient_email, subject_fingerprint, content_fingerprint,
                status, started_at, created_at,
                owner_role, runtime_id, owner_generation, owner_pid,
                outcome, finalized_at,
                prepared_rfc_message_id, subject, content, content_html,
                attachment_count, error_summary
            )
            SELECT
                'legacy-' || CAST(email_tasks.id AS VARCHAR), email_tasks.id,
                email_tasks.identity_id, email_tasks.professor_id, 1,
                COALESCE(professors.email, ''),
                'legacy:subject:' || CAST(email_tasks.id AS VARCHAR),
                'legacy:content:' || CAST(email_tasks.id AS VARCHAR),
                'unknown',
                COALESCE(
                    email_tasks.last_send_attempt_at,
                    email_tasks.updated_at,
                    CURRENT_TIMESTAMP
                ),
                COALESCE(
                    email_tasks.last_send_attempt_at,
                    email_tasks.updated_at,
                    CURRENT_TIMESTAMP
                ),
                'legacy', 'legacy', 'pre-at-most-once', 0,
                'assumed_sent_after_interruption',
                CURRENT_TIMESTAMP, email_tasks.last_rfc_message_id,
                COALESCE(email_tasks.approved_subject, email_tasks.generated_subject, ''),
                COALESCE(
                    email_tasks.approved_body_text,
                    email_tasks.generated_content_text,
                    ''
                ),
                COALESCE(
                    email_tasks.approved_body_html,
                    email_tasks.generated_content_html
                ),
                0, 'Recovered conservatively during at-most-once migration'
            FROM email_tasks
            LEFT JOIN professors ON professors.id = email_tasks.professor_id
            WHERE email_tasks.status = 'sending'
              AND email_tasks.delivery_attempt_id IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE email_tasks
            SET status = 'sent',
                sent_at = COALESCE(sent_at, last_send_attempt_at, updated_at, CURRENT_TIMESTAMP),
                delivery_attempt_id = 'legacy-' || CAST(id AS VARCHAR),
                delivery_outcome = 'assumed_sent_after_interruption',
                delivery_outcome_at = CURRENT_TIMESTAMP,
                last_error = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE status = 'sending'
              AND delivery_attempt_id IS NULL
            """
        )
    )


def downgrade() -> None:
    if "uq_email_logs_delivery_attempt_id" in _indexes("email_logs"):
        op.drop_index("uq_email_logs_delivery_attempt_id", table_name="email_logs")
    if "ix_email_tasks_delivery_sending_attempt" in _indexes("email_tasks"):
        op.drop_index(
            "ix_email_tasks_delivery_sending_attempt",
            table_name="email_tasks",
        )

    if "delivery_attempt_id" in _columns("email_logs"):
        with op.batch_alter_table("email_logs", schema=None) as batch_op:
            batch_op.drop_column("delivery_attempt_id")

    task_columns = _columns("email_tasks")
    if {
        "delivery_attempt_id",
        "delivery_outcome",
        "delivery_outcome_at",
    }.intersection(task_columns):
        with op.batch_alter_table("email_tasks", schema=None) as batch_op:
            for column_name in (
                "delivery_outcome_at",
                "delivery_outcome",
                "delivery_attempt_id",
            ):
                if column_name in task_columns:
                    batch_op.drop_column(column_name)

    if "email_delivery_attempts" in _table_names():
        op.drop_table("email_delivery_attempts")
