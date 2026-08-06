"""Compatibility exports for migrated workspace, matching, and mail runtimes."""

from app.modules.communications.public import (
    EMAIL_TASK_RELATION_OPTIONS as TASK_RELATION_OPTIONS,  # noqa: F401
    RecentHistoryWindow as RecentHistoryWindow,
    build_recent_history_window as build_recent_history_window,
    extract_message_ids as extract_message_ids,
    get_cached_or_discover_sent_folder as get_cached_or_discover_sent_folder,
    is_imap_history_paused as is_imap_history_paused,
    is_imap_incremental_paused as is_imap_incremental_paused,
    log_imap_history_progress as log_imap_history_progress,
    mark_imap_throttled as mark_imap_throttled,
    normalize_subject as normalize_subject,
    poll_for_replies_once as poll_for_replies_once,
    poll_identity_replies as poll_identity_replies,
    poll_imap_history_once as poll_imap_history_once,
    process_imap_fetched_messages as process_imap_fetched_messages,
    repair_identity_replies as repair_identity_replies,
    sync_identity_history_once as sync_identity_history_once,
    sync_identity_history_poll_once as sync_identity_history_poll_once,
    sync_identity_imap_once as sync_identity_imap_once,
    sync_identity_incremental_once as sync_identity_incremental_once,
    sync_identity_incremental_poll_once as sync_identity_incremental_poll_once,
    sync_workspace_professor_replies as sync_workspace_professor_replies,
)
from app.modules.matching.public import (
    INTERRUPTED_MATCH_ANALYSIS_RUN_ERROR as INTERRUPTED_MATCH_ANALYSIS_RUN_ERROR,
    MatchAnalysisAlreadyRunningError as MatchAnalysisAlreadyRunningError,
    MatchCalculationActionResult as MatchCalculationActionResult,
    MatchCalculationCanceledError as MatchCalculationCanceledError,
    MatchUsageSummary as MatchUsageSummary,
    calculate_task_match as calculate_task_match,
    calculate_task_match_once as calculate_task_match_once,
    recover_interrupted_match_analysis_runs as recover_interrupted_match_analysis_runs,
)
from app.modules.workspace.tasks.delivery import *  # noqa: F403
from app.modules.workspace.tasks.runtime import *  # noqa: F403
