from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.audit_time_data import TimeIssue, render_markdown_report, write_reports


class AuditTimeDataTest(unittest.TestCase):
    def test_render_markdown_report_contains_issue_details(self) -> None:
        issue = TimeIssue(
            table="crawl_chunks",
            primary_key="1",
            field="lease_expires_at",
            raw_value="2026-05-31 06:00:00",
            issue_type="lease_expires_before_claimed",
            suggestion="检查 worker 租约写入逻辑",
        )

        markdown = render_markdown_report([issue])

        self.assertIn("crawl_chunks", markdown)
        self.assertIn("lease_expires_before_claimed", markdown)
        self.assertIn("检查 worker 租约写入逻辑", markdown)

    def test_write_reports_creates_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = write_reports(Path(directory), [])
            self.assertTrue(paths.json_path.exists())
            self.assertTrue(paths.markdown_path.exists())

        self.assertTrue(paths.json_path.name.endswith(".json"))
        self.assertTrue(paths.markdown_path.name.endswith(".md"))


if __name__ == "__main__":
    unittest.main()