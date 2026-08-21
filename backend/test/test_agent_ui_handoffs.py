from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from test.migrated_database import create_migrated_sqlite_database


UI_TOKEN = "ui-handoff-ui-token"
AGENT_TOKEN = "ui-handoff-agent-token"


class AgentUiHandoffApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ["AUTO_EMAIL_SENDER_UI_TOKEN"] = UI_TOKEN
        os.environ["AUTO_EMAIL_SENDER_AGENT_TOKEN"] = AGENT_TOKEN
        os.environ["ENABLE_BACKGROUND_WORKERS"] = "0"

        from main import create_app

        cls.client = TestClient(create_app(), raise_server_exceptions=False)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        os.environ.pop("AUTO_EMAIL_SENDER_UI_TOKEN", None)
        os.environ.pop("AUTO_EMAIL_SENDER_AGENT_TOKEN", None)
        os.environ.pop("ENABLE_BACKGROUND_WORKERS", None)

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "agent-ui-handoffs.db"
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{self.db_path.as_posix()}"
        os.environ["AUTO_EMAIL_SENDER_DATA_DIR"] = self.temp_dir.name
        create_migrated_sqlite_database(self.db_path)

        from app.core.config import get_settings
        from app.core.database import dispose_engine, get_engine, get_session_factory

        get_settings.cache_clear()
        if get_engine.cache_info().currsize:
            asyncio.run(dispose_engine())
        get_session_factory.cache_clear()
        get_settings.cache_clear()

    def tearDown(self) -> None:
        from app.core.config import get_settings
        from app.core.database import dispose_engine, get_engine, get_session_factory

        if get_engine.cache_info().currsize:
            asyncio.run(dispose_engine())
        get_session_factory.cache_clear()
        get_settings.cache_clear()
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("AUTO_EMAIL_SENDER_DATA_DIR", None)
        self.temp_dir.cleanup()

    def test_professor_selection_is_frozen_private_and_queryable_after_ack(
        self,
    ) -> None:
        selected_id = self._create_professor(
            name="José",
            email="jose-ui-handoff@example.edu",
        )
        excluded_id = self._create_professor(
            name="李 Ada",
            email="ada-ui-handoff@example.edu",
        )
        self._create_professor(name="李雷", email="han-ui-handoff@example.edu")

        response = self._present_professors(
            {
                "selection": {
                    "mode": "filter",
                    "filter": {
                        "archived": "active",
                        "where": {"name": {"contains_script": "latin"}},
                    },
                    "exclude_ids": [excluded_id],
                },
                "surface": "professors.management",
                "selection_mode": "replace",
                "display": "selected_only",
            },
            idempotency_key="frozen-professor-selection",
        )
        self.assertEqual(response.status_code, 201, msg=response.text)
        handoff = response.json()
        self.assertEqual(handoff["status"], "pending")
        self.assertEqual(handoff["selection_count"], 1)
        self.assertTrue(handoff["selection_fingerprint"])
        self.assertEqual(handoff["route"], "/professors")
        self.assertNotIn("payload", handoff)
        self.assertNotIn("selected_ids", handoff)

        created_later_id = self._create_professor(
            name="Grace Hopper",
            email="grace-ui-handoff@example.edu",
        )
        claim = self._claim("desktop:test-frozen")
        self.assertEqual(claim.status_code, 200, msg=claim.text)
        claimed = claim.json()
        self.assertEqual(claimed["handoff_id"], handoff["handoff_id"])
        self.assertEqual(claimed["selected_ids"], [selected_id])
        self.assertEqual(claimed["payload"]["matched_count"], 2)
        self.assertEqual(claimed["payload"]["excluded_count"], 1)

        page = self.client.post(
            "/api/professors/search/management",
            headers=self._ui_headers(),
            json={
                "page_size": 100,
                "archived": "active",
                "ui_handoff_id": handoff["handoff_id"],
            },
        )
        self.assertEqual(page.status_code, 200, msg=page.text)
        self.assertEqual([item["id"] for item in page.json()["items"]], [selected_id])
        self.assertNotIn(
            created_later_id, [item["id"] for item in page.json()["items"]]
        )

        acknowledged = self._acknowledge(
            handoff["handoff_id"],
            consumer_id="desktop:test-frozen",
            status="applied",
            result={"route": "/professors", "visible_count": 1},
        )
        self.assertEqual(acknowledged.status_code, 200, msg=acknowledged.text)
        self.assertEqual(acknowledged.json()["status"], "applied")
        self.assertGreater(
            acknowledged.json()["expires_at"],
            handoff["expires_at"],
        )

        page_after_ack = self.client.post(
            "/api/professors/search/management",
            headers=self._ui_headers(),
            json={
                "page_size": 100,
                "archived": "active",
                "ui_handoff_id": handoff["handoff_id"],
            },
        )
        self.assertEqual(page_after_ack.status_code, 200, msg=page_after_ack.text)
        self.assertEqual(
            [item["id"] for item in page_after_ack.json()["items"]],
            [selected_id],
        )

        with closing(sqlite3.connect(self.db_path)) as connection:
            rows = connection.execute(
                "SELECT id, archived_at, personal_note FROM professors ORDER BY id",
            ).fetchall()
        self.assertTrue(all(archived_at is None for _, archived_at, _ in rows))
        self.assertTrue(all(personal_note is None for _, _, personal_note in rows))

    def test_idempotency_selection_validation_and_home_constraints(self) -> None:
        first_id = self._create_professor(
            name="First",
            email="first-ui-handoff@example.edu",
        )
        second_id = self._create_professor(
            name="Second",
            email="second-ui-handoff@example.edu",
        )
        payload = {
            "selection": {"mode": "ids", "ids": [first_id]},
            "surface": "professors.management",
        }
        first = self._present_professors(payload, idempotency_key="same-handoff")
        replay = self._present_professors(payload, idempotency_key="same-handoff")
        mismatch = self._present_professors(
            {
                "selection": {"mode": "ids", "ids": [second_id]},
                "surface": "professors.management",
            },
            idempotency_key="same-handoff",
        )
        self.assertEqual(first.status_code, 201, msg=first.text)
        self.assertEqual(replay.status_code, 201, msg=replay.text)
        self.assertEqual(first.json()["handoff_id"], replay.json()["handoff_id"])
        self.assertTrue(replay.json()["idempotent_replay"])
        self.assertEqual(mismatch.status_code, 409, msg=mismatch.text)
        self.assertEqual(mismatch.json()["error"]["code"], "IDEMPOTENCY_KEY_REUSED")

        missing = self._present_professors(
            {"selection": {"mode": "ids", "ids": [999_999]}},
        )
        empty = self._present_professors(
            {
                "selection": {
                    "mode": "filter",
                    "filter": {"where": {"name": {"eq": "Nobody"}}},
                },
            },
        )
        invalid_home = self._present_professors(
            {
                "selection": {"mode": "ids", "ids": [first_id]},
                "surface": "professors.home",
            },
        )
        self.assertEqual(missing.status_code, 404, msg=missing.text)
        self.assertEqual(missing.json()["error"]["code"], "PROFESSOR_NOT_FOUND")
        self.assertEqual(empty.status_code, 409, msg=empty.text)
        self.assertEqual(empty.json()["error"]["code"], "UI_HANDOFF_SELECTION_EMPTY")
        self.assertEqual(invalid_home.status_code, 422, msg=invalid_home.text)

        with patch("app.services.agent_ui_handoffs.UI_HANDOFF_MAX_SELECTION", 1):
            too_large = self._present_professors(
                {"selection": {"mode": "ids", "ids": [first_id, second_id]}},
            )
        self.assertEqual(too_large.status_code, 413, msg=too_large.text)
        self.assertEqual(
            too_large.json()["error"]["code"],
            "UI_HANDOFF_SELECTION_TOO_LARGE",
        )

        identity_id = self._create_identity()
        archived = self.client.post(
            f"/api/professors/{second_id}/archive",
            headers=self._ui_headers(),
        )
        self.assertEqual(archived.status_code, 200, msg=archived.text)
        archived_home = self._present_professors(
            {
                "selection": {"mode": "ids", "ids": [second_id]},
                "surface": "professors.home",
                "identity_id": identity_id,
            },
        )
        missing_identity = self._present_professors(
            {
                "selection": {"mode": "ids", "ids": [first_id]},
                "surface": "professors.home",
                "identity_id": 999_999,
            },
        )
        management_identity = self._present_professors(
            {
                "selection": {"mode": "ids", "ids": [first_id]},
                "surface": "professors.management",
                "identity_id": identity_id,
            },
        )
        valid_home = self._present_professors(
            {
                "selection": {"mode": "ids", "ids": [first_id]},
                "surface": "professors.home",
                "identity_id": identity_id,
            },
        )
        self.assertEqual(archived_home.status_code, 409, msg=archived_home.text)
        self.assertEqual(
            archived_home.json()["error"]["code"],
            "HOME_SELECTION_ARCHIVED_UNSUPPORTED",
        )
        self.assertEqual(missing_identity.status_code, 404, msg=missing_identity.text)
        self.assertEqual(
            management_identity.status_code, 422, msg=management_identity.text
        )
        self.assertEqual(valid_home.status_code, 201, msg=valid_home.text)
        self.assertEqual(valid_home.json()["surface"], "professors.home")

    def test_selected_only_queries_validate_scope_identity_and_pagination(self) -> None:
        first_id = self._create_professor(
            name="First Selected",
            email="first-selected-page@example.edu",
        )
        archived_id = self._create_professor(
            name="Archived Selected",
            email="archived-selected-page@example.edu",
        )
        last_id = self._create_professor(
            name="Last Selected",
            email="last-selected-page@example.edu",
        )
        unrelated_id = self._create_professor(
            name="Unrelated",
            email="unrelated-selected-page@example.edu",
        )
        archived = self.client.post(
            f"/api/professors/{archived_id}/archive",
            headers=self._ui_headers(),
        )
        self.assertEqual(archived.status_code, 200, msg=archived.text)

        management = self._present_professors(
            {
                "selection": {
                    "mode": "ids",
                    "ids": [first_id, archived_id, last_id],
                },
                "surface": "professors.management",
                "display": "selected_only",
            },
        )
        self.assertEqual(management.status_code, 201, msg=management.text)
        management_handoff = management.json()

        paged_ids: list[int] = []
        for page_number in (1, 2, 3):
            page = self.client.post(
                "/api/professors/search/management",
                headers=self._ui_headers(),
                json={
                    "page": page_number,
                    "page_size": 1,
                    "archived": "all",
                    "ui_handoff_id": management_handoff["handoff_id"],
                },
            )
            self.assertEqual(page.status_code, 200, msg=page.text)
            self.assertEqual(page.json()["total_count"], 3)
            self.assertEqual(page.json()["total_pages"], 3)
            paged_ids.extend(item["id"] for item in page.json()["items"])
        self.assertEqual(
            set(paged_ids),
            {first_id, archived_id, last_id},
        )
        self.assertNotIn(unrelated_id, paged_ids)

        first_identity_id = self._create_identity(
            email="first-home-handoff@example.com",
        )
        second_identity_id = self._create_identity(
            email="second-home-handoff@example.com",
        )
        wrong_surface = self.client.post(
            "/api/professors/search/dashboard",
            headers=self._ui_headers(),
            json={
                "identity_id": first_identity_id,
                "ui_handoff_id": management_handoff["handoff_id"],
            },
        )
        self.assertEqual(wrong_surface.status_code, 404, msg=wrong_surface.text)
        self.assertEqual(
            wrong_surface.json()["error"]["code"],
            "UI_HANDOFF_NOT_FOUND",
        )

        home = self._present_professors(
            {
                "selection": {"mode": "ids", "ids": [first_id, last_id]},
                "surface": "professors.home",
                "identity_id": first_identity_id,
                "display": "selected_only",
            },
        )
        self.assertEqual(home.status_code, 201, msg=home.text)
        home_handoff = home.json()

        home_ids: list[int] = []
        for page_number in (1, 2):
            page = self.client.post(
                "/api/professors/search/dashboard",
                headers=self._ui_headers(),
                json={
                    "identity_id": first_identity_id,
                    "page": page_number,
                    "page_size": 1,
                    "ui_handoff_id": home_handoff["handoff_id"],
                },
            )
            self.assertEqual(page.status_code, 200, msg=page.text)
            self.assertEqual(page.json()["total_count"], 2)
            home_ids.extend(item["id"] for item in page.json()["items"])
        self.assertEqual(set(home_ids), {first_id, last_id})

        wrong_identity = self.client.post(
            "/api/professors/search/dashboard",
            headers=self._ui_headers(),
            json={
                "identity_id": second_identity_id,
                "ui_handoff_id": home_handoff["handoff_id"],
            },
        )
        self.assertEqual(wrong_identity.status_code, 409, msg=wrong_identity.text)
        self.assertEqual(
            wrong_identity.json()["error"]["code"],
            "UI_HANDOFF_IDENTITY_MISMATCH",
        )

    def test_claim_acknowledgement_retry_cancel_expiry_and_lease_recovery(self) -> None:
        professor_id = self._create_professor(email="lifecycle-ui-handoff@example.edu")
        created = self._present_professors(
            {"selection": {"mode": "ids", "ids": [professor_id]}},
        ).json()

        first_claim = self._claim("desktop:first")
        self.assertEqual(first_claim.status_code, 200, msg=first_claim.text)
        self.assertEqual(self._claim("desktop:second").status_code, 204)
        wrong_consumer = self._acknowledge(
            created["handoff_id"],
            consumer_id="desktop:other",
            status="applied",
        )
        self.assertEqual(wrong_consumer.status_code, 409, msg=wrong_consumer.text)
        self.assertEqual(
            wrong_consumer.json()["error"]["code"],
            "UI_HANDOFF_CONSUMER_MISMATCH",
        )

        awaiting = self._acknowledge(
            created["handoff_id"],
            consumer_id="desktop:first",
            status="awaiting_user",
            result={"reason": "navigation_blocked"},
        )
        self.assertEqual(awaiting.status_code, 200, msg=awaiting.text)
        self.assertEqual(awaiting.json()["status"], "awaiting_user")
        self.assertEqual(
            awaiting.json()["available_actions"], ["read", "retry", "cancel"]
        )

        retried = self.client.post(
            f"/api/agent/v1/ui-handoffs/{created['handoff_id']}/retry",
            headers=self._agent_headers(),
        )
        self.assertEqual(retried.status_code, 200, msg=retried.text)
        second_claim = self._claim("desktop:second")
        self.assertEqual(second_claim.status_code, 200, msg=second_claim.text)
        self.assertEqual(second_claim.json()["delivery_attempts"], 2)
        invalid_failure = self._acknowledge(
            created["handoff_id"],
            consumer_id="desktop:second",
            status="failed",
        )
        self.assertEqual(invalid_failure.status_code, 422, msg=invalid_failure.text)
        failed = self._acknowledge(
            created["handoff_id"],
            consumer_id="desktop:second",
            status="failed",
            failure_message="page adapter rejected the payload",
        )
        self.assertEqual(failed.status_code, 200, msg=failed.text)
        self.assertEqual(failed.json()["status"], "failed")

        retried_after_failure = self.client.post(
            f"/api/agent/v1/ui-handoffs/{created['handoff_id']}/retry",
            headers=self._agent_headers(),
        )
        self.assertEqual(
            retried_after_failure.status_code, 200, msg=retried_after_failure.text
        )
        lease_claim = self._claim("desktop:lease-one")
        self.assertEqual(lease_claim.status_code, 200, msg=lease_claim.text)
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute(
                "UPDATE agent_ui_handoffs SET claim_expires_at = ? WHERE id = ?",
                ("2000-01-01 00:00:00.000000", created["handoff_id"]),
            )
        recovered_claim = self._claim("desktop:lease-two")
        self.assertEqual(recovered_claim.status_code, 200, msg=recovered_claim.text)
        self.assertEqual(recovered_claim.json()["handoff_id"], created["handoff_id"])
        self.assertEqual(recovered_claim.json()["delivery_attempts"], 4)

        applied = self._acknowledge(
            created["handoff_id"],
            consumer_id="desktop:lease-two",
            status="applied",
        )
        duplicate_applied = self._acknowledge(
            created["handoff_id"],
            consumer_id="desktop:lease-two",
            status="applied",
        )
        self.assertEqual(applied.status_code, 200, msg=applied.text)
        self.assertEqual(duplicate_applied.status_code, 200, msg=duplicate_applied.text)

        cancel_created = self._present_professors(
            {"selection": {"mode": "ids", "ids": [professor_id]}},
        ).json()
        canceled = self.client.post(
            f"/api/agent/v1/ui-handoffs/{cancel_created['handoff_id']}/cancel",
            headers=self._agent_headers(),
        )
        canceled_again = self.client.post(
            f"/api/agent/v1/ui-handoffs/{cancel_created['handoff_id']}/cancel",
            headers=self._agent_headers(),
        )
        self.assertEqual(canceled.status_code, 200, msg=canceled.text)
        self.assertEqual(canceled_again.status_code, 200, msg=canceled_again.text)
        self.assertEqual(canceled.json()["status"], "canceled")

        expired_created = self._present_professors(
            {"selection": {"mode": "ids", "ids": [professor_id]}},
        ).json()
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute(
                "UPDATE agent_ui_handoffs SET expires_at = ? WHERE id = ?",
                ("2000-01-01 00:00:00.000000", expired_created["handoff_id"]),
            )
        expired = self.client.get(
            f"/api/agent/v1/ui-handoffs/{expired_created['handoff_id']}",
            headers=self._agent_headers(),
        )
        self.assertEqual(expired.status_code, 200, msg=expired.text)
        self.assertEqual(expired.json()["status"], "expired")
        unavailable_page = self.client.post(
            "/api/professors/search/management",
            headers=self._ui_headers(),
            json={"ui_handoff_id": expired_created["handoff_id"]},
        )
        self.assertEqual(unavailable_page.status_code, 410, msg=unavailable_page.text)
        self.assertEqual(
            unavailable_page.json()["error"]["code"],
            "UI_HANDOFF_EXPIRED",
        )

    def test_concurrent_claim_has_exactly_one_winner(self) -> None:
        professor_id = self._create_professor(email="concurrent-ui-handoff@example.edu")
        created = self._present_professors(
            {"selection": {"mode": "ids", "ids": [professor_id]}},
        ).json()

        def claim(consumer_id: str) -> tuple[int, dict[str, object] | None]:
            response = self._claim(consumer_id)
            return response.status_code, response.json() if response.content else None

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(claim, ["desktop:concurrent-a", "desktop:concurrent-b"])
            )

        self.assertEqual(sorted(status for status, _ in results), [200, 204])
        winner = next(body for status, body in results if status == 200)
        assert winner is not None
        self.assertEqual(winner["handoff_id"], created["handoff_id"])
        self.assertEqual(winner["delivery_attempts"], 1)

    def test_cancel_and_acknowledgement_race_has_one_authoritative_winner(self) -> None:
        professor_id = self._create_professor(
            email="cancel-ack-race-ui-handoff@example.edu",
        )
        created = self._present_professors(
            {"selection": {"mode": "ids", "ids": [professor_id]}},
        ).json()
        claim = self._claim("desktop:cancel-ack-race")
        self.assertEqual(claim.status_code, 200, msg=claim.text)

        def cancel() -> tuple[int, dict[str, object]]:
            response = self.client.post(
                f"/api/agent/v1/ui-handoffs/{created['handoff_id']}/cancel",
                headers=self._agent_headers(),
            )
            return response.status_code, response.json()

        def acknowledge() -> tuple[int, dict[str, object]]:
            response = self._acknowledge(
                created["handoff_id"],
                consumer_id="desktop:cancel-ack-race",
                status="applied",
            )
            return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=2) as executor:
            cancel_future = executor.submit(cancel)
            acknowledge_future = executor.submit(acknowledge)
            results = [cancel_future.result(), acknowledge_future.result()]

        self.assertEqual(sorted(status_code for status_code, _ in results), [200, 409])
        final = self.client.get(
            f"/api/agent/v1/ui-handoffs/{created['handoff_id']}",
            headers=self._agent_headers(),
        )
        self.assertEqual(final.status_code, 200, msg=final.text)
        final_status = final.json()["status"]
        self.assertIn(final_status, {"applied", "canceled"})
        winner = next(body for status_code, body in results if status_code == 200)
        self.assertEqual(winner["status"], final_status)

    def test_expired_cancel_retry_and_acknowledgement_persist_expired_state(
        self,
    ) -> None:
        professor_id = self._create_professor(
            email="expired-actions-ui-handoff@example.edu",
        )

        cancel_handoff = self._present_professors(
            {"selection": {"mode": "ids", "ids": [professor_id]}},
        ).json()
        self._expire_handoff(cancel_handoff["handoff_id"])
        cancel = self.client.post(
            f"/api/agent/v1/ui-handoffs/{cancel_handoff['handoff_id']}/cancel",
            headers=self._agent_headers(),
        )
        self.assertEqual(cancel.status_code, 409, msg=cancel.text)
        self.assertEqual(cancel.json()["error"]["code"], "UI_HANDOFF_EXPIRED")
        self.assertEqual(
            self.client.get(
                f"/api/agent/v1/ui-handoffs/{cancel_handoff['handoff_id']}",
                headers=self._agent_headers(),
            ).json()["status"],
            "expired",
        )

        retry_handoff = self._present_professors(
            {"selection": {"mode": "ids", "ids": [professor_id]}},
        ).json()
        retry_claim = self._claim("desktop:expired-retry").json()
        awaiting = self._acknowledge(
            retry_handoff["handoff_id"],
            consumer_id="desktop:expired-retry",
            status="awaiting_user",
        )
        self.assertEqual(awaiting.status_code, 200, msg=awaiting.text)
        self._expire_handoff(retry_handoff["handoff_id"])
        retry = self.client.post(
            f"/api/agent/v1/ui-handoffs/{retry_handoff['handoff_id']}/retry",
            headers=self._agent_headers(),
        )
        self.assertEqual(retry.status_code, 409, msg=retry.text)
        self.assertEqual(retry.json()["error"]["code"], "UI_HANDOFF_EXPIRED")
        self.assertEqual(retry_claim["handoff_id"], retry_handoff["handoff_id"])

        ack_handoff = self._present_professors(
            {"selection": {"mode": "ids", "ids": [professor_id]}},
        ).json()
        ack_claim = self._claim("desktop:expired-ack").json()
        self.assertEqual(ack_claim["handoff_id"], ack_handoff["handoff_id"])
        self._expire_handoff(ack_handoff["handoff_id"])
        acknowledgement = self._acknowledge(
            ack_handoff["handoff_id"],
            consumer_id="desktop:expired-ack",
            status="applied",
        )
        self.assertEqual(acknowledgement.status_code, 409, msg=acknowledgement.text)
        self.assertEqual(
            acknowledgement.json()["error"]["code"],
            "UI_HANDOFF_EXPIRED",
        )
        self.assertEqual(
            self.client.get(
                f"/api/agent/v1/ui-handoffs/{ack_handoff['handoff_id']}",
                headers=self._agent_headers(),
            ).json()["status"],
            "expired",
        )

    def test_claim_housekeeping_persists_failed_expiry_before_next_claim(self) -> None:
        professor_id = self._create_professor(
            email="claim-housekeeping-ui-handoff@example.edu",
        )
        failed_handoff = self._present_professors(
            {"selection": {"mode": "ids", "ids": [professor_id]}},
        ).json()
        failed_claim = self._claim("desktop:failed-housekeeping")
        self.assertEqual(failed_claim.status_code, 200, msg=failed_claim.text)
        failed = self._acknowledge(
            failed_handoff["handoff_id"],
            consumer_id="desktop:failed-housekeeping",
            status="failed",
            failure_message="page no longer supports this resource",
        )
        self.assertEqual(failed.status_code, 200, msg=failed.text)
        self._expire_handoff(failed_handoff["handoff_id"])

        pending_handoff = self._present_professors(
            {"selection": {"mode": "ids", "ids": [professor_id]}},
        ).json()
        next_claim = self._claim("desktop:next-housekeeping")
        self.assertEqual(next_claim.status_code, 200, msg=next_claim.text)
        self.assertEqual(next_claim.json()["handoff_id"], pending_handoff["handoff_id"])

        with closing(sqlite3.connect(self.db_path)) as connection:
            failed_status = connection.execute(
                "SELECT status FROM agent_ui_handoffs WHERE id = ?",
                (failed_handoff["handoff_id"],),
            ).fetchone()[0]
        self.assertEqual(failed_status, "expired")

    def test_claim_lease_never_outlives_the_handoff(self) -> None:
        professor_id = self._create_professor(
            email="bounded-lease-ui-handoff@example.edu",
        )
        created = self._present_professors(
            {"selection": {"mode": "ids", "ids": [professor_id]}},
        ).json()
        shortened_expiry = datetime.now(timezone.utc) + timedelta(seconds=5)
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute(
                "UPDATE agent_ui_handoffs SET expires_at = ? WHERE id = ?",
                (shortened_expiry.isoformat(), created["handoff_id"]),
            )

        claim = self._claim("desktop:bounded-lease")
        self.assertEqual(claim.status_code, 200, msg=claim.text)
        claimed = claim.json()
        self.assertLessEqual(
            datetime.fromisoformat(claimed["claim_expires_at"]),
            datetime.fromisoformat(claimed["expires_at"]),
        )

    def test_task_crawl_thread_and_draft_surfaces_freeze_context(self) -> None:
        identity_id = self._create_identity(email="surface-sender@example.com")
        professor_id = self._create_professor(email="surface-professor@example.edu")
        llm_profile_id = self._create_llm_profile()
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            task_id = connection.execute(
                """
                INSERT INTO email_tasks (source, identity_id, llm_profile_id, professor_id, status)
                VALUES ('manual', ?, ?, ?, 'review_required')
                RETURNING id
                """,
                (identity_id, llm_profile_id, professor_id),
            ).fetchone()[0]
            job_id = connection.execute(
                """
                INSERT INTO crawl_jobs (university, school, start_url)
                VALUES ('Example University', 'Engineering', 'https://example.edu/faculty')
                RETURNING id
                """,
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO email_logs (identity_id, professor_id, direction, content)
                VALUES (?, ?, 'sent', 'hello')
                """,
                (identity_id, professor_id),
            )

        cases = [
            (
                f"/api/agent/v1/tasks/{task_id}/present",
                "tasks.center",
                {
                    "task_id": task_id,
                    "identity_id": identity_id,
                    "professor_id": professor_id,
                },
            ),
            (
                f"/api/agent/v1/drafts/{task_id}/present",
                "draft.workspace",
                {
                    "task_id": task_id,
                    "identity_id": identity_id,
                    "professor_id": professor_id,
                },
            ),
            (
                f"/api/agent/v1/crawler/jobs/{job_id}/present",
                "crawler.job",
                {"job_id": job_id},
            ),
            (
                f"/api/agent/v1/communications/threads/{identity_id}:{professor_id}/present",
                "communications.thread",
                {"identity_id": identity_id, "professor_id": professor_id},
            ),
        ]
        for index, (path, expected_surface, expected_payload) in enumerate(cases):
            with self.subTest(surface=expected_surface):
                response = self.client.post(
                    path,
                    headers={
                        **self._agent_headers(),
                        "Idempotency-Key": f"surface-{index}",
                    },
                )
                self.assertEqual(response.status_code, 201, msg=response.text)
                self.assertEqual(response.json()["surface"], expected_surface)
                claim = self._claim(f"desktop:surface-{index}")
                self.assertEqual(claim.status_code, 200, msg=claim.text)
                self.assertEqual(claim.json()["surface"], expected_surface)
                for key, value in expected_payload.items():
                    self.assertEqual(claim.json()["payload"][key], value)
                ack = self._acknowledge(
                    claim.json()["handoff_id"],
                    consumer_id=f"desktop:surface-{index}",
                    status="applied",
                )
                self.assertEqual(ack.status_code, 200, msg=ack.text)

        missing_cases = [
            "/api/agent/v1/tasks/999999/present",
            "/api/agent/v1/drafts/999999/present",
            "/api/agent/v1/crawler/jobs/999999/present",
            f"/api/agent/v1/communications/threads/{identity_id}:999999/present",
        ]
        for path in missing_cases:
            with self.subTest(path=path):
                response = self.client.post(path, headers=self._agent_headers())
                self.assertEqual(response.status_code, 404, msg=response.text)

    def _present_professors(
        self,
        payload: dict[str, object],
        *,
        idempotency_key: str | None = None,
    ):
        headers = self._agent_headers()
        if idempotency_key is not None:
            headers = {**headers, "Idempotency-Key": idempotency_key}
        return self.client.post(
            "/api/agent/v1/professors/present-selection",
            headers=headers,
            json=payload,
        )

    def _claim(self, consumer_id: str):
        return self.client.post(
            "/api/agent/v1/ui-handoffs/claim-next",
            headers=self._agent_headers(),
            json={"consumer_id": consumer_id},
        )

    def _acknowledge(
        self,
        handoff_id: str,
        *,
        consumer_id: str,
        status: str,
        result: dict[str, object] | None = None,
        failure_message: str | None = None,
    ):
        payload: dict[str, object] = {
            "consumer_id": consumer_id,
            "status": status,
            "result": result or {},
        }
        if failure_message is not None:
            payload["failure_message"] = failure_message
        return self.client.post(
            f"/api/agent/v1/ui-handoffs/{handoff_id}/acknowledge",
            headers=self._agent_headers(),
            json=payload,
        )

    def _create_identity(self, *, email: str = "handoff-sender@example.com") -> int:
        response = self.client.post(
            "/api/identities",
            headers=self._ui_headers(),
            json={
                "name": f"UI Handoff {email}",
                "profile_name": f"UI Handoff {email}",
                "sender_name": "Test Sender",
                "email_address": email,
                "smtp_host": "smtp.example.com",
                "smtp_port": 465,
                "smtp_username": email,
                "smtp_password": "smtp-secret-value",
                "imap_host": "imap.example.com",
                "imap_port": 993,
                "imap_username": email,
                "imap_password": "imap-secret-value",
                "default_language": "zh-CN",
                "is_default": True,
            },
        )
        self.assertEqual(response.status_code, 201, msg=response.text)
        return response.json()["id"]

    def _create_llm_profile(self) -> int:
        response = self.client.post(
            "/api/llm-profiles",
            headers=self._ui_headers(),
            json={
                "name": "UI Handoff Model",
                "provider": "openai",
                "api_base_url": "https://api.example.com/v1",
                "api_key": "llm-secret-value",
                "model_name": "test-model",
                "is_default": True,
            },
        )
        self.assertEqual(response.status_code, 201, msg=response.text)
        return response.json()["id"]

    def _create_professor(self, *, name: str = "Test Professor", email: str) -> int:
        response = self.client.post(
            "/api/professors",
            headers=self._ui_headers(),
            json={
                "name": name,
                "email": email,
                "university": "Example University",
                "research_direction": "Agent systems",
            },
        )
        self.assertEqual(response.status_code, 201, msg=response.text)
        return response.json()["id"]

    def _expire_handoff(self, handoff_id: str) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute(
                "UPDATE agent_ui_handoffs SET expires_at = ? WHERE id = ?",
                ("2000-01-01 00:00:00.000000", handoff_id),
            )

    @staticmethod
    def _ui_headers() -> dict[str, str]:
        return {"Authorization": f"Bearer {UI_TOKEN}"}

    @staticmethod
    def _agent_headers() -> dict[str, str]:
        return {"Authorization": f"Bearer {AGENT_TOKEN}"}


if __name__ == "__main__":
    unittest.main()
