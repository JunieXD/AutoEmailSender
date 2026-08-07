from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from openpyxl import Workbook

from app.services.crawl_benchmark_publication import (
    LEGACY_HEADERS,
    build_publication_payload,
    load_database_records,
    load_existing_legacy_records,
    load_existing_public_records,
    load_legacy_xlsx_records,
    merge_public_records,
    write_publication_payload,
)


def make_public_legacy_record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "recordId": "legacy-0123456789abcdef",
        "sourceKind": "legacy_xlsx",
        "university": "历史大学",
        "school": "信息学院",
        "startUrl": "https://history.example.edu/faculty",
        "entryType": "list",
        "testedAt": None,
        "appVersion": "2.3.7",
        "modelName": "legacy-model",
        "publicStatus": "verified",
        "candidateCount": 10,
        "emailCount": 9,
        "titleCount": 8,
        "researchDirectionCount": 7,
        "enrichmentSelectedCount": None,
        "enrichmentSucceededCount": None,
        "enrichmentPendingCount": None,
        "enrichmentFailedCount": None,
        "pageCount": None,
        "durationSeconds": 120,
        "inputTokens": 100,
        "cachedTokens": 20,
        "outputTokens": 30,
        "totalTokens": 130,
    }
    record.update(overrides)
    return record


class CrawlBenchmarkDatabasePublicationTests(unittest.TestCase):
    def test_database_export_only_contains_public_aggregate_records(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "auto_email_sender.db"
            self._create_database(database_path)

            records = load_database_records(database_path)

        self.assertEqual(len(records), 2)
        verified = next(record for record in records if record["publicStatus"] == "verified")
        adapting = next(record for record in records if record["publicStatus"] == "adapting")

        self.assertEqual(verified["university"], "南岭大学")
        self.assertEqual(verified["candidateCount"], 2)
        self.assertEqual(verified["emailCount"], 1)
        self.assertEqual(verified["titleCount"], 2)
        self.assertEqual(verified["researchDirectionCount"], 1)
        self.assertEqual(verified["enrichmentSelectedCount"], 2)
        self.assertEqual(verified["enrichmentSucceededCount"], 1)
        self.assertEqual(verified["enrichmentPendingCount"], 0)
        self.assertEqual(verified["enrichmentFailedCount"], 1)
        self.assertEqual(verified["pageCount"], 2)
        self.assertEqual(verified["appVersion"], "2.4.2")
        self.assertEqual(verified["modelName"], "public-model")
        self.assertEqual(adapting["candidateCount"], 0)

        serialized = json.dumps(records, ensure_ascii=False)
        self.assertNotIn("secret@example.edu", serialized)
        self.assertNotIn("error_message", serialized)
        self.assertNotIn("jobId", serialized)

    def test_same_job_keeps_record_id_when_later_enrichment_updates_counts(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "auto_email_sender.db"
            self._create_database(database_path)
            before = load_database_records(database_path)

            connection = sqlite3.connect(database_path)
            try:
                connection.execute(
                    "UPDATE crawl_candidates SET email = ? WHERE id = 2",
                    ("later@example.edu",),
                )
                connection.execute(
                    """
                    UPDATE crawl_candidate_enrichment_tasks
                    SET status = 'succeeded'
                    WHERE id = 2
                    """
                )
                connection.execute(
                    """
                    UPDATE crawl_job_runs
                    SET finished_at = '2026-08-04 10:05:00'
                    WHERE id = 11
                    """
                )
                connection.commit()
            finally:
                connection.close()

            after = load_database_records(database_path)

        before_record = next(record for record in before if record["school"] == "计算机学院")
        after_record = next(record for record in after if record["school"] == "计算机学院")
        self.assertEqual(after_record["recordId"], before_record["recordId"])
        self.assertEqual(after_record["emailCount"], 2)
        self.assertEqual(after_record["enrichmentSucceededCount"], 2)

    def test_records_from_two_databases_with_same_local_ids_are_merged(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            first_database = root / "first.db"
            second_database = root / "second.db"
            output_path = root / "crawl-benchmark.json"
            self._create_database(first_database)
            self._create_database(second_database)

            connection = sqlite3.connect(second_database)
            try:
                connection.execute(
                    """
                    UPDATE crawl_jobs
                    SET university = ?, school = ?, start_url = ?, created_at = ?
                    WHERE id = 1
                    """,
                    (
                        "海滨大学",
                        "人工智能学院",
                        "https://coastal.example.edu/faculty",
                        "2026-08-05 09:00:00",
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            first_records = load_database_records(first_database)
            write_publication_payload(
                output_path,
                build_publication_payload(first_records),
            )
            existing_records = load_existing_public_records(output_path)
            merged = merge_public_records(
                load_database_records(second_database),
                existing_records,
            )

        verified_targets = {
            (record["university"], record["school"])
            for record in merged
            if record["publicStatus"] == "verified"
        }
        self.assertIn(("南岭大学", "计算机学院"), verified_targets)
        self.assertIn(("海滨大学", "人工智能学院"), verified_targets)
        self.assertEqual(len({record["recordId"] for record in merged}), len(merged))

    @staticmethod
    def _create_database(database_path: Path) -> None:
        connection = sqlite3.connect(database_path)
        try:
            connection.executescript(
                """
                CREATE TABLE llm_profiles (
                    id INTEGER PRIMARY KEY,
                    model_name TEXT,
                    api_key TEXT
                );
                CREATE TABLE crawl_jobs (
                    id INTEGER PRIMARY KEY,
                    university TEXT NOT NULL,
                    school TEXT NOT NULL,
                    start_url TEXT NOT NULL,
                    entry_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    job_kind TEXT NOT NULL,
                    deleted_at TEXT,
                    current_run_id INTEGER,
                    llm_profile_id INTEGER,
                    created_at TEXT,
                    updated_at TEXT
                );
                CREATE TABLE crawl_job_runs (
                    id INTEGER PRIMARY KEY,
                    app_version TEXT,
                    active_seconds INTEGER,
                    input_tokens INTEGER,
                    cached_tokens INTEGER,
                    output_tokens INTEGER,
                    total_tokens INTEGER,
                    finished_at TEXT,
                    updated_at TEXT
                );
                CREATE TABLE crawl_candidates (
                    id INTEGER PRIMARY KEY,
                    job_id INTEGER,
                    name TEXT,
                    email TEXT,
                    title TEXT,
                    research_direction TEXT
                );
                CREATE TABLE crawl_pages (
                    id INTEGER PRIMARY KEY,
                    job_id INTEGER
                );
                CREATE TABLE crawl_worker_token_usages (
                    id INTEGER PRIMARY KEY,
                    job_id INTEGER,
                    model_name TEXT
                );
                CREATE TABLE crawl_candidate_enrichment_tasks (
                    id INTEGER PRIMARY KEY,
                    job_id INTEGER,
                    candidate_id INTEGER,
                    status TEXT
                );
                """
            )
            connection.execute(
                "INSERT INTO llm_profiles (id, model_name, api_key) VALUES (1, ?, ?)",
                ("mutable-model", "never-publish-this-key"),
            )
            jobs = [
                (
                    1,
                    "南岭大学",
                    "计算机学院",
                    "https://example.edu/faculty",
                    "list",
                    "needs_review",
                    "faculty_crawl",
                    None,
                    11,
                    1,
                ),
                (
                    2,
                    "南岭大学",
                    "软件学院",
                    "https://example.edu/software",
                    "list",
                    "failed",
                    "faculty_crawl",
                    None,
                    12,
                    1,
                ),
                (
                    3,
                    "示例大学",
                    "取消任务学院",
                    "https://example.edu/canceled",
                    "list",
                    "canceled",
                    "faculty_crawl",
                    None,
                    13,
                    1,
                ),
                (
                    4,
                    "bjtu",
                    "cs",
                    "https://example.edu/internal",
                    "list",
                    "needs_review",
                    "faculty_crawl",
                    None,
                    14,
                    1,
                ),
                (
                    5,
                    "测试大学",
                    "测试学院",
                    "https://example.edu/placeholder",
                    "list",
                    "completed",
                    "faculty_crawl",
                    None,
                    15,
                    1,
                ),
                (
                    6,
                    "南岭大学",
                    "物理学院",
                    "https://user:password@example.edu/faculty",
                    "list",
                    "completed",
                    "faculty_crawl",
                    None,
                    16,
                    1,
                ),
            ]
            connection.executemany(
                """
                INSERT INTO crawl_jobs (
                    id, university, school, start_url, entry_type, status,
                    job_kind, deleted_at, current_run_id, llm_profile_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '2026-08-03 10:00:00', '2026-08-03 10:05:00')
                """,
                jobs,
            )
            connection.executemany(
                """
                INSERT INTO crawl_job_runs (
                    id, app_version, active_seconds, input_tokens, cached_tokens,
                    output_tokens, total_tokens, finished_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, '2026-08-03 10:05:00', '2026-08-03 10:05:00')
                """,
                [
                    (11, "2.4.2", 65, 100, 30, 20, 120),
                    (12, "2.4.2", 15, 10, 0, 2, 12),
                    (13, "2.4.2", 5, 0, 0, 0, 0),
                    (14, "2.4.2", 20, 20, 0, 4, 24),
                    (15, "2.4.2", 20, 20, 0, 4, 24),
                    (16, "2.4.2", 20, 20, 0, 4, 24),
                ],
            )
            connection.executemany(
                """
                INSERT INTO crawl_candidates (
                    id, job_id, name, email, title, research_direction
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (1, 1, "公开汇总前会移除此姓名", "secret@example.edu", "教授", "人工智能"),
                    (2, 1, "另一姓名", None, "副教授", None),
                ],
            )
            connection.executemany(
                "INSERT INTO crawl_pages (id, job_id) VALUES (?, ?)",
                [(1, 1), (2, 1)],
            )
            connection.execute(
                "INSERT INTO crawl_worker_token_usages (id, job_id, model_name) VALUES (1, 1, ?)",
                ("public-model",),
            )
            connection.executemany(
                """
                INSERT INTO crawl_candidate_enrichment_tasks (
                    id, job_id, candidate_id, status
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    (1, 1, 1, "succeeded"),
                    (2, 1, 2, "failed_terminal"),
                ],
            )
            connection.commit()
        finally:
            connection.close()


class CrawlBenchmarkLegacyWorkbookTests(unittest.TestCase):
    def test_legacy_workbook_is_normalized_without_manual_fields(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            workbook_path = Path(temporary_directory) / "legacy.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "分学校抓取数据"
            headers = list(LEGACY_HEADERS.values())
            sheet.append(headers)
            values = {
                "学校": "历史大学",
                "学院": "信息学院",
                "列表页首页链接": "https://history.example.edu/faculty",
                "系统版本号": "2.3.7",
                "模型": "legacy-model",
                "抓取方式": "列表页首页",
                "抓取导师数": 10,
                "有邮箱记录数": 9,
                "有职称记录数": 8,
                "有研究方向记录数": 7,
                "总共耗时": "1小时2分3秒",
                "输入Token数": 100,
                "缓存命中Token数": 20,
                "输出Token数": 30,
                "总Token数": 130,
            }
            sheet.append([values[header] for header in headers])
            workbook.save(workbook_path)

            records = load_legacy_xlsx_records(workbook_path)

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["durationSeconds"], 3723)
        self.assertEqual(record["publicStatus"], "verified")
        self.assertEqual(record["appVersion"], "2.3.7")
        self.assertIsNone(record["testedAt"])

    def test_existing_legacy_rows_survive_future_database_only_updates(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "crawl-benchmark.json"
            legacy_record = make_public_legacy_record()
            database_record = {
                "recordId": "db-one",
                "sourceKind": "database",
                "university": "当前大学",
                "school": "计算机学院",
            }
            payload = build_publication_payload(
                [legacy_record, database_record],
                generated_at=datetime(2026, 8, 3, tzinfo=UTC),
            )
            write_publication_payload(output_path, payload)

            preserved = load_existing_legacy_records(output_path)
            merged = merge_public_records([database_record], preserved)

        self.assertEqual(
            {record["recordId"] for record in merged},
            {"legacy-0123456789abcdef", "db-one"},
        )

    def test_existing_legacy_rows_are_resanitized_before_reuse(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "crawl-benchmark.json"
            safe_record = make_public_legacy_record(
                email="must-not-survive@example.edu",
                errorLog="must not survive",
                emailCount=99,
            )
            credential_url_record = make_public_legacy_record(
                recordId="legacy-fedcba9876543210",
                startUrl="https://user:password@history.example.edu/faculty",
            )
            output_path.write_text(
                json.dumps({"records": [safe_record, credential_url_record]}),
                encoding="utf-8",
            )

            preserved = load_existing_legacy_records(output_path)

        self.assertEqual(len(preserved), 1)
        self.assertNotIn("email", preserved[0])
        self.assertNotIn("errorLog", preserved[0])
        self.assertEqual(preserved[0]["emailCount"], 10)


if __name__ == "__main__":
    unittest.main()
