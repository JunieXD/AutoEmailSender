from __future__ import annotations

import ast
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = BACKEND_ROOT / "app"

REVIEWED_LEGACY_VIOLATIONS = {
    "app/core/agent_mutation_headers.py -> app.services.agent_mutations",
}

FORBIDDEN_LAYER_IMPORTS = {
    "core": {"api", "services", "schemas", "agents", "modules"},
    "models": {"api", "services", "schemas", "agents"},
    "schemas": {"api", "services", "agents"},
    "services": {"api", "agents"},
}

LEGACY_LAYER_ROOTS = (
    APP_ROOT / "api",
    APP_ROOT / "schemas",
    APP_ROOT / "services",
    APP_ROOT / "agents",
)


def _module_name(path: Path) -> str:
    relative = path.relative_to(BACKEND_ROOT).with_suffix("")
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


def _internal_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names if alias.name.startswith("app"))
        elif isinstance(node, ast.ImportFrom):
            target = _resolve_import_from(path, node)
            if target.startswith("app"):
                imports.add(target)
    return imports


def _layer(module: str) -> str | None:
    parts = module.split(".")
    return parts[1] if len(parts) > 1 and parts[0] == "app" else None


def _legacy_layer_violations() -> set[str]:
    violations: set[str] = set()
    for source in sorted(APP_ROOT.rglob("*.py")):
        source_module = _module_name(source)
        source_layer = _layer(source_module)
        forbidden_targets = FORBIDDEN_LAYER_IMPORTS.get(source_layer or "", set())
        for target in _internal_imports(source):
            if _layer(target) in forbidden_targets:
                relative_source = source.relative_to(BACKEND_ROOT).as_posix()
                violations.add(f"{relative_source} -> {target}")
    return violations


def _module_boundary_violations() -> set[str]:
    violations: set[str] = set()
    modules_root = APP_ROOT / "modules"
    if not modules_root.exists():
        return violations

    for source in sorted(modules_root.rglob("*.py")):
        source_module = _module_name(source)
        source_parts = source_module.split(".")
        if len(source_parts) < 3:
            continue
        source_domain = source_parts[2]
        for target in _internal_imports(source):
            target_parts = target.split(".")
            reason: str | None = None
            if target.startswith("app.api"):
                reason = "domain modules must not depend on legacy HTTP adapters"
            elif len(target_parts) >= 3 and target_parts[:2] == ["app", "modules"]:
                target_domain = target_parts[2]
                target_public = f"app.modules.{target_domain}.public"
                if target_domain != source_domain and not target.startswith(target_public):
                    reason = "cross-domain imports must use the target public facade"
            if reason:
                relative_source = source.relative_to(BACKEND_ROOT).as_posix()
                violations.add(f"{relative_source} -> {target} ({reason})")
    return violations


def _legacy_module_reexport_shims() -> set[str]:
    shims: set[str] = set()
    for root in LEGACY_LAYER_ROOTS:
        for source in sorted(root.rglob("*.py")):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            imports_domain_module = False
            is_pure_reexport = True
            for node in tree.body:
                if (
                    isinstance(node, ast.Expr)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                ):
                    continue
                if isinstance(node, ast.ImportFrom) and node.module == "__future__":
                    continue
                if isinstance(node, ast.ImportFrom):
                    imports_domain_module |= (node.module or "").startswith("app.modules")
                    continue
                if isinstance(node, ast.Import):
                    imports_domain_module |= any(
                        alias.name.startswith("app.modules") for alias in node.names
                    )
                    continue
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    if all(
                        isinstance(target, ast.Name) and target.id == "__all__"
                        for target in targets
                    ):
                        continue
                is_pure_reexport = False
                break
            if imports_domain_module and is_pure_reexport:
                shims.add(source.relative_to(BACKEND_ROOT).as_posix())
    return shims


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_legacy_layer_violations_match_reviewed_baseline(self) -> None:
        actual = _legacy_layer_violations()
        self.assertEqual(
            actual,
            REVIEWED_LEGACY_VIOLATIONS,
            msg=(
                "Backend import-boundary baseline changed. New edges must be removed; "
                "removed edges must also be deleted from REVIEWED_LEGACY_VIOLATIONS."
            ),
        )

    def test_domain_modules_use_public_cross_domain_boundaries(self) -> None:
        self.assertEqual(_module_boundary_violations(), set())

    def test_legacy_layers_do_not_reintroduce_module_reexport_shims(self) -> None:
        self.assertEqual(_legacy_module_reexport_shims(), set())


if __name__ == "__main__":
    unittest.main()
