from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from typer.testing import CliRunner

import auto_email_sender_cli.main as cli_main
from auto_email_sender_cli.main import app
from auto_email_sender_cli.commands.common import augment_state_metadata
from auto_email_sender_cli.agent_installation import (
    AGENT_SUPPORT_MANIFEST_SCHEMA_VERSION,
    SUPPORTED_AGENT_SUPPORT_MANIFEST_SCHEMA_VERSIONS,
    _sha256_directory,
    inspect_agent_skill_installation,
)
from auto_email_sender_cli.capabilities import (
    CAPABILITIES,
    list_capabilities,
    list_capability_cards,
    list_resource_catalog,
)
from auto_email_sender_cli.describe import describe_command, describe_commands
from auto_email_sender_cli.errors import (
    CliError,
    RuntimeUnavailableError,
    redact_error_details,
)
from auto_email_sender_cli.result_protocol import prepare_result_data


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_version_json_uses_stable_envelope(self) -> None:
        result = self.runner.invoke(app, ["--format", "json", "version"])

        self.assertEqual(result.exit_code, 0, msg=result.output)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["_meta"]["schema_version"], "4")
        self.assertEqual(payload["_meta"]["command"], "version")
        self.assertEqual(
            payload["data"]["schema_version"], payload["_meta"]["schema_version"]
        )
        self.assertEqual(payload["data"]["contract_version"], "4")
        self.assertEqual(payload["data"]["catalog_version"], "4")
        self.assertNotIn("build_revision", payload["_meta"])
        self.assertNotIn("warnings", payload["_meta"])
        self.assertIn(
            payload["data"]["build_kind"], {"development", "embedded", "override"}
        )
        self.assertNotIn("agent_guide", payload["_meta"])

    def test_wait_stops_when_crawler_needs_review(self) -> None:
        fake_client = _FakeAgentClient(
            {"/api/agent/v1/crawler/jobs/52": {"id": 52, "status": "needs_review"}},
        )
        with patch(
            "auto_email_sender_cli.commands.wait.AgentApiClient",
            return_value=fake_client,
        ):
            result = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "wait",
                    "--resource",
                    "crawler.jobs",
                    "--id",
                    "52",
                ],
            )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["data"]["state_category"], "attention_required")
        self.assertTrue(payload["data"]["settled"])
        self.assertFalse(payload["data"]["terminal"])
        self.assertFalse(payload["data"]["timed_out"])
        self.assertEqual(payload["data"]["poll_count"], 1)

    def test_wait_terminal_condition_does_not_treat_review_as_terminal(self) -> None:
        fake_client = _FakeAgentClient(
            {"/api/agent/v1/crawler/jobs/52": {"id": 52, "status": "needs_review"}},
        )
        with patch(
            "auto_email_sender_cli.commands.wait.AgentApiClient",
            return_value=fake_client,
        ):
            result = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "wait",
                    "--resource",
                    "crawler.jobs",
                    "--id",
                    "52",
                    "--until",
                    "terminal",
                    "--timeout-seconds",
                    "0",
                ],
            )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["data"]["settled"])
        self.assertFalse(payload["data"]["terminal"])
        self.assertTrue(payload["data"]["timed_out"])
        self.assertEqual(payload["data"]["until"], "terminal")

    def test_wait_rejects_unknown_stop_condition(self) -> None:
        result = self.runner.invoke(
            app,
            [
                "--format",
                "json",
                "wait",
                "--resource",
                "crawler.jobs",
                "--id",
                "52",
                "--until",
                "unknown",
            ],
        )

        self.assertEqual(result.exit_code, 2, msg=result.output)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["error"]["code"], "INVALID_WAIT_CONDITION")
        self.assertEqual(
            payload["error"]["details"]["available_conditions"],
            ["settled", "terminal"],
        )

    def test_wait_aggregates_multiple_resources_without_full_results(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/crawler/jobs/51": {"id": 51, "status": "needs_review"},
                "/api/agent/v1/crawler/jobs/52": {"id": 52, "status": "failed"},
            },
        )
        with patch(
            "auto_email_sender_cli.commands.wait.AgentApiClient",
            return_value=fake_client,
        ):
            result = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "wait",
                    "--resource",
                    "crawler.jobs",
                    "--id",
                    "51",
                    "--id",
                    "52",
                ],
            )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        payload = json.loads(result.stdout)["data"]
        self.assertEqual(payload["total_count"], 2)
        self.assertEqual(payload["settled_count"], 2)
        self.assertEqual(payload["terminal_count"], 1)
        self.assertEqual(payload["by_status"], {"failed": 1, "needs_review": 1})
        self.assertEqual(payload["failed_ids"], [52])
        self.assertEqual(payload["attention_required_ids"], [51])
        self.assertEqual(payload["timed_out_ids"], [])
        self.assertEqual(payload["poll_count"], 2)
        self.assertEqual(payload["poll_rounds"], 1)
        self.assertNotIn("result", payload)
        self.assertTrue(
            all("available_actions" not in item for item in payload["resources"])
        )
        groups = {item["status"]: item for item in payload["action_groups"]}
        self.assertEqual(groups["needs_review"]["ids"], [51])
        review_actions = {
            item["action"]: item for item in groups["needs_review"]["available_actions"]
        }
        self.assertEqual(review_actions["enrich"]["required_input"], ["selection_mode"])
        self.assertNotIn("arguments", review_actions["enrich"])

    def test_wait_many_reports_only_unsettled_ids_on_timeout(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/crawler/jobs/51": {"id": 51, "status": "needs_review"},
                "/api/agent/v1/crawler/jobs/52": {"id": 52, "status": "running"},
            },
        )
        with patch(
            "auto_email_sender_cli.commands.wait.AgentApiClient",
            return_value=fake_client,
        ):
            result = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "wait",
                    "--resource",
                    "crawler.jobs",
                    "--id",
                    "51",
                    "--id",
                    "52",
                    "--timeout-seconds",
                    "0",
                ],
            )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        payload = json.loads(result.stdout)["data"]
        self.assertTrue(payload["timed_out"])
        self.assertEqual(payload["timed_out_ids"], [52])
        self.assertEqual(payload["settled_count"], 1)

    def test_wait_many_isolates_permanent_query_failures(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/crawler/jobs/51": {"id": 51, "status": "needs_review"},
                "/api/agent/v1/crawler/jobs/52": CliError(
                    code="CRAWL_JOB_NOT_FOUND",
                    message="任务不存在",
                    exit_code=4,
                    retryable=False,
                ),
            },
        )
        with patch(
            "auto_email_sender_cli.commands.wait.AgentApiClient",
            return_value=fake_client,
        ):
            result = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "wait",
                    "--resource",
                    "crawler.jobs",
                    "--id",
                    "51",
                    "--id",
                    "52",
                ],
            )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        payload = json.loads(result.stdout)["data"]
        self.assertEqual(payload["settled_count"], 1)
        self.assertEqual(payload["query_failed_ids"], [52])
        self.assertEqual(payload["timed_out_ids"], [])
        self.assertFalse(payload["timed_out"])
        self.assertEqual(
            payload["query_failures"],
            [
                {
                    "id": 52,
                    "code": "CRAWL_JOB_NOT_FOUND",
                    "reason": "任务不存在",
                    "retryable": False,
                }
            ],
        )
        self.assertEqual(payload["by_status"], {"needs_review": 1, "unknown": 1})

    def test_wait_single_stops_before_request_after_deadline(self) -> None:
        fake_client = _FakeAgentClient(
            {"/api/agent/v1/crawler/jobs/52": {"id": 52, "status": "running"}},
        )
        with (
            patch(
                "auto_email_sender_cli.commands.wait.AgentApiClient",
                return_value=fake_client,
            ),
            patch("auto_email_sender_cli.commands.wait.time.sleep", return_value=None),
        ):
            result = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "wait",
                    "--resource",
                    "crawler.jobs",
                    "--id",
                    "52",
                    "--timeout-seconds",
                    "0.01",
                    "--interval-seconds",
                    "0.1",
                ],
            )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        payload = json.loads(result.stdout)["data"]
        self.assertTrue(payload["timed_out"])
        self.assertEqual(payload["poll_count"], 1)
        self.assertEqual(payload["poll_rounds"], 1)

    def test_wait_many_polls_resources_concurrently(self) -> None:
        fake_client = _ConcurrentWaitAgentClient()
        with patch(
            "auto_email_sender_cli.commands.wait.AgentApiClient",
            return_value=fake_client,
        ):
            result = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "wait",
                    "--resource",
                    "crawler.jobs",
                    "--id",
                    "51",
                    "--id",
                    "52",
                    "--id",
                    "53",
                ],
            )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertGreaterEqual(fake_client.max_active_requests, 2)
        payload = json.loads(result.stdout)["data"]
        self.assertEqual(payload["settled_count"], 3)
        self.assertEqual(payload["poll_count"], 3)

    def test_wait_many_clears_retryable_failure_after_recovery(self) -> None:
        fake_client = _RecoveringWaitAgentClient()
        with patch(
            "auto_email_sender_cli.commands.wait.AgentApiClient",
            return_value=fake_client,
        ):
            result = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "wait",
                    "--resource",
                    "crawler.jobs",
                    "--id",
                    "51",
                    "--id",
                    "52",
                    "--timeout-seconds",
                    "1",
                    "--interval-seconds",
                    "0.1",
                ],
            )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        envelope = json.loads(result.stdout)
        payload = envelope["data"]
        self.assertEqual(payload["settled_count"], 2)
        self.assertEqual(payload["query_failed_ids"], [])
        self.assertEqual(payload["query_failures"], [])
        self.assertEqual(payload["poll_count"], 3)
        self.assertEqual(payload["poll_rounds"], 2)
        self.assertNotIn("warnings", envelope["_meta"])

    def test_crawler_enrich_can_select_all_without_candidate_ids(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/crawler/jobs/52/enrich": {
                    "phase": "submission",
                    "selection": {
                        "mode": "all",
                        "matched_count": 2,
                        "eligible_count": 2,
                    },
                    "submission": {"queued_count": 2},
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
                    "crawler",
                    "jobs",
                    "enrich",
                    "52",
                    "--selection",
                    "all",
                    "--exclude-candidate-id",
                    "8",
                ],
            )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(
            fake_client.json_bodies[0],
            {
                "selection": {
                    "mode": "all",
                    "ids": [],
                    "filter": {},
                    "exclude_ids": [8],
                },
                "llm_profile_id": None,
            },
        )

    def test_crawler_enrich_rejects_inconsistent_selection_options(self) -> None:
        cases = (
            ["--selection", "ids"],
            ["--selection", "all", "--candidate-id", "7"],
            ["--selection", "filter"],
            ["--selection", "ids", "--candidate-id", "7", "--review-status", "pending"],
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result = self.runner.invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "crawler",
                        "jobs",
                        "enrich",
                        "52",
                        *arguments,
                    ],
                )
                self.assertEqual(result.exit_code, 2, msg=result.output)
                self.assertEqual(
                    json.loads(result.stdout)["error"]["code"], "INVALID_ARGUMENT"
                )

    def test_crawler_approve_can_select_all_and_rejects_inconsistent_options(
        self,
    ) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/crawler/jobs/52/prepare-approve": {
                    "plan_id": "plan-52",
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
                    "crawler",
                    "jobs",
                    "approve",
                    "52",
                    "--selection",
                    "all",
                    "--exclude-candidate-id",
                    "8",
                ],
            )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(
            fake_client.json_bodies[0],
            {
                "selection": {
                    "mode": "all",
                    "ids": [],
                    "filter": {},
                    "exclude_ids": [8],
                },
            },
        )

        cases = (
            ["--selection", "ids"],
            ["--selection", "all", "--candidate-id", "7"],
            ["--selection", "filter"],
            ["--selection", "ids", "--candidate-id", "7", "--review-status", "pending"],
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                invalid = self.runner.invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "crawler",
                        "jobs",
                        "approve",
                        "52",
                        *arguments,
                    ],
                )
                self.assertEqual(invalid.exit_code, 2, msg=invalid.output)
                self.assertEqual(
                    json.loads(invalid.stdout)["error"]["code"], "INVALID_ARGUMENT"
                )

    def test_crawler_batch_commands_send_compact_item_payloads(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/crawler/jobs/create-many": {
                    "phase": "submission",
                    "requested_count": 2,
                    "created_count": 2,
                    "failed_count": 0,
                    "created_job_ids": [51, 52],
                    "failures": [],
                },
                "/api/agent/v1/crawler/jobs/enrich-many": {
                    "phase": "submission",
                    "requested_count": 2,
                    "accepted_count": 2,
                    "failed_count": 0,
                    "queued_count": 4,
                    "skipped_count": 1,
                    "items": [],
                    "failures": [],
                },
            },
        )
        items = [
            {
                "university": "示例大学 A",
                "school": "计算机学院",
                "start_url": "https://a.example.edu/faculty",
            },
            {
                "university": "示例大学 B",
                "school": "电子学院",
                "start_url": "https://b.example.edu/faculty",
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            items_file = Path(directory) / "crawl-jobs.json"
            items_file.write_text(json.dumps({"items": items}), encoding="utf-8")
            with patch(
                "auto_email_sender_cli.commands.common.AgentApiClient",
                return_value=fake_client,
            ):
                created = self.runner.invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "crawler",
                        "jobs",
                        "create-many",
                        "--items-file",
                        str(items_file),
                    ],
                )
                enriched = self.runner.invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "crawler",
                        "jobs",
                        "enrich-many",
                        "--job-id",
                        "51",
                        "--job-id",
                        "52",
                        "--selection",
                        "filter",
                        "--review-status",
                        "pending",
                        "--llm-profile-id",
                        "3",
                    ],
                )

        self.assertEqual(created.exit_code, 0, msg=created.output)
        self.assertEqual(enriched.exit_code, 0, msg=enriched.output)
        self.assertEqual(fake_client.json_bodies[0], {"items": items})
        self.assertEqual(
            fake_client.json_bodies[1],
            {
                "items": [
                    {
                        "job_id": job_id,
                        "selection": {
                            "mode": "filter",
                            "ids": [],
                            "filter": {"review_status": ["pending"]},
                            "exclude_ids": [],
                        },
                        "llm_profile_id": 3,
                    }
                    for job_id in (51, 52)
                ],
            },
        )

    def test_crawler_batch_commands_reject_ambiguous_or_oversized_inputs(self) -> None:
        duplicate_jobs = self.runner.invoke(
            app,
            [
                "--format",
                "json",
                "crawler",
                "jobs",
                "enrich-many",
                "--job-id",
                "51",
                "--job-id",
                "51",
                "--selection",
                "all",
            ],
        )
        invalid_mode = self.runner.invoke(
            app,
            [
                "--format",
                "json",
                "crawler",
                "jobs",
                "enrich-many",
                "--job-id",
                "51",
                "--selection",
                "ids",
            ],
        )
        for result in (duplicate_jobs, invalid_mode):
            self.assertEqual(result.exit_code, 2, msg=result.output)
            self.assertEqual(
                json.loads(result.stdout)["error"]["code"], "INVALID_ARGUMENT"
            )

    def test_parser_failures_always_use_the_json_error_envelope(self) -> None:
        cases = (
            (["--format", "json", "professors", "get"], "professors.get"),
            (
                [
                    "--format",
                    "json",
                    "drafts",
                    "generate",
                    "--professor-id",
                    "1",
                    "--identity-id",
                    "1",
                    "--llm-profile-id",
                    "1",
                    "--generation-mode",
                    "invalid",
                ],
                "drafts.generate",
            ),
            (["--format", "json", "version", "--unknown-option"], "version"),
            (["--format", "json", "unknown-command"], "cli"),
        )

        for arguments, command in cases:
            with self.subTest(arguments=arguments):
                result = self.runner.invoke(app, arguments)
                self.assertEqual(result.exit_code, 2, msg=result.output)
                payload = json.loads(result.stdout)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["error"]["code"], "INVALID_ARGUMENT")
                self.assertEqual(payload["_meta"]["command"], command)

    def test_guide_version_matches_cli_version_metadata(self) -> None:
        guide = self.runner.invoke(app, ["--format", "json", "guide"])
        version = self.runner.invoke(app, ["--format", "json", "version"])

        self.assertEqual(guide.exit_code, 0, msg=guide.output)
        self.assertEqual(version.exit_code, 0, msg=version.output)
        guide_payload = json.loads(guide.stdout)
        version_payload = json.loads(version.stdout)
        self.assertEqual(
            guide_payload["data"]["version"], version_payload["data"]["cli_version"]
        )
        self.assertTrue(guide_payload["data"]["deprecated"])

    def test_json_alias_is_supported(self) -> None:
        result = self.runner.invoke(app, ["--json", "guide", "--topic", "sending"])

        self.assertEqual(result.exit_code, 0, msg=result.output)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["data"]["topic"], "sending")
        self.assertIn("describe", " ".join(payload["data"]["rules"]))

    def test_unknown_guide_topic_returns_machine_error(self) -> None:
        result = self.runner.invoke(
            app,
            ["--format", "json", "guide", "--topic", "missing"],
        )

        self.assertEqual(result.exit_code, 2)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "INVALID_GUIDE_TOPIC")

    def test_error_output_redacts_credentials_in_messages_and_details(self) -> None:
        result = self.runner.invoke(
            app,
            ["--format", "json", "guide", "--topic", "password=known-secret"],
        )

        self.assertEqual(result.exit_code, 2, msg=result.output)
        self.assertNotIn("known-secret", result.stdout)
        redacted = redact_error_details(
            {
                "api_key": "api-secret",
                "nested": [{"access_token": "token-secret"}],
                "comparison_token": "keep-for-retry",
            },
        )
        self.assertNotIn("api-secret", json.dumps(redacted))
        self.assertNotIn("token-secret", json.dumps(redacted))
        self.assertEqual(redacted["comparison_token"], "keep-for-retry")

    def test_capabilities_report_available_and_planned_commands_honestly(self) -> None:
        result = self.runner.invoke(
            app,
            ["--format", "json", "capabilities", "--command", "communications"],
        )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["data"]["view"], "commands")
        self.assertEqual(payload["data"]["items"][0]["availability"], "available")
        self.assertIn("risk", payload["data"]["items"][0])
        self.assertIn("traits", payload["data"]["items"][0]["risk"])
        self.assertTrue(
            any(
                item["availability"] == "available" for item in payload["data"]["items"]
            )
        )

    def test_capabilities_mark_desktop_only_areas_as_unavailable(self) -> None:
        # Inspect the manifest directly: root full discovery is intentionally
        # rejected by the CLI because it needlessly consumes an Agent context.
        items = list_capabilities()
        by_command = {item["command"]: item for item in items}
        self.assertEqual(by_command["professors.create"]["availability"], "available")
        self.assertEqual(by_command["professors.export"]["availability"], "available")
        self.assertEqual(
            by_command["templates.import-file"]["availability"], "available"
        )
        self.assertEqual(by_command["drafts.rewrite"]["availability"], "available")
        self.assertTrue(by_command["drafts.rewrite"]["external_action"])
        self.assertEqual(
            by_command["materials.prepare-delete"]["availability"], "available"
        )
        self.assertEqual(by_command["communications.sync"]["availability"], "available")
        self.assertEqual(
            by_command["professors.tags.prepare-bulk"]["availability"],
            "available",
        )
        self.assertEqual(
            by_command["professors.tags.usage"]["availability"], "available"
        )
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
        self.assertEqual(
            by_command["matching.jobs.create"]["availability"], "available"
        )
        self.assertEqual(by_command["matching.jobs.create"]["risk_level"], "L2")
        self.assertTrue(by_command["matching.jobs.create"]["external_action"])
        self.assertEqual(
            by_command["crawler.jobs.approve"]["availability"], "available"
        )
        self.assertTrue(by_command["crawler.jobs.approve"]["requires_plan"])
        self.assertEqual(by_command["crawler.jobs.events"]["availability"], "available")
        self.assertEqual(by_command["crawler.jobs.retry"]["availability"], "available")
        self.assertTrue(by_command["crawler.jobs.retry"]["requires_plan"])
        self.assertEqual(by_command["crawler.jobs.enrich"]["availability"], "available")
        self.assertTrue(by_command["crawler.jobs.enrich"]["external_action"])
        self.assertEqual(by_command["campaigns.create"]["availability"], "available")
        self.assertTrue(by_command["campaigns.create"]["requires_plan"])
        self.assertEqual(
            by_command["campaigns.resend-context"]["availability"], "available"
        )
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
        self.assertEqual(
            by_command["enrichment.jobs.create"]["availability"], "available"
        )
        self.assertTrue(by_command["enrichment.jobs.create"]["external_action"])
        self.assertEqual(
            by_command["communication-groups.create"]["availability"], "available"
        )
        self.assertEqual(by_command["settings.update"]["availability"], "available")
        self.assertEqual(
            by_command["identities.update-settings"]["availability"], "available"
        )
        self.assertEqual(
            by_command["identities.test-smtp"]["availability"], "available"
        )
        self.assertEqual(
            by_command["llm-profiles.update-settings"]["availability"], "available"
        )
        self.assertEqual(
            by_command["llm-profiles.set-default"]["availability"], "available"
        )
        self.assertEqual(by_command["llm-profiles.models"]["availability"], "available")
        self.assertTrue(by_command["llm-profiles.models"]["external_action"])
        self.assertEqual(by_command["llm-profiles.test"]["risk_level"], "L2")
        self.assertEqual(by_command["diagnostics.logs"]["availability"], "available")
        self.assertEqual(
            by_command["diagnostics.crawler-debug"]["availability"], "available"
        )
        self.assertEqual(by_command["workspaces.get"]["availability"], "available")
        self.assertEqual(by_command["workspaces.ensure-task"]["risk_level"], "L1")
        self.assertTrue(by_command["workspaces.refresh-replies"]["external_action"])
        self.assertEqual(
            by_command["tasks.cancel-schedule"]["availability"], "available"
        )
        self.assertEqual(by_command["tasks.continue-manually"]["risk_level"], "L1")
        self.assertEqual(
            by_command["tasks.start-follow-up"]["availability"], "available"
        )
        self.assertEqual(by_command["tasks.set-primary-material"]["risk_level"], "L2")
        self.assertTrue(by_command["tasks.set-primary-material"]["external_action"])
        self.assertEqual(
            by_command["tasks.set-outreach-config"]["availability"], "available"
        )
        self.assertEqual(by_command["tasks.calculate-match"]["risk_level"], "L2")
        self.assertTrue(by_command["tasks.calculate-match"]["external_action"])
        self.assertEqual(by_command["test-email.get"]["availability"], "available")
        self.assertEqual(by_command["test-email.prepare-send"]["risk_level"], "L3")
        self.assertTrue(by_command["test-email.prepare-send"]["requires_plan"])

    def test_capabilities_default_to_a_bounded_resource_catalog(self) -> None:
        result = self.runner.invoke(app, ["--format", "json", "capabilities"])

        self.assertEqual(result.exit_code, 0, msg=result.output)
        payload = json.loads(result.stdout)["data"]
        self.assertEqual(payload["view"], "catalog")
        self.assertEqual(payload["summary"]["commands"], len(CAPABILITIES))
        self.assertTrue(payload["items"])
        self.assertTrue(
            all(
                "resource" in item and "command" not in item
                for item in payload["items"]
            )
        )
        self.assertIn("catalog_revision", payload)
        # The old default emitted every leaf's detailed metadata.  This limit
        # protects the routine discovery path from consuming an Agent turn.
        semantic_output = json.dumps(
            json.loads(result.stdout),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.assertLess(len(semantic_output), 5_000)
        self.assertLess(len(result.stdout.encode("utf-8")), 6_000)
        system = next(item for item in payload["items"] if item["resource"] == "system")
        self.assertIn("delegated_gateway", system["traits"])
        self.assertIn("mutates", system["traits"])
        self.assertIn("external_action", system["traits"])

        invoke = list_capability_cards(command="invoke")[0]
        self.assertIn("delegated_effects", invoke["risk"]["traits"])
        self.assertIn("requires_target_contract", invoke["risk"]["traits"])

        professors = self.runner.invoke(
            app,
            ["--format", "json", "capabilities", "--resource", "professors"],
        )
        self.assertEqual(professors.exit_code, 0, msg=professors.output)
        self.assertLess(len(professors.stdout.encode("utf-8")), 8_000)

    def test_every_catalog_resource_can_be_selected_without_guessing_aliases(
        self,
    ) -> None:
        catalog = list_resource_catalog()
        self.assertTrue(catalog)
        for resource_record in catalog:
            resource = str(resource_record["resource"])
            with self.subTest(resource=resource):
                selected = list_capability_cards(resource=resource)
                self.assertTrue(selected)
                self.assertTrue(
                    all(
                        item["resource"] == resource
                        or str(item["resource"]).startswith(f"{resource}.")
                        for item in selected
                    ),
                )

    def test_unavailable_capabilities_are_describable_manual_action_stubs(self) -> None:
        unavailable = [
            item for item in CAPABILITIES if item.availability != "available"
        ]
        self.assertTrue(unavailable)
        for capability in unavailable:
            with self.subTest(command=capability.command):
                cards = list_capability_cards(command=capability.command)
                self.assertEqual(len(cards), 1)
                self.assertTrue(cards[0]["unavailable_reason"])
                self.assertTrue(cards[0]["manual_action"]["location"])

                description = describe_command(app, capability.command)
                self.assertIsNotNone(description)
                assert description is not None
                self.assertEqual(description["kind"], "unavailable")
                self.assertEqual(
                    description["unavailability"]["availability"],
                    capability.availability,
                )
                self.assertTrue(
                    description["unavailability"]["manual_action"]["instruction"],
                )

                result = self.runner.invoke(
                    app,
                    ["--format", "json", "describe", "--command", capability.command],
                )
                self.assertEqual(result.exit_code, 0, msg=result.output)
                self.assertEqual(
                    json.loads(result.stdout)["data"]["unavailability"]["availability"],
                    capability.availability,
                )

    def test_unsupported_root_options_fail_closed_on_system_commands(self) -> None:
        cases = (
            (["--filter", '{"id":{"eq":1}}', "capabilities"], "FILTER_NOT_SUPPORTED"),
            (["--if-revision", "arbitrary", "version"], "IF_REVISION_REQUIRES_WRITE"),
            (["--expand", "body_text", "capabilities"], "PROJECTION_NOT_SUPPORTED"),
            (["--projection", "summary", "version"], "PROJECTION_NOT_SUPPORTED"),
            (
                ["--output-file", "ignored.jsonl", "doctor"],
                "OUTPUT_FILE_REQUIRES_COLLECTION",
            ),
        )
        for arguments, error_code in cases:
            with self.subTest(arguments=arguments):
                result = self.runner.invoke(app, ["--format", "json", *arguments])
                self.assertEqual(result.exit_code, 2, msg=result.output)
                self.assertEqual(json.loads(result.stdout)["error"]["code"], error_code)

    def test_capabilities_can_negotiate_an_unchanged_scope_without_repeating_catalog(
        self,
    ) -> None:
        initial = self.runner.invoke(app, ["--format", "json", "capabilities"])
        self.assertEqual(initial.exit_code, 0, msg=initial.output)
        initial_data = json.loads(initial.stdout)["data"]

        cached = self.runner.invoke(
            app,
            [
                "--format",
                "json",
                "capabilities",
                "--since",
                initial_data["scope_revision"],
            ],
        )
        self.assertEqual(cached.exit_code, 0, msg=cached.output)
        cached_data = json.loads(cached.stdout)["data"]
        self.assertEqual(
            cached_data["catalog_revision"], initial_data["catalog_revision"]
        )
        self.assertEqual(cached_data["scope_revision"], initial_data["scope_revision"])
        self.assertEqual(cached_data["items"], [])
        self.assertTrue(cached_data["summary"]["unchanged"])
        self.assertEqual(cached_data["cache"]["status"], "not_modified")
        self.assertLess(len(cached.stdout.encode("utf-8")), 1_000)

        different_view = self.runner.invoke(
            app,
            [
                "--format",
                "json",
                "capabilities",
                "--view",
                "commands",
                "--resource",
                "professors",
                "--since",
                initial_data["scope_revision"],
            ],
        )
        self.assertEqual(different_view.exit_code, 0, msg=different_view.output)
        different_view_data = json.loads(different_view.stdout)["data"]
        self.assertNotEqual(
            different_view_data["scope_revision"], initial_data["scope_revision"]
        )
        self.assertTrue(different_view_data["items"])
        self.assertEqual(different_view_data["cache"]["status"], "stale")

    def test_capabilities_are_available_without_a_running_desktop_app(self) -> None:
        result = self.runner.invoke(app, ["--format", "json", "capabilities"])

        self.assertEqual(result.exit_code, 0, msg=result.output)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["data"]["items"])

    def test_capabilities_accept_spaced_command_names_and_suggest_unknown_commands(
        self,
    ) -> None:
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
        self.assertIn(
            "drafts.generate", missing_payload["error"]["details"]["suggestions"]
        )

    def test_capabilities_search_ranks_multilingual_intents_and_typos(self) -> None:
        cases = (
            ("导入导师", "professors.import"),
            ("查看回信", "communications.threads.list"),
            ("generate email draft", "drafts.generate"),
            ("professers improt", "professors.import"),
            ("列出当前系统中所有姓名包含英文字母的导师", "professors.list"),
            ("批量将指定导师移入回收站", "professors.prepare-bulk-archive"),
            ("确认并执行已有变更计划", "plans.execute"),
            ("准备社区导师批量投稿包", "professors.community.export-package"),
            ("查看社区导师库", "professors.community.catalog"),
        )
        for query, expected in cases:
            with self.subTest(query=query):
                result = self.runner.invoke(
                    app,
                    ["--format", "json", "capabilities", "--query", query],
                )
                self.assertEqual(result.exit_code, 0, msg=result.output)
                payload = json.loads(result.stdout)["data"]
                self.assertEqual(payload["view"], "commands")
                self.assertEqual(payload["items"][0]["command"], expected)
                self.assertLessEqual(len(payload["items"]), 8)

    def test_capabilities_intent_alias_explains_matches_and_suppresses_noise(
        self,
    ) -> None:
        result = self.runner.invoke(
            app,
            [
                "--format",
                "json",
                "capabilities",
                "--intent",
                "列出当前系统中所有姓名包含英文字母的导师",
                "--select",
                "command,match",
                "--minimal",
            ],
        )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        payload = json.loads(result.stdout)["data"]
        self.assertEqual(
            payload["query_scope"]["mode"], "deterministic_multilingual_v2"
        )
        self.assertEqual(
            payload["query_scope"]["intent"], "列出当前系统中所有姓名包含英文字母的导师"
        )
        self.assertEqual(
            [item["command"] for item in payload["items"]], ["professors.list"]
        )
        match = payload["items"][0]["match"]
        self.assertEqual(match["confidence"], "high")
        self.assertIn("command_alias", match["reasons"])
        self.assertTrue(match["matched_terms"])

    def test_community_export_accepts_a_frozen_professor_id_file(self) -> None:
        fake_client = _FakeAgentClient(
            {"/api/agent/v1/community-mentors/share-package": b"community xlsx"}
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ids_file = root / "selection.json"
            ids_file.write_text(json.dumps({"professor_ids": [7, 9]}), encoding="utf-8")
            output = root / "community-share.xlsx"
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
                        "community",
                        "export-package",
                        "--professor-id-file",
                        ids_file.as_posix(),
                        "--output",
                        output.as_posix(),
                    ],
                )
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(fake_client.download_params[0], {"professor_ids": "7,9"})

    def test_community_export_rejects_conflicting_selection_sources(self) -> None:
        fake_client = _FakeAgentClient({})
        with tempfile.TemporaryDirectory() as temp_dir:
            ids_file = Path(temp_dir) / "selection.txt"
            ids_file.write_text("7\n", encoding="utf-8")
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
                        "community",
                        "export-package",
                        "--professor-id",
                        "7",
                        "--professor-id-file",
                        ids_file.as_posix(),
                    ],
                )
        self.assertEqual(result.exit_code, 2, msg=result.output)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "PROFESSOR_ID_INPUT_CONFLICT")

    def test_community_export_batch_writes_submission_input_and_resume_state(self) -> None:
        fake_client = _FakeAgentClient(
            {"/api/agent/v1/community-mentors/share-package": b"community xlsx"}
        )
        payload = {
            "items": [
                {"university": "甲大学", "school": "计算机学院", "professor_ids": [7, 9]},
                {"university": "乙大学", "school": "软件学院", "department": "软件工程系", "professor_ids": [11]},
            ]
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            items_file = root / "batch.json"
            output_dir = root / "batch"
            items_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
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
                        "community",
                        "export-batch",
                        "--items-file",
                        items_file.as_posix(),
                        "--output-dir",
                        output_dir.as_posix(),
                    ],
                )
            self.assertEqual(result.exit_code, 0, msg=result.output)
            self.assertEqual(len(fake_client.download_calls), 2)
            self.assertEqual(fake_client.download_params[0], {"professor_ids": "7,9"})
            self.assertEqual(fake_client.download_params[1], {"professor_ids": "11"})
            submissions = json.loads((output_dir / "submissions.json").read_text(encoding="utf-8"))
            self.assertEqual(len(submissions["submissions"]), 2)
            state = json.loads((output_dir / "export-state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "succeeded")
            self.assertTrue(all(item["status"] == "succeeded" for item in state["items"]))

            resumed_client = _FakeAgentClient(
                {"/api/agent/v1/community-mentors/share-package": b"should not download"}
            )
            with patch(
                "auto_email_sender_cli.commands.professors.AgentApiClient",
                return_value=resumed_client,
            ):
                resumed = self.runner.invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "professors",
                        "community",
                        "export-batch",
                        "--items-file",
                        items_file.as_posix(),
                        "--output-dir",
                        output_dir.as_posix(),
                        "--resume",
                    ],
                )
            self.assertEqual(resumed.exit_code, 0, msg=resumed.output)
            self.assertEqual(resumed_client.download_calls, [])

    def test_capabilities_no_match_distinguishes_query_from_existing_resource(
        self,
    ) -> None:
        result = self.runner.invoke(
            app,
            [
                "--format",
                "json",
                "capabilities",
                "--intent",
                "完全不存在的量子操作",
                "--resource",
                "professors",
                "--resource-exact",
            ],
        )

        self.assertEqual(result.exit_code, 4, msg=result.output)
        error = json.loads(result.stdout)["error"]
        self.assertEqual(error["code"], "CAPABILITY_NOT_FOUND")
        self.assertIn("任务意图", error["message"])
        self.assertEqual(error["details"]["resource"], "professors")
        self.assertTrue(error["details"]["resource_exists"])
        self.assertEqual(error["details"]["suggestions"], [])

    def test_capabilities_exact_resource_select_and_minimal_output(self) -> None:
        exact = self.runner.invoke(
            app,
            [
                "--format",
                "json",
                "capabilities",
                "--resource",
                "professors",
                "--resource-exact",
                "--select",
                "command,summary,risk",
                "--minimal",
            ],
        )
        self.assertEqual(exact.exit_code, 0, msg=exact.output)
        payload = json.loads(exact.stdout)["data"]
        self.assertEqual(len(payload["items"]), 11)
        self.assertTrue(
            all(
                set(item) <= {"command", "summary", "risk"} for item in payload["items"]
            )
        )
        self.assertTrue(
            all(
                not item["command"].startswith("professors.tags.")
                for item in payload["items"]
            )
        )
        self.assertIn("scope_revision", payload)
        self.assertIn("summary", payload)
        self.assertNotIn("build", payload)
        self.assertNotIn("next", payload)
        self.assertNotIn("warnings", json.loads(exact.stdout).get("_meta", {}))

    def test_unchanged_contract_revisions_are_reused_in_process(self) -> None:
        with (
            patch.dict(cli_main._COMMAND_CONTRACT_REVISION_CACHE, {}, clear=True),
            patch.object(
                cli_main,
                "describe_command_revisions",
                wraps=cli_main.describe_command_revisions,
            ) as describe_revisions,
        ):
            first = cli_main._current_command_contract_revisions(["professors.list"])
            second = cli_main._current_command_contract_revisions(["professors.list"])

        self.assertEqual(first, second)
        describe_revisions.assert_called_once_with(app, ["professors.list"])

    def test_capabilities_search_options_fail_closed_when_conflicting(self) -> None:
        cases = (
            (
                ["--query", "导师", "--command", "professors.list"],
                "--query 与 --command",
            ),
            (["--limit", "2", "--resource", "professors"], "--limit 只能"),
            (["--resource-exact"], "--resource-exact 必须"),
            (["--query", "导师", "--view", "full"], "--query 返回"),
            (["--select", "missing", "--resource", "professors"], "未知命令卡字段"),
            (["--select", "command"], "--select 只能"),
        )
        for arguments, message in cases:
            with self.subTest(arguments=arguments):
                result = self.runner.invoke(
                    app, ["--format", "json", "capabilities", *arguments]
                )
                self.assertEqual(result.exit_code, 2, msg=result.output)
                error = json.loads(result.stdout)["error"]
                self.assertEqual(error["code"], "INVALID_ARGUMENT")
                self.assertIn(message, error["message"])

    def test_global_options_work_after_leaf_commands_without_stealing_leaf_options(
        self,
    ) -> None:
        version = self.runner.invoke(app, ["version", "--format", "json"])
        self.assertEqual(version.exit_code, 0, msg=version.output)
        self.assertTrue(json.loads(version.stdout)["ok"])

        invalid_filter = self.runner.invoke(
            app,
            [
                "professors",
                "list",
                "--filter",
                '{"not_a_field":{"eq":1}}',
                "--format",
                "json",
            ],
        )
        self.assertEqual(invalid_filter.exit_code, 2, msg=invalid_filter.output)
        self.assertEqual(
            json.loads(invalid_filter.stdout)["error"]["code"], "INVALID_FILTER"
        )

        with (
            tempfile.TemporaryDirectory() as temporary_directory,
            patch(
                "auto_email_sender_cli.commands.professors.AgentApiClient",
                side_effect=RuntimeUnavailableError("测试服务不可用。"),
            ),
        ):
            export = self.runner.invoke(
                app,
                [
                    "professors",
                    "export",
                    "--output",
                    (Path(temporary_directory) / "unused.csv").as_posix(),
                    "--format",
                    "csv",
                    "--json",
                ],
            )
        self.assertEqual(export.exit_code, 7, msg=export.output)
        self.assertEqual(json.loads(export.stdout)["error"]["code"], "APP_UNAVAILABLE")

        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            side_effect=RuntimeUnavailableError("测试服务不可用。"),
        ):
            diagnostics = self.runner.invoke(
                app,
                [
                    "diagnostics",
                    "logs",
                    "--request-id",
                    "log-filter",
                    "--operation-id",
                    "operation-id",
                    "--format",
                    "json",
                ],
            )
        self.assertEqual(diagnostics.exit_code, 7, msg=diagnostics.output)
        self.assertEqual(
            json.loads(diagnostics.stdout)["_meta"]["request_id"], "operation-id"
        )

    def test_usage_errors_offer_structured_argument_corrections(self) -> None:
        result = self.runner.invoke(
            app,
            ["professors", "get", "--profesor-id", "1", "--format", "json"],
        )
        self.assertEqual(result.exit_code, 2, msg=result.output)
        payload = json.loads(result.stdout)
        self.assertIn("--professor-id", payload["error"]["details"]["suggestions"])

    def test_describe_returns_machine_readable_command_contract_without_runtime(
        self,
    ) -> None:
        result = self.runner.invoke(
            app,
            ["--format", "json", "describe", "--command", "drafts generate"],
        )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        payload = json.loads(result.stdout)["data"]
        self.assertEqual(payload["command"], "drafts.generate")
        self.assertEqual(payload["risk"]["level"], "L1")
        self.assertEqual(payload["preconditions"]["runtime"], "desktop_app_ready")
        parameters = payload["input"]["required"]
        self.assertEqual(parameters["professor_id"]["type"], "integer")
        self.assertEqual(
            parameters["generation_mode"]["enum"],
            ["template", "ai_rewrite", "manual"],
        )
        self.assertNotIn("parameters", payload)
        self.assertIn("template_id", payload["input"]["optional_contracts"])
        self.assertIsNone(
            payload["input"]["optional_contracts"]["template_id"]["default"]
        )
        generation_mode = payload["input"]["required"]["generation_mode"]
        self.assertIn("--generation-mode", generation_mode["flags"])
        self.assertEqual(generation_mode["enum"], ["template", "ai_rewrite", "manual"])
        self.assertIn("global_option_contracts", payload["input"])
        byte_budget = payload["input"]["global_option_contracts"]["max_output_bytes"]
        self.assertEqual(byte_budget["default"], 64 * 1024)
        self.assertEqual(byte_budget["minimum"], 1024)
        self.assertEqual(byte_budget["maximum"], 16 * 1024 * 1024)
        self.assertTrue(payload["details_available"])
        self.assertLess(
            len(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            ),
            4_000,
        )

    def test_describe_expands_only_requested_contract_sections(self) -> None:
        result = self.runner.invoke(
            app,
            [
                "--format",
                "json",
                "describe",
                "--command",
                "plans execute",
                "--section",
                "output",
            ],
        )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        payload = json.loads(result.stdout)["data"]
        self.assertIn("details", payload)
        self.assertEqual(set(payload["details"]), {"output"})
        self.assertIn("schema", payload["details"]["output"])

        full = self.runner.invoke(
            app,
            [
                "--format",
                "json",
                "describe",
                "--command",
                "plans execute",
                "--view",
                "full",
            ],
        )
        self.assertEqual(full.exit_code, 0, msg=full.output)
        full_payload = json.loads(full.stdout)["data"]
        self.assertIn("parameters", full_payload)
        self.assertIn("output", full_payload)
        self.assertLess(
            len(result.stdout.encode("utf-8")), len(full.stdout.encode("utf-8"))
        )

        input_section = self.runner.invoke(
            app,
            [
                "--format",
                "json",
                "describe",
                "--command",
                "professors.list",
                "--section",
                "input",
            ],
        )
        self.assertEqual(input_section.exit_code, 0, msg=input_section.output)
        input_contract = json.loads(input_section.stdout)["data"]["details"]["input"]
        self.assertIn("schema", input_contract)
        self.assertIn("global_options", input_contract)
        self.assertNotIn("parameters", input_contract)
        self.assertTrue(input_contract["global_options"]["filter"]["supported"])
        self.assertTrue(input_contract["global_options"]["output_file"]["supported"])

    def test_compact_describe_exposes_contract_revision_and_bounds_default_output(
        self,
    ) -> None:
        result = self.runner.invoke(
            app,
            ["--format", "json", "describe", "--command", "plans.execute"],
        )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        payload = json.loads(result.stdout)["data"]
        self.assertEqual(len(payload["contract_revision"]), 16)
        self.assertIn("idempotency", payload)
        self.assertIn("trust", payload)
        self.assertLess(len(result.stdout.encode("utf-8")), 4_000)

    def test_describe_since_returns_a_bounded_not_modified_response(self) -> None:
        initial = self.runner.invoke(
            app,
            ["--format", "json", "describe", "--command", "plans.execute"],
        )
        self.assertEqual(initial.exit_code, 0, msg=initial.output)
        revision = json.loads(initial.stdout)["data"]["contract_revision"]

        cached = self.runner.invoke(
            app,
            [
                "--format",
                "json",
                "describe",
                "--command",
                "plans.execute",
                "--since",
                revision,
            ],
        )
        self.assertEqual(cached.exit_code, 0, msg=cached.output)
        payload = json.loads(cached.stdout)["data"]
        self.assertTrue(payload["unchanged"])
        self.assertEqual(payload["cache"]["status"], "not_modified")
        self.assertEqual(payload["contract_revision"], revision)
        self.assertLess(len(cached.stdout.encode("utf-8")), 800)

    def test_every_available_capability_has_a_describe_contract(self) -> None:
        descriptions = describe_commands(
            app,
            (item.command for item in CAPABILITIES if item.availability == "available"),
        )
        missing = [
            capability.command
            for capability in CAPABILITIES
            if capability.availability == "available"
            and capability.command not in descriptions
        ]

        self.assertEqual(missing, [])

    def test_guide_routing_and_doctor_explain_outdated_skills(self) -> None:
        routing = self.runner.invoke(
            app, ["--format", "json", "guide", "--topic", "routing"]
        )
        self.assertEqual(routing.exit_code, 0, msg=routing.output)
        self.assertIn("describe", " ".join(json.loads(routing.stdout)["data"]["rules"]))

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_path = Path(temp_dir) / "runtime.json"
            with (
                patch(
                    "auto_email_sender_cli.main.get_runtime_file_path",
                    return_value=runtime_path,
                ),
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
        skill_check = next(
            check for check in doctor_payload["checks"] if check["id"] == "agent_skills"
        )
        self.assertFalse(skill_check["ok"])
        self.assertIn("重新安装", doctor_payload["recommended_action"])

    def test_source_skill_contains_only_global_protocol_invariants(self) -> None:
        skill_path = (
            Path(__file__).resolve().parents[2]
            / "agent-support"
            / "skills"
            / "auto-email-sender"
            / "SKILL.md"
        )
        skill = skill_path.read_text(encoding="utf-8")

        self.assertLess(len(skill.encode("utf-8")), 5_000)
        self.assertIn("capabilities --resource", skill)
        self.assertIn("scope_revision", skill)
        self.assertIn("describe --command", skill)
        self.assertIn("delegated_effects", skill)
        self.assertIn("requires_target_contract", skill)
        self.assertIn("untrusted", skill)
        self.assertIn("APP_UNAVAILABLE", skill)
        self.assertNotIn("guide --topic", skill)
        self.assertNotIn("campaigns prepare-send", skill)

    def test_skill_inspection_detects_modified_or_outdated_official_skills(
        self,
    ) -> None:
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
            skill_hash = hashlib.sha256(
                f"F\tSKILL.md\t{file_hash}\n".encode("utf-8")
            ).hexdigest()
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
            with patch.dict(
                os.environ,
                {"AUTO_EMAIL_SENDER_AGENT_MANIFEST_FILE": manifest_path.as_posix()},
            ):
                healthy = inspect_agent_skill_installation()
                target_file.write_text("modified skill", encoding="utf-8")
                outdated = inspect_agent_skill_installation()

        self.assertTrue(healthy["ok"])
        self.assertEqual(healthy["items"][0]["state"], "installed")
        self.assertFalse(outdated["ok"])
        self.assertEqual(outdated["items"][0]["state"], "needs_update")

    def test_agent_support_manifest_versions_match_the_shared_contract(self) -> None:
        contract_path = (
            Path(__file__).resolve().parents[2]
            / "contracts"
            / "agent-support-manifest.schema.json"
        )
        contract = json.loads(contract_path.read_text(encoding="utf-8"))

        self.assertEqual(
            AGENT_SUPPORT_MANIFEST_SCHEMA_VERSION,
            contract["x-current-version"],
        )
        self.assertEqual(
            SUPPORTED_AGENT_SUPPORT_MANIFEST_SCHEMA_VERSIONS,
            frozenset(contract["x-supported-versions"]),
        )

    def test_skill_inspection_distinguishes_new_old_and_invalid_manifest_versions(
        self,
    ) -> None:
        cases = (
            (6, "当前 CLI 版本过旧"),
            (3, "安装清单版本过旧"),
            ("5", "安装清单版本无效"),
            ([], "安装清单版本无效"),
            ({}, "安装清单版本无效"),
            (True, "安装清单版本无效"),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "installation.json"
            for schema_version, expected_message in cases:
                with self.subTest(schema_version=schema_version):
                    manifest_path.write_text(
                        json.dumps(
                            {
                                "schema_version": schema_version,
                                "enabled": True,
                            },
                        ),
                        encoding="utf-8",
                    )
                    with patch.dict(
                        os.environ,
                        {
                            "AUTO_EMAIL_SENDER_AGENT_MANIFEST_FILE": manifest_path.as_posix()
                        },
                    ):
                        result = inspect_agent_skill_installation()

                    self.assertFalse(result["ok"])
                    self.assertEqual(result["manifest_schema_version"], schema_version)
                    self.assertIn(expected_message, result["message"])
                    self.assertEqual(
                        result["supported_manifest_schema_versions"],
                        [4, 5],
                    )

    @unittest.skipIf(
        os.name == "nt", "macOS symlink binding is not available on Windows"
    )
    def test_schema_v5_installation_verifies_onedir_bundle_and_macos_symlink(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle = root / "cli-bundle"
            internal = bundle / "_internal"
            internal.mkdir(parents=True)
            source = bundle / "auto-email-sender"
            source.write_bytes(b"official cli executable")
            (internal / "base_library.zip").write_bytes(b"runtime")
            target = root / "bin" / "auto-email-sender"
            target.parent.mkdir()
            target.symlink_to(source)
            expected_hash = _sha256_directory(bundle)
            assert expected_hash is not None
            manifest_path = root / "installation.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": 5,
                        "enabled": True,
                        "cli_source": source.as_posix(),
                        "cli_target": target.as_posix(),
                        "cli_sha256": expected_hash,
                        "agents": {},
                    },
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"AUTO_EMAIL_SENDER_AGENT_MANIFEST_FILE": manifest_path.as_posix()},
            ):
                healthy = inspect_agent_skill_installation()
                (internal / "base_library.zip").write_bytes(b"modified runtime")
                outdated = inspect_agent_skill_installation()

        self.assertTrue(healthy["ok"])
        self.assertTrue(healthy["cli"]["ok"])
        self.assertEqual(healthy["cli"]["hash_kind"], "canonical_directory_v1")
        self.assertFalse(outdated["cli"]["ok"])
        failed_checks = {
            check["id"] for check in outdated["cli"]["checks"] if not check["ok"]
        }
        self.assertEqual(failed_checks, {"cli_bundle_sha256"})

    def test_schema_v5_installation_accepts_managed_windows_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle = root / "cli-bundle"
            internal = bundle / "_internal"
            internal.mkdir(parents=True)
            source = bundle / "auto-email-sender.exe"
            source.write_bytes(b"official cli executable")
            (internal / "python312.dll").write_bytes(b"runtime")
            target = root / "bin" / "auto-email-sender.cmd"
            target.parent.mkdir()
            escaped_source = str(source.resolve()).replace("%", "%%")
            target.write_bytes(
                f'@echo off\r\n"{escaped_source}" %*\r\nexit /b %ERRORLEVEL%\r\n'.encode(),
            )
            expected_hash = _sha256_directory(bundle)
            assert expected_hash is not None
            manifest_path = root / "installation.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": 5,
                        "enabled": True,
                        "cli_source": source.as_posix(),
                        "cli_target": target.as_posix(),
                        "cli_sha256": expected_hash,
                        "agents": {},
                    },
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"AUTO_EMAIL_SENDER_AGENT_MANIFEST_FILE": manifest_path.as_posix()},
            ):
                healthy = inspect_agent_skill_installation()
                target.write_text("user modified launcher", encoding="utf-8")
                outdated = inspect_agent_skill_installation()

        self.assertTrue(healthy["cli"]["ok"])
        self.assertEqual(
            healthy["cli"]["checks"][-1]["binding_type"],
            "windows_launcher",
        )
        self.assertFalse(outdated["cli"]["ok"])

    def test_skill_inspection_does_not_treat_malformed_agent_manifest_as_healthy(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "installation.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": 4,
                        "enabled": True,
                        "skill_source": Path(temp_dir).as_posix(),
                        "agents": {"codex": "not-an-agent-record"},
                    },
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"AUTO_EMAIL_SENDER_AGENT_MANIFEST_FILE": manifest_path.as_posix()},
            ):
                result = inspect_agent_skill_installation()

        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "needs_update")
        self.assertIn("格式损坏", result["message"])

    def test_installation_inspection_verifies_cli_source_target_and_manifest_hash(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source-cli"
            target = root / "target-cli"
            source.write_bytes(b"official cli build")
            target.write_bytes(b"official cli build")
            expected_hash = hashlib.sha256(b"official cli build").hexdigest()
            manifest_path = root / "installation.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": 4,
                        "enabled": True,
                        "cli_source": source.as_posix(),
                        "cli_target": target.as_posix(),
                        "cli_sha256": expected_hash,
                        "agents": {},
                    },
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"AUTO_EMAIL_SENDER_AGENT_MANIFEST_FILE": manifest_path.as_posix()},
            ):
                healthy = inspect_agent_skill_installation()
                target.write_bytes(b"outdated cli build")
                outdated = inspect_agent_skill_installation()

        self.assertTrue(healthy["cli"]["ok"])
        self.assertEqual(healthy["cli"]["state"], "installed")
        self.assertEqual(len(healthy["cli"]["checks"]), 3)
        self.assertFalse(outdated["cli"]["ok"])
        failed_checks = {
            check["id"] for check in outdated["cli"]["checks"] if not check["ok"]
        }
        self.assertIn("cli_target_sha256", failed_checks)
        self.assertIn("cli_source_target_match", failed_checks)

    def test_status_tells_user_to_manually_open_a_stopped_desktop_app(self) -> None:
        descriptor = SimpleNamespace(
            desktop_pid=12345,
            backend_pid=23456,
            app_version="2.4.1",
            protocol_version="3",
        )
        with (
            patch(
                "auto_email_sender_cli.main.load_runtime_descriptor",
                return_value=descriptor,
            ),
            patch(
                "auto_email_sender_cli.main.probe_runtime_descriptor",
                return_value=SimpleNamespace(
                    desktop_process_running=False,
                    backend_process_running=False,
                    backend_reachable=False,
                    runtime_matches=False,
                    backend_ready=False,
                    backend_state=None,
                ),
            ),
            patch(
                "auto_email_sender_cli.output._MACHINE_OUTPUT_REQUIRES_ASCII",
                True,
            ),
        ):
            result = self.runner.invoke(app, ["--format", "json", "status"])

        self.assertEqual(result.exit_code, 0, msg=result.output)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["data"]["state"], "stopped")
        self.assertIn("手动打开", " ".join(payload["_meta"]["warnings"]))
        result.stdout.encode("ascii")

    def test_status_uses_the_authenticated_runtime_probe(self) -> None:
        descriptor = SimpleNamespace(
            desktop_pid=12345,
            backend_pid=23456,
            app_version="2.4.1",
            protocol_version="3",
            base_url="http://127.0.0.1:48120",
        )
        with (
            patch(
                "auto_email_sender_cli.main.load_runtime_descriptor",
                return_value=descriptor,
            ),
            patch(
                "auto_email_sender_cli.main.probe_runtime_descriptor",
                return_value=SimpleNamespace(
                    desktop_process_running=True,
                    backend_process_running=True,
                    backend_reachable=True,
                    runtime_matches=True,
                    backend_ready=True,
                    backend_state="ready",
                ),
            ) as probe,
        ):
            result = self.runner.invoke(app, ["--format", "json", "status"])

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(json.loads(result.stdout)["data"]["state"], "ready")
        probe.assert_called_once_with(descriptor)

    def test_doctor_recommends_manually_opening_an_app_without_runtime_info(
        self,
    ) -> None:
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

    def test_doctor_strict_emits_the_same_result_and_fails_when_unhealthy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_path = Path(temp_dir) / "runtime.json"
            with (
                patch(
                    "auto_email_sender_cli.main.get_runtime_file_path",
                    return_value=runtime_path,
                ),
                patch(
                    "auto_email_sender_cli.main.inspect_agent_skill_installation",
                    return_value={
                        "ok": False,
                        "state": "needs_update",
                        "message": "安装清单需要更新。",
                        "items": [],
                        "cli": {
                            "ok": False,
                            "state": "needs_update",
                            "message": "安装清单需要更新。",
                        },
                    },
                ),
            ):
                result = self.runner.invoke(
                    app,
                    ["--format", "json", "doctor", "--strict"],
                )

        self.assertEqual(result.exit_code, 1, msg=result.output)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["data"]["healthy"])

    def test_jsonl_has_meta_item_and_summary_records(self) -> None:
        with patch(
            "auto_email_sender_cli.output._MACHINE_OUTPUT_REQUIRES_ASCII",
            True,
        ):
            result = self.runner.invoke(app, ["--format", "jsonl", "capabilities"])

        self.assertEqual(result.exit_code, 0, msg=result.output)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(rows[0]["type"], "meta")
        self.assertEqual(rows[1]["type"], "item")
        result.stdout.encode("ascii")

    def test_jsonl_errors_use_an_explicit_error_record(self) -> None:
        with patch(
            "auto_email_sender_cli.output._MACHINE_OUTPUT_REQUIRES_ASCII",
            True,
        ):
            result = self.runner.invoke(
                app,
                ["--format", "jsonl", "describe", "--command", "missing.command"],
            )

        self.assertEqual(result.exit_code, 4, msg=result.output)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["type"], "error")
        self.assertEqual(rows[0]["error"]["code"], "COMMAND_NOT_FOUND")
        self.assertEqual(rows[0]["meta"]["command"], "describe")
        result.stdout.encode("ascii")

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
        self.assertNotIn("revision", payload["data"]["items"][0])
        self.assertNotIn("pagination", payload["_meta"])
        self.assertEqual(payload["data"]["next_cursor"], "1")
        self.assertEqual(payload["data"]["limit"], 1)
        self.assertTrue(payload["data"]["truncated"])
        self.assertEqual(
            payload["data"]["continuation"],
            {
                "command": "professors.list",
                "input": {"archived": "active", "cursor": "1", "limit": 1},
                "cursor": "1",
                "mode": "cursor",
                "reuse_previous_input": True,
            },
        )
        self.assertEqual(fake_client.calls[0][1], "/api/agent/v1/professors")

    def test_professor_list_search_alias_maps_to_the_existing_query_parameter(
        self,
    ) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/professors": {
                    "items": [],
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
                ["--format", "json", "professors", "list", "--search", "Ada"],
            )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(fake_client.calls[0][2]["q"], "Ada")

    def test_large_business_results_use_explicit_summary_and_continuation_protocol(
        self,
    ) -> None:
        long_message = "诊断日志内容" * 200
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/diagnostics/operation-logs": {
                    "items": [
                        {
                            "id": 7,
                            "message": long_message,
                            "metadata": {"raw_payload": "x" * 200},
                        },
                    ],
                    "next_cursor": "8",
                    "has_more": True,
                    "pagination_mode": "offset",
                },
            },
        )
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=fake_client,
        ):
            summary = self.runner.invoke(
                app,
                ["--format", "json", "diagnostics", "logs"],
            )
            expanded = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "--expand",
                    "message",
                    "diagnostics",
                    "logs",
                ],
            )
            full = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "--projection",
                    "full",
                    "diagnostics",
                    "logs",
                ],
            )

        self.assertEqual(summary.exit_code, 0, msg=summary.output)
        summary_data = json.loads(summary.stdout)["data"]
        self.assertEqual(summary_data["projection"]["mode"], "summary")
        self.assertEqual(summary_data["limit"], 25)
        self.assertTrue(summary_data["truncated"])
        self.assertIn("/items/0/message", summary_data["omitted_paths"])
        self.assertEqual(summary_data["items"][0]["message"]["kind"], "text_summary")
        self.assertEqual(
            summary_data["items"][0]["message"]["characters"], len(long_message)
        )
        self.assertEqual(
            summary_data["continuation"]["input"],
            {"offset": 8, "limit": 25},
        )
        self.assertTrue(summary_data["continuation"]["reuse_previous_input"])
        self.assertEqual(fake_client.calls[0][2]["limit"], 25)

        self.assertEqual(expanded.exit_code, 0, msg=expanded.output)
        expanded_data = json.loads(expanded.stdout)["data"]
        self.assertEqual(expanded_data["items"][0]["message"], long_message)
        self.assertIn("/items/0/metadata", expanded_data["omitted_paths"])

        self.assertEqual(full.exit_code, 0, msg=full.output)
        full_data = json.loads(full.stdout)["data"]
        self.assertEqual(full_data["projection"]["mode"], "full")
        self.assertEqual(full_data["items"][0]["message"], long_message)
        self.assertNotIn("/items/0/message", full_data["omitted_paths"])

    def test_summary_projection_compacts_legacy_aliases_and_receipt_snapshots(
        self,
    ) -> None:
        data = {
            "items": [{"id": 1, "name": "A"}],
            "records": [{"id": 1, "name": "A"}],
            "next_cursor": None,
            "has_more": False,
            "mutation_receipt": {
                "request_id": "req-1",
                "changed_resources": [
                    {"id": "1", "after": {"id": 1, "name": "A"}},
                ],
            },
        }

        summary = prepare_result_data(data, command="usage.records")
        full = prepare_result_data(
            data,
            command="usage.records",
            projection="full",
        )

        self.assertEqual(summary["items"], [{"id": 1, "name": "A"}])
        self.assertNotIn("records", summary)
        self.assertNotIn("after", summary["mutation_receipt"]["changed_resources"][0])
        self.assertIn(
            "/mutation_receipt/changed_resources/0/after",
            summary["omitted_paths"],
        )
        self.assertEqual(full["records"], data["records"])
        self.assertEqual(
            full["mutation_receipt"]["changed_resources"][0]["after"],
            {"id": 1, "name": "A"},
        )

    def test_json_and_jsonl_apply_the_same_projection_to_object_results(self) -> None:
        long_body = "完整草稿正文" * 300
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/drafts/17": {
                    "task_id": 17,
                    "status": "review_required",
                    "body_text": long_body,
                },
            },
        )
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=fake_client,
        ):
            json_result = self.runner.invoke(
                app,
                ["--format", "json", "drafts", "get", "17"],
            )
            jsonl_result = self.runner.invoke(
                app,
                ["--format", "jsonl", "drafts", "get", "17"],
            )

        self.assertEqual(json_result.exit_code, 0, msg=json_result.output)
        self.assertEqual(jsonl_result.exit_code, 0, msg=jsonl_result.output)
        json_data = json.loads(json_result.stdout)["data"]
        jsonl_rows = [json.loads(line) for line in jsonl_result.stdout.splitlines()]
        self.assertEqual(jsonl_rows[1]["data"], json_data)
        self.assertEqual(json_data["body_text"]["kind"], "text_summary")
        self.assertNotIn(long_body, jsonl_result.stdout)

    def test_nested_json_pointer_expand_keeps_structured_parents_traversable(
        self,
    ) -> None:
        long_body = "邮件正文" * 300
        projected = prepare_result_data(
            {
                "messages": [
                    {
                        "id": 1,
                        "body_text": long_body,
                        "metadata": {"raw": "x" * 300},
                    },
                ],
            },
            command="communications.threads.get",
            projection="summary",
            expanded_paths=("/messages/0/body_text",),
        )

        self.assertEqual(projected["messages"][0]["body_text"], long_body)
        self.assertEqual(projected["messages"][0]["metadata"]["kind"], "object_summary")

    def test_large_arbitrary_nested_arrays_are_bounded_unless_explicitly_expanded(
        self,
    ) -> None:
        data = {
            "plan_id": "change-large",
            "summary": {
                "items": [
                    {"id": index, "name": f"导师 {index}"} for index in range(5_000)
                ]
            },
        }
        projected = prepare_result_data(data, command="plans.show")
        expanded = prepare_result_data(
            data,
            command="plans.show",
            expanded_paths=("/summary/items",),
        )
        expanded_with_budget = prepare_result_data(
            data,
            command="plans.show",
            expanded_paths=("/summary/items",),
            max_output_bytes=256 * 1024,
        )

        self.assertEqual(projected["summary"]["items"]["kind"], "array_summary")
        self.assertEqual(projected["summary"]["items"]["item_count"], 5_000)
        self.assertIn("/summary/items", projected["omitted_paths"])
        self.assertTrue(projected["truncated"])
        self.assertLess(
            len(json.dumps(projected, ensure_ascii=False).encode("utf-8")), 2_000
        )
        self.assertTrue(expanded["truncated"])
        self.assertTrue(expanded["projection"]["budget_compacted"])
        self.assertLessEqual(
            len(
                json.dumps(expanded, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            ),
            64 * 1024,
        )
        self.assertEqual(len(expanded_with_budget["summary"]["items"]), 5_000)
        self.assertNotIn("omitted_paths", expanded_with_budget)

    def test_full_and_expanded_results_cannot_bypass_explicit_byte_budget(self) -> None:
        body = "完整中文正文" * 20_000
        full = prepare_result_data(
            {"task_id": 7, "body_text": body},
            command="drafts.get",
            projection="full",
            max_output_bytes=1024,
        )
        expanded = prepare_result_data(
            {"task_id": 7, "body_text": body},
            command="drafts.get",
            expanded_paths=("body_text",),
            max_output_bytes=1024,
        )

        for result in (full, expanded):
            with self.subTest(mode=result["projection"]["mode"]):
                encoded = json.dumps(
                    result,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                self.assertLessEqual(len(encoded), 1024)
                self.assertEqual(result["projection"]["budget_bytes"], 1024)
                self.assertEqual(result["projection"]["output_bytes"], len(encoded))
                self.assertTrue(result["projection"]["budget_compacted"])
                self.assertTrue(result["truncated"])
                self.assertIn("/body_text", result["omitted_paths"])
                self.assertNotIn(body, encoded.decode("utf-8"))

        maximum_budget = prepare_result_data(
            {"task_id": 7, "body_text": body},
            command="drafts.get",
            projection="full",
            max_output_bytes=16 * 1024 * 1024,
        )
        self.assertEqual(maximum_budget["body_text"], body)
        self.assertFalse(maximum_budget["projection"].get("budget_compacted", False))

    def test_single_oversized_collection_item_is_summarized_with_utf8_byte_count(
        self,
    ) -> None:
        projected = prepare_result_data(
            {
                "items": [{"id": 1, "message": "中文" * 100_000}],
                "next_cursor": None,
                "has_more": False,
            },
            command="diagnostics.logs",
            projection="full",
            max_output_bytes=1024,
        )
        encoded = json.dumps(
            projected, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")

        self.assertLessEqual(len(encoded), 1024)
        self.assertEqual(projected["projection"]["output_bytes"], len(encoded))
        self.assertEqual(projected["items"][0]["id"], 1)
        self.assertNotEqual(projected["items"][0].get("message"), "中文" * 100_000)
        self.assertIn("/items/0/message", projected["omitted_paths"])

    def test_root_output_budget_options_work_after_leaf_and_fail_closed(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/professors": {
                    "items": [
                        {"id": 1, "name": "A", "personal_note": "中文" * 2_000},
                        {"id": 2, "name": "B", "personal_note": "中文" * 2_000},
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
                [
                    "--format",
                    "json",
                    "professors",
                    "list",
                    "--projection",
                    "full",
                    "--max-output-bytes",
                    "1024",
                    "--max-items",
                    "1",
                ],
            )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        data = json.loads(result.stdout)["data"]
        encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        self.assertLessEqual(len(encoded), 1024)
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["projection"]["max_items"], 1)
        self.assertEqual(fake_client.calls[0][2]["limit"], 1)

        for arguments in (
            ["--max-output-bytes", "0", "professors", "list"],
            ["--max-output-bytes", str(16 * 1024 * 1024 + 1), "professors", "list"],
            ["--max-items", "0", "professors", "list"],
            ["--max-items", "1", "professors", "get", "1"],
            ["--expand", "x" * 513, "professors", "list"],
        ):
            with self.subTest(arguments=arguments):
                invalid = self.runner.invoke(app, ["--format", "json", *arguments])
                self.assertEqual(invalid.exit_code, 2, msg=invalid.output)
                self.assertFalse(json.loads(invalid.stdout)["ok"])

    def test_jsonl_obeys_stdout_budget_while_file_export_preserves_full_records(
        self,
    ) -> None:
        note = "完整导师备注" * 10_000
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/professors": {
                    "items": [{"id": 1, "name": "A", "personal_note": note}],
                    "next_cursor": None,
                    "has_more": False,
                },
            },
        )
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=fake_client,
        ):
            jsonl = self.runner.invoke(
                app,
                [
                    "--format",
                    "jsonl",
                    "--projection",
                    "full",
                    "--max-output-bytes",
                    "1024",
                    "professors",
                    "list",
                ],
            )

        self.assertEqual(jsonl.exit_code, 0, msg=jsonl.output)
        rows = [json.loads(line) for line in jsonl.stdout.splitlines()]
        self.assertTrue(rows[0]["result"]["projection"]["budget_compacted"])
        self.assertNotIn(note, jsonl.stdout)

        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "professors.jsonl"
            with patch(
                "auto_email_sender_cli.commands.common.AgentApiClient",
                return_value=fake_client,
            ):
                exported = self.runner.invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "--projection",
                        "full",
                        "--max-output-bytes",
                        "1024",
                        "--output-file",
                        destination.as_posix(),
                        "professors",
                        "list",
                        "--all",
                    ],
                )
            self.assertEqual(exported.exit_code, 0, msg=exported.output)
            exported_rows = [
                json.loads(line)
                for line in destination.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(exported_rows[0]["personal_note"], note)

    def test_default_projection_enforces_byte_budget_on_rich_collection_items(
        self,
    ) -> None:
        items = [
            {
                "id": index,
                "status": "pending",
                "name": f"导师 {index}",
                "email": f"mentor-{index}@example.edu",
                "recent_papers": ["论文" * 50 for _ in range(10)],
                "evidence": {
                    "source_url": f"https://example.edu/{index}",
                    "quote": "证据" * 100,
                },
            }
            for index in range(500)
        ]

        projected = prepare_result_data(
            {"items": items, "next_cursor": None, "has_more": False},
            command="crawler.jobs.candidates",
        )
        expanded = prepare_result_data(
            {"items": items[:1], "next_cursor": None, "has_more": False},
            command="crawler.jobs.candidates",
            expanded_paths=("recent_papers",),
        )

        encoded = json.dumps(
            projected, ensure_ascii=False, separators=(",", ":")
        ).encode()
        self.assertLessEqual(len(encoded), 64 * 1024)
        self.assertTrue(projected["projection"]["budget_compacted"])
        self.assertEqual(projected["projection"]["budget_bytes"], 64 * 1024)
        self.assertEqual(projected["items"][0]["id"], 0)
        self.assertEqual(projected["items"][0]["status"], "pending")
        self.assertIn("/items/*/recent_papers", projected["omitted_paths"])
        self.assertLessEqual(len(projected["omitted_paths"]), 32)
        self.assertEqual(
            expanded["items"][0]["recent_papers"], items[0]["recent_papers"]
        )

    def test_structurally_summarized_collection_returns_an_executable_recovery_action(
        self,
    ) -> None:
        projected = prepare_result_data(
            {
                "items": [
                    {"id": index, "name": f"导师 {index}"} for index in range(501)
                ],
                "next_cursor": None,
                "has_more": False,
                "fetched_all": True,
            },
            command="professors.list",
        )

        self.assertEqual(projected["items"]["kind"], "array_summary")
        self.assertEqual(projected["items"]["item_count"], 501)
        self.assertEqual(
            projected["recovery_action"],
            {
                "action": "export_complete_collection",
                "command": "professors.list",
                "reuse_previous_input": True,
                "required_input": ["output_file"],
                "global_options": {"output_file": "<path>.jsonl"},
            },
        )
        self.assertIn("--output-file", projected["projection"]["recovery"])
        self.assertIn("/items", projected["omitted_paths"])

    def test_nested_arrays_are_bounded_by_bytes_before_item_count(self) -> None:
        projected = prepare_result_data(
            {
                "plan_id": "plan-rich",
                "summary": {
                    "items": [
                        {"id": index, "value": "内容" * 200} for index in range(49)
                    ],
                },
            },
            command="plans.show",
        )

        self.assertEqual(projected["summary"]["items"]["kind"], "array_summary")
        self.assertEqual(projected["summary"]["items"]["item_count"], 49)
        self.assertIn("/summary/items", projected["omitted_paths"])

    def test_result_protocol_metadata_is_sparse_and_blocked_actions_expand_on_demand(
        self,
    ) -> None:
        complete = prepare_result_data(
            {"id": 1, "name": "导师"}, command="professors.get"
        )
        self.assertTrue(
            {
                "projection",
                "limit",
                "continuation",
                "truncated",
                "omitted_paths",
            }.isdisjoint(
                complete,
            ),
        )

        collection = prepare_result_data(
            {"items": [{"id": 1}], "next_cursor": None, "has_more": False},
            command="professors.list",
        )
        self.assertEqual(collection["limit"], 1)
        self.assertNotIn("continuation", collection)
        self.assertNotIn("truncated", collection)
        self.assertNotIn("projection", collection)

        blocked = {"retry": "当前状态不允许重试。", "wait": "任务已经结束。"}
        compact = prepare_result_data(
            {"id": 1, "blocked_actions": blocked},
            command="crawler.jobs.get",
        )
        expanded = prepare_result_data(
            {"id": 1, "blocked_actions": blocked},
            command="crawler.jobs.get",
            expanded_paths=("blocked_actions",),
        )
        self.assertEqual(compact["blocked_actions"]["kind"], "object_summary")
        self.assertIn("/blocked_actions", compact["omitted_paths"])
        self.assertEqual(expanded["blocked_actions"], blocked)
        self.assertEqual(expanded["projection"]["expanded_paths"], ["blocked_actions"])
        self.assertNotIn("truncated", expanded)

        domain_limit = prepare_result_data(
            {"id": 1, "limit": 25},
            command="settings.get",
        )
        self.assertEqual(domain_limit, {"id": 1, "limit": 25})

    def test_one_hundred_state_records_stay_bounded_without_losing_action_safety(
        self,
    ) -> None:
        items = [
            augment_state_metadata(
                {"id": index + 1, "status": "partially_completed"},
                command="campaigns.list",
            )
            for index in range(100)
        ]
        projected = prepare_result_data(
            {"items": items, "next_cursor": None, "has_more": False},
            command="campaigns.list",
        )

        encoded = json.dumps(
            projected, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        self.assertLess(len(encoded), 40_000)
        first = projected["items"][0]
        self.assertEqual(first["available_actions"][0]["risk_level"], "L0")
        self.assertEqual(first["blocked_actions"]["kind"], "object_summary")
        self.assertIn("/items/*/blocked_actions", projected["omitted_paths"])
        self.assertEqual(projected["omitted_paths_total"], 100)

    def test_root_complete_capability_views_require_a_narrow_scope(self) -> None:
        for view in ("commands", "full"):
            with self.subTest(view=view):
                result = self.runner.invoke(
                    app,
                    ["--format", "json", "capabilities", "--view", view],
                )
                self.assertEqual(result.exit_code, 2, msg=result.output)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["error"]["code"], "RESULT_TOO_LARGE")
                self.assertIn(
                    "--resource", payload["error"]["details"]["suggestions"][0]
                )

    def test_retryable_write_error_returns_the_generated_request_id(self) -> None:
        class FailingClient:
            descriptor = SimpleNamespace(app_version="test")

            def request(self, *_: object, **__: object) -> object:
                raise RuntimeUnavailableError("本地服务暂时不可用。")

        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=FailingClient(),
        ):
            result = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "professors",
                    "create",
                    "--name",
                    "测试导师",
                    "--email",
                    "test@example.edu",
                ],
            )

        self.assertEqual(result.exit_code, 7, msg=result.output)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["error"]["retryable"])
        self.assertRegex(payload["_meta"]["request_id"], r"^cli_[A-Za-z0-9_-]+$")

    def test_invoke_reuses_target_parser_and_confirmation_gate(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/professors/7": {
                    "id": 7,
                    "name": "测试导师",
                    "email": "p@example.edu",
                },
            },
        )
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=fake_client,
        ):
            invoked = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "invoke",
                    "--command",
                    "professors.get",
                    "--input",
                    "-",
                ],
                input='{"professor_id":7}',
            )

        self.assertEqual(invoked.exit_code, 0, msg=invoked.output)
        invoked_payload = json.loads(invoked.stdout)
        self.assertEqual(invoked_payload["_meta"]["command"], "professors.get")
        self.assertEqual(invoked_payload["data"]["id"], 7)
        self.assertEqual(
            fake_client.calls[0][:2], ("GET", "/api/agent/v1/professors/7")
        )

        invalid = self.runner.invoke(
            app,
            [
                "--format",
                "json",
                "invoke",
                "--command",
                "professors.get",
                "--input",
                "-",
            ],
            input='{"unknown":7}',
        )
        self.assertEqual(invalid.exit_code, 2, msg=invalid.output)
        self.assertEqual(
            json.loads(invalid.stdout)["error"]["code"], "INVALID_INVOKE_INPUT"
        )

        confirmation = self.runner.invoke(
            app,
            [
                "--format",
                "json",
                "invoke",
                "--command",
                "plans.execute",
                "--input",
                "-",
            ],
            input='{"plan_id":"plan-7"}',
        )
        self.assertEqual(confirmation.exit_code, 6, msg=confirmation.output)
        confirmation_payload = json.loads(confirmation.stdout)
        self.assertEqual(
            confirmation_payload["error"]["code"], "PLAN_CONFIRMATION_REQUIRED"
        )
        self.assertEqual(confirmation_payload["_meta"]["command"], "plans.execute")

    def test_professor_write_commands_use_safe_agent_routes_and_partial_updates(
        self,
    ) -> None:
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
        create_payload = json.loads(create_professor.stdout)
        receipt_request_id = create_payload["data"]["mutation_receipt"]["request_id"]
        self.assertNotIn("request_id", create_payload["_meta"])
        self.assertEqual(create_professor.stdout.count(receipt_request_id), 1)
        self.assertNotIn(
            "after",
            create_payload["data"]["mutation_receipt"]["changed_resources"][0],
        )
        self.assertEqual(
            fake_client.calls[0][:2], ("POST", "/api/agent/v1/professor-tags")
        )
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
        self.assertEqual(
            fake_client.calls[2][:2], ("PUT", "/api/agent/v1/professors/7")
        )
        self.assertEqual(
            fake_client.json_bodies[2],
            {"research_direction": "具身智能", "recent_papers": []},
        )
        self.assertEqual(
            fake_client.calls[3][:2],
            ("PUT", "/api/agent/v1/professors/7/tags"),
        )
        self.assertEqual(fake_client.json_bodies[3], {"tag_ids": []})

    def test_template_write_commands_use_agent_routes_and_preserve_partial_fields(
        self,
    ) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/templates": {"id": 4, "name": "首次联系"},
                "/api/agent/v1/templates/4": {"id": 4, "name": "首次联系"},
                "/api/agent/v1/templates/4/duplicate": {
                    "id": 5,
                    "name": "首次联系（副本）",
                },
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
                    [
                        "--format",
                        "json",
                        "templates",
                        "import-file",
                        file_path.as_posix(),
                    ],
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

    def test_attachment_updates_distinguish_omitted_from_explicit_clear(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/drafts/17": {"task_id": 17},
                "/api/agent/v1/tasks/17/approve-draft": {
                    "current_task": {"id": 17},
                },
                "/api/agent/v1/drafts/17/rewrite": {"task_id": 17},
                "/api/agent/v1/campaigns/8/items/19/approve-draft": {
                    "current_task": {"id": 19},
                },
                "/api/agent/v1/test-email/2/3/draft": {
                    "draft": {"body_text": "测试正文"},
                },
                "/api/agent/v1/test-email/2/3/prepare-send": {
                    "plan_id": "change_test_email_attachments",
                    "status": "awaiting_confirmation",
                },
            },
        )
        command_arguments = [
            ["drafts", "save", "17", "--body-text", "保存正文"],
            ["drafts", "approve", "17", "--body-text", "批准正文"],
            ["drafts", "rewrite", "17", "--body-text", "改写正文"],
            [
                "campaigns",
                "approve-item-draft",
                "8",
                "19",
                "--body-text",
                "活动正文",
            ],
            [
                "test-email",
                "save",
                "--identity-id",
                "2",
                "--llm-profile-id",
                "3",
                "--body-text",
                "测试正文",
            ],
            [
                "test-email",
                "prepare-send",
                "--identity-id",
                "2",
                "--llm-profile-id",
                "3",
                "--body-text",
                "测试正文",
            ],
        ]

        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=fake_client,
        ):
            omitted_results = [
                self.runner.invoke(app, ["--format", "json", *arguments])
                for arguments in command_arguments
            ]
            clear_results = [
                self.runner.invoke(
                    app,
                    ["--format", "json", *arguments, "--clear-attachments"],
                )
                for arguments in command_arguments
            ]
            invoked_clear = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "invoke",
                    "--command",
                    "drafts.save",
                    "--input",
                    "-",
                ],
                input=json.dumps(
                    {
                        "task_id": 17,
                        "body_text": "通过 invoke 清空",
                        "clear_attachments": True,
                    },
                    ensure_ascii=False,
                ),
            )

        for result in [*omitted_results, *clear_results, invoked_clear]:
            self.assertEqual(result.exit_code, 0, msg=result.output)
        for request_body in fake_client.json_bodies[:4]:
            self.assertNotIn("attachment_material_ids", request_body)
        for request_body in fake_client.json_bodies[4:6]:
            self.assertNotIn("selected_material_ids", request_body)
        for request_body in fake_client.json_bodies[6:10]:
            self.assertEqual(request_body["attachment_material_ids"], [])
        for request_body in fake_client.json_bodies[10:12]:
            self.assertEqual(request_body["selected_material_ids"], [])
        self.assertEqual(fake_client.json_bodies[12]["attachment_material_ids"], [])

        for command in (
            "drafts.save",
            "drafts.approve",
            "drafts.rewrite",
            "campaigns.approve-item-draft",
            "test-email.save",
            "test-email.prepare-send",
        ):
            description = describe_command(app, command)
            self.assertIsNotNone(description)
            clear_contract = description["input"]["schema"]["properties"][
                "clear_attachments"
            ]
            self.assertEqual(clear_contract["flags"], ["--clear-attachments"])

    def test_attachment_options_reject_conflicts_and_duplicate_ids_locally(
        self,
    ) -> None:
        fake_client = _FakeAgentClient({})
        invalid_commands = [
            [
                "drafts",
                "save",
                "17",
                "--body-text",
                "正文",
                "--attachment-material-id",
                "3",
                "--clear-attachments",
            ],
            [
                "drafts",
                "generate",
                "--professor-id",
                "1",
                "--identity-id",
                "2",
                "--llm-profile-id",
                "3",
                "--generation-mode",
                "template",
                "--attachment-material-id",
                "4",
                "--attachment-material-id",
                "4",
            ],
            [
                "campaigns",
                "create",
                "--name",
                "重复附件",
                "--identity-id",
                "2",
                "--llm-profile-id",
                "3",
                "--professor-id",
                "7",
                "--attachment-material-id",
                "4",
                "--attachment-material-id",
                "4",
            ],
            [
                "test-email",
                "save",
                "--identity-id",
                "2",
                "--llm-profile-id",
                "3",
                "--body-text",
                "正文",
                "--material-id",
                "5",
                "--material-id",
                "5",
            ],
        ]

        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=fake_client,
        ):
            results = [
                self.runner.invoke(app, ["--format", "json", *arguments])
                for arguments in invalid_commands
            ]

        for result in results:
            self.assertEqual(result.exit_code, 2, msg=result.output)
        self.assertEqual(fake_client.calls, [])

    def test_approve_only_commands_use_scoped_agent_routes_without_sending(
        self,
    ) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/tasks/17/approve-draft": {
                    "current_task": {"id": 17, "status": "approved"},
                },
                "/api/agent/v1/campaigns/8/items/19/thread": {
                    "current_task": {"id": 19, "status": "review_required"},
                },
                "/api/agent/v1/campaigns/8/items/19/approve-draft": {
                    "current_task": {"id": 19, "status": "approved"},
                },
                "/api/agent/v1/campaigns/8/approve-drafts": {
                    "approved_count": 2,
                    "campaign": {"id": 8, "status": "paused", "approved_count": 2},
                },
            },
        )
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=fake_client,
        ):
            draft = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "--if-revision",
                    "draft-revision",
                    "drafts",
                    "approve",
                    "17",
                    "--subject",
                    "最终主题",
                    "--body-text",
                    "最终正文",
                    "--attachment-material-id",
                    "3",
                ],
            )
            thread = self.runner.invoke(
                app,
                ["--format", "json", "campaigns", "item-thread", "8", "19"],
            )
            item = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "campaigns",
                    "approve-item-draft",
                    "8",
                    "19",
                    "--body-text",
                    "活动正文",
                ],
            )
            bulk = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "campaigns",
                    "approve-drafts",
                    "8",
                    "--item-id",
                    "19",
                    "--item-id",
                    "20",
                ],
            )

        for result in (draft, thread, item, bulk):
            self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(
            [call[:2] for call in fake_client.calls],
            [
                ("POST", "/api/agent/v1/tasks/17/approve-draft"),
                ("GET", "/api/agent/v1/campaigns/8/items/19/thread"),
                ("POST", "/api/agent/v1/campaigns/8/items/19/approve-draft"),
                ("POST", "/api/agent/v1/campaigns/8/approve-drafts"),
            ],
        )
        self.assertEqual(
            fake_client.json_bodies[0],
            {
                "subject": "最终主题",
                "body_text": "最终正文",
                "body_html": None,
                "attachment_material_ids": [3],
            },
        )
        self.assertEqual(fake_client.json_bodies[3], {"item_ids": [19, 20]})

        duplicate = self.runner.invoke(
            app,
            [
                "--format",
                "json",
                "campaigns",
                "approve-drafts",
                "8",
                "--item-id",
                "19",
                "--item-id",
                "19",
            ],
        )
        self.assertEqual(duplicate.exit_code, 2, msg=duplicate.output)

    def test_delivery_commands_preserve_page_filters_and_concurrency_token(
        self,
    ) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/deliveries": {
                    "items": [
                        {
                            "id": 17,
                            "status": "waiting_scheduled",
                            "expected_updated_at": "2026-08-09T09:00:00.123456+00:00",
                        },
                    ],
                    "next_cursor": "3",
                    "has_more": True,
                    "pagination_mode": "page",
                    "total": 5,
                },
                "/api/agent/v1/deliveries/17/schedule": {
                    "ok": True,
                    "task_id": 17,
                    "message": "发送时间已更新",
                },
            },
        )
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=fake_client,
        ):
            listed = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "deliveries",
                    "list",
                    "--view",
                    "attention",
                    "--page",
                    "2",
                    "--page-size",
                    "1",
                    "--source",
                    "manual",
                    "--search-field",
                    "recipient_name",
                    "--query",
                    "张老师",
                ],
            )
            changed = self.runner.invoke(
                app,
                [
                    "--format",
                    "json",
                    "deliveries",
                    "reschedule",
                    "17",
                    "--scheduled-at",
                    "2026-08-10T09:00:00+08:00",
                    "--expected-updated-at",
                    "2026-08-09T09:00:00.123456+00:00",
                ],
            )

        self.assertEqual(listed.exit_code, 0, msg=listed.output)
        self.assertEqual(changed.exit_code, 0, msg=changed.output)
        self.assertEqual(fake_client.calls[0][0:2], ("GET", "/api/agent/v1/deliveries"))
        self.assertEqual(
            fake_client.calls[0][2],
            {
                "view": "attention",
                "page": 2,
                "page_size": 1,
                "source": "manual",
                "search_fields": "recipient_name",
                "query": "张老师",
            },
        )
        self.assertEqual(
            fake_client.calls[1][0:2],
            ("PATCH", "/api/agent/v1/deliveries/17/schedule"),
        )
        self.assertEqual(
            fake_client.json_bodies[1],
            {
                "scheduled_at": "2026-08-10T09:00:00+08:00",
                "expected_updated_at": "2026-08-09T09:00:00.123456+00:00",
            },
        )

    def test_professor_template_download_refuses_accidental_overwrite(self) -> None:
        fake_client = _FakeAgentClient(
            {"/api/agent/v1/professors/import-template": b"template-bytes"},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "professors.csv"
            with patch(
                "auto_email_sender_cli.commands.professors.AgentApiClient",
                return_value=fake_client,
            ):
                downloaded = self.runner.invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "professors",
                        "download-template",
                        "--output",
                        output.as_posix(),
                        "--format",
                        "csv",
                    ],
                )
                existing = self.runner.invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "professors",
                        "download-template",
                        "--output",
                        output.as_posix(),
                        "--format",
                        "csv",
                    ],
                )

            self.assertEqual(downloaded.exit_code, 0, msg=downloaded.output)
            self.assertEqual(output.read_bytes(), b"template-bytes")
            self.assertEqual(existing.exit_code, 2, msg=existing.output)
            self.assertEqual(
                json.loads(existing.stdout)["error"]["code"], "OUTPUT_EXISTS"
            )
            self.assertEqual(
                fake_client.download_calls,
                [
                    "/api/agent/v1/professors/import-template",
                    "/api/agent/v1/professors/import-template",
                ],
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

    def test_professor_bulk_archive_accepts_a_reusable_filter_selection(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/professors/prepare-bulk-archive": {
                    "plan_id": "change_bulk_archive_selection",
                    "action": "professor.archive.bulk",
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
                    "prepare-bulk-archive",
                    "--selection-filter",
                    '{"name":{"contains_script":"latin"}}',
                    "--archived",
                    "all",
                    "--exclude-id",
                    "9",
                ],
            )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(
            fake_client.json_bodies[0],
            {
                "selection": {
                    "mode": "filter",
                    "filter": {
                        "archived": "all",
                        "where": {"name": {"contains_script": "latin"}},
                    },
                    "exclude_ids": [9],
                },
            },
        )

        conflicting = self.runner.invoke(
            app,
            [
                "--format",
                "json",
                "professors",
                "prepare-bulk-archive",
                "--professor-id",
                "7",
                "--selection-filter",
                '{"name":{"contains":"A"}}',
            ],
        )
        self.assertEqual(conflicting.exit_code, 2, msg=conflicting.output)
        self.assertEqual(
            json.loads(conflicting.stdout)["error"]["code"], "INVALID_ARGUMENT"
        )

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
            with (
                patch(
                    "auto_email_sender_cli.commands.common.AgentApiClient",
                    return_value=fake_client,
                ),
                patch(
                    "auto_email_sender_cli.commands.professors.AgentApiClient",
                    return_value=fake_client,
                ),
            ):
                catalog = self.runner.invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "professors",
                        "community",
                        "catalog",
                        "--refresh",
                    ],
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
        self.assertEqual(
            fake_client.calls[0][:2], ("GET", "/api/agent/v1/community-mentors/catalog")
        )
        self.assertEqual(fake_client.calls[0][2], {"refresh": True})
        self.assertEqual(
            fake_client.calls[1][:2],
            ("POST", "/api/agent/v1/community-mentors/records"),
        )
        self.assertEqual(
            fake_client.json_bodies[1],
            {
                "dataset_version": import_payload["dataset_version"],
                "unit_paths": import_payload["unit_paths"],
            },
        )
        self.assertEqual(
            fake_client.calls[2][:2],
            ("POST", "/api/agent/v1/community-mentors/preview"),
        )
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
        self.assertEqual(
            fake_client.calls[0][:2], ("GET", "/api/agent/v1/test-email/2/status")
        )
        self.assertEqual(
            fake_client.calls[1][:2], ("GET", "/api/agent/v1/test-email/2/3")
        )
        self.assertEqual(
            fake_client.calls[2][:2],
            ("POST", "/api/agent/v1/test-email/2/3/generate-draft"),
        )
        self.assertEqual(fake_client.json_bodies[2], {"outreach_template_id": 4})
        self.assertEqual(
            fake_client.calls[3][:2], ("PUT", "/api/agent/v1/test-email/2/3/draft")
        )
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
            with (
                patch(
                    "auto_email_sender_cli.commands.resources.AgentApiClient",
                    return_value=fake_client,
                ),
                patch(
                    "auto_email_sender_cli.commands.common.AgentApiClient",
                    return_value=fake_client,
                ),
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
                    [
                        "--format",
                        "json",
                        "materials",
                        "set-primary",
                        "8",
                        "--identity-id",
                        "3",
                    ],
                )
                missing_target = self.runner.invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "materials",
                        "set-primary",
                        "8",
                    ],
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
        self.assertEqual(missing_target.exit_code, 2, msg=missing_target.output)
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
        self.assertEqual(fake_client.calls[1][2], {"identity_id": 3})
        self.assertEqual(
            fake_client.calls[2][:2],
            ("POST", "/api/agent/v1/materials/8/prepare-delete"),
        )
        self.assertEqual(
            fake_client.download_calls,
            ["/api/agent/v1/materials/8/download"],
        )

    def test_material_upload_without_identity_uses_global_catalog(self) -> None:
        fake_client = _FakeAgentClient(
            {
                "/api/agent/v1/materials": {
                    "id": 9,
                    "source_identity_id": None,
                    "display_name": "共享附件",
                    "is_primary": False,
                },
            },
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "shared.txt"
            file_path.write_text("shared attachment", encoding="utf-8")
            with patch(
                "auto_email_sender_cli.commands.resources.AgentApiClient",
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
                    ],
                )

        self.assertEqual(upload.exit_code, 0, msg=upload.output)
        self.assertEqual(
            fake_client.data_bodies[0],
            {"material_type": "other", "display_name": ""},
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
            rows = [
                json.loads(line)
                for line in output.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(rows[0]["type"], "meta")
            self.assertEqual(rows[1]["data"]["content"], "没有名额")
            self.assertEqual(
                rows[1]["data"]["trust_level"], "untrusted_external_content"
            )
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

    def test_single_task_commands_use_agent_routes_without_direct_delivery(
        self,
    ) -> None:
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

    def test_single_task_outreach_config_rejects_conflicting_clear_options(
        self,
    ) -> None:
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

    def test_matching_commands_use_agent_routes_and_keep_async_job_state_visible(
        self,
    ) -> None:
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
                [
                    "--format",
                    "json",
                    "usage",
                    "records",
                    "--feature-type",
                    "match_analysis",
                ],
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
                {"page": 1, "page_size": 25, "feature_type": "match_analysis"},
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
                    "items": [
                        {"id": 5, "professor_name": "补全导师", "status": "queued"}
                    ],
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

    def test_crawler_commands_use_scoped_agent_routes_and_keep_web_content_marked(
        self,
    ) -> None:
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
                "/api/agent/v1/crawler/jobs/52/cancel": {
                    "id": 52,
                    "status": "canceled",
                },
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
                    "--selection",
                    "ids",
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
                    "--selection",
                    "ids",
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
        self.assertEqual(
            fake_client.json_bodies[10],
            {
                "selection": {
                    "mode": "ids",
                    "ids": [7, 8],
                    "filter": {},
                    "exclude_ids": [],
                },
            },
        )
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
            {
                "selection": {
                    "mode": "ids",
                    "ids": [7, 8],
                    "filter": {},
                    "exclude_ids": [],
                },
                "llm_profile_id": 3,
            },
        )

    def test_communication_group_commands_preserve_and_update_match_source(
        self,
    ) -> None:
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
                    "--match-source-identity-id",
                    "2",
                    "--confirm-merge-existing-groups",
                ],
            )
            update_source = self.runner.invoke(
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
                    "--match-source-identity-id",
                    "3",
                ],
            )
            preserve_source = self.runner.invoke(
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
            clear_source = self.runner.invoke(
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
                    "--clear-match-source-identity",
                ],
            )
            delete = self.runner.invoke(
                app,
                ["--format", "json", "communication-groups", "delete", "12"],
            )

        for result in (
            create,
            update_source,
            preserve_source,
            clear_source,
            delete,
        ):
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
                "match_source_identity_id": 2,
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
                "match_source_identity_id": 3,
            },
        )
        self.assertEqual(
            fake_client.calls[2][:2],
            ("PUT", "/api/agent/v1/communication-groups/12"),
        )
        self.assertEqual(
            fake_client.json_bodies[2],
            {
                "identity_ids": [2, 3, 4],
                "confirm_merge_existing_groups": False,
            },
        )
        self.assertEqual(
            fake_client.calls[3][:2],
            ("PUT", "/api/agent/v1/communication-groups/12"),
        )
        self.assertEqual(
            fake_client.json_bodies[3],
            {
                "identity_ids": [2, 3, 4],
                "confirm_merge_existing_groups": False,
                "match_source_identity_id": None,
            },
        )
        self.assertEqual(
            fake_client.calls[4][:2],
            ("POST", "/api/agent/v1/communication-groups/12/delete"),
        )

    def test_communication_group_update_rejects_conflicting_match_source_flags(
        self,
    ) -> None:
        fake_client = _FakeAgentClient({})
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=fake_client,
        ):
            result = self.runner.invoke(
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
                    "--match-source-identity-id",
                    "2",
                    "--clear-match-source-identity",
                ],
            )

        self.assertEqual(result.exit_code, 2, msg=result.output)
        self.assertIn("不能同时使用", json.loads(result.stdout)["error"]["message"])
        self.assertEqual(fake_client.calls, [])

    def test_settings_update_merges_only_explicit_fields_with_current_settings(
        self,
    ) -> None:
        settings = {
            "match_analysis_job_worker_count": 1,
            "match_analysis_job_item_concurrency": 2,
            "match_analysis_job_interval_seconds": 5,
            "crawler_worker_count": 1,
            "crawler_profile_enrichment_concurrency": 2,
            "crawler_host_concurrency": 1,
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

    def test_identity_default_template_and_connection_commands_use_safe_routes(
        self,
    ) -> None:
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
            {
                "/api/agent/v1/professors/export": b"name,email\nExported,export@example.edu\n"
            },
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
        self.assertEqual(
            fake_client.download_calls, ["/api/agent/v1/professors/export"]
        )
        self.assertEqual(fake_client.download_params, [{"format": "csv"}])

    def test_diagnostics_commands_use_safe_routes_and_require_force_to_overwrite(
        self,
    ) -> None:
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
            with (
                patch(
                    "auto_email_sender_cli.commands.common.AgentApiClient",
                    return_value=log_client,
                ),
                patch(
                    "auto_email_sender_cli.commands.diagnostics.AgentApiClient",
                    return_value=export_client,
                ),
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
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(export_path.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(debug_path.stat().st_mode), 0o600)

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

    def test_generate_draft_keeps_reference_and_attachments_in_distinct_fields(
        self,
    ) -> None:
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
        payload = json.loads(result.stdout)
        self.assertNotIn("pagination", payload["_meta"])

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
                    "--confirmed-fingerprint",
                    "fingerprint-test",
                ],
            )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(fake_client.calls[0][0], "POST")
        self.assertEqual(
            fake_client.calls[0][1],
            "/api/agent/v1/plans/plan_test/execute",
        )
        self.assertEqual(
            fake_client.json_bodies[0],
            {"confirm": True, "confirmed_fingerprint": "fingerprint-test"},
        )
        self.assertEqual(json.loads(result.stdout)["data"]["result"]["outcome"], "sent")


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
        response = self.responses[path]
        if isinstance(response, Exception):
            raise response
        return response

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


class _ConcurrentWaitAgentClient(_FakeAgentClient):
    def __init__(self) -> None:
        super().__init__({})
        self._lock = threading.Lock()
        self._active_requests = 0
        self.max_active_requests = 0

    def request(self, method: str, path: str, **kwargs: object) -> object:
        with self._lock:
            self._active_requests += 1
            self.max_active_requests = max(
                self.max_active_requests,
                self._active_requests,
            )
        try:
            time.sleep(0.02)
            resource_id = int(path.rsplit("/", 1)[-1])
            return {"id": resource_id, "status": "needs_review"}
        finally:
            with self._lock:
                self._active_requests -= 1


class _RecoveringWaitAgentClient(_FakeAgentClient):
    def __init__(self) -> None:
        super().__init__({})
        self._attempts_by_path: dict[str, int] = {}

    def request(self, method: str, path: str, **kwargs: object) -> object:
        attempts = self._attempts_by_path.get(path, 0) + 1
        self._attempts_by_path[path] = attempts
        if path.endswith("/52") and attempts == 1:
            raise CliError(
                code="APP_UNAVAILABLE",
                message="本地服务暂时不可用",
                exit_code=7,
                retryable=True,
            )
        resource_id = int(path.rsplit("/", 1)[-1])
        return {"id": resource_id, "status": "needs_review"}


if __name__ == "__main__":
    unittest.main()
