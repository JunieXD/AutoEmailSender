from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.agent_api_errors import AgentApiError
from app.core.database import get_async_session, get_session_factory
from app.core.time import utc_now
from app.models import EmailLog, EmailLogRecordState, IdentityProfile, Professor
from app.schemas.agent import (
    AgentCommunicationSyncRead,
    AgentCommunicationSyncRequest,
    AgentCommunicationThreadDetailRead,
    AgentCommunicationThreadRead,
    AgentMessageRead,
    AgentPage,
    AgentUiHandoffRead,
)
from app.services.agent_mutations import execute_agent_factory_mutation
from app.services.agent_ui_handoffs import create_communication_thread_ui_handoff
from app.services.operation_logs import (
    record_operation_log,
    sanitize_user_visible_error,
)

from .support import (
    _identity_has_imap_config,
    _project_agent_collection_response,
    _slice_page,
)

router = APIRouter()


async def sync_identity_history_poll_once(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
) -> int:
    from app.modules.communications.public import (
        sync_identity_history_poll_once as sync_once,
    )

    return await sync_once(session_factory, identity_id)


@router.post(
    "/communications/threads/{thread_id}/present",
    response_model=AgentUiHandoffRead,
    status_code=status.HTTP_201_CREATED,
)
async def present_agent_communication_thread(
    thread_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentUiHandoffRead:
    return await create_communication_thread_ui_handoff(
        get_session_factory(),
        thread_id,
        idempotency_key=idempotency_key,
    )


@router.get(
    "/communications/threads",
    response_model=AgentPage[AgentCommunicationThreadRead],
)
async def list_agent_communication_threads(
    identity_id: int | None = Query(default=None, ge=1),
    professor_id: int | None = Query(default=None, ge=1),
    sent: bool | None = Query(default=None),
    replied: bool | None = Query(default=None),
    fields: str | None = Query(default=None, max_length=4_000),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentCommunicationThreadRead] | Response:
    rows = await _query_threads(
        session,
        identity_id=identity_id,
        professor_id=professor_id,
        sent=sent,
        replied=replied,
        cursor=cursor,
        limit=limit,
    )
    page, next_cursor, has_more = _slice_page(rows, cursor=cursor, limit=limit)
    response = AgentPage(
        items=[_serialize_thread_row(row) for row in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )
    return _project_agent_collection_response(response, fields)


@router.get(
    "/communications/threads/{thread_id}",
    response_model=AgentCommunicationThreadDetailRead,
)
async def read_agent_communication_thread(
    thread_id: str,
    include_body: bool = Query(default=False),
    message_cursor: int = Query(default=0, ge=0),
    message_limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentCommunicationThreadDetailRead:
    identity_id, professor_id = _parse_thread_id(thread_id)
    rows = await _query_threads(
        session,
        identity_id=identity_id,
        professor_id=professor_id,
        sent=None,
        replied=None,
        cursor=0,
        limit=1,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="未找到通信线程")
    messages = list(
        await session.scalars(
            select(EmailLog)
            .where(
                EmailLog.identity_id == identity_id,
                EmailLog.professor_id == professor_id,
                EmailLog.direction.in_(["sent", "received"]),
                EmailLog.record_state == EmailLogRecordState.CANONICAL.value,
            )
            .order_by(EmailLog.created_at.asc(), EmailLog.id.asc())
            .offset(message_cursor)
            .limit(message_limit + 1),
        ),
    )
    message_page, next_cursor, has_more = _slice_page(
        messages,
        cursor=message_cursor,
        limit=message_limit,
    )
    thread = _serialize_thread_row(rows[0])
    return AgentCommunicationThreadDetailRead(
        **thread.model_dump(),
        messages=[
            _serialize_message(message, include_body=include_body)
            for message in message_page
        ],
        messages_next_cursor=next_cursor,
        messages_has_more=has_more,
    )


@router.get(
    "/communications/messages",
    response_model=AgentPage[AgentMessageRead],
)
async def list_agent_messages(
    thread_id: str | None = Query(default=None),
    identity_id: int | None = Query(default=None, ge=1),
    professor_id: int | None = Query(default=None, ge=1),
    direction: Literal["sent", "received", "draft"] | None = Query(default=None),
    include_body: bool = Query(default=False),
    order: Literal["asc", "desc"] = Query(default="desc"),
    fields: str | None = Query(default=None, max_length=4_000),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentMessageRead] | Response:
    if thread_id is not None:
        thread_identity_id, thread_professor_id = _parse_thread_id(thread_id)
        if identity_id is not None and identity_id != thread_identity_id:
            raise HTTPException(
                status_code=400, detail="thread_id 与 identity_id 不一致"
            )
        if professor_id is not None and professor_id != thread_professor_id:
            raise HTTPException(
                status_code=400, detail="thread_id 与 professor_id 不一致"
            )
        identity_id = thread_identity_id
        professor_id = thread_professor_id

    statement = select(EmailLog).where(
        EmailLog.record_state == EmailLogRecordState.CANONICAL.value,
    )
    if identity_id is not None:
        statement = statement.where(EmailLog.identity_id == identity_id)
    if professor_id is not None:
        statement = statement.where(EmailLog.professor_id == professor_id)
    if direction is not None:
        statement = statement.where(EmailLog.direction == direction)
    ordering = (
        (EmailLog.created_at.asc(), EmailLog.id.asc())
        if order == "asc"
        else (EmailLog.created_at.desc(), EmailLog.id.desc())
    )
    messages = list(
        await session.scalars(
            statement.order_by(*ordering).offset(cursor).limit(limit + 1),
        ),
    )
    page, next_cursor, has_more = _slice_page(messages, cursor=cursor, limit=limit)
    response = AgentPage(
        items=[
            _serialize_message(message, include_body=include_body) for message in page
        ],
        next_cursor=next_cursor,
        has_more=has_more,
    )
    return _project_agent_collection_response(response, fields)


@router.get("/communications/messages/{message_id}", response_model=AgentMessageRead)
async def read_agent_message(
    message_id: int,
    include_body: bool = Query(default=True),
    session: AsyncSession = Depends(get_async_session),
) -> AgentMessageRead:
    message = await session.get(EmailLog, message_id)
    if message is None or message.record_state != EmailLogRecordState.CANONICAL.value:
        raise HTTPException(status_code=404, detail="未找到邮件记录")
    return _serialize_message(message, include_body=include_body)


@router.post("/communications/sync", response_model=AgentCommunicationSyncRead)
async def sync_agent_communications(
    payload: AgentCommunicationSyncRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentCommunicationSyncRead:
    identity = await session.scalar(
        select(IdentityProfile).where(
            IdentityProfile.id == payload.identity_id,
            IdentityProfile.deleted_at.is_(None),
        )
    )
    if identity is None:
        raise HTTPException(status_code=404, detail="未找到身份配置")
    if not _identity_has_imap_config(identity):
        raise AgentApiError(
            status_code=409,
            code="IMAP_NOT_CONFIGURED",
            message="该发件身份尚未配置 IMAP，无法同步邮箱通信记录。",
        )

    async def mutation() -> AgentCommunicationSyncRead:
        detected_count = await sync_identity_history_poll_once(
            get_session_factory(),
            identity.id,
        )
        async with get_session_factory()() as mutation_session:
            await record_operation_log(
                mutation_session,
                category="agent_action",
                event_name="agent_cli.communication_synced",
                entity_type="identity_profile",
                entity_id=str(identity.id),
                metadata={
                    "actor": "agent_cli",
                    "identity_id": identity.id,
                    "detected_count": detected_count,
                },
            )
            await mutation_session.commit()
        return AgentCommunicationSyncRead(
            identity_id=identity.id,
            detected_count=detected_count,
            completed_at=utc_now(),
            message=f"已完成一次邮箱同步检查，新增 {detected_count} 条通信记录。",
        )

    try:
        return await execute_agent_factory_mutation(
            get_session_factory(),
            command="communications.sync",
            request_data=payload.model_dump(mode="json"),
            idempotency_key=idempotency_key,
            response_type=AgentCommunicationSyncRead,
            mutation=mutation,
            external_execution=True,
        )
    except AgentApiError:
        raise
    except Exception as exc:
        message = sanitize_user_visible_error(exc)
        await record_operation_log(
            session,
            category="agent_action",
            event_name="agent_cli.communication_sync_failed",
            level="warning",
            entity_type="identity_profile",
            entity_id=str(identity.id),
            metadata={"actor": "agent_cli", "identity_id": identity.id},
        )
        await session.commit()
        raise AgentApiError(
            status_code=502,
            code="MAILBOX_SYNC_FAILED",
            message=f"邮箱同步失败：{message}",
            retryable=True,
        ) from exc


async def _query_threads(
    session: AsyncSession,
    *,
    identity_id: int | None,
    professor_id: int | None,
    sent: bool | None,
    replied: bool | None,
    cursor: int,
    limit: int,
) -> Sequence[object]:
    sent_count = func.sum(case((EmailLog.direction == "sent", 1), else_=0))
    received_count = func.sum(case((EmailLog.direction == "received", 1), else_=0))
    last_message_at = func.max(EmailLog.created_at)
    statement = (
        select(
            EmailLog.identity_id.label("identity_id"),
            IdentityProfile.name.label("identity_name"),
            IdentityProfile.email_address.label("identity_email_address"),
            EmailLog.professor_id.label("professor_id"),
            Professor.name.label("professor_name"),
            Professor.email.label("professor_email"),
            sent_count.label("sent_count"),
            received_count.label("received_count"),
            last_message_at.label("last_message_at"),
        )
        .join(IdentityProfile, IdentityProfile.id == EmailLog.identity_id)
        .join(Professor, Professor.id == EmailLog.professor_id)
        .where(
            EmailLog.direction.in_(["sent", "received"]),
            EmailLog.record_state == EmailLogRecordState.CANONICAL.value,
        )
        .group_by(
            EmailLog.identity_id,
            IdentityProfile.name,
            IdentityProfile.email_address,
            EmailLog.professor_id,
            Professor.name,
            Professor.email,
        )
    )
    if identity_id is not None:
        statement = statement.where(EmailLog.identity_id == identity_id)
    if professor_id is not None:
        statement = statement.where(EmailLog.professor_id == professor_id)
    if sent is not None:
        statement = statement.having(sent_count > 0 if sent else sent_count == 0)
    if replied is not None:
        statement = statement.having(
            received_count > 0 if replied else received_count == 0
        )
    result = await session.execute(
        statement.order_by(
            last_message_at.desc(),
            EmailLog.identity_id.asc(),
            EmailLog.professor_id.asc(),
        )
        .offset(cursor)
        .limit(limit + 1),
    )
    return result.all()


def _parse_thread_id(thread_id: str) -> tuple[int, int]:
    identity_raw, separator, professor_raw = thread_id.partition(":")
    try:
        identity_id = int(identity_raw)
        professor_id = int(professor_raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="通信线程 ID 无效") from exc
    if not separator or identity_id < 1 or professor_id < 1:
        raise HTTPException(status_code=400, detail="通信线程 ID 无效")
    return identity_id, professor_id


def _serialize_thread_row(row: object) -> AgentCommunicationThreadRead:
    sent_count = int(getattr(row, "sent_count") or 0)
    received_count = int(getattr(row, "received_count") or 0)
    identity_id = int(getattr(row, "identity_id"))
    professor_id = int(getattr(row, "professor_id"))
    return AgentCommunicationThreadRead(
        id=f"{identity_id}:{professor_id}",
        identity_id=identity_id,
        identity_name=str(getattr(row, "identity_name")),
        identity_email_address=str(getattr(row, "identity_email_address")),
        professor_id=professor_id,
        professor_name=str(getattr(row, "professor_name")),
        professor_email=getattr(row, "professor_email"),
        sent_count=sent_count,
        received_count=received_count,
        has_sent=sent_count > 0,
        has_reply=received_count > 0,
        last_message_at=getattr(row, "last_message_at"),
    )


def _serialize_message(message: EmailLog, *, include_body: bool) -> AgentMessageRead:
    return AgentMessageRead(
        id=message.id,
        thread_id=f"{message.identity_id}:{message.professor_id}",
        email_task_id=message.email_task_id,
        identity_id=message.identity_id,
        professor_id=message.professor_id,
        direction=message.direction,  # type: ignore[arg-type]
        subject=message.subject,
        content=message.content if include_body else None,
        content_html=message.content_html if include_body else None,
        body_included=include_body,
        from_email=message.from_email,
        to_emails=message.to_emails or [],
        cc_emails=message.cc_emails or [],
        bcc_emails=message.bcc_emails or [],
        rfc_message_id=message.rfc_message_id,
        failure_summary=message.failure_summary,
        created_at=message.created_at,
    )
