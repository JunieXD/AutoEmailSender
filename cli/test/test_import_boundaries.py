from __future__ import annotations

import ast
import unittest
from pathlib import Path


CLI_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = CLI_ROOT / "src" / "auto_email_sender_cli"

ALLOWED_CATEGORY_IMPORTS = {
    "bootstrap": {
        "bootstrap",
        "commands",
        "transport",
        "protocol",
        "catalog",
        "invocation",
        "installation",
        "root",
    },
    "commands": {"commands", "transport", "protocol", "catalog", "invocation"},
    "transport": {"transport", "protocol"},
    "protocol": {"protocol"},
    "catalog": {"catalog", "protocol"},
    "invocation": {"invocation", "catalog", "protocol"},
    "installation": {"installation", "protocol"},
    "root": {"root", "protocol"},
}


def _module_name(path: Path) -> str:
    relative = path.relative_to(CLI_ROOT / "src").with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _category(module: str) -> str:
    parts = module.split(".")
    name = parts[-1]
    if len(parts) > 1 and parts[1] == "commands":
        return "commands"
    if name in {"main", "__main__"}:
        return "bootstrap"
    if name in {"client", "runtime"}:
        return "transport"
    if name in {"errors", "output", "result_protocol", "version"}:
        return "protocol"
    if name in {
        "action_links",
        "capabilities",
        "contracts",
        "describe",
        "guide",
        "operation_specs",
    }:
        return "catalog"
    if name == "invoke":
        return "invocation"
    if name == "agent_installation":
        return "installation"
    return "root"


def _resolve_import_from(source: Path, node: ast.ImportFrom) -> str:
    target = node.module or ""
    if node.level == 0:
        return target

    source_module = _module_name(source)
    package_parts = source_module.split(".")
    if source.name != "__init__.py":
        package_parts.pop()
    if node.level > 1:
        package_parts = package_parts[: -(node.level - 1)]
    if target:
        package_parts.extend(target.split("."))
    return ".".join(package_parts)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add(_resolve_import_from(path, node))
    return imports


def _boundary_violations() -> set[str]:
    violations: set[str] = set()
    for source in sorted(PACKAGE_ROOT.rglob("*.py")):
        source_module = _module_name(source)
        source_category = _category(source_module)
        allowed_targets = ALLOWED_CATEGORY_IMPORTS[source_category]
        relative_source = source.relative_to(CLI_ROOT).as_posix()
        for target in _imports(source):
            if target.startswith("auto_email_sender_cli"):
                target_category = _category(target)
                if target_category not in allowed_targets:
                    violations.add(
                        f"{relative_source} -> {target} "
                        f"({source_category} must not depend on {target_category})"
                    )
            if target == "app" or target.startswith(
                ("app.", "backend", "sqlalchemy", "sqlite3")
            ):
                violations.add(
                    f"{relative_source} -> {target} "
                    "(CLI must use the versioned Agent API instead of backend/database internals)"
                )
    return violations


class CliImportBoundaryTests(unittest.TestCase):
    def test_cli_dependency_direction_has_no_exceptions(self) -> None:
        self.assertEqual(_boundary_violations(), set())


if __name__ == "__main__":
    unittest.main()
