import random
import unittest
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

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

    def test_build_jittered_batch_schedule_fills_dates_in_order(self) -> None:
        result = build_jittered_batch_schedule(
            task_count=30,
            scheduled_dates=["2026-05-04", "2026-05-05", "2026-05-06"],
            window_start_time="09:00",
            window_end_time="18:00",
            emails_per_window=20,
            now=datetime(2026, 5, 3, 12, 0, tzinfo=UTC),
            jitter_ratio=0,
        )

        dates = [item.date().isoformat() for item in result]
        self.assertEqual(dates.count("2026-05-04"), 20)
        self.assertEqual(dates.count("2026-05-05"), 10)
        self.assertEqual(dates.count("2026-05-06"), 0)

    def test_build_jittered_batch_schedule_uses_remaining_window_for_today(self) -> None:
        result = build_jittered_batch_schedule(
            task_count=4,
            scheduled_dates=["2026-05-04"],
            window_start_time="09:00",
            window_end_time="18:00",
            emails_per_window=20,
            now=datetime(2026, 5, 4, 14, 0, tzinfo=UTC),
            jitter_ratio=0,
        )

        self.assertEqual(result[0], datetime(2026, 5, 4, 14, 30, tzinfo=UTC))
        self.assertEqual(result[-1], datetime(2026, 5, 4, 17, 30, tzinfo=UTC))
        self.assertTrue(all(item >= datetime(2026, 5, 4, 14, 0, tzinfo=UTC) for item in result))

    def test_build_jittered_batch_schedule_stores_local_window_times_as_utc(self) -> None:
        result = build_jittered_batch_schedule(
            task_count=1,
            scheduled_dates=["2026-05-04"],
            window_start_time="09:00",
            window_end_time="11:00",
            emails_per_window=1,
            now=datetime(2026, 5, 3, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            jitter_ratio=0,
        )

        self.assertEqual(result, [datetime(2026, 5, 4, 2, 0, tzinfo=UTC)])
        self.assertIs(result[0].tzinfo, UTC)

    def test_build_jittered_batch_schedule_skips_expired_today_window(self) -> None:
        result = build_jittered_batch_schedule(
            task_count=2,
            scheduled_dates=["2026-05-04", "2026-05-05"],
            window_start_time="09:00",
            window_end_time="18:00",
            emails_per_window=20,
            now=datetime(2026, 5, 4, 18, 30, tzinfo=UTC),
            jitter_ratio=0,
        )

        self.assertEqual({item.date().isoformat() for item in result}, {"2026-05-05"})

    def test_build_jittered_batch_schedule_keeps_jitter_inside_window(self) -> None:
        result = build_jittered_batch_schedule(
            task_count=12,
            scheduled_dates=["2026-05-04"],
            window_start_time="09:00",
            window_end_time="10:00",
            emails_per_window=12,
            now=datetime(2026, 5, 3, 12, 0, tzinfo=UTC),
            random_source=random.Random(42),
        )

        window_start = datetime(2026, 5, 4, 9, 0, tzinfo=UTC)
        window_end = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
        self.assertEqual(len(result), 12)
        self.assertEqual(result, sorted(result))
        self.assertTrue(all(window_start <= item <= window_end for item in result))

    def test_build_jittered_batch_schedule_rejects_insufficient_capacity(self) -> None:
        with self.assertRaisesRegex(ValueError, "不足以覆盖全部任务"):
            build_jittered_batch_schedule(
                task_count=5,
                scheduled_dates=["2026-05-04"],
                window_start_time="09:00",
                window_end_time="18:00",
                emails_per_window=4,
                now=datetime(2026, 5, 3, 12, 0, tzinfo=UTC),
                jitter_ratio=0,
            )

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
