import unittest
from datetime import UTC, datetime

from app.services.batch_schedule import (
    build_jittered_batch_schedule,
    has_future_batch_window,
    is_batch_window_expired,
    is_datetime_in_batch_window,
    normalize_scheduled_dates,
)


class BatchScheduleTest(unittest.TestCase):
    def test_build_jittered_batch_schedule_spreads_actual_count_across_window(self) -> None:
        result = build_jittered_batch_schedule(
            task_count=6,
            scheduled_dates=["2026-05-04"],
            window_start_time="09:00",
            window_end_time="18:00",
            emails_per_window=20,
            now=datetime(2026, 5, 3, 12, 0, tzinfo=UTC),
            jitter_ratio=0,
        )

        self.assertEqual(len(result), 6)
        self.assertEqual(result[0], datetime(2026, 5, 4, 9, 45, tzinfo=UTC))
        self.assertEqual(result[-1], datetime(2026, 5, 4, 17, 15, tzinfo=UTC))
        self.assertEqual(result, sorted(result))

    def test_normalize_scheduled_dates_sorts_and_deduplicates_dates(self) -> None:
        result = normalize_scheduled_dates(
            ["2026-05-04", "2026-04-28", "2026-05-04"],
        )

        self.assertEqual(result, ["2026-04-28", "2026-05-04"])

    def test_normalize_scheduled_dates_rejects_invalid_date(self) -> None:
        with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
            normalize_scheduled_dates(["2026-02-30"])

    def test_normalize_scheduled_dates_rejects_non_yyyy_mm_dd_format(self) -> None:
        for value in ["20260504", "2026-W19-1"]:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
                    normalize_scheduled_dates([value])

    def test_is_datetime_in_batch_window_requires_selected_date_and_time_window(self) -> None:
        now = datetime(2026, 5, 4, 10, 30, tzinfo=UTC)

        self.assertTrue(
            is_datetime_in_batch_window(
                now,
                scheduled_dates=["2026-05-04"],
                window_start_time="09:00",
                window_end_time="18:00",
            ),
        )
        self.assertFalse(
            is_datetime_in_batch_window(
                now,
                scheduled_dates=["2026-05-05"],
                window_start_time="09:00",
                window_end_time="18:00",
            ),
        )
        self.assertFalse(
            is_datetime_in_batch_window(
                now,
                scheduled_dates=["2026-05-04"],
                window_start_time="11:00",
                window_end_time="18:00",
            ),
        )

    def test_has_future_batch_window_includes_active_and_future_windows(self) -> None:
        self.assertTrue(
            has_future_batch_window(
                datetime(2026, 5, 4, 10, 30, tzinfo=UTC),
                scheduled_dates=["2026-05-04"],
                window_end_time="18:00",
            ),
        )
        self.assertTrue(
            has_future_batch_window(
                datetime(2026, 5, 4, 20, 0, tzinfo=UTC),
                scheduled_dates=["2026-05-05"],
                window_end_time="09:00",
            ),
        )
        self.assertFalse(
            has_future_batch_window(
                datetime(2026, 5, 4, 18, 0, tzinfo=UTC),
                scheduled_dates=["2026-05-04"],
                window_end_time="18:00",
            ),
        )

    def test_is_batch_window_expired_only_after_last_window_end(self) -> None:
        self.assertFalse(
            is_batch_window_expired(
                datetime(2026, 5, 4, 17, 59, tzinfo=UTC),
                scheduled_dates=["2026-05-04"],
                window_end_time="18:00",
            ),
        )
        self.assertFalse(
            is_batch_window_expired(
                datetime(2026, 5, 4, 20, 0, tzinfo=UTC),
                scheduled_dates=["2026-05-04", "2026-05-05"],
                window_end_time="09:00",
            ),
        )
        self.assertTrue(
            is_batch_window_expired(
                datetime(2026, 5, 5, 9, 0, tzinfo=UTC),
                scheduled_dates=["2026-05-04", "2026-05-05"],
                window_end_time="09:00",
            ),
        )
