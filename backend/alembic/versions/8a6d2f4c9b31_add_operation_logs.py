"""add operation logs

Revision ID: 8a6d2f4c9b31
Revises: 7b9c2d4e6f10
Create Date: 2026-04-26 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8a6d2f4c9b31"
down_revision: Union[str, Sequence[str], None] = "7b9c2d4e6f10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "operation_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("event_name", sa.String(length=120), nullable=False),
        sa.Column(
            "level",
            sa.String(length=20),
            server_default=sa.text("'info'"),
            nullable=False,
        ),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("entity_type", sa.String(length=80), nullable=True),
        sa.Column("entity_id", sa.String(length=80), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_operation_logs")),
    )
    op.create_index(
        op.f("ix_operation_logs_request_id"),
        "operation_logs",
        ["request_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_operation_logs_category"), "operation_logs", ["category"], unique=False
    )
    op.create_index(
        op.f("ix_operation_logs_event_name"),
        "operation_logs",
        ["event_name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_operation_logs_entity_type"),
        "operation_logs",
        ["entity_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_operation_logs_entity_id"),
        "operation_logs",
        ["entity_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_operation_logs_created_at"),
        "operation_logs",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_operation_logs_created_at"), table_name="operation_logs")
    op.drop_index(op.f("ix_operation_logs_entity_id"), table_name="operation_logs")
    op.drop_index(op.f("ix_operation_logs_entity_type"), table_name="operation_logs")
    op.drop_index(op.f("ix_operation_logs_event_name"), table_name="operation_logs")
    op.drop_index(op.f("ix_operation_logs_category"), table_name="operation_logs")
    op.drop_index(op.f("ix_operation_logs_request_id"), table_name="operation_logs")
    op.drop_table("operation_logs")
