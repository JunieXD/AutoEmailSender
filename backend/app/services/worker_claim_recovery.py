from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.campaigns.public import recover_interrupted_batch_draft_claims
from app.modules.communications.public import recover_interrupted_imap_background_claims
from app.modules.crawler.public import recover_interrupted_crawler_v2_claims
from app.modules.matching.public import recover_interrupted_match_analysis_job_items


@dataclass(frozen=True, slots=True)
class WorkerClaimRecoverySummary:
    batch_drafts: int = 0
    match_analysis_items: int = 0
    crawler_v2_items: int = 0
    imap_claims: int = 0

    @property
    def total(self) -> int:
        return (
            self.batch_drafts
            + self.match_analysis_items
            + self.crawler_v2_items
            + self.imap_claims
        )

    def to_log_detail(self) -> str:
        return (
            f"total={self.total} batch_drafts={self.batch_drafts} "
            f"match_analysis_items={self.match_analysis_items} "
            f"crawler_v2_items={self.crawler_v2_items} "
            f"imap_claims={self.imap_claims}"
        )


async def recover_interrupted_worker_claims(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    preserve_full_imap_claims: bool,
) -> WorkerClaimRecoverySummary:
    """Recover only non-mail-delivery background claims from dead processes.

    For a Worker-only restart, the caller must already hold the exclusive Worker
    role lock and must pass ``preserve_full_imap_claims=True`` because the API can
    still own a manual IMAP synchronization.  API cold-start recovery runs before
    any request is accepted and can recover every IMAP claim.

    Delivery attempts are intentionally absent.  ``sending`` recovery keeps its
    separate at-most-once/assume-sent rules and must never be made dispatchable by
    this generation recovery path.
    """

    imap_claims = await recover_interrupted_imap_background_claims(
        session_factory,
        preserve_full_sync_claims=preserve_full_imap_claims,
    )
    batch_drafts = await recover_interrupted_batch_draft_claims(session_factory)
    match_analysis_items = await recover_interrupted_match_analysis_job_items(
        session_factory
    )
    crawler_v2_items = await recover_interrupted_crawler_v2_claims(session_factory)
    return WorkerClaimRecoverySummary(
        batch_drafts=batch_drafts,
        match_analysis_items=match_analysis_items,
        crawler_v2_items=crawler_v2_items,
        imap_claims=imap_claims,
    )


__all__ = [
    "WorkerClaimRecoverySummary",
    "recover_interrupted_worker_claims",
]
