"""add workflow tables

Revision ID: 9f8d3a1a2b10
Revises: 04d66ff4c25b
Create Date: 2026-04-18 14:20:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9f8d3a1a2b10"
down_revision: Union[str, Sequence[str], None] = "04d66ff4c25b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "batch_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("identity_id", sa.Integer(), nullable=False),
        sa.Column("llm_profile_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "schedule_type",
            sa.String(length=20),
            server_default=sa.text("'immediate'"),
            nullable=False,
        ),
        sa.Column("window_start_time", sa.String(length=5), nullable=True),
        sa.Column("window_end_time", sa.String(length=5), nullable=True),
        sa.Column("emails_per_window", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'running'"),
            nullable=False,
        ),
        sa.Column("email_subject", sa.Text(), nullable=True),
        sa.Column("email_body", sa.Text(), nullable=True),
        sa.Column("selected_attachment_ids", sa.JSON(), nullable=True),
        sa.Column(
            "target_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"],
            ["identity_profiles.id"],
            name=op.f("fk_batch_tasks_identity_id_identity_profiles"),
        ),
        sa.ForeignKeyConstraint(
            ["llm_profile_id"],
            ["llm_profiles.id"],
            name=op.f("fk_batch_tasks_llm_profile_id_llm_profiles"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_batch_tasks")),
    )
    op.create_index(
        op.f("ix_batch_tasks_identity_id"), "batch_tasks", ["identity_id"], unique=False
    )
    op.create_index(
        op.f("ix_batch_tasks_llm_profile_id"),
        "batch_tasks",
        ["llm_profile_id"],
        unique=False,
    )

    with op.batch_alter_table("email_tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("batch_task_id", sa.Integer(), nullable=True))
        batch_op.create_index(
            op.f("ix_email_tasks_batch_task_id"), ["batch_task_id"], unique=False
        )
        batch_op.create_foreign_key(
            op.f("fk_email_tasks_batch_task_id_batch_tasks"),
            "batch_tasks",
            ["batch_task_id"],
            ["id"],
        )

    op.create_table(
        "email_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email_task_id", sa.Integer(), nullable=True),
        sa.Column("identity_id", sa.Integer(), nullable=False),
        sa.Column("llm_profile_id", sa.Integer(), nullable=False),
        sa.Column("professor_id", sa.Integer(), nullable=False),
        sa.Column("direction", sa.String(length=20), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["email_task_id"],
            ["email_tasks.id"],
            name=op.f("fk_email_logs_email_task_id_email_tasks"),
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"],
            ["identity_profiles.id"],
            name=op.f("fk_email_logs_identity_id_identity_profiles"),
        ),
        sa.ForeignKeyConstraint(
            ["llm_profile_id"],
            ["llm_profiles.id"],
            name=op.f("fk_email_logs_llm_profile_id_llm_profiles"),
        ),
        sa.ForeignKeyConstraint(
            ["professor_id"],
            ["professors.id"],
            name=op.f("fk_email_logs_professor_id_professors"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_logs")),
    )
    op.create_index(
        op.f("ix_email_logs_email_task_id"),
        "email_logs",
        ["email_task_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_email_logs_identity_id"), "email_logs", ["identity_id"], unique=False
    )
    op.create_index(
        op.f("ix_email_logs_llm_profile_id"),
        "email_logs",
        ["llm_profile_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_email_logs_professor_id"), "email_logs", ["professor_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_email_logs_professor_id"), table_name="email_logs")
    op.drop_index(op.f("ix_email_logs_llm_profile_id"), table_name="email_logs")
    op.drop_index(op.f("ix_email_logs_identity_id"), table_name="email_logs")
    op.drop_index(op.f("ix_email_logs_email_task_id"), table_name="email_logs")
    op.drop_table("email_logs")

    with op.batch_alter_table("email_tasks", schema=None) as batch_op:
        batch_op.drop_constraint(
            op.f("fk_email_tasks_batch_task_id_batch_tasks"), type_="foreignkey"
        )
        batch_op.drop_index(op.f("ix_email_tasks_batch_task_id"))
        batch_op.drop_column("batch_task_id")

    op.drop_index(op.f("ix_batch_tasks_llm_profile_id"), table_name="batch_tasks")
    op.drop_index(op.f("ix_batch_tasks_identity_id"), table_name="batch_tasks")
    op.drop_table("batch_tasks")
