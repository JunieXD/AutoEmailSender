"""Public entry point for matching capabilities."""

from .job_runtime import (
    MATCH_ANALYSIS_JOB_DELETABLE_STATUSES,
    create_match_analysis_job,
    create_match_analysis_job_record,
    delete_match_analysis_job_record,
    match_analysis_job_item_score,
    request_match_analysis_job_cancel,
    request_match_analysis_job_cancel_record,
    restore_match_analysis_job_record,
    retry_failed_match_analysis_job,
    retry_failed_match_analysis_job_record,
    run_queued_match_analysis_jobs_once,
    serialize_match_analysis_job,
    serialize_match_analysis_job_item,
)
from .schemas import (
    CreateMatchAnalysisJobRequest,
    MatchAnalysisJobActionResponse,
    MatchAnalysisJobItemRead,
    MatchAnalysisJobRead,
)
from .scoring import build_draft_email, estimate_match_score

__all__ = [
    "MATCH_ANALYSIS_JOB_DELETABLE_STATUSES",
    "CreateMatchAnalysisJobRequest",
    "MatchAnalysisJobActionResponse",
    "MatchAnalysisJobItemRead",
    "MatchAnalysisJobRead",
    "build_draft_email",
    "create_match_analysis_job",
    "create_match_analysis_job_record",
    "delete_match_analysis_job_record",
    "estimate_match_score",
    "match_analysis_job_item_score",
    "request_match_analysis_job_cancel",
    "request_match_analysis_job_cancel_record",
    "restore_match_analysis_job_record",
    "retry_failed_match_analysis_job",
    "retry_failed_match_analysis_job_record",
    "run_queued_match_analysis_jobs_once",
    "serialize_match_analysis_job",
    "serialize_match_analysis_job_item",
]
