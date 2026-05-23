from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.models import EmailDirection, EmailLog, EmailTask, EmailTaskCancellationReason, EmailTaskStatus, Professor
from app.schemas.professor import (
    ProfessorActionResult,
    ProfessorBulkArchivePayload,
    ProfessorDashboardItemRead,
    ProfessorImportFileResult,
    ProfessorImportResult,
    ProfessorManagementItemRead,
    ProfessorRead,
    ProfessorUpsertPayload,
)
from app.services.operation_logs import record_operation_log
from app.services.professor_management import (
    build_professor_export,
    build_professor_template,
    is_valid_professor_email,
    normalize_professor_payload,
    parse_professor_import_file,
)
from app.services.sample_professors import SAMPLE_PROFESSORS


router = APIRouter(prefix="/api/professors", tags=["professors"])


@router.get("", response_model=list[ProfessorDashboardItemRead])
async def list_professors(
    identity_id: int | None = None,
    llm_profile_id: int | None = None,
    ids: str | None = Query(default=None),
    session: AsyncSession = Depends(get_async_session),
) -> list[ProfessorDashboardItemRead]:
    statement = (
        select(Professor)
        .where(Professor.archived_at.is_(None))
        .order_by(Professor.created_at.desc())
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
    sent_count_by_professor: dict[int, int] = defaultdict(int)

    if identity_id is not None and llm_profile_id is not None:
        task_result = await session.execute(
            select(EmailTask)
            .where(
                EmailTask.identity_id == identity_id,
                EmailTask.professor_id.in_(professor_ids),
                ~(
                    (EmailTask.status == EmailTaskStatus.CANCELED.value)
                    & (EmailTask.cancellation_reason == EmailTaskCancellationReason.USER_REMOVED.value)
                ),
            )
            .order_by(EmailTask.created_at.desc(), EmailTask.id.desc()),
        )
        for task in task_result.scalars():
            tasks_by_professor[task.professor_id].append(task)

        log_result = await session.execute(
            select(EmailLog)
            .where(
                EmailLog.identity_id == identity_id,
                EmailLog.professor_id.in_(professor_ids),
                EmailLog.direction == EmailDirection.SENT.value,
            ),
        )
        for log in log_result.scalars():
            sent_count_by_professor[log.professor_id] += 1

    latest_match_task_by_professor: dict[int, EmailTask] = {}
    for professor_id, tasks in tasks_by_professor.items():
        latest_match = next((task for task in tasks if task.match_score is not None), None)
        if latest_match is not None:
            latest_match_task_by_professor[professor_id] = latest_match

    return [
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
            match_score=latest_match_task_by_professor.get(professor.id).match_score
            if professor.id in latest_match_task_by_professor
            else None,
            sent_count=sent_count_by_professor.get(professor.id, 0),
            status=_map_dashboard_status(
                tasks_by_professor.get(professor.id, []),
                sent_count_by_professor.get(professor.id, 0),
            ),
        )
        for professor in professors
    ]


@router.get("/management", response_model=list[ProfessorManagementItemRead])
async def list_professors_for_management(
    archived: str = Query(default="active"),
    session: AsyncSession = Depends(get_async_session),
) -> list[ProfessorManagementItemRead]:
    statement = select(Professor).order_by(Professor.updated_at.desc(), Professor.created_at.desc())
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
                session.add(Professor(**payload))
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
            professor.archived_at = None
            professor.updated_at = datetime.now(UTC)
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
            "failed_count": parsed.failed_count,
            "row_count": len(parsed.data),
        },
    )
    await session.commit()

    return ProfessorImportFileResult(
        inserted_count=inserted_count,
        updated_count=updated_count,
        failed_count=parsed.failed_count,
        message=(
            f"导入完成：新增 {inserted_count} 条，更新 {updated_count} 条，失败 {parsed.failed_count} 条。"
        ),
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
    await _record_professor_log(session, professor, "professor.created")
    await session.commit()
    await session.refresh(professor)
    return _serialize_management_professor(professor)


@router.get("/{professor_id}", response_model=ProfessorRead)
async def get_professor(
    professor_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> Professor:
    professor = await session.get(Professor, professor_id)
    if not professor:
        raise HTTPException(status_code=404, detail="未找到导师")
    return professor


@router.patch("/{professor_id}", response_model=ProfessorManagementItemRead)
async def update_professor(
    professor_id: int,
    payload: ProfessorUpsertPayload,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorManagementItemRead:
    professor = await session.get(Professor, professor_id)
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
    professor.updated_at = datetime.now(UTC)

    await _record_professor_log(session, professor, "professor.updated")
    await session.commit()
    await session.refresh(professor)
    return _serialize_management_professor(professor)


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
        professor.archived_at = datetime.now(UTC)
        professor.updated_at = datetime.now(UTC)
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
    archive_time = datetime.now(UTC)
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
        professor.updated_at = datetime.now(UTC)
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
        archived_at=professor.archived_at,
        created_at=professor.created_at,
        updated_at=professor.updated_at,
    )


def _map_dashboard_status(tasks: list[EmailTask], sent_count: int = 0) -> str:
    if any(
        task.is_replied or task.status == EmailTaskStatus.REPLY_DETECTED.value
        for task in tasks
    ):
        return "replied"

    if sent_count > 0 or any(task.status == EmailTaskStatus.SENT.value or task.sent_at for task in tasks):
        return "contacted"

    if not tasks:
        return "not_contacted"

    latest_task = tasks[0]
    if latest_task.status in {
        EmailTaskStatus.DRAFT_FAILED.value,
        EmailTaskStatus.SEND_FAILED.value,
    }:
        return "failed"

    if latest_task.status in {
        EmailTaskStatus.APPROVED.value,
        EmailTaskStatus.SCHEDULED.value,
        EmailTaskStatus.SENDING.value,
    }:
        return "ready_to_send"

    if latest_task.status in {
        EmailTaskStatus.DISCOVERED.value,
        EmailTaskStatus.MATCHED.value,
        EmailTaskStatus.GENERATING_DRAFT.value,
        EmailTaskStatus.REVIEW_REQUIRED.value,
    }:
        return "preparing"

    return "not_contacted"
