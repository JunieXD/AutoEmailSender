from __future__ import annotations

import base64
from datetime import datetime
import json
import re
from typing import Any, Iterable, Literal

from sqlalchemy import (
    Integer,
    String,
    and_,
    case,
    cast,
    func,
    literal,
    or_,
    select,
    text,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from app.models import (
    AgentUiHandoff,
    AgentUiHandoffItem,
    BatchTask,
    BatchTaskStatus,
    EmailDirection,
    EmailLog,
    EmailLogRecordState,
    EmailTask,
    EmailTaskStatus,
    IdentityProfessorMatchResult,
    Professor,
    ProfessorTag,
    ProfessorTagLink,
)
from app.core.agent_api_errors import AgentApiError
from app.core.time import as_utc_aware, as_utc_naive, utc_now
from app.modules.campaigns.public import email_task_is_not_user_removed_expression
from app.modules.identities.public import resolve_identity_communication_scope
from app.services.contact_status import build_contact_status_by_professor
from app.services.match_results import (
    load_resolved_match_results,
    match_result_is_stale,
    resolve_identity_match_scope,
)
from app.services.professor_schedule import load_active_scheduled_professor_ids

from .schemas import (
    ProfessorDashboardItemRead,
    ProfessorDashboardPageRead,
    ProfessorDashboardPageRequest,
    ProfessorFilterOptionsRead,
    ProfessorIdSelectionRead,
    ProfessorManagementItemRead,
    ProfessorManagementPageRead,
    ProfessorManagementPageRequest,
    ProfessorPageRequestBase,
    ProfessorTagRead,
)


NO_FIELD_FILTER_VALUE = "__no_field__"
NO_TAG_FILTER_VALUE = "__no_tag__"
TITLE_SPLIT_PATTERN = re.compile(r"[、，,/／|｜；;]+")


def _archive_condition(archived: Literal["active", "archived", "all"]):
    if archived == "active":
        return Professor.archived_at.is_(None)
    if archived == "archived":
        return Professor.archived_at.is_not(None)
    return literal(True)


async def _has_any_professors(
    session: AsyncSession,
    *,
    archived: Literal["active", "archived", "all"],
) -> bool:
    professor_id = await session.scalar(
        select(Professor.id).where(_archive_condition(archived)).limit(1),
    )
    return professor_id is not None


async def _ui_handoff_professor_condition(
    session: AsyncSession,
    handoff_id: str | None,
    *,
    surface: Literal["professors.management", "professors.home"],
    identity_id: int | None = None,
) -> ColumnElement[bool] | None:
    if handoff_id is None:
        return None
    handoff = await session.get(AgentUiHandoff, handoff_id)
    if handoff is None or handoff.surface != surface:
        raise AgentApiError(
            status_code=404,
            code="UI_HANDOFF_NOT_FOUND",
            message="未找到可用于当前导师页面的界面交接。",
        )
    if as_utc_aware(handoff.expires_at) <= as_utc_aware(utc_now()):
        raise AgentApiError(
            status_code=410,
            code="UI_HANDOFF_EXPIRED",
            message="导师界面交接已经过期，请重新生成。",
        )
    if handoff.status in {"failed", "canceled", "expired"}:
        raise AgentApiError(
            status_code=409,
            code="UI_HANDOFF_UNAVAILABLE",
            message=f"状态 {handoff.status} 的界面交接不能用于筛选导师。",
        )
    if surface == "professors.home":
        payload = handoff.payload if isinstance(handoff.payload, dict) else {}
        handoff_identity_id = payload.get("identity_id")
        if handoff_identity_id != identity_id:
            raise AgentApiError(
                status_code=409,
                code="UI_HANDOFF_IDENTITY_MISMATCH",
                message="该导师界面交接属于其他发件身份，请重新发起页面定位。",
            )
    selected_ids = select(cast(AgentUiHandoffItem.resource_id, Integer)).where(
        AgentUiHandoffItem.handoff_id == handoff_id,
        AgentUiHandoffItem.resource_type == "professor",
    )
    return Professor.id.in_(selected_ids)


async def _sqlite_professor_fts_available(session: AsyncSession) -> bool:
    bind = session.bind
    if bind is None or bind.dialect.name != "sqlite":
        return False
    return bool(
        await session.scalar(
            text(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'professors_fts' LIMIT 1",
            ),
        ),
    )


def _escaped_contains_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _contains(column: ColumnElement[Any], value: str) -> ColumnElement[bool]:
    return column.ilike(_escaped_contains_pattern(value), escape="\\")


def _selected_text_condition(
    column: ColumnElement[Any],
    values: Iterable[str],
) -> ColumnElement[bool] | None:
    selected = list(dict.fromkeys(values))
    if not selected:
        return None
    include_missing = NO_FIELD_FILTER_VALUE in selected
    concrete = [value for value in selected if value != NO_FIELD_FILTER_VALUE]
    conditions: list[ColumnElement[bool]] = []
    if concrete:
        conditions.append(func.trim(column).in_(concrete))
    if include_missing:
        conditions.append(func.trim(func.coalesce(column, "")) == "")
    return or_(*conditions) if conditions else literal(False)


def _title_condition(values: Iterable[str]) -> ColumnElement[bool] | None:
    selected = list(dict.fromkeys(values))
    if not selected:
        return None
    conditions: list[ColumnElement[bool]] = []
    if NO_FIELD_FILTER_VALUE in selected:
        conditions.append(func.trim(func.coalesce(Professor.title, "")) == "")
    conditions.extend(
        _contains(Professor.title, value)
        for value in selected
        if value != NO_FIELD_FILTER_VALUE
    )
    return or_(*conditions) if conditions else literal(False)


def _tag_filter_condition(values: Iterable[str]) -> ColumnElement[bool] | None:
    selected = list(dict.fromkeys(values))
    if not selected:
        return None
    tag_ids: list[int] = []
    for value in selected:
        if value == NO_TAG_FILTER_VALUE:
            continue
        try:
            tag_id = int(value)
        except ValueError:
            continue
        if tag_id > 0:
            tag_ids.append(tag_id)
    conditions: list[ColumnElement[bool]] = []
    if tag_ids:
        conditions.append(Professor.tags.any(ProfessorTag.id.in_(tag_ids)))
    if NO_TAG_FILTER_VALUE in selected:
        conditions.append(~Professor.tags.any())
    return or_(*conditions) if conditions else literal(False)


def _keyword_condition(
    request: ProfessorPageRequestBase,
    *,
    use_fts: bool,
) -> ColumnElement[bool] | None:
    keyword = request.keyword.strip()
    if not keyword:
        return None
    scopes = set(request.keyword_search_scopes)
    conditions: list[ColumnElement[bool]] = []
    field_by_scope = {
        "name": Professor.name,
        "email": Professor.email,
        "university": Professor.university,
        "school": Professor.school,
        "department": Professor.department,
        "title": Professor.title,
        "researchDirection": Professor.research_direction,
    }
    selected_database_scopes = [scope for scope in field_by_scope if scope in scopes]
    if use_fts and len(keyword) >= 3 and selected_database_scopes:
        fts_column_by_scope = {
            "name": "name",
            "email": "email",
            "university": "university",
            "school": "school",
            "department": "department",
            "title": "title",
            "researchDirection": "research_direction",
        }
        selected_columns = [
            fts_column_by_scope[scope] for scope in selected_database_scopes
        ]
        phrase = '"' + keyword.replace('"', '""') + '"'
        fts_query = (
            phrase
            if len(selected_columns) == len(fts_column_by_scope)
            else "{" + " ".join(selected_columns) + "}: " + phrase
        )
        conditions.append(
            text(
                "professors.id IN ("
                "SELECT rowid FROM professors_fts "
                "WHERE professors_fts MATCH :professor_fts_query"
                ")",
            ).bindparams(professor_fts_query=fts_query),
        )
    else:
        for scope in selected_database_scopes:
            conditions.append(_contains(field_by_scope[scope], keyword))
    if "personalNote" in scopes:
        conditions.append(_contains(Professor.personal_note, keyword))
    if "tag" in scopes:
        conditions.append(Professor.tags.any(_contains(ProfessorTag.name, keyword)))
    return or_(*conditions) if conditions else literal(False)


def _static_filter_conditions(
    request: ProfessorPageRequestBase,
    *,
    archived: Literal["active", "archived", "all"],
    use_fts: bool,
) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = [_archive_condition(archived)]
    for condition in (
        _keyword_condition(request, use_fts=use_fts),
        _selected_text_condition(Professor.university, request.universities),
        _selected_text_condition(Professor.school, request.schools),
        _selected_text_condition(Professor.department, request.departments),
        _title_condition(request.titles),
        _tag_filter_condition(request.tag_ids),
    ):
        if condition is not None:
            conditions.append(condition)
    return conditions


def _hierarchy_conditions(
    *,
    archived: Literal["active", "archived", "all"],
    universities: Iterable[str] = (),
    schools: Iterable[str] = (),
) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = [_archive_condition(archived)]
    university_condition = _selected_text_condition(Professor.university, universities)
    school_condition = _selected_text_condition(Professor.school, schools)
    if university_condition is not None:
        conditions.append(university_condition)
    if school_condition is not None:
        conditions.append(school_condition)
    return conditions


async def _load_filter_options(
    session: AsyncSession,
    request: ProfessorPageRequestBase,
    *,
    archived: Literal["active", "archived", "all"],
) -> ProfessorFilterOptionsRead:
    universities = list(
        await session.scalars(
            select(func.trim(Professor.university))
            .where(
                _archive_condition(archived),
                func.trim(func.coalesce(Professor.university, "")) != "",
            )
            .distinct(),
        ),
    )
    schools = list(
        await session.scalars(
            select(func.trim(Professor.school))
            .where(
                *_hierarchy_conditions(
                    archived=archived,
                    universities=request.universities,
                ),
                func.trim(func.coalesce(Professor.school, "")) != "",
            )
            .distinct(),
        ),
    )
    departments = list(
        await session.scalars(
            select(func.trim(Professor.department))
            .where(
                *_hierarchy_conditions(
                    archived=archived,
                    universities=request.universities,
                    schools=request.schools,
                ),
                func.trim(func.coalesce(Professor.department, "")) != "",
            )
            .distinct(),
        ),
    )
    raw_titles = list(
        await session.scalars(
            select(Professor.title)
            .where(
                _archive_condition(archived),
                func.trim(func.coalesce(Professor.title, "")) != "",
            )
            .distinct(),
        ),
    )
    title_values = {
        item.strip()
        for raw_title in raw_titles
        if raw_title
        for item in TITLE_SPLIT_PATTERN.split(raw_title)
        if item.strip()
    }
    tag_rows = (
        await session.execute(
            select(ProfessorTag.id, ProfessorTag.name)
            .join(ProfessorTagLink, ProfessorTagLink.tag_id == ProfessorTag.id)
            .join(Professor, Professor.id == ProfessorTagLink.professor_id)
            .where(
                _archive_condition(archived),
            )
            .distinct()
            .order_by(ProfessorTag.name.asc(), ProfessorTag.id.asc()),
        )
    ).all()
    return ProfessorFilterOptionsRead(
        universities=sorted(set(universities)),
        schools=sorted(set(schools)),
        departments=sorted(set(departments)),
        titles=sorted(title_values),
        tags=[{"id": tag_id, "name": name} for tag_id, name in tag_rows],
    )


def _serialize_tag(tag: ProfessorTag) -> ProfessorTagRead:
    return ProfessorTagRead(
        id=tag.id,
        name=tag.name,
        text_color=tag.text_color,
        background_color=tag.background_color,
    )


def _serialize_management_professor(
    professor: Professor,
) -> ProfessorManagementItemRead:
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
        tags=[_serialize_tag(tag) for tag in professor.tags],
    )


def _management_sort_expression(request: ProfessorManagementPageRequest):
    if request.sort_key == "updatedAtDesc":
        return Professor.updated_at, False
    if request.sort_key == "nameAsc":
        return Professor.name, False
    if request.sort_key == "universityAsc":
        return Professor.university, True
    return Professor.created_at, False


def _ordered_expressions(
    primary: ColumnElement[Any],
    *,
    direction: Literal["asc", "desc"],
    nulls_last: bool,
) -> tuple[ColumnElement[Any], ColumnElement[Any]]:
    primary_order = primary.asc() if direction == "asc" else primary.desc()
    if nulls_last:
        primary_order = primary_order.nulls_last()
    id_order = Professor.id.asc() if direction == "asc" else Professor.id.desc()
    return primary_order, id_order


def _encode_cursor(
    *,
    sort_key: str,
    direction: str,
    value: Any,
    professor_id: int,
    secondary_value: Any | None = None,
) -> str:
    if isinstance(value, datetime):
        value = value.isoformat()
    cursor_payload = {"k": sort_key, "d": direction, "v": value, "i": professor_id}
    if secondary_value is not None:
        cursor_payload["s"] = secondary_value
    payload = json.dumps(
        cursor_payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(
    cursor: str,
    *,
    sort_key: str,
    direction: str,
    datetime_value: bool,
) -> tuple[Any, Any | None, int]:
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(cursor + padding))
        if not isinstance(payload, dict):
            raise ValueError
        if payload.get("k") != sort_key or payload.get("d") != direction:
            raise ValueError
        value = payload.get("v")
        professor_id = payload["i"]
        if (
            isinstance(professor_id, bool)
            or not isinstance(professor_id, int)
            or professor_id < 1
        ):
            raise ValueError
        if datetime_value and value is not None:
            if not isinstance(value, str):
                raise ValueError
            value = datetime.fromisoformat(value)
        elif sort_key in {"nameAsc", "universityAsc"}:
            if value is not None and not isinstance(value, str):
                raise ValueError
        elif sort_key in {"matchScoreDesc", "sentCountDesc"}:
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int)
            ):
                raise ValueError
        secondary_value = payload.get("s")
        if sort_key == "universityAsc" and not isinstance(secondary_value, str):
            raise ValueError
        return value, secondary_value, professor_id
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("分页游标无效或已与当前排序条件不匹配") from exc


def _keyset_condition(
    primary: ColumnElement[Any],
    *,
    value: Any,
    professor_id: int,
    direction: Literal["asc", "desc"],
    nulls_last: bool,
    sqlite_datetime: bool = False,
) -> ColumnElement[bool]:
    id_comparison = (
        Professor.id > professor_id
        if direction == "asc"
        else Professor.id < professor_id
    )
    if value is None:
        return and_(primary.is_(None), id_comparison)
    comparison_primary: ColumnElement[Any] = primary
    comparison_value: Any = value
    if sqlite_datetime and isinstance(value, datetime):
        # SQLite stores DateTime values as text. Server defaults omit microseconds,
        # while SQLAlchemy binds cursor datetimes with them, so compare the same
        # canonical text representation on both sides of the keyset condition.
        text_primary = cast(primary, String)
        comparison_primary = case(
            (func.length(text_primary) == 19, text_primary + literal(".000000")),
            else_=text_primary,
        )
        comparison_value = as_utc_naive(value).isoformat(
            sep=" ",
            timespec="microseconds",
        )
    value_comparison = (
        comparison_primary > comparison_value
        if direction == "asc"
        else comparison_primary < comparison_value
    )
    conditions: list[ColumnElement[bool]] = [
        value_comparison,
        and_(comparison_primary == comparison_value, id_comparison),
    ]
    if nulls_last:
        conditions.append(primary.is_(None))
    return or_(*conditions)


def _compound_keyset_condition(
    primary: ColumnElement[Any],
    secondary: ColumnElement[Any],
    *,
    primary_value: Any,
    secondary_value: Any,
    professor_id: int,
    direction: Literal["asc", "desc"],
    nulls_last: bool,
) -> ColumnElement[bool]:
    primary_comparison = (
        primary > primary_value if direction == "asc" else primary < primary_value
    )
    secondary_comparison = (
        secondary > secondary_value
        if direction == "asc"
        else secondary < secondary_value
    )
    within_primary = or_(
        secondary_comparison,
        and_(
            secondary == secondary_value,
            Professor.id > professor_id,
        ),
    )
    if primary_value is None:
        return and_(primary.is_(None), within_primary)
    conditions: list[ColumnElement[bool]] = [
        primary_comparison,
        and_(primary == primary_value, within_primary),
    ]
    if nulls_last:
        conditions.append(primary.is_(None))
    return or_(*conditions)


async def list_management_professor_page(
    session: AsyncSession,
    request: ProfessorManagementPageRequest,
) -> ProfessorManagementPageRead:
    conditions = _static_filter_conditions(
        request,
        archived=request.archived,
        use_fts=await _sqlite_professor_fts_available(session),
    )
    handoff_condition = await _ui_handoff_professor_condition(
        session,
        request.ui_handoff_id,
        surface="professors.management",
    )
    if handoff_condition is not None:
        conditions.append(handoff_condition)
    total_count = int(
        (await session.scalar(select(func.count(Professor.id)).where(*conditions))) or 0
    )
    total_pages = max(1, (total_count + request.page_size - 1) // request.page_size)
    safe_page = min(request.page, total_pages)
    primary_sort, nulls_last = _management_sort_expression(request)
    sqlite_datetime_cursor = (
        request.sort_key in {"latest", "updatedAtDesc"}
        and session.bind is not None
        and session.bind.dialect.name == "sqlite"
    )
    statement = (
        select(Professor, primary_sort.label("_sort_value"))
        .options(selectinload(Professor.tags))
        .where(*conditions)
    )
    uses_cursor = bool(request.cursor)
    if uses_cursor and request.cursor:
        cursor_value, secondary_value, cursor_id = _decode_cursor(
            request.cursor,
            sort_key=request.sort_key,
            direction=request.sort_direction,
            datetime_value=request.sort_key in {"latest", "updatedAtDesc"},
        )
        if request.sort_key == "universityAsc":
            if not isinstance(secondary_value, str):
                raise ValueError("分页游标无效或已与当前排序条件不匹配")
            statement = statement.where(
                _compound_keyset_condition(
                    primary_sort,
                    Professor.name,
                    primary_value=cursor_value,
                    secondary_value=secondary_value,
                    professor_id=cursor_id,
                    direction=request.sort_direction,
                    nulls_last=True,
                ),
            )
        else:
            statement = statement.where(
                _keyset_condition(
                    primary_sort,
                    value=cursor_value,
                    professor_id=cursor_id,
                    direction=request.sort_direction,
                    nulls_last=nulls_last,
                    sqlite_datetime=sqlite_datetime_cursor,
                ),
            )
    if request.sort_key == "universityAsc":
        name_order = (
            Professor.name.asc()
            if request.sort_direction == "asc"
            else Professor.name.desc()
        )
        order_by = (
            *_ordered_expressions(
                primary_sort,
                direction=request.sort_direction,
                nulls_last=True,
            )[:1],
            name_order,
            Professor.id.asc(),
        )
    else:
        order_by = _ordered_expressions(
            primary_sort,
            direction=request.sort_direction,
            nulls_last=nulls_last,
        )
    statement = statement.order_by(*order_by).limit(request.page_size + 1)
    if not uses_cursor:
        statement = statement.offset((safe_page - 1) * request.page_size)
    rows = (await session.execute(statement)).all()
    has_more = len(rows) > request.page_size
    page_rows = rows[: request.page_size]
    next_cursor = None
    if has_more and page_rows:
        last_row = page_rows[-1]
        next_cursor = _encode_cursor(
            sort_key=request.sort_key,
            direction=request.sort_direction,
            value=last_row._sort_value,
            professor_id=last_row.Professor.id,
            secondary_value=(
                last_row.Professor.name if request.sort_key == "universityAsc" else None
            ),
        )
    return ProfessorManagementPageRead(
        items=[_serialize_management_professor(row.Professor) for row in page_rows],
        total_count=total_count,
        has_any_professors=await _has_any_professors(
            session,
            archived=request.archived,
        ),
        page=safe_page,
        page_size=request.page_size,
        total_pages=total_pages,
        next_cursor=next_cursor,
        filter_options=await _load_filter_options(
            session,
            request,
            archived=request.archived,
        ),
    )


def _dashboard_summary_expressions(
    *,
    identity_id: int,
    communication_identity_ids: tuple[int, ...],
    match_source_identity_id: int,
):
    normalized_message_id = func.trim(func.coalesce(EmailLog.normalized_message_id, ""))
    rfc_message_id = func.trim(func.coalesce(EmailLog.rfc_message_id, ""))
    fingerprint = func.trim(func.coalesce(EmailLog.message_fingerprint, ""))
    event_key = case(
        (
            EmailLog.delivery_attempt_id.is_not(None),
            literal("delivery:") + EmailLog.delivery_attempt_id,
        ),
        (
            normalized_message_id != "",
            literal("message:") + func.lower(normalized_message_id),
        ),
        (
            rfc_message_id != "",
            literal("message:") + func.lower(rfc_message_id),
        ),
        (
            fingerprint != "",
            literal("fingerprint:") + func.lower(fingerprint),
        ),
        else_=literal("log:") + cast(EmailLog.id, String),
    )
    grouped_events = (
        select(
            EmailLog.professor_id.label("professor_id"),
            EmailLog.direction.label("direction"),
            event_key.label("event_key"),
            func.max(
                case(
                    (func.trim(func.coalesce(EmailLog.failure_summary, "")) == "", 1),
                    else_=0,
                ),
            ).label("successful"),
            func.min(EmailLog.created_at).label("created_at"),
        )
        .where(
            EmailLog.identity_id.in_(communication_identity_ids),
            EmailLog.direction.in_(
                [EmailDirection.SENT.value, EmailDirection.RECEIVED.value],
            ),
            EmailLog.record_state == EmailLogRecordState.CANONICAL.value,
        )
        .group_by(EmailLog.professor_id, EmailLog.direction, event_key)
        .subquery("dashboard_events")
    )
    log_summary = (
        select(
            grouped_events.c.professor_id,
            func.sum(
                case(
                    (
                        and_(
                            grouped_events.c.direction == EmailDirection.SENT.value,
                            grouped_events.c.successful == 1,
                        ),
                        1,
                    ),
                    else_=0,
                ),
            ).label("sent_count"),
            func.max(
                case(
                    (
                        and_(
                            grouped_events.c.direction == EmailDirection.SENT.value,
                            grouped_events.c.successful == 1,
                        ),
                        grouped_events.c.created_at,
                    ),
                    else_=None,
                ),
            ).label("last_sent_at"),
            func.max(
                case(
                    (
                        grouped_events.c.direction == EmailDirection.RECEIVED.value,
                        grouped_events.c.created_at,
                    ),
                    else_=None,
                ),
            ).label("last_replied_at"),
        )
        .group_by(grouped_events.c.professor_id)
        .subquery("dashboard_log_summary")
    )

    valid_task_conditions = (
        EmailTask.identity_id == identity_id,
        EmailTask.batch_send_canceled_at.is_(None),
        email_task_is_not_user_removed_expression(),
    )
    task_summary = (
        select(
            EmailTask.professor_id.label("professor_id"),
            func.max(
                case(
                    (
                        or_(
                            EmailTask.is_replied.is_(True),
                            EmailTask.status == EmailTaskStatus.REPLY_DETECTED.value,
                        ),
                        1,
                    ),
                    else_=0,
                ),
            ).label("has_reply"),
            func.max(
                case(
                    (
                        or_(
                            EmailTask.sent_at.is_not(None),
                            EmailTask.status.in_(
                                [
                                    EmailTaskStatus.SENT.value,
                                    EmailTaskStatus.REPLY_DETECTED.value,
                                ],
                            ),
                        ),
                        1,
                    ),
                    else_=0,
                ),
            ).label("has_sent"),
            func.max(EmailTask.sent_at).label("last_sent_at"),
            func.max(
                case(
                    (
                        or_(
                            EmailTask.is_replied.is_(True),
                            EmailTask.status == EmailTaskStatus.REPLY_DETECTED.value,
                        ),
                        EmailTask.updated_at,
                    ),
                    else_=None,
                ),
            ).label("last_replied_at"),
        )
        .where(*valid_task_conditions)
        .group_by(EmailTask.professor_id)
        .subquery("dashboard_task_summary")
    )
    ranked_tasks = (
        select(
            EmailTask.professor_id.label("professor_id"),
            EmailTask.id.label("task_id"),
            EmailTask.status.label("status"),
            EmailTask.updated_at.label("updated_at"),
            func.row_number()
            .over(
                partition_by=EmailTask.professor_id,
                order_by=(EmailTask.created_at.desc(), EmailTask.id.desc()),
            )
            .label("row_number"),
        )
        .where(*valid_task_conditions)
        .subquery("dashboard_ranked_tasks")
    )
    latest_task = (
        select(
            ranked_tasks.c.professor_id,
            ranked_tasks.c.task_id,
            ranked_tasks.c.status,
            ranked_tasks.c.updated_at,
        )
        .where(ranked_tasks.c.row_number == 1)
        .subquery("dashboard_latest_task")
    )
    active_schedule = (
        select(EmailTask.professor_id.label("professor_id"), literal(1).label("active"))
        .outerjoin(BatchTask, BatchTask.id == EmailTask.batch_task_id)
        .where(
            EmailTask.identity_id == identity_id,
            EmailTask.status.in_(
                [EmailTaskStatus.APPROVED.value, EmailTaskStatus.SCHEDULED.value],
            ),
            EmailTask.scheduled_at.is_not(None),
            EmailTask.batch_send_canceled_at.is_(None),
            or_(
                EmailTask.batch_task_id.is_(None),
                and_(
                    BatchTask.status == BatchTaskStatus.RUNNING.value,
                    BatchTask.deleted_at.is_(None),
                ),
            ),
        )
        .group_by(EmailTask.professor_id)
        .subquery("dashboard_active_schedule")
    )
    canonical_match = (
        select(
            IdentityProfessorMatchResult.professor_id.label("professor_id"),
            IdentityProfessorMatchResult.match_score.label("match_score"),
            IdentityProfessorMatchResult.primary_material_id.label(
                "primary_material_id",
            ),
            IdentityProfessorMatchResult.updated_at.label("updated_at"),
        )
        .where(IdentityProfessorMatchResult.identity_id == match_source_identity_id)
        .subquery("dashboard_canonical_match")
    )
    ranked_legacy_match = (
        select(
            EmailTask.professor_id.label("professor_id"),
            EmailTask.match_score.label("match_score"),
            EmailTask.primary_material_id.label("primary_material_id"),
            EmailTask.updated_at.label("updated_at"),
            func.row_number()
            .over(
                partition_by=EmailTask.professor_id,
                order_by=(
                    EmailTask.updated_at.desc(),
                    EmailTask.created_at.desc(),
                    EmailTask.id.desc(),
                ),
            )
            .label("row_number"),
        )
        .where(
            EmailTask.identity_id == match_source_identity_id,
            EmailTask.match_score.is_not(None),
            EmailTask.batch_send_canceled_at.is_(None),
            or_(
                EmailTask.match_source_identity_id.is_(None),
                EmailTask.match_source_identity_id == match_source_identity_id,
            ),
            email_task_is_not_user_removed_expression(),
        )
        .subquery("dashboard_ranked_legacy_match")
    )
    legacy_match = (
        select(
            ranked_legacy_match.c.professor_id,
            ranked_legacy_match.c.match_score,
            ranked_legacy_match.c.primary_material_id,
            ranked_legacy_match.c.updated_at,
        )
        .where(ranked_legacy_match.c.row_number == 1)
        .subquery("dashboard_legacy_match")
    )

    sent_count = func.coalesce(log_summary.c.sent_count, 0)
    last_sent_at = func.coalesce(
        log_summary.c.last_sent_at, task_summary.c.last_sent_at
    )
    last_replied_at = func.coalesce(
        log_summary.c.last_replied_at,
        task_summary.c.last_replied_at,
    )
    has_reply = or_(
        log_summary.c.last_replied_at.is_not(None),
        task_summary.c.has_reply == 1,
    )
    has_sent = or_(sent_count > 0, task_summary.c.has_sent == 1)
    status = case(
        (has_reply, "replied"),
        (has_sent, "contacted"),
        (latest_task.c.professor_id.is_(None), "not_contacted"),
        (
            latest_task.c.status.in_(
                [EmailTaskStatus.DRAFT_FAILED.value, EmailTaskStatus.SEND_FAILED.value],
            ),
            "failed",
        ),
        (
            latest_task.c.status.in_(
                [
                    EmailTaskStatus.APPROVED.value,
                    EmailTaskStatus.SCHEDULED.value,
                    EmailTaskStatus.SENDING.value,
                ],
            ),
            "ready_to_send",
        ),
        (
            latest_task.c.status.in_(
                [
                    EmailTaskStatus.DISCOVERED.value,
                    EmailTaskStatus.MATCHED.value,
                    EmailTaskStatus.GENERATING_DRAFT.value,
                    EmailTaskStatus.REVIEW_REQUIRED.value,
                ],
            ),
            "preparing",
        ),
        else_="not_contacted",
    )
    return {
        "joins": (
            log_summary,
            task_summary,
            latest_task,
            active_schedule,
            canonical_match,
            legacy_match,
        ),
        "sent_count": sent_count,
        "last_sent_at": last_sent_at,
        "last_replied_at": last_replied_at,
        "status": status,
        "latest_task_id": latest_task.c.task_id,
        "latest_task_updated_at": latest_task.c.updated_at,
        "active_schedule": func.coalesce(active_schedule.c.active, 0),
        "match_score": func.coalesce(
            canonical_match.c.match_score,
            legacy_match.c.match_score,
        ),
        "match_primary_material_id": case(
            (
                canonical_match.c.professor_id.is_not(None),
                canonical_match.c.primary_material_id,
            ),
            else_=legacy_match.c.primary_material_id,
        ),
        "match_updated_at": case(
            (
                canonical_match.c.professor_id.is_not(None),
                canonical_match.c.updated_at,
            ),
            else_=legacy_match.c.updated_at,
        ),
    }


def _join_dashboard_summaries(statement, joins):
    for summary in joins:
        statement = statement.outerjoin(summary, summary.c.professor_id == Professor.id)
    return statement


def _dashboard_filter_conditions(
    request: ProfessorDashboardPageRequest,
    expressions: dict[str, Any],
    *,
    use_fts: bool,
) -> list[ColumnElement[bool]]:
    conditions = _static_filter_conditions(
        request,
        archived="active",
        use_fts=use_fts,
    )
    match_score = expressions["match_score"]
    if request.match_score_missing:
        conditions.append(match_score.is_(None))
    else:
        if request.min_match_score is not None:
            conditions.append(match_score >= request.min_match_score)
        if request.max_match_score is not None:
            conditions.append(match_score <= request.max_match_score)
        if (
            request.min_match_score is not None
            and request.max_match_score is not None
            and request.min_match_score > request.max_match_score
        ):
            conditions.append(literal(False))
    if request.statuses:
        status_conditions: list[ColumnElement[bool]] = []
        for status in request.statuses:
            if status == "scheduled":
                status_conditions.append(expressions["active_schedule"] == 1)
            else:
                status_conditions.append(expressions["status"] == status)
        conditions.append(or_(*status_conditions))
    return conditions


def _dashboard_sort_expression(
    request: ProfessorDashboardPageRequest,
    expressions: dict[str, Any],
):
    if request.sort_key == "matchScoreDesc":
        return expressions["match_score"], True, False
    if request.sort_key == "sentCountDesc":
        return expressions["sent_count"], False, False
    if request.sort_key == "nameAsc":
        return Professor.name, False, False
    if request.sort_key == "lastSentAt":
        return expressions["last_sent_at"], True, True
    if request.sort_key == "lastRepliedAt":
        return expressions["last_replied_at"], True, True
    return Professor.created_at, False, True


async def list_dashboard_professor_page(
    session: AsyncSession,
    request: ProfessorDashboardPageRequest,
) -> ProfessorDashboardPageRead:
    communication_scope = await resolve_identity_communication_scope(
        session,
        active_identity_id=request.identity_id,
    )
    match_scope = await resolve_identity_match_scope(
        session,
        active_identity_id=request.identity_id,
    )
    expressions = _dashboard_summary_expressions(
        identity_id=request.identity_id,
        communication_identity_ids=communication_scope.identity_ids,
        match_source_identity_id=match_scope.source_identity_id,
    )
    conditions = _dashboard_filter_conditions(
        request,
        expressions,
        use_fts=await _sqlite_professor_fts_available(session),
    )
    handoff_condition = await _ui_handoff_professor_condition(
        session,
        request.ui_handoff_id,
        surface="professors.home",
        identity_id=request.identity_id,
    )
    if handoff_condition is not None:
        conditions.append(handoff_condition)
    count_statement = _join_dashboard_summaries(
        select(func.count(Professor.id)).select_from(Professor),
        expressions["joins"],
    ).where(*conditions)
    total_count = int((await session.scalar(count_statement)) or 0)
    total_pages = max(1, (total_count + request.page_size - 1) // request.page_size)
    safe_page = min(request.page, total_pages)
    primary_sort, nulls_last, datetime_cursor = _dashboard_sort_expression(
        request,
        expressions,
    )
    sqlite_datetime_cursor = (
        datetime_cursor
        and session.bind is not None
        and session.bind.dialect.name == "sqlite"
    )
    statement = select(
        Professor,
        primary_sort.label("_sort_value"),
    ).options(selectinload(Professor.tags))
    statement = _join_dashboard_summaries(statement, expressions["joins"]).where(
        *conditions,
    )
    uses_cursor = bool(request.cursor)
    if request.cursor:
        cursor_value, _, cursor_id = _decode_cursor(
            request.cursor,
            sort_key=request.sort_key,
            direction=request.sort_direction,
            datetime_value=datetime_cursor,
        )
        statement = statement.where(
            _keyset_condition(
                primary_sort,
                value=cursor_value,
                professor_id=cursor_id,
                direction=request.sort_direction,
                nulls_last=nulls_last,
                sqlite_datetime=sqlite_datetime_cursor,
            ),
        )
    statement = statement.order_by(
        *_ordered_expressions(
            primary_sort,
            direction=request.sort_direction,
            nulls_last=nulls_last,
        ),
    ).limit(request.page_size + 1)
    if not uses_cursor:
        statement = statement.offset((safe_page - 1) * request.page_size)
    rows = (await session.execute(statement)).all()
    has_more = len(rows) > request.page_size
    page_rows = rows[: request.page_size]
    professors = [row.Professor for row in page_rows]
    professor_ids = [professor.id for professor in professors]
    resolved_matches = await load_resolved_match_results(
        session,
        active_identity_id=request.identity_id,
        professor_ids=professor_ids,
    )
    contact_statuses = await build_contact_status_by_professor(
        session,
        identity_id=request.identity_id,
        professor_ids=professor_ids,
        communication_identity_ids=communication_scope.identity_ids,
    )
    active_scheduled_ids = await load_active_scheduled_professor_ids(
        session,
        identity_id=request.identity_id,
        professor_ids=professor_ids,
    )
    items: list[ProfessorDashboardItemRead] = []
    for professor in professors:
        match_result = resolved_matches.get(professor.id)
        contact_status = contact_statuses.get(professor.id)
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
                match_score=match_result.match_score if match_result else None,
                match_source_identity_id=resolved_matches.scope.source_identity_id,
                match_source_identity_name=(
                    resolved_matches.scope.source_identity.profile_name
                    or resolved_matches.scope.source_identity.name
                ),
                match_is_shared=resolved_matches.scope.uses_group_match_source,
                match_is_stale=(
                    match_result_is_stale(
                        match_result,
                        resolved_matches.scope.source_identity,
                    )
                    if match_result is not None
                    else False
                ),
                match_analyzed_at=(
                    match_result.analyzed_at if match_result is not None else None
                ),
                sent_count=contact_status.sent_count if contact_status else 0,
                status=contact_status.status if contact_status else "not_contacted",
                has_active_schedule=professor.id in active_scheduled_ids,
                last_sent_at=(contact_status.last_sent_at if contact_status else None),
                last_replied_at=(
                    contact_status.last_replied_at if contact_status else None
                ),
                personal_note=professor.personal_note,
                tags=[_serialize_tag(tag) for tag in professor.tags],
            ),
        )
    next_cursor = None
    if has_more and page_rows:
        last_row = page_rows[-1]
        next_cursor = _encode_cursor(
            sort_key=request.sort_key,
            direction=request.sort_direction,
            value=last_row._sort_value,
            professor_id=last_row.Professor.id,
        )
    return ProfessorDashboardPageRead(
        items=items,
        total_count=total_count,
        has_any_professors=await _has_any_professors(
            session,
            archived="active",
        ),
        page=safe_page,
        page_size=request.page_size,
        total_pages=total_pages,
        next_cursor=next_cursor,
        filter_options=await _load_filter_options(
            session,
            request,
            archived="active",
        ),
    )


async def list_management_professor_ids(
    session: AsyncSession,
    request: ProfessorManagementPageRequest,
) -> ProfessorIdSelectionRead:
    conditions = _static_filter_conditions(
        request,
        archived=request.archived,
        use_fts=await _sqlite_professor_fts_available(session),
    )
    handoff_condition = await _ui_handoff_professor_condition(
        session,
        request.ui_handoff_id,
        surface="professors.management",
    )
    if handoff_condition is not None:
        conditions.append(handoff_condition)
    if request.archived == "all":
        conditions.append(Professor.archived_at.is_(None))
    ids = list(
        await session.scalars(
            select(Professor.id).where(*conditions).order_by(Professor.id.asc()),
        ),
    )
    return ProfessorIdSelectionRead(ids=ids, total_count=len(ids))


async def list_dashboard_professor_ids(
    session: AsyncSession,
    request: ProfessorDashboardPageRequest,
) -> ProfessorIdSelectionRead:
    communication_scope = await resolve_identity_communication_scope(
        session,
        active_identity_id=request.identity_id,
    )
    match_scope = await resolve_identity_match_scope(
        session,
        active_identity_id=request.identity_id,
    )
    expressions = _dashboard_summary_expressions(
        identity_id=request.identity_id,
        communication_identity_ids=communication_scope.identity_ids,
        match_source_identity_id=match_scope.source_identity_id,
    )
    conditions = _dashboard_filter_conditions(
        request,
        expressions,
        use_fts=await _sqlite_professor_fts_available(session),
    )
    handoff_condition = await _ui_handoff_professor_condition(
        session,
        request.ui_handoff_id,
        surface="professors.home",
        identity_id=request.identity_id,
    )
    if handoff_condition is not None:
        conditions.append(handoff_condition)
    statement = (
        _join_dashboard_summaries(
            select(Professor.id).select_from(Professor),
            expressions["joins"],
        )
        .where(*conditions)
        .order_by(Professor.id.asc())
    )
    ids = list(await session.scalars(statement))
    return ProfessorIdSelectionRead(ids=ids, total_count=len(ids))
