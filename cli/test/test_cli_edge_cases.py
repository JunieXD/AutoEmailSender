from __future__ import annotations

import errno
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import BinaryIO, Self
from unittest.mock import Mock, patch

from auto_email_sender_cli.main import app
from auto_email_sender_cli.result_protocol import prepare_result_data
from typer.testing import CliRunner


class CliEdgeCaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()
        self.client = Mock()
        self.client.descriptor = SimpleNamespace(app_version="test")
        self.client.last_request_id = None
        self.client.last_response_headers = {}

    def test_invoke_reports_invalid_file_encoding_as_json(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "input.json"
            source.write_bytes('{"professor_id":1}'.encode("utf-16"))
            result = self.runner.invoke(
                app,
                [
                    "--json",
                    "invoke",
                    "--command",
                    "professors.get",
                    "--input",
                    str(source),
                ],
            )
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertEqual(
            json.loads(result.stdout)["error"]["code"], "INVALID_INVOKE_INPUT"
        )

    def test_invoke_preserves_option_shaped_positional_values(self) -> None:
        self.client.request.return_value = {"plan_id": "--help", "status": "canceled"}
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=self.client,
        ):
            result = self.runner.invoke(
                app,
                ["--json", "invoke", "--command", "plans.show", "--input", "-"],
                input='{"plan_id":"--help"}',
            )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(result.stdout)["data"]["plan_id"], "--help")
        self.assertEqual(
            self.client.request.call_args.args, ("GET", "/api/agent/v1/plans/--help")
        )

    def test_interspersed_global_options_preserve_leaf_format_and_request_id(
        self,
    ) -> None:
        self.client.download_bytes.return_value = b"id,name\n1,Example\n"
        with (
            tempfile.TemporaryDirectory() as folder,
            patch(
                "auto_email_sender_cli.commands.professors.AgentApiClient",
                return_value=self.client,
            ),
        ):
            destination = Path(folder) / "professors.csv"
            result = self.runner.invoke(
                app,
                [
                    "professors",
                    "--json",
                    "--operation-id",
                    "export-op",
                    "export",
                    "--format",
                    "csv",
                    "--output",
                    str(destination),
                ],
            )
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertEqual(destination.read_bytes(), b"id,name\n1,Example\n")
            self.client.download_bytes.assert_called_once_with(
                "/api/agent/v1/professors/export", params={"format": "csv"}
            )

        self.client.request.return_value = {
            "items": [],
            "has_more": False,
            "next_cursor": None,
        }
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=self.client,
        ):
            result = self.runner.invoke(
                app,
                [
                    "diagnostics",
                    "--json",
                    "--operation-id=read-op",
                    "logs",
                    "--request-id",
                    "log-filter",
                ],
            )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            self.client.request.call_args.kwargs["params"]["request_id"], "log-filter"
        )

    def test_export_handles_unusable_parent_as_json(self) -> None:
        self.client.download_bytes.return_value = b"export"
        with (
            tempfile.TemporaryDirectory() as folder,
            patch(
                "auto_email_sender_cli.commands.professors.AgentApiClient",
                return_value=self.client,
            ),
        ):
            blocker = Path(folder) / "file"
            blocker.write_text("original", encoding="utf-8")
            result = self.runner.invoke(
                app,
                [
                    "--json",
                    "professors",
                    "export",
                    "--output",
                    str(blocker / "export.csv"),
                ],
            )
            self.assertEqual(blocker.read_text(encoding="utf-8"), "original")
        self.assertNotEqual(result.exit_code, 0)
        self.assertFalse(json.loads(result.stdout)["ok"])

    def test_failed_export_keeps_original_and_removes_partial_file(self) -> None:
        self.client.download_bytes.return_value = b"new export"
        original_fdopen = os.fdopen

        class FailingWriter:
            def __init__(self, output: BinaryIO) -> None:
                self.output = output

            def __enter__(self) -> Self:
                return self

            def __exit__(self, *_: object) -> None:
                self.output.close()

            def write(self, content: bytes) -> None:
                self.output.write(content[:3])
                self.output.flush()
                raise OSError(errno.ENOSPC, "simulated disk full")

        def failing_fdopen(*args: object, **kwargs: object) -> FailingWriter:
            return FailingWriter(original_fdopen(*args, **kwargs))

        for command in ("export", "download-template"):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as folder:
                destination = Path(folder) / "export.csv"
                destination.write_bytes(b"original export")
                with (
                    patch(
                        "auto_email_sender_cli.commands.professors.AgentApiClient",
                        return_value=self.client,
                    ),
                    patch(
                        "auto_email_sender_cli.commands.exports.os.fdopen",
                        side_effect=failing_fdopen,
                    ),
                ):
                    result = self.runner.invoke(
                        app,
                        [
                            "--json",
                            "professors",
                            command,
                            "--output",
                            str(destination),
                            "--force",
                        ],
                    )
                self.assertEqual(result.exit_code, 8, result.output)
                self.assertEqual(
                    json.loads(result.stdout)["error"]["code"], "OUTPUT_WRITE_FAILED"
                )
                self.assertEqual(destination.read_bytes(), b"original export")
                self.assertEqual(list(Path(folder).iterdir()), [destination])

    def test_single_wait_returns_timeout_when_budget_expires_before_request(
        self,
    ) -> None:
        with (
            patch(
                "auto_email_sender_cli.commands.wait.AgentApiClient",
                return_value=self.client,
            ),
            patch(
                "auto_email_sender_cli.commands.wait.time.monotonic",
                side_effect=[0, 2, 2],
            ),
        ):
            result = self.runner.invoke(
                app,
                [
                    "--json",
                    "wait",
                    "--resource",
                    "crawler.jobs",
                    "--id",
                    "1",
                    "--timeout-seconds",
                    "1",
                ],
            )
        self.assertEqual(result.exit_code, 0, result.output)
        data = json.loads(result.stdout)["data"]
        self.assertTrue(data["timed_out"])
        self.assertEqual(data["poll_count"], 0)
        self.client.request.assert_not_called()

    def test_projection_metadata_cannot_exceed_final_data_budget(self) -> None:
        for size in (710, 950):
            with self.subTest(name_size=size):
                self.client.request.return_value = {"id": 1, "name": "x" * size}
                with patch(
                    "auto_email_sender_cli.commands.common.AgentApiClient",
                    return_value=self.client,
                ):
                    result = self.runner.invoke(
                        app,
                        [
                            "--json",
                            "--projection",
                            "full",
                            "--max-output-bytes",
                            "1024",
                            "professors",
                            "get",
                            "1",
                        ],
                    )
                self.assertEqual(result.exit_code, 0, result.output)
                data = json.loads(result.stdout)["data"]
                encoded = json.dumps(
                    data, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
                self.assertLessEqual(len(encoded), 1024)
                self.assertTrue(data["truncated"])
                self.assertIn("/name", data["omitted_paths"])

    def test_returned_recovery_action_exports_every_remaining_page(self) -> None:
        first_page = {
            "items": [{"id": 4, "name": "A"}, {"id": 5, "name": "B"}],
            "has_more": True,
            "next_cursor": "5",
            "limit": 2,
        }
        self.client.request.return_value = first_page
        original_input = {"query": "Example", "cursor": 3, "limit": 2}
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=self.client,
        ):
            initial = self.runner.invoke(
                app,
                [
                    "--json",
                    "--max-items",
                    "1",
                    "invoke",
                    "--command",
                    "professors.list",
                    "--input",
                    "-",
                ],
                input=json.dumps(original_input),
            )
            self.assertEqual(initial.exit_code, 0, initial.output)
            action = json.loads(initial.stdout)["data"]["recovery_action"]
            self.client.request.reset_mock()
            self.client.request.side_effect = [
                first_page,
                {
                    "items": [{"id": 6, "name": "C"}],
                    "has_more": False,
                    "next_cursor": None,
                },
            ]
            with tempfile.TemporaryDirectory() as folder:
                destination = Path(folder) / "complete.jsonl"
                recovered = self.runner.invoke(
                    app,
                    [
                        "--json",
                        "--max-items",
                        "1",
                        "--output-file",
                        str(destination),
                        "invoke",
                        "--command",
                        action["command"],
                        "--input",
                        "-",
                    ],
                    input=json.dumps({**original_input, **action["input"]}),
                )
                self.assertEqual(recovered.exit_code, 0, recovered.output)
                self.assertEqual(
                    [
                        json.loads(line)["id"]
                        for line in destination.read_text().splitlines()
                    ],
                    [4, 5, 6],
                )
                self.assertFalse(json.loads(recovered.stdout)["data"]["has_more"])
        self.assertEqual(len(self.client.request.call_args_list), 2)
        self.assertEqual(
            self.client.request.call_args_list[0].kwargs["params"]["q"], "Example"
        )

    def test_non_collection_result_does_not_offer_unsupported_export(self) -> None:
        data = prepare_result_data(
            {"items": [{"id": index} for index in range(5)]},
            command="campaigns.resend-context",
            max_items=2,
        )
        self.assertNotIn("recovery_action", data)


if __name__ == "__main__":
    unittest.main()
