"""add imap sync states

Revision ID: f8a9b0c1d2e3
Revises: e7a1b2c3d4f5
Create Date: 2026-05-18 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f8a9b0c1d2e3"
down_revision: Union[str, Sequence[str], None] = "e7a1b2c3d4f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "imap_mailbox_sync_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("identity_id", sa.Integer(), nullable=False),
        sa.Column(
            "folder",
            sa.String(length=64),
            server_default=sa.text("'INBOX'"),
            nullable=False,
        ),
        sa.Column("uidvalidity", sa.Integer(), nullable=True),
        sa.Column("last_seen_uid", sa.Integer(), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
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
            name=op.f("fk_imap_mailbox_sync_states_identity_id_identity_profiles"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_imap_mailbox_sync_states")),
        sa.UniqueConstraint(
            "identity_id",
            "folder",
            name="uq_imap_mailbox_identity_folder",
        ),
    )
    op.create_index(
        op.f("ix_imap_mailbox_sync_states_identity_id"),
        "imap_mailbox_sync_states",
        ["identity_id"],
        unique=False,
    )

    op.create_table(
        "imap_professor_sync_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("identity_id", sa.Integer(), nullable=False),
        sa.Column("professor_id", sa.Integer(), nullable=False),
        sa.Column("professor_email", sa.String(length=255), nullable=False),
        sa.Column(
            "folder",
            sa.String(length=64),
            server_default=sa.text("'INBOX'"),
            nullable=False,
        ),
        sa.Column(
            "historical_scan_status",
            sa.String(length=32),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("last_scanned_uid", sa.Integer(), nullable=True),
        sa.Column(
            "historical_scan_started_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "historical_scan_completed_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
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
            name=op.f("fk_imap_professor_sync_states_identity_id_identity_profiles"),
        ),
        sa.ForeignKeyConstraint(
            ["professor_id"],
            ["professors.id"],
            name=op.f("fk_imap_professor_sync_states_professor_id_professors"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_imap_professor_sync_states")),
        sa.UniqueConstraint(
            "identity_id",
            "professor_id",
            "professor_email",
            "folder",
            name="uq_imap_professor_identity_professor_email_folder",
        ),
    )
    op.create_index(
        op.f("ix_imap_professor_sync_states_identity_id"),
        "imap_professor_sync_states",
        ["identity_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_imap_professor_sync_states_professor_email"),
        "imap_professor_sync_states",
        ["professor_email"],
        unique=False,
    )
    op.create_index(
        op.f("ix_imap_professor_sync_states_professor_id"),
        "imap_professor_sync_states",
        ["professor_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_imap_professor_sync_states_professor_id"),
        table_name="imap_professor_sync_states",
    )
    op.drop_index(
        op.f("ix_imap_professor_sync_states_professor_email"),
        table_name="imap_professor_sync_states",
    )
    op.drop_index(
        op.f("ix_imap_professor_sync_states_identity_id"),
        table_name="imap_professor_sync_states",
    )
    op.drop_table("imap_professor_sync_states")
    op.drop_index(
        op.f("ix_imap_mailbox_sync_states_identity_id"),
        table_name="imap_mailbox_sync_states",
    )
    op.drop_table("imap_mailbox_sync_states")
