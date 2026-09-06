from __future__ import annotations

import json
import runpy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from auto_email_sender_cli.describe import compact_command_description, describe_command
from auto_email_sender_cli.main import app
from typer.testing import CliRunner


class AgentWorkflowTests(unittest.TestCase):
    def test_returned_context_supports_execution_and_recovery_without_rediscovery(
        self,
    ) -> None:
        script = (
            Path(__file__).resolve().parents[2]
            / "scripts/quality/benchmark_agent_workflows.py"
        )
        result = runpy.run_path(str(script))["run_workflows"]()
        self.assertEqual(result["unexpected_errors"], 0)
        self.assertLess(result["total_context_bytes"], 20_000)
        self.assertTrue(
            all(item["contract_lookups"] == 0 for item in result["scenarios"])
        )
        self.assertTrue(all(item["calls"] <= 3 for item in result["scenarios"]))

    def test_execution_card_preserves_material_semantics_and_clear_constraints(
        self,
    ) -> None:
        generate = compact_command_description(describe_command(app, "drafts.generate"))
        options = generate["input"]["optional_contracts"]
        self.assertIn("不会作为附件", options["reference_material_id"]["description"])
        self.assertIn(
            "不会自动供 AI", options["attachment_material_ids"]["description"]
        )
        self.assertIn("默认模板", options["template_id"]["description"])
        save = compact_command_description(describe_command(app, "drafts.save"))
        self.assertEqual(
            save["input"]["constraints"][0]["mutually_exclusive"],
            ["clear_attachments", "attachment_material_ids"],
        )
        result = CliRunner().invoke(
            app,
            ["--json", "invoke", "--command", "drafts.save", "--input", "-"],
            input=json.dumps(
                {
                    "task_id": 1,
                    "clear_attachments": True,
                    "attachment_material_ids": [1],
                }
            ),
        )
        self.assertEqual(result.exit_code, 2)
        self.assertFalse(json.loads(result.stdout)["ok"])

    def test_discovery_cache_distinguishes_diagnostics_and_attached_contract(
        self,
    ) -> None:
        runner = CliRunner()

        def discover(*extra):
            result = runner.invoke(
                app,
                [
                    "--json",
                    "capabilities",
                    "--intent",
                    "生成草稿",
                    "--limit",
                    "1",
                    *extra,
                ],
            )
            self.assertEqual(result.exit_code, 0, result.output)
            return json.loads(result.stdout)["data"]

        initial = discover("--with-contract")
        self.assertEqual(
            initial["execution_contract"]["contract_revision"],
            initial["items"][0]["contract_revision"],
        )
        cached = discover("--with-contract", "--since", initial["scope_revision"])
        self.assertEqual(cached["cache"]["status"], "not_modified")
        self.assertNotIn("execution_contract", cached)
        plain = discover()
        detailed = discover("--diagnostics")
        self.assertNotEqual(plain["scope_revision"], detailed["scope_revision"])
        self.assertNotIn("build", plain)
        self.assertIn("score", detailed["items"][0]["match"])
        self.assertNotIn("score", plain["items"][0]["match"])

    def test_successful_small_write_needs_no_followup_to_recover_duplicate_snapshot(
        self,
    ) -> None:
        client = Mock()
        client.descriptor = SimpleNamespace(app_version="fixture")
        client.last_request_id = None
        client.last_response_headers = {}
        client.request.return_value = {"id": 7, "name": "Updated"}
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient", return_value=client
        ):
            result = CliRunner().invoke(
                app, ["--json", "professors", "update", "7", "--name", "Updated"]
            )
        self.assertEqual(result.exit_code, 0, result.output)
        data = json.loads(result.stdout)["data"]
        self.assertEqual(data["name"], "Updated")
        self.assertEqual(
            data["mutation_receipt"]["changed_resources"][0]["changed_fields"], ["name"]
        )
        self.assertNotIn("truncated", data)
        self.assertNotIn("projection", data)
