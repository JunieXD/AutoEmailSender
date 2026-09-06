from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_api_errors import AgentApiError
from app.core.agent_revisions import ensure_revision
from app.core.database import get_async_session
from app.modules.identities.public import (
    CommunicationGroupMutationError,
    IdentityCommunicationGroupRead,
    IdentityCommunicationGroupWrite,
    create_communication_group_record,
    delete_communication_group_record,
    get_communication_group_record,
    list_communication_group_records,
    update_communication_group_record,
)
from app.schemas.agent import AgentCommunicationGroupDeleteRead, AgentPage
from app.services.agent_mutations import execute_agent_mutation

from .support import (
    _project_agent_collection_response,
    _slice_page,
)

router = APIRouter()


@router.get(
    "/communication-groups",
    response_model=AgentPage[IdentityCommunicationGroupRead],
)
async def list_agent_communication_groups(
    group_id: int | None = Query(default=None, ge=1),
    match_source_identity_id: int | None = Query(default=None, ge=1),
    fields: str | None = Query(default=None, max_length=4_000),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[IdentityCommunicationGroupRead] | Response:
    groups = await list_communication_group_records(session)
    if group_id is not None:
        groups = [group for group in groups if group.id == group_id]
    if match_source_identity_id is not None:
        groups = [
            group
            for group in groups
            if group.match_source_identity_id == match_source_identity_id
        ]
    page, next_cursor, has_more = _slice_page(
        groups[cursor:], cursor=cursor, limit=limit
    )
    response = AgentPage(items=list(page), next_cursor=next_cursor, has_more=has_more)
    return _project_agent_collection_response(response, fields)


@router.get(
    "/communication-groups/{group_id}",
    response_model=IdentityCommunicationGroupRead,
)
async def read_agent_communication_group(
    group_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> IdentityCommunicationGroupRead:
    try:
        return await get_communication_group_record(session, group_id)
    except CommunicationGroupMutationError as exc:
        raise _agent_communication_group_error(exc) from exc


@router.post(
    "/communication-groups",
    response_model=IdentityCommunicationGroupRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_communication_group(
    payload: IdentityCommunicationGroupWrite,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> IdentityCommunicationGroupRead:
    try:
        return await execute_agent_mutation(
            session,
            command="communication-groups.create",
            request_data=payload.model_dump(mode="json"),
            idempotency_key=idempotency_key,
            response_type=IdentityCommunicationGroupRead,
            mutation=lambda: _create_agent_communication_group(session, payload),
        )
    except CommunicationGroupMutationError as exc:
        raise _agent_communication_group_error(exc) from exc


@router.put(
    "/communication-groups/{group_id}",
    response_model=IdentityCommunicationGroupRead,
)
async def update_agent_communication_group(
    group_id: int,
    payload: IdentityCommunicationGroupWrite,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
    session: AsyncSession = Depends(get_async_session),
) -> IdentityCommunicationGroupRead:
    try:
        return await execute_agent_mutation(
            session,
            command="communication-groups.update",
            request_data={
                "group_id": group_id,
                "if_revision": if_revision,
                **payload.model_dump(mode="json"),
            },
            idempotency_key=idempotency_key,
            response_type=IdentityCommunicationGroupRead,
            mutation=lambda: _update_agent_communication_group_with_revision(
                session,
                group_id,
                payload,
                if_revision=if_revision,
            ),
        )
    except CommunicationGroupMutationError as exc:
        raise _agent_communication_group_error(exc) from exc


@router.post(
    "/communication-groups/{group_id}/delete",
    response_model=AgentCommunicationGroupDeleteRead,
)
async def delete_agent_communication_group(
    group_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentCommunicationGroupDeleteRead:
    try:
        return await execute_agent_mutation(
            session,
            command="communication-groups.delete",
            request_data={"group_id": group_id},
            idempotency_key=idempotency_key,
            response_type=AgentCommunicationGroupDeleteRead,
            mutation=lambda: _delete_agent_communication_group(session, group_id),
        )
    except CommunicationGroupMutationError as exc:
        raise _agent_communication_group_error(exc) from exc


async def _create_agent_communication_group(
    session: AsyncSession,
    payload: IdentityCommunicationGroupWrite,
) -> IdentityCommunicationGroupRead:
    group = await create_communication_group_record(
        session,
        payload,
        event_name="agent_cli.communication_group.created",
        actor="agent_cli",
    )
    return await get_communication_group_record(session, group.id)


async def _update_agent_communication_group(
    session: AsyncSession,
    group_id: int,
    payload: IdentityCommunicationGroupWrite,
) -> IdentityCommunicationGroupRead:
    group = await update_communication_group_record(
        session,
        group_id,
        payload,
        event_name="agent_cli.communication_group.updated",
        actor="agent_cli",
    )
    return await get_communication_group_record(session, group.id)


async def _update_agent_communication_group_with_revision(
    session: AsyncSession,
    group_id: int,
    payload: IdentityCommunicationGroupWrite,
    *,
    if_revision: str | None,
) -> IdentityCommunicationGroupRead:
    if if_revision:
        current = await get_communication_group_record(session, group_id)
        ensure_revision(
            if_revision,
            current.revision,
            resource="communication-groups",
            resource_id=group_id,
            latest=current.model_dump(mode="json"),
        )
    return await _update_agent_communication_group(session, group_id, payload)


async def _delete_agent_communication_group(
    session: AsyncSession,
    group_id: int,
) -> AgentCommunicationGroupDeleteRead:
    await delete_communication_group_record(
        session,
        group_id,
        event_name="agent_cli.communication_group.deleted",
        actor="agent_cli",
    )
    return AgentCommunicationGroupDeleteRead(ok=True, group_id=group_id)


def _agent_communication_group_error(
    error: CommunicationGroupMutationError,
) -> AgentApiError:
    return AgentApiError(
        status_code=error.status_code,
        code=error.code,
        message=error.message,
        details=error.details or {},
    )
