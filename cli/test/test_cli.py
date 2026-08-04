from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from typer.testing import CliRunner

from auto_email_sender_cli.main import app
from auto_email_sender_cli.agent_installation import inspect_agent_skill_installation
from auto_email_sender_cli.capabilities import CAPABILITIES
from auto_email_sender_cli.describe import describe_command


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

    def test_capabilities_mark_desktop_only_areas_as_unavailable(self) -> None:
        result = self.runner.invoke(
            app,
            ["--format", "json", "capabilities"],
        )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        items = json.loads(result.stdout)["data"]["items"]
        by_command = {item["command"]: item for item in items}
        self.assertEqual(by_command["professors.create"]["availability"], "available")
        self.assertEqual(by_command["professors.export"]["availability"], "available")
        self.assertEqual(by_command["templates.import-file"]["availability"], "available")
        self.assertEqual(by_command["drafts.rewrite"]["availability"], "available")
        self.assertTrue(by_command["drafts.rewrite"]["external_action"])
        self.assertEqual(by_command["materials.prepare-delete"]["availability"], "available")
        self.assertEqual(by_command["communications.sync"]["availability"], "available")
        self.assertEqual(
            by_command["professors.tags.prepare-bulk"]["availability"],
            "available",
        )
        self.assertEqual(by_command["professors.tags.usage"]["availability"], "available")
        self.assertTrue(by_command["professors.tags.prepare-delete"]["requires_plan"])
        self.assertTrue(by_command["professors.prepare-bulk-archive"]["requires_plan"])
        self.assertEqual(by_command["professors.import"]["availability"], "available")
        self.assertEqual(
            by_command["professors.community.import"]["availability"],
            "available",
        )
        self.assertTrue(by_command["professors.community.import"]["requires_plan"])
        self.assertEqual(
            by_command["professors.community.export-package"]["availability"],
            "available",
        )
        self.assertEqual(by_command["matching.jobs.create"]["availability"], "available")
        self.assertEqual(by_command["matching.jobs.create"]["risk_level"], "L2")
        self.assertTrue(by_command["matching.jobs.create"]["external_action"])
        self.assertEqual(by_command["crawler.jobs.approve"]["availability"], "available")
        self.assertTrue(by_command["crawler.jobs.approve"]["requires_plan"])
        self.assertEqual(by_command["crawler.jobs.events"]["availability"], "available")
        self.assertEqual(by_command["crawler.jobs.retry"]["availability"], "available")
        self.assertTrue(by_command["crawler.jobs.retry"]["requires_plan"])
        self.assertEqual(by_command["crawler.jobs.enrich"]["availability"], "available")
        self.assertTrue(by_command["crawler.jobs.enrich"]["external_action"])
        self.assertEqual(by_command["campaigns.create"]["availability"], "available")
        self.assertTrue(by_command["campaigns.create"]["requires_plan"])
        self.assertEqual(by_command["campaigns.resend-context"]["availability"], "available")
        self.assertEqual(by_command["campaigns.stop"]["availability"], "available")
        self.assertEqual(by_command["campaigns.remove-item"]["risk_level"], "L1")
        self.assertEqual(
            by_command["campaigns.prepare-restore-item-send"]["risk_level"],
            "L3",
        )
        self.assertTrue(
            by_command["campaigns.prepare-restore-item-send"]["requires_plan"],
        )
        self.assertEqual(by_command["campaigns.retry-item-draft"]["risk_level"], "L2")
        self.assertTrue(by_command["campaigns.retry-item-draft"]["external_action"])
        self.assertEqual(by_command["campaigns.prepare-resume"]["risk_level"], "L3")
        self.assertTrue(by_command["campaigns.prepare-resume"]["requires_plan"])
        self.assertEqual(by_command["campaigns.prepare-send"]["risk_level"], "L3")
        self.assertTrue(by_command["campaigns.prepare-send"]["requires_plan"])
        self.assertEqual(by_command["enrichment.jobs.create"]["availability"], "available")
        self.assertTrue(by_command["enrichment.jobs.create"]["external_action"])
        self.assertEqual(by_command["communication-groups.create"]["availability"], "available")
        self.assertEqual(by_command["settings.update"]["availability"], "available")
        self.assertEqual(by_command["identities.update-settings"]["availability"], "available")
        self.assertEqual(by_command["identities.test-smtp"]["availability"], "available")
        self.assertEqual(by_command["llm-profiles.update-settings"]["availability"], "available")
        self.assertEqual(by_command["llm-profiles.set-default"]["availability"], "available")
        self.assertEqual(by_command["llm-profiles.models"]["availability"], "available")
        self.assertTrue(by_command["llm-profiles.models"]["external_action"])
        self.assertEqual(by_command["llm-profiles.test"]["risk_level"], "L2")
        self.assertEqual(by_command["diagnostics.logs"]["availability"], "available")
        self.assertEqual(by_command["diagnostics.crawler-debug"]["availability"], "available")
        self.assertEqual(by_command["workspaces.get"]["availability"], "available")
        self.assertEqual(by_command["workspaces.ensure-task"]["risk_level"], "L1")
        self.assertTrue(by_command["workspaces.refresh-replies"]["external_action"])
        self.assertEqual(by_command["tasks.cancel-schedule"]["availability"], "available")
        self.assertEqual(by_command["tasks.continue-manually"]["risk_level"], "L1")
        self.assertEqual(by_command["tasks.start-follow-up"]["availability"], "available")
        self.assertEqual(by_command["tasks.set-primary-material"]["risk_level"], "L2")
        self.assertTrue(by_command["tasks.set-primary-material"]["external_action"])
        self.assertEqual(by_command["tasks.set-outreach-config"]["availability"], "available")
        self.assertEqual(by_command["tasks.calculate-match"]["risk_level"], "L2")
        self.assertTrue(by_command["tasks.calculate-match"]["external_action"])
        self.assertEqual(by_command["test-email.get"]["availability"], "available")
        self.assertEqual(by_command["test-email.prepare-send"]["risk_level"], "L3")
        self.assertTrue(by_command["test-email.prepare-send"]["requires_plan"])

    def test_capabilities_are_available_without_a_running_desktop_app(self) -> None:
        result = self.runner.invoke(app, ["--format", "json", "capabilities"])

        self.assertEqual(result.exit_code, 0, msg=result.output)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["data"]["items"])

    def test_capabilities_accept_spaced_command_names_and_suggest_unknown_commands(self) -> None:
        result = self.runner.invoke(
            app,
            ["--format", "json", "capabilities", "--command", "drafts generate"],
        )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["data"]["items"][0]["command"], "drafts.generate")

        missing = self.runner.invoke(
            app,
            ["--format", "json", "capabilities", "--command", "drafts missing"],
        )
        self.assertEqual(missing.exit_code, 4, msg=missing.output)
        missing_payload = json.loads(missing.stdout)
        self.assertIn("drafts.generate", missing_payload["error"]["details"]["suggestions"])

    def test_describe_returns_machine_readable_command_contract_without_runtime(self) -> None:
        result = self.runner.invoke(
            app,
            ["--format", "json", "describe", "--command", "drafts generate"],
        )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        payload = json.loads(result.stdout)["data"]
        self.assertEqual(payload["command"], "drafts.generate")
        self.assertEqual(payload["risk"]["level"], "L1")
        self.assertTrue(payload["preconditions"]["manual_app_open_required"])
        parameters = {parameter["name"]: parameter for parameter in payload["parameters"]}
        self.assertTrue(parameters["professor_id"]["required"])
        self.assertEqual(parameters["professor_id"]["type"]["kind"], "integer")
        self.assertEqual(parameters["generation_mode"]["type"]["values"], ["template", "ai_rewrite", "manual"])
        self.assertIn("guide --topic drafts", " ".join(payload["next_steps"]))

    def test_every_available_capability_has_a_describe_contract(self) -> None:
        missing = [
            capability.command
            for capability in CAPABILITIES
            if capability.availability == "available" and describe_command(app, capability.command) is None
        ]

        self.assertEqual(missing, [])

    def test_guide_routing_and_doctor_explain_outdated_skills(self) -> None:
        routing = self.runner.invoke(app, ["--format", "json", "guide", "--topic", "routing"])
        self.assertEqual(routing.exit_code, 0, msg=routing.output)
        self.assertIn("describe", " ".join(json.loads(routing.stdout)["data"]["rules"]))

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_path = Path(temp_dir) / "runtime.json"
            with (
                patch("auto_email_sender_cli.main.get_runtime_file_path", return_value=runtime_path),
                patch(
                    "auto_email_sender_cli.main.inspect_agent_skill_installation",
                    return_value={
                        "ok": False,
                        "state": "needs_update",
                        "message": "Agent 使用说明已过期或被修改，需要更新。",
                        "items": [{"id": "codex", "state": "needs_update"}],
                    },
                ),
            ):
                doctor = self.runner.invoke(app, ["--format", "json", "doctor"])

        self.assertEqual(doctor.exit_code, 0, msg=doctor.output)
        doctor_payload = json.loads(doctor.stdout)["data"]
        skill_check = next(check for check in doctor_payload["checks"] if check["id"] == "agent_skills")
        self.assertFalse(skill_check["ok"])
        self.assertIn("重新安装", doctor_payload["recommended_action"])

    def test_skill_inspection_detects_modified_or_outdated_official_skills(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source-skill"
            target = root / "codex-skill"
            source.mkdir()
            target.mkdir()
            source_file = source / "SKILL.md"
            target_file = target / "SKILL.md"
            source_file.write_text("official skill", encoding="utf-8")
            target_file.write_text("official skill", encoding="utf-8")
            file_hash = hashlib.sha256(b"official skill").hexdigest()
            skill_hash = hashlib.sha256(f"F\tSKILL.md\t{file_hash}\n".encode("utf-8")).hexdigest()
            manifest_path = root / "installation.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": 4,
                        "enabled": True,
                        "skill_source": source.as_posix(),
                        "agents": {
                            "codex": {
                                "skill_target": target.as_posix(),
                                "skill_sha256": skill_hash,
                            },
                        },
                    },
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"AUTO_EMAIL_SENDER_AGENT_MANIFEST_FILE": manifest_path.as_posix()}):
                healthy = inspect_agent_skill_installation()
                target_file.write_text("modified skill", encoding="utf-8")
                outdated = inspect_agent_skill_installation()

        self.assertTrue(healthy["ok"])
        self.assertEqual(healthy["items"][0]["state"], "installed")
        self.assertFalse(outdated["ok"])
        self.assertEqual(outdated["items"][0]["state"], "needs_update")

    def test_status_tells_user_to_manually_open_a_stopped_desktop_app(self) -> None:
        descriptor = SimpleNamespace(
            desktop_pid=12345,
            app_version="2.4.1",
            protocol_version="2",
        )
        with (
            patch(
                "auto_email_sender_cli.main.load_runtime_descriptor",
                return_value=descriptor,
            ),
            patch("auto_email_sender_cli.main.process_is_running", return_value=False),
        ):
            result = self.runner.invoke(app, ["--format", "json", "status"])

        self.assertEqual(result.exit_code, 0, msg=result.output)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["data"]["state"], "stopped")
        self.assertIn("手动打开", " ".join(payload["_meta"]["warnings"]))

    def test_doctor_recommends_manually_opening_an_app_without_runtime_info(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_path = Path(temp_dir) / "runtime.json"
            with patch(
                "auto_email_sender_cli.main.get_runtime_file_path",
                return_value=runtime_path,
            ):
                result = self.runner.invoke(app, ["--format", "json", "doctor"])

        self.assertEqual(result.exit_code, 0, msg=result.output)
        payload = json.loads(result.stdout)
        self.assertIn("手动打开", payload["data"]["recommended_action"])
        self.assertIsNone(payload["data"]["repair_command"])

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

    def test_professor_write_commands_use_safe_agent_routes_and_partial_updates(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/professor-tags": {
                    "id": 9,
                    "name": "重点",
                },
                "/api/agent/v1/professors": {
                    "id": 7,
                    "name": "测试导师",
                    "email": "p@example.edu",
                },
                "/api/agent/v1/professors/7": {
                    "id": 7,
                    "name": "测试导师",
                    "research_direction": "具身智能",
                },
                "/api/agent/v1/professors/7/tags": {
                    "id": 7,
                    "tags": [],
                },
            },
        )
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=fake_client,
        ):
            create_tag = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "professors",
                    "tags",
                    "create",
                    "--name",
                    "重点",
                    "--text-color",
                    "#111827",
                    "--background-color",
                    "#dbeafe",
                ],
            )
            create_professor = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "professors",
                    "create",
                    "--name",
                    "测试导师",
                    "--email",
                    "p@example.edu",
                    "--recent-paper",
                    "Paper A",
                    "--tag-id",
                    "9",
                ],
            )
            update_professor = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "professors",
                    "update",
                    "7",
                    "--research-direction",
                    "具身智能",
                    "--clear-recent-papers",
                ],
            )
            clear_tags = self.runner.invoke(
                app,
                ["--format", "json", "professors", "tags", "set", "7"],
            )

        for result in (create_tag, create_professor, update_professor, clear_tags):
            self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(fake_client.calls[0][:2], ("POST", "/api/agent/v1/professor-tags"))
        self.assertEqual(
            fake_client.json_bodies[0],
            {
                "name": "重点",
                "text_color": "#111827",
                "background_color": "#dbeafe",
            },
        )
        self.assertEqual(fake_client.calls[1][:2], ("POST", "/api/agent/v1/professors"))
        self.assertEqual(fake_client.json_bodies[1]["tag_ids"], [9])
        self.assertEqual(fake_client.json_bodies[1]["recent_papers"], ["Paper A"])
        self.assertEqual(fake_client.calls[2][:2], ("PUT", "/api/agent/v1/professors/7"))
        self.assertEqual(
            fake_client.json_bodies[2],
            {"research_direction": "具身智能", "recent_papers": []},
        )
        self.assertEqual(
            fake_client.calls[3][:2],
            ("PUT", "/api/agent/v1/professors/7/tags"),
        )
        self.assertEqual(fake_client.json_bodies[3], {"tag_ids": []})

    def test_template_write_commands_use_agent_routes_and_preserve_partial_fields(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/templates": {"id": 4, "name": "首次联系"},
                "/api/agent/v1/templates/4": {"id": 4, "name": "首次联系"},
                "/api/agent/v1/templates/4/duplicate": {"id": 5, "name": "首次联系（副本）"},
                "/api/agent/v1/templates/4/default": {"id": 4, "is_default": True},
                "/api/agent/v1/templates/4/restore": {"id": 4, "archived_at": None},
            },
        )
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=fake_client,
        ):
            create = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "templates",
                    "create",
                    "--name",
                    "首次联系",
                    "--subject",
                    "联系 {{name}} 教授",
                    "--body-text",
                    "老师您好。",
                    "--set-default",
                ],
            )
            update = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "templates",
                    "update",
                    "4",
                    "--body-text",
                    "更新后的正文。",
                ],
            )
            default_set = self.runner.invoke(
                app,
                ["--format", "json", "templates", "set-default", "4"],
            )
            duplicate = self.runner.invoke(
                app,
                ["--format", "json", "templates", "duplicate", "4"],
            )

        for result in (create, update, default_set, duplicate):
            self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(fake_client.calls[0][:2], ("POST", "/api/agent/v1/templates"))
        self.assertEqual(fake_client.json_bodies[0]["is_default"], True)
        self.assertEqual(fake_client.calls[1][:2], ("PUT", "/api/agent/v1/templates/4"))
        self.assertEqual(fake_client.json_bodies[1], {"body_text": "更新后的正文。"})
        self.assertEqual(
            fake_client.calls[2][:2],
            ("POST", "/api/agent/v1/templates/4/default"),
        )
        self.assertEqual(
            fake_client.calls[3][:2],
            ("POST", "/api/agent/v1/templates/4/duplicate"),
        )

    def test_template_archive_command_uses_change_plan_route(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/templates/4/prepare-archive": {
                    "plan_id": "change_template_archive",
                    "action": "template.archive",
                    "status": "awaiting_confirmation",
                },
            },
        )
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=fake_client,
        ):
            result = self.runner.invoke(
                app,
                ["--format", "json", "templates", "prepare-archive", "4"],
            )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(
            fake_client.calls[0][:2],
            ("POST", "/api/agent/v1/templates/4/prepare-archive"),
        )

    def test_template_import_file_uses_agent_route_without_persisting(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/templates/import-file": {
                    "subject": None,
                    "body_text": "老师您好。",
                    "body_html": "<p>老师您好。</p>",
                    "format_name": "md",
                    "trust_level": "untrusted_external_content",
                },
            },
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "follow-up.md"
            file_path.write_text("老师您好。", encoding="utf-8")
            with patch(
                "auto_email_sender_cli.commands.resources.AgentApiClient",
                return_value=fake_client,
            ):
                result = self.runner.invoke(
                    app,
                    ["--format", "json", "templates", "import-file", file_path.as_posix()],
                )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(
            fake_client.calls[0][:2],
            ("POST", "/api/agent/v1/templates/import-file"),
        )
        uploaded_file = fake_client.file_bodies[0]["file"]
        self.assertEqual(uploaded_file[0], "follow-up.md")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["data"]["format_name"], "md")

    def test_draft_rewrite_uses_agent_route_with_current_text(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/drafts/17/rewrite": {
                    "task_id": 17,
                    "status": "review_required",
                    "generated_subject": "改写后的主题",
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
                    "rewrite",
                    "17",
                    "--subject",
                    "原主题",
                    "--body-text",
                    "请简化这封邮件。",
                    "--attachment-material-id",
                    "3",
                ],
            )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(
            fake_client.calls[0][:2],
            ("POST", "/api/agent/v1/drafts/17/rewrite"),
        )
        self.assertEqual(
            fake_client.json_bodies[0],
            {
                "subject": "原主题",
                "body_text": "请简化这封邮件。",
                "body_html": None,
                "llm_profile_id": None,
                "attachment_material_ids": [3],
            },
        )

    def test_professor_bulk_tags_command_creates_change_plan(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/professors/prepare-bulk-tags": {
                    "plan_id": "change_professor_tags",
                    "action": "professor.tags.bulk",
                    "status": "awaiting_confirmation",
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
                    "professors",
                    "tags",
                    "prepare-bulk",
                    "--professor-id",
                    "7",
                    "--professor-id",
                    "8",
                    "--mode",
                    "add",
                    "--tag-id",
                    "9",
                ],
            )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(
            fake_client.calls[0][:2],
            ("POST", "/api/agent/v1/professors/prepare-bulk-tags"),
        )
        self.assertEqual(
            fake_client.json_bodies[0],
            {"professor_ids": [7, 8], "mode": "add", "tag_ids": [9]},
        )

    def test_professor_tag_and_bulk_archive_commands_use_agent_routes(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/professor-tags/9/usage": {
                    "tag": {"id": 9, "name": "重点"},
                    "professors": [],
                },
                "/api/agent/v1/professor-tags/9/prepare-delete": {
                    "plan_id": "change_tag_delete",
                    "action": "professor.tag.delete",
                    "status": "awaiting_confirmation",
                },
                "/api/agent/v1/professors/prepare-bulk-archive": {
                    "plan_id": "change_bulk_archive",
                    "action": "professor.archive.bulk",
                    "status": "awaiting_confirmation",
                },
            },
        )
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=fake_client,
        ):
            usage = self.runner.invoke(
                app,
                ["--format", "json", "professors", "tags", "usage", "9"],
            )
            delete = self.runner.invoke(
                app,
                ["--format", "json", "professors", "tags", "prepare-delete", "9"],
            )
            archive = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "professors",
                    "prepare-bulk-archive",
                    "--professor-id",
                    "7",
                    "--professor-id",
                    "8",
                ],
            )

        for result in (usage, delete, archive):
            self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(
            fake_client.calls[0][:2],
            ("GET", "/api/agent/v1/professor-tags/9/usage"),
        )
        self.assertEqual(
            fake_client.calls[1][:2],
            ("POST", "/api/agent/v1/professor-tags/9/prepare-delete"),
        )
        self.assertEqual(
            fake_client.calls[2][:2],
            ("POST", "/api/agent/v1/professors/prepare-bulk-archive"),
        )
        self.assertEqual(fake_client.json_bodies[2], {"professor_ids": [7, 8]})

    def test_professor_import_uploads_file_to_create_change_plan(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/professors/prepare-import": {
                    "plan_id": "change_professor_import",
                    "action": "professor.import",
                    "status": "awaiting_confirmation",
                },
            },
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "professors.csv"
            file_path.write_text("name,email\n", encoding="utf-8")
            with patch(
                "auto_email_sender_cli.commands.professors.AgentApiClient",
                return_value=fake_client,
            ):
                result = self.runner.invoke(
                    app,
                    ["--format", "json", "professors", "import", file_path.as_posix()],
                )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(
            fake_client.calls[0][:2],
            ("POST", "/api/agent/v1/professors/prepare-import"),
        )
        uploaded_file = fake_client.file_bodies[0]["file"]
        self.assertEqual(uploaded_file[0], "professors.csv")

    def test_community_mentor_commands_use_agent_routes_and_import_plan(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/community-mentors/catalog": {
                    "dataset_version": "2026-08-03T000000Z-abcdef123456",
                    "record_count": 1,
                },
                "/api/agent/v1/community-mentors/records": {"records": []},
                "/api/agent/v1/community-mentors/preview": {
                    "records": [{"record": {"id": "mentor_example0001"}}],
                },
                "/api/agent/v1/community-mentors/prepare-import": {
                    "plan_id": "change_community_import",
                    "action": "community_mentor.import",
                    "status": "awaiting_confirmation",
                },
                "/api/agent/v1/community-mentors/share-package": b"community xlsx",
            },
        )
        import_payload = {
            "dataset_version": "2026-08-03T000000Z-abcdef123456",
            "unit_paths": ["data/org_example_university/org_example_school.json"],
            "items": [
                {
                    "community_record_id": "mentor_example0001",
                    "comparison_token": "a" * 64,
                    "field_choices": {"research_direction": "community"},
                    "confirm_identity_match": False,
                },
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            items_file = root / "community-import.json"
            items_file.write_text(json.dumps(import_payload), encoding="utf-8")
            output = root / "community-share.xlsx"
            with patch(
                "auto_email_sender_cli.commands.common.AgentApiClient",
                return_value=fake_client,
            ), patch(
                "auto_email_sender_cli.commands.professors.AgentApiClient",
                return_value=fake_client,
            ):
                catalog = self.runner.invoke(
                    app,
                    ["--format", "json", "professors", "community", "catalog", "--refresh"],
                )
                records = self.runner.invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "professors",
                        "community",
                        "records",
                        "--dataset-version",
                        import_payload["dataset_version"],
                        "--unit-path",
                        import_payload["unit_paths"][0],
                    ],
                )
                preview = self.runner.invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "professors",
                        "community",
                        "preview",
                        "--dataset-version",
                        import_payload["dataset_version"],
                        "--unit-path",
                        import_payload["unit_paths"][0],
                        "--record-id",
                        "mentor_example0001",
                    ],
                )
                prepared = self.runner.invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "professors",
                        "community",
                        "import",
                        "--items-file",
                        items_file.as_posix(),
                    ],
                )
                exported = self.runner.invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "professors",
                        "community",
                        "export-package",
                        "--professor-id",
                        "7",
                        "--output",
                        output.as_posix(),
                    ],
                )
            exported_content = output.read_bytes()

        for result in (catalog, records, preview, prepared, exported):
            self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(fake_client.calls[0][:2], ("GET", "/api/agent/v1/community-mentors/catalog"))
        self.assertEqual(fake_client.calls[0][2], {"refresh": True})
        self.assertEqual(fake_client.calls[1][:2], ("POST", "/api/agent/v1/community-mentors/records"))
        self.assertEqual(
            fake_client.json_bodies[1],
            {
                "dataset_version": import_payload["dataset_version"],
                "unit_paths": import_payload["unit_paths"],
            },
        )
        self.assertEqual(fake_client.calls[2][:2], ("POST", "/api/agent/v1/community-mentors/preview"))
        self.assertEqual(
            fake_client.json_bodies[2]["record_ids"],
            ["mentor_example0001"],
        )
        self.assertEqual(
            fake_client.calls[3][:2],
            ("POST", "/api/agent/v1/community-mentors/prepare-import"),
        )
        self.assertEqual(fake_client.json_bodies[3], import_payload)
        self.assertEqual(
            fake_client.download_calls,
            ["/api/agent/v1/community-mentors/share-package"],
        )
        self.assertEqual(fake_client.download_params[0], {"professor_ids": "7"})
        self.assertEqual(exported_content, b"community xlsx")

    def test_test_email_commands_use_agent_routes_and_prepare_plan(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/test-email/2/status": {"completed": False},
                "/api/agent/v1/test-email/2/3": {
                    "identity": {"id": 2, "email_address": "self@example.com"},
                    "draft": {"body_text": "旧草稿"},
                },
                "/api/agent/v1/test-email/2/3/generate-draft": {
                    "draft": {"body_text": "生成草稿"},
                },
                "/api/agent/v1/test-email/2/3/draft": {
                    "draft": {"body_text": "保存草稿"},
                },
                "/api/agent/v1/test-email/2/3/prepare-send": {
                    "plan_id": "change_test_email",
                    "action": "test_email.send",
                    "status": "awaiting_confirmation",
                },
            },
        )
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=fake_client,
        ):
            status = self.runner.invoke(
                app,
                ["--format", "json", "test-email", "status", "--identity-id", "2"],
            )
            thread = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "test-email",
                    "get",
                    "--identity-id",
                    "2",
                    "--llm-profile-id",
                    "3",
                ],
            )
            generated = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "test-email",
                    "generate",
                    "--identity-id",
                    "2",
                    "--llm-profile-id",
                    "3",
                    "--template-id",
                    "4",
                ],
            )
            saved = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "test-email",
                    "save",
                    "--identity-id",
                    "2",
                    "--llm-profile-id",
                    "3",
                    "--subject",
                    "测试主题",
                    "--body-text",
                    "测试正文",
                    "--material-id",
                    "5",
                ],
            )
            prepared = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "test-email",
                    "prepare-send",
                    "--identity-id",
                    "2",
                    "--llm-profile-id",
                    "3",
                    "--subject",
                    "测试主题",
                    "--body-text",
                    "测试正文",
                    "--material-id",
                    "5",
                ],
            )

        for result in (status, thread, generated, saved, prepared):
            self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(fake_client.calls[0][:2], ("GET", "/api/agent/v1/test-email/2/status"))
        self.assertEqual(fake_client.calls[1][:2], ("GET", "/api/agent/v1/test-email/2/3"))
        self.assertEqual(
            fake_client.calls[2][:2],
            ("POST", "/api/agent/v1/test-email/2/3/generate-draft"),
        )
        self.assertEqual(fake_client.json_bodies[2], {"outreach_template_id": 4})
        self.assertEqual(fake_client.calls[3][:2], ("PUT", "/api/agent/v1/test-email/2/3/draft"))
        self.assertEqual(
            fake_client.json_bodies[3],
            {
                "subject": "测试主题",
                "body_text": "测试正文",
                "body_html": None,
                "selected_material_ids": [5],
            },
        )
        self.assertEqual(
            fake_client.calls[4][:2],
            ("POST", "/api/agent/v1/test-email/2/3/prepare-send"),
        )
        self.assertEqual(fake_client.json_bodies[4]["selected_material_ids"], [5])

    def test_material_upload_and_set_primary_use_agent_routes(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/materials": {
                    "id": 8,
                    "display_name": "个人简历",
                    "is_primary": True,
                },
                "/api/agent/v1/materials/8/set-primary": {
                    "id": 8,
                    "is_primary": True,
                },
                "/api/agent/v1/materials/8/prepare-delete": {
                    "plan_id": "change_material_delete",
                    "action": "material.delete",
                    "status": "awaiting_confirmation",
                },
                "/api/agent/v1/materials/8/download": b"candidate resume",
            },
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "resume.txt"
            download_path = Path(temp_dir) / "downloaded-resume.txt"
            file_path.write_text("candidate resume", encoding="utf-8")
            with patch(
                "auto_email_sender_cli.commands.resources.AgentApiClient",
                return_value=fake_client,
            ), patch(
                "auto_email_sender_cli.commands.common.AgentApiClient",
                return_value=fake_client,
            ):
                upload = self.runner.invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "materials",
                        "upload",
                        file_path.as_posix(),
                        "--identity-id",
                        "2",
                        "--material-type",
                        "resume",
                        "--display-name",
                        "个人简历",
                    ],
                )
                set_primary = self.runner.invoke(
                    app,
                    ["--format", "json", "materials", "set-primary", "8"],
                )
                prepare_delete = self.runner.invoke(
                    app,
                    ["--format", "json", "materials", "prepare-delete", "8"],
                )
                download = self.runner.invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "materials",
                        "download",
                        "8",
                        "--output",
                        download_path.as_posix(),
                    ],
                )
                downloaded_content = download_path.read_bytes()

        self.assertEqual(upload.exit_code, 0, msg=upload.output)
        self.assertEqual(set_primary.exit_code, 0, msg=set_primary.output)
        self.assertEqual(prepare_delete.exit_code, 0, msg=prepare_delete.output)
        self.assertEqual(download.exit_code, 0, msg=download.output)
        self.assertEqual(downloaded_content, b"candidate resume")
        self.assertEqual(fake_client.calls[0][:2], ("POST", "/api/agent/v1/materials"))
        self.assertEqual(
            fake_client.data_bodies[0],
            {
                "identity_id": 2,
                "material_type": "resume",
                "display_name": "个人简历",
            },
        )
        uploaded_file = fake_client.file_bodies[0]["file"]
        self.assertEqual(uploaded_file[0], "resume.txt")
        self.assertEqual(
            fake_client.calls[1][:2],
            ("POST", "/api/agent/v1/materials/8/set-primary"),
        )
        self.assertEqual(
            fake_client.calls[2][:2],
            ("POST", "/api/agent/v1/materials/8/prepare-delete"),
        )
        self.assertEqual(
            fake_client.download_calls,
            ["/api/agent/v1/materials/8/download"],
        )

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

    def test_communications_sync_uses_scoped_agent_route(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/communications/sync": {
                    "identity_id": 2,
                    "detected_count": 3,
                    "message": "已完成一次邮箱同步检查，新增 3 条通信记录。",
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
                    "communications",
                    "sync",
                    "--identity-id",
                    "2",
                ],
            )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(
            fake_client.calls[0][:2],
            ("POST", "/api/agent/v1/communications/sync"),
        )
        self.assertEqual(fake_client.json_bodies[0], {"identity_id": 2})

    def test_workspace_commands_use_scoped_agent_routes(self) -> None:
        workspace = {
            "professor": {"id": 7, "name": "测试导师"},
            "identity": {"id": 2, "name": "测试身份"},
            "llm_profile": {"id": 3, "name": "测试模型"},
            "current_task": {"id": 11, "status": "review_required"},
            "messages": [],
        }
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/workspaces/7": workspace,
                "/api/agent/v1/workspaces/7/ensure-task": workspace,
                "/api/agent/v1/workspaces/7/refresh-replies": {
                    **workspace,
                    "sync_warnings": [],
                },
            },
        )
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=fake_client,
        ):
            get_result = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "workspaces",
                    "get",
                    "7",
                    "--identity-id",
                    "2",
                    "--llm-profile-id",
                    "3",
                ],
            )
            ensure_result = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "workspaces",
                    "ensure-task",
                    "7",
                    "--identity-id",
                    "2",
                    "--llm-profile-id",
                    "3",
                ],
            )
            refresh_result = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "workspaces",
                    "refresh-replies",
                    "7",
                    "--identity-id",
                    "2",
                    "--llm-profile-id",
                    "3",
                ],
            )

        for result in (get_result, ensure_result, refresh_result):
            self.assertEqual(result.exit_code, 0, msg=result.output)
        expected_params = {"identity_id": 2, "llm_profile_id": 3}
        self.assertEqual(
            fake_client.calls[0],
            ("GET", "/api/agent/v1/workspaces/7", expected_params),
        )
        self.assertEqual(
            fake_client.calls[1],
            ("POST", "/api/agent/v1/workspaces/7/ensure-task", expected_params),
        )
        self.assertEqual(
            fake_client.calls[2],
            (
                "POST",
                "/api/agent/v1/workspaces/7/refresh-replies",
                expected_params,
            ),
        )
        self.assertEqual(fake_client.json_bodies[1], None)
        self.assertEqual(fake_client.json_bodies[2], None)

    def test_single_task_commands_use_agent_routes_without_direct_delivery(self) -> None:
        workspace = {
            "professor": {"id": 7, "name": "测试导师"},
            "identity": {"id": 2, "name": "测试身份"},
            "llm_profile": {"id": 3, "name": "测试模型"},
            "current_task": {"id": 11, "status": "review_required"},
            "messages": [],
        }
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/tasks/11/cancel-schedule": workspace,
                "/api/agent/v1/tasks/11/continue-manually": workspace,
                "/api/agent/v1/tasks/11/start-follow-up": workspace,
                "/api/agent/v1/tasks/11/primary-material": workspace,
                "/api/agent/v1/tasks/11/outreach-config": workspace,
                "/api/agent/v1/tasks/11/calculate-match": {
                    "task_id": 11,
                    "thread": workspace,
                    "usage": {"total_tokens": 18},
                    "run_id": 77,
                },
            },
        )
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=fake_client,
        ):
            results = [
                self.runner.invoke(
                    app,
                    ["--format", "json", "tasks", "cancel-schedule", "11"],
                ),
                self.runner.invoke(
                    app,
                    ["--format", "json", "tasks", "continue-manually", "11"],
                ),
                self.runner.invoke(
                    app,
                    ["--format", "json", "tasks", "start-follow-up", "11"],
                ),
                self.runner.invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "tasks",
                        "set-primary-material",
                        "11",
                        "--material-id",
                        "4",
                    ],
                ),
                self.runner.invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "tasks",
                        "set-outreach-config",
                        "11",
                        "--generation-mode",
                        "template",
                        "--template-id",
                        "3",
                        "--subject",
                        "再次联系 {{name}}",
                        "--body-text",
                        "老师您好",
                        "--body-html",
                        "<p>老师您好</p>",
                    ],
                ),
                self.runner.invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "tasks",
                        "calculate-match",
                        "11",
                        "--llm-profile-id",
                        "2",
                    ],
                ),
            ]

        for result in results:
            self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(
            [call[:2] for call in fake_client.calls],
            [
                ("POST", "/api/agent/v1/tasks/11/cancel-schedule"),
                ("POST", "/api/agent/v1/tasks/11/continue-manually"),
                ("POST", "/api/agent/v1/tasks/11/start-follow-up"),
                ("POST", "/api/agent/v1/tasks/11/primary-material"),
                ("POST", "/api/agent/v1/tasks/11/outreach-config"),
                ("POST", "/api/agent/v1/tasks/11/calculate-match"),
            ],
        )
        self.assertEqual(fake_client.json_bodies[0], None)
        self.assertEqual(fake_client.json_bodies[1], None)
        self.assertEqual(fake_client.json_bodies[2], None)
        self.assertEqual(fake_client.json_bodies[3], {"primary_material_id": 4})
        self.assertEqual(
            fake_client.json_bodies[4],
            {
                "outreach_generation_mode": "template",
                "outreach_template_id": 3,
                "outreach_template_subject": "再次联系 {{name}}",
                "outreach_template_body_text": "老师您好",
                "outreach_template_body_html": "<p>老师您好</p>",
            },
        )
        self.assertEqual(fake_client.json_bodies[5], {"llm_profile_id": 2})

    def test_single_task_outreach_config_rejects_conflicting_clear_options(self) -> None:
        result = self.runner.invoke(
            app,
            [
                "--format",
                "json",
                "tasks",
                "set-outreach-config",
                "11",
                "--generation-mode",
                "template",
                "--template-id",
                "3",
                "--clear-template",
            ],
        )

        self.assertNotEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("--clear-template", result.output)

    def test_crawler_events_command_uses_agent_route(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/crawler/jobs/52/events": {
                    "items": [
                        {
                            "event_type": "page_fetched",
                            "message": "页面已抓取",
                            "trust_level": "untrusted_external_content",
                        },
                    ],
                    "next_cursor": None,
                    "has_more": False,
                },
            },
        )
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=fake_client,
        ):
            result = self.runner.invoke(
                app,
                ["--format", "json", "crawler", "jobs", "events", "52", "--all"],
            )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(
            fake_client.calls[0],
            ("GET", "/api/agent/v1/crawler/jobs/52/events", {"limit": 500}),
        )

    def test_matching_commands_use_agent_routes_and_keep_async_job_state_visible(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/matching/jobs": {
                    "items": [
                        {
                            "id": 41,
                            "name": "匹配分析",
                            "status": "queued",
                            "target_count": 2,
                            "succeeded_count": 0,
                            "failed_count": 0,
                        },
                    ],
                    "next_cursor": None,
                    "has_more": False,
                },
                "/api/agent/v1/matching/jobs/41/items": {
                    "items": [
                        {
                            "id": 1,
                            "professor_name": "测试导师",
                            "status": "queued",
                            "match_score": None,
                            "total_tokens": 0,
                            "error_message": None,
                        },
                    ],
                    "next_cursor": None,
                    "has_more": False,
                },
                "/api/agent/v1/matching/jobs/41/cancel": {
                    "ok": True,
                    "job": {"id": 41, "status": "canceled"},
                },
                "/api/agent/v1/matching/jobs/41/retry-failed": {
                    "id": 42,
                    "status": "queued",
                },
                "/api/agent/v1/matching/jobs/41/delete": {
                    "ok": True,
                    "job": {"id": 41, "deleted_at": "2026-08-04T00:00:00Z"},
                },
                "/api/agent/v1/matching/jobs/41/restore": {
                    "ok": True,
                    "job": {"id": 41, "deleted_at": None},
                },
            },
        )
        create_response = {
            "id": 41,
            "name": "匹配分析",
            "status": "queued",
            "target_count": 2,
        }
        fake_client.responses["/api/agent/v1/matching/jobs"] = create_response
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=fake_client,
        ):
            create = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "matching",
                    "jobs",
                    "create",
                    "--identity-id",
                    "2",
                    "--llm-profile-id",
                    "3",
                    "--professor-id",
                    "7",
                    "--professor-id",
                    "8",
                    "--name",
                    "匹配分析",
                ],
            )
            items = self.runner.invoke(
                app,
                ["--format", "json", "matching", "jobs", "items", "41", "--all"],
            )
            cancel = self.runner.invoke(
                app,
                ["--format", "json", "matching", "jobs", "cancel", "41"],
            )
            retry = self.runner.invoke(
                app,
                ["--format", "json", "matching", "jobs", "retry-failed", "41"],
            )
            delete = self.runner.invoke(
                app,
                ["--format", "json", "matching", "jobs", "delete", "41"],
            )
            restore = self.runner.invoke(
                app,
                ["--format", "json", "matching", "jobs", "restore", "41"],
            )

        for result in (create, items, cancel, retry, delete, restore):
            self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(
            fake_client.calls[0][:2],
            ("POST", "/api/agent/v1/matching/jobs"),
        )
        self.assertEqual(
            fake_client.json_bodies[0],
            {
                "identity_id": 2,
                "llm_profile_id": 3,
                "professor_ids": [7, 8],
                "name": "匹配分析",
            },
        )
        self.assertEqual(
            fake_client.calls[1][:2],
            ("GET", "/api/agent/v1/matching/jobs/41/items"),
        )
        self.assertEqual(
            fake_client.calls[2][:2],
            ("POST", "/api/agent/v1/matching/jobs/41/cancel"),
        )
        self.assertEqual(
            fake_client.calls[3][:2],
            ("POST", "/api/agent/v1/matching/jobs/41/retry-failed"),
        )
        self.assertEqual(
            fake_client.calls[4][:2],
            ("POST", "/api/agent/v1/matching/jobs/41/delete"),
        )
        self.assertEqual(
            fake_client.calls[5][:2],
            ("POST", "/api/agent/v1/matching/jobs/41/restore"),
        )

    def test_dashboard_and_usage_commands_use_safe_read_only_agent_routes(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/dashboard/overview": {
                    "mentor": {"summary": {"total_professors": 2}},
                    "email": {"summary": {"sent_count": 1}},
                },
                "/api/agent/v1/usage/records": {
                    "records": [],
                    "summary": {"total_tokens": 0},
                    "pagination": {"page": 1, "page_size": 100, "total_records": 0},
                },
                "/api/agent/v1/usage/chart": {
                    "preset": "last_7_days",
                    "buckets": [],
                },
                "/api/agent/v1/usage/visualization": {
                    "preset": "last_24_hours",
                    "summary": {"total_tokens": 0},
                    "recent_records": [],
                },
            },
        )
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=fake_client,
        ):
            dashboard = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "dashboard",
                    "overview",
                    "--identity-id",
                    "2",
                    "--university",
                    "示例大学",
                ],
            )
            records = self.runner.invoke(
                app,
                ["--format", "json", "usage", "records", "--feature-type", "match_analysis"],
            )
            chart = self.runner.invoke(
                app,
                ["--format", "json", "usage", "chart", "--preset", "last_7_days"],
            )
            visualization = self.runner.invoke(
                app,
                ["--format", "json", "usage", "visualization"],
            )

        for result in (dashboard, records, chart, visualization):
            self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(
            fake_client.calls[0],
            (
                "GET",
                "/api/agent/v1/dashboard/overview",
                {"identity_id": 2, "university": "示例大学"},
            ),
        )
        self.assertEqual(
            fake_client.calls[1],
            (
                "GET",
                "/api/agent/v1/usage/records",
                {"page": 1, "page_size": 100, "feature_type": "match_analysis"},
            ),
        )
        self.assertEqual(
            fake_client.calls[2],
            (
                "GET",
                "/api/agent/v1/usage/chart",
                {"preset": "last_7_days", "feature_type": "all"},
            ),
        )
        self.assertEqual(
            fake_client.calls[3],
            (
                "GET",
                "/api/agent/v1/usage/visualization",
                {"preset": "last_24_hours"},
            ),
        )

    def test_enrichment_job_commands_use_agent_task_routes(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/enrichment/jobs/52/items": {
                    "items": [{"id": 5, "professor_name": "补全导师", "status": "queued"}],
                    "next_cursor": None,
                    "has_more": False,
                },
                "/api/agent/v1/enrichment/jobs/52/cancel": {
                    "ok": True,
                    "job": {"id": 52, "status": "canceled"},
                },
                "/api/agent/v1/enrichment/jobs/52/retry-failed": {
                    "id": 53,
                    "status": "queued",
                },
                "/api/agent/v1/enrichment/jobs/52/delete": {
                    "ok": True,
                    "job": {"id": 52, "deleted_at": "2026-08-04T00:00:00Z"},
                },
                "/api/agent/v1/enrichment/jobs/52/restore": {
                    "ok": True,
                    "job": {"id": 52, "deleted_at": None},
                },
            },
        )
        fake_client.responses["/api/agent/v1/enrichment/jobs"] = {
            "id": 52,
            "name": "Agent 信息补全",
            "status": "queued",
            "target_count": 2,
        }
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=fake_client,
        ):
            create = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "enrichment",
                    "jobs",
                    "create",
                    "--llm-profile-id",
                    "3",
                    "--professor-id",
                    "7",
                    "--professor-id",
                    "8",
                    "--name",
                    "Agent 信息补全",
                ],
            )
            items = self.runner.invoke(
                app,
                ["--format", "json", "enrichment", "jobs", "items", "52", "--all"],
            )
            cancel = self.runner.invoke(
                app,
                ["--format", "json", "enrichment", "jobs", "cancel", "52"],
            )
            retry = self.runner.invoke(
                app,
                ["--format", "json", "enrichment", "jobs", "retry-failed", "52"],
            )
            delete = self.runner.invoke(
                app,
                ["--format", "json", "enrichment", "jobs", "delete", "52"],
            )
            restore = self.runner.invoke(
                app,
                ["--format", "json", "enrichment", "jobs", "restore", "52"],
            )

        for result in (create, items, cancel, retry, delete, restore):
            self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(
            fake_client.calls[0][:2],
            ("POST", "/api/agent/v1/enrichment/jobs"),
        )
        self.assertEqual(
            fake_client.json_bodies[0],
            {
                "professor_ids": [7, 8],
                "llm_profile_id": 3,
                "name": "Agent 信息补全",
            },
        )
        self.assertEqual(
            fake_client.calls[1][:2],
            ("GET", "/api/agent/v1/enrichment/jobs/52/items"),
        )
        self.assertEqual(
            fake_client.calls[2][:2],
            ("POST", "/api/agent/v1/enrichment/jobs/52/cancel"),
        )
        self.assertEqual(
            fake_client.calls[3][:2],
            ("POST", "/api/agent/v1/enrichment/jobs/52/retry-failed"),
        )
        self.assertEqual(
            fake_client.calls[4][:2],
            ("POST", "/api/agent/v1/enrichment/jobs/52/delete"),
        )
        self.assertEqual(
            fake_client.calls[5][:2],
            ("POST", "/api/agent/v1/enrichment/jobs/52/restore"),
        )

    def test_crawler_commands_use_scoped_agent_routes_and_keep_web_content_marked(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/crawler/jobs": {
                    "id": 52,
                    "status": "queued",
                    "candidate_count": 0,
                },
                "/api/agent/v1/crawler/jobs/52/pages": {
                    "items": [
                        {
                            "id": 6,
                            "url": "https://example.edu/faculty",
                            "page_type": "faculty_list",
                            "status": "completed",
                            "trust_level": "untrusted_external_content",
                        },
                    ],
                    "next_cursor": None,
                    "has_more": False,
                },
                "/api/agent/v1/crawler/jobs/52/candidates": {
                    "items": [
                        {
                            "id": 7,
                            "name": "抓取导师",
                            "email": "crawler@example.edu",
                            "review_status": "pending",
                            "trust_level": "untrusted_external_content",
                        },
                    ],
                    "next_cursor": None,
                    "has_more": False,
                },
                "/api/agent/v1/crawler/candidates/7": {
                    "id": 7,
                    "name": "抓取导师",
                    "title": "副教授",
                    "email": None,
                },
                "/api/agent/v1/crawler/jobs/52/pause": {"id": 52, "status": "paused"},
                "/api/agent/v1/crawler/jobs/52/resume": {"id": 52, "status": "queued"},
                "/api/agent/v1/crawler/jobs/52/cancel": {"id": 52, "status": "canceled"},
                "/api/agent/v1/crawler/jobs/52/resume-review": {
                    "id": 52,
                    "status": "needs_review",
                },
                "/api/agent/v1/crawler/jobs/52/delete": {
                    "id": 52,
                    "deleted_at": "2026-08-04T00:00:00Z",
                },
                "/api/agent/v1/crawler/jobs/52/restore": {"id": 52, "deleted_at": None},
                "/api/agent/v1/crawler/jobs/52/prepare-approve": {
                    "plan_id": "change_crawler_approval",
                    "action": "crawler.candidates.approve",
                    "status": "awaiting_confirmation",
                    "summary": {"candidate_count": 2},
                },
                "/api/agent/v1/crawler/jobs/52/prepare-retry": {
                    "plan_id": "change_crawler_retry",
                    "action": "crawler.job.retry",
                    "status": "awaiting_confirmation",
                    "summary": {"clear_existing_data": False},
                },
                "/api/agent/v1/crawler/jobs/52/enrich": {
                    "selected_count": 2,
                    "enriched_count": 0,
                    "unchanged_count": 0,
                    "failed_count": 0,
                    "skipped_count": 0,
                    "message": "已加入补全队列。",
                },
            },
        )
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=fake_client,
        ):
            create = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "crawler",
                    "jobs",
                    "create",
                    "--university",
                    "示例大学",
                    "--school",
                    "计算机学院",
                    "--start-url",
                    "https://example.edu/faculty",
                    "--additional-start-url",
                    "https://example.edu/lab",
                    "--llm-profile-id",
                    "3",
                ],
            )
            pages = self.runner.invoke(
                app,
                ["--format", "json", "crawler", "jobs", "pages", "52", "--all"],
            )
            candidates = self.runner.invoke(
                app,
                ["--format", "json", "crawler", "jobs", "candidates", "52", "--all"],
            )
            update = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "crawler",
                    "candidates",
                    "update",
                    "7",
                    "--title",
                    "副教授",
                    "--clear-email",
                ],
            )
            pause = self.runner.invoke(
                app,
                ["--format", "json", "crawler", "jobs", "pause", "52"],
            )
            resume = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "crawler",
                    "jobs",
                    "resume",
                    "52",
                    "--llm-profile-id",
                    "3",
                ],
            )
            cancel = self.runner.invoke(
                app,
                ["--format", "json", "crawler", "jobs", "cancel", "52"],
            )
            review = self.runner.invoke(
                app,
                ["--format", "json", "crawler", "jobs", "resume-review", "52"],
            )
            delete = self.runner.invoke(
                app,
                ["--format", "json", "crawler", "jobs", "delete", "52"],
            )
            restore = self.runner.invoke(
                app,
                ["--format", "json", "crawler", "jobs", "restore", "52"],
            )
            approve = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "crawler",
                    "jobs",
                    "approve",
                    "52",
                    "--candidate-id",
                    "7",
                    "--candidate-id",
                    "8",
                ],
            )
            retry = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "crawler",
                    "jobs",
                    "retry",
                    "52",
                    "--keep-existing-data",
                    "--llm-profile-id",
                    "3",
                ],
            )
            enrich = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "crawler",
                    "jobs",
                    "enrich",
                    "52",
                    "--candidate-id",
                    "7",
                    "--candidate-id",
                    "8",
                    "--llm-profile-id",
                    "3",
                ],
            )

        for result in (
            create,
            pages,
            candidates,
            update,
            pause,
            resume,
            cancel,
            review,
            delete,
            restore,
            approve,
            retry,
            enrich,
        ):
            self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(
            fake_client.calls[0][:2],
            ("POST", "/api/agent/v1/crawler/jobs"),
        )
        self.assertEqual(
            fake_client.json_bodies[0],
            {
                "university": "示例大学",
                "school": "计算机学院",
                "start_url": "https://example.edu/faculty",
                "start_urls": [
                    "https://example.edu/faculty",
                    "https://example.edu/lab",
                ],
                "entry_type": "list",
                "llm_profile_id": 3,
            },
        )
        self.assertEqual(
            fake_client.calls[1][:2],
            ("GET", "/api/agent/v1/crawler/jobs/52/pages"),
        )
        self.assertEqual(
            fake_client.calls[2][:2],
            ("GET", "/api/agent/v1/crawler/jobs/52/candidates"),
        )
        self.assertEqual(
            fake_client.calls[3][:2],
            ("PATCH", "/api/agent/v1/crawler/candidates/7"),
        )
        self.assertEqual(fake_client.json_bodies[3], {"title": "副教授", "email": None})
        self.assertEqual(
            fake_client.calls[5][:2],
            ("POST", "/api/agent/v1/crawler/jobs/52/resume"),
        )
        self.assertEqual(fake_client.json_bodies[5], {"llm_profile_id": 3})
        self.assertEqual(
            fake_client.calls[8][:2],
            ("POST", "/api/agent/v1/crawler/jobs/52/delete"),
        )
        self.assertEqual(
            fake_client.calls[9][:2],
            ("POST", "/api/agent/v1/crawler/jobs/52/restore"),
        )
        self.assertEqual(
            fake_client.calls[10][:2],
            ("POST", "/api/agent/v1/crawler/jobs/52/prepare-approve"),
        )
        self.assertEqual(fake_client.json_bodies[10], {"candidate_ids": [7, 8]})
        self.assertEqual(
            fake_client.calls[11][:2],
            ("POST", "/api/agent/v1/crawler/jobs/52/prepare-retry"),
        )
        self.assertEqual(
            fake_client.json_bodies[11],
            {"clear_existing_data": False, "llm_profile_id": 3},
        )
        self.assertEqual(
            fake_client.calls[12][:2],
            ("POST", "/api/agent/v1/crawler/jobs/52/enrich"),
        )
        self.assertEqual(
            fake_client.json_bodies[12],
            {"candidate_ids": [7, 8], "llm_profile_id": 3},
        )

    def test_communication_group_commands_require_explicit_merge_flag(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/communication-groups": {
                    "id": 12,
                    "members": [{"id": 2, "profile_name": "身份 A"}],
                },
                "/api/agent/v1/communication-groups/12": {
                    "id": 12,
                    "members": [{"id": 2}, {"id": 3}, {"id": 4}],
                },
                "/api/agent/v1/communication-groups/12/delete": {
                    "ok": True,
                    "group_id": 12,
                },
            },
        )
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=fake_client,
        ):
            create = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "communication-groups",
                    "create",
                    "--identity-id",
                    "2",
                    "--identity-id",
                    "3",
                    "--confirm-merge-existing-groups",
                ],
            )
            update = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "communication-groups",
                    "update",
                    "12",
                    "--identity-id",
                    "2",
                    "--identity-id",
                    "3",
                    "--identity-id",
                    "4",
                ],
            )
            delete = self.runner.invoke(
                app,
                ["--format", "json", "communication-groups", "delete", "12"],
            )

        for result in (create, update, delete):
            self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(
            fake_client.calls[0][:2],
            ("POST", "/api/agent/v1/communication-groups"),
        )
        self.assertEqual(
            fake_client.json_bodies[0],
            {
                "identity_ids": [2, 3],
                "confirm_merge_existing_groups": True,
            },
        )
        self.assertEqual(
            fake_client.calls[1][:2],
            ("PUT", "/api/agent/v1/communication-groups/12"),
        )
        self.assertEqual(
            fake_client.json_bodies[1],
            {
                "identity_ids": [2, 3, 4],
                "confirm_merge_existing_groups": False,
            },
        )
        self.assertEqual(
            fake_client.calls[2][:2],
            ("POST", "/api/agent/v1/communication-groups/12/delete"),
        )

    def test_settings_update_merges_only_explicit_fields_with_current_settings(self) -> None:
        settings = {
            "match_analysis_job_worker_count": 1,
            "match_analysis_job_item_concurrency": 2,
            "match_analysis_job_interval_seconds": 5,
            "crawler_worker_count": 1,
            "crawler_profile_enrichment_concurrency": 2,
            "crawler_host_concurrency": 1,
            "crawler_agent_max_chunks_per_run": 2,
            "draft_max_tokens": 4096,
            "batch_draft_generation_concurrency": 2,
            "draft_rewrite_intensity": "moderate",
            "draft_rewrite_tone": "professional",
            "draft_rewrite_formality": "balanced",
            "draft_rewrite_length": "default",
            "draft_rewrite_specificity": "balanced",
            "draft_template_preservation": "balanced",
            "draft_custom_instruction": "",
            "intended_research_direction": "AI",
            "updated_at": "2026-08-04T00:00:00Z",
        }
        fake_client = _FakeAgentClient({"/api/agent/v1/settings": settings})
        with patch(
            "auto_email_sender_cli.commands.settings.AgentApiClient",
            return_value=fake_client,
        ):
            result = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "settings",
                    "update",
                    "--crawler-worker-count",
                    "3",
                    "--draft-rewrite-tone",
                    "friendly",
                ],
            )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(fake_client.calls[0][:2], ("GET", "/api/agent/v1/settings"))
        self.assertEqual(fake_client.calls[1][:2], ("PATCH", "/api/agent/v1/settings"))
        self.assertEqual(fake_client.json_bodies[1]["crawler_worker_count"], 3)
        self.assertEqual(fake_client.json_bodies[1]["draft_rewrite_tone"], "friendly")
        self.assertEqual(fake_client.json_bodies[1]["draft_max_tokens"], 4096)

    def test_identity_default_template_and_connection_commands_use_safe_routes(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/identities/2/default": {"id": 2, "is_default": True},
                "/api/agent/v1/identities/2/default-template": {
                    "id": 2,
                    "default_outreach_template_id": 7,
                },
                "/api/agent/v1/identities/2/smtp-test": {
                    "ok": False,
                    "message": "认证失败",
                    "possible_cause": "请检查授权码",
                },
                "/api/agent/v1/identities/2/imap-test": {
                    "ok": True,
                    "message": "连接成功",
                },
            },
        )
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=fake_client,
        ):
            set_default = self.runner.invoke(
                app,
                ["--format", "json", "identities", "set-default", "2"],
            )
            set_template = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "identities",
                    "set-default-template",
                    "2",
                    "--template-id",
                    "7",
                ],
            )
            smtp = self.runner.invoke(
                app,
                ["--format", "json", "identities", "test-smtp", "2"],
            )
            imap = self.runner.invoke(
                app,
                ["--format", "json", "identities", "test-imap", "2"],
            )

        for result in (set_default, set_template, smtp, imap):
            self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(
            fake_client.calls[0][:2],
            ("POST", "/api/agent/v1/identities/2/default"),
        )
        self.assertEqual(
            fake_client.calls[1][:2],
            ("POST", "/api/agent/v1/identities/2/default-template"),
        )
        self.assertEqual(fake_client.json_bodies[1], {"template_id": 7})
        self.assertEqual(
            fake_client.calls[2][:2],
            ("POST", "/api/agent/v1/identities/2/smtp-test"),
        )
        self.assertEqual(
            fake_client.calls[3][:2],
            ("POST", "/api/agent/v1/identities/2/imap-test"),
        )

    def test_llm_profile_default_models_and_test_commands_use_safe_routes(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/llm-profiles/4/default": {"id": 4, "is_default": True},
                "/api/agent/v1/llm-profiles/4/models": {
                    "profile_id": 4,
                    "ok": True,
                    "models": ["test-model"],
                    "trust_level": "untrusted_external_content",
                },
                "/api/agent/v1/llm-profiles/4/test": {
                    "profile_id": 4,
                    "ok": True,
                    "consumes_tokens": True,
                    "trust_level": "untrusted_external_content",
                },
            },
        )
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=fake_client,
        ):
            default_set = self.runner.invoke(
                app,
                ["--format", "json", "llm-profiles", "set-default", "4"],
            )
            models = self.runner.invoke(
                app,
                ["--format", "json", "llm-profiles", "models", "4"],
            )
            test = self.runner.invoke(
                app,
                ["--format", "json", "llm-profiles", "test", "4"],
            )

        for result in (default_set, models, test):
            self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(
            fake_client.calls[0][:2],
            ("POST", "/api/agent/v1/llm-profiles/4/default"),
        )
        self.assertEqual(
            fake_client.calls[1][:2],
            ("GET", "/api/agent/v1/llm-profiles/4/models"),
        )
        self.assertEqual(
            fake_client.calls[2][:2],
            ("POST", "/api/agent/v1/llm-profiles/4/test"),
        )

    def test_safe_identity_and_llm_settings_commands_use_agent_routes(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/identities/2/settings": {
                    "id": 2,
                    "profile_name": "更新后的身份",
                },
                "/api/agent/v1/llm-profiles/4/settings": {
                    "id": 4,
                    "model_name": "safe-model",
                },
            },
        )
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=fake_client,
        ):
            identity_update = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "identities",
                    "update-settings",
                    "2",
                    "--profile-name",
                    "更新后的身份",
                    "--daily-send-limit",
                    "8",
                    "--clear-match-threshold",
                ],
            )
            llm_update = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "llm-profiles",
                    "update-settings",
                    "4",
                    "--model-name",
                    "safe-model",
                    "--temperature",
                    "0.5",
                    "--clear-max-tokens",
                ],
            )
            conflicting_clear = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "identities",
                    "update-settings",
                    "2",
                    "--daily-send-limit",
                    "8",
                    "--clear-daily-send-limit",
                ],
            )

        self.assertEqual(identity_update.exit_code, 0, msg=identity_update.output)
        self.assertEqual(llm_update.exit_code, 0, msg=llm_update.output)
        self.assertNotEqual(conflicting_clear.exit_code, 0)
        self.assertEqual(
            fake_client.calls[0][:2],
            ("PUT", "/api/agent/v1/identities/2/settings"),
        )
        self.assertEqual(
            fake_client.json_bodies[0],
            {
                "profile_name": "更新后的身份",
                "match_threshold": None,
                "daily_send_limit": 8,
            },
        )
        self.assertEqual(
            fake_client.calls[1][:2],
            ("PUT", "/api/agent/v1/llm-profiles/4/settings"),
        )
        self.assertEqual(
            fake_client.json_bodies[1],
            {
                "model_name": "safe-model",
                "temperature": 0.5,
                "max_tokens": None,
            },
        )
        self.assertEqual(len(fake_client.calls), 2)

    def test_professor_export_downloads_the_requested_format(self) -> None:
        fake_client = _FakeAgentClient(
            {"/api/agent/v1/professors/export": b"name,email\nExported,export@example.edu\n"},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "professors.csv"
            with patch(
                "auto_email_sender_cli.commands.professors.AgentApiClient",
                return_value=fake_client,
            ):
                result = self.runner.invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "professors",
                        "export",
                        "--output",
                        output.as_posix(),
                        "--format",
                        "csv",
                    ],
                )
            exported_content = output.read_bytes()

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(exported_content, b"name,email\nExported,export@example.edu\n")
        self.assertEqual(fake_client.download_calls, ["/api/agent/v1/professors/export"])
        self.assertEqual(fake_client.download_params, [{"format": "csv"}])

    def test_diagnostics_commands_use_safe_routes_and_require_force_to_overwrite(self) -> None:
        log_client = _FakeAgentClient(
            {
                "/api/agent/v1/diagnostics/operation-logs": {
                    "items": [{"id": 7, "level": "error", "message": "已脱敏"}],
                    "total": 1,
                    "limit": 10,
                    "offset": 5,
                },
            },
        )
        export_client = _FakeAgentClient(
            {
                "/api/agent/v1/diagnostics/export": {
                    "total": 1,
                    "items": [{"id": 7, "message": "[REDACTED]"}],
                    "startup_logs": [],
                },
                "/api/agent/v1/diagnostics/crawler-debug/52/export": b'{"event":"crawl"}\n',
            },
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            export_path = directory / "diagnostics.json"
            debug_path = directory / "crawler.jsonl"
            export_path.write_text("keep this", encoding="utf-8")
            with patch(
                "auto_email_sender_cli.commands.common.AgentApiClient",
                return_value=log_client,
            ), patch(
                "auto_email_sender_cli.commands.diagnostics.AgentApiClient",
                return_value=export_client,
            ):
                logs = self.runner.invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "diagnostics",
                        "logs",
                        "--limit",
                        "10",
                        "--offset",
                        "5",
                        "--level",
                        "error",
                        "--category",
                        "diagnostics",
                    ],
                )
                blocked_export = self.runner.invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "diagnostics",
                        "export",
                        "--output",
                        export_path.as_posix(),
                    ],
                )
                self.assertEqual(export_path.read_text(encoding="utf-8"), "keep this")
                export = self.runner.invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "diagnostics",
                        "export",
                        "--output",
                        export_path.as_posix(),
                        "--force",
                        "--category",
                        "diagnostics",
                    ],
                )
                debug = self.runner.invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "diagnostics",
                        "crawler-debug",
                        "52",
                        "--output",
                        debug_path.as_posix(),
                    ],
                )

            self.assertEqual(logs.exit_code, 0, msg=logs.output)
            self.assertEqual(blocked_export.exit_code, 2, msg=blocked_export.output)
            self.assertEqual(
                json.loads(blocked_export.stdout)["error"]["code"],
                "OUTPUT_EXISTS",
            )
            self.assertEqual(export.exit_code, 0, msg=export.output)
            self.assertEqual(debug.exit_code, 0, msg=debug.output)
            self.assertEqual(debug_path.read_bytes(), b'{"event":"crawl"}\n')

        self.assertEqual(
            log_client.calls[0],
            (
                "GET",
                "/api/agent/v1/diagnostics/operation-logs",
                {
                    "level": "error",
                    "category": "diagnostics",
                    "limit": 10,
                    "offset": 5,
                },
            ),
        )
        self.assertEqual(
            export_client.calls[0],
            ("GET", "/api/agent/v1/diagnostics/export", {}),
        )
        self.assertEqual(
            export_client.calls[1],
            (
                "GET",
                "/api/agent/v1/diagnostics/export",
                {"category": "diagnostics"},
            ),
        )
        self.assertEqual(
            export_client.download_calls,
            ["/api/agent/v1/diagnostics/crawler-debug/52/export"],
        )

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

    def test_campaign_commands_use_safe_agent_routes_and_create_plans(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/campaigns": {
                    "items": [],
                    "next_cursor": None,
                    "has_more": False,
                },
                "/api/agent/v1/campaigns/prepare-create": {
                    "plan_id": "change_campaign_create",
                    "action": "campaign.create",
                    "status": "awaiting_confirmation",
                },
                "/api/agent/v1/campaigns/9/start-drafts": {
                    "id": 9,
                    "status": "running",
                },
                "/api/agent/v1/campaigns/9/pause": {
                    "id": 9,
                    "status": "paused",
                },
                "/api/agent/v1/campaigns/9/prepare-send": {
                    "plan_id": "change_campaign_send",
                    "action": "campaign.send",
                    "status": "awaiting_confirmation",
                },
            },
        )
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=fake_client,
        ):
            listed = self.runner.invoke(
                app,
                ["--format", "json", "campaigns", "list", "--identity-id", "2"],
            )
            created = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "campaigns",
                    "create",
                    "--name",
                    "二次联系",
                    "--identity-id",
                    "2",
                    "--llm-profile-id",
                    "3",
                    "--professor-id",
                    "7",
                    "--professor-id",
                    "8",
                    "--generation-mode",
                    "ai_rewrite",
                    "--template-id",
                    "4",
                    "--reference-material-id",
                    "5",
                    "--attachment-material-id",
                    "6",
                ],
            )
            started = self.runner.invoke(
                app,
                ["--format", "json", "campaigns", "start-drafts", "9"],
            )
            paused = self.runner.invoke(
                app,
                ["--format", "json", "campaigns", "pause", "9"],
            )
            send_prepared = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "campaigns",
                    "prepare-send",
                    "9",
                    "--item-id",
                    "11",
                    "--item-id",
                    "12",
                ],
            )

        for result in (listed, created, started, paused, send_prepared):
            self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(fake_client.calls[0][:2], ("GET", "/api/agent/v1/campaigns"))
        self.assertEqual(fake_client.calls[0][2]["identity_id"], 2)
        self.assertEqual(
            fake_client.calls[1][:2],
            ("POST", "/api/agent/v1/campaigns/prepare-create"),
        )
        self.assertEqual(
            fake_client.json_bodies[1],
            {
                "name": "二次联系",
                "identity_id": 2,
                "llm_profile_id": 3,
                "professor_ids": [7, 8],
                "generation_mode": "ai_rewrite",
                "template_id": 4,
                "reference_material_id": 5,
                "attachment_material_ids": [6],
                "subject": None,
                "body_text": None,
                "body_html": None,
                "schedule_type": "immediate",
                "window_start_time": None,
                "window_end_time": None,
                "emails_per_window": None,
                "scheduled_dates": [],
            },
        )
        self.assertEqual(
            fake_client.calls[2][:2],
            ("POST", "/api/agent/v1/campaigns/9/start-drafts"),
        )
        self.assertEqual(
            fake_client.calls[3][:2],
            ("POST", "/api/agent/v1/campaigns/9/pause"),
        )
        self.assertEqual(
            fake_client.calls[4][:2],
            ("POST", "/api/agent/v1/campaigns/9/prepare-send"),
        )
        self.assertEqual(fake_client.json_bodies[4], {"item_ids": [11, 12]})

    def test_campaign_lifecycle_commands_use_agent_routes(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/campaigns/9/stop": {"id": 9, "status": "stopped"},
                "/api/agent/v1/campaigns/9/archive": {"id": 9, "status": "stopped"},
                "/api/agent/v1/campaigns/9/restore": {"id": 9, "status": "stopped"},
                "/api/agent/v1/campaigns/9/items/11/remove": {
                    "id": 9,
                    "target_count": 1,
                },
                "/api/agent/v1/campaigns/9/items/11/cancel-send": {
                    "id": 9,
                    "canceled_send_count": 1,
                },
                "/api/agent/v1/campaigns/9/items/11/prepare-restore-send": {
                    "plan_id": "change_campaign_item_restore",
                    "action": "campaign.item_send_restore",
                    "status": "awaiting_confirmation",
                },
                "/api/agent/v1/campaigns/9/items/11/retry-draft": {
                    "id": 9,
                    "pending_generation_count": 1,
                },
                "/api/agent/v1/campaigns/9/prepare-resume": {
                    "plan_id": "change_campaign_resume",
                    "action": "campaign.resume",
                    "status": "awaiting_confirmation",
                },
            },
        )
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=fake_client,
        ):
            results = [
                self.runner.invoke(
                    app,
                    ["--format", "json", "campaigns", "stop", "9"],
                ),
                self.runner.invoke(
                    app,
                    ["--format", "json", "campaigns", "archive", "9"],
                ),
                self.runner.invoke(
                    app,
                    ["--format", "json", "campaigns", "restore", "9"],
                ),
                self.runner.invoke(
                    app,
                    ["--format", "json", "campaigns", "remove-item", "9", "11"],
                ),
                self.runner.invoke(
                    app,
                    ["--format", "json", "campaigns", "cancel-item-send", "9", "11"],
                ),
                self.runner.invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "campaigns",
                        "prepare-restore-item-send",
                        "9",
                        "11",
                    ],
                ),
                self.runner.invoke(
                    app,
                    ["--format", "json", "campaigns", "retry-item-draft", "9", "11"],
                ),
                self.runner.invoke(
                    app,
                    ["--format", "json", "campaigns", "prepare-resume", "9"],
                ),
            ]

        for result in results:
            self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(
            [call[:2] for call in fake_client.calls],
            [
                ("POST", "/api/agent/v1/campaigns/9/stop"),
                ("POST", "/api/agent/v1/campaigns/9/archive"),
                ("POST", "/api/agent/v1/campaigns/9/restore"),
                ("POST", "/api/agent/v1/campaigns/9/items/11/remove"),
                ("POST", "/api/agent/v1/campaigns/9/items/11/cancel-send"),
                (
                    "POST",
                    "/api/agent/v1/campaigns/9/items/11/prepare-restore-send",
                ),
                ("POST", "/api/agent/v1/campaigns/9/items/11/retry-draft"),
                ("POST", "/api/agent/v1/campaigns/9/prepare-resume"),
            ],
        )

    def test_campaign_resend_context_uses_a_read_only_agent_route(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/campaigns/9/resend-context": {
                    "task": {"id": 9, "name": "原活动"},
                    "items": [],
                    "warnings": [],
                },
            },
        )
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=fake_client,
        ):
            result = self.runner.invoke(
                app,
                ["--format", "json", "campaigns", "resend-context", "9"],
            )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(
            fake_client.calls[0][:2],
            ("GET", "/api/agent/v1/campaigns/9/resend-context"),
        )

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
        self.data_bodies: list[dict[str, object] | None] = []
        self.file_bodies: list[object | None] = []
        self.download_calls: list[str] = []
        self.download_params: list[dict[str, object] | None] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
        json_body: object | None = None,
        data: dict[str, object] | None = None,
        files: object | None = None,
        **_: object,
    ) -> object:
        self.calls.append((method, path, params))
        self.json_bodies.append(json_body)
        self.data_bodies.append(data)
        self.file_bodies.append(files)
        return self.responses[path]

    def download_bytes(
        self,
        path: str,
        *,
        params: dict[str, object] | None = None,
    ) -> bytes:
        self.download_calls.append(path)
        self.download_params.append(params)
        response = self.responses[path]
        assert isinstance(response, bytes)
        return response


if __name__ == "__main__":
    unittest.main()
