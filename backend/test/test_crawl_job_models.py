from __future__ import annotations

import unittest

from app.models.crawl_chunk import CrawlPageChunkStatus
from app.models.crawl_job import (
    CrawlCandidateReviewStatus,
    CrawlJobEntryType,
    CrawlJobStatus,
    CrawlPageStatus,
)


class CrawlJobModelTests(unittest.TestCase):
    def test_entry_type_constants_are_stable(self) -> None:
        self.assertEqual(CrawlJobEntryType.LIST.value, "list")
        self.assertEqual(CrawlJobEntryType.PROFILE.value, "profile")

    def test_status_constants_are_stable(self) -> None:
        self.assertEqual(CrawlJobStatus.QUEUED.value, "queued")
        self.assertEqual(CrawlJobStatus.RUNNING.value, "running")
        self.assertEqual(CrawlJobStatus.NEEDS_REVIEW.value, "needs_review")
        self.assertEqual(CrawlJobStatus.PARTIALLY_COMPLETED.value, "partially_completed")
        self.assertEqual(CrawlJobStatus.COMPLETED.value, "completed")
        self.assertEqual(CrawlJobStatus.FAILED.value, "failed")
        self.assertEqual(CrawlJobStatus.CANCELED.value, "canceled")

    def test_candidate_review_status_constants_are_stable(self) -> None:
        self.assertEqual(CrawlCandidateReviewStatus.PENDING.value, "pending")
        self.assertEqual(CrawlCandidateReviewStatus.ACCEPTED.value, "accepted")
        self.assertEqual(CrawlCandidateReviewStatus.REJECTED.value, "rejected")
        self.assertEqual(CrawlCandidateReviewStatus.MERGED.value, "merged")

    def test_page_status_constants_are_stable(self) -> None:
        self.assertEqual(CrawlPageStatus.SUCCEEDED.value, "succeeded")
        self.assertEqual(CrawlPageStatus.FAILED.value, "failed")

    def test_chunk_status_constants_are_stable(self) -> None:
        self.assertEqual(CrawlPageChunkStatus.PENDING.value, "pending")
        self.assertEqual(CrawlPageChunkStatus.PROCESSING.value, "processing")
        self.assertEqual(CrawlPageChunkStatus.COMPLETED.value, "completed")
        self.assertEqual(CrawlPageChunkStatus.NO_CANDIDATES.value, "no_candidates")
        self.assertEqual(CrawlPageChunkStatus.SPLIT_REQUIRED.value, "split_required")
        self.assertEqual(CrawlPageChunkStatus.SUPERSEDED.value, "superseded")
        self.assertEqual(CrawlPageChunkStatus.FAILED.value, "failed")


if __name__ == "__main__":
    unittest.main()
