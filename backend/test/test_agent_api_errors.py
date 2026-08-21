from __future__ import annotations

import asyncio
import json
import unittest

from fastapi import HTTPException
from starlette.requests import Request

from app.core.agent_api_errors import (
    AgentApiError,
    agent_api_error_handler,
    http_exception_handler,
)


class AgentApiErrorTests(unittest.TestCase):
    def test_agent_http_exception_handler_returns_the_common_error_envelope(
        self,
    ) -> None:
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/agent/v1/professors/999",
                "headers": [],
                "query_string": b"",
                "client": ("test", 1),
                "server": ("test", 80),
                "scheme": "http",
            },
        )
        response = asyncio.run(
            http_exception_handler(
                request,
                HTTPException(status_code=404, detail="未找到导师"),
            ),
        )
        payload = json.loads(response.body)
        self.assertEqual(payload["error"]["code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(payload["error"]["details"]["http_status"], 404)
        self.assertEqual(payload["error"]["message"], "未找到导师")

    def test_agent_error_handler_redacts_nested_credentials(self) -> None:
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/agent/v1/test",
                "headers": [],
                "query_string": b"",
                "client": ("test", 1),
                "server": ("test", 80),
                "scheme": "http",
            },
        )
        error = AgentApiError(
            status_code=502,
            code="UPSTREAM_FAILED",
            message="provider password=top-secret; Authorization: Bearer bearer-secret",
            details={
                "password": "top-secret",
                "api_key": "api-secret",
                "nested": [
                    {"access_token": "access-secret", "body": "password=body-secret"},
                ],
                "comparison_token": "comparison-token-is-not-a-credential",
                "total_tokens": 17,
            },
            suggested_command="check password=command-secret",
        )

        response = asyncio.run(agent_api_error_handler(request, error))
        payload = json.loads(response.body)
        serialized = json.dumps(payload, ensure_ascii=False)

        self.assertNotIn("top-secret", serialized)
        self.assertNotIn("api-secret", serialized)
        self.assertNotIn("access-secret", serialized)
        self.assertNotIn("body-secret", serialized)
        self.assertNotIn("bearer-secret", serialized)
        self.assertNotIn("command-secret", serialized)
        self.assertEqual(
            payload["error"]["details"]["comparison_token"],
            "comparison-token-is-not-a-credential",
        )
        self.assertEqual(payload["error"]["details"]["total_tokens"], 17)


if __name__ == "__main__":
    unittest.main()
