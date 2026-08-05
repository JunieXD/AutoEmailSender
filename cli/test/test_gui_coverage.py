from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from auto_email_sender_cli.capabilities import get_capability


ROOT = Path(__file__).resolve().parents[2]
COVERAGE_FILE = ROOT / "docs" / "agent_cli_gui_coverage.json"
API_DIR = ROOT / "frontend" / "src" / "lib" / "api"
EXPORT_PATTERN = re.compile(
    r"^export\s+(?:async\s+)?(?:const|function|class)\s+([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)


class GuiCoverageTests(unittest.TestCase):
    def test_every_business_api_module_has_an_explicit_cli_classification(self) -> None:
        document = json.loads(COVERAGE_FILE.read_text(encoding="utf-8"))
        self.assertEqual(document.get("schema_version"), 2)
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
            source = item["source"]
            exported_names = set(
                EXPORT_PATTERN.findall((API_DIR / source).read_text(encoding="utf-8")),
            )
            classified_actions = item.get("exported_actions")
            self.assertIsInstance(classified_actions, list, item)
            classified_names = {
                name
                for name in classified_actions
                if isinstance(name, str) and name
            }
            self.assertEqual(len(classified_names), len(classified_actions), item)
            excluded_exports = item.get("excluded_exports", {})
            self.assertIsInstance(excluded_exports, dict, item)
            self.assertTrue(
                all(isinstance(name, str) and isinstance(reason, str) and reason for name, reason in excluded_exports.items()),
                item,
            )
            self.assertFalse(classified_names & set(excluded_exports), item)
            self.assertEqual(classified_names | set(excluded_exports), exported_names, source)

            overrides = item.get("action_overrides", {})
            self.assertIsInstance(overrides, dict, item)
            self.assertTrue(set(overrides).issubset(classified_names), item)
            for action_name in classified_names:
                override = overrides.get(action_name, {})
                self.assertIsInstance(override, dict, f"{source}:{action_name}")
                action_status = override.get("status", item.get("status"))
                self.assertIn(action_status, allowed_statuses, f"{source}:{action_name}")
                self.assertTrue(
                    override.get("reason", item.get("reason")),
                    f"{source}:{action_name}",
                )
                action_commands = override.get(
                    "required_capabilities",
                    item.get("required_capabilities", []),
                )
                self.assertIsInstance(action_commands, list, f"{source}:{action_name}")
                for command in action_commands:
                    capability = get_capability(command)
                    self.assertIsNotNone(capability, f"{source}:{action_name} -> {command}")
                    assert capability is not None
                    if action_status == "available":
                        self.assertIn(
                            capability.availability,
                            {"available", "ui_only"},
                            f"{source}:{action_name} -> {command}",
                        )
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
