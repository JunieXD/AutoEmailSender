from __future__ import annotations

import unittest
from pathlib import Path


class CliBuildScriptTests(unittest.TestCase):
    def test_posix_build_creates_arm64_macos_one_file_cli_and_self_checks(self) -> None:
        script = _read_script("build-cli.sh")

        self.assertIn("uv run pyinstaller", script)
        self.assertIn("--onefile", script)
        self.assertIn("--target-arch arm64", script)
        self.assertIn("--copy-metadata auto-email-sender-cli", script)
        self.assertIn('"$CliExecutable" --format json version', script)
        self.assertIn('"$CliExecutable" --format json guide --topic overview', script)

    def test_windows_build_creates_one_file_cli_and_self_checks(self) -> None:
        script = _read_script("build-cli.ps1")

        self.assertIn("uv run pyinstaller", script)
        self.assertIn("--onefile", script)
        self.assertIn("auto-email-sender.exe", script)
        self.assertIn("& $CliExecutable --format json version", script)
        self.assertIn("& $CliExecutable --format json guide --topic overview", script)


def _read_script(name: str) -> str:
    return (
        Path(__file__).resolve().parents[2] / "scripts" / name
    ).read_text(encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
