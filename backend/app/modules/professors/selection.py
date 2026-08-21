from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.query_chunks import chunked_values
from app.models import Professor
from app.schemas.selection import SelectionSpec


PROFESSOR_NAME_SCRIPT_PATTERNS: dict[str, str] = {
    "latin": r"[A-Za-z\u00C0-\u02AF\u1D00-\u1D7F\u1E00-\u1EFF\uAB30-\uAB6F\uFB00-\uFB06\uFF21-\uFF5A]",
    "han": r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF\U00020000-\U0002FA1F]",
    "cyrillic": r"[\u0400-\u052F\u1C80-\u1C8F\u2DE0-\u2DFF\uA640-\uA69F]",
    "arabic": r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]",
    "digit": r"[0-9\u0660-\u0669\u06F0-\u06F9\u0966-\u096F\uFF10-\uFF19]",
}

_TEXT_FILTER_COLUMNS = {
    "name": Professor.name,
    "email": Professor.email,
    "title": Professor.title,
    "university": Professor.university,
    "school": Professor.school,
    "department": Professor.department,
    "research_direction": Professor.research_direction,
    "personal_note": Professor.personal_note,
}


class ProfessorSelectionError(Exception):
    def __init__(self, *, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def professor_name_script_clause(script: str) -> object:
    pattern = PROFESSOR_NAME_SCRIPT_PATTERNS.get(script)
    if pattern is None:
        raise ProfessorSelectionError(
            status_code=422,
            code="INVALID_PROFESSOR_SELECTION_FILTER",
            message="name.contains_script 仅支持 latin、han、cyrillic、arabic 或 digit。",
        )
    return Professor.name.regexp_match(pattern)


async def resolve_professor_selection(
    session: AsyncSession,
    selection: SelectionSpec,
) -> tuple[list[int], int, int]:
    """Resolve a reusable selection to immutable ordered IDs for a plan."""

    if selection.mode == "ids":
        matched_ids = list(selection.ids)
    else:
        filters = selection.filter if selection.mode == "filter" else {}
        unknown_filters = sorted(set(filters) - {"archived", "where"})
        if unknown_filters:
            raise ProfessorSelectionError(
                status_code=422,
                code="INVALID_PROFESSOR_SELECTION_FILTER",
                message=f"不支持的导师选择字段：{', '.join(unknown_filters)}",
            )
        archived = filters.get("archived", "active")
        if archived not in {"active", "archived", "all"}:
            raise ProfessorSelectionError(
                status_code=422,
                code="INVALID_PROFESSOR_SELECTION_FILTER",
                message="archived 必须是 active、archived 或 all。",
            )
        where = filters.get("where", {})
        if not isinstance(where, dict):
            raise ProfessorSelectionError(
                status_code=422,
                code="INVALID_PROFESSOR_SELECTION_FILTER",
                message="where 必须是结构化筛选 JSON 对象。",
            )
        statement = select(Professor.id)
        if archived == "active":
            statement = statement.where(Professor.archived_at.is_(None))
        elif archived == "archived":
            statement = statement.where(Professor.archived_at.is_not(None))
        statement = _apply_professor_selection_where(statement, where)
        matched_ids = list(
            await session.scalars(statement.order_by(Professor.id.asc()))
        )

    matched_count = len(matched_ids)
    excluded_ids = set(selection.exclude_ids)
    if excluded_ids:
        existing_exclusions: set[int] = set()
        for id_chunk in chunked_values(sorted(excluded_ids)):
            existing_exclusions.update(
                await session.scalars(
                    select(Professor.id).where(Professor.id.in_(id_chunk)),
                ),
            )
        if existing_exclusions != excluded_ids:
            raise ProfessorSelectionError(
                status_code=404,
                code="PROFESSOR_SELECTION_EXCLUSIONS_NOT_FOUND",
                message="部分排除导师不存在。",
            )
    selected_ids = [
        professor_id for professor_id in matched_ids if professor_id not in excluded_ids
    ]
    excluded_count = matched_count - len(selected_ids)
    if not selected_ids:
        raise ProfessorSelectionError(
            status_code=409,
            code="PROFESSOR_SELECTION_EMPTY",
            message="没有导师匹配当前批量归档选择条件。",
        )
    return selected_ids, matched_count, excluded_count


def _apply_professor_selection_where(
    statement: object, where: dict[str, object]
) -> object:
    for field, condition in where.items():
        column = _TEXT_FILTER_COLUMNS.get(field)
        if column is None:
            raise ProfessorSelectionError(
                status_code=422,
                code="INVALID_PROFESSOR_SELECTION_FILTER",
                message=f"导师批量选择不支持字段 {field}。",
            )
        if not isinstance(condition, dict) or len(condition) != 1:
            raise ProfessorSelectionError(
                status_code=422,
                code="INVALID_PROFESSOR_SELECTION_FILTER",
                message=f"字段 {field} 必须且只能指定一个筛选运算符。",
            )
        operator, expected = next(iter(condition.items()))
        if operator == "contains_script":
            if field != "name" or not isinstance(expected, str):
                raise ProfessorSelectionError(
                    status_code=422,
                    code="INVALID_PROFESSOR_SELECTION_FILTER",
                    message="contains_script 目前只支持导师 name 字段。",
                )
            statement = statement.where(
                professor_name_script_clause(expected.strip().lower())
            )
        elif operator in {"eq", "ne"}:
            if isinstance(expected, (dict, list)):
                raise ProfessorSelectionError(
                    status_code=422,
                    code="INVALID_PROFESSOR_SELECTION_FILTER",
                    message=f"字段 {field} 的 {operator} 运算符需要字符串、标量或 null。",
                )
            clause = column.is_(None) if expected is None else column == expected
            statement = statement.where(~clause if operator == "ne" else clause)
        elif operator == "contains" and not isinstance(expected, (dict, list)):
            escaped = _escape_like(str(expected))
            statement = statement.where(column.ilike(f"%{escaped}%", escape="\\"))
        elif operator in {"empty", "exists"} and isinstance(expected, bool):
            if operator == "empty":
                clause = func.length(func.trim(func.coalesce(column, ""))) == 0
            else:
                clause = column.is_not(None)
            statement = statement.where(clause if expected else ~clause)
        else:
            raise ProfessorSelectionError(
                status_code=422,
                code="INVALID_PROFESSOR_SELECTION_FILTER",
                message=(
                    f"字段 {field} 的筛选运算符 {operator} 不受支持；"
                    "可使用 eq、ne、contains、empty、exists，name 还可使用 contains_script。"
                ),
            )
    return statement


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


__all__: Sequence[str] = (
    "PROFESSOR_NAME_SCRIPT_PATTERNS",
    "ProfessorSelectionError",
    "professor_name_script_clause",
    "resolve_professor_selection",
)
