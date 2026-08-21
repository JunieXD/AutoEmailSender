"""add identity materials

Revision ID: b1f4f0d34c6a
Revises: 7a1d5e42c9bd
Create Date: 2026-04-19 13:00:00.000000

"""

from __future__ import annotations

import hashlib
import json
import mimetypes
from pathlib import Path
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b1f4f0d34c6a"
down_revision: Union[str, Sequence[str], None] = "7a1d5e42c9bd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "identity_materials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("identity_id", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("file_path", sa.String(length=500), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=True),
        sa.Column(
            "size_bytes", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column(
            "material_type",
            sa.String(length=32),
            server_default=sa.text("'other'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["identity_id"], ["identity_profiles.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_identity_materials_identity_id"),
        "identity_materials",
        ["identity_id"],
        unique=False,
    )

    with op.batch_alter_table("identity_profiles", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("current_primary_material_id", sa.Integer(), nullable=True)
        )
        batch_op.create_index(
            batch_op.f("ix_identity_profiles_current_primary_material_id"),
            ["current_primary_material_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            "fk_identity_profiles_current_primary_material_id_identity_materials",
            "identity_materials",
            ["current_primary_material_id"],
            ["id"],
        )

    with op.batch_alter_table("batch_tasks", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("primary_material_id", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("selected_material_ids", sa.JSON(), nullable=True)
        )
        batch_op.create_index(
            batch_op.f("ix_batch_tasks_primary_material_id"),
            ["primary_material_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            "fk_batch_tasks_primary_material_id_identity_materials",
            "identity_materials",
            ["primary_material_id"],
            ["id"],
        )

    with op.batch_alter_table("email_tasks", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("primary_material_id", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("selected_material_ids", sa.JSON(), nullable=True)
        )
        batch_op.create_index(
            batch_op.f("ix_email_tasks_primary_material_id"),
            ["primary_material_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            "fk_email_tasks_primary_material_id_identity_materials",
            "identity_materials",
            ["primary_material_id"],
            ["id"],
        )

    _backfill_identity_materials()


def downgrade() -> None:
    with op.batch_alter_table("email_tasks", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_email_tasks_primary_material_id_identity_materials", type_="foreignkey"
        )
        batch_op.drop_index(batch_op.f("ix_email_tasks_primary_material_id"))
        batch_op.drop_column("selected_material_ids")
        batch_op.drop_column("primary_material_id")

    with op.batch_alter_table("batch_tasks", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_batch_tasks_primary_material_id_identity_materials", type_="foreignkey"
        )
        batch_op.drop_index(batch_op.f("ix_batch_tasks_primary_material_id"))
        batch_op.drop_column("selected_material_ids")
        batch_op.drop_column("primary_material_id")

    with op.batch_alter_table("identity_profiles", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_identity_profiles_current_primary_material_id_identity_materials",
            type_="foreignkey",
        )
        batch_op.drop_index(
            batch_op.f("ix_identity_profiles_current_primary_material_id")
        )
        batch_op.drop_column("current_primary_material_id")

    op.drop_index(
        op.f("ix_identity_materials_identity_id"), table_name="identity_materials"
    )
    op.drop_table("identity_materials")


def _backfill_identity_materials() -> None:
    from app.services.file_storage import build_display_name, extract_text_from_document

    connection = op.get_bind()
    metadata = sa.MetaData()
    identity_profiles = sa.Table(
        "identity_profiles", metadata, autoload_with=connection
    )
    attachment_assets = sa.Table(
        "attachment_assets", metadata, autoload_with=connection
    )
    batch_tasks = sa.Table("batch_tasks", metadata, autoload_with=connection)
    email_tasks = sa.Table("email_tasks", metadata, autoload_with=connection)
    identity_materials = sa.Table(
        "identity_materials", metadata, autoload_with=connection
    )

    primary_material_by_identity: dict[int, int] = {}
    attachment_material_map: dict[int, int] = {}

    identity_rows = connection.execute(
        sa.select(
            identity_profiles.c.id,
            identity_profiles.c.resume_file_path,
            identity_profiles.c.resume_text,
            identity_profiles.c.updated_at,
            identity_profiles.c.created_at,
        ),
    ).mappings()
    for row in identity_rows:
        file_path = row["resume_file_path"]
        if not file_path:
            continue
        original_filename = Path(file_path).name or "resume"
        material_id = _insert_material(
            connection,
            identity_materials,
            identity_id=row["id"],
            display_name=build_display_name(original_filename),
            original_filename=original_filename,
            file_path=file_path,
            mime_type=(mimetypes.guess_type(file_path)[0]),
            size_bytes=_file_size(file_path),
            sha256=_sha256_for_file(file_path),
            extracted_text=row["resume_text"] or extract_text_from_document(file_path),
            material_type="resume",
            created_at=row["updated_at"] or row["created_at"],
        )
        primary_material_by_identity[row["id"]] = material_id

    attachment_rows = connection.execute(
        sa.select(
            attachment_assets.c.id,
            attachment_assets.c.identity_id,
            attachment_assets.c.file_name,
            attachment_assets.c.file_path,
            attachment_assets.c.mime_type,
            attachment_assets.c.created_at,
        ),
    ).mappings()
    for row in attachment_rows:
        original_filename = (
            row["file_name"] or Path(row["file_path"]).name or "material"
        )
        material_id = _insert_material(
            connection,
            identity_materials,
            identity_id=row["identity_id"],
            display_name=build_display_name(original_filename),
            original_filename=original_filename,
            file_path=row["file_path"],
            mime_type=row["mime_type"],
            size_bytes=_file_size(row["file_path"]),
            sha256=_sha256_for_file(row["file_path"]),
            extracted_text=extract_text_from_document(row["file_path"]),
            material_type="other",
            created_at=row["created_at"],
        )
        attachment_material_map[row["id"]] = material_id

    for identity_id, material_id in primary_material_by_identity.items():
        connection.execute(
            identity_profiles.update()
            .where(identity_profiles.c.id == identity_id)
            .values(current_primary_material_id=material_id),
        )

    batch_rows = connection.execute(
        sa.select(
            batch_tasks.c.id,
            batch_tasks.c.identity_id,
            batch_tasks.c.selected_attachment_ids,
        ),
    ).mappings()
    for row in batch_rows:
        connection.execute(
            batch_tasks.update()
            .where(batch_tasks.c.id == row["id"])
            .values(
                primary_material_id=primary_material_by_identity.get(
                    row["identity_id"]
                ),
                selected_material_ids=_map_material_ids(
                    row["selected_attachment_ids"],
                    attachment_material_map,
                )
                or None,
            ),
        )

    email_rows = connection.execute(
        sa.select(
            email_tasks.c.id,
            email_tasks.c.identity_id,
            email_tasks.c.selected_attachments,
        ),
    ).mappings()
    for row in email_rows:
        connection.execute(
            email_tasks.update()
            .where(email_tasks.c.id == row["id"])
            .values(
                primary_material_id=primary_material_by_identity.get(
                    row["identity_id"]
                ),
                selected_material_ids=_map_material_ids(
                    row["selected_attachments"],
                    attachment_material_map,
                )
                or None,
            ),
        )


def _insert_material(
    connection,
    identity_materials,
    *,
    identity_id: int,
    display_name: str,
    original_filename: str,
    file_path: str,
    mime_type: str | None,
    size_bytes: int,
    sha256: str,
    extracted_text: str | None,
    material_type: str,
    created_at,
) -> int:
    result = connection.execute(
        identity_materials.insert().values(
            identity_id=identity_id,
            display_name=display_name,
            original_filename=original_filename,
            file_path=file_path,
            mime_type=mime_type,
            size_bytes=size_bytes,
            sha256=sha256,
            extracted_text=extracted_text,
            material_type=material_type,
            created_at=created_at,
        ),
    )
    return int(result.inserted_primary_key[0])


def _map_material_ids(raw_value, id_map: dict[int, int]) -> list[int]:
    mapped: list[int] = []
    for legacy_id in _normalize_json_ids(raw_value):
        next_id = id_map.get(legacy_id)
        if next_id is not None:
            mapped.append(next_id)
    return mapped


def _normalize_json_ids(raw_value) -> list[int]:
    if raw_value is None:
        return []
    if isinstance(raw_value, list):
        values = raw_value
    elif isinstance(raw_value, str):
        stripped = raw_value.strip()
        if not stripped:
            return []
        try:
            values = json.loads(stripped)
        except json.JSONDecodeError:
            return []
    else:
        return []
    return [int(item) for item in values if str(item).strip()]


def _file_size(file_path: str | None) -> int:
    if not file_path:
        return 0
    path = Path(file_path)
    return path.stat().st_size if path.exists() else 0


def _sha256_for_file(file_path: str | None) -> str:
    if not file_path:
        return hashlib.sha256(b"").hexdigest()
    path = Path(file_path)
    if not path.exists():
        return hashlib.sha256(b"").hexdigest()
    return hashlib.sha256(path.read_bytes()).hexdigest()
