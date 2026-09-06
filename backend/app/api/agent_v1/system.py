from __future__ import annotations

import os
from typing import Literal

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_revisions import ensure_revision
from app.core.agent_runtime_descriptor import (
    RUNTIME_PROTOCOL_VERSION,
    get_desktop_pid,
    get_runtime_id,
)
from app.core.database import get_async_session
from app.modules.system.public import (
    RuntimeSettingsRead,
    RuntimeSettingsUpdate,
    get_runtime_settings,
    serialize_runtime_settings,
    update_runtime_settings,
)
from app.schemas.agent import AgentInfoRead, AgentRuntimeInfoRead
from app.services.agent_mutations import execute_agent_mutation

router = APIRouter()


@router.get("/info", response_model=AgentInfoRead)
async def read_agent_api_info() -> AgentInfoRead:
    return AgentInfoRead(
        app_version=os.getenv("AUTO_EMAIL_SENDER_APP_VERSION", "development"),
    )


@router.get("/runtime", response_model=AgentRuntimeInfoRead)
async def read_agent_runtime(request: Request) -> AgentRuntimeInfoRead:
    runtime_error = getattr(request.app.state, "runtime_error", None)
    runtime_ready = bool(getattr(request.app.state, "runtime_ready", False))
    state: Literal["starting", "ready", "error"] = (
        "error" if runtime_error else ("ready" if runtime_ready else "starting")
    )
    return AgentRuntimeInfoRead(
        runtime_id=get_runtime_id(),
        protocol_version=RUNTIME_PROTOCOL_VERSION,
        app_version=os.getenv("AUTO_EMAIL_SENDER_APP_VERSION", "development"),
        backend_pid=os.getpid(),
        desktop_pid=get_desktop_pid(),
        state=state,
    )


@router.get("/settings", response_model=RuntimeSettingsRead)
async def read_agent_runtime_settings(
    session: AsyncSession = Depends(get_async_session),
) -> RuntimeSettingsRead:
    settings = await get_runtime_settings(session)
    await session.commit()
    return serialize_runtime_settings(settings)


@router.patch("/settings", response_model=RuntimeSettingsRead)
async def update_agent_runtime_settings(
    payload: RuntimeSettingsUpdate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
    session: AsyncSession = Depends(get_async_session),
) -> RuntimeSettingsRead:
    return await execute_agent_mutation(
        session,
        command="settings.update",
        request_data={
            "if_revision": if_revision,
            **payload.model_dump(mode="json"),
        },
        idempotency_key=idempotency_key,
        response_type=RuntimeSettingsRead,
        mutation=lambda: _update_agent_runtime_settings_with_revision(
            session,
            payload,
            if_revision=if_revision,
        ),
    )


async def _update_agent_runtime_settings(
    session: AsyncSession,
    payload: RuntimeSettingsUpdate,
) -> RuntimeSettingsRead:
    settings = await update_runtime_settings(
        session,
        payload,
        event_name="agent_cli.runtime_settings.updated",
        actor="agent_cli",
    )
    return serialize_runtime_settings(settings)


async def _update_agent_runtime_settings_with_revision(
    session: AsyncSession,
    payload: RuntimeSettingsUpdate,
    *,
    if_revision: str | None,
) -> RuntimeSettingsRead:
    if if_revision:
        settings = await get_runtime_settings(session)
        current = serialize_runtime_settings(settings)
        ensure_revision(
            if_revision,
            current.revision,
            resource="settings",
            resource_id="1",
            latest=current.model_dump(mode="json"),
        )
    return await _update_agent_runtime_settings(session, payload)
