from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Literal, TypeVar

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.agent_api_errors import AgentApiError
from app.core.database import get_async_session, get_session_factory
from app.models import (
    EmailLog,
    EmailTask,
    IdentityMaterial,
    IdentityProfile,
    LLMProfile,
    OutreachTemplate,
    Professor,
    ProfessorTag,
)
from app.schemas.agent import (
    AgentActionPlanRead,
    AgentCommunicationThreadDetailRead,
    AgentCommunicationThreadRead,
    AgentDraftRead,
    AgentDraftGenerateRequest,
    AgentDraftRegenerateRequest,
    AgentDraftSaveRequest,
    AgentIdentityRead,
    AgentInfoRead,
    AgentLLMProfileRead,
    AgentMaterialRead,
    AgentMessageRead,
    AgentPage,
    AgentPlanExecuteRequest,
    AgentPrepareSendRequest,
    AgentProfessorRead,
    AgentProfessorTagRead,
    AgentTemplateRead,
)
from app.services.agent_action_plans import (
    cancel_email_action_plan,
    create_email_action_plan,
    execute_email_action_plan,
    get_email_action_plan,
)
from app.services.agent_drafts import (
    generate_agent_draft,
    regenerate_agent_draft,
    save_agent_draft,
)


router = APIRouter(prefix="/api/agent/v1", tags=["agent-v1"])
PageItem = TypeVar("PageItem")


@router.get("/info", response_model=AgentInfoRead)
async def read_agent_api_info() -> AgentInfoRead:
    return AgentInfoRead(
        app_version=os.getenv("AUTO_EMAIL_SENDER_APP_VERSION", "development"),
    )


@router.get("/professors", response_model=AgentPage[AgentProfessorRead])
async def list_agent_professors(
    q: str | None = Query(default=None),
    archived: Literal["active", "archived", "all"] = Query(default="active"),
    tag_id: int | None = Query(default=None, ge=1),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentProfessorRead]:
    statement = select(Professor).options(selectinload(Professor.tags))
    if archived == "active":
        statement = statement.where(Professor.archived_at.is_(None))
    elif archived == "archived":
        statement = statement.where(Professor.archived_at.is_not(None))
    if tag_id is not None:
        statement = statement.where(Professor.tags.any(ProfessorTag.id == tag_id))
    normalized_query = (q or "").strip()
    if normalized_query:
        search = f"%{normalized_query}%"
        statement = statement.where(
            or_(
                Professor.name.ilike(search),
                Professor.email.ilike(search),
                Professor.university.ilike(search),
                Professor.school.ilike(search),
                Professor.department.ilike(search),
                Professor.research_direction.ilike(search),
                Professor.personal_note.ilike(search),
            ),
        )
    professors = list(
        (
            await session.scalars(
                statement.order_by(Professor.id.asc()).offset(cursor).limit(limit + 1),
            )
        ).unique(),
    )
    page, next_cursor, has_more = _slice_page(professors, cursor=cursor, limit=limit)
    return AgentPage(
        items=[_serialize_professor(professor) for professor in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get("/professors/{professor_id}", response_model=AgentProfessorRead)
async def read_agent_professor(
    professor_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> AgentProfessorRead:
    professor = await session.scalar(
        select(Professor)
        .options(selectinload(Professor.tags))
        .where(Professor.id == professor_id),
    )
    if professor is None:
        raise HTTPException(status_code=404, detail="未找到导师")
    return _serialize_professor(professor)


@router.get("/professor-tags", response_model=AgentPage[AgentProfessorTagRead])
async def list_agent_professor_tags(
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentProfessorTagRead]:
    tags = list(
        await session.scalars(
            select(ProfessorTag)
            .order_by(ProfessorTag.id.asc())
            .offset(cursor)
            .limit(limit + 1),
        ),
    )
    page, next_cursor, has_more = _slice_page(tags, cursor=cursor, limit=limit)
    return AgentPage(
        items=[_serialize_tag(tag) for tag in page],
        next_cursor=next_cursor,
        has_more=has_more,
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
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentCommunicationThreadRead]:
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
    return AgentPage(
        items=[_serialize_thread_row(row) for row in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )


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
        messages=[_serialize_message(message, include_body=include_body) for message in message_page],
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
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentMessageRead]:
    if thread_id is not None:
        thread_identity_id, thread_professor_id = _parse_thread_id(thread_id)
        if identity_id is not None and identity_id != thread_identity_id:
            raise HTTPException(status_code=400, detail="thread_id 与 identity_id 不一致")
        if professor_id is not None and professor_id != thread_professor_id:
            raise HTTPException(status_code=400, detail="thread_id 与 professor_id 不一致")
        identity_id = thread_identity_id
        professor_id = thread_professor_id

    statement = select(EmailLog)
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
    return AgentPage(
        items=[_serialize_message(message, include_body=include_body) for message in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get("/communications/messages/{message_id}", response_model=AgentMessageRead)
async def read_agent_message(
    message_id: int,
    include_body: bool = Query(default=True),
    session: AsyncSession = Depends(get_async_session),
) -> AgentMessageRead:
    message = await session.get(EmailLog, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="未找到邮件记录")
    return _serialize_message(message, include_body=include_body)


@router.get("/templates", response_model=AgentPage[AgentTemplateRead])
async def list_agent_templates(
    include_archived: bool = Query(default=False),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentTemplateRead]:
    statement = select(OutreachTemplate)
    if not include_archived:
        statement = statement.where(OutreachTemplate.archived_at.is_(None))
    templates = list(
        await session.scalars(
            statement.order_by(
                OutreachTemplate.is_default.desc(),
                OutreachTemplate.updated_at.desc(),
                OutreachTemplate.id.desc(),
            )
            .offset(cursor)
            .limit(limit + 1),
        ),
    )
    page, next_cursor, has_more = _slice_page(templates, cursor=cursor, limit=limit)
    return AgentPage(
        items=[_serialize_template(template) for template in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get("/templates/{template_id}", response_model=AgentTemplateRead)
async def read_agent_template(
    template_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> AgentTemplateRead:
    template = await session.get(OutreachTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="未找到邮件模板")
    return _serialize_template(template)


@router.get("/materials", response_model=AgentPage[AgentMaterialRead])
async def list_agent_materials(
    identity_id: int | None = Query(default=None, ge=1),
    material_type: str | None = Query(default=None),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentMaterialRead]:
    statement = select(IdentityMaterial).options(selectinload(IdentityMaterial.identity))
    if identity_id is not None:
        statement = statement.where(IdentityMaterial.identity_id == identity_id)
    if material_type:
        statement = statement.where(IdentityMaterial.material_type == material_type.strip().lower())
    materials = list(
        (
            await session.scalars(
                statement.order_by(IdentityMaterial.id.asc()).offset(cursor).limit(limit + 1),
            )
        ).unique(),
    )
    page, next_cursor, has_more = _slice_page(materials, cursor=cursor, limit=limit)
    return AgentPage(
        items=[_serialize_material(material, include_text=False) for material in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get("/materials/{material_id}", response_model=AgentMaterialRead)
async def read_agent_material(
    material_id: int,
    include_text: bool = Query(default=False),
    session: AsyncSession = Depends(get_async_session),
) -> AgentMaterialRead:
    material = await session.scalar(
        select(IdentityMaterial)
        .options(selectinload(IdentityMaterial.identity))
        .where(IdentityMaterial.id == material_id),
    )
    if material is None:
        raise HTTPException(status_code=404, detail="未找到材料")
    return _serialize_material(material, include_text=include_text)


@router.get("/identities", response_model=AgentPage[AgentIdentityRead])
async def list_agent_identities(
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentIdentityRead]:
    identities = list(
        await session.scalars(
            select(IdentityProfile)
            .order_by(IdentityProfile.is_default.desc(), IdentityProfile.id.asc())
            .offset(cursor)
            .limit(limit + 1),
        ),
    )
    page, next_cursor, has_more = _slice_page(identities, cursor=cursor, limit=limit)
    return AgentPage(
        items=[_serialize_identity(identity) for identity in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get("/identities/{identity_id}", response_model=AgentIdentityRead)
async def read_agent_identity(
    identity_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> AgentIdentityRead:
    identity = await session.get(IdentityProfile, identity_id)
    if identity is None:
        raise HTTPException(status_code=404, detail="未找到身份配置")
    return _serialize_identity(identity)


@router.get("/llm-profiles", response_model=AgentPage[AgentLLMProfileRead])
async def list_agent_llm_profiles(
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentLLMProfileRead]:
    profiles = list(
        await session.scalars(
            select(LLMProfile)
            .order_by(LLMProfile.is_default.desc(), LLMProfile.id.asc())
            .offset(cursor)
            .limit(limit + 1),
        ),
    )
    page, next_cursor, has_more = _slice_page(profiles, cursor=cursor, limit=limit)
    return AgentPage(
        items=[_serialize_llm_profile(profile) for profile in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get("/llm-profiles/{profile_id}", response_model=AgentLLMProfileRead)
async def read_agent_llm_profile(
    profile_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> AgentLLMProfileRead:
    profile = await session.get(LLMProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="未找到 LLM 配置")
    return _serialize_llm_profile(profile)


@router.get("/drafts/{task_id}", response_model=AgentDraftRead)
async def read_agent_draft(
    task_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> AgentDraftRead:
    task = await session.scalar(
        select(EmailTask)
        .options(selectinload(EmailTask.professor))
        .where(EmailTask.id == task_id),
    )
    if task is None:
        raise HTTPException(status_code=404, detail="未找到邮件任务")
    return _serialize_draft(task)


@router.post(
    "/drafts",
    response_model=AgentDraftRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_draft(payload: AgentDraftGenerateRequest) -> AgentDraftRead:
    try:
        task = await generate_agent_draft(get_session_factory(), payload)
    except HTTPException as exc:
        raise AgentApiError(
            status_code=exc.status_code,
            code="DRAFT_OPERATION_REJECTED",
            message=str(exc.detail),
        ) from exc
    except ValueError as exc:
        raise AgentApiError(
            status_code=409,
            code="DRAFT_OPERATION_REJECTED",
            message=str(exc),
        ) from exc
    return _serialize_draft(task)


@router.put("/drafts/{task_id}", response_model=AgentDraftRead)
async def save_agent_draft_content(
    task_id: int,
    payload: AgentDraftSaveRequest,
) -> AgentDraftRead:
    try:
        task = await save_agent_draft(get_session_factory(), task_id, payload)
    except ValueError as exc:
        raise AgentApiError(
            status_code=409,
            code="DRAFT_OPERATION_REJECTED",
            message=str(exc),
        ) from exc
    return _serialize_draft(task)


@router.post("/drafts/{task_id}/regenerate", response_model=AgentDraftRead)
async def regenerate_agent_draft_content(
    task_id: int,
    payload: AgentDraftRegenerateRequest,
) -> AgentDraftRead:
    try:
        task = await regenerate_agent_draft(get_session_factory(), task_id, payload)
    except ValueError as exc:
        raise AgentApiError(
            status_code=409,
            code="DRAFT_OPERATION_REJECTED",
            message=str(exc),
        ) from exc
    return _serialize_draft(task)


@router.post(
    "/drafts/{task_id}/prepare-send",
    response_model=AgentActionPlanRead,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_agent_draft_send(
    task_id: int,
    payload: AgentPrepareSendRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentActionPlanRead:
    try:
        return await create_email_action_plan(
            get_session_factory(),
            task_id,
            payload,
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        raise AgentApiError(
            status_code=409,
            code="DRAFT_NOT_SENDABLE",
            message=str(exc),
            suggested_command=f"auto-email-sender drafts get {task_id}",
        ) from exc


@router.get("/plans/{plan_id}", response_model=AgentActionPlanRead)
async def read_agent_action_plan(plan_id: str) -> AgentActionPlanRead:
    return await get_email_action_plan(get_session_factory(), plan_id)


@router.post("/plans/{plan_id}/execute", response_model=AgentActionPlanRead)
async def execute_agent_action_plan(
    plan_id: str,
    payload: AgentPlanExecuteRequest,
) -> AgentActionPlanRead:
    return await execute_email_action_plan(get_session_factory(), plan_id, payload)


@router.post("/plans/{plan_id}/cancel", response_model=AgentActionPlanRead)
async def cancel_agent_action_plan(plan_id: str) -> AgentActionPlanRead:
    return await cancel_email_action_plan(get_session_factory(), plan_id)


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
        .where(EmailLog.direction.in_(["sent", "received"]))
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
        statement = statement.having(received_count > 0 if replied else received_count == 0)
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


def _slice_page(
    items: Sequence[PageItem],
    *,
    cursor: int,
    limit: int,
) -> tuple[Sequence[PageItem], str | None, bool]:
    has_more = len(items) > limit
    page = items[:limit]
    next_cursor = str(cursor + len(page)) if has_more else None
    return page, next_cursor, has_more


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


def _serialize_tag(tag: ProfessorTag) -> AgentProfessorTagRead:
    return AgentProfessorTagRead(
        id=tag.id,
        name=tag.name,
        text_color=tag.text_color,
        background_color=tag.background_color,
    )


def _serialize_professor(professor: Professor) -> AgentProfessorRead:
    return AgentProfessorRead(
        id=professor.id,
        name=professor.name,
        email=professor.email,
        title=professor.title,
        university=professor.university,
        school=professor.school,
        department=professor.department,
        research_direction=professor.research_direction,
        recent_papers=professor.recent_papers or [],
        profile_url=professor.profile_url,
        source_url=professor.source_url,
        crawl_status=professor.crawl_status,
        skip_reason=professor.skip_reason,
        personal_note=professor.personal_note,
        archived_at=professor.archived_at,
        created_at=professor.created_at,
        updated_at=professor.updated_at,
        tags=[_serialize_tag(tag) for tag in professor.tags],
    )


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


def _serialize_identity(identity: IdentityProfile) -> AgentIdentityRead:
    return AgentIdentityRead(
        id=identity.id,
        name=identity.name,
        profile_name=identity.profile_name,
        sender_name=identity.sender_name,
        email_address=identity.email_address,
        default_language=identity.default_language,
        outreach_generation_mode=identity.outreach_generation_mode,
        default_outreach_template_id=identity.default_outreach_template_id,
        current_primary_material_id=identity.current_primary_material_id,
        communication_group_id=identity.communication_group_id,
        match_threshold=identity.match_threshold,
        daily_send_limit=identity.daily_send_limit,
        send_interval_min=identity.send_interval_min,
        send_interval_max=identity.send_interval_max,
        same_domain_cooldown_minutes=identity.same_domain_cooldown_minutes,
        smtp_configured=bool(
            identity.smtp_host and identity.smtp_username and identity.smtp_password
        ),
        imap_configured=bool(
            identity.imap_host and identity.imap_username and identity.imap_password
        ),
        is_default=identity.is_default,
        created_at=identity.created_at,
        updated_at=identity.updated_at,
    )


def _serialize_llm_profile(profile: LLMProfile) -> AgentLLMProfileRead:
    return AgentLLMProfileRead(
        id=profile.id,
        name=profile.name,
        provider=profile.provider,
        model_name=profile.model_name,
        temperature=profile.temperature,
        max_tokens=profile.max_tokens,
        credential_configured=bool(profile.api_key),
        is_default=profile.is_default,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def _serialize_material(
    material: IdentityMaterial,
    *,
    include_text: bool,
) -> AgentMaterialRead:
    return AgentMaterialRead(
        id=material.id,
        identity_id=material.identity_id,
        display_name=material.display_name,
        original_filename=material.original_filename,
        mime_type=material.mime_type,
        size_bytes=material.size_bytes,
        material_type=material.material_type,
        is_primary=material.identity.current_primary_material_id == material.id,
        has_extracted_text=bool(material.extracted_text),
        extracted_text=material.extracted_text if include_text else None,
        created_at=material.created_at,
    )


def _serialize_template(template: OutreachTemplate) -> AgentTemplateRead:
    return AgentTemplateRead(
        id=template.id,
        name=template.name,
        recommended_generation_mode=template.recommended_generation_mode,
        subject=template.subject,
        body_text=template.body_text,
        body_html=template.body_html,
        is_default=template.is_default,
        archived_at=template.archived_at,
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


def _serialize_draft(task: EmailTask) -> AgentDraftRead:
    raw_mode = (task.outreach_generation_mode or "llm").lower()
    generation_mode: Literal["template", "ai_rewrite", "manual"]
    if raw_mode == "template":
        generation_mode = "template"
    elif raw_mode == "manual":
        generation_mode = "manual"
    else:
        generation_mode = "ai_rewrite"
    return AgentDraftRead(
        task_id=task.id,
        source=task.source,
        batch_task_id=task.batch_task_id,
        parent_task_id=task.parent_task_id,
        identity_id=task.identity_id,
        professor_id=task.professor_id,
        professor_name=task.professor.name,
        professor_email=task.professor.email,
        llm_profile_id=task.llm_profile_id,
        status=task.status,
        generation_mode=generation_mode,
        template_id=task.outreach_template_id,
        reference_material_id=task.primary_material_id,
        attachment_material_ids=task.selected_material_ids or [],
        generated_subject=task.generated_subject,
        generated_body_text=task.generated_content_text,
        generated_body_html=task.generated_content_html,
        approved_subject=task.approved_subject,
        approved_body_text=task.approved_body_text,
        approved_body_html=task.approved_body_html,
        approved_at=task.approved_at,
        scheduled_at=task.scheduled_at,
        sent_at=task.sent_at,
        last_error=task.last_error,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )
