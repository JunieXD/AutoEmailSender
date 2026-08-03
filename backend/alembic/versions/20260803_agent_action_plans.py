"""add persistent Agent action plans

Revision ID: 20260803_agent_action_plans
Revises: 20260803_merge_community_batch
Create Date: 2026-08-03 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260803_agent_action_plans"
down_revision: Union[str, Sequence[str], None] = "20260803_merge_community_batch"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "agent_action_plans" in _table_names():
        return

    op.create_table(
        "agent_action_plans",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'awaiting_confirmation'"),
            nullable=False,
        ),
        sa.Column("email_task_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=True),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("content_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("execution_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
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
            ["email_task_id"],
            ["email_tasks.id"],
            name=op.f("fk_agent_action_plans_email_task_id_email_tasks"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_action_plans")),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_agent_action_plans_idempotency_key",
        ),
    )
    op.create_index(
        op.f("ix_agent_action_plans_email_task_id"),
        "agent_action_plans",
        ["email_task_id"],
        unique=False,
    )
    op.create_index(
        "ix_agent_action_plans_status_expires_at",
        "agent_action_plans",
        ["status", "expires_at"],
        unique=False,
    )


def downgrade() -> None:
    if "agent_action_plans" not in _table_names():
        return
    op.drop_index(
        "ix_agent_action_plans_status_expires_at",
        table_name="agent_action_plans",
    )
    op.drop_index(
        op.f("ix_agent_action_plans_email_task_id"),
        table_name="agent_action_plans",
    )
    op.drop_table("agent_action_plans")
