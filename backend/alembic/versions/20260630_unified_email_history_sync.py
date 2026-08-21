"""unified email history sync

Revision ID: 20260630_unified_email_history_sync
Revises: 20260614taskmat
Create Date: 2026-06-30 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260630_unified_email_history_sync"
down_revision: Union[str, Sequence[str], None] = "20260614taskmat"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MESSAGE_INDEX_WHERE = "normalized_message_id IS NOT NULL"
IMAP_UID_INDEX_WHERE = (
    "folder_role IS NOT NULL "
    "AND folder IS NOT NULL "
    "AND uidvalidity IS NOT NULL "
    "AND imap_uid IS NOT NULL"
)
FINGERPRINT_INDEX_WHERE = "message_fingerprint IS NOT NULL"


def upgrade() -> None:
    op.drop_index("uq_email_logs_rfc_message_id", table_name="email_logs")

    with op.batch_alter_table("email_logs", schema=None) as batch_op:
        batch_op.alter_column(
            "llm_profile_id",
            existing_type=sa.Integer(),
            nullable=True,
        )
        batch_op.add_column(
            sa.Column(
                "ingest_source",
                sa.String(length=20),
                server_default=sa.text("'system'"),
                nullable=False,
            ),
        )
        batch_op.add_column(
            sa.Column("folder_role", sa.String(length=20), nullable=True)
        )
        batch_op.add_column(sa.Column("folder", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("uidvalidity", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("imap_uid", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("normalized_message_id", sa.String(length=255), nullable=True)
        )
        batch_op.add_column(
            sa.Column("message_fingerprint", sa.String(length=255), nullable=True)
        )
        batch_op.add_column(
            sa.Column("from_email", sa.String(length=255), nullable=True)
        )
        batch_op.add_column(sa.Column("to_emails", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("cc_emails", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("bcc_emails", sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True)
        )

    op.execute(
        sa.text(
            """
            UPDATE email_logs
            SET normalized_message_id = lower(trim(rfc_message_id))
            WHERE rfc_message_id IS NOT NULL
              AND trim(rfc_message_id) != ''
              AND id = (
                  SELECT MIN(candidate.id)
                  FROM email_logs AS candidate
                  WHERE candidate.identity_id = email_logs.identity_id
                    AND candidate.professor_id = email_logs.professor_id
                    AND candidate.direction = email_logs.direction
                    AND candidate.rfc_message_id IS NOT NULL
                    AND trim(candidate.rfc_message_id) != ''
                    AND lower(trim(candidate.rfc_message_id)) = lower(trim(email_logs.rfc_message_id))
              )
            """,
        ),
    )

    op.create_index(
        "uq_email_logs_identity_professor_direction_message",
        "email_logs",
        ["identity_id", "professor_id", "direction", "normalized_message_id"],
        unique=True,
        sqlite_where=sa.text(MESSAGE_INDEX_WHERE),
        postgresql_where=sa.text(MESSAGE_INDEX_WHERE),
    )
    op.create_index(
        "uq_email_logs_identity_professor_imap_uid",
        "email_logs",
        [
            "identity_id",
            "professor_id",
            "folder_role",
            "folder",
            "uidvalidity",
            "imap_uid",
        ],
        unique=True,
        sqlite_where=sa.text(IMAP_UID_INDEX_WHERE),
        postgresql_where=sa.text(IMAP_UID_INDEX_WHERE),
    )
    op.create_index(
        "uq_email_logs_identity_professor_direction_fingerprint",
        "email_logs",
        ["identity_id", "professor_id", "direction", "message_fingerprint"],
        unique=True,
        sqlite_where=sa.text(FINGERPRINT_INDEX_WHERE),
        postgresql_where=sa.text(FINGERPRINT_INDEX_WHERE),
    )

    with op.batch_alter_table("imap_mailbox_sync_states", schema=None) as batch_op:
        batch_op.drop_constraint("uq_imap_mailbox_identity_folder", type_="unique")
        batch_op.add_column(
            sa.Column(
                "folder_role",
                sa.String(length=20),
                server_default=sa.text("'inbox'"),
                nullable=False,
            ),
        )
        batch_op.alter_column(
            "folder",
            existing_type=sa.String(length=64),
            type_=sa.String(length=255),
            existing_nullable=False,
            existing_server_default=sa.text("'INBOX'"),
        )
        batch_op.create_unique_constraint(
            "uq_imap_mailbox_identity_folder_role_folder",
            ["identity_id", "folder_role", "folder"],
        )

    with op.batch_alter_table("imap_professor_sync_states", schema=None) as batch_op:
        batch_op.drop_constraint(
            "uq_imap_professor_identity_professor_email_folder", type_="unique"
        )
        batch_op.add_column(
            sa.Column(
                "folder_role",
                sa.String(length=20),
                server_default=sa.text("'inbox'"),
                nullable=False,
            ),
        )
        batch_op.alter_column(
            "folder",
            existing_type=sa.String(length=64),
            type_=sa.String(length=255),
            existing_nullable=False,
            existing_server_default=sa.text("'INBOX'"),
        )
        batch_op.create_unique_constraint(
            "uq_imap_professor_identity_professor_email_folder_role_folder",
            ["identity_id", "professor_id", "professor_email", "folder_role", "folder"],
        )


def downgrade() -> None:
    null_llm_profile_count = op.get_bind().scalar(
        sa.text("SELECT COUNT(*) FROM email_logs WHERE llm_profile_id IS NULL"),
    )
    if null_llm_profile_count:
        raise RuntimeError(
            "cannot downgrade unified email history sync: "
            "email_logs.llm_profile_id contains NULL values. "
            "Clean up IMAP/unified email history rows before downgrade.",
        )

    with op.batch_alter_table("imap_professor_sync_states", schema=None) as batch_op:
        batch_op.drop_constraint(
            "uq_imap_professor_identity_professor_email_folder_role_folder",
            type_="unique",
        )
        batch_op.alter_column(
            "folder",
            existing_type=sa.String(length=255),
            type_=sa.String(length=64),
            existing_nullable=False,
            existing_server_default=sa.text("'INBOX'"),
        )
        batch_op.drop_column("folder_role")
        batch_op.create_unique_constraint(
            "uq_imap_professor_identity_professor_email_folder",
            ["identity_id", "professor_id", "professor_email", "folder"],
        )

    with op.batch_alter_table("imap_mailbox_sync_states", schema=None) as batch_op:
        batch_op.drop_constraint(
            "uq_imap_mailbox_identity_folder_role_folder", type_="unique"
        )
        batch_op.alter_column(
            "folder",
            existing_type=sa.String(length=255),
            type_=sa.String(length=64),
            existing_nullable=False,
            existing_server_default=sa.text("'INBOX'"),
        )
        batch_op.drop_column("folder_role")
        batch_op.create_unique_constraint(
            "uq_imap_mailbox_identity_folder",
            ["identity_id", "folder"],
        )

    op.drop_index(
        "uq_email_logs_identity_professor_direction_fingerprint",
        table_name="email_logs",
    )
    op.drop_index("uq_email_logs_identity_professor_imap_uid", table_name="email_logs")
    op.drop_index(
        "uq_email_logs_identity_professor_direction_message",
        table_name="email_logs",
    )

    with op.batch_alter_table("email_logs", schema=None) as batch_op:
        batch_op.drop_column("synced_at")
        batch_op.drop_column("bcc_emails")
        batch_op.drop_column("cc_emails")
        batch_op.drop_column("to_emails")
        batch_op.drop_column("from_email")
        batch_op.drop_column("message_fingerprint")
        batch_op.drop_column("normalized_message_id")
        batch_op.drop_column("imap_uid")
        batch_op.drop_column("uidvalidity")
        batch_op.drop_column("folder")
        batch_op.drop_column("folder_role")
        batch_op.drop_column("ingest_source")
        batch_op.alter_column(
            "llm_profile_id",
            existing_type=sa.Integer(),
            nullable=False,
        )

    # This restores the previous global uniqueness contract. Downgrade can fail
    # if duplicate rfc_message_id values were inserted while the scoped indexes existed.
    op.create_index(
        "uq_email_logs_rfc_message_id",
        "email_logs",
        ["rfc_message_id"],
        unique=True,
    )
