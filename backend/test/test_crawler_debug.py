from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from app.core.config import get_settings
from app.modules.crawler.pages.debug import (
    append_crawler_worker_debug_event,
    crawler_debug_file_path,
)


class CrawlerRuntimeDebugLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_debug_dir = os.environ.get("CRAWLER_DEBUG_DIR")
        self.previous_debug = os.environ.get("CRAWLER_DEBUG")
        os.environ["CRAWLER_DEBUG_DIR"] = str(Path(self.temp_dir.name) / "crawler")
        os.environ["CRAWLER_DEBUG"] = "1"
        get_settings.cache_clear()

    def tearDown(self) -> None:
        if self.previous_debug_dir is None:
            os.environ.pop("CRAWLER_DEBUG_DIR", None)
        else:
            os.environ["CRAWLER_DEBUG_DIR"] = self.previous_debug_dir
        if self.previous_debug is None:
            os.environ.pop("CRAWLER_DEBUG", None)
        else:
            os.environ["CRAWLER_DEBUG"] = self.previous_debug
        get_settings.cache_clear()
        self.temp_dir.cleanup()

    def test_worker_debug_event_uses_existing_export_file_path(self) -> None:
        debug_file = append_crawler_worker_debug_event(
            42,
            worker_kind="chunk",
            event_name="llm_response",
            work_item_id=7,
            payload={"parsed_payload": {"chunk_status": "completed"}},
        )

        self.assertEqual(debug_file, crawler_debug_file_path(42))
        assert debug_file is not None
        rows = [
            json.loads(line)
            for line in debug_file.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(len(rows), 1)
        raw_event = rows[0]["raw_event"]
        self.assertEqual(raw_event["worker_kind"], "chunk")
        self.assertEqual(raw_event["event_name"], "llm_response")
        self.assertEqual(raw_event["work_item_id"], "7")
        self.assertEqual(raw_event["parsed_payload"]["chunk_status"], "completed")

    def test_worker_debug_event_summarizes_large_content(self) -> None:
        debug_file = append_crawler_worker_debug_event(
            43,
            worker_kind="page",
            event_name="page_fetched",
            work_item_id="task-1",
            payload={"content": "x" * 2000, "nested": {"chunk_content": "y" * 2000}},
        )

        assert debug_file is not None
        row = json.loads(debug_file.read_text(encoding="utf-8").splitlines()[0])
        raw_event = row["raw_event"]
        self.assertLess(len(raw_event["content"]), 700)
        self.assertIn("已截断", raw_event["content"])
        self.assertLess(len(raw_event["nested"]["chunk_content"]), 700)
        self.assertIn("已截断", raw_event["nested"]["chunk_content"])


if __name__ == "__main__":
    unittest.main()
