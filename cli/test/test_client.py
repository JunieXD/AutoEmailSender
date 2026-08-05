from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx

from auto_email_sender_cli.client import AgentApiClient, _exit_code_for_api_error
from auto_email_sender_cli.errors import (
    CliError,
    ExternalExecutionUnknownError,
    RuntimeUnavailableError,
    RuntimeProtocolMismatchError,
)
from auto_email_sender_cli.runtime import RuntimeDescriptor


class AgentApiClientTests(unittest.TestCase):
    def test_api_error_exit_codes_follow_error_contract_not_only_http_status(self) -> None:
        self.assertEqual(
            _exit_code_for_api_error(
                status_code=409,
                code="PLAN_CONFIRMATION_REQUIRED",
            ),
            6,
        )
        self.assertEqual(
            _exit_code_for_api_error(
                status_code=409,
                code="COMMUNICATION_GROUP_MERGE_CONFIRMATION_REQUIRED",
            ),
            6,
        )
        self.assertEqual(
            _exit_code_for_api_error(
                status_code=422,
                code="INVALID_AGENT_REQUEST",
            ),
            2,
        )
        self.assertEqual(
            _exit_code_for_api_error(
                status_code=503,
                code="APP_UNAVAILABLE",
            ),
            7,
        )
        self.assertEqual(
            _exit_code_for_api_error(
                status_code=409,
                code="REVISION_CONFLICT",
            ),
            5,
        )

    def test_successful_non_json_response_is_a_structured_cli_error(self) -> None:
        descriptor = _descriptor()
        response = httpx.Response(
            200,
            content=b"not-json",
            request=httpx.Request("GET", "http://127.0.0.1:48120/api/agent/v1/info"),
        )
        with (
            patch("auto_email_sender_cli.client.ensure_runtime_descriptor", return_value=descriptor),
            patch("auto_email_sender_cli.client.httpx.request", return_value=response),
        ):
            with self.assertRaises(CliError) as raised:
                AgentApiClient(descriptor).request("GET", "/api/agent/v1/info")

        self.assertEqual(raised.exception.code, "INVALID_API_RESPONSE")
        self.assertEqual(raised.exception.exit_code, 8)

    def test_network_failure_recovers_runtime_and_retries_once(self) -> None:
        first = _descriptor(base_url="http://127.0.0.1:48120", access_token="old-token")
        second = _descriptor(base_url="http://127.0.0.1:48121", access_token="new-token")
        success = httpx.Response(
            200,
            json={"status": "ok"},
            request=httpx.Request("GET", "http://127.0.0.1:48121/api/ping"),
        )
        with (
            patch(
                "auto_email_sender_cli.client.ensure_runtime_descriptor",
                side_effect=[first, second],
            ) as ensure_runtime,
            patch(
                "auto_email_sender_cli.client.httpx.request",
                side_effect=[httpx.ConnectError("backend restarting"), success],
            ) as request,
        ):
            client = AgentApiClient()
            result = client.request("GET", "/api/ping")

        self.assertEqual(result, {"status": "ok"})
        self.assertEqual(ensure_runtime.call_count, 2)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(
            request.call_args_list[1].kwargs["headers"]["Authorization"],
            "Bearer new-token",
        )

    def test_runtime_refresh_rechecks_protocol_before_retrying(self) -> None:
        first = _descriptor(base_url="http://127.0.0.1:48120")
        incompatible = _descriptor(
            base_url="http://127.0.0.1:48121",
            protocol_version="1",
        )
        with (
            patch(
                "auto_email_sender_cli.client.ensure_runtime_descriptor",
                side_effect=[first, incompatible],
            ),
            patch(
                "auto_email_sender_cli.client.httpx.request",
                side_effect=httpx.ConnectError("backend restarting"),
            ) as request,
        ):
            with self.assertRaises(RuntimeProtocolMismatchError):
                AgentApiClient().request("GET", "/api/ping")

        request.assert_called_once()

    def test_rotated_token_is_reloaded_after_authentication_failure(self) -> None:
        first = _descriptor(access_token="old-token")
        second = _descriptor(access_token="new-token", base_url="http://127.0.0.1:48122")
        unauthorized = httpx.Response(
            401,
            json={"error": {"code": "INVALID_ACCESS_TOKEN"}},
            request=httpx.Request("GET", "http://127.0.0.1:48120/api/ping"),
        )
        success = httpx.Response(
            200,
            json={"status": "ok"},
            request=httpx.Request("GET", "http://127.0.0.1:48122/api/ping"),
        )
        with (
            patch(
                "auto_email_sender_cli.client.ensure_runtime_descriptor",
                side_effect=[first, second],
            ),
            patch(
                "auto_email_sender_cli.client.httpx.request",
                side_effect=[unauthorized, success],
            ) as request,
        ):
            result = AgentApiClient().request("GET", "/api/ping")

        self.assertEqual(result, {"status": "ok"})
        self.assertEqual(request.call_count, 2)
        self.assertEqual(
            request.call_args_list[1].kwargs["headers"]["Authorization"],
            "Bearer new-token",
        )

    def test_download_bytes_uses_agent_token_and_binary_accept_header(self) -> None:
        descriptor = _descriptor()
        response = httpx.Response(
            200,
            content=b"resume content",
            request=httpx.Request("GET", "http://127.0.0.1:48120/api/materials/8/download"),
        )
        with (
            patch(
                "auto_email_sender_cli.client.ensure_runtime_descriptor",
                return_value=descriptor,
            ),
            patch(
                "auto_email_sender_cli.client.httpx.request",
                return_value=response,
            ) as request,
        ):
            result = AgentApiClient().download_bytes("/api/agent/v1/materials/8/download")

        self.assertEqual(result, b"resume content")
        self.assertEqual(request.call_args.kwargs["headers"]["Authorization"], "Bearer agent-token")
        self.assertEqual(
            request.call_args.kwargs["headers"]["Accept"],
            "application/octet-stream",
        )

    def test_direct_runtime_descriptor_with_old_protocol_is_rejected_before_request(self) -> None:
        descriptor = _descriptor(protocol_version="1")
        with patch("auto_email_sender_cli.client.httpx.request") as request:
            with self.assertRaises(RuntimeProtocolMismatchError):
                AgentApiClient(descriptor).request("GET", "/api/agent/v1/professors")

        request.assert_not_called()

    def test_non_idempotent_network_timeout_is_reported_as_unknown_without_retry(self) -> None:
        descriptor = _descriptor()
        with (
            patch("auto_email_sender_cli.client.httpx.request", side_effect=httpx.ReadTimeout("smtp timeout")) as request,
            patch("auto_email_sender_cli.client.ensure_runtime_descriptor", return_value=descriptor),
        ):
            with self.assertRaises(ExternalExecutionUnknownError) as raised:
                AgentApiClient(descriptor).request(
                    "POST",
                    "/api/agent/v1/plans/42/execute",
                    idempotency_key="req_same",
                )

        self.assertEqual(raised.exception.code, "EXTERNAL_EXECUTION_UNKNOWN")
        self.assertEqual(raised.exception.details["request_id"], "req_same")
        request.assert_called_once()

    def test_mutating_connect_error_is_app_unavailable_without_a_second_execution(self) -> None:
        descriptor = _descriptor()
        with patch(
            "auto_email_sender_cli.client.httpx.request",
            side_effect=httpx.ConnectError("desktop is closed"),
        ) as request:
            with self.assertRaises(RuntimeUnavailableError) as raised:
                AgentApiClient(descriptor).request(
                    "POST",
                    "/api/agent/v1/communications/sync",
                    json_body={"identity_id": 1},
                    idempotency_key="req_app_closed",
                )

        self.assertEqual(raised.exception.code, "APP_UNAVAILABLE")
        self.assertIn("手动打开", raised.exception.message)
        request.assert_called_once()

    def test_mutating_connect_error_does_not_refresh_runtime_or_retry(self) -> None:
        first = _descriptor(base_url="http://127.0.0.1:48120")
        refreshed = _descriptor(base_url="http://127.0.0.1:48121")
        with (
            patch(
                "auto_email_sender_cli.client.ensure_runtime_descriptor",
                side_effect=[first, refreshed],
            ) as ensure_runtime,
            patch(
                "auto_email_sender_cli.client.httpx.request",
                side_effect=httpx.ConnectError("desktop is closed"),
            ) as request,
        ):
            with self.assertRaises(RuntimeUnavailableError):
                AgentApiClient().request(
                    "POST",
                    "/api/agent/v1/communications/sync",
                    json_body={"identity_id": 1},
                    idempotency_key="req_no_retry",
                )

        self.assertEqual(ensure_runtime.call_count, 1)
        request.assert_called_once()


def _descriptor(**overrides: object) -> RuntimeDescriptor:
    values: dict[str, object] = {
        "protocol_version": "2",
        "app_version": "2.4.1",
        "base_url": "http://127.0.0.1:48120",
        "access_token": "agent-token",
        "desktop_pid": 1234,
        "started_at": "2026-08-03T00:00:00Z",
    }
    values.update(overrides)
    return RuntimeDescriptor.model_validate(values)


if __name__ == "__main__":
    unittest.main()
