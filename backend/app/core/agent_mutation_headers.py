from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.core.request_context import get_request_id
from app.services.agent_mutations import (
    clear_mutation_receipt_context,
    get_mutation_receipt_context,
    install_mutation_receipt_context_box,
)


class AgentMutationHeadersMiddleware:
    """Expose additive receipt metadata without changing existing DTO shapes."""

    def __init__(self, app: Callable[..., Awaitable[Any]]) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        clear_mutation_receipt_context()
        # Install a mutable request-local box so receipt updates made in a
        # downstream task are visible when the response-start message is sent.
        install_mutation_receipt_context_box()

        async def send_with_headers(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                receipt = get_mutation_receipt_context()
                if receipt and str(scope.get("path", "")).startswith("/api/agent/v1/"):
                    headers = list(message.get("headers", []))
                    headers.extend(
                        [
                            (b"x-agent-mutation-receipt", receipt["id"].encode("utf-8")),
                            (b"x-agent-mutation-status", receipt["status"].encode("utf-8")),
                            (b"x-agent-mutation-command", receipt["command"].encode("utf-8")),
                        ],
                    )
                    request_id = get_request_id()
                    if request_id:
                        headers.append((b"x-audit-reference", request_id.encode("utf-8")))
                    message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        finally:
            clear_mutation_receipt_context()
