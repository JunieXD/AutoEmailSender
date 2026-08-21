from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy import Integer, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.core.time import (
    as_utc_aware,
    as_utc_naive,
    parse_api_datetime,
    serialize_api_datetime,
    utc_now,
)
from app.models.types import UTCDateTime


class TimeTestBase(DeclarativeBase):
    pass


class TimeSample(TimeTestBase):
    __tablename__ = "time_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    happened_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class TimeUtilsTest(unittest.TestCase):
    def test_as_utc_aware_treats_naive_as_utc(self) -> None:
        value = datetime(2026, 5, 31, 6, 44, 37)

        self.assertEqual(
            as_utc_aware(value), datetime(2026, 5, 31, 6, 44, 37, tzinfo=UTC)
        )

    def test_as_utc_aware_converts_offset_to_utc(self) -> None:
        value = datetime(2026, 5, 31, 14, 44, 37, tzinfo=timezone(timedelta(hours=8)))

        self.assertEqual(
            as_utc_aware(value), datetime(2026, 5, 31, 6, 44, 37, tzinfo=UTC)
        )

    def test_as_utc_naive_drops_timezone_after_conversion(self) -> None:
        value = datetime(2026, 5, 31, 14, 44, 37, tzinfo=timezone(timedelta(hours=8)))

        self.assertEqual(as_utc_naive(value), datetime(2026, 5, 31, 6, 44, 37))

    def test_parse_api_datetime_rejects_date_only_values(self) -> None:
        with self.assertRaises(ValueError):
            parse_api_datetime("2026-05-31")

    def test_parse_api_datetime_accepts_z_and_offsets(self) -> None:
        self.assertEqual(
            parse_api_datetime("2026-05-31T06:44:37Z"),
            datetime(2026, 5, 31, 6, 44, 37, tzinfo=UTC),
        )
        self.assertEqual(
            parse_api_datetime("2026-05-31T14:44:37+08:00"),
            datetime(2026, 5, 31, 6, 44, 37, tzinfo=UTC),
        )

    def test_serialize_api_datetime_uses_z_suffix(self) -> None:
        value = datetime(
            2026, 5, 31, 14, 44, 37, 123456, tzinfo=timezone(timedelta(hours=8))
        )

        self.assertEqual(serialize_api_datetime(value), "2026-05-31T06:44:37Z")

    def test_utc_now_returns_aware_utc_datetime(self) -> None:
        now = utc_now()

        self.assertIs(now.tzinfo, UTC)

    def test_utc_datetime_reads_sqlite_values_as_aware_utc(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        TimeTestBase.metadata.create_all(engine)

        with Session(engine) as session:
            sample = TimeSample(
                happened_at=datetime(
                    2026, 5, 31, 14, 44, 37, tzinfo=timezone(timedelta(hours=8))
                )
            )
            session.add(sample)
            session.commit()
            sample_id = sample.id

        with Session(engine) as session:
            loaded = session.scalar(
                select(TimeSample).where(TimeSample.id == sample_id)
            )

        assert loaded is not None
        self.assertEqual(
            loaded.happened_at, datetime(2026, 5, 31, 6, 44, 37, tzinfo=UTC)
        )

    def test_utc_datetime_treats_naive_bind_value_as_utc(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        TimeTestBase.metadata.create_all(engine)

        with Session(engine) as session:
            sample = TimeSample(happened_at=datetime(2026, 5, 31, 6, 44, 37))
            session.add(sample)
            session.commit()
            sample_id = sample.id

        with Session(engine) as session:
            loaded = session.scalar(
                select(TimeSample).where(TimeSample.id == sample_id)
            )

        assert loaded is not None
        self.assertEqual(
            loaded.happened_at, datetime(2026, 5, 31, 6, 44, 37, tzinfo=UTC)
        )


if __name__ == "__main__":
    unittest.main()
