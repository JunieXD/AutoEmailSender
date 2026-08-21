from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings
from app.core.time import as_utc_aware


@dataclass(frozen=True)
class TimeIssue:
    table: str
    primary_key: str
    field: str
    raw_value: str
    issue_type: str
    suggestion: str


@dataclass(frozen=True)
class ReportPaths:
    json_path: Path
    markdown_path: Path


def parse_sqlite_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return as_utc_aware(value)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        return as_utc_aware(datetime.fromisoformat(normalized))
    except ValueError:
        return None


def audit_database(database_path: Path) -> list[TimeIssue]:
    if not database_path.exists():
        raise FileNotFoundError(f"数据库不存在：{database_path}")
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        issues: list[TimeIssue] = []
        issues.extend(
            _audit_order(
                connection,
                "crawl_page_tasks",
                "id",
                "claimed_at",
                "lease_expires_at",
                "lease_expires_before_claimed",
                "检查 page worker 租约写入逻辑",
            )
        )
        issues.extend(
            _audit_order(
                connection,
                "crawl_page_chunks",
                "id",
                "claimed_at",
                "lease_expires_at",
                "lease_expires_before_claimed",
                "检查 chunk worker 租约写入逻辑",
            )
        )
        issues.extend(
            _audit_order(
                connection,
                "crawl_candidate_enrichment_tasks",
                "id",
                "claimed_at",
                "lease_expires_at",
                "lease_expires_before_claimed",
                "检查 enrichment worker 租约写入逻辑",
            )
        )
        issues.extend(
            _audit_order(
                connection,
                "crawl_job_runs",
                "id",
                "started_at",
                "finished_at",
                "finished_before_started",
                "检查抓取运行状态结算逻辑",
            )
        )
        issues.extend(
            _audit_order(
                connection,
                "crawl_jobs",
                "id",
                "created_at",
                "updated_at",
                "updated_before_created",
                "检查任务更新时间写入逻辑",
            )
        )
        issues.extend(
            _audit_order(
                connection,
                "email_tasks",
                "id",
                "created_at",
                "updated_at",
                "updated_before_created",
                "检查邮件任务更新时间写入逻辑",
            )
        )
        issues.extend(_audit_scheduled_at_range(connection))
        issues.extend(_audit_active_seconds(connection))
        return issues
    finally:
        connection.close()


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "select 1 from sqlite_master where type = 'table' and name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _audit_order(
    connection: sqlite3.Connection,
    table: str,
    primary_key: str,
    start_field: str,
    end_field: str,
    issue_type: str,
    suggestion: str,
) -> list[TimeIssue]:
    if not _table_exists(connection, table):
        return []
    rows = connection.execute(
        f"select {primary_key}, {start_field}, {end_field} from {table} where {start_field} is not null and {end_field} is not null"
    ).fetchall()
    issues: list[TimeIssue] = []
    for row in rows:
        start_at = parse_sqlite_datetime(row[start_field])
        end_at = parse_sqlite_datetime(row[end_field])
        if start_at is not None and end_at is not None and end_at < start_at:
            issues.append(
                TimeIssue(
                    table=table,
                    primary_key=str(row[primary_key]),
                    field=end_field,
                    raw_value=str(row[end_field]),
                    issue_type=issue_type,
                    suggestion=suggestion,
                )
            )
    return issues


def _audit_scheduled_at_range(connection: sqlite3.Connection) -> list[TimeIssue]:
    if not _table_exists(connection, "email_tasks"):
        return []
    now = datetime.now(UTC)
    rows = connection.execute(
        "select id, scheduled_at from email_tasks where scheduled_at is not null"
    ).fetchall()
    issues: list[TimeIssue] = []
    for row in rows:
        scheduled_at = parse_sqlite_datetime(row["scheduled_at"])
        if scheduled_at is None:
            continue
        if abs((scheduled_at - now).days) > 3660:
            issues.append(
                TimeIssue(
                    table="email_tasks",
                    primary_key=str(row["id"]),
                    field="scheduled_at",
                    raw_value=str(row["scheduled_at"]),
                    issue_type="scheduled_at_out_of_expected_range",
                    suggestion="确认 scheduled_at 是否被错误地按本地时间或错误年份写入",
                )
            )
    return issues


def _audit_active_seconds(connection: sqlite3.Connection) -> list[TimeIssue]:
    if not _table_exists(connection, "crawl_job_runs"):
        return []
    rows = connection.execute(
        "select id, started_at, finished_at, active_seconds from crawl_job_runs where started_at is not null and finished_at is not null and active_seconds is not null"
    ).fetchall()
    issues: list[TimeIssue] = []
    for row in rows:
        started_at = parse_sqlite_datetime(row["started_at"])
        finished_at = parse_sqlite_datetime(row["finished_at"])
        if started_at is None or finished_at is None:
            continue
        elapsed_seconds = max(0, int((finished_at - started_at).total_seconds()))
        active_seconds = int(row["active_seconds"] or 0)
        if active_seconds > elapsed_seconds + 300:
            issues.append(
                TimeIssue(
                    table="crawl_job_runs",
                    primary_key=str(row["id"]),
                    field="active_seconds",
                    raw_value=str(row["active_seconds"]),
                    issue_type="active_seconds_exceeds_elapsed_time",
                    suggestion="检查 active_started_at / finished_at 是否存在时区误算",
                )
            )
    return issues


def render_markdown_report(issues: Iterable[TimeIssue]) -> str:
    issue_list = list(issues)
    lines = ["# 时间数据审计报告", "", f"问题数量：{len(issue_list)}", ""]
    if not issue_list:
        lines.append("未发现明显时间数据异常。")
        return "\n".join(lines) + "\n"
    lines.extend(
        [
            "| 表 | 主键 | 字段 | 原始值 | 问题类型 | 建议 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for issue in issue_list:
        lines.append(
            f"| {issue.table} | {issue.primary_key} | {issue.field} | {issue.raw_value} | {issue.issue_type} | {issue.suggestion} |"
        )
    return "\n".join(lines) + "\n"


def write_reports(output_directory: Path, issues: Iterable[TimeIssue]) -> ReportPaths:
    output_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    issue_list = list(issues)
    json_path = output_directory / f"time-audit-{timestamp}.json"
    markdown_path = output_directory / f"time-audit-{timestamp}.md"
    json_path.write_text(
        json.dumps(
            [asdict(issue) for issue in issue_list], ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown_report(issue_list), encoding="utf-8")
    return ReportPaths(json_path=json_path, markdown_path=markdown_path)


def _database_path_from_settings() -> Path:
    database_url = get_settings().database_url
    prefix = "sqlite+aiosqlite:///"
    if database_url.startswith(prefix):
        return Path(database_url.removeprefix(prefix))
    prefix = "sqlite:///"
    if database_url.startswith(prefix):
        return Path(database_url.removeprefix(prefix))
    raise ValueError("时间审计脚本当前只支持 SQLite 数据库")


def main() -> None:
    parser = argparse.ArgumentParser(description="审计 SQLite 时间数据异常")
    parser.add_argument("--database", type=Path, default=None)
    parser.add_argument("--output-directory", type=Path, default=Path("../data/logs"))
    args = parser.parse_args()

    database_path = args.database or _database_path_from_settings()
    issues = audit_database(database_path)
    paths = write_reports(args.output_directory, issues)
    print(f"JSON: {paths.json_path}")
    print(f"Markdown: {paths.markdown_path}")
    print(f"Issues: {len(issues)}")


if __name__ == "__main__":
    main()
