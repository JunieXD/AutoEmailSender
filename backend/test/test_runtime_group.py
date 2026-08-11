from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock

from app.core.runtime_group import (
    RUNTIME_STATUS_REPLACE_RETRY_DELAYS_SECONDS,
    _replace_runtime_status_with_retry,
    _windows_extended_length_path,
)


class RuntimeStatusReplaceTests(unittest.TestCase):
    def test_windows_drive_path_uses_extended_length_prefix(self) -> None:
        self.assertEqual(
            _windows_extended_length_path(r"C:\qa\用户 数据 Ω\runtime\api.json"),
            r"\\?\C:\qa\用户 数据 Ω\runtime\api.json",
        )

    def test_windows_unc_path_uses_extended_unc_prefix(self) -> None:
        self.assertEqual(
            _windows_extended_length_path(r"\\server\share\runtime\worker.json"),
            r"\\?\UNC\server\share\runtime\worker.json",
        )

    def test_existing_extended_length_path_is_unchanged(self) -> None:
        extended_path = r"\\?\C:\qa\runtime\api.json"
        self.assertEqual(_windows_extended_length_path(extended_path), extended_path)

    def test_transient_reader_lock_is_retried_without_losing_atomic_replace(self) -> None:
        temporary_path = Mock(spec=Path)
        temporary_path.replace.side_effect = [
            PermissionError("sharing violation"),
            PermissionError("sharing violation"),
            None,
        ]
        sleeps: list[float] = []
        status_path = Path("runtime/worker.json")

        _replace_runtime_status_with_retry(
            temporary_path,
            status_path,
            sleep=sleeps.append,
        )

        self.assertEqual(temporary_path.replace.call_count, 3)
        temporary_path.replace.assert_called_with(status_path)
        self.assertEqual(
            sleeps,
            list(RUNTIME_STATUS_REPLACE_RETRY_DELAYS_SECONDS[:2]),
        )

    def test_persistent_permission_error_still_fails_closed(self) -> None:
        temporary_path = Mock(spec=Path)
        temporary_path.replace.side_effect = PermissionError("access denied")
        sleeps: list[float] = []

        with self.assertRaises(PermissionError):
            _replace_runtime_status_with_retry(
                temporary_path,
                Path("runtime/api.json"),
                sleep=sleeps.append,
            )

        self.assertEqual(
            temporary_path.replace.call_count,
            len(RUNTIME_STATUS_REPLACE_RETRY_DELAYS_SECONDS) + 1,
        )
        self.assertEqual(
            sleeps,
            list(RUNTIME_STATUS_REPLACE_RETRY_DELAYS_SECONDS),
        )


if __name__ == "__main__":
    unittest.main()
