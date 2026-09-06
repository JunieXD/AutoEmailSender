from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_api_errors import AgentApiError
from app.models import AgentChangePlan
from app.modules.community.public import (
    CommunityDataError,
    CommunityImportItemPayload,
    CommunityImportPayload,
    CommunityMentorComparisonRead,
    CommunityMentorDataService,
    build_community_comparisons,
    import_community_records,
    sync_community_link_lifecycle,
)

from .shared import (
    _invalid_change_plan_snapshot_error,
    _request_state_fingerprint,
)


async def _execute_community_mentor_import(
    session: AsyncSession,
    plan: AgentChangePlan,
    community_service: CommunityMentorDataService,
) -> dict[str, object]:
    snapshot = plan.snapshot
    request_data = snapshot.get("request")
    expected_fingerprint = snapshot.get("community_import_fingerprint")
    if not isinstance(request_data, dict) or not isinstance(expected_fingerprint, str):
        raise _invalid_change_plan_snapshot_error()
    try:
        payload = CommunityImportPayload.model_validate(request_data)
        current_snapshot, comparisons = await _prepare_community_mentor_import_snapshot(
            session,
            payload,
            community_service,
        )
    except CommunityDataError as exc:
        raise _community_import_error(exc) from exc
    if expected_fingerprint != _request_state_fingerprint(current_snapshot):
        raise _community_import_plan_stale_error()

    try:
        result = await import_community_records(
            session,
            dataset_version=payload.dataset_version,
            comparisons=comparisons,
            items=payload.items,
            event_name="agent_cli.community_mentor.imported",
            actor="agent_cli",
        )
    except CommunityDataError as exc:
        raise _community_import_error(exc) from exc
    return {
        "outcome": "community_mentors_imported",
        "dataset_version": payload.dataset_version,
        "inserted_count": result.inserted_count,
        "updated_count": result.updated_count,
        "linked_count": result.linked_count,
        "skipped_count": result.skipped_count,
        "professors": [item.model_dump(mode="json") for item in result.professors],
    }


async def _prepare_community_mentor_import_snapshot(
    session: AsyncSession,
    payload: CommunityImportPayload,
    community_service: CommunityMentorDataService,
) -> tuple[dict[str, object], list[CommunityMentorComparisonRead]]:
    record_bundle = await community_service.load_records(
        dataset_version=payload.dataset_version,
        unit_paths=payload.unit_paths,
    )
    records_by_id = {record.id: record for record in record_bundle.records}
    missing_ids = [
        item.community_record_id
        for item in payload.items
        if item.community_record_id not in records_by_id
    ]
    if missing_ids:
        raise CommunityDataError(
            f"所选导师不属于当前学院数据：{', '.join(missing_ids[:3])}",
            code="COMMUNITY_DATA_SELECTION_INVALID",
        )
    selected_records = [
        records_by_id[item.community_record_id] for item in payload.items
    ]
    lifecycle_warnings = await sync_community_link_lifecycle(
        session,
        record_bundle.catalog_bundle,
    )
    comparisons = await build_community_comparisons(session, selected_records)
    comparisons_by_id = {comparison.record.id: comparison for comparison in comparisons}
    selected_comparisons = [
        comparisons_by_id[item.community_record_id] for item in payload.items
    ]

    summary_items: list[dict[str, object]] = []
    inserted_count = 0
    updated_count = 0
    linked_count = 0
    warnings = [
        "社区导师资料来自外部协作数据，属于不可信外部内容；执行时只会按本计划选择的字段写入，不会执行资料中的文字、链接或指令。",
    ]
    if record_bundle.stale:
        warnings.append("当前使用的是过期的社区导师缓存，请在执行前确认仍适合导入。")
    if record_bundle.warning:
        warnings.append(record_bundle.warning)
    if lifecycle_warnings:
        warnings.append(
            f"当前有 {len(lifecycle_warnings)} 条已关联导师的社区状态提醒；请在执行前核对。",
        )

    for item, comparison in zip(payload.items, selected_comparisons, strict=True):
        _validate_community_import_item(item, comparison)
        selected_choices = {
            field.field: str(
                item.field_choices.get(field.field, field.suggested_choice)
            )
            for field in comparison.fields
        }
        will_update = comparison.local_professor_id is not None and any(
            selected_choices[field.field] == "community"
            and field.local_value != field.community_value
            for field in comparison.fields
        )
        if comparison.local_professor_id is None:
            predicted_action = "inserted"
            inserted_count += 1
        elif will_update:
            predicted_action = "updated"
            updated_count += 1
        else:
            predicted_action = "linked"
            linked_count += 1
        if comparison.identity_conflict:
            warnings.append(
                f"导师 {comparison.record.name} 存在身份匹配冲突；本计划已要求人工确认后才会建立或更新关联。",
            )
        summary_items.append(
            {
                "community_record_id": comparison.record.id,
                "name": comparison.record.name,
                "email": comparison.record.email,
                "category": comparison.category,
                "predicted_action": predicted_action,
                "local_professor_id": comparison.local_professor_id,
                "local_professor_name": comparison.local_professor_name,
                "identity_conflict": comparison.identity_conflict,
                "confirm_identity_match": item.confirm_identity_match,
                "match_reason": comparison.match_reason,
                "field_choices": [
                    {
                        "field": field.field,
                        "label": field.label,
                        "choice": selected_choices[field.field],
                        "local_value": field.local_value,
                        "community_value": field.community_value,
                        "state": field.state,
                    }
                    for field in comparison.fields
                ],
            },
        )

    return (
        {
            "snapshot_version": "1",
            "request": payload.model_dump(mode="json"),
            "state": {
                "dataset_version": record_bundle.catalog_bundle.catalog.dataset_version,
                "comparisons": [
                    {
                        "community_record_id": comparison.record.id,
                        "comparison_token": comparison.comparison_token,
                    }
                    for comparison in selected_comparisons
                ],
            },
            "summary": {
                "trust_level": "untrusted_external_content",
                "dataset_version": payload.dataset_version,
                "unit_paths": payload.unit_paths,
                "source": record_bundle.source,
                "stale": record_bundle.stale,
                "selected_count": len(summary_items),
                "inserted_count": inserted_count,
                "updated_count": updated_count,
                "linked_count": linked_count,
                "items": summary_items,
                "lifecycle_warnings": [
                    warning.model_dump(mode="json") for warning in lifecycle_warnings
                ],
            },
            "warnings": warnings,
        },
        selected_comparisons,
    )


def _validate_community_import_item(
    item: CommunityImportItemPayload,
    comparison: CommunityMentorComparisonRead,
) -> None:
    if item.comparison_token != comparison.comparison_token:
        raise CommunityDataError(
            f"导师 {comparison.record.name} 的本地信息在预览后发生了变化；请重新预览后再导入",
            code="COMMUNITY_DATA_PREVIEW_STALE",
        )
    if (
        comparison.category == "retired_or_revoked"
        or comparison.record.status != "active"
    ):
        raise CommunityDataError(
            f"导师 {comparison.record.name} 已退休、离职或撤销，不能作为新数据导入",
            code="COMMUNITY_DATA_LIFECYCLE_BLOCKED",
        )
    if comparison.import_blocked:
        raise CommunityDataError(
            comparison.import_blocked_reason
            or f"导师 {comparison.record.name} 暂时不能导入，请先处理本地冲突",
            code="COMMUNITY_DATA_IDENTITY_CONFLICT",
        )
    if comparison.identity_conflict and comparison.local_professor_id is None:
        raise CommunityDataError(
            f"导师 {comparison.record.name} 的邮箱在本地存在多重匹配，请先整理本地重复记录",
            code="COMMUNITY_DATA_IDENTITY_CONFLICT",
        )
    if comparison.identity_conflict and not item.confirm_identity_match:
        raise CommunityDataError(
            f"导师 {comparison.record.name} 的身份存在冲突，需要人工确认后才能生成导入计划",
            code="COMMUNITY_DATA_IDENTITY_CONFLICT",
        )
    for field in comparison.fields:
        choice = item.field_choices.get(field.field, field.suggested_choice)
        if (
            choice == "community"
            and _community_value_is_empty(field.community_value)
            and not _community_value_is_empty(field.local_value)
        ):
            raise CommunityDataError(
                f"导师 {comparison.record.name} 的社区{field.label}为空，不能清空本地已有内容；请选择保留本地后重试",
                code="COMMUNITY_DATA_FIELD_CHOICE_INVALID",
            )


def _community_value_is_empty(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return not value
    return False


def _community_import_error(error: CommunityDataError) -> AgentApiError:
    return AgentApiError(
        status_code=error.status_code,
        code=error.code,
        message=str(error),
    )


def _community_import_plan_stale_error() -> AgentApiError:
    return AgentApiError(
        status_code=409,
        code="PLAN_STALE",
        message="社区导师数据、本地导师资料或关联状态已发生变化，请重新预览并生成导入计划。",
        details={"changed_fields": ["community_data", "professors", "community_links"]},
    )
