from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response


STARTUP_OBSERVABILITY_PATHS = frozenset(
    {
        "/health",
        "/ready",
        "/startup-status",
    }
)


class RuntimeReadinessMiddleware(BaseHTTPMiddleware):
    """Prevent business requests from racing API cold-start recovery.

    The desktop API starts listening before migrations and recovery finish so
    Electron can display ``/startup-status``.  All other routes must remain
    unavailable until that initialization has completed.  Applications used
    without running their ASGI lifespan (some narrow unit tests) are left
    untouched; a real Uvicorn process always initializes ``runtime_ready``.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if (
            request.method == "OPTIONS"
            or request.url.path in STARTUP_OBSERVABILITY_PATHS
        ):
            return await call_next(request)

        runtime_ready = getattr(request.app.state, "runtime_ready", None)
        if runtime_ready is None or runtime_ready is True:
            return await call_next(request)

        runtime_error = getattr(request.app.state, "runtime_error", None)
        if runtime_error:
            detail = (
                getattr(request.app.state, "runtime_error_detail", None)
                or runtime_error
            )
            return JSONResponse(status_code=500, content={"detail": detail})

        return JSONResponse(
            status_code=503,
            content={"detail": "后端初始化中"},
            headers={"Retry-After": "1"},
        )


__all__ = ["RuntimeReadinessMiddleware", "STARTUP_OBSERVABILITY_PATHS"]
