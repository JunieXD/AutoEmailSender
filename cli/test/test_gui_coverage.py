from __future__ import annotations

import json
import unittest
from pathlib import Path

from auto_email_sender_cli.capabilities import get_capability


ROOT = Path(__file__).resolve().parents[2]
COVERAGE_FILE = ROOT / "docs" / "agent_cli_gui_coverage.json"
API_DIR = ROOT / "frontend" / "src" / "lib" / "api"


class GuiCoverageTests(unittest.TestCase):
    def test_every_business_api_module_has_an_explicit_cli_classification(self) -> None:
        document = json.loads(COVERAGE_FILE.read_text(encoding="utf-8"))
        actions = document.get("actions")
        self.assertIsInstance(actions, list)
        by_source = {item.get("source"): item for item in actions if isinstance(item, dict)}
        business_sources = {
            path.name
            for path in API_DIR.glob("*.ts")
            if not path.name.endswith(".test.ts")
            and path.name not in set(document.get("excluded_sources", []))
        }
        self.assertEqual(set(by_source), business_sources)
        self.assertEqual(len(by_source), len(actions))

        allowed_statuses = {"available", "ui_only", "planned", "unsupported_on_platform"}
        for item in actions:
            self.assertIn(item.get("status"), allowed_statuses, item)
            self.assertTrue(item.get("id"), item)
            self.assertTrue(item.get("reason"), item)
            ui_only = set(item.get("ui_only_capabilities", []))
            commands = item.get("required_capabilities", [])
            self.assertIsInstance(commands, list)
            for command in commands:
                capability = get_capability(command)
                self.assertIsNotNone(capability, f"{item['id']} -> {command}")
                assert capability is not None
                if item["status"] == "available":
                    self.assertIn(
                        capability.availability,
                        {"available", "ui_only"},
                        f"{item['id']} -> {command}",
                    )
                    if capability.availability == "ui_only":
                        self.assertIn(command, ui_only, f"{item['id']} missing ui_only declaration")
                elif item["status"] == "ui_only":
                    self.assertIn(capability.availability, {"ui_only", "planned", "unsupported_on_platform"})


if __name__ == "__main__":
    unittest.main()
