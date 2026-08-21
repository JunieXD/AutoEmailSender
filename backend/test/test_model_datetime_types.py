from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import (
    Base,
    CrawlJob,
    CrawlJobRun,
    CrawlJobStatus,
    CrawlPageTask,
    EmailTask,
    EmailTaskSource,
    EmailTaskStatus,
)


class ModelDateTimeTypesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def test_crawl_page_task_lease_reads_as_utc_aware(self) -> None:
        with Session(self.engine) as session:
            job = CrawlJob(
                university="U",
                school="S",
                start_url="https://example.edu",
                status=CrawlJobStatus.RUNNING.value,
            )
            session.add(job)
            session.flush()
            task = CrawlPageTask(
                job_id=job.id,
                normalized_url="https://example.edu/a",
                original_url="https://example.edu/a",
                lease_expires_at=datetime(2026, 5, 31, 6, 44, 37),
            )
            session.add(task)
            session.commit()
            task_id = task.id

        with Session(self.engine) as session:
            loaded = session.scalar(
                select(CrawlPageTask).where(CrawlPageTask.id == task_id)
            )

        assert loaded is not None
        self.assertEqual(
            loaded.lease_expires_at, datetime(2026, 5, 31, 6, 44, 37, tzinfo=UTC)
        )

    def test_crawl_job_run_started_at_reads_as_utc_aware(self) -> None:
        with Session(self.engine) as session:
            job = CrawlJob(
                university="U",
                school="S",
                start_url="https://example.edu",
                status=CrawlJobStatus.RUNNING.value,
            )
            session.add(job)
            session.flush()
            run = CrawlJobRun(
                job_id=job.id,
                attempt_number=1,
                status=CrawlJobStatus.RUNNING.value,
                started_at=datetime(
                    2026, 5, 31, 14, 44, 37, tzinfo=timezone(timedelta(hours=8))
                ),
            )
            session.add(run)
            session.commit()
            run_id = run.id

        with Session(self.engine) as session:
            loaded = session.scalar(select(CrawlJobRun).where(CrawlJobRun.id == run_id))

        assert loaded is not None
        self.assertEqual(
            loaded.started_at, datetime(2026, 5, 31, 6, 44, 37, tzinfo=UTC)
        )

    def test_email_task_scheduled_at_reads_as_utc_aware(self) -> None:
        with Session(self.engine) as session:
            task = EmailTask(
                professor_id=1,
                identity_id=1,
                llm_profile_id=1,
                source=EmailTaskSource.MANUAL.value,
                status=EmailTaskStatus.SCHEDULED.value,
                scheduled_at=datetime(2026, 5, 31, 6, 44, 37),
            )
            session.add(task)
            session.commit()
            task_id = task.id

        with Session(self.engine) as session:
            loaded = session.scalar(select(EmailTask).where(EmailTask.id == task_id))

        assert loaded is not None
        self.assertEqual(
            loaded.scheduled_at, datetime(2026, 5, 31, 6, 44, 37, tzinfo=UTC)
        )


if __name__ == "__main__":
    unittest.main()
