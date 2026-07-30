from __future__ import annotations

from collections import defaultdict

from app.core.time import utc_now

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only, selectinload

from app.core.database import get_async_session
from app.models import (
    EmailTask,
    EmailTaskCancellationReason,
    EmailTaskStatus,
    Professor,
    ProfessorTag,
    ProfessorTagLink,
)
from app.schemas.professor import (
    ProfessorActionResult,
    ProfessorBulkArchivePayload,
    ProfessorBulkTagsPayload,
    ProfessorBulkTagsResult,
    ProfessorDashboardItemRead,
    ProfessorImportFileResult,
    ProfessorImportResult,
    ProfessorManagementItemRead,
    ProfessorNoteUpdatePayload,
    ProfessorNoteUpdateRead,
    ProfessorRead,
    ProfessorTagPayload,
    ProfessorTagRead,
    ProfessorTagUpdatePayload,
    ProfessorTagUsageProfessorRead,
    ProfessorTagUsageRead,
    ProfessorUpsertPayload,
)
from app.services.contact_status import build_contact_status_by_professor
from app.services.identity_communication_groups import resolve_identity_communication_scope
from app.services.operation_logs import record_operation_log
from app.services.professor_schedule import load_active_scheduled_professor_ids
from app.services.professor_management import (
    build_professor_export,
    build_professor_template,
    is_valid_professor_email,
    normalize_professor_payload,
    parse_professor_import_file,
)
from app.services.sample_professors import SAMPLE_PROFESSORS


router = APIRouter(prefix="/api/professors", tags=["professors"])
DEFAULT_IMPORTED_TAG_TEXT_COLOR = "#166534"
DEFAULT_IMPORTED_TAG_BACKGROUND_COLOR = "#dcfce7"


@router.get("", response_model=list[ProfessorDashboardItemRead])
async def list_professors(
    identity_id: int | None = None,
    llm_profile_id: int | None = None,
    ids: str | None = Query(default=None),
    session: AsyncSession = Depends(get_async_session),
) -> list[ProfessorDashboardItemRead]:
    statement = (
        select(Professor)
        .options(selectinload(Professor.tags))
        .where(Professor.archived_at.is_(None))
        .order_by(Professor.created_at.desc(), Professor.id.asc())
    )
    if ids:
        professor_ids = [int(item) for item in ids.split(",") if item.strip()]
        if not professor_ids:
            return []
        statement = statement.where(Professor.id.in_(professor_ids))

    professors = list((await session.execute(statement)).scalars())
    if not professors:
        return []

    professor_ids = [professor.id for professor in professors]
    tasks_by_professor: dict[int, list[EmailTask]] = defaultdict(list)
    contact_status_by_professor = {}
    active_scheduled_professor_ids: set[int] = set()

    if identity_id is not None:
        try:
            communication_scope = await resolve_identity_communication_scope(
                session,
                active_identity_id=identity_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        task_result = await session.execute(
            select(EmailTask)
            .options(
                load_only(
                    EmailTask.professor_id,
                    EmailTask.status,
                    EmailTask.created_at,
                    EmailTask.match_score,
                    EmailTask.sent_at,
                    EmailTask.is_replied,
                    EmailTask.updated_at,
                ),
            )
            .where(
                EmailTask.identity_id == identity_id,
                EmailTask.professor_id.in_(professor_ids),
                ~(
                    (EmailTask.status == EmailTaskStatus.CANCELED.value)
                    & (EmailTask.cancellation_reason == EmailTaskCancellationReason.USER_REMOVED.value)
                ),
            )
            .order_by(EmailTask.professor_id.asc(), EmailTask.created_at.desc(), EmailTask.id.desc()),
        )
        for task in task_result.scalars():
            tasks_by_professor[task.professor_id].append(task)
        contact_status_by_professor = await build_contact_status_by_professor(
            session,
            identity_id=identity_id,
            professor_ids=professor_ids,
            tasks_by_professor=tasks_by_professor,
            communication_identity_ids=communication_scope.identity_ids,
        )
        active_scheduled_professor_ids = await load_active_scheduled_professor_ids(
            session,
            identity_id=identity_id,
            professor_ids=professor_ids,
        )

    latest_match_task_by_professor: dict[int, EmailTask] = {}
    for professor_id, tasks in tasks_by_professor.items():
        latest_match = next((task for task in tasks if task.match_score is not None), None)
        if latest_match is not None:
            latest_match_task_by_professor[professor_id] = latest_match

    items: list[ProfessorDashboardItemRead] = []
    for professor in professors:
        contact_status = contact_status_by_professor.get(professor.id)
        latest_match_task = latest_match_task_by_professor.get(professor.id)
        items.append(
            ProfessorDashboardItemRead(
                id=professor.id,
                name=professor.name,
                email=professor.email,
                title=professor.title,
                university=professor.university,
                school=professor.school,
                department=professor.department,
                research_direction=professor.research_direction,
                recent_papers=professor.recent_papers or [],
                match_score=latest_match_task.match_score if latest_match_task else None,
                sent_count=contact_status.sent_count if contact_status else 0,
                status=contact_status.status if contact_status else "not_contacted",
                has_active_schedule=professor.id in active_scheduled_professor_ids,
                last_sent_at=contact_status.last_sent_at if contact_status else None,
                last_replied_at=contact_status.last_replied_at if contact_status else None,
                personal_note=professor.personal_note,
                tags=_serialize_professor_tags(professor),
            )
        )
    return items


@router.get("/management", response_model=list[ProfessorManagementItemRead])
async def list_professors_for_management(
    archived: str = Query(default="active"),
    session: AsyncSession = Depends(get_async_session),
) -> list[ProfessorManagementItemRead]:
    statement = (
        select(Professor)
        .options(selectinload(Professor.tags))
        .order_by(Professor.updated_at.desc(), Professor.created_at.desc())
    )
    statement = _apply_archived_filter(statement, archived)
    professors = list((await session.execute(statement)).scalars())
    return [_serialize_management_professor(professor) for professor in professors]


@router.get("/template")
async def download_professor_template(
    format: str = Query(default="xlsx"),
) -> Response:
    try:
        content, media_type, filename = build_professor_template(format)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.get("/export")
async def export_professors(
    format: str = Query(default="xlsx"),
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    professors = list(
        (
            await session.execute(
                select(Professor)
                .where(Professor.archived_at.is_(None))
                .order_by(Professor.updated_at.desc(), Professor.created_at.desc()),
            )
        ).scalars(),
    )
    try:
        content, media_type, filename = build_professor_export(professors, format)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.post("/import-file", response_model=ProfessorImportFileResult)
async def import_professors_from_file(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorImportFileResult:
    if not file.filename:
        raise HTTPException(status_code=400, detail="请选择要导入的文件")

    try:
        parsed = parse_professor_import_file(file.filename, await file.read())
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    inserted_count = 0
    updated_count = 0
    created_tag_count = 0
    personal_note_column_present = any(
        bool(payload.get("has_personal_note_column"))
        for payload in parsed.data.values()
    )
    if parsed.data:
        existing_professors = {
            professor.email.lower(): professor
            for professor in (
                await session.execute(
                    select(Professor).where(Professor.email.in_(list(parsed.data.keys()))),
                )
            ).scalars()
            if professor.email
        }

        for email, payload in parsed.data.items():
            professor = existing_professors.get(email)
            if professor is None:
                professor_data = {
                    key: value
                    for key, value in payload.items()
                    if key not in {"tag_names", "has_personal_note_column"}
                }
                professor = Professor(**professor_data)
                session.add(professor)
                await session.flush()
                created_tag_count += await _sync_professor_tags_by_names(
                    session,
                    professor,
                    payload["tag_names"],
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
            if payload["tag_names"]:
                created_tag_count += await _sync_professor_tags_by_names(
                    session,
                    professor,
                    payload["tag_names"],
                )
            professor.archived_at = None
            professor.updated_at = utc_now()
            updated_count += 1

    await record_operation_log(
        session,
        category="user_action",
        event_name="professor.import_file",
        entity_type="professor",
        metadata={
            "filename": file.filename,
            "inserted_count": inserted_count,
            "updated_count": updated_count,
            "created_tag_count": created_tag_count,
            "failed_count": parsed.failed_count,
            "row_count": len(parsed.data),
            "personal_note_column_present": personal_note_column_present,
        },
    )
    await session.commit()

    return ProfessorImportFileResult(
        inserted_count=inserted_count,
        updated_count=updated_count,
        failed_count=parsed.failed_count,
        message=(
            f"导入完成：新增 {inserted_count} 条，更新 {updated_count} 条，"
            f"创建标签 {created_tag_count} 个，失败 {parsed.failed_count} 条。"
        ),
    )


@router.get("/tags", response_model=list[ProfessorTagRead])
async def list_professor_tags(
    session: AsyncSession = Depends(get_async_session),
) -> list[ProfessorTagRead]:
    tags = list(
        (
            await session.execute(
                select(ProfessorTag).order_by(ProfessorTag.id.asc()),
            )
        ).scalars(),
    )
    return [_serialize_tag(tag) for tag in tags]


@router.get("/tags/{tag_id}/usage", response_model=ProfessorTagUsageRead)
async def get_professor_tag_usage(
    tag_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorTagUsageRead:
    tag = await session.get(ProfessorTag, tag_id)
    if tag is None:
        raise HTTPException(status_code=404, detail="未找到标签")

    professors = list(
        (
            await session.execute(
                select(Professor)
                .join(
                    ProfessorTagLink,
                    ProfessorTagLink.professor_id == Professor.id,
                )
                .where(ProfessorTagLink.tag_id == tag_id)
                .order_by(Professor.name.asc(), Professor.id.asc()),
            )
        ).scalars(),
    )
    return ProfessorTagUsageRead(
        tag=_serialize_tag(tag),
        professors=[
            ProfessorTagUsageProfessorRead(
                id=professor.id,
                name=professor.name,
                email=professor.email,
                university=professor.university,
                school=professor.school,
            )
            for professor in professors
        ],
    )


@router.post(
    "/tags",
    response_model=ProfessorTagRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_professor_tag(
    payload: ProfessorTagPayload,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorTagRead:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="标签名不能为空")
    existing = await session.scalar(select(ProfessorTag).where(ProfessorTag.name == name))
    if existing is not None:
        raise HTTPException(status_code=409, detail="标签已存在")

    tag = ProfessorTag(
        name=name,
        text_color=payload.text_color,
        background_color=payload.background_color,
    )
    session.add(tag)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="标签已存在") from exc
    await session.refresh(tag)
    return _serialize_tag(tag)


@router.delete("/tags/{tag_id}", response_model=ProfessorActionResult)
async def delete_professor_tag(
    tag_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorActionResult:
    tag = await session.get(ProfessorTag, tag_id)
    if tag is None:
        raise HTTPException(status_code=404, detail="未找到标签")

    await session.execute(delete(ProfessorTagLink).where(ProfessorTagLink.tag_id == tag_id))
    await session.delete(tag)
    await session.commit()
    return ProfessorActionResult(
        ok=True,
        affected_count=1,
        message="标签已删除",
    )


@router.patch("/{professor_id}/tags", response_model=ProfessorManagementItemRead)
async def update_professor_tags(
    professor_id: int,
    payload: ProfessorTagUpdatePayload,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorManagementItemRead:
    professor = await session.scalar(
        select(Professor)
        .options(selectinload(Professor.tags))
        .where(Professor.id == professor_id),
    )
    if not professor:
        raise HTTPException(status_code=404, detail="未找到导师")

    await _sync_professor_tags(session, professor, payload.tag_ids)
    professor.updated_at = utc_now()
    await _record_professor_log(session, professor, "professor.tags_updated")
    await session.commit()
    return _serialize_management_professor(
        await _get_professor_with_tags_or_404(session, professor.id),
    )


@router.post("", response_model=ProfessorManagementItemRead, status_code=status.HTTP_201_CREATED)
async def create_professor(
    payload: ProfessorUpsertPayload,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorManagementItemRead:
    professor_data = normalize_professor_payload(payload)
    _ensure_professor_email_valid(professor_data["email"])

    existing = await session.scalar(
        select(Professor).where(Professor.email == professor_data["email"]),
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="该邮箱的导师已存在")

    professor = Professor(**professor_data)
    session.add(professor)
    await session.flush()
    await _sync_professor_tags(session, professor, payload.tag_ids)
    await _record_professor_log(session, professor, "professor.created")
    await session.commit()
    return _serialize_management_professor(
        await _get_professor_with_tags_or_404(session, professor.id),
    )


@router.get("/{professor_id}", response_model=ProfessorRead)
async def get_professor(
    professor_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorRead:
    professor = await session.scalar(
        select(Professor)
        .options(selectinload(Professor.tags))
        .where(Professor.id == professor_id)
        .execution_options(populate_existing=True),
    )
    if not professor:
        raise HTTPException(status_code=404, detail="未找到导师")
    return ProfessorRead(
        id=professor.id,
        name=professor.name,
        email=professor.email,
        title=professor.title,
        university=professor.university,
        school=professor.school,
        department=professor.department,
        research_direction=professor.research_direction,
        recent_papers=professor.recent_papers,
        profile_url=professor.profile_url,
        source_url=professor.source_url,
        crawl_status=professor.crawl_status,
        skip_reason=professor.skip_reason,
        personal_note=professor.personal_note,
        archived_at=professor.archived_at,
        created_at=professor.created_at,
        updated_at=professor.updated_at,
        tags=_serialize_professor_tags(professor),
    )


@router.patch("/{professor_id}", response_model=ProfessorManagementItemRead)
async def update_professor(
    professor_id: int,
    payload: ProfessorUpsertPayload,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorManagementItemRead:
    professor = await session.scalar(
        select(Professor)
        .options(selectinload(Professor.tags))
        .where(Professor.id == professor_id),
    )
    if not professor:
        raise HTTPException(status_code=404, detail="未找到导师")

    professor_data = normalize_professor_payload(payload)
    _ensure_professor_email_valid(professor_data["email"])

    existing = await session.scalar(
        select(Professor).where(
            Professor.email == professor_data["email"],
            Professor.id != professor_id,
        ),
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="该邮箱已被其他导师使用")

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
    await _sync_professor_tags(session, professor, payload.tag_ids)
    professor.updated_at = utc_now()

    await _record_professor_log(session, professor, "professor.updated")
    await session.commit()
    return _serialize_management_professor(
        await _get_professor_with_tags_or_404(session, professor.id),
    )


@router.patch("/{professor_id}/note", response_model=ProfessorNoteUpdateRead)
async def update_professor_personal_note(
    professor_id: int,
    payload: ProfessorNoteUpdatePayload,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorNoteUpdateRead:
    professor = await session.get(Professor, professor_id)
    if not professor:
        raise HTTPException(status_code=404, detail="未找到导师")

    professor.personal_note = payload.personal_note
    professor.updated_at = utc_now()
    await _record_professor_log(
        session,
        professor,
        "professor.personal_note_updated",
        metadata=_build_personal_note_log_metadata(professor.personal_note),
    )
    await session.commit()
    await session.refresh(professor)
    return ProfessorNoteUpdateRead(
        id=professor.id,
        personal_note=professor.personal_note,
        updated_at=professor.updated_at,
    )


@router.post("/{professor_id}/archive", response_model=ProfessorActionResult)
async def archive_professor(
    professor_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorActionResult:
    professor = await session.get(Professor, professor_id)
    if not professor:
        raise HTTPException(status_code=404, detail="未找到导师")

    affected_count = 0
    if professor.archived_at is None:
        professor.archived_at = utc_now()
        professor.updated_at = utc_now()
        affected_count = 1
    await _record_professor_log(
        session,
        professor,
        "professor.archived",
        metadata={"affected_count": affected_count},
    )
    await session.commit()

    return ProfessorActionResult(
        ok=True,
        affected_count=affected_count,
        message="导师已移入回收站",
    )


@router.post("/bulk-archive", response_model=ProfessorActionResult)
async def bulk_archive_professors(
    payload: ProfessorBulkArchivePayload,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorActionResult:
    if not payload.ids:
        raise HTTPException(status_code=400, detail="请至少选择一位导师")

    professors = list(
        (
            await session.execute(
                select(Professor).where(Professor.id.in_(payload.ids)),
            )
        ).scalars()
    )

    affected_count = 0
    archive_time = utc_now()
    for professor in professors:
        if professor.archived_at is None:
            professor.archived_at = archive_time
            professor.updated_at = archive_time
            affected_count += 1

    await record_operation_log(
        session,
        category="user_action",
        event_name="professor.bulk_archived",
        entity_type="professor",
        metadata={
            "requested_count": len(payload.ids),
            "affected_count": affected_count,
            "ids": payload.ids,
        },
    )
    await session.commit()
    return ProfessorActionResult(
        ok=True,
        affected_count=affected_count,
        message=f"已将 {affected_count} 位导师移入回收站",
    )


@router.post("/bulk-tags", response_model=ProfessorBulkTagsResult)
async def bulk_update_professor_tags(
    payload: ProfessorBulkTagsPayload,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorBulkTagsResult:
    if not payload.professor_ids:
        raise HTTPException(status_code=400, detail="请至少选择一位导师")
    if payload.mode in {"add", "remove"} and not payload.tag_ids:
        raise HTTPException(status_code=400, detail="请选择要追加或移除的标签")

    requested_professor_ids = list(dict.fromkeys(payload.professor_ids))
    professors = list(
        (
            await session.execute(
                select(Professor)
                .options(selectinload(Professor.tags))
                .where(Professor.id.in_(requested_professor_ids)),
            )
        ).scalars(),
    )
    professors_by_id = {professor.id: professor for professor in professors}
    missing_professor_ids = [
        professor_id
        for professor_id in requested_professor_ids
        if professor_id not in professors_by_id
    ]
    if missing_professor_ids:
        raise HTTPException(status_code=404, detail="导师不存在")

    tags = await _load_tags_by_ids(session, payload.tag_ids)
    tag_ids = [tag.id for tag in tags]
    now = utc_now()
    for professor_id in requested_professor_ids:
        professor = professors_by_id[professor_id]
        current_tag_ids = [tag.id for tag in professor.tags]
        if payload.mode == "add":
            next_tag_ids = current_tag_ids + [
                tag_id for tag_id in tag_ids if tag_id not in current_tag_ids
            ]
        elif payload.mode == "remove":
            remove_tag_ids = set(tag_ids)
            next_tag_ids = [
                tag_id for tag_id in current_tag_ids if tag_id not in remove_tag_ids
            ]
        else:
            next_tag_ids = tag_ids
        await _sync_professor_tags(session, professor, next_tag_ids)
        professor.updated_at = now

    await session.flush()
    refreshed_professors = list(
        (
            await session.execute(
                select(Professor)
                .options(selectinload(Professor.tags))
                .where(Professor.id.in_(requested_professor_ids)),
            )
        ).scalars(),
    )
    refreshed_by_id = {
        professor.id: professor for professor in refreshed_professors
    }
    ordered_professors = [
        refreshed_by_id[professor_id]
        for professor_id in requested_professor_ids
    ]

    await record_operation_log(
        session,
        category="user_action",
        event_name="professor.bulk_tags_updated",
        entity_type="professor",
        metadata={
            "requested_count": len(requested_professor_ids),
            "affected_count": len(ordered_professors),
            "ids": requested_professor_ids,
            "mode": payload.mode,
            "tag_ids": tag_ids,
        },
    )
    await session.commit()
    return ProfessorBulkTagsResult(
        ok=True,
        affected_count=len(ordered_professors),
        professors=[
            _serialize_management_professor(professor)
            for professor in ordered_professors
        ],
        message=f"已更新 {len(ordered_professors)} 位导师的标签",
    )


@router.post("/{professor_id}/restore", response_model=ProfessorActionResult)
async def restore_professor(
    professor_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorActionResult:
    professor = await session.get(Professor, professor_id)
    if not professor:
        raise HTTPException(status_code=404, detail="未找到导师")

    affected_count = 0
    if professor.archived_at is not None:
        professor.archived_at = None
        professor.updated_at = utc_now()
        affected_count = 1
    await _record_professor_log(
        session,
        professor,
        "professor.restored",
        metadata={"affected_count": affected_count},
    )
    await session.commit()

    return ProfessorActionResult(
        ok=True,
        affected_count=affected_count,
        message="导师已恢复到正常列表",
    )


@router.post("/import-sample", response_model=ProfessorImportResult)
async def import_sample_professors(
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorImportResult:
    existing_emails = {
        email
        for email in (
            await session.execute(
                select(Professor.email).where(Professor.email.is_not(None)),
            )
        ).scalars()
    }

    inserted_count = 0
    for item in SAMPLE_PROFESSORS:
        email = item["email"]
        if isinstance(email, str) and email in existing_emails:
            continue
        professor = Professor(**item)
        session.add(professor)
        if isinstance(email, str):
            existing_emails.add(email)
        inserted_count += 1

    await record_operation_log(
        session,
        category="user_action",
        event_name="professor.import_sample",
        entity_type="professor",
        metadata={
            "inserted_count": inserted_count,
            "sample_count": len(SAMPLE_PROFESSORS),
        },
    )
    await session.commit()
    total_count = await session.scalar(select(func.count(Professor.id)))
    return ProfessorImportResult(
        inserted_count=inserted_count,
        total_count=total_count or 0,
        message="样例导师数据已导入",
    )


@router.post("/trigger-crawler")
async def trigger_crawler(
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, str]:
    await record_operation_log(
        session,
        category="crawler",
        event_name="crawler.trigger_requested",
        entity_type="crawler",
        metadata={"source": "professors.trigger_crawler"},
    )
    await session.commit()
    return {
        "status": "accepted",
        "message": "已接收智能抓取请求，当前版本先返回占位结果，后续可接入真实 crawler。",
    }


def _apply_archived_filter(statement, archived: str):
    normalized = archived.lower()
    if normalized == "active":
        return statement.where(Professor.archived_at.is_(None))
    if normalized == "archived":
        return statement.where(Professor.archived_at.is_not(None))
    if normalized == "all":
        return statement
    raise HTTPException(status_code=400, detail="archived 参数仅支持 active、archived、all")


def _ensure_professor_email_valid(email: str) -> None:
    if not is_valid_professor_email(email):
        raise HTTPException(status_code=400, detail="邮箱格式不正确")


async def _record_professor_log(
    session: AsyncSession,
    professor: Professor,
    event_name: str,
    *,
    metadata: dict[str, object] | None = None,
) -> None:
    base_metadata: dict[str, object] = {
        "name": professor.name,
        "email": professor.email,
        "university": professor.university,
        "school": professor.school,
        "archived": professor.archived_at is not None,
    }
    if metadata:
        base_metadata.update(metadata)
    await record_operation_log(
        session,
        category="user_action",
        event_name=event_name,
        entity_type="professor",
        entity_id=str(professor.id),
        metadata=base_metadata,
    )


def _serialize_management_professor(professor: Professor) -> ProfessorManagementItemRead:
    return ProfessorManagementItemRead(
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
        tags=_serialize_professor_tags(professor),
    )


def _serialize_tag(tag: ProfessorTag) -> ProfessorTagRead:
    return ProfessorTagRead(
        id=tag.id,
        name=tag.name,
        text_color=tag.text_color,
        background_color=tag.background_color,
    )


def _serialize_professor_tags(professor: Professor) -> list[ProfessorTagRead]:
    return [_serialize_tag(tag) for tag in professor.tags]


def _build_personal_note_log_metadata(personal_note: str | None) -> dict[str, object]:
    return {
        "has_personal_note": personal_note is not None,
        "personal_note_length": len(personal_note or ""),
    }


async def _get_professor_with_tags_or_404(
    session: AsyncSession,
    professor_id: int,
) -> Professor:
    professor = await session.scalar(
        select(Professor)
        .options(selectinload(Professor.tags))
        .where(Professor.id == professor_id),
    )
    if professor is None:
        raise HTTPException(status_code=404, detail="未找到导师")
    return professor


async def _sync_professor_tags(
    session: AsyncSession,
    professor: Professor,
    tag_ids: list[int],
) -> None:
    tags = await _load_tags_by_ids(session, tag_ids)
    await session.execute(
        delete(ProfessorTagLink).where(
            ProfessorTagLink.professor_id == professor.id,
        ),
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


async def _sync_professor_tags_by_names(
    session: AsyncSession,
    professor: Professor,
    tag_names: list[str],
) -> int:
    tags, created_count = await _load_or_create_tags_by_names(session, tag_names)
    await _sync_professor_tags(session, professor, [tag.id for tag in tags])
    return created_count


async def _load_or_create_tags_by_names(
    session: AsyncSession,
    tag_names: list[str],
) -> tuple[list[ProfessorTag], int]:
    if not tag_names:
        return [], 0

    existing_tags = list(
        (
            await session.execute(
                select(ProfessorTag).where(ProfessorTag.name.in_(tag_names)),
            )
        ).scalars(),
    )
    tags_by_name = {tag.name: tag for tag in existing_tags}
    created_count = 0
    for name in tag_names:
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

    return [tags_by_name[name] for name in tag_names], created_count


async def _load_tags_by_ids(
    session: AsyncSession,
    tag_ids: list[int],
) -> list[ProfessorTag]:
    if not tag_ids:
        return []

    if len(set(tag_ids)) != len(tag_ids):
        raise HTTPException(status_code=400, detail="标签不能重复")

    tags = list(
        (
            await session.execute(
                select(ProfessorTag)
                .where(ProfessorTag.id.in_(tag_ids))
                .order_by(ProfessorTag.name.asc()),
            )
        ).scalars(),
    )
    tags_by_id = {tag.id: tag for tag in tags}
    missing = [tag_id for tag_id in tag_ids if tag_id not in tags_by_id]
    if missing:
        raise HTTPException(status_code=400, detail="标签不存在")
    return [tags_by_id[tag_id] for tag_id in tag_ids]
