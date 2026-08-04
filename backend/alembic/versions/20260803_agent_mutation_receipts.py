"""add idempotent Agent mutation receipts

Revision ID: 20260803_agent_mutation_receipts
Revises: 20260803_crawl_run_app_version
Create Date: 2026-08-03 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260803_agent_mutation_receipts"
down_revision: Union[str, Sequence[str], None] = "20260803_crawl_run_app_version"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "agent_mutation_receipts" in _table_names():
        return
    op.create_table(
        "agent_mutation_receipts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("command", sa.String(length=120), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_mutation_receipts")),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_agent_mutation_receipts_idempotency_key",
        ),
    )


def downgrade() -> None:
    if "agent_mutation_receipts" in _table_names():
        op.drop_table("agent_mutation_receipts")
