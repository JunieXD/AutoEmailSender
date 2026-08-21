"""make the material library global

Revision ID: 20260811_global_material_library
Revises: 20260810_agent_ui_handoffs
Create Date: 2026-08-11 00:00:00.000000

"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260811_global_material_library"
down_revision: Union[str, Sequence[str], None] = "20260810_agent_ui_handoffs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    table_names = set(inspector.get_table_names())
    if (
        "identity_materials" not in table_names
        or "identity_profiles" not in table_names
    ):
        return

    material_columns = {
        column["name"]: column for column in inspector.get_columns("identity_materials")
    }
    if "identity_id" not in material_columns:
        raise RuntimeError(
            "identity_materials.identity_id is required for the global-library upgrade"
        )

    material_fk = _foreign_key_for_column("identity_materials", "identity_id")
    material_fk_is_global = (
        material_fk is not None
        and str((material_fk.get("options") or {}).get("ondelete") or "").upper()
        == "SET NULL"
    )
    material_schema_needs_rebuild = (
        not material_columns["identity_id"].get("nullable", False)
        or not material_fk_is_global
    )
    if material_schema_needs_rebuild:
        with op.batch_alter_table(
            "identity_materials",
            schema=None,
            naming_convention=NAMING_CONVENTION,
        ) as batch_op:
            if material_fk is not None:
                batch_op.drop_constraint(
                    material_fk.get("name")
                    or "fk_identity_materials_identity_id_identity_profiles",
                    type_="foreignkey",
                )
            if not material_columns["identity_id"].get("nullable", False):
                batch_op.alter_column(
                    "identity_id",
                    existing_type=sa.Integer(),
                    nullable=True,
                )

    # A previous interrupted/manual migration may already have the final
    # nullable SET NULL schema while still containing rows written with
    # foreign-key enforcement disabled. Normalize those rows on every path.
    connection.execute(
        sa.text(
            """
            UPDATE identity_materials
            SET identity_id = NULL
            WHERE identity_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM identity_profiles
                  WHERE identity_profiles.id = identity_materials.identity_id
              )
            """
        )
    )

    if material_schema_needs_rebuild:
        # The first batch drops any existing FK before changing nullability.
        # Recreate it even when a partially migrated schema already used
        # SET NULL, otherwise that edge case would silently lose the FK.
        with op.batch_alter_table(
            "identity_materials",
            schema=None,
            naming_convention=NAMING_CONVENTION,
        ) as batch_op:
            batch_op.create_foreign_key(
                "fk_identity_materials_identity_id_identity_profiles",
                "identity_profiles",
                ["identity_id"],
                ["id"],
                ondelete="SET NULL",
            )

    connection.execute(
        sa.text(
            """
            UPDATE identity_profiles
            SET current_primary_material_id = NULL
            WHERE current_primary_material_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM identity_materials
                  WHERE identity_materials.id = identity_profiles.current_primary_material_id
              )
            """
        )
    )
    primary_fk = _foreign_key_for_column(
        "identity_profiles", "current_primary_material_id"
    )
    primary_fk_has_set_null = (
        primary_fk is not None
        and str((primary_fk.get("options") or {}).get("ondelete") or "").upper()
        == "SET NULL"
    )
    if not primary_fk_has_set_null:
        with op.batch_alter_table(
            "identity_profiles",
            schema=None,
            naming_convention=NAMING_CONVENTION,
        ) as batch_op:
            if primary_fk is not None:
                batch_op.drop_constraint(
                    primary_fk.get("name")
                    or "fk_identity_profiles_current_primary_material_id_identity_materials",
                    type_="foreignkey",
                )
            batch_op.create_foreign_key(
                "fk_identity_profiles_current_primary_material_id_identity_materials",
                "identity_materials",
                ["current_primary_material_id"],
                ["id"],
                ondelete="SET NULL",
            )

    _verify_global_schema(connection)


def downgrade() -> None:
    connection = op.get_bind()
    _ensure_legacy_ownership_is_representable(connection)

    material_fk = _foreign_key_for_column("identity_materials", "identity_id")
    with op.batch_alter_table(
        "identity_materials",
        schema=None,
        naming_convention=NAMING_CONVENTION,
    ) as batch_op:
        if material_fk is not None:
            batch_op.drop_constraint(
                material_fk.get("name")
                or "fk_identity_materials_identity_id_identity_profiles",
                type_="foreignkey",
            )
        batch_op.alter_column(
            "identity_id",
            existing_type=sa.Integer(),
            nullable=False,
        )
        batch_op.create_foreign_key(
            "fk_identity_materials_identity_id_identity_profiles",
            "identity_profiles",
            ["identity_id"],
            ["id"],
        )

    primary_fk = _foreign_key_for_column(
        "identity_profiles", "current_primary_material_id"
    )
    with op.batch_alter_table(
        "identity_profiles",
        schema=None,
        naming_convention=NAMING_CONVENTION,
    ) as batch_op:
        if primary_fk is not None:
            batch_op.drop_constraint(
                primary_fk.get("name")
                or "fk_identity_profiles_current_primary_material_id_identity_materials",
                type_="foreignkey",
            )
        batch_op.create_foreign_key(
            "fk_identity_profiles_current_primary_material_id_identity_materials",
            "identity_materials",
            ["current_primary_material_id"],
            ["id"],
        )


def _foreign_key_for_column(
    table_name: str, column_name: str
) -> dict[str, object] | None:
    for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys(table_name):
        if list(foreign_key.get("constrained_columns") or []) == [column_name]:
            return foreign_key
    return None


def _verify_global_schema(connection: sa.Connection) -> None:
    inspector = sa.inspect(connection)
    columns = {
        column["name"]: column for column in inspector.get_columns("identity_materials")
    }
    if not columns["identity_id"].get("nullable", False):
        raise RuntimeError(
            "global material migration did not make identity_id nullable"
        )
    material_fk = _foreign_key_for_column("identity_materials", "identity_id")
    primary_fk = _foreign_key_for_column(
        "identity_profiles", "current_primary_material_id"
    )
    if (
        str(((material_fk or {}).get("options") or {}).get("ondelete") or "").upper()
        != "SET NULL"
    ):
        raise RuntimeError(
            "global material migration did not protect materials on identity deletion"
        )
    if (
        str(((primary_fk or {}).get("options") or {}).get("ondelete") or "").upper()
        != "SET NULL"
    ):
        raise RuntimeError(
            "global material migration did not protect identity defaults on material deletion"
        )


def _ensure_legacy_ownership_is_representable(connection: sa.Connection) -> None:
    owner_by_material_id = {
        int(row.id): (int(row.identity_id) if row.identity_id is not None else None)
        for row in connection.execute(
            sa.text("SELECT id, identity_id FROM identity_materials")
        )
    }
    if any(owner_id is None for owner_id in owner_by_material_id.values()):
        raise RuntimeError(
            "cannot downgrade global material library: materials without a source identity exist",
        )

    scalar_references = (
        ("identity_profiles", "id", "current_primary_material_id"),
        ("email_tasks", "identity_id", "primary_material_id"),
        ("batch_tasks", "identity_id", "primary_material_id"),
        ("match_analysis_runs", "identity_id", "primary_material_id"),
        ("identity_professor_match_results", "identity_id", "primary_material_id"),
    )
    for table_name, identity_column, material_column in scalar_references:
        if not _has_columns(table_name, identity_column, material_column):
            continue
        rows = connection.execute(
            sa.text(
                f"SELECT {identity_column} AS identity_id, {material_column} AS material_id "
                f"FROM {table_name} WHERE {material_column} IS NOT NULL"
            )
        )
        for row in rows:
            if owner_by_material_id.get(int(row.material_id)) != int(row.identity_id):
                raise RuntimeError(
                    "cannot downgrade global material library: cross-identity material references exist",
                )

    json_references = (
        ("email_tasks", "identity_id", "selected_material_ids"),
        ("email_tasks", "identity_id", "draft_rewrite_source_selected_material_ids"),
        ("batch_tasks", "identity_id", "selected_material_ids"),
        ("test_compose_sessions", "identity_id", "selected_material_ids"),
    )
    for table_name, identity_column, material_column in json_references:
        if not _has_columns(table_name, identity_column, material_column):
            continue
        rows = connection.execute(
            sa.text(
                f"SELECT {identity_column} AS identity_id, {material_column} AS material_ids "
                f"FROM {table_name} WHERE {material_column} IS NOT NULL"
            )
        )
        for row in rows:
            for material_id in _normalize_json_ids(row.material_ids):
                if owner_by_material_id.get(material_id) != int(row.identity_id):
                    raise RuntimeError(
                        "cannot downgrade global material library: cross-identity material references exist",
                    )


def _has_columns(table_name: str, *column_names: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return False
    existing = {column["name"] for column in inspector.get_columns(table_name)}
    return set(column_names).issubset(existing)


def _normalize_json_ids(raw_value: object) -> Iterable[int]:
    if isinstance(raw_value, str):
        try:
            raw_value = json.loads(raw_value)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw_value, list):
        return []

    material_ids: list[int] = []
    for value in raw_value:
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            material_id = value
        elif isinstance(value, str) and value.strip().isdigit():
            material_id = int(value.strip())
        else:
            continue
        if material_id > 0:
            material_ids.append(material_id)
    return material_ids
