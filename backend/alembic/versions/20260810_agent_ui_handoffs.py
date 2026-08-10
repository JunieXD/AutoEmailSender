"""add Agent UI handoffs

Revision ID: 20260810_agent_ui_handoffs
Revises: 20260809_professor_scale_search
Create Date: 2026-08-10 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260810_agent_ui_handoffs"
down_revision: str | Sequence[str] | None = "20260809_professor_scale_search"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _table_names()
    if "agent_ui_handoffs" not in tables:
        op.create_table(
            "agent_ui_handoffs",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
            sa.Column("surface", sa.String(length=80), nullable=False),
            sa.Column("route", sa.String(length=240), nullable=False),
            sa.Column(
                "status",
                sa.String(length=32),
                server_default=sa.text("'pending'"),
                nullable=False,
            ),
            sa.Column("idempotency_key", sa.String(length=160), nullable=True),
            sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
            sa.Column("selection_fingerprint", sa.String(length=64), nullable=True),
            sa.Column("selection_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("result", sa.JSON(), nullable=True),
            sa.Column("failure_message", sa.Text(), nullable=True),
            sa.Column("consumer_id", sa.String(length=120), nullable=True),
            sa.Column("delivery_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("awaiting_user_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
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
            sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_ui_handoffs")),
            sa.UniqueConstraint(
                "idempotency_key",
                name="uq_agent_ui_handoffs_idempotency_key",
            ),
        )
        op.create_index(
            "ix_agent_ui_handoffs_status_expires_at",
            "agent_ui_handoffs",
            ["status", "expires_at"],
            unique=False,
        )
        op.create_index(
            "ix_agent_ui_handoffs_consumer_claim",
            "agent_ui_handoffs",
            ["consumer_id", "status", "claim_expires_at"],
            unique=False,
        )

    if "agent_ui_handoff_items" not in tables:
        op.create_table(
            "agent_ui_handoff_items",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("handoff_id", sa.String(length=64), nullable=False),
            sa.Column("resource_type", sa.String(length=40), nullable=False),
            sa.Column("resource_id", sa.String(length=120), nullable=False),
            sa.Column("ordinal", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(
                ["handoff_id"],
                ["agent_ui_handoffs.id"],
                name=op.f("fk_agent_ui_handoff_items_handoff_id_agent_ui_handoffs"),
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_ui_handoff_items")),
            sa.UniqueConstraint(
                "handoff_id",
                "resource_type",
                "resource_id",
                name="uq_agent_ui_handoff_items_resource",
            ),
        )
        op.create_index(
            op.f("ix_agent_ui_handoff_items_handoff_id"),
            "agent_ui_handoff_items",
            ["handoff_id"],
            unique=False,
        )
        op.create_index(
            "ix_agent_ui_handoff_items_resource",
            "agent_ui_handoff_items",
            ["resource_type", "resource_id"],
            unique=False,
        )


def downgrade() -> None:
    tables = _table_names()
    if "agent_ui_handoff_items" in tables:
        op.drop_index("ix_agent_ui_handoff_items_resource", table_name="agent_ui_handoff_items")
        op.drop_index(
            op.f("ix_agent_ui_handoff_items_handoff_id"),
            table_name="agent_ui_handoff_items",
        )
        op.drop_table("agent_ui_handoff_items")
    if "agent_ui_handoffs" in tables:
        op.drop_index("ix_agent_ui_handoffs_consumer_claim", table_name="agent_ui_handoffs")
        op.drop_index("ix_agent_ui_handoffs_status_expires_at", table_name="agent_ui_handoffs")
        op.drop_table("agent_ui_handoffs")
