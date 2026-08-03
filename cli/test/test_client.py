from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx

from auto_email_sender_cli.client import AgentApiClient
from auto_email_sender_cli.runtime import RuntimeDescriptor


class AgentApiClientTests(unittest.TestCase):
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


def _descriptor(**overrides: object) -> RuntimeDescriptor:
    values: dict[str, object] = {
        "protocol_version": "1",
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
