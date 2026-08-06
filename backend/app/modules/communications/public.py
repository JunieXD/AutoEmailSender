"""Public entry point for email transport, synchronization, and history."""

from . import transport
from .addresses import email_matches, normalize_email_address, normalize_email_list
from .events import CommunicationEvent, collapse_communication_logs, load_communication_events
from .imap.errors import is_account_level_throttle_error, is_provider_throttle_error
from .imap.fetcher import ImapFetchedMessage
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
