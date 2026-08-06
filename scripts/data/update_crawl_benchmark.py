from __future__ import annotations

import argparse
import os
from pathlib import Path
import platform
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.crawl_benchmark_publication import (  # noqa: E402
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
    return parser


def resolve_default_database_path() -> Path:
    configured_data_dir = os.environ.get("AUTO_EMAIL_SENDER_DATA_DIR", "").strip()
    if configured_data_dir:
        return Path(configured_data_dir).expanduser() / "auto_email_sender.db"

    system = platform.system()
    if system == "Darwin":
        data_dir = Path.home() / "Library" / "Application Support" / "auto-email-sender-desktop"
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


def main() -> int:
    args = build_parser().parse_args()
    database_path = args.database or resolve_default_database_path()
    output_path = args.output.expanduser().resolve()

    database_records = load_database_records(database_path)
    existing_records = load_existing_public_records(output_path)
    imported_legacy_records = (
        load_legacy_xlsx_records(args.legacy_xlsx)
        if args.legacy_xlsx is not None
        else []
    )
    records = merge_public_records(
        database_records,
        [*existing_records, *imported_legacy_records],
    )
    payload = build_publication_payload(records)
    write_publication_payload(output_path, payload)

    target_count = count_public_targets(records)
    print(
        f"已更新 {output_path}：{len(records)} 条脱敏运行记录，"
        f"覆盖 {target_count} 个学校/学院目标。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
