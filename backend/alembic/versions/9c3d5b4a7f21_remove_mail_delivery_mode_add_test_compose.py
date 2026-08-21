"""remove mail delivery mode and add test compose tables

Revision ID: 9c3d5b4a7f21
Revises: d91f2a4b8c7e
Create Date: 2026-04-23 15:40:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9c3d5b4a7f21"
down_revision: Union[str, Sequence[str], None] = "d91f2a4b8c7e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings", schema=None) as batch_op:
        batch_op.drop_column("mail_delivery_mode")

    with op.batch_alter_table("email_tasks", schema=None) as batch_op:
        batch_op.drop_column("delivery_mode")

    with op.batch_alter_table("email_logs", schema=None) as batch_op:
        batch_op.drop_column("delivery_mode")

    op.create_table(
        "test_compose_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("identity_id", sa.Integer(), nullable=False),
        sa.Column("llm_profile_id", sa.Integer(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("body_text", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=True),
        sa.Column("selected_material_ids", sa.JSON(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["identity_id"],
            ["identity_profiles.id"],
            name=op.f("fk_test_compose_sessions_identity_id_identity_profiles"),
        ),
        sa.ForeignKeyConstraint(
            ["llm_profile_id"],
            ["llm_profiles.id"],
            name=op.f("fk_test_compose_sessions_llm_profile_id_llm_profiles"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_compose_sessions")),
    )
    op.create_index(
        op.f("ix_test_compose_sessions_identity_id"),
        "test_compose_sessions",
        ["identity_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_test_compose_sessions_llm_profile_id"),
        "test_compose_sessions",
        ["llm_profile_id"],
        unique=False,
    )

    op.create_table(
        "test_compose_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("identity_id", sa.Integer(), nullable=False),
        sa.Column("llm_profile_id", sa.Integer(), nullable=False),
        sa.Column("recipient_email", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_html", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'sent'"),
            nullable=False,
        ),
        sa.Column("rfc_message_id", sa.String(length=255), nullable=True),
        sa.Column("provider_payload", sa.JSON(), nullable=True),
        sa.Column("failure_summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"],
            ["identity_profiles.id"],
            name=op.f("fk_test_compose_messages_identity_id_identity_profiles"),
        ),
        sa.ForeignKeyConstraint(
            ["llm_profile_id"],
            ["llm_profiles.id"],
            name=op.f("fk_test_compose_messages_llm_profile_id_llm_profiles"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["test_compose_sessions.id"],
            name=op.f("fk_test_compose_messages_session_id_test_compose_sessions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_compose_messages")),
    )
    op.create_index(
        op.f("ix_test_compose_messages_identity_id"),
        "test_compose_messages",
        ["identity_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_test_compose_messages_llm_profile_id"),
        "test_compose_messages",
        ["llm_profile_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_test_compose_messages_session_id"),
        "test_compose_messages",
        ["session_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_test_compose_messages_session_id"), table_name="test_compose_messages"
    )
    op.drop_index(
        op.f("ix_test_compose_messages_llm_profile_id"),
        table_name="test_compose_messages",
    )
    op.drop_index(
        op.f("ix_test_compose_messages_identity_id"), table_name="test_compose_messages"
    )
    op.drop_table("test_compose_messages")

    op.drop_index(
        op.f("ix_test_compose_sessions_llm_profile_id"),
        table_name="test_compose_sessions",
    )
    op.drop_index(
        op.f("ix_test_compose_sessions_identity_id"), table_name="test_compose_sessions"
    )
    op.drop_table("test_compose_sessions")

    with op.batch_alter_table("email_logs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("delivery_mode", sa.String(length=20), nullable=True)
        )

    with op.batch_alter_table("email_tasks", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("delivery_mode", sa.String(length=20), nullable=True)
        )

    with op.batch_alter_table("app_settings", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "mail_delivery_mode",
                sa.String(length=20),
                server_default=sa.text("'dry_run'"),
                nullable=False,
            ),
        )
