"""Validate the public community-share XLSX contract without third-party packages."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import urlsplit
from zipfile import BadZipFile

from submission_xlsx import read_workbook

COMMUNITY_COLUMNS = (
    "name",
    "email",
    "title",
    "university",
    "school",
    "department",
    "research_direction",
    "recent_papers",
    "profile_url",
    "source_url",
)
MAX_BYTES = 5 * 1024 * 1024
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
ERROR_VALUES = frozenset(
    {"#REF!", "#DIV/0!", "#VALUE!", "#N/A", "#NAME?", "#NUM!", "#NULL!"}
)


def _valid_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and not parsed.username
            and not parsed.password
            and not any(char.isspace() or ord(char) < 32 for char in value)
        )
    except ValueError:
        return False


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _trim(row: list[str]) -> list[str]:
    values = list(row)
    while values and not values[-1].strip():
        values.pop()
    return values


def inspect_xlsx(path: Path) -> dict[str, object]:
    errors: list[str] = []
    path = path.expanduser().resolve()
    try:
        size_bytes = path.stat().st_size
        if size_bytes > MAX_BYTES:
            raise ValueError(f"文件超过 5 MiB 限制：{size_bytes} bytes")
        file_hash = sha256_file(path)
        workbook = read_workbook(path)
    except (OSError, ValueError, BadZipFile, KeyError, RuntimeError) as exc:
        return {"ok": False, "path": str(path), "errors": [str(exc)]}
    if workbook.active_sheet_name != "community-share":
        errors.append(
            f"活动工作表必须是 community-share，实际为 {workbook.active_sheet_name!r}"
        )
    sheet = workbook.sheet("community-share")
    if sheet is None:
        errors.append("缺少 community-share 工作表")
        return {
            "ok": False,
            "path": str(path),
            "size_bytes": size_bytes,
            "sha256": file_hash,
            "errors": errors,
        }
    for other in workbook.sheets:
        if other.formulas:
            errors.append(f"{other.name} 含公式：{', '.join(other.formulas[:5])}")
        for row_number, row in enumerate(other.rows, start=1):
            if any(value.strip().upper() in ERROR_VALUES for value in row):
                errors.append(f"{other.name}!{row_number} 含电子表格错误值")
                break
    if (
        not sheet.rows
        or tuple(value.strip() for value in _trim(sheet.rows[0])) != COMMUNITY_COLUMNS
    ):
        actual = (
            tuple(value.strip() for value in _trim(sheet.rows[0])) if sheet.rows else ()
        )
        errors.append(
            f"表头必须严格为 {','.join(COMMUNITY_COLUMNS)}，实际为 {','.join(actual)}"
        )
        rows: list[dict[str, str]] = []
    else:
        rows = []
        for row_number, raw in enumerate(sheet.rows[1:], start=2):
            values = list(raw[: len(COMMUNITY_COLUMNS)]) + [""] * max(
                0, len(COMMUNITY_COLUMNS) - len(raw)
            )
            if not any(value.strip() for value in values):
                continue
            if any(value.strip() for value in raw[len(COMMUNITY_COLUMNS) :]):
                errors.append(f"community-share!{row_number} 存在额外列")
            record = dict(zip(COMMUNITY_COLUMNS, values, strict=True))
            rows.append(record)
            if not record["name"].strip():
                errors.append(f"community-share!{row_number}.name 不能为空")
            if not EMAIL_RE.fullmatch(record["email"].strip()):
                errors.append(f"community-share!{row_number}.email 不是有效邮箱")
            for field in ("university", "school"):
                if not record[field].strip():
                    errors.append(f"community-share!{row_number}.{field} 不能为空")
            if not _valid_url(record["source_url"].strip()):
                errors.append(
                    f"community-share!{row_number}.source_url 必须是 http(s) URL"
                )
    universities = sorted(
        {row["university"].strip() for row in rows if row["university"].strip()}
    )
    schools = sorted({row["school"].strip() for row in rows if row["school"].strip()})
    if len(universities) != 1:
        errors.append("每个文件必须且只能包含一个非空 university")
    if len(schools) != 1:
        errors.append("每个文件必须且只能包含一个非空 school")
    return {
        "ok": not errors,
        "path": str(path),
        "size_bytes": size_bytes,
        "sha256": file_hash,
        "professor_count": len(rows),
        "university": universities[0] if len(universities) == 1 else "",
        "school": schools[0] if len(schools) == 1 else "",
        "departments": sorted(
            {row["department"].strip() for row in rows if row["department"].strip()}
        ),
        "errors": errors,
    }
