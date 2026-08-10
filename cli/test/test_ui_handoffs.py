from __future__ import annotations

import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from typer.testing import CliRunner

from auto_email_sender_cli.capabilities import search_capability_cards
from auto_email_sender_cli.commands.common import augment_state_metadata
from auto_email_sender_cli.describe import describe_command
from auto_email_sender_cli.main import app


class _HandoffClient:
    calls: list[tuple[str, str, dict[str, object]]] = []
    responses: list[dict[str, object]] = []
    descriptor = SimpleNamespace(app_version="test")
    last_request_id = "backend-request"
    last_response_headers: dict[str, str] = {}

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def request(self, method: str, path: str, **kwargs: object) -> dict[str, object]:
        self.__class__.calls.append((method, path, kwargs))
        if self.__class__.responses:
            return self.__class__.responses.pop(0)
        return _handoff_response()


def _handoff_response(
    *,
    status: str = "pending",
    handoff_id: str = "uih_test",
) -> dict[str, object]:
    actions = {
        "pending": ["read", "wait", "cancel"],
        "claimed": ["read", "wait", "cancel"],
        "awaiting_user": ["read", "retry", "cancel"],
        "failed": ["read", "retry"],
        "applied": ["read"],
    }
    return {
        "handoff_id": handoff_id,
        "schema_version": 1,
        "surface": "professors.management",
        "route": "/professors",
        "status": status,
        "selection_count": 2,
        "selection_fingerprint": "frozen-hash",
        "ui_effects": ["focus_window", "navigate", "replace_selection"],
        "result": None,
        "failure_message": None,
        "delivery_attempts": 0,
        "expires_at": "2026-08-10T12:30:00Z",
        "claimed_at": None,
        "awaiting_user_at": None,
        "applied_at": None,
        "failed_at": None,
        "canceled_at": None,
        "created_at": "2026-08-10T12:00:00Z",
        "updated_at": "2026-08-10T12:00:00Z",
        "idempotent_replay": False,
        "available_actions": actions.get(status, ["read"]),
    }


class UiHandoffCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()
        _HandoffClient.calls = []
        _HandoffClient.responses = []
        self.client_patch = patch(
            "auto_email_sender_cli.commands.ui_handoffs.AgentApiClient",
            _HandoffClient,
        )
        self.common_client_patch = patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            _HandoffClient,
        )
        self.client_patch.start()
        self.common_client_patch.start()

    def tearDown(self) -> None:
        self.common_client_patch.stop()
        self.client_patch.stop()

    def test_professor_selection_builds_frozen_filter_handoff(self) -> None:
        result = self.runner.invoke(
            app,
            [
                "--format",
                "json",
                "--request-id",
                "agent-filter-selection",
                "professors",
                "present-selection",
                "--selection-filter",
                '{"name":{"contains_script":"latin"}}',
                "--exclude-id",
                "9",
                "--surface",
                "home",
                "--identity-id",
                "3",
                "--selection-mode",
                "add",
                "--display",
                "keep-current",
            ],
        )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        method, path, kwargs = _HandoffClient.calls[0]
        self.assertEqual((method, path), ("POST", "/api/agent/v1/professors/present-selection"))
        self.assertEqual(kwargs["idempotency_key"], "agent-filter-selection")
        self.assertEqual(
            kwargs["json_body"],
            {
                "selection": {
                    "mode": "filter",
                    "filter": {
                        "archived": "active",
                        "where": {"name": {"contains_script": "latin"}},
                    },
                    "exclude_ids": [9],
                },
                "surface": "professors.home",
                "selection_mode": "add",
                "display": "keep_current",
                "identity_id": 3,
            },
        )
        payload = json.loads(result.output)
        self.assertNotIn("mutation_receipt", payload["data"])
        actions = {item["action"]: item for item in payload["data"]["available_actions"]}
        self.assertEqual(actions["wait"]["command"], "ui-handoffs.wait")
        self.assertEqual(actions["cancel"]["arguments"], {"handoff_id": "uih_test"})

    def test_professor_selection_validates_modes_and_scoped_all(self) -> None:
        invalid_cases = [
            ["professors", "present-selection", "--all", "--professor-id", "1"],
            ["professors", "present-selection", "--professor-id", "1", "--surface", "home"],
            [
                "professors",
                "present-selection",
                "--professor-id",
                "1",
                "--surface",
                "management",
                "--identity-id",
                "2",
            ],
            [
                "professors",
                "present-selection",
                "--all",
                "--archived",
                "archived",
                "--surface",
                "home",
                "--identity-id",
                "2",
            ],
            [
                "professors",
                "present-selection",
                "--professor-id",
                "1",
                "--exclude-id",
                "2",
            ],
        ]
        for arguments in invalid_cases:
            with self.subTest(arguments=arguments):
                result = self.runner.invoke(app, ["--format", "json", *arguments])
                self.assertEqual(result.exit_code, 2, msg=result.output)
        self.assertEqual(_HandoffClient.calls, [])

        archived_all = self.runner.invoke(
            app,
            [
                "--format",
                "json",
                "professors",
                "present-selection",
                "--all",
                "--archived",
                "archived",
            ],
        )
        self.assertEqual(archived_all.exit_code, 0, msg=archived_all.output)
        selection = _HandoffClient.calls[-1][2]["json_body"]["selection"]
        self.assertEqual(
            selection,
            {"mode": "filter", "filter": {"archived": "archived"}, "exclude_ids": []},
        )

    def test_resource_present_commands_use_exact_routes(self) -> None:
        cases = [
            (["tasks", "present", "11"], "/api/agent/v1/tasks/11/present"),
            (["drafts", "present", "12"], "/api/agent/v1/drafts/12/present"),
            (["crawler", "jobs", "present", "13"], "/api/agent/v1/crawler/jobs/13/present"),
            (
                ["communications", "threads", "present", "2:17"],
                "/api/agent/v1/communications/threads/2:17/present",
            ),
        ]
        for arguments, expected_path in cases:
            with self.subTest(arguments=arguments):
                result = self.runner.invoke(app, ["--format", "json", *arguments])
                self.assertEqual(result.exit_code, 0, msg=result.output)
                self.assertEqual(_HandoffClient.calls[-1][1], expected_path)

    def test_get_cancel_retry_and_wait_publish_executable_actions(self) -> None:
        get_result = self.runner.invoke(
            app,
            ["--format", "json", "ui-handoffs", "get", "uih_test"],
        )
        self.assertEqual(get_result.exit_code, 0, msg=get_result.output)
        self.assertEqual(_HandoffClient.calls[-1][:2], ("GET", "/api/agent/v1/ui-handoffs/uih_test"))

        for command in ("cancel", "retry"):
            _HandoffClient.responses = [
                _handoff_response(status="canceled" if command == "cancel" else "pending"),
            ]
            result = self.runner.invoke(
                app,
                ["--format", "json", "ui-handoffs", command, "uih_test"],
            )
            self.assertEqual(result.exit_code, 0, msg=result.output)
            self.assertTrue(_HandoffClient.calls[-1][1].endswith(f"/{command}"))

        _HandoffClient.responses = [_handoff_response(status="applied")]
        waited = self.runner.invoke(
            app,
            [
                "--format",
                "json",
                "ui-handoffs",
                "wait",
                "uih_test",
                "--until",
                "applied",
                "--timeout-seconds",
                "0",
            ],
        )
        self.assertEqual(waited.exit_code, 0, msg=waited.output)
        waited_payload = json.loads(waited.output)["data"]
        self.assertTrue(waited_payload["settled"])
        self.assertTrue(waited_payload["terminal"])
        self.assertTrue(waited_payload["condition_met"])
        self.assertFalse(waited_payload["timed_out"])
        self.assertEqual(waited_payload["poll_count"], 1)

    def test_wait_for_applied_stops_immediately_on_unmet_terminal_status(self) -> None:
        for status in ("failed", "canceled", "expired"):
            with self.subTest(status=status):
                _HandoffClient.calls = []
                _HandoffClient.responses = [_handoff_response(status=status)]
                result = self.runner.invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "ui-handoffs",
                        "wait",
                        "uih_test",
                        "--until",
                        "applied",
                        "--timeout-seconds",
                        "30",
                    ],
                )

                self.assertEqual(result.exit_code, 0, msg=result.output)
                payload = json.loads(result.output)
                data = payload["data"]
                self.assertEqual(data["status"], status)
                self.assertTrue(data["settled"])
                self.assertTrue(data["terminal"])
                self.assertFalse(data["condition_met"])
                self.assertFalse(data["timed_out"])
                self.assertEqual(data["poll_count"], 1)
                self.assertEqual(len(_HandoffClient.calls), 1)
                self.assertIn("无法自行满足 applied", payload["_meta"]["warnings"][0])

    def test_contract_and_intent_discovery_explain_ui_only_effect(self) -> None:
        description = describe_command(app, "professors.present-selection")
        self.assertIsNotNone(description)
        assert description is not None
        self.assertFalse(description["effects"]["mutates"])
        self.assertEqual(description["effects"]["external_services"], [])
        self.assertEqual(
            description["effects"]["ui_effects"],
            ["focus_window", "navigate", "apply_temporary_ui_state"],
        )
        self.assertTrue(description["effects"]["requires_explicit_user_intent"])

        wait_description = describe_command(app, "ui-handoffs.wait")
        self.assertIsNotNone(wait_description)
        assert wait_description is not None
        self.assertTrue(
            {
                "condition_met",
                "timed_out",
                "until",
                "poll_count",
                "elapsed_seconds",
            }.issubset(wait_description["output"]["known_fields"]),
        )
        wait_properties = wait_description["output"]["schema"]["properties"]["data"][
            "properties"
        ]
        self.assertEqual(wait_properties["schema_version"]["type"], "integer")
        self.assertEqual(
            wait_properties["delivery_attempts"]["type"],
            ["integer", "null"],
        )
        self.assertEqual(wait_properties["idempotent_replay"]["type"], "boolean")
        professor_description = describe_command(app, "professors.get")
        self.assertIsNotNone(professor_description)
        assert professor_description is not None
        self.assertIsNotNone(professor_description["output"]["state_metadata"])

        matches = search_capability_cards(
            "只筛选出名字有英文的导师，在软件页面里勾选，不要后续操作",
            limit=3,
        )
        self.assertEqual(matches[0]["command"], "professors.present-selection")
        self.assertEqual(matches[0]["match"]["confidence"], "high")

    def test_existing_resource_results_offer_present_in_app_action(self) -> None:
        cases = [
            (
                "professors.get",
                {"id": 6, "name": "Ada Lovelace"},
                "professors.present-selection",
                {"professor_ids": [6]},
            ),
            (
                "crawler.jobs.get",
                {"id": 7, "status": "running"},
                "crawler.jobs.present",
                {"job_id": 7},
            ),
            (
                "drafts.get",
                {"task_id": 8, "status": "review_required"},
                "drafts.present",
                {"task_id": 8},
            ),
            (
                "communications.threads.get",
                {"id": "2:17", "identity_id": 2, "professor_id": 17},
                "communications.threads.present",
                {"thread_id": "2:17"},
            ),
        ]
        for command, value, target, arguments in cases:
            with self.subTest(command=command):
                augmented = augment_state_metadata(value, command=command)
                action = next(
                    item
                    for item in augmented["available_actions"]
                    if item["action"] == "present-in-app"
                )
                self.assertEqual(action["command"], target)
                self.assertEqual(action["arguments"], arguments)


if __name__ == "__main__":
    unittest.main()
