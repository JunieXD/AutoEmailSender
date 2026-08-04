from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.time import serialize_api_datetime, utc_now
from app.models import Professor, ProfessorTag, ProfessorTagLink
from app.schemas.professor import (
    ProfessorBulkTagsPayload,
    ProfessorTagPayload,
    ProfessorTagUpdatePayload,
    ProfessorUpsertPayload,
)
from app.services.operation_logs import record_operation_log
from app.services.professor_management import (
    ParsedProfessorImport,
    is_valid_professor_email,
    normalize_professor_payload,
)


DEFAULT_IMPORTED_TAG_TEXT_COLOR = "#166534"
DEFAULT_IMPORTED_TAG_BACKGROUND_COLOR = "#dcfce7"


@dataclass(slots=True)
class ProfessorMutationError(ValueError):
    status_code: int
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class ProfessorImportMutationResult:
    inserted_count: int
    updated_count: int
    created_tag_count: int
    failed_count: int


async def create_professor_record(
    session: AsyncSession,
    payload: ProfessorUpsertPayload,
    *,
    event_name: str,
    actor: str,
) -> Professor:
    professor_data = normalize_professor_payload(payload)
    ensure_professor_email_valid(professor_data["email"])
    existing = await session.scalar(
        select(Professor).where(Professor.email == professor_data["email"]),
    )
    if existing is not None:
        raise ProfessorMutationError(
            409,
            "PROFESSOR_EMAIL_CONFLICT",
            "该邮箱的导师已存在",
        )

    professor = Professor(**professor_data)
    session.add(professor)
    await session.flush()
    await sync_professor_tags(session, professor, payload.tag_ids)
    await record_professor_event(session, professor, event_name, actor=actor)
    return await get_professor_with_tags_or_raise(session, professor.id)


async def update_professor_record(
    session: AsyncSession,
    professor_id: int,
    payload: ProfessorUpsertPayload,
    *,
    event_name: str,
    actor: str,
) -> Professor:
    professor = await get_professor_with_tags_or_raise(session, professor_id)
    professor_data = normalize_professor_payload(payload)
    ensure_professor_email_valid(professor_data["email"])
    existing = await session.scalar(
        select(Professor).where(
            Professor.email == professor_data["email"],
            Professor.id != professor_id,
        ),
    )
    if existing is not None:
        raise ProfessorMutationError(
            409,
            "PROFESSOR_EMAIL_CONFLICT",
            "该邮箱已被其他导师使用",
        )

    professor.name = professor_data["name"]
    professor.email = professor_data["email"]
    professor.title = professor_data["title"]
    professor.university = professor_data["university"]
    professor.school = professor_data["school"]
    professor.department = professor_data["department"]
    professor.research_direction = professor_data["research_direction"]
    professor.recent_papers = professor_data["recent_papers"]
    professor.profile_url = professor_data["profile_url"]
    professor.source_url = professor_data["source_url"]
    if "personal_note" in payload.model_fields_set:
        professor.personal_note = professor_data["personal_note"]
    await sync_professor_tags(session, professor, payload.tag_ids)
    professor.updated_at = utc_now()
    await record_professor_event(session, professor, event_name, actor=actor)
    return await get_professor_with_tags_or_raise(session, professor.id)


async def archive_professor_record(
    session: AsyncSession,
    professor_id: int,
    *,
    event_name: str,
    actor: str,
) -> tuple[Professor, int]:
    professor = await get_professor_with_tags_or_raise(session, professor_id)
    affected_count = 0
    if professor.archived_at is None:
        now = utc_now()
        professor.archived_at = now
        professor.updated_at = now
        affected_count = 1
    await record_professor_event(
        session,
        professor,
        event_name,
        actor=actor,
        metadata={"affected_count": affected_count},
    )
    return professor, affected_count


async def restore_professor_record(
    session: AsyncSession,
    professor_id: int,
    *,
    event_name: str,
    actor: str,
) -> tuple[Professor, int]:
    professor = await get_professor_with_tags_or_raise(session, professor_id)
    affected_count = 0
    if professor.archived_at is not None:
        professor.archived_at = None
        professor.updated_at = utc_now()
        affected_count = 1
    await record_professor_event(
        session,
        professor,
        event_name,
        actor=actor,
        metadata={"affected_count": affected_count},
    )
    return professor, affected_count


async def get_professor_tag_usage_snapshot(
    session: AsyncSession,
    tag_id: int,
) -> dict[str, object]:
    tag, professors = await _load_professor_tag_with_usage(session, tag_id)
    return {
        "tag": _serialize_tag_snapshot(tag),
        "professors": [
            {
                "id": professor.id,
                "name": professor.name,
                "email": professor.email,
                "university": professor.university,
                "school": professor.school,
            }
            for professor in professors
        ],
    }


async def prepare_professor_tag_delete_snapshot(
    session: AsyncSession,
    tag_id: int,
) -> dict[str, object]:
    tag, professors = await _load_professor_tag_with_usage(session, tag_id)
    usage = {
        "tag": _serialize_tag_snapshot(tag),
        "professors": [
            {
                "id": professor.id,
                "name": professor.name,
                "email": professor.email,
                "university": professor.university,
                "school": professor.school,
            }
            for professor in professors
        ],
    }
    warnings = [
        "删除后该标签会从所有已关联导师中移除，无法自动恢复。",
    ]
    if professors:
        warnings.append(f"该标签当前关联 {len(professors)} 位导师。")
    return {
        "snapshot_version": "1",
        "request": {"tag_id": tag.id},
        "summary": {
            "tag": usage["tag"],
            "professor_count": len(professors),
            "professors": usage["professors"],
        },
        "warnings": warnings,
        "state": {
            "tag": _serialize_tag_snapshot(tag),
            "professors": [
                {
                    "id": professor.id,
                    "name": professor.name,
                    "email": professor.email,
                    "archived_at": serialize_api_datetime(professor.archived_at)
                    if professor.archived_at is not None
                    else None,
                    "updated_at": serialize_api_datetime(professor.updated_at),
                    "tags": [_serialize_tag_snapshot(item) for item in professor.tags],
                }
                for professor in professors
            ],
        },
    }


async def delete_professor_tag_record(
    session: AsyncSession,
    tag_id: int,
    *,
    event_name: str,
    actor: str,
) -> dict[str, object]:
    tag, professors = await _load_professor_tag_with_usage(session, tag_id)
    tag_name = tag.name
    professor_ids = [professor.id for professor in professors]
    await session.execute(delete(ProfessorTagLink).where(ProfessorTagLink.tag_id == tag.id))
    await session.delete(tag)
    await record_operation_log(
        session,
        category="user_action",
        event_name=event_name,
        entity_type="professor_tag",
        entity_id=str(tag_id),
        metadata={
            "actor": actor,
            "tag_name": tag_name,
            "affected_professor_count": len(professor_ids),
            "professor_ids": professor_ids,
        },
    )
    return {
        "tag_id": tag_id,
        "tag_name": tag_name,
        "affected_professor_count": len(professor_ids),
    }


async def prepare_bulk_professor_archive_snapshot(
    session: AsyncSession,
    professor_ids: list[int],
) -> dict[str, object]:
    ordered_ids, professors = await _resolve_bulk_professor_archive(session, professor_ids)
    professors_by_id = {professor.id: professor for professor in professors}
    items = [
        {
            "id": professors_by_id[professor_id].id,
            "name": professors_by_id[professor_id].name,
            "email": professors_by_id[professor_id].email,
            "archived_at": serialize_api_datetime(
                professors_by_id[professor_id].archived_at,
            )
            if professors_by_id[professor_id].archived_at is not None
            else None,
            "will_archive": professors_by_id[professor_id].archived_at is None,
        }
        for professor_id in ordered_ids
    ]
    affected_count = sum(item["will_archive"] for item in items)
    already_archived_count = len(items) - affected_count
    warnings = []
    if already_archived_count:
        warnings.append(f"其中 {already_archived_count} 位导师已在回收站中，不会重复移动。")
    return {
        "snapshot_version": "1",
        "request": {"professor_ids": ordered_ids},
        "summary": {
            "professor_count": len(items),
            "affected_count": affected_count,
            "already_archived_count": already_archived_count,
            "professors": items,
        },
        "warnings": warnings,
        "state": {
            "professors": [
                {
                    "id": professor.id,
                    "archived_at": serialize_api_datetime(professor.archived_at)
                    if professor.archived_at is not None
                    else None,
                    "updated_at": serialize_api_datetime(professor.updated_at),
                }
                for professor in sorted(professors, key=lambda item: item.id)
            ],
        },
    }


async def bulk_archive_professor_records(
    session: AsyncSession,
    professor_ids: list[int],
    *,
    event_name: str,
    actor: str,
) -> dict[str, object]:
    ordered_ids, professors = await _resolve_bulk_professor_archive(session, professor_ids)
    now = utc_now()
    affected_ids = [
        professor.id for professor in professors if professor.archived_at is None
    ]
    for professor in professors:
        if professor.id not in affected_ids:
            continue
        professor.archived_at = now
        professor.updated_at = now
    await record_operation_log(
        session,
        category="user_action",
        event_name=event_name,
        entity_type="professor",
        metadata={
            "actor": actor,
            "requested_count": len(ordered_ids),
            "affected_count": len(affected_ids),
            "ids": ordered_ids,
        },
    )
    return {
        "professor_ids": ordered_ids,
        "affected_count": len(affected_ids),
    }


async def set_professor_tags_record(
    session: AsyncSession,
    professor_id: int,
    payload: ProfessorTagUpdatePayload,
    *,
    event_name: str,
    actor: str,
) -> Professor:
    professor = await get_professor_with_tags_or_raise(session, professor_id)
    await sync_professor_tags(session, professor, payload.tag_ids)
    professor.updated_at = utc_now()
    await record_professor_event(session, professor, event_name, actor=actor)
    return await get_professor_with_tags_or_raise(session, professor.id)


async def prepare_bulk_professor_tags_snapshot(
    session: AsyncSession,
    payload: ProfessorBulkTagsPayload,
) -> dict[str, object]:
    """Build a user-visible, non-mutating preview for a batch tag update."""

    professor_ids, professors, target_tags = await _resolve_bulk_tag_update(
        session,
        payload,
    )
    target_tag_ids = [tag.id for tag in target_tags]
    professors_by_id = {professor.id: professor for professor in professors}
    preview_professors: list[dict[str, object]] = []
    changed_count = 0
    archived_count = 0
    for professor_id in professor_ids:
        professor = professors_by_id[professor_id]
        current_tags = [_serialize_tag_snapshot(tag) for tag in professor.tags]
        next_tag_ids = _next_bulk_tag_ids(
            [tag.id for tag in professor.tags],
            mode=payload.mode,
            target_tag_ids=target_tag_ids,
        )
        next_tags = _tags_for_ids(next_tag_ids, professor.tags, target_tags)
        will_change = [tag["id"] for tag in current_tags] != next_tag_ids
        if will_change:
            changed_count += 1
        if professor.archived_at is not None:
            archived_count += 1
        preview_professors.append(
            {
                "id": professor.id,
                "name": professor.name,
                "email": professor.email,
                "archived_at": serialize_api_datetime(professor.archived_at)
                if professor.archived_at is not None
                else None,
                "current_tags": current_tags,
                "next_tags": next_tags,
                "will_change": will_change,
            },
        )

    warnings: list[str] = []
    if payload.mode == "replace" and not target_tags:
        warnings.append(f"这会清空 {len(professor_ids)} 位导师的全部标签。")
    if archived_count:
        warnings.append(f"其中 {archived_count} 位导师已归档，其标签也会被修改。")
    unchanged_count = len(professor_ids) - changed_count
    if unchanged_count:
        warnings.append(f"其中 {unchanged_count} 位导师的标签不会发生变化。")

    return {
        "snapshot_version": "1",
        "request": {
            "professor_ids": professor_ids,
            "mode": payload.mode,
            "tag_ids": target_tag_ids,
        },
        "summary": {
            "mode": payload.mode,
            "professor_count": len(professor_ids),
            "changed_count": changed_count,
            "unchanged_count": unchanged_count,
            "target_tags": [_serialize_tag_snapshot(tag) for tag in target_tags],
            "professors": preview_professors,
        },
        "warnings": warnings,
    }


async def bulk_update_professor_tags_record(
    session: AsyncSession,
    payload: ProfessorBulkTagsPayload,
    *,
    event_name: str,
    actor: str,
) -> list[Professor]:
    """Apply a validated bulk tag update without committing the transaction."""

    professor_ids, professors, target_tags = await _resolve_bulk_tag_update(
        session,
        payload,
    )
    target_tag_ids = [tag.id for tag in target_tags]
    professors_by_id = {professor.id: professor for professor in professors}
    now = utc_now()
    for professor_id in professor_ids:
        professor = professors_by_id[professor_id]
        next_tag_ids = _next_bulk_tag_ids(
            [tag.id for tag in professor.tags],
            mode=payload.mode,
            target_tag_ids=target_tag_ids,
        )
        await sync_professor_tags(session, professor, next_tag_ids)
        professor.updated_at = now

    await session.flush()
    refreshed_professors = list(
        (
            await session.scalars(
                select(Professor)
                .options(selectinload(Professor.tags))
                .where(Professor.id.in_(professor_ids)),
            )
        ).unique(),
    )
    refreshed_by_id = {professor.id: professor for professor in refreshed_professors}
    ordered_professors = [refreshed_by_id[professor_id] for professor_id in professor_ids]
    await record_operation_log(
        session,
        category="user_action",
        event_name=event_name,
        entity_type="professor",
        metadata={
            "actor": actor,
            "requested_count": len(professor_ids),
            "affected_count": len(ordered_professors),
            "ids": professor_ids,
            "mode": payload.mode,
            "tag_ids": target_tag_ids,
        },
    )
    return ordered_professors


async def prepare_professor_import_snapshot(
    session: AsyncSession,
    parsed: ParsedProfessorImport,
    *,
    filename: str,
) -> dict[str, object]:
    """Build a stable, user-visible preview for a parsed spreadsheet import."""

    existing_by_email = await _load_existing_import_professors(session, parsed)
    tag_names = _collect_import_tag_names(parsed)
    existing_tags = list(
        await session.scalars(
            select(ProfessorTag).where(ProfessorTag.name.in_(tag_names)),
        ),
    ) if tag_names else []
    existing_tag_names = {tag.name for tag in existing_tags}

    inserted_count = 0
    updated_count = 0
    restored_count = 0
    items: list[dict[str, object]] = []
    state: list[dict[str, object]] = []
    for email in sorted(parsed.data):
        item = parsed.data[email]
        existing = existing_by_email.get(email)
        operation = "update" if existing is not None else "insert"
        if existing is None:
            inserted_count += 1
        else:
            updated_count += 1
            if existing.archived_at is not None:
                restored_count += 1
        current_tags = (
            [_serialize_tag_snapshot(tag) for tag in existing.tags]
            if existing is not None
            else []
        )
        item_tag_names = [str(tag_name) for tag_name in item.get("tag_names", [])]
        items.append(
            {
                "id": existing.id if existing is not None else None,
                "name": item["name"],
                "email": email,
                "operation": operation,
                "current_tags": current_tags,
                "imported_tag_names": item_tag_names,
                "will_restore": bool(existing and existing.archived_at is not None),
            },
        )
        state.append(
            {
                "email": email,
                "professor": _serialize_import_professor_state(existing),
            },
        )

    warnings: list[str] = []
    if updated_count:
        warnings.append(f"导入会覆盖 {updated_count} 位已有导师的表格字段。")
    if restored_count:
        warnings.append(f"其中 {restored_count} 位已归档导师会恢复到正常列表。")
    if parsed.failed_count:
        warnings.append(f"文件中有 {parsed.failed_count} 行无效数据会被跳过。")
    if tag_names and len(tag_names) > len(existing_tag_names):
        warnings.append(f"会新建 {len(tag_names) - len(existing_tag_names)} 个导师标签。")

    return {
        "snapshot_version": "1",
        "request": {
            "filename": filename,
            "data": parsed.data,
            "failed_count": parsed.failed_count,
        },
        "summary": {
            "filename": filename,
            "total_count": len(parsed.data),
            "inserted_count": inserted_count,
            "updated_count": updated_count,
            "restored_count": restored_count,
            "created_tag_count": len(tag_names) - len(existing_tag_names),
            "failed_count": parsed.failed_count,
            "items": items,
        },
        "warnings": warnings,
        "state": {
            "professors": state,
            "tags": [_serialize_tag_snapshot(tag) for tag in sorted(existing_tags, key=lambda item: item.id)],
        },
    }


async def import_professor_records(
    session: AsyncSession,
    parsed: ParsedProfessorImport,
    *,
    filename: str,
    event_name: str,
    actor: str,
) -> ProfessorImportMutationResult:
    """Apply parsed import data with the same merge rules used by the desktop UI."""

    existing_by_email = await _load_existing_import_professors(session, parsed)
    inserted_count = 0
    updated_count = 0
    created_tag_count = 0
    personal_note_column_present = any(
        bool(payload.get("has_personal_note_column"))
        for payload in parsed.data.values()
    )
    for email, payload in parsed.data.items():
        professor = existing_by_email.get(email)
        if professor is None:
            professor_data = {
                key: value
                for key, value in payload.items()
                if key not in {"tag_names", "has_personal_note_column"}
            }
            professor = Professor(**professor_data)
            session.add(professor)
            await session.flush()
            created_tag_count += await sync_professor_tags_by_names(
                session,
                professor,
                [str(tag_name) for tag_name in payload["tag_names"]],
            )
            inserted_count += 1
            continue

        professor.name = payload["name"]
        professor.email = payload["email"]
        professor.title = payload["title"]
        professor.university = payload["university"]
        professor.school = payload["school"]
        professor.department = payload["department"]
        professor.research_direction = payload["research_direction"]
        professor.recent_papers = payload["recent_papers"]
        professor.profile_url = payload["profile_url"]
        professor.source_url = payload["source_url"]
        if payload.get("has_personal_note_column"):
            professor.personal_note = payload["personal_note"]
        tag_names = [str(tag_name) for tag_name in payload["tag_names"]]
        if tag_names:
            created_tag_count += await sync_professor_tags_by_names(
                session,
                professor,
                tag_names,
            )
        professor.archived_at = None
        professor.updated_at = utc_now()
        updated_count += 1

    await record_operation_log(
        session,
        category="user_action",
        event_name=event_name,
        entity_type="professor",
        metadata={
            "actor": actor,
            "filename": filename,
            "inserted_count": inserted_count,
            "updated_count": updated_count,
            "created_tag_count": created_tag_count,
            "failed_count": parsed.failed_count,
            "row_count": len(parsed.data),
            "personal_note_column_present": personal_note_column_present,
        },
    )
    return ProfessorImportMutationResult(
        inserted_count=inserted_count,
        updated_count=updated_count,
        created_tag_count=created_tag_count,
        failed_count=parsed.failed_count,
    )


async def create_professor_tag_record(
    session: AsyncSession,
    payload: ProfessorTagPayload,
    *,
    event_name: str,
    actor: str,
) -> ProfessorTag:
    name = payload.name.strip()
    if not name:
        raise ProfessorMutationError(400, "TAG_NAME_REQUIRED", "标签名不能为空")
    existing = await session.scalar(select(ProfessorTag).where(ProfessorTag.name == name))
    if existing is not None:
        raise ProfessorMutationError(409, "PROFESSOR_TAG_EXISTS", "标签已存在")
    tag = ProfessorTag(
        name=name,
        text_color=payload.text_color,
        background_color=payload.background_color,
    )
    session.add(tag)
    await session.flush()
    await record_operation_log(
        session,
        category="user_action",
        event_name=event_name,
        entity_type="professor_tag",
        entity_id=str(tag.id),
        metadata={"actor": actor, "name": tag.name},
    )
    return tag


async def get_professor_with_tags_or_raise(
    session: AsyncSession,
    professor_id: int,
) -> Professor:
    professor = await session.scalar(
        select(Professor)
        .options(selectinload(Professor.tags))
        .where(Professor.id == professor_id),
    )
    if professor is None:
        raise ProfessorMutationError(404, "PROFESSOR_NOT_FOUND", "未找到导师")
    return professor


async def sync_professor_tags(
    session: AsyncSession,
    professor: Professor,
    tag_ids: list[int],
) -> None:
    tags = await load_tags_by_ids(session, tag_ids)
    await session.execute(
        delete(ProfessorTagLink).where(ProfessorTagLink.professor_id == professor.id),
    )
    for sort_order, tag in enumerate(tags):
        session.add(
            ProfessorTagLink(
                professor_id=professor.id,
                tag_id=tag.id,
                sort_order=sort_order,
            ),
        )
    session.expire(professor, ["tags"])


async def load_tags_by_ids(
    session: AsyncSession,
    tag_ids: list[int],
) -> list[ProfessorTag]:
    if not tag_ids:
        return []
    if len(set(tag_ids)) != len(tag_ids):
        raise ProfessorMutationError(400, "TAG_IDS_DUPLICATE", "标签不能重复")
    tags = list(
        await session.scalars(
            select(ProfessorTag)
            .where(ProfessorTag.id.in_(tag_ids))
            .order_by(ProfessorTag.name.asc()),
        ),
    )
    tags_by_id = {tag.id: tag for tag in tags}
    missing = [tag_id for tag_id in tag_ids if tag_id not in tags_by_id]
    if missing:
        raise ProfessorMutationError(400, "TAG_NOT_FOUND", "标签不存在")
    return [tags_by_id[tag_id] for tag_id in tag_ids]


async def _load_professor_tag_with_usage(
    session: AsyncSession,
    tag_id: int,
) -> tuple[ProfessorTag, list[Professor]]:
    tag = await session.get(ProfessorTag, tag_id)
    if tag is None:
        raise ProfessorMutationError(404, "PROFESSOR_TAG_NOT_FOUND", "未找到标签")
    professors = list(
        (
            await session.scalars(
                select(Professor)
                .options(selectinload(Professor.tags))
                .join(
                    ProfessorTagLink,
                    ProfessorTagLink.professor_id == Professor.id,
                )
                .where(ProfessorTagLink.tag_id == tag_id)
                .order_by(Professor.name.asc(), Professor.id.asc()),
            )
        ).unique(),
    )
    return tag, professors


async def _resolve_bulk_professor_archive(
    session: AsyncSession,
    professor_ids: list[int],
) -> tuple[list[int], list[Professor]]:
    if not professor_ids:
        raise ProfessorMutationError(400, "PROFESSOR_IDS_REQUIRED", "请至少选择一位导师")
    if len(set(professor_ids)) != len(professor_ids):
        raise ProfessorMutationError(400, "PROFESSOR_IDS_DUPLICATE", "导师 ID 不能重复")
    ordered_ids = list(professor_ids)
    professors = list(
        await session.scalars(select(Professor).where(Professor.id.in_(ordered_ids))),
    )
    found_ids = {professor.id for professor in professors}
    if any(professor_id not in found_ids for professor_id in ordered_ids):
        raise ProfessorMutationError(404, "PROFESSOR_NOT_FOUND", "导师不存在")
    return ordered_ids, professors


async def _resolve_bulk_tag_update(
    session: AsyncSession,
    payload: ProfessorBulkTagsPayload,
) -> tuple[list[int], list[Professor], list[ProfessorTag]]:
    if not payload.professor_ids:
        raise ProfessorMutationError(400, "PROFESSOR_IDS_REQUIRED", "请至少选择一位导师")
    if payload.mode in {"add", "remove"} and not payload.tag_ids:
        raise ProfessorMutationError(400, "TAG_IDS_REQUIRED", "请选择要追加或移除的标签")

    professor_ids = list(dict.fromkeys(payload.professor_ids))
    professors = list(
        (
            await session.scalars(
                select(Professor)
                .options(selectinload(Professor.tags))
                .where(Professor.id.in_(professor_ids)),
            )
        ).unique(),
    )
    professors_by_id = {professor.id: professor for professor in professors}
    if any(professor_id not in professors_by_id for professor_id in professor_ids):
        raise ProfessorMutationError(404, "PROFESSOR_NOT_FOUND", "导师不存在")
    return professor_ids, professors, await load_tags_by_ids(session, payload.tag_ids)


def _next_bulk_tag_ids(
    current_tag_ids: list[int],
    *,
    mode: str,
    target_tag_ids: list[int],
) -> list[int]:
    if mode == "add":
        return current_tag_ids + [
            tag_id for tag_id in target_tag_ids if tag_id not in current_tag_ids
        ]
    if mode == "remove":
        target_tag_id_set = set(target_tag_ids)
        return [tag_id for tag_id in current_tag_ids if tag_id not in target_tag_id_set]
    return target_tag_ids


def _tags_for_ids(
    tag_ids: list[int],
    current_tags: list[ProfessorTag],
    target_tags: list[ProfessorTag],
) -> list[dict[str, object]]:
    tags_by_id = {tag.id: tag for tag in [*current_tags, *target_tags]}
    return [_serialize_tag_snapshot(tags_by_id[tag_id]) for tag_id in tag_ids]


def _serialize_tag_snapshot(tag: ProfessorTag) -> dict[str, object]:
    return {
        "id": tag.id,
        "name": tag.name,
        "text_color": tag.text_color,
        "background_color": tag.background_color,
    }


async def sync_professor_tags_by_names(
    session: AsyncSession,
    professor: Professor,
    tag_names: list[str],
) -> int:
    tags, created_count = await load_or_create_tags_by_names(session, tag_names)
    await sync_professor_tags(session, professor, [tag.id for tag in tags])
    return created_count


async def load_or_create_tags_by_names(
    session: AsyncSession,
    tag_names: list[str],
) -> tuple[list[ProfessorTag], int]:
    normalized_names = list(dict.fromkeys(name.strip() for name in tag_names if name.strip()))
    if not normalized_names:
        return [], 0
    existing_tags = list(
        await session.scalars(
            select(ProfessorTag).where(ProfessorTag.name.in_(normalized_names)),
        ),
    )
    tags_by_name = {tag.name: tag for tag in existing_tags}
    created_count = 0
    for name in normalized_names:
        if name in tags_by_name:
            continue
        tag = ProfessorTag(
            name=name,
            text_color=DEFAULT_IMPORTED_TAG_TEXT_COLOR,
            background_color=DEFAULT_IMPORTED_TAG_BACKGROUND_COLOR,
        )
        session.add(tag)
        await session.flush()
        tags_by_name[name] = tag
        created_count += 1
    return [tags_by_name[name] for name in normalized_names], created_count


async def _load_existing_import_professors(
    session: AsyncSession,
    parsed: ParsedProfessorImport,
) -> dict[str, Professor]:
    if not parsed.data:
        return {}
    professors = list(
        (
            await session.scalars(
                select(Professor)
                .options(selectinload(Professor.tags))
                .where(Professor.email.in_(list(parsed.data.keys()))),
            )
        ).unique(),
    )
    return {
        professor.email.lower(): professor
        for professor in professors
        if professor.email
    }


def _collect_import_tag_names(parsed: ParsedProfessorImport) -> list[str]:
    return sorted(
        {
            str(tag_name).strip()
            for payload in parsed.data.values()
            for tag_name in payload.get("tag_names", [])
            if str(tag_name).strip()
        },
    )


def _serialize_import_professor_state(professor: Professor | None) -> dict[str, object] | None:
    if professor is None:
        return None
    return {
        "id": professor.id,
        "name": professor.name,
        "email": professor.email,
        "title": professor.title,
        "university": professor.university,
        "school": professor.school,
        "department": professor.department,
        "research_direction": professor.research_direction,
        "recent_papers": professor.recent_papers or [],
        "profile_url": professor.profile_url,
        "source_url": professor.source_url,
        "personal_note": professor.personal_note,
        "archived_at": serialize_api_datetime(professor.archived_at)
        if professor.archived_at is not None
        else None,
        "tags": [_serialize_tag_snapshot(tag) for tag in professor.tags],
    }


def ensure_professor_email_valid(email: str) -> None:
    if not is_valid_professor_email(email):
        raise ProfessorMutationError(400, "PROFESSOR_EMAIL_INVALID", "邮箱格式不正确")


async def record_professor_event(
    session: AsyncSession,
    professor: Professor,
    event_name: str,
    *,
    actor: str,
    metadata: dict[str, object] | None = None,
) -> None:
    event_metadata: dict[str, object] = {
        "actor": actor,
        "name": professor.name,
        "email": professor.email,
        "university": professor.university,
        "school": professor.school,
        "archived": professor.archived_at is not None,
    }
    if metadata:
        event_metadata.update(metadata)
    await record_operation_log(
        session,
        category="user_action",
        event_name=event_name,
        entity_type="professor",
        entity_id=str(professor.id),
        metadata=event_metadata,
    )
