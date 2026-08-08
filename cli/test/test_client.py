from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx

from auto_email_sender_cli.client import AgentApiClient, _exit_code_for_api_error
from auto_email_sender_cli.commands.common import fetch_all_pages
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
        http_client, _ = _http_client(httpx.Response(200, content=b"not-json"))
        with patch("auto_email_sender_cli.client.ensure_runtime_descriptor", return_value=descriptor):
            with self.assertRaises(CliError) as raised:
                AgentApiClient(descriptor, http_client=http_client).request(
                    "GET",
                    "/api/agent/v1/info",
                )

        self.assertEqual(raised.exception.code, "INVALID_API_RESPONSE")
        self.assertEqual(raised.exception.exit_code, 8)

    def test_network_failure_recovers_runtime_and_retries_once(self) -> None:
        first = _descriptor(base_url="http://127.0.0.1:48120", access_token="old-token")
        second = _descriptor(base_url="http://127.0.0.1:48121", access_token="new-token")
        http_client, transport = _http_client(
            httpx.ConnectError("backend restarting"),
            httpx.Response(200, json={"status": "ok"}),
        )
        with patch(
            "auto_email_sender_cli.client.ensure_runtime_descriptor",
            side_effect=[first, second],
        ) as ensure_runtime:
            client = AgentApiClient(http_client=http_client)
            result = client.request("GET", "/api/ping")

        self.assertEqual(result, {"status": "ok"})
        self.assertEqual(ensure_runtime.call_count, 2)
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual(
            transport.requests[1].headers["Authorization"],
            "Bearer new-token",
        )

    def test_runtime_refresh_rechecks_protocol_before_retrying(self) -> None:
        first = _descriptor(base_url="http://127.0.0.1:48120")
        incompatible = _descriptor(
            base_url="http://127.0.0.1:48121",
            protocol_version="1",
        )
        http_client, transport = _http_client(httpx.ConnectError("backend restarting"))
        with patch(
            "auto_email_sender_cli.client.ensure_runtime_descriptor",
            side_effect=[first, incompatible],
        ):
            with self.assertRaises(RuntimeProtocolMismatchError):
                AgentApiClient(http_client=http_client).request("GET", "/api/ping")

        self.assertEqual(len(transport.requests), 1)

    def test_rotated_token_is_reloaded_after_authentication_failure(self) -> None:
        first = _descriptor(access_token="old-token")
        second = _descriptor(access_token="new-token", base_url="http://127.0.0.1:48122")
        http_client, transport = _http_client(
            httpx.Response(401, json={"error": {"code": "INVALID_ACCESS_TOKEN"}}),
            httpx.Response(200, json={"status": "ok"}),
        )
        with patch(
            "auto_email_sender_cli.client.ensure_runtime_descriptor",
            side_effect=[first, second],
        ):
            result = AgentApiClient(http_client=http_client).request("GET", "/api/ping")

        self.assertEqual(result, {"status": "ok"})
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual(
            transport.requests[1].headers["Authorization"],
            "Bearer new-token",
        )

    def test_download_bytes_uses_agent_token_and_binary_accept_header(self) -> None:
        descriptor = _descriptor()
        http_client, transport = _http_client(httpx.Response(200, content=b"resume content"))
        with patch("auto_email_sender_cli.client.ensure_runtime_descriptor", return_value=descriptor):
            result = AgentApiClient(http_client=http_client).download_bytes(
                "/api/agent/v1/materials/8/download",
            )

        self.assertEqual(result, b"resume content")
        self.assertEqual(transport.requests[0].headers["Authorization"], "Bearer agent-token")
        self.assertEqual(
            transport.requests[0].headers["Accept"],
            "application/octet-stream",
        )

    def test_direct_runtime_descriptor_with_old_protocol_is_rejected_before_request(self) -> None:
        descriptor = _descriptor(protocol_version="1")
        http_client, transport = _http_client(httpx.Response(200, json={}))
        with self.assertRaises(RuntimeProtocolMismatchError):
            AgentApiClient(descriptor, http_client=http_client).request(
                "GET",
                "/api/agent/v1/professors",
            )

        self.assertEqual(transport.requests, [])

    def test_non_idempotent_network_timeout_is_reported_as_unknown_without_retry(self) -> None:
        descriptor = _descriptor()
        http_client, transport = _http_client(httpx.ReadTimeout("smtp timeout"))
        with patch("auto_email_sender_cli.client.ensure_runtime_descriptor", return_value=descriptor):
            with self.assertRaises(ExternalExecutionUnknownError) as raised:
                AgentApiClient(descriptor, http_client=http_client).request(
                    "POST",
                    "/api/agent/v1/plans/42/execute",
                    idempotency_key="req_same",
                )

        self.assertEqual(raised.exception.code, "EXTERNAL_EXECUTION_UNKNOWN")
        self.assertEqual(raised.exception.details["request_id"], "req_same")
        self.assertEqual(len(transport.requests), 1)

    def test_mutating_connect_error_is_app_unavailable_without_a_second_execution(self) -> None:
        descriptor = _descriptor()
        http_client, transport = _http_client(httpx.ConnectError("desktop is closed"))
        with self.assertRaises(RuntimeUnavailableError) as raised:
            AgentApiClient(descriptor, http_client=http_client).request(
                "POST",
                "/api/agent/v1/communications/sync",
                json_body={"identity_id": 1},
                idempotency_key="req_app_closed",
            )

        self.assertEqual(raised.exception.code, "APP_UNAVAILABLE")
        self.assertIn("手动打开", raised.exception.message)
        self.assertEqual(len(transport.requests), 1)

    def test_mutating_connect_error_does_not_refresh_runtime_or_retry(self) -> None:
        first = _descriptor(base_url="http://127.0.0.1:48120")
        refreshed = _descriptor(base_url="http://127.0.0.1:48121")
        http_client, transport = _http_client(httpx.ConnectError("desktop is closed"))
        with patch(
            "auto_email_sender_cli.client.ensure_runtime_descriptor",
            side_effect=[first, refreshed],
        ) as ensure_runtime:
            with self.assertRaises(RuntimeUnavailableError):
                AgentApiClient(http_client=http_client).request(
                    "POST",
                    "/api/agent/v1/communications/sync",
                    json_body={"identity_id": 1},
                    idempotency_key="req_no_retry",
                )

        self.assertEqual(ensure_runtime.call_count, 1)
        self.assertEqual(len(transport.requests), 1)

    def test_multiple_requests_share_one_http_client_and_connection_pool(self) -> None:
        http_client, transport = _http_client(
            httpx.Response(200, json={"page": 1}),
            httpx.Response(200, json={"page": 2}),
        )
        client = AgentApiClient(_descriptor(), http_client=http_client)

        self.assertEqual(client.request("GET", "/api/pages/1"), {"page": 1})
        self.assertEqual(client.request("GET", "/api/pages/2"), {"page": 2})

        self.assertEqual(len(transport.requests), 2)
        self.assertIs(client._http_client, http_client)

    def test_multi_page_fetch_reuses_the_same_http_client(self) -> None:
        http_client, transport = _http_client(
            httpx.Response(
                200,
                json={"items": [{"id": 1}], "next_cursor": "1", "has_more": True},
            ),
            httpx.Response(
                200,
                json={"items": [{"id": 2}], "next_cursor": None, "has_more": False},
            ),
        )
        client = AgentApiClient(_descriptor(), http_client=http_client)

        result = fetch_all_pages(client, "/api/agent/v1/professors")

        self.assertEqual([item["id"] for item in result["items"]], [1, 2])
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual(transport.requests[1].url.params["cursor"], "1")


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
    return RuntimeDescriptor.from_mapping(values)


class _SequenceTransport(httpx.BaseTransport):
    def __init__(self, outcomes: tuple[httpx.Response | Exception, ...]) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return httpx.Response(
            outcome.status_code,
            headers=outcome.headers,
            content=outcome.content,
            request=request,
        )


def _http_client(*outcomes: httpx.Response | Exception) -> tuple[httpx.Client, _SequenceTransport]:
    transport = _SequenceTransport(outcomes)
    return httpx.Client(transport=transport), transport


if __name__ == "__main__":
    unittest.main()
