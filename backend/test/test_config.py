from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["AUTO_EMAIL_SENDER_DATA_DIR"] = self.temp_dir.name
        os.environ.pop("CRAWLER_DEBUG", None)
        os.environ.pop("SQLITE_BUSY_TIMEOUT_MS", None)
        os.environ.pop("SQLITE_ENABLE_WAL", None)
        os.environ.pop("SQLITE_SYNCHRONOUS", None)

        from app.core.config import get_settings

        get_settings.cache_clear()

    def tearDown(self) -> None:
        from app.core.config import get_settings

        get_settings.cache_clear()
        os.environ.pop("AUTO_EMAIL_SENDER_DATA_DIR", None)
        os.environ.pop("CRAWLER_DEBUG", None)
        os.environ.pop("SQLITE_BUSY_TIMEOUT_MS", None)
        os.environ.pop("SQLITE_ENABLE_WAL", None)
        os.environ.pop("SQLITE_SYNCHRONOUS", None)
        self.temp_dir.cleanup()

    def test_crawler_debug_defaults_to_enabled(self) -> None:
        from app.core.config import get_settings

        settings = get_settings()

        self.assertTrue(settings.crawler_debug_enabled)
        self.assertEqual(
            settings.crawler_debug_dir,
            (Path(self.temp_dir.name) / "logs" / "crawler").resolve(),
        )

    def test_sqlite_lock_defaults_are_enabled(self) -> None:
        from app.core.config import get_settings

        settings = get_settings()

        self.assertEqual(settings.sqlite_busy_timeout_ms, 5000)
        self.assertTrue(settings.sqlite_wal_enabled)
        self.assertTrue(settings.sqlite_foreign_keys_enabled)
        self.assertEqual(settings.sqlite_synchronous, "NORMAL")
        self.assertEqual(settings.sqlite_cache_size_mib, 64)
        self.assertEqual(settings.sqlite_mmap_size_mib, 256)
        self.assertEqual(settings.sqlite_slow_query_ms, 250)

    def test_sqlite_lock_settings_can_be_overridden_by_env(self) -> None:
        from app.core.config import get_settings

        os.environ["SQLITE_BUSY_TIMEOUT_MS"] = "12000"
        os.environ["SQLITE_ENABLE_WAL"] = "0"
        get_settings.cache_clear()

        settings = get_settings()

        self.assertEqual(settings.sqlite_busy_timeout_ms, 12000)
        self.assertFalse(settings.sqlite_wal_enabled)

    def test_sqlite_busy_timeout_negative_env_clamps_to_zero(self) -> None:
        from app.core.config import get_settings

        os.environ["SQLITE_BUSY_TIMEOUT_MS"] = "-1"
        get_settings.cache_clear()

        settings = get_settings()

        self.assertEqual(settings.sqlite_busy_timeout_ms, 0)

    def test_sqlite_synchronous_rejects_unknown_value(self) -> None:
        from app.core.config import get_settings

        os.environ["SQLITE_SYNCHRONOUS"] = "unsafe-value"
        get_settings.cache_clear()

        self.assertEqual(get_settings().sqlite_synchronous, "NORMAL")


if __name__ == "__main__":
    unittest.main()
