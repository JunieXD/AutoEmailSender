"""add runtime delivery fields

Revision ID: 7a1d5e42c9bd
Revises: c52f8b7d1f43
Create Date: 2026-04-18 18:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7a1d5e42c9bd"
down_revision: Union[str, Sequence[str], None] = "c52f8b7d1f43"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "mail_delivery_mode",
            sa.String(length=20),
            server_default=sa.text("'dry_run'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_app_settings")),
    )
    op.bulk_insert(
        sa.table(
            "app_settings",
            sa.column("id", sa.Integer),
            sa.column("mail_delivery_mode", sa.String),
        ),
        [{"id": 1, "mail_delivery_mode": "dry_run"}],
    )

    with op.batch_alter_table("email_tasks", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("delivery_mode", sa.String(length=20), nullable=True)
        )
        batch_op.add_column(sa.Column("fit_points", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("risk_points", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("match_keywords", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("approved_subject", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("approved_body_text", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("approved_body_html", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("last_send_attempt_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("last_rfc_message_id", sa.String(length=255), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False
            ),
        )

    with op.batch_alter_table("email_logs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("delivery_mode", sa.String(length=20), nullable=True)
        )
        batch_op.add_column(sa.Column("content_html", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("rfc_message_id", sa.String(length=255), nullable=True)
        )
        batch_op.add_column(sa.Column("provider_payload", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("failure_summary", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("reply_headers", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("email_logs", schema=None) as batch_op:
        batch_op.drop_column("reply_headers")
        batch_op.drop_column("failure_summary")
        batch_op.drop_column("provider_payload")
        batch_op.drop_column("rfc_message_id")
        batch_op.drop_column("content_html")
        batch_op.drop_column("delivery_mode")

    with op.batch_alter_table("email_tasks", schema=None) as batch_op:
        batch_op.drop_column("retry_count")
        batch_op.drop_column("last_rfc_message_id")
        batch_op.drop_column("last_send_attempt_at")
        batch_op.drop_column("approved_body_html")
        batch_op.drop_column("approved_body_text")
        batch_op.drop_column("approved_subject")
        batch_op.drop_column("match_keywords")
        batch_op.drop_column("risk_points")
        batch_op.drop_column("fit_points")
        batch_op.drop_column("delivery_mode")

    op.drop_table("app_settings")
