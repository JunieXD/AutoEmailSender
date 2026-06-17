from __future__ import annotations

from pathlib import Path
import unittest


class BackendBuildScriptTest(unittest.TestCase):
    def test_declares_document_extraction_fallback_dependencies(self) -> None:
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        content = pyproject.read_text(encoding="utf-8")

        self.assertIn('"pypdf>=', content)

    def test_includes_async_sqlite_driver_for_packaged_runtime(self) -> None:
        script = Path(__file__).resolve().parents[1] / ".." / "scripts" / "build-backend.ps1"
        content = script.resolve().read_text(encoding="utf-8")

        self.assertIn("--hidden-import aiosqlite", content)

    def test_installs_only_playwright_browsers_to_packaged_resource_dir(self) -> None:
        script = Path(__file__).resolve().parents[1] / ".." / "scripts" / "build-backend.ps1"
        content = script.resolve().read_text(encoding="utf-8")
        legacy_browser_driver = "patch" + "right"

        self.assertIn("$env:PLAYWRIGHT_BROWSERS_PATH = $PlaywrightBrowsersDir", content)
        self.assertIn("uv run python -m playwright install --only-shell chromium", content)
        self.assertNotIn(f"uv run python -m {legacy_browser_driver} install", content)

    def test_collects_document_extraction_and_playwright_dependencies_for_packaging(self) -> None:
        script = Path(__file__).resolve().parents[1] / ".." / "scripts" / "build-backend.ps1"
        content = script.resolve().read_text(encoding="utf-8")

        for package_name in [
            "markitdown",
            "mammoth",
            "pdfminer",
            "pdfplumber",
            "pypdf",
            "playwright",
        ]:
            self.assertIn(f"--collect-all {package_name}", content)

        legacy_fetch_backend = "crawl" + "4ai"
        legacy_browser_driver = "patch" + "right"

        self.assertNotIn(f"--collect-all {legacy_fetch_backend}", content)
        self.assertNotIn(f"--collect-all {legacy_browser_driver}", content)
        self.assertNotIn(f"--exclude-module {legacy_browser_driver}", content)
        self.assertNotIn(
            "$Packaged" + legacy_browser_driver[:1].upper() + legacy_browser_driver[1:] + "Dir",
            content,
        )
        self.assertNotIn(f"_internal\\{legacy_browser_driver}", content)

    def test_backend_packaging_uses_noarchive_for_smaller_differential_updates(self) -> None:
        script = Path(__file__).resolve().parents[1] / ".." / "scripts" / "build-backend.ps1"
        content = script.resolve().read_text(encoding="utf-8")

        self.assertIn("--debug noarchive", content)

    def test_playwright_install_helper_installs_only_playwright(self) -> None:
        script = (
            Path(__file__).resolve().parents[1]
            / ".."
            / "scripts"
            / "install-backend-playwright.ps1"
        )
        content = script.resolve().read_text(encoding="utf-8")
        legacy_browser_driver = "patch" + "right"

        self.assertIn("$env:PLAYWRIGHT_BROWSERS_PATH = $PlaywrightBrowsersDir", content)
        self.assertIn("uv run python -m playwright install --only-shell chromium", content)
        self.assertNotIn(f"uv run python -m {legacy_browser_driver} install", content)
