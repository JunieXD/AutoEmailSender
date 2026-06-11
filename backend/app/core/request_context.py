from __future__ import annotations

import re
import uuid
from contextvars import ContextVar

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.backend_error_logging import write_backend_error_log


REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_HEADER_BYTES = REQUEST_ID_HEADER.lower().encode("ascii")
_SAFE_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    return _request_id_var.get()


def _is_safe_request_id(value: str) -> bool:
    return bool(_SAFE_REQUEST_ID_PATTERN.fullmatch(value))


def _resolve_request_id(scope: Scope) -> str:
    for name, value in scope.get("headers", []):
        if name.lower() == _REQUEST_ID_HEADER_BYTES:
            try:
                request_id = value.decode("ascii")
            except UnicodeDecodeError:
                break
            if _is_safe_request_id(request_id):
                return request_id
            break
    return str(uuid.uuid4())


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _resolve_request_id(scope)
        token = _request_id_var.set(request_id)
        response_started = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                headers = list(message.get("headers", []))
                headers.append((_REQUEST_ID_HEADER_BYTES, request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception as exc:
            app = scope.get("app")
            if response_started or getattr(app, "debug", False):
                raise
            write_backend_error_log(
                request_id=request_id,
                method=str(scope.get("method", "")),
                path=str(scope.get("path", "")),
                exc=exc,
            )
            response = JSONResponse(
                {
                    "detail": str(exc).strip() or "Internal Server Error",
                    "request_id": request_id,
                },
                status_code=500,
                headers={REQUEST_ID_HEADER: request_id},
            )
            await response(scope, receive, send)
        finally:
            _request_id_var.reset(token)
