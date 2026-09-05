from __future__ import annotations

import ast
import unittest
from pathlib import Path


CLI_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = CLI_ROOT / "src" / "auto_email_sender_cli"

def _module_name(path: Path) -> str:
    relative = path.relative_to(CLI_ROOT / "src").with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


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
        relative_source = source.relative_to(CLI_ROOT).as_posix()
        for target in _imports(source):
            if target == "app" or target.startswith(
                ("app.", "backend", "sqlalchemy", "sqlite3")
            ):
                violations.add(
                    f"{relative_source} -> {target} "
                    "(CLI must use the versioned Agent API instead of backend/database internals)"
                )
    return violations


class CliImportBoundaryTests(unittest.TestCase):
    def test_cli_uses_api_instead_of_backend_or_database(self) -> None:
        self.assertEqual(_boundary_violations(), set())


if __name__ == "__main__":
    unittest.main()
