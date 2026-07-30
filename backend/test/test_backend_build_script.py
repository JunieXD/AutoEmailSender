from __future__ import annotations

from pathlib import Path
import runpy
import unittest


class BackendBuildScriptTest(unittest.TestCase):
    def test_declares_document_extraction_fallback_dependencies(self) -> None:
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        content = pyproject.read_text(encoding="utf-8")

        for package_name in [
            "defusedxml",
            "lxml",
            "mammoth",
            "markdownify",
            "pdfminer-six",
            "pdfplumber",
            "pypdf",
        ]:
            self.assertIn(f'"{package_name}>=', content)
        self.assertNotIn('"markitdown', content)

    def test_includes_async_sqlite_driver_for_packaged_runtime(self) -> None:
        script = Path(__file__).resolve().parents[1] / ".." / "scripts" / "build-backend.ps1"
        content = script.resolve().read_text(encoding="utf-8")

        self.assertIn("--hidden-import aiosqlite", content)

    def test_installs_only_playwright_browsers_to_packaged_resource_dir(self) -> None:
        script = Path(__file__).resolve().parents[1] / ".." / "scripts" / "build-backend.ps1"
        content = script.resolve().read_text(encoding="utf-8")

        self.assertIn("$env:PLAYWRIGHT_BROWSERS_PATH = $PlaywrightBrowsersDir", content)
        self.assertIn("uv run python -m playwright install --only-shell chromium", content)

    def test_collects_document_extraction_dependencies_for_packaging(self) -> None:
        script = Path(__file__).resolve().parents[1] / ".." / "scripts" / "build-backend.ps1"
        content = script.resolve().read_text(encoding="utf-8")

        for package_name in [
            "mammoth",
            "pdfminer",
            "pypdf",
            "tldextract",
        ]:
            self.assertIn(f"--collect-all {package_name}", content)
        self.assertIn("--hidden-import app.services.document_extraction", content)
        self.assertIn("--hidden-import lxml.etree", content)
        self.assertNotIn("--collect-all markitdown", content)
        self.assertNotIn("--collect-all pdfplumber", content)

    def test_excludes_unused_heavy_document_dependencies_from_packaging(self) -> None:
        script = Path(__file__).resolve().parents[1] / ".." / "scripts" / "build-backend.ps1"
        content = script.resolve().read_text(encoding="utf-8")

        for package_name in [
            "markitdown",
            "magika",
            "onnxruntime",
            "numpy",
            "PIL",
            "pypdfium2",
        ]:
            self.assertIn(f"--exclude-module {package_name}", content)
        self.assertIn("MARKITDOWN_NOTICE.txt", content)
        self.assertIn('--add-data "$DocumentExtractionNotice;licenses"', content)

        notice = (
            Path(__file__).resolve().parents[1]
            / "app"
            / "services"
            / "document_extraction"
            / "MARKITDOWN_NOTICE.txt"
        ).read_text(encoding="utf-8")
        self.assertIn("Microsoft MarkItDown 0.1.5", notice)
        self.assertIn("MIT License", notice)

    def test_backend_build_scripts_use_precise_playwright_hooks(self) -> None:
        scripts_dir = Path(__file__).resolve().parents[2] / "scripts"

        for script_name in ["build-backend.ps1", "build-backend.sh"]:
            with self.subTest(script_name=script_name):
                content = (scripts_dir / script_name).read_text(encoding="utf-8")
                self.assertIn("pyinstaller-hooks", content)
                self.assertIn("--additional-hooks-dir", content)
                self.assertNotIn("--collect-all playwright", content)

    def test_precise_playwright_hooks_keep_driver_package_without_bundled_node(self) -> None:
        import playwright

        hooks_dir = Path(__file__).resolve().parents[2] / "scripts" / "pyinstaller-hooks"
        hook_namespace = runpy.run_path(str(hooks_dir / "hook-playwright.py"))
        package_dir = Path(playwright.__file__).resolve().parent
        collected_sources = {Path(source).resolve() for source, _destination in hook_namespace["datas"]}
        expected_driver_files = {
            source.resolve()
            for source in (package_dir / "driver" / "package").rglob("*")
            if source.is_file()
        }

        self.assertTrue(expected_driver_files)
        self.assertTrue(expected_driver_files.issubset(collected_sources))
        self.assertIn("playwright.async_api", hook_namespace["hiddenimports"])
        self.assertIn("playwright.sync_api", hook_namespace["hiddenimports"])
        self.assertFalse(
            any(
                source.is_relative_to(package_dir) and source.suffix == ".py"
                for source in collected_sources
            )
        )

        for node_name in ["node", "node.exe"]:
            bundled_node = (package_dir / "driver" / node_name).resolve()
            self.assertNotIn(bundled_node, collected_sources)

        hook_source = (hooks_dir / "hook-playwright.py").read_text(encoding="utf-8")
        self.assertIn('"driver/node"', hook_source)
        self.assertIn('"driver/node.exe"', hook_source)

        for api_name in ["async_api", "sync_api"]:
            with self.subTest(api_name=api_name):
                api_hook = runpy.run_path(str(hooks_dir / f"hook-playwright.{api_name}.py"))
                self.assertEqual(api_hook["datas"], [])
                self.assertEqual(api_hook["binaries"], [])
                self.assertEqual(api_hook["hiddenimports"], [])

    def test_collects_llm_tokenizer_namespace_dependencies_for_packaging(self) -> None:
        script = Path(__file__).resolve().parents[1] / ".." / "scripts" / "build-backend.ps1"
        content = script.resolve().read_text(encoding="utf-8")

        self.assertIn("--collect-all tiktoken", content)
        self.assertIn("--collect-submodules tiktoken_ext", content)
        self.assertIn("--hidden-import tiktoken_ext.openai_public", content)

    def test_runs_packaged_backend_self_check_after_build(self) -> None:
        script = Path(__file__).resolve().parents[1] / ".." / "scripts" / "build-backend.ps1"
        content = script.resolve().read_text(encoding="utf-8")

        self.assertIn('Join-Path $BackendDir "dist\\backend\\backend.exe"', content)
        self.assertIn("--self-check", content)
        self.assertIn(
            '& $PackagedBackendExe --document-self-check '
            '(Join-Path $BackendDir "test\\fixtures\\document_extraction")',
            content,
        )

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

        self.assertIn("$env:PLAYWRIGHT_BROWSERS_PATH = $PlaywrightBrowsersDir", content)
        self.assertIn("uv run python -m playwright install --only-shell chromium", content)

    def test_macos_backend_build_script_matches_packaged_runtime_dependencies(self) -> None:
        script = Path(__file__).resolve().parents[1] / ".." / "scripts" / "build-backend.sh"
        content = script.resolve().read_text(encoding="utf-8")

        self.assertIn("set -euo pipefail", content)
        self.assertIn('PLAYWRIGHT_BROWSERS_PATH="$PlaywrightBrowsersDir"', content)
        self.assertIn("uv run python -m playwright install --only-shell chromium", content)
        self.assertIn("uv run pyinstaller", content)
        self.assertIn("--debug noarchive", content)
        self.assertIn("--hidden-import main", content)
        self.assertIn("--hidden-import aiosqlite", content)
        self.assertIn("--hidden-import app.services.document_extraction", content)
        self.assertIn("--hidden-import lxml.etree", content)
        self.assertIn("--collect-all mammoth", content)
        self.assertIn("--collect-all pdfminer", content)
        self.assertIn("--collect-all pypdf", content)
        self.assertIn("--additional-hooks-dir", content)
        self.assertNotIn("--collect-all playwright", content)
        self.assertIn("--collect-all tldextract", content)
        self.assertIn("--collect-all tiktoken", content)
        self.assertIn("--collect-submodules tiktoken_ext", content)
        self.assertIn("--hidden-import tiktoken_ext.openai_public", content)
        self.assertNotIn("--collect-all markitdown", content)
        self.assertNotIn("--collect-all pdfplumber", content)
        for package_name in [
            "markitdown",
            "magika",
            "onnxruntime",
            "numpy",
            "PIL",
            "pypdfium2",
        ]:
            self.assertIn(f"--exclude-module {package_name}", content)
        self.assertIn('--add-data "$AlembicIni:."', content)
        self.assertIn('--add-data "$AlembicDir:alembic"', content)
        self.assertIn('--add-data "$DocumentExtractionNotice:licenses"', content)
        self.assertIn('"$PackagedBackendExe" --self-check', content)
        self.assertIn(
            '"$PackagedBackendExe" --document-self-check '
            '"$BackendDir/test/fixtures/document_extraction"',
            content,
        )
