from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import Request
from fastapi.responses import JSONResponse


@dataclass(slots=True)
class AgentApiError(Exception):
    status_code: int
    code: str
    message: str
    retryable: bool = False
    details: dict[str, object] = field(default_factory=dict)
    suggested_command: str | None = None

    def __str__(self) -> str:
        return self.message


async def agent_api_error_handler(
    request: Request,
    exc: AgentApiError,
) -> JSONResponse:
    _ = request
    error: dict[str, object] = {
        "code": exc.code,
        "message": exc.message,
        "retryable": exc.retryable,
        "details": exc.details,
    }
    if exc.suggested_command:
        error["suggested_action"] = {"command": exc.suggested_command}
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": error},
    )
