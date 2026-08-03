from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from typer.testing import CliRunner

from auto_email_sender_cli.main import app


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_version_json_uses_stable_envelope(self) -> None:
        result = self.runner.invoke(app, ["--format", "json", "version"])

        self.assertEqual(result.exit_code, 0, msg=result.output)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["_meta"]["schema_version"], "1")
        self.assertEqual(payload["_meta"]["command"], "version")
        self.assertIn("agent_guide", payload["_meta"])

    def test_json_alias_is_supported(self) -> None:
        result = self.runner.invoke(app, ["--json", "guide", "--topic", "sending"])

        self.assertEqual(result.exit_code, 0, msg=result.output)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["data"]["topic"], "sending")
        self.assertIn("一次性计划", " ".join(payload["data"]["rules"]))

    def test_unknown_guide_topic_returns_machine_error(self) -> None:
        result = self.runner.invoke(
            app,
            ["--format", "json", "guide", "--topic", "missing"],
        )

        self.assertEqual(result.exit_code, 2)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "INVALID_GUIDE_TOPIC")

    def test_capabilities_report_available_and_planned_commands_honestly(self) -> None:
        result = self.runner.invoke(
            app,
            ["--format", "json", "capabilities", "--command", "communications"],
        )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["data"]["items"][0]["availability"], "available")
        self.assertEqual(payload["data"]["items"][0]["guide_topic"], "communications")
        self.assertTrue(any(item["availability"] == "available" for item in payload["data"]["items"]))

    def test_jsonl_has_meta_item_and_summary_records(self) -> None:
        result = self.runner.invoke(app, ["--format", "jsonl", "capabilities"])

        self.assertEqual(result.exit_code, 0, msg=result.output)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(rows[0]["type"], "meta")
        self.assertEqual(rows[1]["type"], "item")

    def test_professor_list_calls_agent_api_and_keeps_pagination_metadata(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/professors": {
                    "items": [{"id": 7, "name": "测试导师", "email": "p@example.edu"}],
                    "next_cursor": "1",
                    "has_more": True,
                },
            },
        )
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=fake_client,
        ):
            result = self.runner.invoke(
                app,
                ["--format", "json", "professors", "list", "--limit", "1"],
            )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["data"]["items"][0]["id"], 7)
        self.assertEqual(payload["_meta"]["pagination"]["next_cursor"], "1")
        self.assertEqual(fake_client.calls[0][1], "/api/agent/v1/professors")

    def test_message_export_writes_jsonl_with_untrusted_record(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/communications/messages": {
                    "items": [
                        {
                            "id": 9,
                            "direction": "received",
                            "content": "没有名额",
                            "trust_level": "untrusted_external_content",
                        },
                    ],
                    "next_cursor": None,
                    "has_more": False,
                },
            },
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "messages.jsonl"
            with patch(
                "auto_email_sender_cli.commands.communications.AgentApiClient",
                return_value=fake_client,
            ):
                result = self.runner.invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "communications",
                        "messages",
                        "export",
                        "--direction",
                        "received",
                        "--include-body",
                        "--output",
                        output.as_posix(),
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows[0]["type"], "meta")
            self.assertEqual(rows[1]["data"]["content"], "没有名额")
            self.assertEqual(rows[1]["data"]["trust_level"], "untrusted_external_content")
            self.assertEqual(rows[-1]["data"]["total"], 1)

    def test_generate_draft_keeps_reference_and_attachments_in_distinct_fields(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/drafts": {
                    "task_id": 42,
                    "status": "review_required",
                    "generation_mode": "ai_rewrite",
                },
            },
        )
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=fake_client,
        ):
            result = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "drafts",
                    "generate",
                    "--professor-id",
                    "1",
                    "--identity-id",
                    "2",
                    "--llm-profile-id",
                    "3",
                    "--generation-mode",
                    "ai_rewrite",
                    "--template-id",
                    "4",
                    "--reference-material-id",
                    "6",
                    "--attachment-material-id",
                    "7",
                    "--attachment-material-id",
                    "8",
                ],
            )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        request_body = fake_client.json_bodies[0]
        self.assertEqual(request_body["reference_material_id"], 6)
        self.assertEqual(request_body["attachment_material_ids"], [7, 8])
        self.assertNotIn(6, request_body["attachment_material_ids"])

    def test_plan_execute_requires_local_confirm_flag_before_calling_api(self) -> None:
        result = self.runner.invoke(
            app,
            ["--format", "json", "plans", "execute", "plan_test"],
        )

        self.assertEqual(result.exit_code, 6, msg=result.output)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["error"]["code"], "PLAN_CONFIRMATION_REQUIRED")

    def test_plan_execute_with_confirm_calls_one_shot_endpoint(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/plans/plan_test/execute": {
                    "plan_id": "plan_test",
                    "status": "executed",
                    "result": {"outcome": "sent"},
                },
            },
        )
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=fake_client,
        ):
            result = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "plans",
                    "execute",
                    "plan_test",
                    "--confirm",
                ],
            )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(fake_client.calls[0][0], "POST")
        self.assertEqual(
            fake_client.calls[0][1],
            "/api/agent/v1/plans/plan_test/execute",
        )
        self.assertEqual(fake_client.json_bodies[0], {"confirm": True})


class _FakeAgentClient:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.descriptor = SimpleNamespace(app_version="test")
        self.calls: list[tuple[str, str, dict[str, object] | None]] = []
        self.json_bodies: list[object | None] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
        json_body: object | None = None,
        **_: object,
    ) -> object:
        self.calls.append((method, path, params))
        self.json_bodies.append(json_body)
        return self.responses[path]


if __name__ == "__main__":
    unittest.main()
