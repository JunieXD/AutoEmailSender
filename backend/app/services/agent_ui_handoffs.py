from __future__ import annotations

import json
import secrets
from collections.abc import Sequence
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.agent_api_errors import AgentApiError
from app.core.query_chunks import chunked_values
from app.core.time import as_utc_aware, utc_now
from app.models import (
    AgentUiHandoff,
    AgentUiHandoffItem,
    CrawlJob,
    EmailLog,
    EmailLogRecordState,
    EmailTask,
    IdentityProfile,
    Professor,
)
from app.modules.professors.public import (
    ProfessorSelectionError,
    resolve_professor_selection,
)
from app.schemas.agent import (
    AgentProfessorPresentSelectionRequest,
    AgentUiHandoffAcknowledgeRequest,
    AgentUiHandoffClaimRead,
    AgentUiHandoffRead,
)
from app.services.agent_mutations import fingerprint, normalize_idempotency_key


UI_HANDOFF_TTL = timedelta(minutes=30)
UI_HANDOFF_APPLIED_TTL = timedelta(hours=8)
UI_HANDOFF_CLAIM_LEASE = timedelta(seconds=30)
UI_HANDOFF_MAX_SELECTION = 10_000
UI_HANDOFF_MAX_RESULT_BYTES = 16_384

UI_HANDOFF_PENDING = "pending"
UI_HANDOFF_CLAIMED = "claimed"
UI_HANDOFF_AWAITING_USER = "awaiting_user"
UI_HANDOFF_APPLIED = "applied"
UI_HANDOFF_FAILED = "failed"
UI_HANDOFF_CANCELED = "canceled"
UI_HANDOFF_EXPIRED = "expired"

_OPEN_STATUSES = {
    UI_HANDOFF_PENDING,
    UI_HANDOFF_CLAIMED,
    UI_HANDOFF_AWAITING_USER,
}
_EXPIRABLE_STATUSES = _OPEN_STATUSES | {UI_HANDOFF_FAILED}
_PROFESSOR_SURFACES = {"professors.management", "professors.home"}


async def create_professor_selection_ui_handoff(
    session_factory: async_sessionmaker[AsyncSession],
    request: AgentProfessorPresentSelectionRequest,
    *,
    idempotency_key: str | None,
) -> AgentUiHandoffRead:
    normalized_key = normalize_idempotency_key(idempotency_key)
    canonical_request = request.model_dump(mode="json")
    request_fingerprint = fingerprint(
        {"operation": "professors.present-selection", **canonical_request},
    )
    async with session_factory() as session:
        existing = await _idempotent_handoff(
            session,
            normalized_key,
            request_fingerprint,
        )
        if existing is not None:
            return _serialize_handoff(existing, idempotent_replay=True)

        try:
            (
                selected_ids,
                matched_count,
                excluded_count,
            ) = await resolve_professor_selection(
                session,
                request.selection,
            )
        except ProfessorSelectionError as exc:
            if exc.code == "PROFESSOR_SELECTION_EMPTY":
                raise AgentApiError(
                    status_code=409,
                    code="UI_HANDOFF_SELECTION_EMPTY",
                    message="没有导师匹配当前界面选择条件；软件界面未被改变。",
                ) from exc
            raise AgentApiError(
                status_code=exc.status_code,
                code=exc.code,
                message=exc.message,
            ) from exc

        _ensure_selection_size(selected_ids)
        professors_by_id = await _load_professors_by_id(session, selected_ids)
        missing_ids = [
            professor_id
            for professor_id in selected_ids
            if professor_id not in professors_by_id
        ]
        if missing_ids:
            raise AgentApiError(
                status_code=404,
                code="PROFESSOR_NOT_FOUND",
                message=f"未找到导师：{missing_ids[0]}",
            )
        archived_count = sum(
            1
            for professor_id in selected_ids
            if professors_by_id[professor_id].archived_at is not None
        )
        if request.surface == "professors.home" and archived_count:
            raise AgentApiError(
                status_code=409,
                code="HOME_SELECTION_ARCHIVED_UNSUPPORTED",
                message="首页只能展示未归档导师；请改用 professors.management 界面。",
            )
        if request.identity_id is not None:
            identity = await session.scalar(
                select(IdentityProfile).where(
                    IdentityProfile.id == request.identity_id,
                    IdentityProfile.deleted_at.is_(None),
                )
            )
            if identity is None:
                raise AgentApiError(
                    status_code=404,
                    code="IDENTITY_NOT_FOUND",
                    message=f"未找到发件身份：{request.identity_id}",
                )

        if archived_count == 0:
            archive_scope = "active"
        elif archived_count == len(selected_ids):
            archive_scope = "archived"
        else:
            archive_scope = "all"
        selection_fingerprint = fingerprint(
            {"resource": "professor", "ids": selected_ids},
        )
        payload: dict[str, object] = {
            "kind": "professor_selection",
            "resource": "professors",
            "selection_mode": request.selection_mode,
            "display": request.display,
            "archive_scope": archive_scope,
            "matched_count": matched_count,
            "excluded_count": excluded_count,
        }
        if request.identity_id is not None:
            payload["identity_id"] = request.identity_id
        ui_effects = [
            "focus_window",
            "navigate",
            f"{request.selection_mode}_selection",
        ]
        if request.display == "selected_only":
            ui_effects.append("show_selected")
        payload["ui_effects"] = ui_effects
        route = "/professors" if request.surface == "professors.management" else "/"
        return await _insert_handoff(
            session,
            surface=request.surface,
            route=route,
            payload=payload,
            resource_type="professor",
            resource_ids=[str(professor_id) for professor_id in selected_ids],
            selection_fingerprint=selection_fingerprint,
            idempotency_key=normalized_key,
            request_fingerprint=request_fingerprint,
        )


async def create_task_center_ui_handoff(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    *,
    idempotency_key: str | None,
) -> AgentUiHandoffRead:
    return await _create_email_task_handoff(
        session_factory,
        task_id,
        surface="tasks.center",
        operation="tasks.present",
        idempotency_key=idempotency_key,
    )


async def create_draft_workspace_ui_handoff(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    *,
    idempotency_key: str | None,
) -> AgentUiHandoffRead:
    return await _create_email_task_handoff(
        session_factory,
        task_id,
        surface="draft.workspace",
        operation="drafts.present",
        idempotency_key=idempotency_key,
    )


async def _create_email_task_handoff(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    *,
    surface: str,
    operation: str,
    idempotency_key: str | None,
) -> AgentUiHandoffRead:
    normalized_key = normalize_idempotency_key(idempotency_key)
    request_fingerprint = fingerprint({"operation": operation, "task_id": task_id})
    async with session_factory() as session:
        existing = await _idempotent_handoff(
            session,
            normalized_key,
            request_fingerprint,
        )
        if existing is not None:
            return _serialize_handoff(existing, idempotent_replay=True)
        task = await session.get(EmailTask, task_id)
        if task is None:
            raise AgentApiError(
                status_code=404,
                code="TASK_NOT_FOUND",
                message=f"未找到任务：{task_id}",
            )
        if task.professor_id is None or task.identity_id is None:
            raise AgentApiError(
                status_code=409,
                code="TASK_UI_CONTEXT_INCOMPLETE",
                message="该任务缺少导师或发件身份，无法在软件中定位。",
            )
        payload: dict[str, object] = {
            "kind": "task_context",
            "resource": "tasks",
            "task_id": task.id,
            "professor_id": task.professor_id,
            "identity_id": task.identity_id,
            "ui_effects": ["focus_window", "navigate", "focus_resource"],
        }
        route = (
            "/tasks" if surface == "tasks.center" else f"/workspace/{task.professor_id}"
        )
        return await _insert_handoff(
            session,
            surface=surface,
            route=route,
            payload=payload,
            resource_type="email_task",
            resource_ids=[str(task.id)],
            selection_fingerprint=None,
            idempotency_key=normalized_key,
            request_fingerprint=request_fingerprint,
        )


async def create_crawl_job_ui_handoff(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: int,
    *,
    idempotency_key: str | None,
) -> AgentUiHandoffRead:
    normalized_key = normalize_idempotency_key(idempotency_key)
    request_fingerprint = fingerprint(
        {"operation": "crawler.jobs.present", "job_id": job_id},
    )
    async with session_factory() as session:
        existing = await _idempotent_handoff(
            session,
            normalized_key,
            request_fingerprint,
        )
        if existing is not None:
            return _serialize_handoff(existing, idempotent_replay=True)
        job = await session.get(CrawlJob, job_id)
        if job is None:
            raise AgentApiError(
                status_code=404,
                code="CRAWL_JOB_NOT_FOUND",
                message=f"未找到抓取任务：{job_id}",
            )
        return await _insert_handoff(
            session,
            surface="crawler.job",
            route="/tasks",
            payload={
                "kind": "crawl_job_context",
                "resource": "crawler.jobs",
                "job_id": job.id,
                "ui_effects": ["focus_window", "navigate", "focus_resource"],
            },
            resource_type="crawl_job",
            resource_ids=[str(job.id)],
            selection_fingerprint=None,
            idempotency_key=normalized_key,
            request_fingerprint=request_fingerprint,
        )


async def create_communication_thread_ui_handoff(
    session_factory: async_sessionmaker[AsyncSession],
    thread_id: str,
    *,
    idempotency_key: str | None,
) -> AgentUiHandoffRead:
    identity_id, professor_id = _parse_thread_id(thread_id)
    normalized_key = normalize_idempotency_key(idempotency_key)
    request_fingerprint = fingerprint(
        {"operation": "communications.threads.present", "thread_id": thread_id},
    )
    async with session_factory() as session:
        existing = await _idempotent_handoff(
            session,
            normalized_key,
            request_fingerprint,
        )
        if existing is not None:
            return _serialize_handoff(existing, idempotent_replay=True)
        exists = await session.scalar(
            select(func.count(EmailLog.id)).where(
                EmailLog.identity_id == identity_id,
                EmailLog.professor_id == professor_id,
                EmailLog.direction.in_(["sent", "received"]),
                EmailLog.record_state == EmailLogRecordState.CANONICAL.value,
            ),
        )
        if not exists:
            raise AgentApiError(
                status_code=404,
                code="COMMUNICATION_THREAD_NOT_FOUND",
                message=f"未找到通信线程：{thread_id}",
            )
        return await _insert_handoff(
            session,
            surface="communications.thread",
            route=f"/workspace/{professor_id}",
            payload={
                "kind": "communication_thread_context",
                "resource": "communications.threads",
                "thread_id": thread_id,
                "identity_id": identity_id,
                "professor_id": professor_id,
                "ui_effects": ["focus_window", "navigate", "focus_resource"],
            },
            resource_type="communication_thread",
            resource_ids=[thread_id],
            selection_fingerprint=None,
            idempotency_key=normalized_key,
            request_fingerprint=request_fingerprint,
        )


async def get_ui_handoff(
    session_factory: async_sessionmaker[AsyncSession],
    handoff_id: str,
) -> AgentUiHandoffRead:
    async with session_factory() as session:
        handoff = await _get_handoff_or_raise(session, handoff_id)
        changed = _expire_if_needed(handoff)
        if changed:
            await session.commit()
        return _serialize_handoff(handoff)


async def cancel_ui_handoff(
    session_factory: async_sessionmaker[AsyncSession],
    handoff_id: str,
) -> AgentUiHandoffRead:
    async with session_factory() as session:
        handoff = await _get_handoff_or_raise(session, handoff_id)
        if _expire_if_needed(handoff):
            await session.commit()
            raise _expired_handoff_error()
        if handoff.status == UI_HANDOFF_CANCELED:
            return _serialize_handoff(handoff)
        if handoff.status not in _OPEN_STATUSES:
            raise AgentApiError(
                status_code=409,
                code="UI_HANDOFF_NOT_CANCELABLE",
                message=f"状态 {handoff.status} 的界面交接不能取消。",
            )
        now = utc_now()
        canceled = await session.execute(
            update(AgentUiHandoff)
            .where(
                AgentUiHandoff.id == handoff_id,
                AgentUiHandoff.status.in_(list(_OPEN_STATUSES)),
                AgentUiHandoff.expires_at > now,
            )
            .values(
                status=UI_HANDOFF_CANCELED,
                canceled_at=now,
                consumer_id=None,
                claim_expires_at=None,
                updated_at=now,
            )
            .execution_options(synchronize_session=False),
        )
        if canceled.rowcount != 1:
            await session.rollback()
            await session.refresh(handoff)
            if _expire_if_needed(handoff):
                await session.commit()
                raise _expired_handoff_error()
            if handoff.status == UI_HANDOFF_CANCELED:
                return _serialize_handoff(handoff)
            raise AgentApiError(
                status_code=409,
                code="UI_HANDOFF_NOT_CANCELABLE",
                message=f"状态 {handoff.status} 的界面交接不能取消。",
            )
        await session.commit()
        await session.refresh(handoff)
        return _serialize_handoff(handoff)


async def retry_ui_handoff(
    session_factory: async_sessionmaker[AsyncSession],
    handoff_id: str,
) -> AgentUiHandoffRead:
    async with session_factory() as session:
        handoff = await _get_handoff_or_raise(session, handoff_id)
        if _expire_if_needed(handoff):
            await session.commit()
            raise _expired_handoff_error()
        if handoff.status not in {UI_HANDOFF_FAILED, UI_HANDOFF_AWAITING_USER}:
            raise AgentApiError(
                status_code=409,
                code="UI_HANDOFF_NOT_RETRYABLE",
                message=f"状态 {handoff.status} 的界面交接不能重试。",
            )
        now = utc_now()
        retried = await session.execute(
            update(AgentUiHandoff)
            .where(
                AgentUiHandoff.id == handoff_id,
                AgentUiHandoff.status.in_(
                    [UI_HANDOFF_FAILED, UI_HANDOFF_AWAITING_USER],
                ),
                AgentUiHandoff.expires_at > now,
            )
            .values(
                status=UI_HANDOFF_PENDING,
                consumer_id=None,
                claimed_at=None,
                claim_expires_at=None,
                awaiting_user_at=None,
                failed_at=None,
                failure_message=None,
                result=None,
                updated_at=now,
            )
            .execution_options(synchronize_session=False),
        )
        if retried.rowcount != 1:
            await session.rollback()
            await session.refresh(handoff)
            if _expire_if_needed(handoff):
                await session.commit()
                raise _expired_handoff_error()
            raise AgentApiError(
                status_code=409,
                code="UI_HANDOFF_NOT_RETRYABLE",
                message=f"状态 {handoff.status} 的界面交接不能重试。",
            )
        await session.commit()
        await session.refresh(handoff)
        return _serialize_handoff(handoff)


async def claim_next_ui_handoff(
    session_factory: async_sessionmaker[AsyncSession],
    consumer_id: str,
) -> AgentUiHandoffClaimRead | None:
    async with session_factory() as session:
        now = utc_now()
        await session.execute(
            update(AgentUiHandoff)
            .where(
                AgentUiHandoff.status.in_(sorted(_EXPIRABLE_STATUSES)),
                AgentUiHandoff.expires_at <= now,
            )
            .values(
                status=UI_HANDOFF_EXPIRED,
                consumer_id=None,
                claim_expires_at=None,
                updated_at=now,
            ),
        )
        await session.execute(
            update(AgentUiHandoff)
            .where(
                AgentUiHandoff.status == UI_HANDOFF_CLAIMED,
                AgentUiHandoff.claim_expires_at.is_not(None),
                AgentUiHandoff.claim_expires_at <= now,
            )
            .values(
                status=UI_HANDOFF_PENDING,
                consumer_id=None,
                claimed_at=None,
                claim_expires_at=None,
                updated_at=now,
            ),
        )
        # Persist housekeeping before entering the optimistic claim loop. A
        # losing claim must not roll back expiry or stale-lease recovery and
        # accidentally make an expired record claimable on the next attempt.
        await session.commit()

        for _attempt in range(3):
            now = utc_now()
            candidate = await session.scalar(
                select(AgentUiHandoff)
                .where(
                    AgentUiHandoff.status == UI_HANDOFF_PENDING,
                    AgentUiHandoff.expires_at > now,
                )
                .order_by(AgentUiHandoff.created_at.asc(), AgentUiHandoff.id.asc())
                .limit(1),
            )
            if candidate is None:
                await session.commit()
                return None
            claim_expires_at = min(
                as_utc_aware(now + UI_HANDOFF_CLAIM_LEASE),
                as_utc_aware(candidate.expires_at),
            )
            claimed = await session.execute(
                update(AgentUiHandoff)
                .where(
                    AgentUiHandoff.id == candidate.id,
                    AgentUiHandoff.status == UI_HANDOFF_PENDING,
                    AgentUiHandoff.expires_at > now,
                )
                .values(
                    status=UI_HANDOFF_CLAIMED,
                    consumer_id=consumer_id,
                    claimed_at=now,
                    claim_expires_at=claim_expires_at,
                    delivery_attempts=AgentUiHandoff.delivery_attempts + 1,
                    updated_at=now,
                ),
            )
            if claimed.rowcount != 1:
                await session.rollback()
                continue
            await session.commit()
            handoff = await _get_handoff_or_raise(session, candidate.id)
            return await _serialize_claim(session, handoff)
        raise AgentApiError(
            status_code=409,
            code="UI_HANDOFF_CLAIM_CONFLICT",
            message="界面交接正在被其他窗口领取，请稍后重试。",
            retryable=True,
        )


async def acknowledge_ui_handoff(
    session_factory: async_sessionmaker[AsyncSession],
    handoff_id: str,
    request: AgentUiHandoffAcknowledgeRequest,
) -> AgentUiHandoffRead:
    _validate_result_size(request.result)
    async with session_factory() as session:
        handoff = await _get_handoff_or_raise(session, handoff_id)
        if _expire_if_needed(handoff):
            await session.commit()
            raise _expired_handoff_error()
        if (
            handoff.status == request.status
            and handoff.consumer_id == request.consumer_id
        ):
            return _serialize_handoff(handoff)
        if handoff.status not in {UI_HANDOFF_CLAIMED, UI_HANDOFF_AWAITING_USER}:
            raise AgentApiError(
                status_code=409,
                code="UI_HANDOFF_ACKNOWLEDGEMENT_CONFLICT",
                message=f"状态 {handoff.status} 的界面交接不能接收该回执。",
            )
        if handoff.consumer_id != request.consumer_id:
            raise AgentApiError(
                status_code=409,
                code="UI_HANDOFF_CONSUMER_MISMATCH",
                message="界面交接已被其他桌面窗口领取。",
            )
        now = utc_now()
        values: dict[str, object] = {
            "status": request.status,
            "result": request.result or None,
            "claim_expires_at": None,
            "updated_at": now,
        }
        if request.status == UI_HANDOFF_APPLIED:
            values["applied_at"] = now
            values["failure_message"] = None
            values["expires_at"] = max(
                as_utc_aware(handoff.expires_at),
                as_utc_aware(now + UI_HANDOFF_APPLIED_TTL),
            )
        elif request.status == UI_HANDOFF_AWAITING_USER:
            values["awaiting_user_at"] = now
            values["failure_message"] = None
        else:
            values["failed_at"] = now
            values["failure_message"] = (request.failure_message or "").strip()
        acknowledged = await session.execute(
            update(AgentUiHandoff)
            .where(
                AgentUiHandoff.id == handoff_id,
                AgentUiHandoff.status.in_(
                    [UI_HANDOFF_CLAIMED, UI_HANDOFF_AWAITING_USER],
                ),
                AgentUiHandoff.consumer_id == request.consumer_id,
                AgentUiHandoff.expires_at > now,
            )
            .values(**values)
            .execution_options(synchronize_session=False),
        )
        if acknowledged.rowcount != 1:
            await session.rollback()
            await session.refresh(handoff)
            if _expire_if_needed(handoff):
                await session.commit()
                raise _expired_handoff_error()
            if (
                handoff.status == request.status
                and handoff.consumer_id == request.consumer_id
            ):
                return _serialize_handoff(handoff)
            if handoff.status not in {
                UI_HANDOFF_CLAIMED,
                UI_HANDOFF_AWAITING_USER,
            }:
                raise AgentApiError(
                    status_code=409,
                    code="UI_HANDOFF_ACKNOWLEDGEMENT_CONFLICT",
                    message=f"状态 {handoff.status} 的界面交接不能接收该回执。",
                )
            if handoff.consumer_id != request.consumer_id:
                raise AgentApiError(
                    status_code=409,
                    code="UI_HANDOFF_CONSUMER_MISMATCH",
                    message="界面交接已被其他桌面窗口领取。",
                )
            raise AgentApiError(
                status_code=409,
                code="UI_HANDOFF_ACKNOWLEDGEMENT_CONFLICT",
                message=f"状态 {handoff.status} 的界面交接不能接收该回执。",
            )
        await session.commit()
        await session.refresh(handoff)
        return _serialize_handoff(handoff)


async def _insert_handoff(
    session: AsyncSession,
    *,
    surface: str,
    route: str,
    payload: dict[str, object],
    resource_type: str,
    resource_ids: Sequence[str],
    selection_fingerprint: str | None,
    idempotency_key: str | None,
    request_fingerprint: str,
) -> AgentUiHandoffRead:
    now = utc_now()
    handoff = AgentUiHandoff(
        id=_new_handoff_id(),
        schema_version=1,
        surface=surface,
        route=route,
        status=UI_HANDOFF_PENDING,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        selection_fingerprint=selection_fingerprint,
        selection_count=len(resource_ids),
        payload=payload,
        result=None,
        delivery_attempts=0,
        expires_at=now + UI_HANDOFF_TTL,
        created_at=now,
        updated_at=now,
    )
    session.add(handoff)
    session.add_all(
        AgentUiHandoffItem(
            handoff_id=handoff.id,
            resource_type=resource_type,
            resource_id=resource_id,
            ordinal=ordinal,
        )
        for ordinal, resource_id in enumerate(resource_ids)
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if idempotency_key is not None:
            existing = await session.scalar(
                select(AgentUiHandoff).where(
                    AgentUiHandoff.idempotency_key == idempotency_key,
                ),
            )
            if existing is not None:
                _ensure_same_request(existing, request_fingerprint)
                return _serialize_handoff(existing, idempotent_replay=True)
        raise AgentApiError(
            status_code=409,
            code="UI_HANDOFF_CREATE_CONFLICT",
            message="界面交接创建发生冲突，请重试。",
            retryable=True,
        ) from exc
    return _serialize_handoff(handoff)


async def _idempotent_handoff(
    session: AsyncSession,
    idempotency_key: str | None,
    request_fingerprint: str,
) -> AgentUiHandoff | None:
    if idempotency_key is None:
        return None
    existing = await session.scalar(
        select(AgentUiHandoff).where(
            AgentUiHandoff.idempotency_key == idempotency_key,
        ),
    )
    if existing is None:
        return None
    _ensure_same_request(existing, request_fingerprint)
    if _expire_if_needed(existing):
        await session.commit()
    return existing


def _ensure_same_request(handoff: AgentUiHandoff, request_fingerprint: str) -> None:
    if handoff.request_fingerprint != request_fingerprint:
        raise AgentApiError(
            status_code=409,
            code="IDEMPOTENCY_KEY_REUSED",
            message="Idempotency-Key 已用于不同的界面交接请求。",
        )


async def _get_handoff_or_raise(
    session: AsyncSession,
    handoff_id: str,
) -> AgentUiHandoff:
    handoff = await session.get(AgentUiHandoff, handoff_id)
    if handoff is None:
        raise AgentApiError(
            status_code=404,
            code="UI_HANDOFF_NOT_FOUND",
            message=f"未找到界面交接：{handoff_id}",
        )
    return handoff


async def _serialize_claim(
    session: AsyncSession,
    handoff: AgentUiHandoff,
) -> AgentUiHandoffClaimRead:
    selected_ids: list[int] = []
    if handoff.surface in _PROFESSOR_SURFACES:
        raw_ids = list(
            await session.scalars(
                select(AgentUiHandoffItem.resource_id)
                .where(
                    AgentUiHandoffItem.handoff_id == handoff.id,
                    AgentUiHandoffItem.resource_type == "professor",
                )
                .order_by(AgentUiHandoffItem.ordinal.asc()),
            ),
        )
        selected_ids = [int(resource_id) for resource_id in raw_ids]
    public = _serialize_handoff(handoff).model_dump()
    if handoff.consumer_id is None or handoff.claim_expires_at is None:
        raise RuntimeError("claimed UI handoff is missing its consumer lease")
    return AgentUiHandoffClaimRead(
        **public,
        consumer_id=handoff.consumer_id,
        claim_expires_at=handoff.claim_expires_at,
        payload=handoff.payload,
        selected_ids=selected_ids,
    )


def _serialize_handoff(
    handoff: AgentUiHandoff,
    *,
    idempotent_replay: bool = False,
) -> AgentUiHandoffRead:
    ui_effects = handoff.payload.get("ui_effects")
    return AgentUiHandoffRead(
        handoff_id=handoff.id,
        schema_version=handoff.schema_version,
        surface=handoff.surface,
        route=handoff.route,
        status=handoff.status,
        selection_count=handoff.selection_count,
        selection_fingerprint=handoff.selection_fingerprint,
        ui_effects=[str(item) for item in ui_effects]
        if isinstance(ui_effects, list)
        else [],
        result=handoff.result,
        failure_message=handoff.failure_message,
        delivery_attempts=handoff.delivery_attempts,
        expires_at=handoff.expires_at,
        claimed_at=handoff.claimed_at,
        awaiting_user_at=handoff.awaiting_user_at,
        applied_at=handoff.applied_at,
        failed_at=handoff.failed_at,
        canceled_at=handoff.canceled_at,
        created_at=handoff.created_at,
        updated_at=handoff.updated_at,
        idempotent_replay=idempotent_replay,
        available_actions=_available_actions(handoff.status),
    )


def _available_actions(status: str) -> list[str]:
    if status in {UI_HANDOFF_PENDING, UI_HANDOFF_CLAIMED}:
        return ["read", "wait", "cancel"]
    if status == UI_HANDOFF_AWAITING_USER:
        return ["read", "retry", "cancel"]
    if status == UI_HANDOFF_FAILED:
        return ["read", "retry"]
    return ["read"]


def _expire_if_needed(handoff: AgentUiHandoff) -> bool:
    if handoff.status not in _EXPIRABLE_STATUSES:
        return False
    now = utc_now()
    if as_utc_aware(handoff.expires_at) > as_utc_aware(now):
        return False
    handoff.status = UI_HANDOFF_EXPIRED
    handoff.consumer_id = None
    handoff.claim_expires_at = None
    handoff.updated_at = now
    return True


def _expired_handoff_error() -> AgentApiError:
    return AgentApiError(
        status_code=409,
        code="UI_HANDOFF_EXPIRED",
        message="界面交接已经过期，请重新生成。",
    )


async def _load_professors_by_id(
    session: AsyncSession,
    professor_ids: Sequence[int],
) -> dict[int, Professor]:
    result: dict[int, Professor] = {}
    for id_chunk in chunked_values(professor_ids):
        result.update(
            {
                professor.id: professor
                for professor in await session.scalars(
                    select(Professor).where(Professor.id.in_(id_chunk)),
                )
            },
        )
    return result


def _ensure_selection_size(selected_ids: Sequence[int]) -> None:
    if len(selected_ids) > UI_HANDOFF_MAX_SELECTION:
        raise AgentApiError(
            status_code=413,
            code="UI_HANDOFF_SELECTION_TOO_LARGE",
            message=(
                f"界面选择最多支持 {UI_HANDOFF_MAX_SELECTION} 项；"
                "请缩小筛选范围后重试。"
            ),
        )


def _parse_thread_id(thread_id: str) -> tuple[int, int]:
    identity_raw, separator, professor_raw = thread_id.partition(":")
    try:
        identity_id = int(identity_raw)
        professor_id = int(professor_raw)
    except ValueError as exc:
        raise AgentApiError(
            status_code=400,
            code="COMMUNICATION_THREAD_ID_INVALID",
            message="通信线程 ID 无效。",
        ) from exc
    if not separator or identity_id < 1 or professor_id < 1:
        raise AgentApiError(
            status_code=400,
            code="COMMUNICATION_THREAD_ID_INVALID",
            message="通信线程 ID 无效。",
        )
    return identity_id, professor_id


def _validate_result_size(result: dict[str, object]) -> None:
    encoded = json.dumps(
        result,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > UI_HANDOFF_MAX_RESULT_BYTES:
        raise AgentApiError(
            status_code=413,
            code="UI_HANDOFF_RESULT_TOO_LARGE",
            message="界面交接回执过大。",
        )


def _new_handoff_id() -> str:
    return f"uih_{secrets.token_urlsafe(18)}"


__all__: Sequence[str] = (
    "UI_HANDOFF_APPLIED",
    "UI_HANDOFF_AWAITING_USER",
    "UI_HANDOFF_CANCELED",
    "UI_HANDOFF_CLAIMED",
    "UI_HANDOFF_EXPIRED",
    "UI_HANDOFF_FAILED",
    "UI_HANDOFF_PENDING",
    "acknowledge_ui_handoff",
    "cancel_ui_handoff",
    "claim_next_ui_handoff",
    "create_communication_thread_ui_handoff",
    "create_crawl_job_ui_handoff",
    "create_draft_workspace_ui_handoff",
    "create_professor_selection_ui_handoff",
    "create_task_center_ui_handoff",
    "get_ui_handoff",
    "retry_ui_handoff",
)
