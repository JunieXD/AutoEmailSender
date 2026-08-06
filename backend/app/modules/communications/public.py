"""Public entry point for email transport, synchronization, and history."""

from . import transport
from .addresses import email_matches, normalize_email_address, normalize_email_list
from .events import CommunicationEvent, collapse_communication_logs, load_communication_events
from .imap.errors import is_account_level_throttle_error, is_provider_throttle_error
from .imap.fetcher import ImapFetchedMessage
from .imap.sync import (
    TASK_RELATION_OPTIONS as EMAIL_TASK_RELATION_OPTIONS,
    RecentHistoryWindow,
    _load_email_task as load_email_task,
    _record_email_task_log as record_email_task_log,
    build_recent_history_window,
    extract_message_ids,
    get_cached_or_discover_sent_folder,
    is_imap_history_paused,
    is_imap_incremental_paused,
    log_imap_history_progress,
    mark_imap_throttled,
    normalize_subject,
    poll_for_replies_once,
    poll_identity_replies,
    poll_imap_history_once,
    process_imap_fetched_messages,
    repair_identity_replies,
    sync_identity_history_once,
    sync_identity_history_poll_once,
    sync_identity_imap_once,
    sync_identity_incremental_once,
    sync_identity_incremental_poll_once,
    sync_workspace_professor_replies,
)
from .imap.state import (
    RECENT_V2_STRATEGY_VERSION,
    claim_next_professor_scans,
    claim_recent_v2_professor_scans,
    clear_identity_sent_folder_discovery_cache,
    clear_identity_sent_folder_discovery_cache_in_session,
    ensure_recent_v2_professor_scan_states,
    get_recent_v2_due_summary,
    mark_professor_scan_completed,
    mark_professor_scan_failed,
    mark_recent_v2_batch_completed,
    prepare_recent_v2_bulk_sent_batch,
    reset_professor_scans_to_pending,
)
from .ingestion import (
    EmailLogIngestRecord,
    build_message_fingerprint,
    normalize_message_id,
    upsert_email_log,
)
from .smtp_errors import explain_smtp_error
from .transport import (
    ImapHistoryHeaderFetchResult,
    ImapMailboxHistoryHeaderFetchResult,
    ImapMailboxUidSearchResult,
    MailAttachment,
    MailRuntimeError,
    ReceivedEmail,
    SendMailResult,
    SentFolderSyncResult,
    strip_quoted_reply_html,
    strip_quoted_reply_text,
    test_imap_connection,
    test_smtp_connection,
    text_to_html,
)

_TEST_COMPOSE_SCHEMA_EXPORTS = {
    "TestComposeDraftRead",
    "TestComposeDraftUpdateRequest",
    "TestComposeGenerateRequest",
    "TestComposeIdentityRead",
    "TestComposeLLMRead",
    "TestComposeMessageRead",
    "TestComposeMessageSendRequest",
    "TestComposeStatusRead",
    "TestComposeThreadRead",
}
_TEST_COMPOSE_EXPORTS = {
    "build_test_compose_thread",
    "generate_test_compose_draft",
    "get_test_compose_status",
    "prepare_test_compose_send_snapshot",
    "save_test_compose_draft",
    "send_test_compose_message",
}


def __getattr__(name: str):
    if name in _TEST_COMPOSE_SCHEMA_EXPORTS:
        from .test_compose import schemas as owner
    elif name in _TEST_COMPOSE_EXPORTS:
        from .test_compose import runtime as owner
    else:
        raise AttributeError(name)

    value = getattr(owner, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | _TEST_COMPOSE_EXPORTS | _TEST_COMPOSE_SCHEMA_EXPORTS)


__all__ = [
    *[name for name in globals() if not name.startswith("_")],
    *sorted(_TEST_COMPOSE_SCHEMA_EXPORTS),
    *sorted(_TEST_COMPOSE_EXPORTS),
]
