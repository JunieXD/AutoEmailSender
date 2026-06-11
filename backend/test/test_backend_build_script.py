from __future__ import annotations

from pathlib import Path
import unittest


class BackendBuildScriptTest(unittest.TestCase):
    def test_installs_only_playwright_browsers_to_packaged_resource_dir(self) -> None:
        script = Path(__file__).resolve().parents[1] / ".." / "scripts" / "build-backend.ps1"
        content = script.resolve().read_text(encoding="utf-8")

        self.assertIn("$env:PLAYWRIGHT_BROWSERS_PATH = $PlaywrightBrowsersDir", content)
        self.assertIn("uv run python -m playwright install --only-shell chromium", content)
        self.assertNotIn("uv run python -m patchright install", content)

    def test_collects_document_extraction_dependencies_for_packaging(self) -> None:
        script = Path(__file__).resolve().parents[1] / ".." / "scripts" / "build-backend.ps1"
        content = script.resolve().read_text(encoding="utf-8")

        for package_name in [
            "markitdown",
            "mammoth",
            "pdfminer",
            "pdfplumber",
            "pypdf",
            "crawl4ai",
            "playwright",
        ]:
            self.assertIn(f"--collect-all {package_name}", content)

        self.assertNotIn("--collect-all patchright", content)
        self.assertIn("--exclude-module patchright", content)
        self.assertIn('$PackagedPatchrightDir = Join-Path $BackendDistDir "_internal\\patchright"', content)
        self.assertIn("Remove-Item -Recurse -Force $PackagedPatchrightDir", content)

    def test_playwright_install_helper_does_not_install_patchright(self) -> None:
        script = (
            Path(__file__).resolve().parents[1]
            / ".."
            / "scripts"
            / "install-backend-playwright.ps1"
        )
        content = script.resolve().read_text(encoding="utf-8")

        self.assertIn("$env:PLAYWRIGHT_BROWSERS_PATH = $PlaywrightBrowsersDir", content)
        self.assertIn("uv run python -m playwright install --only-shell chromium", content)
        self.assertNotIn("uv run python -m patchright install", content)
