"""Compatibility exports for the migrated match-analysis HTTP adapter."""

from app.modules.matching.api import (
    cancel_match_analysis_job,
    create_job,
    delete_match_analysis_job,
    get_match_analysis_job,
    list_match_analysis_job_items,
    list_match_analysis_jobs,
    restore_match_analysis_job,
    retry_failed_match_analysis_job_api,
    router,
)

__all__ = [
    "cancel_match_analysis_job",
    "create_job",
    "delete_match_analysis_job",
    "get_match_analysis_job",
    "list_match_analysis_job_items",
    "list_match_analysis_jobs",
    "restore_match_analysis_job",
    "retry_failed_match_analysis_job_api",
    "router",
]
