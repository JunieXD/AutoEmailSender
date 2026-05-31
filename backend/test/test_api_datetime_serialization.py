from __future__ import annotations

import unittest
from datetime import UTC, datetime

from app.schemas.base import ApiSchema


class ExampleSchema(ApiSchema):
    created_at: datetime


class ApiDateTimeSerializationTest(unittest.TestCase):
    def test_datetime_serializes_with_z_suffix(self) -> None:
        schema = ExampleSchema(created_at=datetime(2026, 5, 31, 6, 44, 37, tzinfo=UTC))

        self.assertEqual(schema.model_dump(mode="json"), {"created_at": "2026-05-31T06:44:37Z"})

    def test_naive_datetime_serializes_as_utc(self) -> None:
        schema = ExampleSchema(created_at=datetime(2026, 5, 31, 6, 44, 37))

        self.assertEqual(schema.model_dump(mode="json"), {"created_at": "2026-05-31T06:44:37Z"})


if __name__ == "__main__":
    unittest.main()