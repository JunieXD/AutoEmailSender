from __future__ import annotations

import asyncio
import unittest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    Base,
    ImapMailboxSyncState,
    ImapProfessorHistoricalScanStatus,
    ImapProfessorSyncState,
)


class ImapSyncModelsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self._run_async(self._create_schema())

    def tearDown(self) -> None:
        self._run_async(self.engine.dispose())

    def _run_async(self, awaitable):
        return asyncio.run(awaitable)

    async def _create_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    def test_mailbox_state_defaults_to_inbox(self) -> None:
        async def scenario() -> tuple[str, str]:
            async with self.session_factory() as session:
                session.add(ImapMailboxSyncState(identity_id=1))
                await session.commit()
                saved = await session.scalar(select(ImapMailboxSyncState))
                return saved.folder_role, saved.folder

        self.assertEqual(self._run_async(scenario()), ("inbox", "INBOX"))

    def test_mailbox_state_can_store_folder_role_and_real_folder(self) -> None:
        async def scenario() -> tuple[str, str]:
            async with self.session_factory() as session:
                session.add(
                    ImapMailboxSyncState(
                        identity_id=1,
                        folder_role="sent",
                        folder="Sent",
                    ),
                )
                await session.commit()
                saved = await session.scalar(select(ImapMailboxSyncState))
                return saved.folder_role, saved.folder

        self.assertEqual(self._run_async(scenario()), ("sent", "Sent"))

    def test_professor_state_defaults_to_pending(self) -> None:
        async def scenario() -> tuple[str, str]:
            async with self.session_factory() as session:
                session.add(
                    ImapProfessorSyncState(
                        identity_id=1,
                        professor_id=2,
                        professor_email="prof@example.edu",
                    ),
                )
                await session.commit()
                saved = await session.scalar(select(ImapProfessorSyncState))
                return saved.folder_role, saved.historical_scan_status

        self.assertEqual(
            self._run_async(scenario()),
            ("inbox", ImapProfessorHistoricalScanStatus.PENDING.value),
        )

    def test_professor_state_can_store_folder_role_and_real_folder(self) -> None:
        async def scenario() -> tuple[str, str]:
            async with self.session_factory() as session:
                session.add(
                    ImapProfessorSyncState(
                        identity_id=1,
                        professor_id=2,
                        professor_email="prof@example.edu",
                        folder_role="sent",
                        folder="Sent",
                    ),
                )
                await session.commit()
                saved = await session.scalar(select(ImapProfessorSyncState))
                return saved.folder_role, saved.folder

        self.assertEqual(self._run_async(scenario()), ("sent", "Sent"))

    def test_sync_state_tables_exist_in_metadata(self) -> None:
        self.assertIn("imap_mailbox_sync_states", Base.metadata.tables)
        self.assertIn("imap_professor_sync_states", Base.metadata.tables)
        self.assertIn(
            "last_seen_uid",
            Base.metadata.tables["imap_mailbox_sync_states"].columns,
        )
        self.assertIn(
            "folder_role",
            Base.metadata.tables["imap_mailbox_sync_states"].columns,
        )
        self.assertIn(
            "historical_scan_status",
            Base.metadata.tables["imap_professor_sync_states"].columns,
        )
        self.assertIn(
            "folder_role",
            Base.metadata.tables["imap_professor_sync_states"].columns,
        )
