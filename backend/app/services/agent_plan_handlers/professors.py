from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_api_errors import AgentApiError
from app.models import AgentChangePlan
from app.modules.professors.public import (
    ParsedProfessorImport,
    ProfessorBulkTagsPayload,
    ProfessorMutationError,
    bulk_archive_professor_records,
    bulk_update_professor_tags_record,
    delete_professor_tag_record,
    import_professor_records,
    lock_professor_tag_for_delete,
    prepare_bulk_professor_archive_snapshot,
    prepare_bulk_professor_tags_snapshot,
    prepare_professor_import_snapshot,
    prepare_professor_tag_delete_snapshot,
)
from app.services.agent_mutations import fingerprint

from .shared import (
    _invalid_change_plan_snapshot_error,
    _request_state_summary_fingerprint,
)


async def _execute_professor_bulk_tags(
    session: AsyncSession,
    plan: AgentChangePlan,
) -> dict[str, object]:
    snapshot = plan.snapshot
    request_data = snapshot.get("request")
    expected_fingerprint = snapshot.get("bulk_tags_fingerprint")
    if not isinstance(request_data, dict) or not isinstance(expected_fingerprint, str):
        raise AgentApiError(
            status_code=500,
            code="INVALID_CHANGE_PLAN_SNAPSHOT",
            message="变更计划快照无效，请重新生成计划。",
        )
    try:
        payload = ProfessorBulkTagsPayload.model_validate(request_data)
        current_snapshot = await prepare_bulk_professor_tags_snapshot(session, payload)
    except ProfessorMutationError as exc:
        raise _bulk_tags_plan_stale_error() from exc
    if expected_fingerprint != _bulk_tags_snapshot_fingerprint(current_snapshot):
        raise _bulk_tags_plan_stale_error()

    try:
        result = await bulk_update_professor_tags_record(
            session,
            payload,
            event_name="agent_cli.professor.bulk_tags_updated",
            actor="agent_cli",
        )
    except ProfessorMutationError as exc:
        raise _bulk_tags_plan_stale_error() from exc
    summary = snapshot.get("summary")
    changed_count = summary.get("changed_count") if isinstance(summary, dict) else None
    return {
        "outcome": "tags_updated",
        "mode": payload.mode,
        "affected_count": result.affected_count,
        "changed_count": changed_count,
        "professor_ids": result.professor_ids,
    }


async def _execute_professor_bulk_archive(
    session: AsyncSession,
    plan: AgentChangePlan,
) -> dict[str, object]:
    snapshot = plan.snapshot
    request_data = snapshot.get("request")
    expected_fingerprint = snapshot.get("bulk_archive_fingerprint")
    professor_ids = (
        request_data.get("professor_ids") if isinstance(request_data, dict) else None
    )
    if (
        not isinstance(expected_fingerprint, str)
        or not isinstance(professor_ids, list)
        or any(
            not isinstance(professor_id, int) or isinstance(professor_id, bool)
            for professor_id in professor_ids
        )
    ):
        raise _invalid_change_plan_snapshot_error()
    try:
        current_snapshot = await prepare_bulk_professor_archive_snapshot(
            session,
            professor_ids,
        )
    except ProfessorMutationError as exc:
        raise _bulk_archive_plan_stale_error() from exc
    if expected_fingerprint != _request_state_summary_fingerprint(current_snapshot):
        raise _bulk_archive_plan_stale_error()
    try:
        result = await bulk_archive_professor_records(
            session,
            professor_ids,
            event_name="agent_cli.professor.bulk_archived",
            actor="agent_cli",
        )
    except ProfessorMutationError as exc:
        raise _bulk_archive_plan_stale_error() from exc
    return {"outcome": "professors_archived", **result}


async def _execute_professor_tag_delete(
    session: AsyncSession,
    plan: AgentChangePlan,
) -> dict[str, object]:
    snapshot = plan.snapshot
    request_data = snapshot.get("request")
    expected_fingerprint = snapshot.get("tag_delete_fingerprint")
    tag_id = request_data.get("tag_id") if isinstance(request_data, dict) else None
    if (
        not isinstance(expected_fingerprint, str)
        or not isinstance(tag_id, int)
        or isinstance(tag_id, bool)
    ):
        raise _invalid_change_plan_snapshot_error()
    try:
        await lock_professor_tag_for_delete(session, tag_id)
        current_snapshot = await prepare_professor_tag_delete_snapshot(session, tag_id)
    except ProfessorMutationError as exc:
        raise _tag_delete_plan_stale_error() from exc
    if expected_fingerprint != _request_state_summary_fingerprint(current_snapshot):
        raise _tag_delete_plan_stale_error()
    try:
        result = await delete_professor_tag_record(
            session,
            tag_id,
            event_name="agent_cli.professor.tag_deleted",
            actor="agent_cli",
        )
    except ProfessorMutationError as exc:
        raise _tag_delete_plan_stale_error() from exc
    return {"outcome": "tag_deleted", **result}


async def _execute_professor_import(
    session: AsyncSession,
    plan: AgentChangePlan,
) -> dict[str, object]:
    snapshot = plan.snapshot
    request_data = snapshot.get("request")
    expected_fingerprint = snapshot.get("import_fingerprint")
    if not isinstance(request_data, dict) or not isinstance(expected_fingerprint, str):
        raise AgentApiError(
            status_code=500,
            code="INVALID_CHANGE_PLAN_SNAPSHOT",
            message="变更计划快照无效，请重新生成计划。",
        )
    try:
        filename, parsed = _parsed_professor_import_from_snapshot(request_data)
        current_snapshot = await prepare_professor_import_snapshot(
            session,
            parsed,
            filename=filename,
        )
    except (ProfessorMutationError, ValueError) as exc:
        raise _professor_import_plan_stale_error() from exc
    if expected_fingerprint != _request_state_summary_fingerprint(current_snapshot):
        raise _professor_import_plan_stale_error()

    try:
        result = await import_professor_records(
            session,
            parsed,
            filename=filename,
            event_name="agent_cli.professor.imported",
            actor="agent_cli",
        )
    except ProfessorMutationError as exc:
        raise _professor_import_plan_stale_error() from exc
    return {
        "outcome": "imported",
        "inserted_count": result.inserted_count,
        "updated_count": result.updated_count,
        "created_tag_count": result.created_tag_count,
        "failed_count": result.failed_count,
    }


def _bulk_tags_snapshot_fingerprint(snapshot: dict[str, object]) -> str:
    return fingerprint(
        {
            "request": snapshot.get("request"),
            "summary": snapshot.get("summary"),
        },
    )


def _professor_error(error: ProfessorMutationError) -> AgentApiError:
    return AgentApiError(
        status_code=error.status_code,
        code=error.code,
        message=error.message,
    )


def _bulk_tags_plan_stale_error() -> AgentApiError:
    return AgentApiError(
        status_code=409,
        code="PLAN_STALE",
        message="计划中的导师或标签已发生变化，请重新生成批量标签预览。",
        details={"changed_fields": ["professors", "tags"]},
    )


def _bulk_archive_plan_stale_error() -> AgentApiError:
    return AgentApiError(
        status_code=409,
        code="PLAN_STALE",
        message="计划中的导师状态已发生变化，请重新生成批量归档预览。",
        details={"changed_fields": ["professors"]},
    )


def _tag_delete_plan_stale_error() -> AgentApiError:
    return AgentApiError(
        status_code=409,
        code="PLAN_STALE",
        message="标签或其关联导师已发生变化，请重新生成删除预览。",
        details={"changed_fields": ["tag", "professors", "tag_links"]},
    )


def _professor_import_error(error: ValueError) -> AgentApiError:
    return AgentApiError(
        status_code=400,
        code="PROFESSOR_IMPORT_INVALID",
        message=str(error),
    )


def _professor_import_plan_stale_error() -> AgentApiError:
    return AgentApiError(
        status_code=409,
        code="PLAN_STALE",
        message="导入目标的导师或标签已发生变化，请重新生成导入预览。",
        details={"changed_fields": ["professors", "tags"]},
    )


def _parsed_professor_import_from_snapshot(
    request_data: dict[str, object],
) -> tuple[str, ParsedProfessorImport]:
    filename = request_data.get("filename")
    data = request_data.get("data")
    failed_count = request_data.get("failed_count")
    if (
        not isinstance(filename, str)
        or not isinstance(data, dict)
        or not isinstance(failed_count, int)
        or isinstance(failed_count, bool)
        or failed_count < 0
    ):
        raise ValueError("导入计划数据无效")
    normalized_data: dict[str, object] = {}
    for email, payload in data.items():
        if not isinstance(email, str) or not isinstance(payload, dict):
            raise ValueError("导入计划数据无效")
        normalized_data[email] = payload
    return filename, ParsedProfessorImport(
        data=normalized_data,
        failed_count=failed_count,
    )
