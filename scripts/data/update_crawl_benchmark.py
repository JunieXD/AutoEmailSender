from __future__ import annotations

import argparse
import json
import os
import platform
import sqlite3
import sys
from pathlib import Path
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.crawl_benchmark_publication import (
    build_publication_payload,
    count_public_targets,
    load_database_records,
    load_existing_public_records,
    load_legacy_xlsx_records,
    merge_public_records,
    write_publication_payload,
)

DEFAULT_OUTPUT = REPOSITORY_ROOT / "website" / "data" / "crawl-benchmark.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="从本地数据库生成官网使用的脱敏智能抓取实测数据。",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=None,
        help="auto_email_sender.db 路径；省略时自动寻找桌面应用数据目录。",
    )
    parser.add_argument(
        "--legacy-xlsx",
        type=Path,
        default=None,
        help="可选：导入早期的智能抓取测试记录 XLSX。",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"输出 JSON 路径，默认：{DEFAULT_OUTPUT}",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="读取同一输出文件作为合并基线，只预览，不写文件",
    )
    parser.add_argument(
        "--json", action="store_true", help="输出 Agent 可读的摘要、错误码和下一步"
    )
    return parser


def resolve_default_database_path() -> Path:
    configured_data_dir = os.environ.get("AUTO_EMAIL_SENDER_DATA_DIR", "").strip()
    if configured_data_dir:
        return Path(configured_data_dir).expanduser() / "auto_email_sender.db"

    system = platform.system()
    if system == "Darwin":
        data_dir = (
            Path.home()
            / "Library"
            / "Application Support"
            / "auto-email-sender-desktop"
        )
    elif system == "Windows":
        app_data = os.environ.get("APPDATA", "").strip()
        data_dir = (
            Path(app_data) / "auto-email-sender-desktop"
            if app_data
            else Path.home() / "AppData" / "Roaming" / "auto-email-sender-desktop"
        )
    else:
        config_home = os.environ.get("XDG_CONFIG_HOME", "").strip()
        data_dir = (
            Path(config_home) / "auto-email-sender-desktop"
            if config_home
            else Path.home() / ".config" / "auto-email-sender-desktop"
        )
    return data_dir / "auto_email_sender.db"


class UpdateError(ValueError):
    def __init__(self, code: str, action: str):
        self.code = code
        self.action = action
        super().__init__(code)


def _existing_snapshot(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        raise UpdateError(
            "INVALID_BASELINE",
            "修复输出 JSON 或恢复远端最新版本后重试；不覆盖损坏的合并基线",
        ) from None
    if (
        not isinstance(value, dict)
        or value.get("schemaVersion") not in (2, 3)
        or not isinstance(value.get("records"), list)
    ):
        raise UpdateError(
            "UNSUPPORTED_BASELINE",
            "使用有效的 Schema 2/3 基线；Schema 1 迁移见 docs/operations/crawl-benchmark-publication.md",
        )
    ids = [
        item.get("recordId") if isinstance(item, dict) else None
        for item in value["records"]
    ]
    if any(not isinstance(item, str) or not item for item in ids) or len(
        set(ids)
    ) != len(ids):
        raise UpdateError("INVALID_BASELINE", "修复基线中缺失或重复的 recordId 后重试")
    return value


def update(args: argparse.Namespace) -> dict[str, object]:
    database_path = (
        (args.database or resolve_default_database_path()).expanduser().resolve()
    )
    output_path = args.output.expanduser().resolve()
    inputs = [database_path]
    if args.legacy_xlsx:
        inputs.append(args.legacy_xlsx.expanduser().resolve())
    if output_path in inputs:
        raise UpdateError(
            "OUTPUT_IS_INPUT", "--output 必须与输入数据库和历史工作簿使用不同路径"
        )
    original = _existing_snapshot(output_path)
    existing_records = load_existing_public_records(output_path)
    if original and len(existing_records) != len(original["records"]):
        raise UpdateError(
            "INVALID_BASELINE",
            "基线含无法保留的记录；核对原始公共 JSON 后重试，避免丢失其他电脑的数据",
        )
    database_records = load_database_records(database_path)
    imported = load_legacy_xlsx_records(args.legacy_xlsx) if args.legacy_xlsx else []
    records = merge_public_records(database_records, [*existing_records, *imported])
    before = {
        item["recordId"]: item for item in (original["records"] if original else [])
    }
    after = {item["recordId"]: item for item in records}
    counts = {
        "added": len(after.keys() - before.keys()),
        "updated": sum(
            before[key] != after[key] for key in after.keys() & before.keys()
        ),
        "retained": sum(
            before[key] == after[key] for key in after.keys() & before.keys()
        ),
        "removed": len(before.keys() - after.keys()),
    }
    payload = build_publication_payload(records)
    changed = original is None or {
        key: value for key, value in original.items() if key != "generatedAt"
    } != {key: value for key, value in payload.items() if key != "generatedAt"}
    if not args.dry_run and changed:
        # Detect a local edit between reading the baseline and writing the merge.
        if _existing_snapshot(output_path) != original:
            raise UpdateError(
                "BASELINE_CHANGED", "输出文件在读取后变化；重新运行并合并最新基线"
            )
        write_publication_payload(output_path, payload)
    return {
        "ok": True,
        "status": ("planned" if args.dry_run else "updated")
        if changed
        else "unchanged",
        "output": str(output_path),
        "schema_version": payload["schemaVersion"],
        "record_count": len(records),
        "target_count": count_public_targets(records),
        "changes": counts,
        "changed": changed,
        "written": changed and not args.dry_run,
        "next_action": "以相同参数移除 --dry-run 写入，然后审查 diff"
        if args.dry_run and changed
        else ("审查 diff 并执行 Skill 中的验证命令" if changed else "none"),
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = update(args)
    except UpdateError as error:
        result = {"ok": False, "code": error.code, "next_action": error.action}
    except FileNotFoundError:
        result = {
            "ok": False,
            "code": "INPUT_NOT_FOUND",
            "next_action": "检查 --database / --legacy-xlsx 路径；省略数据库时核对桌面应用数据目录",
        }
    except sqlite3.Error:
        result = {
            "ok": False,
            "code": "DATABASE_READ_FAILED",
            "next_action": "检查数据库是否有效及其 schema 是否受支持；此脚本不会迁移数据库",
        }
    except (ValueError, BadZipFile, ParseError):
        result = {
            "ok": False,
            "code": "INVALID_INPUT",
            "next_action": "检查历史工作簿格式、公共 JSON 和别名配置；修复后重试",
        }
    except OSError:
        result = {
            "ok": False,
            "code": "IO_ERROR",
            "next_action": "检查输入读取权限和输出写入权限后重试",
        }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result["ok"]:
        counts = result["changes"]
        print(
            f"{result['status']} {result['output']}：{result['record_count']} 条脱敏记录，{result['target_count']} 个目标；新增 {counts['added']}、更新 {counts['updated']}、保留 {counts['retained']}。"
        )
    else:
        print(f"{result['code']}: {result['next_action']}", file=sys.stderr)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
