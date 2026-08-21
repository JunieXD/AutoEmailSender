from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from auto_email_sender_cli.capabilities import get_capability


ROOT = Path(__file__).resolve().parents[2]
COVERAGE_FILE = ROOT / "docs" / "development" / "agent_cli_gui_coverage.json"
FRONTEND_SRC = ROOT / "frontend" / "src"
BUSINESS_API_GLOBS = (
    "lib/api/*.ts",
    "entities/*/api/*.ts",
)
_DIRECT_EXPORT_PATTERN = re.compile(
    r"^\s*export\s+(?:default\s+)?(?:async\s+)?(?:const|function|class)\s+([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)
_NAMED_EXPORT_PATTERN = re.compile(r"export\s*\{(?P<body>[^}]*)\}", re.DOTALL)
_STAR_REEXPORT_PATTERN = re.compile(
    r"export\s*\*\s*from\s*['\"](?P<target>[^'\"]+)['\"]\s*;?",
)


def extract_exported_actions(source: str) -> set[str]:
    """Extract value exports, including re-exports, without counting TS types.

    The previous single-line pattern silently dropped ``export { run }`` and
    ``export default function run()``.  This lightweight parser intentionally
    handles the export grammar used by the frontend API modules while keeping
    the coverage check dependency-free (it runs before npm dependencies are
    installed in some CI jobs).
    """

    names = set(_DIRECT_EXPORT_PATTERN.findall(source))
    for match in _NAMED_EXPORT_PATTERN.finditer(source):
        for raw_specifier in match.group("body").split(","):
            specifier = raw_specifier.strip()
            if not specifier or specifier.startswith("type "):
                continue
            parts = re.split(r"\s+as\s+", specifier, maxsplit=1)
            exported_name = parts[-1].strip()
            if re.fullmatch(r"[A-Za-z_$][\w$]*", exported_name):
                names.add(exported_name)
    return names


def extract_exported_actions_from_file(
    path: Path,
    *,
    visited: set[Path] | None = None,
) -> set[str]:
    resolved_path = path.resolve()
    seen = visited if visited is not None else set()
    if resolved_path in seen:
        return set()
    seen.add(resolved_path)

    source = resolved_path.read_text(encoding="utf-8")
    names = extract_exported_actions(source)
    for match in _STAR_REEXPORT_PATTERN.finditer(source):
        target = match.group("target")
        if target.startswith("@/"):
            base = FRONTEND_SRC / target.removeprefix("@/")
        elif target.startswith("."):
            base = resolved_path.parent / target
        else:
            continue

        candidates = (
            base,
            Path(f"{base}.ts"),
            Path(f"{base}.tsx"),
            base / "index.ts",
            base / "index.tsx",
        )
        reexport_path = next(
            (candidate for candidate in candidates if candidate.is_file()),
            None,
        )
        if reexport_path is None:
            raise AssertionError(
                f"无法解析 Frontend re-export: {resolved_path} -> {target}"
            )
        names.update(
            extract_exported_actions_from_file(reexport_path, visited=seen),
        )
    return names


class GuiCoverageTests(unittest.TestCase):
    def test_every_business_api_module_has_an_explicit_cli_classification(self) -> None:
        document = json.loads(COVERAGE_FILE.read_text(encoding="utf-8"))
        self.assertEqual(document.get("schema_version"), 2)
        actions = document.get("actions")
        self.assertIsInstance(actions, list)
        by_source = {
            item.get("source"): item for item in actions if isinstance(item, dict)
        }
        excluded_sources = set(document.get("excluded_sources", []))
        business_sources = {
            path.relative_to(FRONTEND_SRC).as_posix()
            for pattern in BUSINESS_API_GLOBS
            for path in FRONTEND_SRC.glob(pattern)
            if not path.name.endswith(".test.ts")
            and path.relative_to(FRONTEND_SRC).as_posix() not in excluded_sources
        }
        self.assertEqual(set(by_source), business_sources)
        self.assertEqual(len(by_source), len(actions))

        allowed_statuses = {
            "available",
            "ui_only",
            "planned",
            "unsupported_on_platform",
        }
        for item in actions:
            self.assertIn(item.get("status"), allowed_statuses, item)
            self.assertTrue(item.get("id"), item)
            self.assertTrue(item.get("reason"), item)
            source = item["source"]
            exported_names = extract_exported_actions_from_file(FRONTEND_SRC / source)
            classified_actions = item.get("exported_actions")
            self.assertIsInstance(classified_actions, list, item)
            classified_names = {
                name for name in classified_actions if isinstance(name, str) and name
            }
            self.assertEqual(len(classified_names), len(classified_actions), item)
            excluded_exports = item.get("excluded_exports", {})
            self.assertIsInstance(excluded_exports, dict, item)
            self.assertTrue(
                all(
                    isinstance(name, str) and isinstance(reason, str) and reason
                    for name, reason in excluded_exports.items()
                ),
                item,
            )
            self.assertFalse(classified_names & set(excluded_exports), item)
            self.assertEqual(
                classified_names | set(excluded_exports), exported_names, source
            )

            overrides = item.get("action_overrides", {})
            self.assertIsInstance(overrides, dict, item)
            self.assertTrue(set(overrides).issubset(classified_names), item)
            for action_name in classified_names:
                override = overrides.get(action_name, {})
                self.assertIsInstance(override, dict, f"{source}:{action_name}")
                action_status = override.get("status", item.get("status"))
                self.assertIn(
                    action_status, allowed_statuses, f"{source}:{action_name}"
                )
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
                    self.assertIsNotNone(
                        capability, f"{source}:{action_name} -> {command}"
                    )
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
                        self.assertIn(
                            command,
                            ui_only,
                            f"{item['id']} missing ui_only declaration",
                        )
                elif item["status"] == "ui_only":
                    self.assertIn(
                        capability.availability,
                        {"ui_only", "planned", "unsupported_on_platform"},
                    )

    def test_export_scanner_covers_reexports_and_default_named_functions(self) -> None:
        source = """
        export { run, run as execute, type Payload };
        export default function fallback() { return null; }
        export async function load() { return null; }
        """
        self.assertEqual(
            extract_exported_actions(source),
            {"run", "execute", "fallback", "load"},
        )

    def test_export_scanner_follows_entity_star_reexports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "index.ts").write_text(
                "export * from './communityMentors';\n",
                encoding="utf-8",
            )
            (root / "communityMentors.ts").write_text(
                "export const listCommunityMentors = () => [];\n"
                "export type CommunityMentor = { id: string };\n",
                encoding="utf-8",
            )

            self.assertEqual(
                extract_exported_actions_from_file(root / "index.ts"),
                {"listCommunityMentors"},
            )


if __name__ == "__main__":
    unittest.main()
