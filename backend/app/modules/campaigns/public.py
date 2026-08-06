"""Public entry point for campaign and outreach-template capabilities."""

from .item_actions import (
    batch_item_is_ready_for_llm_generation,
    batch_item_uses_llm_generation,
    batch_item_uses_llm_generation_column,
    normalize_batch_item_generation_mode,
    resolve_batch_task_item_next_action,
)
from .status import (
    batch_item_counts_as_completed,
    count_completed_batch_task_items,
    should_mark_batch_task_completed,
    sync_batch_task_completion,
)
from .drafts.fallback import (
    DRAFT_FALLBACK_REASON_MISSING_RESEARCH_DIRECTION,
    DRAFT_GENERATION_SOURCE_LLM,
    DRAFT_GENERATION_SOURCE_TEMPLATE,
    DRAFT_GENERATION_SOURCE_TEMPLATE_FALLBACK,
    InitialBatchDraft,
    build_initial_batch_draft,
    build_missing_research_fallback_for_task,
    professor_has_research_direction,
)
from .scheduling import (
    build_jittered_batch_schedule,
    has_future_batch_window,
    is_batch_window_expired,
    is_datetime_in_batch_window,
    normalize_scheduled_dates,
)
from .schemas import (
    BatchTaskActionResponse,
    BatchTaskBulkApproveDraftsRequest,
    BatchTaskBulkApproveDraftsResponse,
    BatchTaskCardRead,
    BatchTaskItemRead,
    BatchTaskResendContextRead,
    BatchTaskResendContextTaskRead,
    BatchTaskResendDefaultsRead,
    BatchTaskResendItemRead,
    BatchTaskResendSummaryRead,
    CreateBatchTaskRequest,
)
from .templates.library import (
    apply_template_to_identity_legacy_fields,
    clear_global_default_template,
    clear_identity_default_template,
    create_template_from_legacy_identity,
    get_default_outreach_template_for_identity,
    get_outreach_template,
    identity_has_legacy_template,
    normalize_generation_mode,
    normalize_nullable_template_text,
    normalize_template_name,
    serialize_outreach_template,
    sync_template_to_default_identities,
    unlink_template_from_identities,
)
from .templates.mutations import (
    OutreachTemplateMutationError,
    archive_outreach_template_record,
    create_outreach_template_record,
    duplicate_outreach_template_record,
    get_outreach_template_or_raise,
    record_outreach_template_event,
    restore_outreach_template_record,
    set_default_outreach_template_record,
    update_outreach_template_record,
)
from .templates.rendering import (
    OUTREACH_GENERATION_MODE_LLM,
    OUTREACH_GENERATION_MODE_TEMPLATE,
    TEST_RECIPIENT_NAME,
    ImportedOutreachTemplate,
    OutreachTemplateConfig,
    RenderedOutreachTemplate,
    build_outreach_template_snapshot_config,
    build_send_date_context,
    build_send_template_context,
    build_template_context,
    build_test_compose_send_template_context,
    build_test_compose_template_context,
    get_identity_sender_name,
    get_outreach_template_defaults_validation_error,
    has_outreach_template_snapshot,
    html_to_text,
    import_outreach_template_file,
    normalize_html_template,
    render_identity_outreach_template,
    render_outreach_template,
    render_template_string,
    render_template_with_context,
    resolve_outreach_template_config,
)
from .templates.schemas import (
    IdentityDefaultOutreachTemplateUpdate,
    OutreachTemplateCreate,
    OutreachTemplateRead,
    OutreachTemplateUpdate,
)
_AGENT_EXPORTS = {
    "archive_agent_campaign",
    "cancel_agent_campaign_item_send",
    "execute_campaign_create_snapshot",
    "execute_campaign_restore_send_snapshot",
    "execute_campaign_resume_snapshot",
    "execute_campaign_send_snapshot",
    "get_agent_campaign",
    "list_agent_campaign_items",
    "list_agent_campaigns",
    "pause_agent_campaign",
    "prepare_campaign_create_snapshot",
    "prepare_campaign_restore_send_snapshot",
    "prepare_campaign_resume_snapshot",
    "prepare_campaign_send_snapshot",
    "remove_agent_campaign_item",
    "restore_agent_campaign",
    "retry_agent_campaign_item_draft",
    "start_agent_campaign_draft_generation",
    "stop_agent_campaign",
}
_RESEND_EXPORTS = {
    "BatchTaskResendContextError",
    "ResendItemDecision",
    "build_batch_task_resend_context",
    "classify_resend_content",
    "decide_resend_item",
    "filter_available_material_defaults",
    "reused_content_requires_review",
}
_DRAFT_RUNTIME_EXPORTS = {
    "BatchDraftGenerationCoordinator",
    "materialize_missing_research_template_fallbacks",
    "recover_interrupted_workspace_draft_rewrites",
    "recover_stale_generating_drafts",
    "recover_stale_workspace_draft_rewrites",
    "run_queued_batch_drafts_once",
}


def __getattr__(name: str):
    if name in _AGENT_EXPORTS:
        from . import agent as owner
    elif name in _RESEND_EXPORTS:
        from . import resend as owner
    elif name in _DRAFT_RUNTIME_EXPORTS:
        from .drafts import runtime as owner
    else:
        raise AttributeError(name)

    value = getattr(owner, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(
        set(globals()) | _AGENT_EXPORTS | _RESEND_EXPORTS | _DRAFT_RUNTIME_EXPORTS
    )


__all__ = [
    *[name for name in globals() if not name.startswith("_")],
    *sorted(_AGENT_EXPORTS),
    *sorted(_DRAFT_RUNTIME_EXPORTS),
    *sorted(_RESEND_EXPORTS),
]
