from __future__ import annotations

import secrets
from dataclasses import dataclass

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response


PUBLIC_PATHS = frozenset({"/health", "/ready", "/startup-status"})
AGENT_API_PREFIX = "/api/agent/v1"


@dataclass(frozen=True, slots=True)
class ApiAccessTokens:
    ui_token: str | None
    agent_token: str | None

    @property
    def authentication_enabled(self) -> bool:
        return self.ui_token is not None or self.agent_token is not None


class ApiAuthMiddleware(BaseHTTPMiddleware):
    """Keep desktop-renderer and Agent API credentials in separate scopes."""

    def __init__(
        self,
        app: object,
        *,
        ui_token: str | None,
        agent_token: str | None,
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.tokens = ApiAccessTokens(
            ui_token=_normalize_token(ui_token),
            agent_token=_normalize_token(agent_token),
        )

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if (
            request.method == "OPTIONS"
            or request.url.path in PUBLIC_PATHS
            or not request.url.path.startswith("/api")
            or not self.tokens.authentication_enabled
        ):
            return await call_next(request)

        supplied_token = _read_bearer_token(request)
        expects_agent_token = _is_agent_path(request.url.path)
        expected_token = (
            self.tokens.agent_token if expects_agent_token else self.tokens.ui_token
        )
        other_scope_token = (
            self.tokens.ui_token if expects_agent_token else self.tokens.agent_token
        )

        if supplied_token is None:
            return _auth_error(
                status_code=401,
                code="AUTH_REQUIRED",
                message="此本地接口需要访问令牌。",
            )
        if other_scope_token is not None and _tokens_equal(
            supplied_token, other_scope_token
        ):
            return _auth_error(
                status_code=403,
                code="TOKEN_SCOPE_FORBIDDEN",
                message="此访问令牌不能用于当前接口。",
            )
        if expected_token is None or not _tokens_equal(supplied_token, expected_token):
            return _auth_error(
                status_code=401,
                code="INVALID_ACCESS_TOKEN",
                message="本地访问令牌无效或已过期。",
            )
        return await call_next(request)


def _normalize_token(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _read_bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("Authorization")
    if not authorization:
        return None
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _is_agent_path(path: str) -> bool:
    return path == AGENT_API_PREFIX or path.startswith(f"{AGENT_API_PREFIX}/")


def _tokens_equal(left: str, right: str) -> bool:
    return secrets.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _auth_error(*, status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "retryable": False,
                "details": {},
            },
        },
        headers={"WWW-Authenticate": "Bearer"} if status_code == 401 else None,
    )
