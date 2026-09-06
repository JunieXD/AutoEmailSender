"""Exercise Agent CLI workflows against isolated fixtures; never contact the app.

Measures protocol usability (calls, bytes, recovery), not model reasoning accuracy.
Run with uv run --project cli python scripts/quality/benchmark_agent_workflows.py.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from auto_email_sender_cli.main import app
from typer.testing import CliRunner


def run_workflows() -> dict[str, object]:
    runner = CliRunner()
    measurements: list[dict[str, object]] = []

    def scenario(name: str):
        metrics = {
            "scenario": name,
            "calls": 0,
            "context_bytes": 0,
            "contract_lookups": 0,
            "unexpected_errors": 0,
        }
        measurements.append(metrics)

        def call(arguments: list[str], payload: object = None, expected_exit: int = 0):
            result = runner.invoke(
                app,
                ["--json", *arguments],
                input=json.dumps(payload) if payload is not None else None,
            )
            metrics["calls"] += 1
            metrics["context_bytes"] += len(result.stdout.encode("utf-8"))
            metrics["contract_lookups"] += int(arguments[0] == "describe")
            metrics["unexpected_errors"] += int(result.exit_code != expected_exit)
            assert result.exit_code == expected_exit, result.output or repr(
                result.exception
            )
            return json.loads(result.stdout)

        return call

    def invoke(call, action, extra=None, expected_exit=0):
        return call(
            ["invoke", "--command", action["command"], "--input", "-"],
            {**action.get("input", {}), **(extra or {})},
            expected_exit,
        )

    client = Mock()
    client.descriptor = SimpleNamespace(app_version="fixture")
    client.last_request_id = None
    client.last_response_headers = {}
    with (
        patch(
            "auto_email_sender_cli.commands.common.AgentApiClient", return_value=client
        ),
        patch(
            "auto_email_sender_cli.commands.wait.AgentApiClient", return_value=client
        ),
    ):
        call = scenario("discover_and_read")
        discovered = call(
            [
                "capabilities",
                "--intent",
                "列出当前系统中所有姓名包含英文字母的导师",
                "--limit",
                "1",
                "--with-contract",
            ]
        )["data"]
        contract = discovered["execution_contract"]
        assert contract["command"] == "professors.list"
        assert "query" in contract["input"]["optional_contracts"]
        client.request.return_value = {
            "items": [{"id": 7, "name": "Example", "email": "example@example.org"}],
            "next_cursor": None,
            "has_more": False,
        }
        result = invoke(
            call, {"command": contract["command"], "input": {"query": "Example"}}
        )
        assert result["data"]["items"][0]["id"] == 7
        assert client.request.call_args.kwargs["params"]["q"] == "Example"
        measurements[-1]["first_attempt_success"] = True

        call = scenario("list_and_wait")
        client.request.return_value = {
            "items": [{"id": 51, "status": "running"}, {"id": 52, "status": "running"}],
            "next_cursor": None,
            "has_more": False,
        }
        listed = call(["crawler", "jobs", "list"])["data"]
        group = listed["action_groups"][0]
        action = next(
            item for item in group["available_actions"] if item["action"] == "wait"
        )
        chosen_id = group["ids"][0]
        bound_input = {
            key: chosen_id if binding == "id" else [chosen_id]
            for key, binding in action["input_bindings"].items()
        }
        client.request.return_value = {"id": chosen_id, "status": "needs_review"}
        result = invoke(call, action, {**bound_input, "timeout_seconds": 0})["data"]
        assert result["settled"] and not result["terminal"]
        assert client.request.call_args.args[1].endswith(f"/{chosen_id}")
        measurements[-1]["first_attempt_success"] = True

        call = scenario("confirmation_recovery")
        client.request.reset_mock()
        denied = invoke(
            call,
            {"command": "plans.execute", "input": {"plan_id": "plan-fixture"}},
            expected_exit=6,
        )
        client.request.assert_not_called()
        client.request.return_value = {
            "plan_id": "plan-fixture",
            "status": "pending",
            "content_fingerprint": "fixture-fingerprint",
        }
        plan = invoke(call, denied["error"]["recovery_action"])["data"]
        execute = next(
            item for item in plan["available_actions"] if item["action"] == "execute"
        )
        assert execute["required_input"] == ["confirm"]
        assert "confirm" not in execute["input"]
        assert execute["input"]["confirmed_fingerprint"] == "fixture-fingerprint"
        # Fixture confirmation only: no real plan or provider is contacted.
        client.request.return_value = {"plan_id": "plan-fixture", "status": "executed"}
        result = invoke(call, execute, {"confirm": True})
        assert result["ok"]
        assert client.request.call_args.kwargs["json_body"]["confirm"] is True
        measurements[-1]["recovered"] = True

        call = scenario("oversize_export_recovery")
        original = {"query": "Example", "all_items": True}
        page = {
            "items": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}],
            "next_cursor": "2",
            "has_more": True,
        }
        client.request.return_value = page
        error = call(
            [
                "--max-items",
                "1",
                "invoke",
                "--command",
                "professors.list",
                "--input",
                "-",
            ],
            original,
            8,
        )["error"]
        assert error["code"] == "RESULT_TOO_LARGE"
        action = error["recovery_action"]
        client.request.side_effect = [
            page,
            {"items": [{"id": 3, "name": "C"}], "next_cursor": None, "has_more": False},
        ]
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "complete.jsonl"
            assert action["global_options"] == {"output_file": "<path>.jsonl"}
            result = call(
                [
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
                {**original, **action["input"]},
            )
            assert [
                json.loads(line)["id"] for line in destination.read_text().splitlines()
            ] == [1, 2, 3]
            assert result["data"]["has_more"] is False
        measurements[-1]["recovered"] = True

    return {
        "method": "deterministic isolated CLI workflows; no model or external provider",
        "scenarios": measurements,
        "total_context_bytes": sum(item["context_bytes"] for item in measurements),
        "unexpected_errors": sum(item["unexpected_errors"] for item in measurements),
    }


if __name__ == "__main__":
    print(json.dumps(run_workflows(), ensure_ascii=False, indent=2))
