from __future__ import annotations

import re
import unittest

from fastapi.testclient import TestClient

from app.core.request_context import get_request_id
from main import create_app


UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
)


class RequestContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app()

        @self.app.get("/test/request-id")
        async def read_request_id() -> dict[str, str | None]:
            return {"request_id": get_request_id()}

        @self.app.get("/test/request-id-error")
        async def raise_request_error() -> None:
            raise RuntimeError("boom")

        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()

    def test_missing_request_id_header_generates_uuid_response_header(self) -> None:
        response = self.client.get("/api/ping")

        self.assertEqual(response.status_code, 200)
        self.assertRegex(response.headers["X-Request-ID"], UUID_PATTERN)

    def test_valid_request_id_header_is_reused(self) -> None:
        request_id = "frontend.trace_123-abc"

        response = self.client.get("/api/ping", headers={"X-Request-ID": request_id})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Request-ID"], request_id)

    def test_invalid_request_id_header_is_replaced(self) -> None:
        invalid_request_id = "bad/request id"

        response = self.client.get("/api/ping", headers={"X-Request-ID": invalid_request_id})

        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.headers["X-Request-ID"], invalid_request_id)
        self.assertRegex(response.headers["X-Request-ID"], UUID_PATTERN)

    def test_too_long_request_id_header_is_replaced(self) -> None:
        too_long_request_id = "a" * 129

        response = self.client.get("/api/ping", headers={"X-Request-ID": too_long_request_id})

        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.headers["X-Request-ID"], too_long_request_id)
        self.assertRegex(response.headers["X-Request-ID"], UUID_PATTERN)

    def test_request_id_is_available_in_endpoint_and_reset_after_request(self) -> None:
        request_id = "service.context-1"

        response = self.client.get("/test/request-id", headers={"X-Request-ID": request_id})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"request_id": request_id})
        self.assertEqual(response.headers["X-Request-ID"], request_id)
        self.assertIsNone(get_request_id())

    def test_unhandled_exception_response_keeps_request_id_and_resets_context(self) -> None:
        request_id = "service.error-1"
        client = TestClient(self.app, raise_server_exceptions=False)
        try:
            response = client.get("/test/request-id-error", headers={"X-Request-ID": request_id})
        finally:
            client.close()

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.headers["X-Request-ID"], request_id)
        self.assertEqual(response.json(), {"detail": "boom", "request_id": request_id})
        self.assertIsNone(get_request_id())

    def test_unhandled_exception_is_written_to_backend_error_log(self) -> None:
        from app.core.config import get_settings

        request_id = "service.error-log-1"
        error_log = get_settings().data_dir / "logs" / "backend-errors.log"
        if error_log.exists():
            error_log.unlink()
        client = TestClient(self.app, raise_server_exceptions=False)
        try:
            response = client.get("/test/request-id-error", headers={"X-Request-ID": request_id})
        finally:
            client.close()

        self.assertEqual(response.status_code, 500)
        content = error_log.read_text(encoding="utf-8")
        self.assertIn("GET /test/request-id-error", content)
        self.assertIn(request_id, content)
        self.assertIn("RuntimeError: boom", content)
        self.assertIn("Traceback", content)

    def test_unhandled_exception_returns_json_and_resets_context(self) -> None:
        response = self.client.get("/test/request-id-error", headers={"X-Request-ID": "service.error-2"})

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "boom", "request_id": "service.error-2"})
        self.assertIsNone(get_request_id())


if __name__ == "__main__":
    unittest.main()
