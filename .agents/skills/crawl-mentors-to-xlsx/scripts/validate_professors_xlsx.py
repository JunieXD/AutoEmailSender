from __future__ import annotations

import argparse
import json
from pathlib import Path

from professor_import_contract import (
    FULL_COLUMNS,
    REVIEW_FIELDS,
    SAFE_COLUMNS,
    SOURCE_FIELDS,
    SPREADSHEET_ERROR_VALUES,
    ContractIssue,
    ContractValidationError,
    canonicalize_payload,
)
from xlsx_support import SheetData, column_name, read_workbook


def _trim_row(row: list[str]) -> list[str]:
    result = list(row)
    while result and result[-1] == "":
        result.pop()
    return result


def _table_rows(
    sheet: SheetData,
    *,
    columns: tuple[str, ...],
    issues: list[ContractIssue],
) -> list[dict[str, str]]:
    if not sheet.rows:
        issues.append(ContractIssue(sheet.name, "工作表为空"))
        return []
    header = tuple(value.strip() for value in _trim_row(sheet.rows[0]))
    if header != columns:
        issues.append(
            ContractIssue(
                f"{sheet.name}!1",
                f"表头必须严格为：{', '.join(columns)}；实际为：{', '.join(header)}",
            )
        )
        return []
    result: list[dict[str, str]] = []
    for row_number, row in enumerate(sheet.rows[1:], start=2):
        values = list(row[: len(columns)]) + [""] * max(0, len(columns) - len(row))
        if not any(value.strip() for value in row):
            continue
        if any(value.strip() for value in row[len(columns) :]):
            issues.append(
                ContractIssue(f"{sheet.name}!{row_number}", "标准列之后存在额外数据")
            )
        result.append({column: values[index] for index, column in enumerate(columns)})
    return result


def _sheet_summary(sheet: SheetData) -> tuple[dict[str, int | str], list[str]]:
    error_cells = [
        f"{column_name(column_index)}{row_index}"
        for row_index, row in enumerate(sheet.rows, start=1)
        for column_index, value in enumerate(row, start=1)
        if value.strip().upper() in SPREADSHEET_ERROR_VALUES
    ]
    return (
        {
            "name": sheet.name,
            "row_count": len(sheet.rows),
            "column_count": max((len(row) for row in sheet.rows), default=0),
            "formula_count": len(sheet.formulas),
            "error_value_count": len(error_cells),
        },
        error_cells,
    )


def validate(path: Path) -> dict[str, object]:
    issues: list[ContractIssue] = []
    try:
        workbook = read_workbook(path)
    except (OSError, ValueError) as error:
        issues.append(ContractIssue("workbook", str(error)))
        return {
            "ok": False,
            "path": str(path),
            "code": "INVALID_WORKBOOK",
            "errors": [item.as_dict() for item in issues],
        }

    if workbook.active_sheet_name != "Professors":
        issues.append(
            ContractIssue(
                "workbook.active_sheet",
                f"必须是 Professors，实际是 {workbook.active_sheet_name or '<空>'}",
            )
        )
    if not workbook.sheets or workbook.sheets[0].name != "Professors":
        issues.append(
            ContractIssue("workbook.sheets[0]", "第一个工作表必须是 Professors")
        )
    sheet_summaries: list[dict[str, int | str]] = []
    for sheet in workbook.sheets:
        summary, error_cells = _sheet_summary(sheet)
        sheet_summaries.append(summary)
        if sheet.formulas:
            issues.append(
                ContractIssue(
                    sheet.name,
                    f"不得包含公式；发现单元格：{', '.join(sheet.formulas[:10])}",
                )
            )
        if error_cells:
            issues.append(
                ContractIssue(
                    sheet.name,
                    f"不得包含电子表格错误值；发现单元格：{', '.join(error_cells[:10])}",
                )
            )

    professor_sheet = workbook.sheet("Professors")
    review_sheet = workbook.sheet("Needs Review")
    sources_sheet = workbook.sheet("Sources")
    if professor_sheet is None:
        issues.append(ContractIssue("workbook", "缺少 Professors 工作表"))
        professor_rows: list[dict[str, str]] = []
        include_user_fields = False
        columns = SAFE_COLUMNS
    else:
        raw_header = (
            tuple(value.strip() for value in _trim_row(professor_sheet.rows[0]))
            if professor_sheet.rows
            else ()
        )
        if raw_header == FULL_COLUMNS:
            columns = FULL_COLUMNS
            include_user_fields = True
        else:
            columns = SAFE_COLUMNS
            include_user_fields = False
        professor_rows = _table_rows(professor_sheet, columns=columns, issues=issues)

    if review_sheet is None:
        issues.append(ContractIssue("workbook", "缺少 Needs Review 工作表"))
        review_rows: list[dict[str, str]] = []
    else:
        review_rows = _table_rows(review_sheet, columns=REVIEW_FIELDS, issues=issues)

    if sources_sheet is None:
        issues.append(ContractIssue("workbook", "缺少 Sources 工作表"))
        source_rows: list[dict[str, str]] = []
    else:
        source_rows = _table_rows(sources_sheet, columns=SOURCE_FIELDS, issues=issues)

    full_records = [
        {field: row.get(field, "") for field in FULL_COLUMNS} for row in professor_rows
    ]
    raw_payload = {
        "records": full_records,
        "review": review_rows,
        "sources": source_rows,
    }
    try:
        canonical, normalizations = canonicalize_payload(
            raw_payload,
            include_user_fields=include_user_fields,
        )
    except ContractValidationError as error:
        issues.extend(error.issues)
        canonical = {"records": [], "review": [], "sources": []}
        normalizations = []

    if not issues:
        for index, expected in enumerate(canonical["records"]):
            for field in columns:
                actual = professor_rows[index][field].strip()
                if actual != expected[field]:
                    issues.append(
                        ContractIssue(
                            f"Professors!{field}[{index + 2}]",
                            f"必须使用规范值 {expected[field]!r}，实际为 {actual!r}",
                        )
                    )
        for change in normalizations:
            issues.append(
                ContractIssue(
                    change["path"],
                    f"工作簿仍需规范化：{change['from']!r} → {change['to']!r}",
                )
            )

    return {
        "ok": not issues,
        "path": str(path),
        "mode": "full" if include_user_fields else "crawl-safe",
        "columns": list(columns),
        "record_count": len(professor_rows),
        "review_count": len(review_rows),
        "source_count": len(source_rows),
        "active_sheet": workbook.active_sheet_name,
        "sheets": sheet_summaries,
        "formula_count": sum(item["formula_count"] for item in sheet_summaries),
        "error_value_count": sum(item["error_value_count"] for item in sheet_summaries),
        "errors": [item.as_dict() for item in issues],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate an XLSX against the Auto Email Sender professor import contract."
    )
    parser.add_argument("xlsx", type=Path)
    parser.add_argument(
        "--details", action="store_true", help="展开工作表结构及全部错误"
    )
    args = parser.parse_args(argv)
    path = args.xlsx.expanduser().resolve()
    result = validate(path)
    result["error_count"] = len(result["errors"])
    if not result["ok"]:
        result.setdefault("code", "CONTRACT_VIOLATION")
    result["next_action"] = (
        "deliver_xlsx"
        if result["ok"]
        else "修正候选 JSON 后重新生成；此校验要求抓取交付证据完整，全部问题使用 --details"
    )
    if not args.details:
        for key in (
            "columns",
            "active_sheet",
            "sheets",
            "formula_count",
            "error_value_count",
        ):
            result.pop(key, None)
        result["errors"] = result["errors"][:10]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
