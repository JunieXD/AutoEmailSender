from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_api_errors import AgentApiError
from app.models import AgentChangePlan
from app.modules.communications.public import (
    TestComposeMessageSendRequest,
    prepare_test_compose_send_snapshot,
    send_test_compose_message,
)

from .shared import (
    _invalid_change_plan_snapshot_error,
    _request_state_summary_fingerprint,
)


async def _execute_test_email_send(
    session: AsyncSession,
    plan: AgentChangePlan,
) -> dict[str, object]:
    snapshot = plan.snapshot
    expected_fingerprint = snapshot.get("test_email_send_fingerprint")
    if not isinstance(expected_fingerprint, str):
        raise _invalid_change_plan_snapshot_error()
    identity_id, llm_profile_id, payload = _test_email_send_request_from_snapshot(
        snapshot
    )
    try:
        current_snapshot = await prepare_test_compose_send_snapshot(
            session,
            identity_id=identity_id,
            llm_profile_id=llm_profile_id,
            payload=payload,
        )
    except ValueError as exc:
        raise _test_email_error(exc) from exc
    if expected_fingerprint != _request_state_summary_fingerprint(current_snapshot):
        raise _test_email_send_plan_stale_error()

    try:
        thread = await send_test_compose_message(
            session,
            identity_id=identity_id,
            llm_profile_id=llm_profile_id,
            payload=payload,
            commit=False,
            event_name="agent_cli.test_email",
            actor="agent_cli",
        )
    except ValueError as exc:
        raise _test_email_error(exc) from exc
    message = thread.history[0] if thread.history else None
    if message is None:
        raise AgentApiError(
            status_code=500,
            code="TEST_EMAIL_SEND_RESULT_MISSING",
            message="测试邮件发送后未找到结果，请在桌面端检查发送历史。",
        )
    return {
        "outcome": "sent" if message.status == "sent" else "failed",
        "identity_id": identity_id,
        "recipient_email": message.recipient_email,
        "message_id": message.id,
        "status": message.status,
        "rfc_message_id": message.rfc_message_id,
        "failure_summary": message.failure_summary,
    }


def _test_email_send_request_from_snapshot(
    snapshot: dict[str, object],
) -> tuple[int, int, TestComposeMessageSendRequest]:
    request_data = snapshot.get("request")
    if not isinstance(request_data, dict):
        raise _invalid_change_plan_snapshot_error()
    identity_id = request_data.get("identity_id")
    llm_profile_id = request_data.get("llm_profile_id")
    payload_data = request_data.get("payload")
    if (
        not isinstance(identity_id, int)
        or isinstance(identity_id, bool)
        or identity_id < 1
        or not isinstance(llm_profile_id, int)
        or isinstance(llm_profile_id, bool)
        or llm_profile_id < 1
        or not isinstance(payload_data, dict)
    ):
        raise _invalid_change_plan_snapshot_error()
    try:
        payload = TestComposeMessageSendRequest.model_validate(payload_data)
    except ValueError as exc:
        raise _invalid_change_plan_snapshot_error() from exc
    return identity_id, llm_profile_id, payload


def _test_email_error(error: ValueError) -> AgentApiError:
    message = str(error)
    return AgentApiError(
        status_code=404
        if "未找到" in message
        else 409
        if "尚未配置 SMTP" in message
        else 400,
        code=(
            "TEST_EMAIL_RESOURCE_NOT_FOUND"
            if "未找到" in message
            else "TEST_EMAIL_SMTP_REQUIRED"
            if "尚未配置 SMTP" in message
            else "TEST_EMAIL_INVALID"
        ),
        message=message,
    )


def _test_email_send_plan_stale_error() -> AgentApiError:
    return AgentApiError(
        status_code=409,
        code="PLAN_STALE",
        message="测试邮件的收件地址、正文、模板或附件已发生变化，请重新生成并展示发送计划。",
        details={"changed_fields": ["identity", "template", "attachments", "content"]},
    )
