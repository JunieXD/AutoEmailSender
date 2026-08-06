from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.workspace_support import build_workspace_thread, ensure_workspace_task
from app.core.database import get_async_session, get_session_factory
from app.models import Professor
from app.schemas.workspace import WorkspaceSyncWarningRead, WorkspaceThreadRead
from app.modules.identities.public import resolve_identity_communication_scope
from app.services.operation_logs import sanitize_user_visible_error
from app.services.task_runtime import sync_workspace_professor_replies


router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


@router.get("/{professor_id}", response_model=WorkspaceThreadRead)
async def get_workspace_thread(
    professor_id: int,
    identity_id: int = Query(...),
    llm_profile_id: int = Query(...),
    session: AsyncSession = Depends(get_async_session),
) -> WorkspaceThreadRead:
    return await build_workspace_thread(
        session,
        professor_id=professor_id,
        identity_id=identity_id,
        llm_profile_id=llm_profile_id,
    )


@router.post("/{professor_id}/ensure-task", response_model=WorkspaceThreadRead)
async def ensure_personal_workspace_task(
    professor_id: int,
    identity_id: int = Query(...),
    llm_profile_id: int = Query(...),
    session: AsyncSession = Depends(get_async_session),
) -> WorkspaceThreadRead:
    await ensure_workspace_task(
        session,
        professor_id=professor_id,
        identity_id=identity_id,
        llm_profile_id=llm_profile_id,
    )
    return await build_workspace_thread(
        session,
        professor_id=professor_id,
        identity_id=identity_id,
        llm_profile_id=llm_profile_id,
    )

@router.post("/{professor_id}/refresh-replies", response_model=WorkspaceThreadRead)
async def refresh_workspace_replies(
    professor_id: int,
    identity_id: int = Query(...),
    llm_profile_id: int = Query(...),
    session: AsyncSession = Depends(get_async_session),
) -> WorkspaceThreadRead:
    communication_scope = await resolve_identity_communication_scope(
        session,
        active_identity_id=identity_id,
    )
    sync_identities = [
        identity
        for identity in communication_scope.identities
        if _identity_has_imap_config(identity)
    ]
    results = await asyncio.gather(
        *[
            sync_workspace_professor_replies(
                get_session_factory(),
                identity.id,
                professor_id,
            )
            for identity in sync_identities
        ],
        return_exceptions=True,
    )
    sync_warnings = [
        WorkspaceSyncWarningRead(
            identity_id=identity.id,
            identity_name=identity.profile_name or identity.name,
            message=sanitize_user_visible_error(result),
        )
        for identity, result in zip(sync_identities, results, strict=True)
        if isinstance(result, BaseException)
    ]
    return await build_workspace_thread(
        session,
        professor_id=professor_id,
        identity_id=identity_id,
        llm_profile_id=llm_profile_id,
        sync_warnings=sync_warnings,
    )


def _identity_has_imap_config(identity) -> bool:
    return bool(
        identity.imap_host
        and str(identity.imap_host).strip()
        and identity.imap_port
        and identity.imap_username
        and str(identity.imap_username).strip()
        and identity.imap_password
        and str(identity.imap_password).strip()
    )
