"""add identity communication groups

Revision ID: 20260721_identity_comm_groups
Revises: 20260721_match_analysis_cache
Create Date: 2026-07-21 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260721_identity_comm_groups"
down_revision: Union[str, Sequence[str], None] = "20260721_match_analysis_cache"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "identity_communication_groups" not in _table_names():
        op.create_table(
            "identity_communication_groups",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.PrimaryKeyConstraint("id"),
        )

    needs_column = "communication_group_id" not in _column_names("identity_profiles")
    needs_foreign_key = not _has_communication_group_foreign_key()
    if needs_column or needs_foreign_key:
        with op.batch_alter_table("identity_profiles") as batch_op:
            if needs_column:
                batch_op.add_column(
                    sa.Column("communication_group_id", sa.Integer(), nullable=True),
                )
            if needs_foreign_key:
                batch_op.create_foreign_key(
                    "fk_identity_profiles_communication_group_id",
                    "identity_communication_groups",
                    ["communication_group_id"],
                    ["id"],
                    ondelete="SET NULL",
                )

    op.create_index(
        "ix_identity_profiles_communication_group_id",
        "identity_profiles",
        ["communication_group_id"],
        unique=False,
        if_not_exists=True,
    )


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _column_names(table_name: str) -> set[str]:
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def _has_communication_group_foreign_key() -> bool:
    return any(
        foreign_key.get("constrained_columns") == ["communication_group_id"]
        and foreign_key.get("referred_table") == "identity_communication_groups"
        for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys(
            "identity_profiles",
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("identity_profiles") as batch_op:
        batch_op.drop_index("ix_identity_profiles_communication_group_id")
        batch_op.drop_constraint(
            "fk_identity_profiles_communication_group_id",
            type_="foreignkey",
        )
        batch_op.drop_column("communication_group_id")
    op.drop_table("identity_communication_groups")
