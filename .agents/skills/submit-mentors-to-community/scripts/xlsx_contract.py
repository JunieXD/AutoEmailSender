#!/usr/bin/env python3
"""Validate the public community-share XLSX contract without third-party packages."""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[4]
_CRAWL_SCRIPTS = _ROOT / ".agents" / "skills" / "crawl-mentors-to-xlsx" / "scripts"
if str(_CRAWL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_CRAWL_SCRIPTS))

from xlsx_support import read_workbook  # noqa: E402

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
URL_RE = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)
ERROR_VALUES = frozenset({"#REF!", "#DIV/0!", "#VALUE!", "#N/A", "#NAME?", "#NUM!", "#NULL!"})


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
        file_hash = sha256_file(path)
        workbook = read_workbook(path)
    except (OSError, ValueError) as exc:
        return {"ok": False, "path": str(path), "errors": [str(exc)]}
    if size_bytes > MAX_BYTES:
        errors.append(f"文件超过 5 MiB 限制：{size_bytes} bytes")
    if workbook.active_sheet_name != "community-share":
        errors.append(f"活动工作表必须是 community-share，实际为 {workbook.active_sheet_name!r}")
    sheet = workbook.sheet("community-share")
    if sheet is None:
        errors.append("缺少 community-share 工作表")
        return {"ok": False, "path": str(path), "size_bytes": size_bytes, "sha256": file_hash, "errors": errors}
    for other in workbook.sheets:
        if other.formulas:
            errors.append(f"{other.name} 含公式：{', '.join(other.formulas[:5])}")
        for row_number, row in enumerate(other.rows, start=1):
            if any(value.strip().upper() in ERROR_VALUES for value in row):
                errors.append(f"{other.name}!{row_number} 含电子表格错误值")
                break
    if not sheet.rows or tuple(value.strip() for value in _trim(sheet.rows[0])) != COMMUNITY_COLUMNS:
        actual = tuple(value.strip() for value in _trim(sheet.rows[0])) if sheet.rows else ()
        errors.append(f"表头必须严格为 {','.join(COMMUNITY_COLUMNS)}，实际为 {','.join(actual)}")
        rows: list[dict[str, str]] = []
    else:
        rows = []
        for row_number, raw in enumerate(sheet.rows[1:], start=2):
            values = list(raw[: len(COMMUNITY_COLUMNS)]) + [""] * max(0, len(COMMUNITY_COLUMNS) - len(raw))
            if not any(value.strip() for value in values):
                continue
            if any(value.strip() for value in raw[len(COMMUNITY_COLUMNS):]):
                errors.append(f"community-share!{row_number} 存在额外列")
            record = dict(zip(COMMUNITY_COLUMNS, values, strict=True))
            rows.append(record)
            if not record["name"].strip():
                errors.append(f"community-share!{row_number}.name 不能为空")
            if not EMAIL_RE.fullmatch(record["email"].strip()):
                errors.append(f"community-share!{row_number}.email 不是有效邮箱")
            if not URL_RE.fullmatch(record["source_url"].strip()):
                errors.append(f"community-share!{row_number}.source_url 必须是 http(s) URL")
    universities = sorted({row["university"].strip() for row in rows if row["university"].strip()})
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
        "departments": sorted({row["department"].strip() for row in rows if row["department"].strip()}),
        "errors": errors,
    }

