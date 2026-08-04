from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
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


async def request_validation_error_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Keep Agent validation failures from reflecting submitted credentials."""

    if request.url.path.startswith("/api/agent/v1/"):
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "INVALID_AGENT_REQUEST",
                    "message": "请求参数无效；请查看命令帮助后重试。",
                    "retryable": False,
                    "details": {"validation_error_count": len(exc.errors())},
                },
            },
        )
    return JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(exc.errors())},
    )
