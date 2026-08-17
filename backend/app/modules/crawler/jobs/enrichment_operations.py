from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from app.models import CrawlJob


def start_candidate_enrichment_operation(
    job: CrawlJob,
    *,
    skipped_count: int = 0,
) -> str:
    operation_id = str(uuid4())
    job.active_candidate_enrichment_operation_id = operation_id
    job.active_candidate_enrichment_skipped_count = max(0, skipped_count)
    return operation_id


def append_candidate_enrichment_terminal_event(
    job: CrawlJob,
    *,
    now: datetime,
    status: str,
    enriched_count: int,
    unchanged_count: int,
    failed_count: int,
    message: str,
) -> None:
    operation_id = job.active_candidate_enrichment_operation_id
    skipped_count = max(0, job.active_candidate_enrichment_skipped_count or 0)
    candidate_count = enriched_count + unchanged_count + failed_count
    trace = list(job.agent_trace or [])
    trace.append(
        {
            "event_type": "enrichment",
            "message": message,
            "created_at": now.isoformat(),
            "raw": {
                "operation_id": operation_id,
                "status": status,
                "candidate_count": candidate_count,
                "selected_count": candidate_count + skipped_count,
                "enriched_count": enriched_count,
                "unchanged_count": unchanged_count,
                "failed_count": failed_count,
                "skipped_count": skipped_count,
                "skip_reasons": (
                    [
                        {
                            "code": "MISSING_PROFILE_URL",
                            "count": skipped_count,
                            "message": "缺少个人主页",
                        }
                    ]
                    if skipped_count
                    else []
                ),
            },
        }
    )
    job.agent_trace = trace[-100:]
    job.active_candidate_enrichment_operation_id = None
    job.active_candidate_enrichment_skipped_count = 0
