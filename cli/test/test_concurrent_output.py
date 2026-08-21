from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


class ConcurrentOutputTests(unittest.TestCase):
    def test_parallel_version_commands_emit_one_complete_json_document(self) -> None:
        cli_root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = os.pathsep.join(
            value for value in (str(cli_root / "src"), existing_pythonpath) if value
        )

        def run_command(_: int) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "auto_email_sender_cli",
                    "--format",
                    "json",
                    "version",
                ],
                cwd=cli_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )

        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(run_command, range(20)))

        decoder = json.JSONDecoder()
        for index, result in enumerate(results):
            with self.subTest(process=index):
                self.assertEqual(result.returncode, 0, msg=result.stderr)
                self.assertTrue(result.stdout.strip())
                payload, end = decoder.raw_decode(result.stdout)
                self.assertFalse(result.stdout[end:].strip())
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["_meta"]["command"], "version")
                self.assertEqual(result.stderr, "")
