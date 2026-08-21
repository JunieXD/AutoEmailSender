#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

from professor_import_contract import (
    ContractIssue,
    ContractValidationError,
    FULL_COLUMNS,
    SAFE_COLUMNS,
    SPREADSHEET_ERROR_VALUES,
    canonicalize_payload,
)
from xlsx_support import read_workbook, write_professor_workbook


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build an Auto Email Sender professor-import XLSX from validated JSON."
        )
    )
    parser.add_argument("--input", required=True, type=Path, help="candidate JSON path")
    parser.add_argument("--output", required=True, type=Path, help="output .xlsx path")
    parser.add_argument(
        "--include-user-fields",
        action="store_true",
        help="include tags and personal_note columns; use only when explicitly requested",
    )
    return parser


def _error_payload(issues: list[ContractIssue]) -> dict[str, object]:
    return {
        "ok": False,
        "error_count": len(issues),
        "errors": [issue.as_dict() for issue in issues],
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    input_path = args.input.expanduser().resolve()
    output_path = args.output.expanduser().resolve()
    if output_path.suffix.lower() != ".xlsx":
        print(
            json.dumps(
                _error_payload([ContractIssue("--output", "文件扩展名必须是 .xlsx")]),
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2
    try:
        raw = json.loads(input_path.read_text(encoding="utf-8"))
        payload, normalizations = canonicalize_payload(
            raw,
            include_user_fields=args.include_user_fields,
        )
    except FileNotFoundError:
        issues = [ContractIssue("--input", f"文件不存在：{input_path}")]
        print(
            json.dumps(_error_payload(issues), ensure_ascii=False, indent=2),
            file=sys.stderr,
        )
        return 2
    except json.JSONDecodeError as error:
        issues = [
            ContractIssue(
                "--input",
                f"JSON 解析失败：第 {error.lineno} 行第 {error.colno} 列 {error.msg}",
            )
        ]
        print(
            json.dumps(_error_payload(issues), ensure_ascii=False, indent=2),
            file=sys.stderr,
        )
        return 2
    except (OSError, ContractValidationError) as error:
        issues = (
            error.issues
            if isinstance(error, ContractValidationError)
            else [ContractIssue("--input", str(error))]
        )
        print(
            json.dumps(_error_payload(issues), ensure_ascii=False, indent=2),
            file=sys.stderr,
        )
        return 2

    columns = FULL_COLUMNS if args.include_user_fields else SAFE_COLUMNS
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    active_sheet = ""
    sheet_summaries: list[dict[str, int | str]] = []
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{output_path.stem}.",
            suffix=".xlsx",
            dir=output_path.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        write_professor_workbook(
            temporary_path,
            columns=columns,
            records=payload["records"],
            review=payload["review"],
            sources=payload["sources"],
        )
        workbook = read_workbook(temporary_path)
        active_sheet = workbook.active_sheet_name
        sheet_summaries = [
            {
                "name": sheet.name,
                "row_count": len(sheet.rows),
                "column_count": max((len(row) for row in sheet.rows), default=0),
                "formula_count": len(sheet.formulas),
                "error_value_count": sum(
                    1
                    for row in sheet.rows
                    for value in row
                    if value.strip().upper() in SPREADSHEET_ERROR_VALUES
                ),
            }
            for sheet in workbook.sheets
        ]
        if workbook.active_sheet_name != "Professors":
            raise ValueError("生成结果的活动工作表不是 Professors")
        if any(sheet.formulas for sheet in workbook.sheets):
            raise ValueError("生成结果意外包含公式")
        os.replace(temporary_path, output_path)
        temporary_path = None
    except (OSError, ValueError) as error:
        issues = [ContractIssue("--output", str(error))]
        print(
            json.dumps(_error_payload(issues), ensure_ascii=False, indent=2),
            file=sys.stderr,
        )
        return 3
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    result = {
        "ok": True,
        "output": str(output_path),
        "mode": "full" if args.include_user_fields else "crawl-safe",
        "columns": list(columns),
        "record_count": len(payload["records"]),
        "review_count": len(payload["review"]),
        "source_count": len(payload["sources"]),
        "normalizations": normalizations,
        "active_sheet": active_sheet,
        "sheets": sheet_summaries,
        "formula_count": sum(item["formula_count"] for item in sheet_summaries),
        "error_value_count": sum(item["error_value_count"] for item in sheet_summaries),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
