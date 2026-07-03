from __future__ import annotations

import asyncio
import unittest
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.time import utc_now
from app.models import (
    Base,
    ImapMailboxHistoricalScanStatus,
    ImapMailboxSyncState,
    ImapProfessorHistoricalScanStatus,
    ImapProfessorSyncState,
)
from app.core.config import get_settings
from app.services.imap_sync_state import claim_next_mailbox_history_scans, reset_mailbox_history_scans_to_pending


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

    def test_mailbox_state_tracks_sent_folder_cache_and_throttle(self) -> None:
        async def scenario() -> tuple[str | None, str | None, str | None]:
            async with self.session_factory() as session:
                session.add(
                    ImapMailboxSyncState(
                        identity_id=1,
                        folder_role="sent",
                        folder="Sent",
                        discovered_sent_folder="Sent Messages",
                        sent_folder_discovery_error="not found",
                        throttle_reason="Fetch volume limit exceed",
                    ),
                )
                await session.commit()
                saved = await session.scalar(select(ImapMailboxSyncState))
                return (
                    saved.discovered_sent_folder,
                    saved.sent_folder_discovery_error,
                    saved.throttle_reason,
                )

        self.assertEqual(
            self._run_async(scenario()),
            ("Sent Messages", "not found", "Fetch volume limit exceed"),
        )

    def test_mailbox_state_tracks_professor_ensure_fingerprint(self) -> None:
        async def scenario() -> str | None:
            async with self.session_factory() as session:
                session.add(
                    ImapMailboxSyncState(
                        identity_id=1,
                        professor_state_fingerprint="abc123",
                    ),
                )
                await session.commit()
                saved = await session.scalar(select(ImapMailboxSyncState))
                return saved.professor_state_fingerprint

        self.assertEqual(self._run_async(scenario()), "abc123")

    def test_mailbox_state_tracks_folder_history_scan_progress(self) -> None:
        async def scenario() -> tuple[str, int | None, int | None, int, int, str | None]:
            async with self.session_factory() as session:
                session.add(
                    ImapMailboxSyncState(
                        identity_id=1,
                        folder_role="sent",
                        folder="Sent",
                        history_scan_status=ImapMailboxHistoricalScanStatus.RUNNING.value,
                        history_high_water_uid=500,
                        history_next_before_uid=301,
                        history_scanned_count=200,
                        history_matched_count=3,
                        history_last_error="paused",
                    ),
                )
                await session.commit()
                saved = await session.scalar(select(ImapMailboxSyncState))
                return (
                    saved.history_scan_status,
                    saved.history_high_water_uid,
                    saved.history_next_before_uid,
                    saved.history_scanned_count,
                    saved.history_matched_count,
                    saved.history_last_error,
                )

        self.assertEqual(
            self._run_async(scenario()),
            (
                ImapMailboxHistoricalScanStatus.RUNNING.value,
                500,
                301,
                200,
                3,
                "paused",
            ),
        )

    def test_claim_mailbox_history_refreshes_running_started_at(self) -> None:
        async def scenario() -> bool:
            old_started_at = utc_now() - timedelta(hours=2)
            async with self.session_factory() as session:
                session.add(
                    ImapMailboxSyncState(
                        identity_id=1,
                        folder_role="inbox",
                        folder="INBOX",
                        history_scan_status=ImapMailboxHistoricalScanStatus.PENDING.value,
                        history_scan_started_at=old_started_at,
                    ),
                )
                await session.commit()

            [state] = await claim_next_mailbox_history_scans(
                self.session_factory,
                1,
                folders=[("inbox", "INBOX")],
                limit=1,
            )
            return state.history_scan_started_at > old_started_at

        self.assertTrue(self._run_async(scenario()))

    def test_reset_mailbox_history_pending_clears_started_at(self) -> None:
        async def scenario() -> tuple[str, bool]:
            async with self.session_factory() as session:
                state = ImapMailboxSyncState(
                    identity_id=1,
                    folder_role="inbox",
                    folder="INBOX",
                    history_scan_status=ImapMailboxHistoricalScanStatus.RUNNING.value,
                    history_scan_started_at=utc_now() - timedelta(hours=2),
                )
                session.add(state)
                await session.commit()
                state_id = state.id

            await reset_mailbox_history_scans_to_pending(self.session_factory, [state_id])

            async with self.session_factory() as session:
                state = await session.get(ImapMailboxSyncState, state_id)
                return state.history_scan_status, state.history_scan_started_at is None

        self.assertEqual(
            self._run_async(scenario()),
            (ImapMailboxHistoricalScanStatus.PENDING.value, True),
        )

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
        self.assertIn(
            "discovered_sent_folder",
            Base.metadata.tables["imap_mailbox_sync_states"].columns,
        )
        self.assertIn(
            "throttle_paused_until",
            Base.metadata.tables["imap_mailbox_sync_states"].columns,
        )
        self.assertIn(
            "history_scan_status",
            Base.metadata.tables["imap_mailbox_sync_states"].columns,
        )
        self.assertIn(
            "history_next_before_uid",
            Base.metadata.tables["imap_mailbox_sync_states"].columns,
        )
        professor_indexes = {
            index.name
            for index in Base.metadata.tables["imap_professor_sync_states"].indexes
        }
        self.assertIn("ix_imap_professor_sync_identity_status_updated", professor_indexes)
        mailbox_indexes = {
            index.name
            for index in Base.metadata.tables["imap_mailbox_sync_states"].indexes
        }
        self.assertIn("ix_imap_mailbox_sync_identity_history_status_updated", mailbox_indexes)

    def test_imap_efficiency_settings_defaults_are_conservative(self) -> None:
        settings = get_settings()

        self.assertEqual(settings.imap_poll_interval_seconds, 60)
        self.assertEqual(settings.imap_history_batch_size, 200)
        self.assertEqual(settings.imap_history_command_budget_per_minute, 120)
        self.assertEqual(settings.imap_history_command_rate_per_minute, 40)
        self.assertEqual(settings.imap_history_command_burst, 3)
        self.assertEqual(settings.imap_fetch_batch_size, 20)
        self.assertEqual(settings.imap_sent_folder_failure_ttl_seconds, 3600)
        self.assertEqual(settings.imap_throttle_backoff_seconds, 86400)
        self.assertEqual(settings.imap_ensure_state_ttl_seconds, 300)
