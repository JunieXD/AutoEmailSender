from __future__ import annotations

import hashlib
import json
import secrets
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.core.agent_api_errors import AgentApiError
from app.core.error_formatting import safe_exception_message
from app.core.time import as_utc_aware, serialize_api_datetime, utc_now
from app.models import AgentActionPlan, EmailTask, EmailTaskStatus, IdentityProfile
from app.schemas.agent import (
    AgentActionPlanRead,
    AgentPlanExecuteRequest,
    AgentPlanSummaryRead,
    AgentPrepareSendRequest,
)
from app.schemas.email_task import EmailTaskApprovalRequest, EmailTaskScheduleRequest
from app.services.agent_plan_effects import resolve_agent_plan_effects
from app.services.operation_logs import record_operation_log
from app.services.task_runtime import approve_and_schedule_task, approve_and_send_task


PLAN_TTL = timedelta(minutes=30)
PLAN_STATUS_AWAITING = "awaiting_confirmation"
PLAN_STATUS_EXECUTING = "executing"
PLAN_STATUS_EXECUTED = "executed"
PLAN_STATUS_CANCELED = "canceled"
PLAN_STATUS_EXPIRED = "expired"
RECOMMENDED_ATTACHMENT_TOTAL_BYTES = 1024 * 1024


async def create_email_action_plan(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    payload: AgentPrepareSendRequest,
    *,
    idempotency_key: str | None,
) -> AgentActionPlanRead:
    normalized_key = _normalize_idempotency_key(idempotency_key)
    scheduled_at = (
        as_utc_aware(payload.scheduled_at)
        if payload.scheduled_at is not None
        else None
    )
    if scheduled_at is not None and scheduled_at <= utc_now():
        raise AgentApiError(
            status_code=400,
            code="INVALID_SCHEDULE_TIME",
            message="排程时间必须晚于当前时间。",
        )
    request_data = {
        "task_id": task_id,
        "delivery": payload.delivery,
        "scheduled_at": serialize_api_datetime(scheduled_at) if scheduled_at else None,
    }
    request_fingerprint = _fingerprint(request_data)

    async with session_factory() as session:
        if normalized_key:
            existing = await session.scalar(
                select(AgentActionPlan).where(
                    AgentActionPlan.idempotency_key == normalized_key,
                ),
            )
            if existing is not None:
                _ensure_same_idempotent_request(existing, request_fingerprint)
                await _expire_if_needed(session, existing)
                return _serialize_plan(existing, idempotent_replay=True)

        task = await _load_task(session, task_id)
        if task is None:
            raise AgentApiError(
                status_code=404,
                code="DRAFT_NOT_FOUND",
                message="未找到邮件草稿。",
            )
        snapshot = _build_task_snapshot(
            task,
            delivery=payload.delivery,
            scheduled_at=scheduled_at,
        )
        content_fingerprint = _fingerprint(snapshot)
        now = utc_now()
        plan = AgentActionPlan(
            id=_new_plan_id(),
            action="email.schedule" if payload.delivery == "scheduled" else "email.send",
            status=PLAN_STATUS_AWAITING,
            email_task_id=task.id,
            idempotency_key=normalized_key,
            request_fingerprint=request_fingerprint,
            content_fingerprint=content_fingerprint,
            snapshot=snapshot,
            result=None,
            expires_at=now + PLAN_TTL,
            created_at=now,
            updated_at=now,
        )
        session.add(plan)
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            if normalized_key:
                existing = await session.scalar(
                    select(AgentActionPlan).where(
                        AgentActionPlan.idempotency_key == normalized_key,
                    ),
                )
                if existing is not None:
                    _ensure_same_idempotent_request(existing, request_fingerprint)
                    return _serialize_plan(existing, idempotent_replay=True)
            raise AgentApiError(
                status_code=409,
                code="PLAN_CREATE_CONFLICT",
                message="发送计划创建发生冲突，请重新生成。",
                retryable=True,
            ) from exc
        await _record_plan_event(session, plan, "agent_cli.plan_created")
        await session.commit()
        return _serialize_plan(plan)


async def get_email_action_plan(
    session_factory: async_sessionmaker[AsyncSession],
    plan_id: str,
) -> AgentActionPlanRead:
    async with session_factory() as session:
        plan = await _get_plan_or_raise(session, plan_id)
        await _expire_if_needed(session, plan)
        return _serialize_plan(plan)


async def cancel_email_action_plan(
    session_factory: async_sessionmaker[AsyncSession],
    plan_id: str,
) -> AgentActionPlanRead:
    async with session_factory() as session:
        plan = await _get_plan_or_raise(session, plan_id)
        await _expire_if_needed(session, plan)
        if plan.status == PLAN_STATUS_CANCELED:
            return _serialize_plan(plan, idempotent_replay=True)
        if plan.status != PLAN_STATUS_AWAITING:
            raise AgentApiError(
                status_code=409,
                code="PLAN_NOT_CANCELABLE",
                message=f"当前计划状态为 {plan.status}，不能取消。",
            )
        now = utc_now()
        plan.status = PLAN_STATUS_CANCELED
        plan.canceled_at = now
        plan.updated_at = now
        await _record_plan_event(session, plan, "agent_cli.plan_canceled")
        await session.commit()
        return _serialize_plan(plan)


async def execute_email_action_plan(
    session_factory: async_sessionmaker[AsyncSession],
    plan_id: str,
    payload: AgentPlanExecuteRequest,
) -> AgentActionPlanRead:
    if not payload.confirm:
        raise AgentApiError(
            status_code=409,
            code="PLAN_CONFIRMATION_REQUIRED",
            message="尚未执行。请向用户展示计划，并在用户明确确认后使用 --confirm。",
            suggested_command=f"auto-email-sender plans show {plan_id}",
        )

    async with session_factory() as session:
        plan = await _get_plan_or_raise(session, plan_id)
        await _expire_if_needed(session, plan)
        if plan.status == PLAN_STATUS_EXECUTED:
            return _serialize_plan(plan, idempotent_replay=True)
        if plan.status == PLAN_STATUS_CANCELED:
            raise AgentApiError(
                status_code=409,
                code="PLAN_CANCELED",
                message="该发送计划已经取消，请重新生成计划。",
            )
        if plan.status == PLAN_STATUS_EXPIRED:
            raise _plan_expired_error(plan)
        if plan.status == PLAN_STATUS_EXECUTING:
            raise AgentApiError(
                status_code=409,
                code="PLAN_EXECUTION_IN_PROGRESS",
                message="该计划正在执行；不要创建重复发送，请稍后再次查看此计划。",
                retryable=True,
                suggested_command=f"auto-email-sender plans show {plan.id}",
            )

        task = await _load_task(session, plan.email_task_id)
        if task is None:
            raise AgentApiError(
                status_code=409,
                code="PLAN_STALE",
                message="计划对应的草稿已不存在，请重新创建草稿和发送计划。",
                details={"changed_fields": ["task"]},
            )
        expected_snapshot = plan.snapshot
        delivery = str(expected_snapshot.get("delivery") or "immediate")
        scheduled_at = _snapshot_scheduled_at(expected_snapshot)
        if scheduled_at is not None and scheduled_at <= utc_now():
            raise AgentApiError(
                status_code=409,
                code="PLAN_STALE",
                message="计划中的排程时间已经过去，请重新生成发送计划。",
                details={"changed_fields": ["scheduled_at"]},
                suggested_command=f"auto-email-sender drafts prepare-send {task.id}",
            )
        try:
            current_snapshot = _build_task_snapshot(
                task,
                delivery=delivery,
                scheduled_at=scheduled_at,
            )
        except ValueError as exc:
            raise AgentApiError(
                status_code=409,
                code="PLAN_STALE",
                message="草稿状态已发生变化，请重新生成发送计划。",
                details={"changed_fields": ["status"], "reason": str(exc)},
                suggested_command=f"auto-email-sender drafts prepare-send {task.id}",
            ) from exc
        current_fingerprint = _fingerprint(current_snapshot)
        if current_fingerprint != plan.content_fingerprint:
            raise AgentApiError(
                status_code=409,
                code="PLAN_STALE",
                message="发送内容已发生变化，请重新生成并展示新的计划。",
                details={
                    "changed_fields": _changed_snapshot_fields(
                        expected_snapshot,
                        current_snapshot,
                    ),
                },
                suggested_command=f"auto-email-sender drafts prepare-send {task.id}",
            )

        now = utc_now()
        claim = await session.execute(
            update(AgentActionPlan)
            .where(
                AgentActionPlan.id == plan.id,
                AgentActionPlan.status == PLAN_STATUS_AWAITING,
                AgentActionPlan.content_fingerprint == current_fingerprint,
            )
            .values(
                status=PLAN_STATUS_EXECUTING,
                confirmed_at=now,
                execution_started_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False),
        )
        if claim.rowcount != 1:
            await session.rollback()
            raise AgentApiError(
                status_code=409,
                code="PLAN_EXECUTION_IN_PROGRESS",
                message="该计划已被另一个执行请求领取；不要重复发送。",
                retryable=True,
            )
        plan.status = PLAN_STATUS_EXECUTING
        plan.confirmed_at = now
        plan.execution_started_at = now
        await _record_plan_event(session, plan, "agent_cli.plan_confirmed")
        await session.commit()

    result = await _execute_claimed_plan(session_factory, plan_id)
    return result


async def _execute_claimed_plan(
    session_factory: async_sessionmaker[AsyncSession],
    plan_id: str,
) -> AgentActionPlanRead:
    async with session_factory() as session:
        plan = await _get_plan_or_raise(session, plan_id)
        snapshot = plan.snapshot
        task_id = plan.email_task_id
        summary = snapshot.get("summary")
        if not isinstance(summary, dict):
            raise AgentApiError(
                status_code=500,
                code="INVALID_PLAN_SNAPSHOT",
                message="发送计划快照无效，请重新生成计划。",
            )
        subject = str(summary.get("subject") or "")
        body_text = str(summary.get("body_text") or "")
        body_html_value = summary.get("body_html")
        body_html = str(body_html_value) if body_html_value is not None else None
        attachment_ids = snapshot.get("attachment_material_ids")
        selected_material_ids = (
            [int(value) for value in attachment_ids]
            if isinstance(attachment_ids, list)
            else []
        )
        delivery = str(snapshot.get("delivery") or "immediate")
        scheduled_at = _snapshot_scheduled_at(snapshot)

    try:
        if delivery == "scheduled":
            if scheduled_at is None:
                raise ValueError("排程计划缺少 scheduled_at")
            await approve_and_schedule_task(
                session_factory,
                task_id,
                EmailTaskScheduleRequest(
                    subject=subject,
                    body_text=body_text,
                    body_html=body_html,
                    selected_material_ids=selected_material_ids,
                    scheduled_at=scheduled_at,
                ),
            )
        else:
            await approve_and_send_task(
                session_factory,
                task_id,
                EmailTaskApprovalRequest(
                    subject=subject,
                    body_text=body_text,
                    body_html=body_html,
                    selected_material_ids=selected_material_ids,
                ),
            )
        failure_message = None
    except Exception as exc:
        failure_message = safe_exception_message(exc)

    async with session_factory() as session:
        plan = await _get_plan_or_raise(session, plan_id)
        task = await _load_task(session, task_id)
        now = utc_now()
        result = _build_execution_result(
            task,
            delivery=delivery,
            failure_message=failure_message,
        )
        plan.status = PLAN_STATUS_EXECUTED
        plan.result = result
        plan.failure_message = failure_message
        plan.executed_at = now
        plan.updated_at = now
        await _record_plan_event(
            session,
            plan,
            "agent_cli.plan_executed",
            metadata={
                "outcome": result.get("outcome"),
                "task_status": result.get("task_status"),
            },
        )
        await session.commit()
        return _serialize_plan(plan)


def _build_task_snapshot(
    task: EmailTask,
    *,
    delivery: str,
    scheduled_at,
) -> dict[str, object]:
    if delivery not in {"immediate", "scheduled"}:
        raise ValueError("不支持的交付方式")
    if task.status not in {
        EmailTaskStatus.DISCOVERED.value,
        EmailTaskStatus.MATCHED.value,
        EmailTaskStatus.DRAFT_FAILED.value,
        EmailTaskStatus.REVIEW_REQUIRED.value,
        EmailTaskStatus.SEND_FAILED.value,
    }:
        raise ValueError(f"当前草稿状态 {task.status} 不能创建发送计划")
    if task.sent_at is not None or task.is_replied:
        raise ValueError("已发送或已回信的任务不能创建发送计划")
    professor_email = (task.professor.email or "").strip()
    if not professor_email:
        raise ValueError("导师没有可用邮箱地址")
    if not (
        task.identity.smtp_host
        and task.identity.smtp_username
        and task.identity.smtp_password
    ):
        raise ValueError("发件身份尚未配置 SMTP")

    has_saved_snapshot = any(
        value is not None
        for value in (
            task.approved_subject,
            task.approved_body_text,
            task.approved_body_html,
        )
    )
    subject = (
        task.approved_subject if has_saved_snapshot else task.generated_subject
    ) or ""
    body_text = (
        task.approved_body_text if has_saved_snapshot else task.generated_content_text
    ) or ""
    body_html = (
        task.approved_body_html if has_saved_snapshot else task.generated_content_html
    )
    subject = subject.strip()
    body_text = body_text.strip()
    if not subject or not body_text:
        raise ValueError("草稿缺少可发送的主题或正文")

    material_by_id = {material.id: material for material in task.identity.materials}
    attachment_ids = list(dict.fromkeys(task.selected_material_ids or []))
    missing_attachment_ids = [
        material_id for material_id in attachment_ids if material_id not in material_by_id
    ]
    if missing_attachment_ids:
        raise ValueError("草稿包含不存在或不属于当前身份的附件")
    reference = task.primary_material
    raw_mode = (task.outreach_generation_mode or "llm").lower()
    generation_mode = (
        "template"
        if raw_mode == "template"
        else "manual" if raw_mode == "manual" else "ai_rewrite"
    )
    schedule_iso = (
        serialize_api_datetime(as_utc_aware(scheduled_at))
        if scheduled_at is not None
        else None
    )
    attachment_total_size_bytes = sum(
        max(0, material_by_id[material_id].size_bytes)
        for material_id in attachment_ids
    )
    summary = {
        "recipient_count": 1,
        "recipient": {
            "id": task.professor.id,
            "name": task.professor.name,
            "email": professor_email,
        },
        "identity": {
            "id": task.identity.id,
            "name": task.identity.profile_name or task.identity.name,
            "email_address": task.identity.email_address,
        },
        "generation_mode": generation_mode,
        "template": (
            {"id": task.outreach_template.id, "name": task.outreach_template.name}
            if task.outreach_template is not None
            else None
        ),
        "reference_material": (
            {"id": reference.id, "name": reference.display_name}
            if reference is not None
            else None
        ),
        "attachments": [
            {
                "id": material_id,
                "name": material_by_id[material_id].display_name,
                "size_bytes": max(0, material_by_id[material_id].size_bytes),
            }
            for material_id in attachment_ids
        ],
        "attachment_total_size_bytes": attachment_total_size_bytes,
        "delivery": delivery,
        "scheduled_at": schedule_iso,
        "subject": subject,
        "body_text": body_text,
        "body_html": body_html,
    }
    return {
        "snapshot_version": "1",
        "task_id": task.id,
        "task_status": task.status,
        "identity_id": task.identity_id,
        "professor_id": task.professor_id,
        "llm_profile_id": task.llm_profile_id,
        "template_id": task.outreach_template_id,
        "reference_material_id": task.primary_material_id,
        "attachment_material_ids": attachment_ids,
        "delivery": delivery,
        "scheduled_at": schedule_iso,
        "summary": summary,
    }


def _serialize_plan(
    plan: AgentActionPlan,
    *,
    idempotent_replay: bool = False,
) -> AgentActionPlanRead:
    raw_summary = plan.snapshot.get("summary")
    if not isinstance(raw_summary, dict):
        raise AgentApiError(
            status_code=500,
            code="INVALID_PLAN_SNAPSHOT",
            message="发送计划快照无效，请重新生成计划。",
        )
    summary = AgentPlanSummaryRead.model_validate(raw_summary)
    attachment_warning = _build_large_attachment_warning(
        summary.attachment_total_size_bytes,
    )
    return AgentActionPlanRead(
        plan_id=plan.id,
        action=plan.action,  # type: ignore[arg-type]
        status=plan.status,  # type: ignore[arg-type]
        task_id=plan.email_task_id,
        content_fingerprint=plan.content_fingerprint,
        expires_at=plan.expires_at,
        confirmed_at=plan.confirmed_at,
        executed_at=plan.executed_at,
        canceled_at=plan.canceled_at,
        summary=summary,
        effects=resolve_agent_plan_effects(plan.action),
        warnings=[attachment_warning] if attachment_warning else [],
        result=plan.result,
        idempotent_replay=idempotent_replay,
        confirmation_message=(
            "\n".join(
                filter(
                    None,
                    [
                        "尚未发送。请把以上收件人、正文、身份、参考材料和附件展示给用户，得到明确确认后再执行。",
                        attachment_warning,
                    ],
                ),
            )
            if plan.status == PLAN_STATUS_AWAITING
            else None
        ),
    )


async def _expire_if_needed(
    session: AsyncSession,
    plan: AgentActionPlan,
) -> None:
    if (
        plan.status == PLAN_STATUS_AWAITING
        and as_utc_aware(plan.expires_at) <= utc_now()
    ):
        plan.status = PLAN_STATUS_EXPIRED
        plan.updated_at = utc_now()
        await _record_plan_event(session, plan, "agent_cli.plan_expired")
        await session.commit()


async def _get_plan_or_raise(
    session: AsyncSession,
    plan_id: str,
) -> AgentActionPlan:
    plan = await session.get(AgentActionPlan, plan_id)
    if plan is None:
        raise AgentApiError(
            status_code=404,
            code="PLAN_NOT_FOUND",
            message="未找到发送计划。",
        )
    return plan


async def _load_task(session: AsyncSession, task_id: int) -> EmailTask | None:
    return await session.scalar(
        select(EmailTask)
        .options(
            selectinload(EmailTask.professor),
            selectinload(EmailTask.identity).selectinload(IdentityProfile.materials),
            selectinload(EmailTask.primary_material),
            selectinload(EmailTask.outreach_template),
        )
        .where(EmailTask.id == task_id),
    )


async def _record_plan_event(
    session: AsyncSession,
    plan: AgentActionPlan,
    event_name: str,
    *,
    metadata: dict[str, object] | None = None,
) -> None:
    event_metadata: dict[str, object] = {
        "actor": "agent_cli",
        "plan_id": plan.id,
        "action": plan.action,
        "status": plan.status,
        "task_id": plan.email_task_id,
        "risk_level": "L3",
        "content_fingerprint": plan.content_fingerprint,
    }
    if metadata:
        event_metadata.update(metadata)
    await record_operation_log(
        session,
        category="email",
        event_name=event_name,
        entity_type="agent_action_plan",
        entity_id=plan.id,
        metadata=event_metadata,
    )


def _build_execution_result(
    task: EmailTask | None,
    *,
    delivery: str,
    failure_message: str | None,
) -> dict[str, object]:
    task_status = task.status if task is not None else "missing"
    if failure_message:
        outcome = "failed"
    elif delivery == "scheduled" and task_status == EmailTaskStatus.SCHEDULED.value:
        outcome = "scheduled"
    elif delivery == "immediate" and task_status == EmailTaskStatus.SENT.value:
        outcome = "sent"
    elif task_status == EmailTaskStatus.SEND_FAILED.value:
        outcome = "failed"
    else:
        outcome = task_status
    return {
        "outcome": outcome,
        "task_id": task.id if task is not None else None,
        "task_status": task_status,
        "scheduled_at": (
            serialize_api_datetime(task.scheduled_at)
            if task is not None and task.scheduled_at is not None
            else None
        ),
        "sent_at": (
            serialize_api_datetime(task.sent_at)
            if task is not None and task.sent_at is not None
            else None
        ),
        "rfc_message_id": task.last_rfc_message_id if task is not None else None,
        "error": failure_message or (task.last_error if task is not None else "任务不存在"),
    }


def _changed_snapshot_fields(
    expected: dict[str, object],
    current: dict[str, object],
) -> list[str]:
    fields: list[str] = []
    for key in sorted(set(expected) | set(current)):
        if expected.get(key) != current.get(key):
            fields.append(key)
    return fields or ["content"]


def _build_large_attachment_warning(total_size_bytes: int) -> str | None:
    if total_size_bytes <= RECOMMENDED_ATTACHMENT_TOTAL_BYTES:
        return None
    return (
        f"附件总大小为 {_format_file_size(total_size_bytes)}，建议不超过 1 MB，"
        "以减少被邮箱提供商限流的概率。"
    )


def _format_file_size(size_bytes: int) -> str:
    normalized_bytes = max(0, size_bytes)
    if normalized_bytes < 1024:
        return f"{normalized_bytes} B"
    if normalized_bytes < 1024 * 1024:
        return f"{normalized_bytes / 1024:.1f} KB"
    return f"{normalized_bytes / (1024 * 1024):.2f} MB"


def _snapshot_scheduled_at(snapshot: dict[str, object]):
    value = snapshot.get("scheduled_at")
    if not isinstance(value, str) or not value:
        return None
    from app.core.time import parse_api_datetime

    return parse_api_datetime(value)


def _ensure_same_idempotent_request(
    plan: AgentActionPlan,
    request_fingerprint: str,
) -> None:
    if plan.request_fingerprint != request_fingerprint:
        raise AgentApiError(
            status_code=409,
            code="IDEMPOTENCY_KEY_REUSED",
            message="同一个 Idempotency-Key 已用于不同的发送计划请求。",
        )


def _normalize_idempotency_key(value: str | None) -> str | None:
    normalized = (value or "").strip()
    if not normalized:
        return None
    if len(normalized) > 160:
        raise AgentApiError(
            status_code=400,
            code="INVALID_IDEMPOTENCY_KEY",
            message="Idempotency-Key 不能超过 160 个字符。",
        )
    return normalized


def _new_plan_id() -> str:
    return f"plan_{secrets.token_urlsafe(18)}"


def _fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _plan_expired_error(plan: AgentActionPlan) -> AgentApiError:
    return AgentApiError(
        status_code=409,
        code="PLAN_EXPIRED",
        message="发送计划已过期，请重新生成并向用户展示新的计划。",
        details={"expired_at": serialize_api_datetime(plan.expires_at)},
        suggested_command=(
            f"auto-email-sender drafts prepare-send {plan.email_task_id}"
        ),
    )
