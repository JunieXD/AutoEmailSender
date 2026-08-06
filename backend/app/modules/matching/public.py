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
from .task_analysis import (
    INTERRUPTED_MATCH_ANALYSIS_RUN_ERROR,
    MatchAnalysisAlreadyRunningError,
    MatchCalculationActionResult,
    MatchCalculationCanceledError,
    MatchUsageSummary,
    calculate_task_match,
    calculate_task_match_once,
    recover_interrupted_match_analysis_runs,
)

__all__ = [
    "INTERRUPTED_MATCH_ANALYSIS_RUN_ERROR",
    "MATCH_ANALYSIS_JOB_DELETABLE_STATUSES",
    "CreateMatchAnalysisJobRequest",
    "MatchAnalysisJobActionResponse",
    "MatchAnalysisJobItemRead",
    "MatchAnalysisJobRead",
    "MatchAnalysisAlreadyRunningError",
    "MatchCalculationActionResult",
    "MatchCalculationCanceledError",
    "MatchUsageSummary",
    "build_draft_email",
    "calculate_task_match",
    "calculate_task_match_once",
    "create_match_analysis_job",
    "create_match_analysis_job_record",
    "delete_match_analysis_job_record",
    "estimate_match_score",
    "match_analysis_job_item_score",
    "request_match_analysis_job_cancel",
    "request_match_analysis_job_cancel_record",
    "recover_interrupted_match_analysis_runs",
    "restore_match_analysis_job_record",
    "retry_failed_match_analysis_job",
    "retry_failed_match_analysis_job_record",
    "run_queued_match_analysis_jobs_once",
    "serialize_match_analysis_job",
    "serialize_match_analysis_job_item",
]
