from __future__ import annotations

import unittest

from scripts.quality.run_all_tests import (
    _concise_failure_output,
    _format_duration,
    _progress_interval,
)


class RunAllTestsHelpersTests(unittest.TestCase):
    def test_formats_short_minute_and_hour_durations(self) -> None:
        self.assertEqual(_format_duration(0.9), "0s")
        self.assertEqual(_format_duration(65.9), "1m05s")
        self.assertEqual(_format_duration(3661), "1h01m01s")

    def test_progress_interval_targets_about_ten_updates(self) -> None:
        self.assertEqual(_progress_interval(0), 1)
        self.assertEqual(_progress_interval(9), 1)
        self.assertEqual(_progress_interval(100), 10)

    def test_failure_output_drops_success_noise_before_vitest_failures(self) -> None:
        lines = [
            "dots and successful warnings\n",
            "--- Failed Tests 1 ---\n",
            "FAIL example.test.ts > reports the failure\n",
        ]

        self.assertEqual(
            _concise_failure_output(lines),
            "--- Failed Tests 1 ---\nFAIL example.test.ts > reports the failure",
        )

    def test_failure_output_keeps_all_text_for_non_vitest_failures(self) -> None:
        self.assertEqual(
            _concise_failure_output(["process crashed\n"]), "process crashed"
        )

    def test_failure_output_starts_at_unittest_error_details(self) -> None:
        lines = [
            "successful setup noise\n",
            "======================================================================\n",
            "ERROR: test_example\n",
        ]

        self.assertEqual(
            _concise_failure_output(lines),
            "======================================================================\nERROR: test_example",
        )


if __name__ == "__main__":
    unittest.main()
