from __future__ import annotations

import json
import unittest
from pathlib import Path

from auto_email_sender_cli.capabilities import get_capability


ROOT = Path(__file__).resolve().parents[2]
COVERAGE_FILE = ROOT / "docs" / "development" / "agent_cli_concurrency_coverage.json"
BACKEND_TEST_FILE = ROOT / "backend" / "test" / "test_agent_api.py"


class ConcurrencyCoverageTests(unittest.TestCase):
    def test_every_key_object_has_revision_reads_writes_and_conflict_evidence(self) -> None:
        document = json.loads(COVERAGE_FILE.read_text(encoding="utf-8"))
        objects = document.get("objects")
        self.assertIsInstance(objects, list)
        self.assertTrue(objects)
        test_source = BACKEND_TEST_FILE.read_text(encoding="utf-8")
        for item in objects:
            self.assertEqual(item.get("revision_field"), "revision")
            self.assertTrue(item.get("if_revision_supported"))
            self.assertTrue(item.get("conflict_test"))
            self.assertIn(
                f"def {item['conflict_test']}",
                test_source,
                f"missing executable conflict evidence: {item['resource']}",
            )
            for command in [*item.get("read_commands", []), *item.get("write_commands", [])]:
                capability = get_capability(command)
                self.assertIsNotNone(capability, f"unregistered concurrency command: {command}")
                assert capability is not None
                self.assertEqual(capability.availability, "available")

        excluded = document.get("excluded_resources")
        self.assertIsInstance(excluded, dict)
        self.assertTrue(excluded)
        self.assertTrue(all(str(reason).strip() for reason in excluded.values()))


if __name__ == "__main__":
    unittest.main()
