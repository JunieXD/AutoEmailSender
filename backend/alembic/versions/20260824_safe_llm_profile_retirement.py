"""add safe LLM profile retirement

Revision ID: 20260824_safe_llm_retire
Revises: 20260822_thinking_probe_v2
Create Date: 2026-08-24 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260824_safe_llm_retire"
down_revision: str | Sequence[str] | None = "20260822_thinking_probe_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("llm_profiles")}
    unique_constraints = {
        constraint.get("name")
        for constraint in inspector.get_unique_constraints("llm_profiles")
    }

    with op.batch_alter_table(
        "llm_profiles",
        schema=None,
        naming_convention=NAMING_CONVENTION,
    ) as batch_op:
        if "deleted_at" not in columns:
            batch_op.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))
        if "uq_llm_profiles_name" in unique_constraints:
            batch_op.drop_constraint("uq_llm_profiles_name", type_="unique")

    refreshed = sa.inspect(op.get_bind())
    index_names = {
        index.get("name") for index in refreshed.get_indexes("llm_profiles")
    }
    if "ix_llm_profiles_deleted_at" not in index_names:
        op.create_index(
            "ix_llm_profiles_deleted_at",
            "llm_profiles",
            ["deleted_at"],
            unique=False,
        )
    if "uq_llm_profiles_active_name" not in index_names:
        op.create_index(
            "uq_llm_profiles_active_name",
            "llm_profiles",
            ["name"],
            unique=True,
            sqlite_where=sa.text("deleted_at IS NULL"),
            postgresql_where=sa.text("deleted_at IS NULL"),
        )


def downgrade() -> None:
    connection = op.get_bind()
    duplicate_name = connection.execute(
        sa.text(
            """
            SELECT name
            FROM llm_profiles
            GROUP BY name
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).scalar_one_or_none()
    if duplicate_name is not None:
        raise RuntimeError(
            "Cannot downgrade safe LLM retirement while active and deleted "
            f"profiles share the name {duplicate_name!r}"
        )

    inspector = sa.inspect(connection)
    index_names = {
        index.get("name") for index in inspector.get_indexes("llm_profiles")
    }
    if "uq_llm_profiles_active_name" in index_names:
        op.drop_index("uq_llm_profiles_active_name", table_name="llm_profiles")
    if "ix_llm_profiles_deleted_at" in index_names:
        op.drop_index("ix_llm_profiles_deleted_at", table_name="llm_profiles")

    with op.batch_alter_table(
        "llm_profiles",
        schema=None,
        naming_convention=NAMING_CONVENTION,
    ) as batch_op:
        batch_op.create_unique_constraint("uq_llm_profiles_name", ["name"])
        batch_op.drop_column("deleted_at")
