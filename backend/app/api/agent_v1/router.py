from __future__ import annotations

import asyncio
import os
from datetime import datetime
from hashlib import sha256
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from time import perf_counter
from typing import Literal, TypeVar
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ValidationError
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.agent_api_errors import AgentApiError
from app.core.agent_runtime_descriptor import (
    RUNTIME_PROTOCOL_VERSION,
    get_desktop_pid,
    get_runtime_id,
)
from app.core.agent_revisions import ensure_revision, revision_for
from app.core.config import get_settings
from app.core.database import get_async_session, get_session_factory
from app.core.query_chunks import chunked_values
from app.core.time import utc_now
from app.models import (
    CrawlJob,
    CrawlCandidate,
    CrawlPage,
    CrawlJobTriggerMode,
    EmailLog,
    EmailLogRecordState,
    EmailTask,
    IdentityMaterial,
    IdentityProfile,
    LLMProfile,
    MatchAnalysisJob,
    MatchAnalysisJobItem,
    MatchAnalysisRun,
    OperationLog,
    OutreachTemplate,
    Professor,
    ProfessorTag,
)
from app.schemas.agent import (
    AgentActionPlanRead,
    AgentBatchItemsRequest,
    AgentCampaignApproveDraftsRequest,
    AgentCampaignBulkApproveRead,
    AgentCampaignCreateRequest,
    AgentCampaignItemRead,
    AgentCampaignRead,
    AgentCampaignSendRequest,
    AgentChangePlanRead,
    AgentCommunicationGroupDeleteRead,
    AgentCommunicationSyncRead,
    AgentCommunicationSyncRequest,
    AgentCommunicationThreadDetailRead,
    AgentCommunicationThreadRead,
    AgentCrawlCandidateRead,
    AgentCrawlJobEventRead,
    AgentCrawlCandidateUpdateRequest,
    AgentCrawlJobBatchCreateRead,
    AgentCrawlJobBatchEnrichItem,
    AgentCrawlJobBatchEnrichRead,
    AgentCrawlJobApproveRequest,
    AgentCrawlJobEnrichRequest,
    AgentCrawlJobRetryRequest,
    AgentCrawlPageRead,
    AgentDraftRead,
    AgentDraftGenerateRequest,
    AgentDraftRegenerateRequest,
    AgentDraftRewriteRequest,
    AgentDraftSaveRequest,
    AgentEmailDeliveryPageRead,
    AgentIdentityRead,
    AgentIdentitySettingsUpdateRequest,
    AgentInfoRead,
    AgentRuntimeInfoRead,
    AgentLLMProfileRead,
    AgentLLMProfileSettingsUpdateRequest,
    AgentLLMProfileModelsRead,
    AgentLLMProfileTestRead,
    AgentMaterialRead,
    AgentMatchAnalysisJobActionRead,
    AgentMatchAnalysisJobCreateRequest,
    AgentMatchAnalysisJobItemRead,
    AgentMatchAnalysisJobRead,
    AgentMessageRead,
    AgentPage,
    AgentPlanExecuteRequest,
    AgentProfessorBulkArchiveRequest,
    AgentProfessorBulkTagsRequest,
    AgentProfessorPresentSelectionRequest,
    AgentPrepareSendRequest,
    AgentProfessorRead,
    AgentProfessorTagRead,
    AgentProfessorTagUsageRead,
    AgentProfessorTagCreateRequest,
    AgentProfessorTagSetRequest,
    AgentProfessorUpdateRequest,
    AgentProfessorUpsertRequest,
    AgentTaskMatchCalculationRead,
    AgentTaskOutreachConfigRequest,
    AgentTaskPrimaryMaterialRequest,
    AgentTaskRuntimeProfileRequest,
    AgentTaskTokenUsageRead,
    AgentTemplateCreateRequest,
    AgentTemplateImportRead,
    AgentTemplateRead,
    AgentTemplateUpdateRequest,
    AgentUiHandoffAcknowledgeRequest,
    AgentUiHandoffClaimRead,
    AgentUiHandoffClaimRequest,
    AgentUiHandoffRead,
    AgentWorkspaceThreadRead,
)
from app.schemas.dashboard import DashboardOverviewRead
from app.schemas.diagnostics import (
    DiagnosticFileRead,
    OperationLogExportResponse,
    OperationLogListResponse,
    OperationLogRead,
)
from app.modules.identities.public import (
    CommunicationGroupMutationError,
    ConnectionTestResult,
    MaterialMutationError,
    build_material_download_name,
    IdentityCommunicationGroupRead,
    IdentityCommunicationGroupWrite,
    create_communication_group_record,
    delete_communication_group_record,
    get_communication_group_record,
    list_communication_group_records,
    set_primary_material_record,
    resolve_identity_communication_scope,
    update_communication_group_record,
    upload_identity_material_record,
)
from app.modules.professors.public import (
    CreateProfessorInformationEnrichmentJobRequest,
    ProfessorBulkTagsPayload,
    ProfessorInformationEnrichmentItemRead,
    ProfessorInformationEnrichmentJobActionRead,
    ProfessorInformationEnrichmentJobRead,
    ProfessorMutationError,
    ProfessorTagPayload,
    ProfessorTagUpdatePayload,
    ProfessorUpsertPayload,
    archive_professor_record,
    build_professor_template,
    build_professor_export,
    create_professor_record,
    create_professor_information_enrichment_job_record,
    create_professor_tag_record,
    get_professor_tag_usage_snapshot,
    get_professor_information_enrichment_job,
    get_professor_with_tags_or_raise,
    restore_professor_record,
    delete_professor_information_enrichment_job_record,
    list_professor_information_enrichment_items_page,
    list_professor_information_enrichment_jobs,
    professor_name_script_clause,
    request_professor_information_enrichment_cancel,
    restore_professor_information_enrichment_job_record,
    retry_failed_professor_information_enrichment_job_record,
    set_professor_tags_record,
    update_professor_record,
)
from app.modules.system.public import (
    RuntimeSettingsRead,
    RuntimeSettingsUpdate,
    get_runtime_settings,
    serialize_runtime_settings,
    update_runtime_settings,
)
from app.modules.workspace.public import (
    BatchDraftApprovalConflictError,
    EmailTaskApprovalRequest,
    WorkspaceSyncWarningRead,
    WorkspaceThreadRead,
    approve_draft_task,
    approve_generated_batch_drafts,
    build_workspace_thread_for_task,
)
from app.modules.workspace.deliveries.schemas import (
    EmailDeliveryActionRead,
    EmailDeliveryRescheduleRequest,
    EmailDeliverySort,
    EmailDeliverySource,
    EmailDeliveryView,
)
from app.modules.workspace.deliveries.service import (
    list_email_deliveries,
    reschedule_email_delivery,
)
from app.modules.campaigns.public import (
    IdentityDefaultOutreachTemplateUpdate,
    OutreachTemplateCreate,
    OutreachTemplateUpdate,
)
from app.modules.community.public import (
    CommunityCatalogRead,
    CommunityImportPayload,
    CommunityPreviewPayload,
    CommunityRecordSelectionPayload,
    CommunityRecordsRead,
)
from app.modules.campaigns.public import BatchTaskResendContextRead
from app.modules.crawler.public import (
    CrawlCandidateUpdatePayload,
    CrawlJobEnrichResult,
    CrawlJobCreatePayload,
    CrawlJobRetryPayload,
    CrawlJobResumePayload,
    CrawlJobSummaryRead,
)
from app.schemas.token_usage import (
    TokenUsageChartPreset,
    TokenUsageChartRead,
    TokenUsageFeatureFilter,
    TokenUsageRecordListRead,
    TokenUsageVisualizationRead,
)
from app.modules.communications.public import (
    TestComposeDraftUpdateRequest,
    TestComposeGenerateRequest,
    TestComposeMessageSendRequest,
    TestComposeStatusRead,
    TestComposeThreadRead,
)
from app.services.agent_action_plans import (
    cancel_email_action_plan,
    create_email_action_plan,
    execute_email_action_plan,
    get_email_action_plan,
)
from app.services.agent_drafts import (
    generate_agent_draft,
    regenerate_agent_draft,
    rewrite_agent_draft,
    save_agent_draft,
)
from app.services.agent_change_plans import (
    cancel_change_plan,
    create_campaign_create_change_plan,
    create_campaign_restore_send_change_plan,
    create_campaign_resume_change_plan,
    create_campaign_send_change_plan,
    create_community_mentor_import_change_plan,
    create_crawl_candidate_approval_change_plan,
    create_crawl_job_retry_change_plan,
    create_material_delete_change_plan,
    create_professor_bulk_archive_change_plan,
    create_professor_bulk_tags_change_plan,
    create_professor_import_change_plan,
    create_professor_tag_delete_change_plan,
    create_test_email_send_change_plan,
    create_template_archive_change_plan,
    execute_change_plan,
    get_change_plan,
)
from app.services.agent_ui_handoffs import (
    acknowledge_ui_handoff,
    cancel_ui_handoff,
    claim_next_ui_handoff,
    create_communication_thread_ui_handoff,
    create_crawl_job_ui_handoff,
    create_draft_workspace_ui_handoff,
    create_professor_selection_ui_handoff,
    create_task_center_ui_handoff,
    get_ui_handoff,
    retry_ui_handoff,
)
from app.modules.community.public import (
    CommunityDataError,
    CommunityMentorDataService,
    build_community_comparisons,
    build_community_records_response,
    build_community_share_package,
    sync_community_link_lifecycle,
)
from app.modules.campaigns.public import (
    archive_agent_campaign,
    cancel_agent_campaign_item_send,
    get_agent_campaign,
    list_agent_campaign_items,
    list_agent_campaigns,
    pause_agent_campaign,
    remove_agent_campaign_item,
    restore_agent_campaign,
    retry_agent_campaign_item_draft,
    start_agent_campaign_draft_generation,
    stop_agent_campaign,
)
from app.modules.campaigns.public import (
    BatchTaskResendContextError,
    build_batch_task_resend_context,
)
from app.services.agent_mutations import execute_agent_factory_mutation, execute_agent_mutation
from app.modules.matching.public import (
    create_match_analysis_job_record,
    delete_match_analysis_job_record,
    match_analysis_job_item_score,
    request_match_analysis_job_cancel_record,
    restore_match_analysis_job_record,
    retry_failed_match_analysis_job_record,
)
from app.modules.crawler.public import crawler_debug_file_path
from app.services.operation_logs import (
    record_operation_log,
    sanitize_diagnostic_metadata,
    sanitize_diagnostic_text,
    sanitize_user_visible_error,
)
from app.modules.communications.public import test_imap_connection, test_smtp_connection
from app.modules.llm.public import (
    LLMModelCatalogResult,
    LLMProbeResult,
    LLMRuntimeError,
    ensure_llm_runtime_adaptation,
    fetch_llm_profile_models,
    probe_llm_profile,
    resolve_base_url,
)
from app.modules.communications.public import explain_smtp_error
from app.modules.crawler.public import (
    CrawlJobRecordError,
    canonical_candidate_clause,
    cancel_faculty_crawl_job_record,
    create_faculty_crawl_job_record,
    delete_faculty_crawl_job_record,
    enqueue_faculty_crawl_candidate_enrichment_records,
    get_faculty_crawl_candidate_or_raise,
    get_faculty_crawl_job_or_raise,
    get_faculty_crawl_job_summary,
    list_faculty_crawl_candidates,
    list_faculty_crawl_job_records,
    list_faculty_crawl_pages,
    pause_faculty_crawl_job_record,
    restore_faculty_crawl_job_record,
    resume_faculty_crawl_job_record,
    resume_faculty_crawl_job_review_record,
    update_faculty_crawl_candidate_record,
)
from app.modules.crawler.public import build_crawl_job_events
from app.services.dashboard_stats import build_dashboard_overview
from app.modules.campaigns.public import (
    OutreachTemplateMutationError,
    create_outreach_template_record,
    duplicate_outreach_template_record,
    restore_outreach_template_record,
    set_default_outreach_template_record,
    update_outreach_template_record,
)
from app.modules.campaigns.public import (
    apply_template_to_identity_legacy_fields,
    clear_identity_default_template,
    get_outreach_template,
)
from app.modules.campaigns.public import import_outreach_template_file
from app.modules.workspace.public import (
    build_workspace_thread,
    cancel_scheduled_task,
    continue_task_manually,
    ensure_workspace_task,
    start_follow_up_task,
    update_task_outreach_config,
    update_task_primary_material,
)
from app.modules.matching.public import (
    MatchAnalysisAlreadyRunningError,
    calculate_task_match_once,
)
from app.modules.communications.public import (
    build_test_compose_thread,
    generate_test_compose_draft,
    get_test_compose_status,
    save_test_compose_draft,
    sync_identity_history_poll_once,
    sync_workspace_professor_replies,
)
from app.modules.llm.public import ThinkingAdaptationFailed
from app.services.token_usage_records import (
    build_token_usage_chart,
    build_token_usage_visualization,
    list_token_usage_records,
)


router = APIRouter(prefix="/api/agent/v1", tags=["agent-v1"])
PageItem = TypeVar("PageItem")
TestEmailActionResult = TypeVar("TestEmailActionResult")


def get_agent_community_mentor_data_service() -> CommunityMentorDataService:
    return CommunityMentorDataService()


def get_agent_community_mentor_data_service_factory() -> Callable[[], CommunityMentorDataService]:
    return get_agent_community_mentor_data_service


def _cancel_agent_campaign_draft_generation(request: Request, campaign_id: int) -> None:
    runtime_manager = getattr(request.app.state, "runtime_manager", None)
    if runtime_manager is not None:
        runtime_manager.cancel_batch_draft_generation(campaign_id)


@router.get("/info", response_model=AgentInfoRead)
async def read_agent_api_info() -> AgentInfoRead:
    return AgentInfoRead(
        app_version=os.getenv("AUTO_EMAIL_SENDER_APP_VERSION", "development"),
    )


@router.get("/runtime", response_model=AgentRuntimeInfoRead)
async def read_agent_runtime(request: Request) -> AgentRuntimeInfoRead:
    runtime_error = getattr(request.app.state, "runtime_error", None)
    runtime_ready = bool(getattr(request.app.state, "runtime_ready", False))
    state: Literal["starting", "ready", "error"] = (
        "error" if runtime_error else ("ready" if runtime_ready else "starting")
    )
    return AgentRuntimeInfoRead(
        runtime_id=get_runtime_id(),
        protocol_version=RUNTIME_PROTOCOL_VERSION,
        app_version=os.getenv("AUTO_EMAIL_SENDER_APP_VERSION", "development"),
        backend_pid=os.getpid(),
        desktop_pid=get_desktop_pid(),
        state=state,
    )


@router.get("/professors", response_model=AgentPage[AgentProfessorRead])
async def list_agent_professors(
    q: str | None = Query(default=None),
    name_script: Literal["latin", "han", "cyrillic", "arabic", "digit"] | None = Query(default=None),
    archived: Literal["active", "archived", "all"] = Query(default="active"),
    tag_id: int | None = Query(default=None, ge=1),
    professor_id: int | None = Query(default=None, ge=1),
    fields: str | None = Query(default=None, max_length=4_000),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentProfessorRead] | Response:
    statement = select(Professor).options(selectinload(Professor.tags))
    if professor_id is not None:
        statement = statement.where(Professor.id == professor_id)
    if name_script is not None:
        statement = statement.where(professor_name_script_clause(name_script))
    if archived == "active":
        statement = statement.where(Professor.archived_at.is_(None))
    elif archived == "archived":
        statement = statement.where(Professor.archived_at.is_not(None))
    if tag_id is not None:
        statement = statement.where(Professor.tags.any(ProfessorTag.id == tag_id))
    normalized_query = (q or "").strip()
    if normalized_query:
        search = f"%{normalized_query}%"
        statement = statement.where(
            or_(
                Professor.name.ilike(search),
                Professor.email.ilike(search),
                Professor.university.ilike(search),
                Professor.school.ilike(search),
                Professor.department.ilike(search),
                Professor.research_direction.ilike(search),
                Professor.personal_note.ilike(search),
            ),
        )
    professors = list(
        (
            await session.scalars(
                statement.order_by(Professor.id.asc()).offset(cursor).limit(limit + 1),
            )
        ).unique(),
    )
    page, next_cursor, has_more = _slice_page(professors, cursor=cursor, limit=limit)
    response = AgentPage(
        items=[_serialize_professor(professor) for professor in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )
    return _project_agent_collection_response(response, fields)


@router.get("/professors/export")
async def export_agent_professors(
    format: Literal["xlsx", "csv"] = Query(default="xlsx"),
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    professors = list(
        await session.scalars(
            select(Professor)
            .where(Professor.archived_at.is_(None))
            .order_by(Professor.updated_at.desc(), Professor.created_at.desc()),
        ),
    )
    try:
        content, media_type, filename = build_professor_export(professors, format)
    except ValueError as exc:
        raise AgentApiError(
            status_code=400,
            code="INVALID_EXPORT_FORMAT",
            message=str(exc),
        ) from exc
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/professors/import-template")
async def download_agent_professor_import_template(
    format: Literal["xlsx", "csv"] = Query(default="xlsx"),
) -> Response:
    try:
        content, media_type, filename = build_professor_template(format)
    except ValueError as exc:
        raise AgentApiError(
            status_code=400,
            code="INVALID_TEMPLATE_FORMAT",
            message=str(exc),
        ) from exc
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/professors/{professor_id}", response_model=AgentProfessorRead)
async def read_agent_professor(
    professor_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> AgentProfessorRead:
    professor = await session.scalar(
        select(Professor)
        .options(selectinload(Professor.tags))
        .where(Professor.id == professor_id),
    )
    if professor is None:
        raise HTTPException(status_code=404, detail="未找到导师")
    return _serialize_professor(professor)


@router.post(
    "/professors",
    response_model=AgentProfessorRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_professor(
    payload: AgentProfessorUpsertRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentProfessorRead:
    request_data = payload.model_dump(mode="json")
    try:
        return await execute_agent_mutation(
            session,
            command="professors.create",
            request_data=request_data,
            idempotency_key=idempotency_key,
            response_type=AgentProfessorRead,
            mutation=lambda: _create_agent_professor(session, payload),
        )
    except ProfessorMutationError as exc:
        raise _agent_professor_error(exc) from exc


@router.put("/professors/{professor_id}", response_model=AgentProfessorRead)
async def update_agent_professor(
    professor_id: int,
    payload: AgentProfessorUpdateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentProfessorRead:
    if not payload.model_fields_set:
        raise AgentApiError(
            status_code=400,
            code="EMPTY_PROFESSOR_UPDATE",
            message="请至少提供一个需要修改的导师字段。",
        )
    request_data = {
        "professor_id": professor_id,
        "if_revision": if_revision,
        **payload.model_dump(mode="json", exclude_unset=True),
    }
    try:
        return await execute_agent_mutation(
            session,
            command="professors.update",
            request_data=request_data,
            idempotency_key=idempotency_key,
            response_type=AgentProfessorRead,
            mutation=lambda: _update_agent_professor_with_revision(
                session,
                professor_id,
                payload,
                if_revision=if_revision,
            ),
        )
    except ProfessorMutationError as exc:
        raise _agent_professor_error(exc) from exc


@router.post("/professors/{professor_id}/archive", response_model=AgentProfessorRead)
async def archive_agent_professor(
    professor_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentProfessorRead:
    try:
        return await execute_agent_mutation(
            session,
            command="professors.archive",
            request_data={"professor_id": professor_id, "if_revision": if_revision},
            idempotency_key=idempotency_key,
            response_type=AgentProfessorRead,
            mutation=lambda: _archive_agent_professor_with_revision(
                session,
                professor_id,
                if_revision=if_revision,
            ),
        )
    except ProfessorMutationError as exc:
        raise _agent_professor_error(exc) from exc


@router.post("/professors/{professor_id}/restore", response_model=AgentProfessorRead)
async def restore_agent_professor(
    professor_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentProfessorRead:
    try:
        return await execute_agent_mutation(
            session,
            command="professors.restore",
            request_data={"professor_id": professor_id, "if_revision": if_revision},
            idempotency_key=idempotency_key,
            response_type=AgentProfessorRead,
            mutation=lambda: _restore_agent_professor_with_revision(
                session,
                professor_id,
                if_revision=if_revision,
            ),
        )
    except ProfessorMutationError as exc:
        raise _agent_professor_error(exc) from exc


@router.put("/professors/{professor_id}/tags", response_model=AgentProfessorRead)
async def set_agent_professor_tags(
    professor_id: int,
    payload: AgentProfessorTagSetRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentProfessorRead:
    request_data = {
        "professor_id": professor_id,
        "if_revision": if_revision,
        **payload.model_dump(mode="json"),
    }
    try:
        return await execute_agent_mutation(
            session,
            command="professors.tags.set",
            request_data=request_data,
            idempotency_key=idempotency_key,
            response_type=AgentProfessorRead,
            mutation=lambda: _set_agent_professor_tags_with_revision(
                session,
                professor_id,
                payload,
                if_revision=if_revision,
            ),
        )
    except ProfessorMutationError as exc:
        raise _agent_professor_error(exc) from exc


@router.post(
    "/professors/prepare-bulk-tags",
    response_model=AgentChangePlanRead,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_agent_professor_bulk_tags(
    payload: AgentProfessorBulkTagsRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentChangePlanRead:
    return await create_professor_bulk_tags_change_plan(
        get_session_factory(),
        ProfessorBulkTagsPayload.model_validate(payload.model_dump()),
        idempotency_key=idempotency_key,
    )


@router.post(
    "/professors/prepare-bulk-archive",
    response_model=AgentChangePlanRead,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_agent_professor_bulk_archive(
    payload: AgentProfessorBulkArchiveRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentChangePlanRead:
    return await create_professor_bulk_archive_change_plan(
        get_session_factory(),
        payload.resolved_selection(),
        idempotency_key=idempotency_key,
    )


@router.post(
    "/professors/present-selection",
    response_model=AgentUiHandoffRead,
    status_code=status.HTTP_201_CREATED,
)
async def present_agent_professor_selection(
    payload: AgentProfessorPresentSelectionRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentUiHandoffRead:
    return await create_professor_selection_ui_handoff(
        get_session_factory(),
        payload,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/tasks/{task_id}/present",
    response_model=AgentUiHandoffRead,
    status_code=status.HTTP_201_CREATED,
)
async def present_agent_task(
    task_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentUiHandoffRead:
    return await create_task_center_ui_handoff(
        get_session_factory(),
        task_id,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/drafts/{task_id}/present",
    response_model=AgentUiHandoffRead,
    status_code=status.HTTP_201_CREATED,
)
async def present_agent_draft(
    task_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentUiHandoffRead:
    return await create_draft_workspace_ui_handoff(
        get_session_factory(),
        task_id,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/crawler/jobs/{job_id}/present",
    response_model=AgentUiHandoffRead,
    status_code=status.HTTP_201_CREATED,
)
async def present_agent_crawl_job(
    job_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentUiHandoffRead:
    return await create_crawl_job_ui_handoff(
        get_session_factory(),
        job_id,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/communications/threads/{thread_id}/present",
    response_model=AgentUiHandoffRead,
    status_code=status.HTTP_201_CREATED,
)
async def present_agent_communication_thread(
    thread_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentUiHandoffRead:
    return await create_communication_thread_ui_handoff(
        get_session_factory(),
        thread_id,
        idempotency_key=idempotency_key,
    )


@router.get("/ui-handoffs/{handoff_id}", response_model=AgentUiHandoffRead)
async def read_agent_ui_handoff(handoff_id: str) -> AgentUiHandoffRead:
    return await get_ui_handoff(get_session_factory(), handoff_id)


@router.post("/ui-handoffs/{handoff_id}/cancel", response_model=AgentUiHandoffRead)
async def cancel_agent_ui_handoff(handoff_id: str) -> AgentUiHandoffRead:
    return await cancel_ui_handoff(get_session_factory(), handoff_id)


@router.post("/ui-handoffs/{handoff_id}/retry", response_model=AgentUiHandoffRead)
async def retry_agent_ui_handoff(handoff_id: str) -> AgentUiHandoffRead:
    return await retry_ui_handoff(get_session_factory(), handoff_id)


@router.post(
    "/ui-handoffs/claim-next",
    response_model=AgentUiHandoffClaimRead,
    responses={status.HTTP_204_NO_CONTENT: {"description": "没有待交付界面状态"}},
)
async def claim_agent_ui_handoff(
    payload: AgentUiHandoffClaimRequest,
) -> AgentUiHandoffClaimRead | Response:
    handoff = await claim_next_ui_handoff(
        get_session_factory(),
        payload.consumer_id,
    )
    if handoff is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return handoff


@router.post(
    "/ui-handoffs/{handoff_id}/acknowledge",
    response_model=AgentUiHandoffRead,
)
async def acknowledge_agent_ui_handoff(
    handoff_id: str,
    payload: AgentUiHandoffAcknowledgeRequest,
) -> AgentUiHandoffRead:
    return await acknowledge_ui_handoff(
        get_session_factory(),
        handoff_id,
        payload,
    )


@router.post(
    "/professors/prepare-import",
    response_model=AgentChangePlanRead,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_agent_professor_import(
    file: UploadFile = File(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentChangePlanRead:
    if not file.filename:
        raise AgentApiError(
            status_code=400,
            code="PROFESSOR_IMPORT_FILE_REQUIRED",
            message="请选择要导入的文件。",
        )
    return await create_professor_import_change_plan(
        get_session_factory(),
        Path(file.filename).name,
        await file.read(),
        idempotency_key=idempotency_key,
    )


@router.get("/community-mentors/catalog", response_model=CommunityCatalogRead)
async def get_agent_community_catalog(
    refresh: bool = Query(default=False),
    session: AsyncSession = Depends(get_async_session),
    service: CommunityMentorDataService = Depends(get_agent_community_mentor_data_service),
) -> CommunityCatalogRead:
    try:
        bundle = await service.get_catalog(force_refresh=refresh)
        lifecycle_warnings = await sync_community_link_lifecycle(session, bundle)
        await session.commit()
    except CommunityDataError as exc:
        await session.rollback()
        raise _agent_community_mentor_error(exc) from exc
    return CommunityCatalogRead(
        schema_version=bundle.catalog.schema_version,
        dataset_version=bundle.catalog.dataset_version,
        generated_at=bundle.catalog.generated_at,
        record_count=bundle.catalog.record_count,
        universities=bundle.catalog.universities,
        source=bundle.source,
        stale=bundle.stale,
        warning=bundle.warning,
        verified_at=bundle.verified_at,
        lifecycle_warnings=lifecycle_warnings,
    )


@router.post("/community-mentors/records", response_model=CommunityRecordsRead)
async def list_agent_community_records(
    payload: CommunityRecordSelectionPayload,
    session: AsyncSession = Depends(get_async_session),
    service: CommunityMentorDataService = Depends(get_agent_community_mentor_data_service),
) -> CommunityRecordsRead:
    try:
        record_bundle = await service.load_records(
            dataset_version=payload.dataset_version,
            unit_paths=payload.unit_paths,
        )
        lifecycle_warnings = await sync_community_link_lifecycle(
            session,
            record_bundle.catalog_bundle,
        )
        comparisons = await build_community_comparisons(session, record_bundle.records)
        await session.commit()
    except CommunityDataError as exc:
        await session.rollback()
        raise _agent_community_mentor_error(exc) from exc
    return build_community_records_response(
        bundle=record_bundle,
        comparisons=comparisons,
        lifecycle_warnings=lifecycle_warnings,
    )


@router.post("/community-mentors/preview", response_model=CommunityRecordsRead)
async def preview_agent_community_import(
    payload: CommunityPreviewPayload,
    session: AsyncSession = Depends(get_async_session),
    service: CommunityMentorDataService = Depends(get_agent_community_mentor_data_service),
) -> CommunityRecordsRead:
    try:
        record_bundle = await service.load_records(
            dataset_version=payload.dataset_version,
            unit_paths=payload.unit_paths,
        )
        records_by_id = {record.id: record for record in record_bundle.records}
        missing_ids = [record_id for record_id in payload.record_ids if record_id not in records_by_id]
        if missing_ids:
            raise CommunityDataError(
                f"所选导师不属于当前学院数据：{', '.join(missing_ids[:3])}",
                code="COMMUNITY_DATA_SELECTION_INVALID",
            )
        selected_records = [records_by_id[record_id] for record_id in payload.record_ids]
        lifecycle_warnings = await sync_community_link_lifecycle(
            session,
            record_bundle.catalog_bundle,
        )
        comparisons = await build_community_comparisons(session, selected_records)
        await session.commit()
    except CommunityDataError as exc:
        await session.rollback()
        raise _agent_community_mentor_error(exc) from exc
    return build_community_records_response(
        bundle=record_bundle,
        comparisons=comparisons,
        lifecycle_warnings=lifecycle_warnings,
    )


@router.post(
    "/community-mentors/prepare-import",
    response_model=AgentChangePlanRead,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_agent_community_mentor_import(
    payload: CommunityImportPayload,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    service: CommunityMentorDataService = Depends(get_agent_community_mentor_data_service),
) -> AgentChangePlanRead:
    return await create_community_mentor_import_change_plan(
        get_session_factory(),
        payload,
        service,
        idempotency_key=idempotency_key,
    )


@router.get("/community-mentors/share-package")
async def export_agent_community_share_package(
    professor_ids: str = Query(min_length=1, max_length=4_000),
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    try:
        ids = _parse_agent_professor_ids(professor_ids)
    except ValueError as exc:
        raise AgentApiError(
            status_code=400,
            code="PROFESSOR_IDS_INVALID",
            message=str(exc),
        ) from exc
    professors: list[Professor] = []
    for professor_id_chunk in chunked_values(ids):
        professors.extend(
            await session.scalars(
                select(Professor).where(Professor.id.in_(professor_id_chunk)),
            ),
        )
    professors_by_id = {professor.id: professor for professor in professors}
    missing_ids = [professor_id for professor_id in ids if professor_id not in professors_by_id]
    if missing_ids:
        raise AgentApiError(
            status_code=404,
            code="PROFESSOR_NOT_FOUND",
            message=f"未找到导师：{missing_ids[0]}",
        )
    try:
        content = build_community_share_package(
            [professors_by_id[professor_id] for professor_id in ids],
        )
    except ValueError as exc:
        raise AgentApiError(
            status_code=400,
            code="COMMUNITY_SHARE_PACKAGE_INVALID",
            message=str(exc),
        ) from exc
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="community-share.xlsx"'},
    )


@router.get("/professor-tags", response_model=AgentPage[AgentProfessorTagRead])
async def list_agent_professor_tags(
    tag_id: int | None = Query(default=None, ge=1),
    name: str | None = Query(default=None, max_length=200),
    fields: str | None = Query(default=None, max_length=4_000),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentProfessorTagRead] | Response:
    statement = select(ProfessorTag)
    if tag_id is not None:
        statement = statement.where(ProfessorTag.id == tag_id)
    if name is not None:
        statement = statement.where(ProfessorTag.name == name)
    tags = list(
        await session.scalars(
            statement.order_by(ProfessorTag.id.asc())
            .offset(cursor)
            .limit(limit + 1),
        ),
    )
    page, next_cursor, has_more = _slice_page(tags, cursor=cursor, limit=limit)
    response = AgentPage(
        items=[_serialize_tag(tag) for tag in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )
    return _project_agent_collection_response(response, fields)


@router.post(
    "/professor-tags",
    response_model=AgentProfessorTagRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_professor_tag(
    payload: AgentProfessorTagCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentProfessorTagRead:
    try:
        return await execute_agent_mutation(
            session,
            command="professors.tags.create",
            request_data=payload.model_dump(mode="json"),
            idempotency_key=idempotency_key,
            response_type=AgentProfessorTagRead,
            mutation=lambda: _create_agent_professor_tag(session, payload),
        )
    except ProfessorMutationError as exc:
        raise _agent_professor_error(exc) from exc


@router.get(
    "/professor-tags/{tag_id}/usage",
    response_model=AgentProfessorTagUsageRead,
)
async def read_agent_professor_tag_usage(
    tag_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> AgentProfessorTagUsageRead:
    try:
        return AgentProfessorTagUsageRead.model_validate(
            await get_professor_tag_usage_snapshot(session, tag_id),
        )
    except ProfessorMutationError as exc:
        raise _agent_professor_error(exc) from exc


@router.post(
    "/professor-tags/{tag_id}/prepare-delete",
    response_model=AgentChangePlanRead,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_agent_professor_tag_delete(
    tag_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentChangePlanRead:
    return await create_professor_tag_delete_change_plan(
        get_session_factory(),
        tag_id,
        idempotency_key=idempotency_key,
    )


@router.get(
    "/communications/threads",
    response_model=AgentPage[AgentCommunicationThreadRead],
)
async def list_agent_communication_threads(
    identity_id: int | None = Query(default=None, ge=1),
    professor_id: int | None = Query(default=None, ge=1),
    sent: bool | None = Query(default=None),
    replied: bool | None = Query(default=None),
    fields: str | None = Query(default=None, max_length=4_000),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentCommunicationThreadRead] | Response:
    rows = await _query_threads(
        session,
        identity_id=identity_id,
        professor_id=professor_id,
        sent=sent,
        replied=replied,
        cursor=cursor,
        limit=limit,
    )
    page, next_cursor, has_more = _slice_page(rows, cursor=cursor, limit=limit)
    response = AgentPage(
        items=[_serialize_thread_row(row) for row in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )
    return _project_agent_collection_response(response, fields)


@router.get(
    "/communications/threads/{thread_id}",
    response_model=AgentCommunicationThreadDetailRead,
)
async def read_agent_communication_thread(
    thread_id: str,
    include_body: bool = Query(default=False),
    message_cursor: int = Query(default=0, ge=0),
    message_limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentCommunicationThreadDetailRead:
    identity_id, professor_id = _parse_thread_id(thread_id)
    rows = await _query_threads(
        session,
        identity_id=identity_id,
        professor_id=professor_id,
        sent=None,
        replied=None,
        cursor=0,
        limit=1,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="未找到通信线程")
    messages = list(
        await session.scalars(
            select(EmailLog)
            .where(
                EmailLog.identity_id == identity_id,
                EmailLog.professor_id == professor_id,
                EmailLog.direction.in_(["sent", "received"]),
                EmailLog.record_state == EmailLogRecordState.CANONICAL.value,
            )
            .order_by(EmailLog.created_at.asc(), EmailLog.id.asc())
            .offset(message_cursor)
            .limit(message_limit + 1),
        ),
    )
    message_page, next_cursor, has_more = _slice_page(
        messages,
        cursor=message_cursor,
        limit=message_limit,
    )
    thread = _serialize_thread_row(rows[0])
    return AgentCommunicationThreadDetailRead(
        **thread.model_dump(),
        messages=[_serialize_message(message, include_body=include_body) for message in message_page],
        messages_next_cursor=next_cursor,
        messages_has_more=has_more,
    )


@router.get(
    "/communications/messages",
    response_model=AgentPage[AgentMessageRead],
)
async def list_agent_messages(
    thread_id: str | None = Query(default=None),
    identity_id: int | None = Query(default=None, ge=1),
    professor_id: int | None = Query(default=None, ge=1),
    direction: Literal["sent", "received", "draft"] | None = Query(default=None),
    include_body: bool = Query(default=False),
    order: Literal["asc", "desc"] = Query(default="desc"),
    fields: str | None = Query(default=None, max_length=4_000),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentMessageRead] | Response:
    if thread_id is not None:
        thread_identity_id, thread_professor_id = _parse_thread_id(thread_id)
        if identity_id is not None and identity_id != thread_identity_id:
            raise HTTPException(status_code=400, detail="thread_id 与 identity_id 不一致")
        if professor_id is not None and professor_id != thread_professor_id:
            raise HTTPException(status_code=400, detail="thread_id 与 professor_id 不一致")
        identity_id = thread_identity_id
        professor_id = thread_professor_id

    statement = select(EmailLog).where(
        EmailLog.record_state == EmailLogRecordState.CANONICAL.value,
    )
    if identity_id is not None:
        statement = statement.where(EmailLog.identity_id == identity_id)
    if professor_id is not None:
        statement = statement.where(EmailLog.professor_id == professor_id)
    if direction is not None:
        statement = statement.where(EmailLog.direction == direction)
    ordering = (
        (EmailLog.created_at.asc(), EmailLog.id.asc())
        if order == "asc"
        else (EmailLog.created_at.desc(), EmailLog.id.desc())
    )
    messages = list(
        await session.scalars(
            statement.order_by(*ordering).offset(cursor).limit(limit + 1),
        ),
    )
    page, next_cursor, has_more = _slice_page(messages, cursor=cursor, limit=limit)
    response = AgentPage(
        items=[_serialize_message(message, include_body=include_body) for message in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )
    return _project_agent_collection_response(response, fields)


@router.get("/communications/messages/{message_id}", response_model=AgentMessageRead)
async def read_agent_message(
    message_id: int,
    include_body: bool = Query(default=True),
    session: AsyncSession = Depends(get_async_session),
) -> AgentMessageRead:
    message = await session.get(EmailLog, message_id)
    if message is None or message.record_state != EmailLogRecordState.CANONICAL.value:
        raise HTTPException(status_code=404, detail="未找到邮件记录")
    return _serialize_message(message, include_body=include_body)


@router.post("/communications/sync", response_model=AgentCommunicationSyncRead)
async def sync_agent_communications(
    payload: AgentCommunicationSyncRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentCommunicationSyncRead:
    identity = await session.get(IdentityProfile, payload.identity_id)
    if identity is None:
        raise HTTPException(status_code=404, detail="未找到身份配置")
    if not _identity_has_imap_config(identity):
        raise AgentApiError(
            status_code=409,
            code="IMAP_NOT_CONFIGURED",
            message="该发件身份尚未配置 IMAP，无法同步邮箱通信记录。",
        )
    async def mutation() -> AgentCommunicationSyncRead:
        detected_count = await sync_identity_history_poll_once(
            get_session_factory(),
            identity.id,
        )
        async with get_session_factory()() as mutation_session:
            await record_operation_log(
                mutation_session,
                category="agent_action",
                event_name="agent_cli.communication_synced",
                entity_type="identity_profile",
                entity_id=str(identity.id),
                metadata={
                    "actor": "agent_cli",
                    "identity_id": identity.id,
                    "detected_count": detected_count,
                },
            )
            await mutation_session.commit()
        return AgentCommunicationSyncRead(
            identity_id=identity.id,
            detected_count=detected_count,
            completed_at=utc_now(),
            message=f"已完成一次邮箱同步检查，新增 {detected_count} 条通信记录。",
        )

    try:
        return await execute_agent_factory_mutation(
            get_session_factory(),
            command="communications.sync",
            request_data=payload.model_dump(mode="json"),
            idempotency_key=idempotency_key,
            response_type=AgentCommunicationSyncRead,
            mutation=mutation,
            external_execution=True,
        )
    except AgentApiError:
        raise
    except Exception as exc:
        message = sanitize_user_visible_error(exc)
        await record_operation_log(
            session,
            category="agent_action",
            event_name="agent_cli.communication_sync_failed",
            level="warning",
            entity_type="identity_profile",
            entity_id=str(identity.id),
            metadata={"actor": "agent_cli", "identity_id": identity.id},
        )
        await session.commit()
        raise AgentApiError(
            status_code=502,
            code="MAILBOX_SYNC_FAILED",
            message=f"邮箱同步失败：{message}",
            retryable=True,
        ) from exc


@router.get("/workspaces/{professor_id}", response_model=AgentWorkspaceThreadRead)
async def read_agent_workspace(
    professor_id: int,
    identity_id: int = Query(..., ge=1),
    llm_profile_id: int = Query(..., ge=1),
    session: AsyncSession = Depends(get_async_session),
) -> AgentWorkspaceThreadRead:
    workspace = await build_workspace_thread(
        session,
        professor_id=professor_id,
        identity_id=identity_id,
        llm_profile_id=llm_profile_id,
    )
    return _serialize_agent_workspace_thread(workspace)


@router.post(
    "/workspaces/{professor_id}/ensure-task",
    response_model=AgentWorkspaceThreadRead,
)
async def ensure_agent_workspace_task(
    professor_id: int,
    identity_id: int = Query(..., ge=1),
    llm_profile_id: int = Query(..., ge=1),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentWorkspaceThreadRead:
    return await execute_agent_mutation(
        session,
        command="workspaces.ensure-task",
        request_data={
            "professor_id": professor_id,
            "identity_id": identity_id,
            "llm_profile_id": llm_profile_id,
        },
        idempotency_key=idempotency_key,
        response_type=AgentWorkspaceThreadRead,
        mutation=lambda: _ensure_agent_workspace_task(
            session,
            professor_id=professor_id,
            identity_id=identity_id,
            llm_profile_id=llm_profile_id,
        ),
    )


@router.post(
    "/workspaces/{professor_id}/refresh-replies",
    response_model=AgentWorkspaceThreadRead,
)
async def refresh_agent_workspace_replies(
    professor_id: int,
    identity_id: int = Query(..., ge=1),
    llm_profile_id: int = Query(..., ge=1),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentWorkspaceThreadRead:
    async def mutation() -> AgentWorkspaceThreadRead:
        async with get_session_factory()() as session:
            # Validate the requested workspace before opening any configured mailbox.
            await build_workspace_thread(
                session,
                professor_id=professor_id,
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
            )
            communication_scope = await resolve_identity_communication_scope(
                session,
                active_identity_id=identity_id,
            )
            sync_identities = [
                identity
                for identity in communication_scope.identities
                if _identity_has_imap_config(identity)
            ]
            if not sync_identities:
                raise AgentApiError(
                    status_code=409,
                    code="IMAP_NOT_CONFIGURED",
                    message="当前通信范围内没有已配置 IMAP 的发件身份，无法同步回信。",
                )

            results = await asyncio.gather(
                *[
                    sync_workspace_professor_replies(
                        get_session_factory(),
                        identity.id,
                        professor_id,
                    )
                    for identity in sync_identities
                ],
                return_exceptions=True,
            )
            warnings = [
                WorkspaceSyncWarningRead(
                    identity_id=identity.id,
                    identity_name=identity.profile_name or identity.name,
                    message=sanitize_user_visible_error(result),
                )
                for identity, result in zip(sync_identities, results, strict=True)
                if isinstance(result, BaseException)
            ]
            detected_count = sum(
                result
                for result in results
                if isinstance(result, int) and not isinstance(result, bool)
            )
            await record_operation_log(
                session,
                category="agent_action",
                event_name="agent_cli.workspace_replies_refreshed",
                level="warning" if warnings else "info",
                entity_type="professor",
                entity_id=str(professor_id),
                metadata={
                    "actor": "agent_cli",
                    "professor_id": professor_id,
                    "identity_id": identity_id,
                    "llm_profile_id": llm_profile_id,
                    "sync_identity_ids": [identity.id for identity in sync_identities],
                    "detected_count": detected_count,
                    "warning_count": len(warnings),
                },
            )
            await session.commit()
            workspace = await build_workspace_thread(
                session,
                professor_id=professor_id,
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                sync_warnings=warnings,
            )
            return _serialize_agent_workspace_thread(workspace)

    return await execute_agent_factory_mutation(
        get_session_factory(),
        command="workspaces.refresh-replies",
        request_data={
            "professor_id": professor_id,
            "identity_id": identity_id,
            "llm_profile_id": llm_profile_id,
        },
        idempotency_key=idempotency_key,
        response_type=AgentWorkspaceThreadRead,
        mutation=mutation,
        external_execution=True,
    )


@router.get("/deliveries", response_model=AgentEmailDeliveryPageRead)
async def list_agent_email_deliveries(
    view: EmailDeliveryView = Query(default="upcoming"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    identity_id: int | None = Query(default=None, ge=1),
    source: EmailDeliverySource = Query(default="all"),
    status_filter: str | None = Query(default=None, alias="status"),
    sort: EmailDeliverySort | None = Query(default=None),
    search_fields: str | None = Query(default=None, max_length=100),
    query: str | None = Query(default=None, max_length=200),
    task_id: int | None = Query(default=None, ge=1),
    session: AsyncSession = Depends(get_async_session),
) -> AgentEmailDeliveryPageRead:
    try:
        result = await list_email_deliveries(
            session,
            view=view,
            page=page,
            page_size=page_size,
            identity_id=identity_id,
            source=source,
            status=status_filter,
            sort=sort,
            search_fields=(
                tuple(field.strip() for field in search_fields.split(","))
                if search_fields is not None
                else None
            ),
            query=query,
            task_id=task_id,
        )
    except ValueError as exc:
        raise AgentApiError(
            status_code=422,
            code="INVALID_DELIVERY_FILTER",
            message=str(exc),
        ) from exc
    return AgentEmailDeliveryPageRead(
        items=[
            {
                **item.model_dump(),
                "expected_updated_at": item.updated_at.isoformat(),
            }
            for item in result.items
        ],
        next_cursor=str(result.page + 1) if result.page < result.total_pages else None,
        has_more=result.page < result.total_pages,
        page=result.page,
        page_size=result.page_size,
        total=result.total_count,
        total_pages=result.total_pages,
        counts=result.counts,
    )


@router.patch("/deliveries/{task_id}/schedule", response_model=EmailDeliveryActionRead)
async def reschedule_agent_email_delivery(
    task_id: int,
    payload: EmailDeliveryRescheduleRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> EmailDeliveryActionRead:
    async def mutation() -> EmailDeliveryActionRead:
        async with get_session_factory()() as mutation_session:
            try:
                return await reschedule_email_delivery(
                    mutation_session,
                    task_id=task_id,
                    scheduled_at=payload.scheduled_at,
                    expected_updated_at=payload.expected_updated_at,
                )
            except HTTPException as exc:
                raise AgentApiError(
                    status_code=exc.status_code,
                    code="DELIVERY_RESCHEDULE_REJECTED",
                    message=str(exc.detail),
                    retryable=exc.status_code == 409,
                ) from exc

    return await execute_agent_factory_mutation(
        get_session_factory(),
        command="deliveries.reschedule",
        request_data={"task_id": task_id, **payload.model_dump(mode="json")},
        idempotency_key=idempotency_key,
        response_type=EmailDeliveryActionRead,
        mutation=mutation,
    )


@router.post("/tasks/{task_id}/approve-draft", response_model=AgentWorkspaceThreadRead)
async def approve_agent_task_draft(
    task_id: int,
    payload: AgentDraftSaveRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentWorkspaceThreadRead:
    async def mutation() -> AgentWorkspaceThreadRead:
        await _ensure_draft_revision(task_id, if_revision)
        return await _run_agent_task_workspace_action(
            session,
            task_id=task_id,
            command="drafts.approve",
            workspace_task_id=task_id,
            action=lambda: approve_draft_task(
                get_session_factory(),
                task_id,
                EmailTaskApprovalRequest(
                    subject=payload.subject,
                    body_text=payload.body_text,
                    body_html=payload.body_html,
                    selected_material_ids=payload.attachment_material_ids,
                ),
            ),
        )

    return await execute_agent_mutation(
        session,
        command="drafts.approve",
        request_data={
            "task_id": task_id,
            "if_revision": if_revision,
            **payload.model_dump(mode="json"),
        },
        idempotency_key=idempotency_key,
        response_type=AgentWorkspaceThreadRead,
        mutation=mutation,
    )


@router.post("/tasks/{task_id}/cancel-schedule", response_model=AgentWorkspaceThreadRead)
async def cancel_agent_task_schedule(
    task_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentWorkspaceThreadRead:
    return await execute_agent_mutation(
        session,
        command="tasks.cancel-schedule",
        request_data={"task_id": task_id},
        idempotency_key=idempotency_key,
        response_type=AgentWorkspaceThreadRead,
        mutation=lambda: _run_agent_task_workspace_action(
            session,
            task_id=task_id,
            command="tasks.cancel-schedule",
            workspace_task_id=task_id,
            action=lambda: cancel_scheduled_task(get_session_factory(), task_id),
        ),
    )


@router.post("/tasks/{task_id}/continue-manually", response_model=AgentWorkspaceThreadRead)
async def continue_agent_task_manually(
    task_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentWorkspaceThreadRead:
    return await execute_agent_mutation(
        session,
        command="tasks.continue-manually",
        request_data={"task_id": task_id},
        idempotency_key=idempotency_key,
        response_type=AgentWorkspaceThreadRead,
        mutation=lambda: _run_agent_task_workspace_action(
            session,
            task_id=task_id,
            command="tasks.continue-manually",
            action=lambda: continue_task_manually(get_session_factory(), task_id),
        ),
    )


@router.post("/tasks/{task_id}/start-follow-up", response_model=AgentWorkspaceThreadRead)
async def start_agent_task_follow_up(
    task_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentWorkspaceThreadRead:
    return await execute_agent_mutation(
        session,
        command="tasks.start-follow-up",
        request_data={"task_id": task_id},
        idempotency_key=idempotency_key,
        response_type=AgentWorkspaceThreadRead,
        mutation=lambda: _run_agent_task_workspace_action(
            session,
            task_id=task_id,
            command="tasks.start-follow-up",
            action=lambda: start_follow_up_task(get_session_factory(), task_id),
        ),
    )


@router.post("/tasks/{task_id}/primary-material", response_model=AgentWorkspaceThreadRead)
async def update_agent_task_primary_material(
    task_id: int,
    payload: AgentTaskPrimaryMaterialRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentWorkspaceThreadRead:
    async def mutation() -> AgentWorkspaceThreadRead:
        async with get_session_factory()() as mutation_session:
            result = await _run_agent_task_workspace_action(
                mutation_session,
                task_id=task_id,
                command="tasks.set-primary-material",
                workspace_task_id=task_id,
                action=lambda: update_task_primary_material(
                    get_session_factory(),
                    task_id,
                    payload.primary_material_id,
                ),
            )
            await mutation_session.commit()
            return result

    return await execute_agent_factory_mutation(
        get_session_factory(),
        command="tasks.set-primary-material",
        request_data={"task_id": task_id, **payload.model_dump(mode="json")},
        idempotency_key=idempotency_key,
        response_type=AgentWorkspaceThreadRead,
        mutation=mutation,
        external_execution=True,
    )


@router.post("/tasks/{task_id}/outreach-config", response_model=AgentWorkspaceThreadRead)
async def update_agent_task_outreach_config(
    task_id: int,
    payload: AgentTaskOutreachConfigRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentWorkspaceThreadRead:
    request_payload = payload.model_dump(mode="json", exclude_unset=True)
    return await execute_agent_mutation(
        session,
        command="tasks.set-outreach-config",
        request_data={"task_id": task_id, **request_payload},
        idempotency_key=idempotency_key,
        response_type=AgentWorkspaceThreadRead,
        mutation=lambda: _run_agent_task_workspace_action(
            session,
            task_id=task_id,
            command="tasks.set-outreach-config",
            workspace_task_id=task_id,
            action=lambda: update_task_outreach_config(
                get_session_factory(),
                task_id,
                outreach_generation_mode=payload.outreach_generation_mode,
                outreach_template_id=payload.outreach_template_id,
                template_selection_explicit=(
                    "outreach_template_id" in payload.model_fields_set
                ),
                outreach_template_subject=payload.outreach_template_subject,
                outreach_template_body_text=payload.outreach_template_body_text,
                outreach_template_body_html=payload.outreach_template_body_html,
            ),
        ),
    )


@router.post("/tasks/{task_id}/calculate-match", response_model=AgentTaskMatchCalculationRead)
async def calculate_agent_task_match(
    task_id: int,
    payload: AgentTaskRuntimeProfileRequest | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentTaskMatchCalculationRead:
    request_payload = payload.model_dump(mode="json") if payload is not None else None
    async def mutation() -> AgentTaskMatchCalculationRead:
        async with get_session_factory()() as mutation_session:
            result = await _calculate_agent_task_match(
                mutation_session,
                task_id=task_id,
                llm_profile_id=payload.llm_profile_id if payload is not None else None,
            )
            await mutation_session.commit()
            return result

    return await execute_agent_factory_mutation(
        get_session_factory(),
        command="tasks.calculate-match",
        request_data={"task_id": task_id, "payload": request_payload},
        idempotency_key=idempotency_key,
        response_type=AgentTaskMatchCalculationRead,
        mutation=mutation,
        external_execution=True,
    )


@router.get("/templates", response_model=AgentPage[AgentTemplateRead])
async def list_agent_templates(
    include_archived: bool = Query(default=False),
    template_id: int | None = Query(default=None, ge=1),
    is_default: bool | None = Query(default=None),
    fields: str | None = Query(default=None, max_length=4_000),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentTemplateRead] | Response:
    statement = select(OutreachTemplate)
    if not include_archived:
        statement = statement.where(OutreachTemplate.archived_at.is_(None))
    if template_id is not None:
        statement = statement.where(OutreachTemplate.id == template_id)
    if is_default is not None:
        statement = statement.where(OutreachTemplate.is_default.is_(is_default))
    templates = list(
        await session.scalars(
            statement.order_by(
                OutreachTemplate.is_default.desc(),
                OutreachTemplate.updated_at.desc(),
                OutreachTemplate.id.desc(),
            )
            .offset(cursor)
            .limit(limit + 1),
        ),
    )
    page, next_cursor, has_more = _slice_page(templates, cursor=cursor, limit=limit)
    response = AgentPage(
        items=[_serialize_template(template) for template in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )
    return _project_agent_collection_response(response, fields)


@router.get("/templates/{template_id}", response_model=AgentTemplateRead)
async def read_agent_template(
    template_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> AgentTemplateRead:
    template = await session.get(OutreachTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="未找到邮件模板")
    return _serialize_template(template)


@router.post("/templates/import-file", response_model=AgentTemplateImportRead)
async def import_agent_template_file(
    file: UploadFile = File(...),
) -> AgentTemplateImportRead:
    if not file.filename:
        raise AgentApiError(
            status_code=400,
            code="TEMPLATE_IMPORT_FILE_REQUIRED",
            message="请选择要解析的模板文件。",
        )
    try:
        imported = import_outreach_template_file(
            Path(file.filename).name,
            await file.read(),
        )
    except ValueError as exc:
        raise AgentApiError(
            status_code=400,
            code="TEMPLATE_IMPORT_INVALID",
            message=str(exc),
        ) from exc
    return AgentTemplateImportRead(
        subject=imported.subject,
        body_text=imported.body_text,
        body_html=imported.body_html,
        format_name=imported.format_name,
    )


@router.post(
    "/templates",
    response_model=AgentTemplateRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_template(
    payload: AgentTemplateCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentTemplateRead:
    try:
        return await execute_agent_mutation(
            session,
            command="templates.create",
            request_data=payload.model_dump(mode="json"),
            idempotency_key=idempotency_key,
            response_type=AgentTemplateRead,
            mutation=lambda: _create_agent_template(session, payload),
        )
    except OutreachTemplateMutationError as exc:
        raise _agent_template_error(exc) from exc


@router.put("/templates/{template_id}", response_model=AgentTemplateRead)
async def update_agent_template(
    template_id: int,
    payload: AgentTemplateUpdateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentTemplateRead:
    if not payload.model_fields_set:
        raise AgentApiError(
            status_code=400,
            code="EMPTY_TEMPLATE_UPDATE",
            message="请至少提供一个需要修改的模板字段。",
        )
    try:
        return await execute_agent_mutation(
            session,
            command="templates.update",
            request_data={
                "template_id": template_id,
                "if_revision": if_revision,
                **payload.model_dump(mode="json", exclude_unset=True),
            },
            idempotency_key=idempotency_key,
            response_type=AgentTemplateRead,
            mutation=lambda: _update_agent_template_with_revision(
                session,
                template_id,
                payload,
                if_revision=if_revision,
            ),
        )
    except OutreachTemplateMutationError as exc:
        raise _agent_template_error(exc) from exc


@router.post(
    "/templates/{template_id}/duplicate",
    response_model=AgentTemplateRead,
    status_code=status.HTTP_201_CREATED,
)
async def duplicate_agent_template(
    template_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentTemplateRead:
    try:
        return await execute_agent_mutation(
            session,
            command="templates.duplicate",
            request_data={"template_id": template_id},
            idempotency_key=idempotency_key,
            response_type=AgentTemplateRead,
            mutation=lambda: _duplicate_agent_template(session, template_id),
        )
    except OutreachTemplateMutationError as exc:
        raise _agent_template_error(exc) from exc


@router.post("/templates/{template_id}/default", response_model=AgentTemplateRead)
async def set_agent_template_default(
    template_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentTemplateRead:
    try:
        return await execute_agent_mutation(
            session,
            command="templates.set-default",
            request_data={"template_id": template_id, "if_revision": if_revision},
            idempotency_key=idempotency_key,
            response_type=AgentTemplateRead,
            mutation=lambda: _set_agent_template_default_with_revision(
                session,
                template_id,
                if_revision=if_revision,
            ),
        )
    except OutreachTemplateMutationError as exc:
        raise _agent_template_error(exc) from exc


@router.post("/templates/{template_id}/restore", response_model=AgentTemplateRead)
async def restore_agent_template(
    template_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentTemplateRead:
    try:
        return await execute_agent_mutation(
            session,
            command="templates.restore",
            request_data={"template_id": template_id, "if_revision": if_revision},
            idempotency_key=idempotency_key,
            response_type=AgentTemplateRead,
            mutation=lambda: _restore_agent_template_with_revision(
                session,
                template_id,
                if_revision=if_revision,
            ),
        )
    except OutreachTemplateMutationError as exc:
        raise _agent_template_error(exc) from exc


@router.post(
    "/templates/{template_id}/prepare-archive",
    response_model=AgentChangePlanRead,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_agent_template_archive(
    template_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentChangePlanRead:
    return await create_template_archive_change_plan(
        get_session_factory(),
        template_id,
        idempotency_key=idempotency_key,
    )


@router.get("/materials", response_model=AgentPage[AgentMaterialRead])
async def list_agent_materials(
    identity_id: int | None = Query(default=None, ge=1),
    source_identity_id: int | None = Query(default=None, ge=1),
    target_identity_id: int | None = Query(default=None, ge=1),
    material_type: str | None = Query(default=None),
    material_id: int | None = Query(default=None, ge=1),
    fields: str | None = Query(default=None, max_length=4_000),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentMaterialRead] | Response:
    if (
        identity_id is not None
        and source_identity_id is not None
        and identity_id != source_identity_id
    ):
        raise HTTPException(
            status_code=422,
            detail="identity_id 与 source_identity_id 不能指定不同的上传来源身份",
        )
    if (
        target_identity_id is not None
        and await session.get(IdentityProfile, target_identity_id) is None
    ):
        raise HTTPException(status_code=404, detail="未找到身份配置")
    resolved_source_identity_id = (
        source_identity_id if source_identity_id is not None else identity_id
    )
    statement = select(IdentityMaterial).options(
        selectinload(IdentityMaterial.source_identity),
        selectinload(IdentityMaterial.default_for_identities),
    )
    if material_id is not None:
        statement = statement.where(IdentityMaterial.id == material_id)
    if resolved_source_identity_id is not None:
        statement = statement.where(
            IdentityMaterial.identity_id == resolved_source_identity_id,
        )
    if material_type:
        statement = statement.where(IdentityMaterial.material_type == material_type.strip().lower())
    materials = list(
        (
            await session.scalars(
                statement.order_by(IdentityMaterial.id.asc()).offset(cursor).limit(limit + 1),
            )
        ).unique(),
    )
    page, next_cursor, has_more = _slice_page(materials, cursor=cursor, limit=limit)
    default_context_identity_id = (
        target_identity_id if target_identity_id is not None else identity_id
    )
    response = AgentPage(
        items=[
            _serialize_material(
                material,
                include_text=False,
                target_identity_id=default_context_identity_id,
                default_for_identity_ids=[
                    identity.id for identity in material.default_for_identities
                ],
            )
            for material in page
        ],
        next_cursor=next_cursor,
        has_more=has_more,
    )
    return _project_agent_collection_response(response, fields)


@router.get("/materials/{material_id}", response_model=AgentMaterialRead)
async def read_agent_material(
    material_id: int,
    include_text: bool = Query(default=False),
    target_identity_id: int | None = Query(default=None, ge=1),
    session: AsyncSession = Depends(get_async_session),
) -> AgentMaterialRead:
    material = await session.scalar(
        select(IdentityMaterial)
        .options(
            selectinload(IdentityMaterial.source_identity),
            selectinload(IdentityMaterial.default_for_identities),
        )
        .where(IdentityMaterial.id == material_id),
    )
    if material is None:
        raise HTTPException(status_code=404, detail="未找到材料")
    if (
        target_identity_id is not None
        and await session.get(IdentityProfile, target_identity_id) is None
    ):
        raise HTTPException(status_code=404, detail="未找到身份配置")
    return _serialize_material(
        material,
        include_text=include_text,
        target_identity_id=(
            target_identity_id
            if target_identity_id is not None
            else material.identity_id
        ),
        default_for_identity_ids=[
            identity.id for identity in material.default_for_identities
        ],
    )


@router.get("/materials/{material_id}/download")
async def download_agent_material(
    material_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> FileResponse:
    material = await session.scalar(
        select(IdentityMaterial)
        .where(IdentityMaterial.id == material_id),
    )
    if material is None:
        raise HTTPException(status_code=404, detail="未找到材料")
    file_path = Path(material.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="材料文件不存在")
    return FileResponse(
        file_path,
        media_type=material.mime_type,
        filename=build_material_download_name(material),
    )


@router.post(
    "/materials",
    response_model=AgentMaterialRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_agent_material(
    identity_id: int | None = Form(default=None, ge=1),
    file: UploadFile = File(...),
    material_type: str = Form(default="other"),
    display_name: str | None = Form(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentMaterialRead:
    content = await file.read()
    await file.seek(0)
    request_data = {
        "identity_id": identity_id,
        "filename": file.filename or "upload.bin",
        "content_type": file.content_type,
        "size_bytes": len(content),
        "sha256": sha256(content).hexdigest(),
        "material_type": material_type,
        "display_name": display_name,
    }
    try:
        return await execute_agent_mutation(
            session,
            command="materials.upload",
            request_data=request_data,
            idempotency_key=idempotency_key,
            response_type=AgentMaterialRead,
            mutation=lambda: _upload_agent_material(
                session,
                identity_id,
                file,
                material_type,
                display_name,
            ),
        )
    except MaterialMutationError as exc:
        raise _agent_material_error(exc) from exc


@router.post("/materials/{material_id}/set-primary", response_model=AgentMaterialRead)
async def set_agent_primary_material(
    material_id: int,
    identity_id: int | None = Query(default=None, ge=1),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentMaterialRead:
    request_data = {"material_id": material_id}
    if identity_id is not None:
        request_data["identity_id"] = identity_id
    try:
        return await execute_agent_mutation(
            session,
            command="materials.set-primary",
            request_data=request_data,
            idempotency_key=idempotency_key,
            response_type=AgentMaterialRead,
            mutation=lambda: _set_agent_primary_material(
                session,
                material_id,
                identity_id,
            ),
        )
    except MaterialMutationError as exc:
        raise _agent_material_error(exc) from exc


@router.post(
    "/materials/{material_id}/prepare-delete",
    response_model=AgentChangePlanRead,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_agent_material_delete(
    material_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentChangePlanRead:
    return await create_material_delete_change_plan(
        get_session_factory(),
        material_id,
        idempotency_key=idempotency_key,
    )


@router.get("/identities", response_model=AgentPage[AgentIdentityRead])
async def list_agent_identities(
    identity_id: int | None = Query(default=None, ge=1),
    is_default: bool | None = Query(default=None),
    smtp_configured: bool | None = Query(default=None),
    imap_configured: bool | None = Query(default=None),
    fields: str | None = Query(default=None, max_length=4_000),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentIdentityRead] | Response:
    statement = select(IdentityProfile)
    if identity_id is not None:
        statement = statement.where(IdentityProfile.id == identity_id)
    if is_default is not None:
        statement = statement.where(IdentityProfile.is_default.is_(is_default))
    smtp_predicate = (IdentityProfile.smtp_host != "")
    smtp_predicate = smtp_predicate & (IdentityProfile.smtp_username != "")
    smtp_predicate = smtp_predicate & (IdentityProfile.smtp_password != "")
    if smtp_configured is not None:
        statement = statement.where(smtp_predicate if smtp_configured else ~smtp_predicate)
    if imap_configured is not None:
        predicate = func.coalesce(
            func.trim(IdentityProfile.imap_host),
            "",
        ) != ""
        predicate = predicate & (IdentityProfile.imap_port > 0)
        predicate = predicate & (
            func.coalesce(func.trim(IdentityProfile.imap_username), "") != ""
        )
        predicate = predicate & (func.coalesce(IdentityProfile.imap_password, "") != "")
        statement = statement.where(predicate if imap_configured else ~predicate)
    identities = list(
        await session.scalars(
            statement.order_by(IdentityProfile.is_default.desc(), IdentityProfile.id.asc())
            .offset(cursor)
            .limit(limit + 1),
        ),
    )
    page, next_cursor, has_more = _slice_page(identities, cursor=cursor, limit=limit)
    response = AgentPage(
        items=[_serialize_identity(identity) for identity in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )
    return _project_agent_collection_response(response, fields)


@router.get("/identities/{identity_id}", response_model=AgentIdentityRead)
async def read_agent_identity(
    identity_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> AgentIdentityRead:
    identity = await session.get(IdentityProfile, identity_id)
    if identity is None:
        raise HTTPException(status_code=404, detail="未找到身份配置")
    return _serialize_identity(identity)


@router.put(
    "/identities/{identity_id}/settings",
    response_model=AgentIdentityRead,
)
async def update_agent_identity_settings(
    identity_id: int,
    payload: AgentIdentitySettingsUpdateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentIdentityRead:
    request_data = {
        "identity_id": identity_id,
        "if_revision": if_revision,
        **payload.model_dump(mode="json", exclude_unset=True),
    }
    try:
        return await execute_agent_mutation(
            session,
            command="identities.update-settings",
            request_data=request_data,
            idempotency_key=idempotency_key,
            response_type=AgentIdentityRead,
            mutation=lambda: _update_agent_identity_settings_with_revision(
                session,
                identity_id,
                payload,
                if_revision=if_revision,
            ),
        )
    except ValueError as exc:
        raise _agent_identity_error(exc) from exc


@router.post("/identities/{identity_id}/default", response_model=AgentIdentityRead)
async def set_agent_default_identity(
    identity_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentIdentityRead:
    try:
        return await execute_agent_mutation(
            session,
            command="identities.set-default",
            request_data={"identity_id": identity_id, "if_revision": if_revision},
            idempotency_key=idempotency_key,
            response_type=AgentIdentityRead,
            mutation=lambda: _set_agent_default_identity_with_revision(
                session,
                identity_id,
                if_revision=if_revision,
            ),
        )
    except ValueError as exc:
        raise _agent_identity_error(exc) from exc


@router.post(
    "/identities/{identity_id}/default-template",
    response_model=AgentIdentityRead,
)
async def set_agent_identity_default_template(
    identity_id: int,
    payload: IdentityDefaultOutreachTemplateUpdate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentIdentityRead:
    try:
        return await execute_agent_mutation(
            session,
            command="identities.set-default-template",
            request_data={
                "identity_id": identity_id,
                "if_revision": if_revision,
                **payload.model_dump(mode="json"),
            },
            idempotency_key=idempotency_key,
            response_type=AgentIdentityRead,
            mutation=lambda: _set_agent_identity_default_template_with_revision(
                session,
                identity_id,
                payload,
                if_revision=if_revision,
            ),
        )
    except ValueError as exc:
        raise _agent_identity_error(exc) from exc


@router.post("/identities/{identity_id}/smtp-test", response_model=ConnectionTestResult)
async def test_agent_identity_smtp(
    identity_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> ConnectionTestResult:
    try:
        async def mutation() -> ConnectionTestResult:
            async with get_session_factory()() as mutation_session:
                result = await _test_agent_identity_smtp(mutation_session, identity_id)
                await mutation_session.commit()
                return result

        return await execute_agent_factory_mutation(
            get_session_factory(),
            command="identities.test-smtp",
            request_data={"identity_id": identity_id},
            idempotency_key=idempotency_key,
            response_type=ConnectionTestResult,
            mutation=mutation,
            external_execution=True,
        )
    except ValueError as exc:
        raise _agent_identity_error(exc) from exc


@router.post("/identities/{identity_id}/imap-test", response_model=ConnectionTestResult)
async def test_agent_identity_imap(
    identity_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> ConnectionTestResult:
    try:
        async def mutation() -> ConnectionTestResult:
            async with get_session_factory()() as mutation_session:
                result = await _test_agent_identity_imap(mutation_session, identity_id)
                await mutation_session.commit()
                return result

        return await execute_agent_factory_mutation(
            get_session_factory(),
            command="identities.test-imap",
            request_data={"identity_id": identity_id},
            idempotency_key=idempotency_key,
            response_type=ConnectionTestResult,
            mutation=mutation,
            external_execution=True,
        )
    except ValueError as exc:
        raise _agent_identity_error(exc) from exc


@router.get("/llm-profiles", response_model=AgentPage[AgentLLMProfileRead])
async def list_agent_llm_profiles(
    profile_id: int | None = Query(default=None, ge=1),
    provider: str | None = Query(default=None, max_length=100),
    model_name: str | None = Query(default=None, max_length=200),
    is_default: bool | None = Query(default=None),
    fields: str | None = Query(default=None, max_length=4_000),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentLLMProfileRead] | Response:
    statement = select(LLMProfile)
    if profile_id is not None:
        statement = statement.where(LLMProfile.id == profile_id)
    if provider is not None:
        statement = statement.where(LLMProfile.provider == provider)
    if model_name is not None:
        statement = statement.where(LLMProfile.model_name == model_name)
    if is_default is not None:
        statement = statement.where(LLMProfile.is_default.is_(is_default))
    profiles = list(
        await session.scalars(
            statement.order_by(LLMProfile.is_default.desc(), LLMProfile.id.asc())
            .offset(cursor)
            .limit(limit + 1),
        ),
    )
    page, next_cursor, has_more = _slice_page(profiles, cursor=cursor, limit=limit)
    response = AgentPage(
        items=[_serialize_llm_profile(profile) for profile in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )
    return _project_agent_collection_response(response, fields)


@router.get("/llm-profiles/{profile_id}", response_model=AgentLLMProfileRead)
async def read_agent_llm_profile(
    profile_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> AgentLLMProfileRead:
    profile = await session.get(LLMProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="未找到 LLM 配置")
    return _serialize_llm_profile(profile)


@router.put(
    "/llm-profiles/{profile_id}/settings",
    response_model=AgentLLMProfileRead,
)
async def update_agent_llm_profile_settings(
    profile_id: int,
    payload: AgentLLMProfileSettingsUpdateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentLLMProfileRead:
    request_data = {
        "profile_id": profile_id,
        "if_revision": if_revision,
        **payload.model_dump(mode="json", exclude_unset=True),
    }
    try:
        return await execute_agent_mutation(
            session,
            command="llm-profiles.update-settings",
            request_data=request_data,
            idempotency_key=idempotency_key,
            response_type=AgentLLMProfileRead,
            mutation=lambda: _update_agent_llm_profile_settings_with_revision(
                session,
                profile_id,
                payload,
                if_revision=if_revision,
            ),
        )
    except ValueError as exc:
        raise _agent_llm_profile_error(exc) from exc


@router.post(
    "/llm-profiles/{profile_id}/default",
    response_model=AgentLLMProfileRead,
)
async def set_agent_default_llm_profile(
    profile_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentLLMProfileRead:
    try:
        return await execute_agent_mutation(
            session,
            command="llm-profiles.set-default",
            request_data={"profile_id": profile_id, "if_revision": if_revision},
            idempotency_key=idempotency_key,
            response_type=AgentLLMProfileRead,
            mutation=lambda: _set_agent_default_llm_profile_with_revision(
                session,
                profile_id,
                if_revision=if_revision,
            ),
        )
    except ValueError as exc:
        raise _agent_llm_profile_error(exc) from exc


@router.get(
    "/llm-profiles/{profile_id}/models",
    response_model=AgentLLMProfileModelsRead,
)
async def fetch_agent_llm_profile_models(
    profile_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> AgentLLMProfileModelsRead:
    try:
        profile = await _get_agent_llm_profile_or_raise(session, profile_id)
    except ValueError as exc:
        raise _agent_llm_profile_error(exc) from exc
    result = await fetch_llm_profile_models(profile)
    await _record_agent_llm_profile_event(
        session,
        profile,
        "agent_cli.llm_profile.models_fetched",
        level="info" if result.ok else "warning",
        metadata={
            "ok": result.ok,
            "result": "ok" if result.ok else "failed",
            "status_code": result.status_code,
            "duration_ms": result.duration_ms,
            "endpoint_kind": result.endpoint_kind,
            "model_count": len(result.models),
            "selected_model_available": result.selected_model_available,
        },
    )
    await session.commit()
    return _serialize_agent_llm_profile_models(profile.id, result)


@router.post(
    "/llm-profiles/{profile_id}/test",
    response_model=AgentLLMProfileTestRead,
)
async def test_agent_llm_profile(
    profile_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentLLMProfileTestRead:
    try:
        async def mutation() -> AgentLLMProfileTestRead:
            async with get_session_factory()() as mutation_session:
                result = await _test_agent_llm_profile(mutation_session, profile_id)
                await mutation_session.commit()
                return result

        return await execute_agent_factory_mutation(
            get_session_factory(),
            command="llm-profiles.test",
            request_data={"profile_id": profile_id},
            idempotency_key=idempotency_key,
            response_type=AgentLLMProfileTestRead,
            mutation=mutation,
            external_execution=True,
        )
    except ValueError as exc:
        raise _agent_llm_profile_error(exc) from exc


@router.get(
    "/communication-groups",
    response_model=AgentPage[IdentityCommunicationGroupRead],
)
async def list_agent_communication_groups(
    group_id: int | None = Query(default=None, ge=1),
    match_source_identity_id: int | None = Query(default=None, ge=1),
    fields: str | None = Query(default=None, max_length=4_000),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[IdentityCommunicationGroupRead] | Response:
    groups = await list_communication_group_records(session)
    if group_id is not None:
        groups = [group for group in groups if group.id == group_id]
    if match_source_identity_id is not None:
        groups = [
            group
            for group in groups
            if group.match_source_identity_id == match_source_identity_id
        ]
    page, next_cursor, has_more = _slice_page(groups[cursor:], cursor=cursor, limit=limit)
    response = AgentPage(items=list(page), next_cursor=next_cursor, has_more=has_more)
    return _project_agent_collection_response(response, fields)


@router.get(
    "/communication-groups/{group_id}",
    response_model=IdentityCommunicationGroupRead,
)
async def read_agent_communication_group(
    group_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> IdentityCommunicationGroupRead:
    try:
        return await get_communication_group_record(session, group_id)
    except CommunicationGroupMutationError as exc:
        raise _agent_communication_group_error(exc) from exc


@router.post(
    "/communication-groups",
    response_model=IdentityCommunicationGroupRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_communication_group(
    payload: IdentityCommunicationGroupWrite,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> IdentityCommunicationGroupRead:
    try:
        return await execute_agent_mutation(
            session,
            command="communication-groups.create",
            request_data=payload.model_dump(mode="json"),
            idempotency_key=idempotency_key,
            response_type=IdentityCommunicationGroupRead,
            mutation=lambda: _create_agent_communication_group(session, payload),
        )
    except CommunicationGroupMutationError as exc:
        raise _agent_communication_group_error(exc) from exc


@router.put(
    "/communication-groups/{group_id}",
    response_model=IdentityCommunicationGroupRead,
)
async def update_agent_communication_group(
    group_id: int,
    payload: IdentityCommunicationGroupWrite,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
    session: AsyncSession = Depends(get_async_session),
) -> IdentityCommunicationGroupRead:
    try:
        return await execute_agent_mutation(
            session,
            command="communication-groups.update",
            request_data={
                "group_id": group_id,
                "if_revision": if_revision,
                **payload.model_dump(mode="json"),
            },
            idempotency_key=idempotency_key,
            response_type=IdentityCommunicationGroupRead,
            mutation=lambda: _update_agent_communication_group_with_revision(
                session,
                group_id,
                payload,
                if_revision=if_revision,
            ),
        )
    except CommunicationGroupMutationError as exc:
        raise _agent_communication_group_error(exc) from exc


@router.post(
    "/communication-groups/{group_id}/delete",
    response_model=AgentCommunicationGroupDeleteRead,
)
async def delete_agent_communication_group(
    group_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentCommunicationGroupDeleteRead:
    try:
        return await execute_agent_mutation(
            session,
            command="communication-groups.delete",
            request_data={"group_id": group_id},
            idempotency_key=idempotency_key,
            response_type=AgentCommunicationGroupDeleteRead,
            mutation=lambda: _delete_agent_communication_group(session, group_id),
        )
    except CommunicationGroupMutationError as exc:
        raise _agent_communication_group_error(exc) from exc


@router.get("/matching/jobs", response_model=AgentPage[AgentMatchAnalysisJobRead])
async def list_agent_match_analysis_jobs(
    identity_id: int | None = Query(default=None, ge=1),
    llm_profile_id: int | None = Query(default=None, ge=1),
    status_filter: str | None = Query(default=None, alias="status"),
    view: Literal["current", "trash"] = Query(default="current"),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentMatchAnalysisJobRead]:
    statement = select(MatchAnalysisJob)
    if identity_id is not None:
        statement = statement.where(MatchAnalysisJob.identity_id == identity_id)
    if llm_profile_id is not None:
        statement = statement.where(MatchAnalysisJob.llm_profile_id == llm_profile_id)
    if status_filter is not None:
        statement = statement.where(MatchAnalysisJob.status == status_filter)
    if view == "trash":
        statement = statement.where(MatchAnalysisJob.deleted_at.is_not(None))
    else:
        statement = statement.where(MatchAnalysisJob.deleted_at.is_(None))
    jobs = list(
        await session.scalars(
            statement.order_by(MatchAnalysisJob.created_at.desc(), MatchAnalysisJob.id.desc())
            .offset(cursor)
            .limit(limit + 1),
        ),
    )
    page, next_cursor, has_more = _slice_page(jobs, cursor=cursor, limit=limit)
    return AgentPage(
        items=[_serialize_match_analysis_job(job) for job in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.post(
    "/matching/jobs",
    response_model=AgentMatchAnalysisJobRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_match_analysis_job(
    payload: AgentMatchAnalysisJobCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentMatchAnalysisJobRead:
    try:
        return await execute_agent_mutation(
            session,
            command="matching.jobs.create",
            request_data=payload.model_dump(mode="json"),
            idempotency_key=idempotency_key,
            response_type=AgentMatchAnalysisJobRead,
            mutation=lambda: _create_agent_match_analysis_job(session, payload),
        )
    except ValueError as exc:
        raise _agent_match_analysis_error(exc) from exc


@router.get("/matching/jobs/{job_id}", response_model=AgentMatchAnalysisJobRead)
async def read_agent_match_analysis_job(
    job_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> AgentMatchAnalysisJobRead:
    job = await session.get(MatchAnalysisJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="匹配分析任务不存在")
    return _serialize_match_analysis_job(job)


@router.get(
    "/matching/jobs/{job_id}/items",
    response_model=AgentPage[AgentMatchAnalysisJobItemRead],
)
async def list_agent_match_analysis_job_items(
    job_id: int,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentMatchAnalysisJobItemRead]:
    if await session.get(MatchAnalysisJob, job_id) is None:
        raise HTTPException(status_code=404, detail="匹配分析任务不存在")
    items = list(
        await session.scalars(
            select(MatchAnalysisJobItem)
            .options(
                selectinload(MatchAnalysisJobItem.professor)
                .load_only(
                    Professor.id,
                    Professor.name,
                    Professor.email,
                    Professor.title,
                    Professor.university,
                    Professor.school,
                )
                .lazyload(Professor.tags),
                selectinload(MatchAnalysisJobItem.email_task).load_only(
                    EmailTask.id,
                    EmailTask.match_score,
                ),
                selectinload(MatchAnalysisJobItem.match_analysis_run).load_only(
                    MatchAnalysisRun.id,
                    MatchAnalysisRun.match_score,
                ),
            )
            .where(MatchAnalysisJobItem.job_id == job_id)
            .order_by(MatchAnalysisJobItem.id.asc())
            .offset(cursor)
            .limit(limit + 1),
        ),
    )
    page, next_cursor, has_more = _slice_page(items, cursor=cursor, limit=limit)
    return AgentPage(
        items=[_serialize_match_analysis_job_item(item) for item in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.post(
    "/matching/jobs/{job_id}/cancel",
    response_model=AgentMatchAnalysisJobActionRead,
)
async def cancel_agent_match_analysis_job(
    job_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentMatchAnalysisJobActionRead:
    try:
        return await execute_agent_mutation(
            session,
            command="matching.jobs.cancel",
            request_data={"job_id": job_id},
            idempotency_key=idempotency_key,
            response_type=AgentMatchAnalysisJobActionRead,
            mutation=lambda: _cancel_agent_match_analysis_job(session, job_id),
        )
    except ValueError as exc:
        raise _agent_match_analysis_error(exc) from exc


@router.post(
    "/matching/jobs/{job_id}/retry-failed",
    response_model=AgentMatchAnalysisJobRead,
    status_code=status.HTTP_201_CREATED,
)
async def retry_agent_match_analysis_job(
    job_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentMatchAnalysisJobRead:
    try:
        return await execute_agent_mutation(
            session,
            command="matching.jobs.retry-failed",
            request_data={"job_id": job_id},
            idempotency_key=idempotency_key,
            response_type=AgentMatchAnalysisJobRead,
            mutation=lambda: _retry_agent_match_analysis_job(session, job_id),
        )
    except ValueError as exc:
        raise _agent_match_analysis_error(exc) from exc


@router.post(
    "/matching/jobs/{job_id}/delete",
    response_model=AgentMatchAnalysisJobActionRead,
)
async def delete_agent_match_analysis_job(
    job_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentMatchAnalysisJobActionRead:
    try:
        return await execute_agent_mutation(
            session,
            command="matching.jobs.delete",
            request_data={"job_id": job_id},
            idempotency_key=idempotency_key,
            response_type=AgentMatchAnalysisJobActionRead,
            mutation=lambda: _delete_agent_match_analysis_job(session, job_id),
        )
    except ValueError as exc:
        raise _agent_match_analysis_error(exc) from exc


@router.post(
    "/matching/jobs/{job_id}/restore",
    response_model=AgentMatchAnalysisJobActionRead,
)
async def restore_agent_match_analysis_job(
    job_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentMatchAnalysisJobActionRead:
    try:
        return await execute_agent_mutation(
            session,
            command="matching.jobs.restore",
            request_data={"job_id": job_id},
            idempotency_key=idempotency_key,
            response_type=AgentMatchAnalysisJobActionRead,
            mutation=lambda: _restore_agent_match_analysis_job(session, job_id),
        )
    except ValueError as exc:
        raise _agent_match_analysis_error(exc) from exc


@router.get(
    "/enrichment/jobs",
    response_model=AgentPage[ProfessorInformationEnrichmentJobRead],
)
async def list_agent_professor_information_enrichment_jobs(
    view: Literal["current", "trash"] = Query(default="current"),
    status_filter: str | None = Query(default=None, alias="status"),
    llm_profile_id: int | None = Query(default=None, ge=1),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[ProfessorInformationEnrichmentJobRead]:
    try:
        jobs = await list_professor_information_enrichment_jobs(
            session,
            view=view,
            status=status_filter,
            llm_profile_id=llm_profile_id,
            offset=cursor,
            limit=limit + 1,
        )
    except ValueError as exc:
        raise AgentApiError(
            status_code=422,
            code="INVALID_ENRICHMENT_JOB_VIEW",
            message=str(exc),
        ) from exc
    page, next_cursor, has_more = _slice_page(jobs, cursor=cursor, limit=limit)
    return AgentPage(items=list(page), next_cursor=next_cursor, has_more=has_more)


@router.post(
    "/enrichment/jobs",
    response_model=ProfessorInformationEnrichmentJobRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_professor_information_enrichment_job(
    payload: CreateProfessorInformationEnrichmentJobRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorInformationEnrichmentJobRead:
    try:
        return await execute_agent_mutation(
            session,
            command="enrichment.jobs.create",
            request_data=payload.model_dump(mode="json"),
            idempotency_key=idempotency_key,
            response_type=ProfessorInformationEnrichmentJobRead,
            mutation=lambda: _create_agent_professor_information_enrichment_job(session, payload),
        )
    except RuntimeError as exc:
        raise _agent_information_enrichment_error(exc, status_code=409) from exc
    except ValueError as exc:
        raise _agent_information_enrichment_error(exc) from exc


@router.get(
    "/enrichment/jobs/{job_id}",
    response_model=ProfessorInformationEnrichmentJobRead,
)
async def read_agent_professor_information_enrichment_job(
    job_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorInformationEnrichmentJobRead:
    job = await get_professor_information_enrichment_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="信息补全任务不存在")
    return job


@router.get(
    "/enrichment/jobs/{job_id}/items",
    response_model=AgentPage[ProfessorInformationEnrichmentItemRead],
)
async def list_agent_professor_information_enrichment_job_items(
    job_id: int,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[ProfessorInformationEnrichmentItemRead]:
    page = await list_professor_information_enrichment_items_page(
        session,
        job_id,
        cursor=cursor,
        limit=limit,
    )
    if page is None:
        raise HTTPException(status_code=404, detail="信息补全任务不存在")
    return AgentPage(
        items=page.items,
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )


@router.post(
    "/enrichment/jobs/{job_id}/cancel",
    response_model=ProfessorInformationEnrichmentJobActionRead,
)
async def cancel_agent_professor_information_enrichment_job(
    job_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorInformationEnrichmentJobActionRead:
    try:
        return await execute_agent_mutation(
            session,
            command="enrichment.jobs.cancel",
            request_data={"job_id": job_id},
            idempotency_key=idempotency_key,
            response_type=ProfessorInformationEnrichmentJobActionRead,
            mutation=lambda: _cancel_agent_professor_information_enrichment_job(session, job_id),
        )
    except ValueError as exc:
        raise _agent_information_enrichment_error(exc) from exc


@router.post(
    "/enrichment/jobs/{job_id}/retry-failed",
    response_model=ProfessorInformationEnrichmentJobRead,
    status_code=status.HTTP_201_CREATED,
)
async def retry_agent_professor_information_enrichment_job(
    job_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorInformationEnrichmentJobRead:
    try:
        return await execute_agent_mutation(
            session,
            command="enrichment.jobs.retry-failed",
            request_data={"job_id": job_id},
            idempotency_key=idempotency_key,
            response_type=ProfessorInformationEnrichmentJobRead,
            mutation=lambda: _retry_agent_professor_information_enrichment_job(session, job_id),
        )
    except ValueError as exc:
        raise _agent_information_enrichment_error(exc) from exc


@router.post(
    "/enrichment/jobs/{job_id}/delete",
    response_model=ProfessorInformationEnrichmentJobActionRead,
)
async def delete_agent_professor_information_enrichment_job(
    job_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorInformationEnrichmentJobActionRead:
    try:
        return await execute_agent_mutation(
            session,
            command="enrichment.jobs.delete",
            request_data={"job_id": job_id},
            idempotency_key=idempotency_key,
            response_type=ProfessorInformationEnrichmentJobActionRead,
            mutation=lambda: _delete_agent_professor_information_enrichment_job(session, job_id),
        )
    except ValueError as exc:
        raise _agent_information_enrichment_error(exc) from exc


@router.post(
    "/enrichment/jobs/{job_id}/restore",
    response_model=ProfessorInformationEnrichmentJobActionRead,
)
async def restore_agent_professor_information_enrichment_job(
    job_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> ProfessorInformationEnrichmentJobActionRead:
    try:
        return await execute_agent_mutation(
            session,
            command="enrichment.jobs.restore",
            request_data={"job_id": job_id},
            idempotency_key=idempotency_key,
            response_type=ProfessorInformationEnrichmentJobActionRead,
            mutation=lambda: _restore_agent_professor_information_enrichment_job(session, job_id),
        )
    except ValueError as exc:
        raise _agent_information_enrichment_error(exc) from exc


@router.get("/crawler/jobs", response_model=AgentPage[CrawlJobSummaryRead])
async def list_agent_faculty_crawl_jobs(
    view: Literal["current", "trash"] = Query(default="current"),
    status_filter: str | None = Query(default=None, alias="status"),
    llm_profile_id: int | None = Query(default=None, ge=1),
    requested_model_name: str | None = Query(default=None),
    effective_model_name: str | None = Query(default=None),
    university: str | None = Query(default=None),
    school: str | None = Query(default=None),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[CrawlJobSummaryRead]:
    try:
        jobs = await list_faculty_crawl_job_records(
            session,
            view=view,
            offset=cursor,
            limit=limit + 1,
            status=status_filter,
            llm_profile_id=llm_profile_id,
            requested_model_name=requested_model_name,
            effective_model_name=effective_model_name,
            university=university,
            school=school,
        )
    except CrawlJobRecordError as exc:
        raise _agent_crawl_job_error(exc) from exc
    page, next_cursor, has_more = _slice_page(jobs, cursor=cursor, limit=limit)
    return AgentPage(items=list(page), next_cursor=next_cursor, has_more=has_more)


@router.post(
    "/crawler/jobs",
    response_model=CrawlJobSummaryRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_faculty_crawl_job(
    payload: CrawlJobCreatePayload,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJobSummaryRead:
    try:
        return await execute_agent_mutation(
            session,
            command="crawler.jobs.create",
            request_data=payload.model_dump(mode="json"),
            idempotency_key=idempotency_key,
            response_type=CrawlJobSummaryRead,
            mutation=lambda: _create_agent_faculty_crawl_job(session, payload),
        )
    except CrawlJobRecordError as exc:
        raise _agent_crawl_job_error(exc) from exc


@router.post(
    "/crawler/jobs/create-many",
    response_model=AgentCrawlJobBatchCreateRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_many_agent_faculty_crawl_jobs(
    payload: AgentBatchItemsRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentCrawlJobBatchCreateRead:
    return await execute_agent_mutation(
        session,
        command="crawler.jobs.create-many",
        request_data=payload.model_dump(mode="json"),
        idempotency_key=idempotency_key,
        response_type=AgentCrawlJobBatchCreateRead,
        mutation=lambda: _create_many_agent_faculty_crawl_jobs(session, payload.items),
    )


@router.get("/crawler/jobs/{job_id}", response_model=CrawlJobSummaryRead)
async def read_agent_faculty_crawl_job(
    job_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJobSummaryRead:
    try:
        return await get_faculty_crawl_job_summary(session, job_id)
    except CrawlJobRecordError as exc:
        raise _agent_crawl_job_error(exc) from exc


@router.get(
    "/crawler/jobs/{job_id}/events",
    response_model=AgentPage[AgentCrawlJobEventRead],
)
async def list_agent_faculty_crawl_job_events(
    job_id: int,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentCrawlJobEventRead]:
    try:
        job = await get_faculty_crawl_job_or_raise(session, job_id)
    except CrawlJobRecordError as exc:
        raise _agent_crawl_job_error(exc) from exc
    pages = list(
        await session.scalars(
            select(CrawlPage)
            .where(CrawlPage.job_id == job_id)
            .order_by(CrawlPage.created_at.asc(), CrawlPage.id.asc()),
        ),
    )
    candidates = list(
        await session.scalars(
            select(CrawlCandidate)
            .where(
                CrawlCandidate.job_id == job_id,
                canonical_candidate_clause(),
            )
            .order_by(CrawlCandidate.created_at.asc(), CrawlCandidate.id.asc()),
        ),
    )
    events = build_crawl_job_events(job, pages=pages, candidates=candidates)
    page, next_cursor, has_more = _slice_page(events, cursor=cursor, limit=limit)
    return AgentPage(
        items=[AgentCrawlJobEventRead.model_validate(item) for item in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get("/crawler/jobs/{job_id}/pages", response_model=AgentPage[AgentCrawlPageRead])
async def list_agent_faculty_crawl_pages(
    job_id: int,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentCrawlPageRead]:
    try:
        pages = await list_faculty_crawl_pages(
            session,
            job_id,
            offset=cursor,
            limit=limit + 1,
        )
    except CrawlJobRecordError as exc:
        raise _agent_crawl_job_error(exc) from exc
    page, next_cursor, has_more = _slice_page(pages, cursor=cursor, limit=limit)
    return AgentPage(
        items=[AgentCrawlPageRead.model_validate(item) for item in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get(
    "/crawler/jobs/{job_id}/candidates",
    response_model=AgentPage[AgentCrawlCandidateRead],
)
async def list_agent_faculty_crawl_candidates(
    job_id: int,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentCrawlCandidateRead]:
    try:
        candidates = await list_faculty_crawl_candidates(
            session,
            job_id,
            offset=cursor,
            limit=limit + 1,
        )
    except CrawlJobRecordError as exc:
        raise _agent_crawl_job_error(exc) from exc
    page, next_cursor, has_more = _slice_page(candidates, cursor=cursor, limit=limit)
    return AgentPage(
        items=[_serialize_crawl_candidate(item) for item in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.post(
    "/crawler/jobs/{job_id}/prepare-approve",
    response_model=AgentChangePlanRead,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_agent_crawl_candidate_approval(
    job_id: int,
    payload: AgentCrawlJobApproveRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentChangePlanRead:
    return await create_crawl_candidate_approval_change_plan(
        get_session_factory(),
        job_id,
        payload.resolved_selection(),
        idempotency_key=idempotency_key,
    )


@router.post(
    "/crawler/jobs/{job_id}/prepare-retry",
    response_model=AgentChangePlanRead,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_agent_crawl_job_retry(
    job_id: int,
    payload: AgentCrawlJobRetryRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentChangePlanRead:
    return await create_crawl_job_retry_change_plan(
        get_session_factory(),
        job_id,
        CrawlJobRetryPayload.model_validate(payload.model_dump()),
        idempotency_key=idempotency_key,
    )


@router.post(
    "/crawler/jobs/{job_id}/enrich",
    response_model=CrawlJobEnrichResult,
    status_code=status.HTTP_201_CREATED,
)
async def enrich_agent_crawl_candidates(
    job_id: int,
    payload: AgentCrawlJobEnrichRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJobEnrichResult:
    try:
        return await execute_agent_mutation(
            session,
            command="crawler.jobs.enrich",
            request_data={
                "job_id": job_id,
                **payload.model_dump(mode="json", exclude_none=True),
            },
            idempotency_key=idempotency_key,
            response_type=CrawlJobEnrichResult,
            mutation=lambda: enqueue_faculty_crawl_candidate_enrichment_records(
                session,
                job_id,
                payload.resolved_selection(),
                llm_profile_id=payload.llm_profile_id,
                event_name="agent_cli.crawl_candidate_enrichment.queued",
                actor="agent_cli",
            ),
        )
    except CrawlJobRecordError as exc:
        raise _agent_crawl_job_error(exc) from exc


@router.post(
    "/crawler/jobs/enrich-many",
    response_model=AgentCrawlJobBatchEnrichRead,
    status_code=status.HTTP_201_CREATED,
)
async def enrich_many_agent_crawl_candidates(
    payload: AgentBatchItemsRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentCrawlJobBatchEnrichRead:
    return await execute_agent_mutation(
        session,
        command="crawler.jobs.enrich-many",
        request_data=payload.model_dump(mode="json"),
        idempotency_key=idempotency_key,
        response_type=AgentCrawlJobBatchEnrichRead,
        mutation=lambda: _enrich_many_agent_crawl_candidates(session, payload.items),
    )


@router.patch("/crawler/candidates/{candidate_id}", response_model=AgentCrawlCandidateRead)
async def update_agent_faculty_crawl_candidate(
    candidate_id: int,
    payload: AgentCrawlCandidateUpdateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentCrawlCandidateRead:
    try:
        return await execute_agent_mutation(
            session,
            command="crawler.candidates.update",
            request_data={
                "candidate_id": candidate_id,
                "if_revision": if_revision,
                **payload.model_dump(mode="json", exclude_unset=True),
            },
            idempotency_key=idempotency_key,
            response_type=AgentCrawlCandidateRead,
            mutation=lambda: _update_agent_faculty_crawl_candidate(
                session,
                candidate_id,
                payload,
                if_revision=if_revision,
            ),
        )
    except CrawlJobRecordError as exc:
        raise _agent_crawl_job_error(exc) from exc


@router.post("/crawler/jobs/{job_id}/pause", response_model=CrawlJobSummaryRead)
async def pause_agent_faculty_crawl_job(
    job_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJobSummaryRead:
    try:
        return await execute_agent_mutation(
            session,
            command="crawler.jobs.pause",
            request_data={"job_id": job_id},
            idempotency_key=idempotency_key,
            response_type=CrawlJobSummaryRead,
            mutation=lambda: _pause_agent_faculty_crawl_job(session, job_id),
        )
    except CrawlJobRecordError as exc:
        raise _agent_crawl_job_error(exc) from exc


@router.post("/crawler/jobs/{job_id}/resume", response_model=CrawlJobSummaryRead)
async def resume_agent_faculty_crawl_job(
    job_id: int,
    payload: CrawlJobResumePayload | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJobSummaryRead:
    try:
        return await execute_agent_mutation(
            session,
            command="crawler.jobs.resume",
            request_data={
                "job_id": job_id,
                "payload": payload.model_dump(mode="json") if payload is not None else None,
            },
            idempotency_key=idempotency_key,
            response_type=CrawlJobSummaryRead,
            mutation=lambda: _resume_agent_faculty_crawl_job(session, job_id, payload),
        )
    except CrawlJobRecordError as exc:
        raise _agent_crawl_job_error(exc) from exc


@router.post("/crawler/jobs/{job_id}/cancel", response_model=CrawlJobSummaryRead)
async def cancel_agent_faculty_crawl_job(
    job_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJobSummaryRead:
    try:
        return await execute_agent_mutation(
            session,
            command="crawler.jobs.cancel",
            request_data={"job_id": job_id},
            idempotency_key=idempotency_key,
            response_type=CrawlJobSummaryRead,
            mutation=lambda: _cancel_agent_faculty_crawl_job(session, job_id),
        )
    except CrawlJobRecordError as exc:
        raise _agent_crawl_job_error(exc) from exc


@router.post("/crawler/jobs/{job_id}/resume-review", response_model=CrawlJobSummaryRead)
async def resume_agent_faculty_crawl_job_review(
    job_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJobSummaryRead:
    try:
        return await execute_agent_mutation(
            session,
            command="crawler.jobs.resume-review",
            request_data={"job_id": job_id},
            idempotency_key=idempotency_key,
            response_type=CrawlJobSummaryRead,
            mutation=lambda: _resume_agent_faculty_crawl_job_review(session, job_id),
        )
    except CrawlJobRecordError as exc:
        raise _agent_crawl_job_error(exc) from exc


@router.post("/crawler/jobs/{job_id}/delete", response_model=CrawlJobSummaryRead)
async def delete_agent_faculty_crawl_job(
    job_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJobSummaryRead:
    try:
        return await execute_agent_mutation(
            session,
            command="crawler.jobs.delete",
            request_data={"job_id": job_id},
            idempotency_key=idempotency_key,
            response_type=CrawlJobSummaryRead,
            mutation=lambda: _delete_agent_faculty_crawl_job(session, job_id),
        )
    except CrawlJobRecordError as exc:
        raise _agent_crawl_job_error(exc) from exc


@router.post("/crawler/jobs/{job_id}/restore", response_model=CrawlJobSummaryRead)
async def restore_agent_faculty_crawl_job(
    job_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> CrawlJobSummaryRead:
    try:
        return await execute_agent_mutation(
            session,
            command="crawler.jobs.restore",
            request_data={"job_id": job_id},
            idempotency_key=idempotency_key,
            response_type=CrawlJobSummaryRead,
            mutation=lambda: _restore_agent_faculty_crawl_job(session, job_id),
        )
    except CrawlJobRecordError as exc:
        raise _agent_crawl_job_error(exc) from exc


@router.get("/settings", response_model=RuntimeSettingsRead)
async def read_agent_runtime_settings(
    session: AsyncSession = Depends(get_async_session),
) -> RuntimeSettingsRead:
    settings = await get_runtime_settings(session)
    await session.commit()
    return serialize_runtime_settings(settings)


@router.patch("/settings", response_model=RuntimeSettingsRead)
async def update_agent_runtime_settings(
    payload: RuntimeSettingsUpdate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
    session: AsyncSession = Depends(get_async_session),
) -> RuntimeSettingsRead:
    return await execute_agent_mutation(
        session,
        command="settings.update",
        request_data={
            "if_revision": if_revision,
            **payload.model_dump(mode="json"),
        },
        idempotency_key=idempotency_key,
        response_type=RuntimeSettingsRead,
        mutation=lambda: _update_agent_runtime_settings_with_revision(
            session,
            payload,
            if_revision=if_revision,
        ),
    )


@router.get(
    "/diagnostics/operation-logs",
    response_model=OperationLogListResponse,
)
async def list_agent_operation_logs(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    level: str | None = Query(default=None),
    category: str | None = Query(default=None),
    event_name: str | None = Query(default=None),
    request_id: str | None = Query(default=None),
    entity_type: str | None = Query(default=None),
    entity_id: str | None = Query(default=None),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    fields: str | None = Query(default=None, max_length=4_000),
    session: AsyncSession = Depends(get_async_session),
) -> OperationLogListResponse | Response:
    filters = _agent_operation_log_filters(
        level=level,
        category=category,
        event_name=event_name,
        request_id=request_id,
        entity_type=entity_type,
        entity_id=entity_id,
        start_at=start_at,
        end_at=end_at,
    )
    total = int(
        await session.scalar(
            select(func.count()).select_from(OperationLog).where(*filters),
        )
        or 0,
    )
    logs = list(
        (
            await session.scalars(
                select(OperationLog)
                .where(*filters)
                .order_by(OperationLog.created_at.desc(), OperationLog.id.desc())
                .limit(limit)
                .offset(offset),
            )
        ).all(),
    )
    response = OperationLogListResponse(
        items=[_serialize_agent_operation_log(log) for log in logs],
        total=total,
        limit=limit,
        offset=offset,
    )
    return _project_agent_collection_response(response, fields)


@router.get("/diagnostics/export", response_model=OperationLogExportResponse)
async def export_agent_operation_logs(
    level: str | None = Query(default=None),
    category: str | None = Query(default=None),
    event_name: str | None = Query(default=None),
    request_id: str | None = Query(default=None),
    entity_type: str | None = Query(default=None),
    entity_id: str | None = Query(default=None),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_async_session),
) -> OperationLogExportResponse:
    filters = _agent_operation_log_filters(
        level=level,
        category=category,
        event_name=event_name,
        request_id=request_id,
        entity_type=entity_type,
        entity_id=entity_id,
        start_at=start_at,
        end_at=end_at,
    )
    total = int(
        await session.scalar(
            select(func.count()).select_from(OperationLog).where(*filters),
        )
        or 0,
    )
    logs = list(
        (
            await session.scalars(
                select(OperationLog)
                .where(*filters)
                .order_by(OperationLog.created_at.desc(), OperationLog.id.desc())
                .limit(500),
            )
        ).all(),
    )
    return OperationLogExportResponse(
        exported_at=utc_now(),
        total=total,
        items=[_serialize_agent_operation_log(log) for log in logs],
        filters={
            "level": level,
            "category": category,
            "event_name": event_name,
            "request_id": request_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "start_at": start_at.isoformat() if start_at else None,
            "end_at": end_at.isoformat() if end_at else None,
        },
        startup_logs=_read_agent_startup_logs(),
    )


@router.get("/diagnostics/crawler-debug/{job_id}/export")
async def export_agent_crawler_debug_log(job_id: int) -> Response:
    debug_file = crawler_debug_file_path(job_id)
    if not debug_file.is_file():
        raise AgentApiError(
            status_code=404,
            code="CRAWLER_DEBUG_LOG_NOT_FOUND",
            message="未找到该抓取任务的调试日志。",
        )
    try:
        content = sanitize_diagnostic_text(
            debug_file.read_text(encoding="utf-8", errors="replace"),
        )
    except OSError as exc:
        raise AgentApiError(
            status_code=500,
            code="CRAWLER_DEBUG_LOG_READ_FAILED",
            message="无法读取该抓取任务的调试日志。",
        ) from exc
    return Response(
        content=content,
        media_type="application/jsonl; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{debug_file.name}"',
        },
    )


@router.get("/dashboard/overview", response_model=DashboardOverviewRead)
async def read_agent_dashboard_overview(
    identity_id: int = Query(..., ge=1),
    llm_profile_id: int | None = Query(default=None, ge=1),
    university: str | None = Query(default=None),
    school: str | None = Query(default=None),
    email_university: str | None = Query(default=None),
    email_school: str | None = Query(default=None),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    session: AsyncSession = Depends(get_async_session),
) -> DashboardOverviewRead:
    try:
        return await build_dashboard_overview(
            session,
            identity_id=identity_id,
            llm_profile_id=llm_profile_id,
            university=university,
            school=school,
            email_university=email_university,
            email_school=email_school,
            start_date=start_date,
            end_date=end_date,
        )
    except ValueError as exc:
        raise AgentApiError(
            status_code=422,
            code="INVALID_DASHBOARD_FILTER",
            message=str(exc),
        ) from exc


@router.get("/usage/records", response_model=TokenUsageRecordListRead)
async def list_agent_token_usage_records(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=100),
    feature_type: TokenUsageFeatureFilter = Query(default="all"),
    model_name: str | None = Query(default=None),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    fields: str | None = Query(default=None, max_length=4_000),
    session: AsyncSession = Depends(get_async_session),
) -> TokenUsageRecordListRead | Response:
    try:
        response = await list_token_usage_records(
            session,
            page=page,
            page_size=page_size,
            feature_type=feature_type,
            model_name=model_name,
            start_at=start_at,
            end_at=end_at,
        )
        return _project_agent_collection_response(response, fields)
    except ValueError as exc:
        raise AgentApiError(
            status_code=422,
            code="INVALID_TOKEN_USAGE_FILTER",
            message=str(exc),
        ) from exc


@router.get("/usage/chart", response_model=TokenUsageChartRead)
async def read_agent_token_usage_chart(
    preset: TokenUsageChartPreset = Query(default="last_24_hours"),
    feature_type: TokenUsageFeatureFilter = Query(default="all"),
    model_name: str | None = Query(default=None),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_async_session),
) -> TokenUsageChartRead:
    try:
        return await build_token_usage_chart(
            session,
            preset=preset,
            feature_type=feature_type,
            model_name=model_name,
            start_at=start_at,
            end_at=end_at,
        )
    except ValueError as exc:
        raise AgentApiError(
            status_code=422,
            code="INVALID_TOKEN_USAGE_FILTER",
            message=str(exc),
        ) from exc


@router.get("/usage/visualization", response_model=TokenUsageVisualizationRead)
async def read_agent_token_usage_visualization(
    preset: TokenUsageChartPreset = Query(default="last_24_hours"),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_async_session),
) -> TokenUsageVisualizationRead:
    try:
        return await build_token_usage_visualization(
            session,
            preset=preset,
            start_at=start_at,
            end_at=end_at,
        )
    except ValueError as exc:
        raise AgentApiError(
            status_code=422,
            code="INVALID_TOKEN_USAGE_FILTER",
            message=str(exc),
        ) from exc


@router.get("/campaigns", response_model=AgentPage[AgentCampaignRead])
async def list_agent_email_campaigns(
    view: Literal["current", "trash"] = Query(default="current"),
    identity_id: int | None = Query(default=None, ge=1),
    status: Literal["running", "paused", "stopped", "completed", "expired"] | None = Query(
        default=None,
    ),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentCampaignRead]:
    campaigns, next_cursor, has_more = await list_agent_campaigns(
        session,
        view=view,
        identity_id=identity_id,
        status=status,
        cursor=cursor,
        limit=limit,
    )
    return AgentPage(items=campaigns, next_cursor=next_cursor, has_more=has_more)


@router.post(
    "/campaigns/prepare-create",
    response_model=AgentChangePlanRead,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_agent_email_campaign_create(
    payload: AgentCampaignCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentChangePlanRead:
    return await create_campaign_create_change_plan(
        get_session_factory(),
        payload,
        idempotency_key=idempotency_key,
    )


@router.get("/campaigns/{campaign_id}", response_model=AgentCampaignRead)
async def read_agent_email_campaign(
    campaign_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> AgentCampaignRead:
    return await get_agent_campaign(session, campaign_id)


@router.get(
    "/campaigns/{campaign_id}/resend-context",
    response_model=BatchTaskResendContextRead,
)
async def read_agent_email_campaign_resend_context(
    campaign_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> BatchTaskResendContextRead:
    try:
        return await build_batch_task_resend_context(session, campaign_id)
    except BatchTaskResendContextError as exc:
        raise AgentApiError(
            status_code=exc.status_code,
            code="CAMPAIGN_RESEND_CONTEXT_UNAVAILABLE",
            message=str(exc),
        ) from exc


@router.get(
    "/campaigns/{campaign_id}/items",
    response_model=AgentPage[AgentCampaignItemRead],
)
async def list_agent_email_campaign_items(
    campaign_id: int,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentCampaignItemRead]:
    items, next_cursor, has_more = await list_agent_campaign_items(
        session,
        campaign_id,
        cursor=cursor,
        limit=limit,
    )
    return AgentPage(items=items, next_cursor=next_cursor, has_more=has_more)


@router.get(
    "/campaigns/{campaign_id}/items/{item_id}/thread",
    response_model=AgentWorkspaceThreadRead,
)
async def read_agent_email_campaign_item_thread(
    campaign_id: int,
    item_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> AgentWorkspaceThreadRead:
    await _ensure_agent_campaign_item(session, campaign_id=campaign_id, item_id=item_id)
    workspace = await build_workspace_thread_for_task(session, task_id=item_id)
    return _serialize_agent_workspace_thread(workspace)


@router.post(
    "/campaigns/{campaign_id}/items/{item_id}/approve-draft",
    response_model=AgentWorkspaceThreadRead,
)
async def approve_agent_email_campaign_item_draft(
    campaign_id: int,
    item_id: int,
    payload: AgentDraftSaveRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentWorkspaceThreadRead:
    async def mutation() -> AgentWorkspaceThreadRead:
        await _ensure_agent_campaign_item(
            session,
            campaign_id=campaign_id,
            item_id=item_id,
        )
        await _ensure_draft_revision(item_id, if_revision)
        return await _run_agent_task_workspace_action(
            session,
            task_id=item_id,
            command="campaigns.approve-item-draft",
            workspace_task_id=item_id,
            action=lambda: approve_draft_task(
                get_session_factory(),
                item_id,
                EmailTaskApprovalRequest(
                    subject=payload.subject,
                    body_text=payload.body_text,
                    body_html=payload.body_html,
                    selected_material_ids=payload.attachment_material_ids,
                ),
            ),
        )

    return await execute_agent_mutation(
        session,
        command="campaigns.approve-item-draft",
        request_data={
            "campaign_id": campaign_id,
            "item_id": item_id,
            "if_revision": if_revision,
            **payload.model_dump(mode="json"),
        },
        idempotency_key=idempotency_key,
        response_type=AgentWorkspaceThreadRead,
        mutation=mutation,
    )


@router.post(
    "/campaigns/{campaign_id}/approve-drafts",
    response_model=AgentCampaignBulkApproveRead,
)
async def approve_agent_email_campaign_drafts(
    campaign_id: int,
    payload: AgentCampaignApproveDraftsRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentCampaignBulkApproveRead:
    async def mutation() -> AgentCampaignBulkApproveRead:
        try:
            approved_count = await approve_generated_batch_drafts(
                get_session_factory(),
                campaign_id,
                payload.item_ids,
            )
        except BatchDraftApprovalConflictError as exc:
            raise AgentApiError(
                status_code=409,
                code="CAMPAIGN_DRAFT_APPROVAL_CONFLICT",
                message=str(exc),
                retryable=True,
            ) from exc
        except ValueError as exc:
            raise AgentApiError(
                status_code=400,
                code="CAMPAIGN_DRAFT_APPROVAL_REJECTED",
                message=str(exc),
            ) from exc
        async with get_session_factory()() as read_session:
            campaign = await get_agent_campaign(read_session, campaign_id)
        return AgentCampaignBulkApproveRead(
            approved_count=approved_count,
            campaign=campaign,
        )

    return await execute_agent_factory_mutation(
        get_session_factory(),
        command="campaigns.approve-drafts",
        request_data={"campaign_id": campaign_id, **payload.model_dump(mode="json")},
        idempotency_key=idempotency_key,
        response_type=AgentCampaignBulkApproveRead,
        mutation=mutation,
    )


@router.post(
    "/campaigns/{campaign_id}/start-drafts",
    response_model=AgentCampaignRead,
)
async def start_agent_email_campaign_drafts(
    campaign_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentCampaignRead:
    return await execute_agent_mutation(
        session,
        command="campaigns.start-drafts",
        request_data={"campaign_id": campaign_id},
        idempotency_key=idempotency_key,
        response_type=AgentCampaignRead,
        mutation=lambda: start_agent_campaign_draft_generation(session, campaign_id),
    )


@router.post("/campaigns/{campaign_id}/pause", response_model=AgentCampaignRead)
async def pause_agent_email_campaign(
    campaign_id: int,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentCampaignRead:
    campaign = await execute_agent_mutation(
        session,
        command="campaigns.pause",
        request_data={"campaign_id": campaign_id},
        idempotency_key=idempotency_key,
        response_type=AgentCampaignRead,
        mutation=lambda: pause_agent_campaign(session, campaign_id),
    )
    _cancel_agent_campaign_draft_generation(request, campaign_id)
    return campaign


@router.post("/campaigns/{campaign_id}/stop", response_model=AgentCampaignRead)
async def stop_agent_email_campaign(
    campaign_id: int,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentCampaignRead:
    campaign = await execute_agent_mutation(
        session,
        command="campaigns.stop",
        request_data={"campaign_id": campaign_id},
        idempotency_key=idempotency_key,
        response_type=AgentCampaignRead,
        mutation=lambda: stop_agent_campaign(session, campaign_id),
    )
    _cancel_agent_campaign_draft_generation(request, campaign_id)
    return campaign


@router.post("/campaigns/{campaign_id}/archive", response_model=AgentCampaignRead)
async def archive_agent_email_campaign(
    campaign_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentCampaignRead:
    return await execute_agent_mutation(
        session,
        command="campaigns.archive",
        request_data={"campaign_id": campaign_id},
        idempotency_key=idempotency_key,
        response_type=AgentCampaignRead,
        mutation=lambda: archive_agent_campaign(session, campaign_id),
    )


@router.post("/campaigns/{campaign_id}/restore", response_model=AgentCampaignRead)
async def restore_agent_email_campaign(
    campaign_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentCampaignRead:
    return await execute_agent_mutation(
        session,
        command="campaigns.restore",
        request_data={"campaign_id": campaign_id},
        idempotency_key=idempotency_key,
        response_type=AgentCampaignRead,
        mutation=lambda: restore_agent_campaign(session, campaign_id),
    )


@router.post(
    "/campaigns/{campaign_id}/items/{item_id}/remove",
    response_model=AgentCampaignRead,
)
async def remove_agent_email_campaign_item(
    campaign_id: int,
    item_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentCampaignRead:
    return await execute_agent_mutation(
        session,
        command="campaigns.items.remove",
        request_data={"campaign_id": campaign_id, "item_id": item_id},
        idempotency_key=idempotency_key,
        response_type=AgentCampaignRead,
        mutation=lambda: remove_agent_campaign_item(session, campaign_id, item_id),
    )


@router.post(
    "/campaigns/{campaign_id}/items/{item_id}/cancel-send",
    response_model=AgentCampaignRead,
)
async def cancel_agent_email_campaign_item_send(
    campaign_id: int,
    item_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentCampaignRead:
    return await execute_agent_mutation(
        session,
        command="campaigns.items.cancel-send",
        request_data={"campaign_id": campaign_id, "item_id": item_id},
        idempotency_key=idempotency_key,
        response_type=AgentCampaignRead,
        mutation=lambda: cancel_agent_campaign_item_send(session, campaign_id, item_id),
    )


@router.post(
    "/campaigns/{campaign_id}/items/{item_id}/retry-draft",
    response_model=AgentCampaignRead,
)
async def retry_agent_email_campaign_item_draft(
    campaign_id: int,
    item_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentCampaignRead:
    return await execute_agent_mutation(
        session,
        command="campaigns.items.retry-draft",
        request_data={"campaign_id": campaign_id, "item_id": item_id},
        idempotency_key=idempotency_key,
        response_type=AgentCampaignRead,
        mutation=lambda: retry_agent_campaign_item_draft(session, campaign_id, item_id),
    )


@router.post(
    "/campaigns/{campaign_id}/prepare-send",
    response_model=AgentChangePlanRead,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_agent_email_campaign_send(
    campaign_id: int,
    payload: AgentCampaignSendRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentChangePlanRead:
    return await create_campaign_send_change_plan(
        get_session_factory(),
        campaign_id,
        payload,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/campaigns/{campaign_id}/prepare-resume",
    response_model=AgentChangePlanRead,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_agent_email_campaign_resume(
    campaign_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentChangePlanRead:
    return await create_campaign_resume_change_plan(
        get_session_factory(),
        campaign_id,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/campaigns/{campaign_id}/items/{item_id}/prepare-restore-send",
    response_model=AgentChangePlanRead,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_agent_email_campaign_item_send_restore(
    campaign_id: int,
    item_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentChangePlanRead:
    return await create_campaign_restore_send_change_plan(
        get_session_factory(),
        campaign_id,
        item_id,
        idempotency_key=idempotency_key,
    )


@router.get("/drafts/{task_id}", response_model=AgentDraftRead)
async def read_agent_draft(
    task_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> AgentDraftRead:
    task = await session.scalar(
        select(EmailTask)
        .options(selectinload(EmailTask.professor))
        .where(EmailTask.id == task_id),
    )
    if task is None:
        raise HTTPException(status_code=404, detail="未找到邮件任务")
    return _serialize_draft(task)


@router.post(
    "/drafts",
    response_model=AgentDraftRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_draft(
    payload: AgentDraftGenerateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentDraftRead:
    try:
        async def mutation() -> AgentDraftRead:
            task = await generate_agent_draft(get_session_factory(), payload)
            return _serialize_draft(task)

        return await execute_agent_factory_mutation(
            get_session_factory(),
            command="drafts.generate",
            request_data=payload.model_dump(mode="json"),
            idempotency_key=idempotency_key,
            response_type=AgentDraftRead,
            mutation=mutation,
            external_execution=True,
        )
    except HTTPException as exc:
        raise AgentApiError(
            status_code=exc.status_code,
            code="DRAFT_OPERATION_REJECTED",
            message=str(exc.detail),
        ) from exc
    except ValueError as exc:
        raise AgentApiError(
            status_code=409,
            code="DRAFT_OPERATION_REJECTED",
            message=str(exc),
        ) from exc


@router.put("/drafts/{task_id}", response_model=AgentDraftRead)
async def save_agent_draft_content(
    task_id: int,
    payload: AgentDraftSaveRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
) -> AgentDraftRead:
    try:
        async def mutation() -> AgentDraftRead:
            await _ensure_draft_revision(task_id, if_revision)
            task = await save_agent_draft(get_session_factory(), task_id, payload)
            return _serialize_draft(task)

        return await execute_agent_factory_mutation(
            get_session_factory(),
            command="drafts.save",
            request_data={
                "task_id": task_id,
                "if_revision": if_revision,
                **payload.model_dump(mode="json", exclude_unset=True),
            },
            idempotency_key=idempotency_key,
            response_type=AgentDraftRead,
            mutation=mutation,
        )
    except ValueError as exc:
        raise AgentApiError(
            status_code=409,
            code="DRAFT_OPERATION_REJECTED",
            message=str(exc),
        ) from exc


@router.post("/drafts/{task_id}/regenerate", response_model=AgentDraftRead)
async def regenerate_agent_draft_content(
    task_id: int,
    payload: AgentDraftRegenerateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
) -> AgentDraftRead:
    try:
        async def mutation() -> AgentDraftRead:
            await _ensure_draft_revision(task_id, if_revision)
            task = await regenerate_agent_draft(get_session_factory(), task_id, payload)
            return _serialize_draft(task)

        return await execute_agent_factory_mutation(
            get_session_factory(),
            command="drafts.regenerate",
            request_data={
                "task_id": task_id,
                "if_revision": if_revision,
                **payload.model_dump(mode="json", exclude_unset=True),
            },
            idempotency_key=idempotency_key,
            response_type=AgentDraftRead,
            mutation=mutation,
            external_execution=True,
        )
    except ValueError as exc:
        raise AgentApiError(
            status_code=409,
            code="DRAFT_OPERATION_REJECTED",
            message=str(exc),
        ) from exc


@router.post("/drafts/{task_id}/rewrite", response_model=AgentDraftRead)
async def rewrite_agent_draft_content(
    task_id: int,
    payload: AgentDraftRewriteRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_revision: str | None = Header(default=None, alias="If-Revision"),
) -> AgentDraftRead:
    try:
        async def mutation() -> AgentDraftRead:
            await _ensure_draft_revision(task_id, if_revision)
            task = await rewrite_agent_draft(get_session_factory(), task_id, payload)
            return _serialize_draft(task)

        return await execute_agent_factory_mutation(
            get_session_factory(),
            command="drafts.rewrite",
            request_data={
                "task_id": task_id,
                "if_revision": if_revision,
                **payload.model_dump(mode="json", exclude_unset=True),
            },
            idempotency_key=idempotency_key,
            response_type=AgentDraftRead,
            mutation=mutation,
            external_execution=True,
        )
    except ValueError as exc:
        raise AgentApiError(
            status_code=409,
            code="DRAFT_OPERATION_REJECTED",
            message=str(exc),
        ) from exc


@router.post(
    "/drafts/{task_id}/prepare-send",
    response_model=AgentActionPlanRead,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_agent_draft_send(
    task_id: int,
    payload: AgentPrepareSendRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentActionPlanRead:
    try:
        return await create_email_action_plan(
            get_session_factory(),
            task_id,
            payload,
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        raise AgentApiError(
            status_code=409,
            code="DRAFT_NOT_SENDABLE",
            message=str(exc),
            suggested_command=f"auto-email-sender drafts get {task_id}",
        ) from exc


@router.get("/test-email/{identity_id}/status", response_model=TestComposeStatusRead)
async def get_agent_test_email_status(
    identity_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> TestComposeStatusRead:
    return await _run_agent_test_email_action(
        lambda: get_test_compose_status(session, identity_id=identity_id),
    )


@router.get("/test-email/{identity_id}/{llm_profile_id}", response_model=TestComposeThreadRead)
async def get_agent_test_email_thread(
    identity_id: int,
    llm_profile_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> TestComposeThreadRead:
    return await _run_agent_test_email_action(
        lambda: build_test_compose_thread(
            session,
            identity_id=identity_id,
            llm_profile_id=llm_profile_id,
        ),
    )


@router.post(
    "/test-email/{identity_id}/{llm_profile_id}/generate-draft",
    response_model=TestComposeThreadRead,
)
async def generate_agent_test_email_draft(
    identity_id: int,
    llm_profile_id: int,
    payload: TestComposeGenerateRequest | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> TestComposeThreadRead:
    request_data = {
        "identity_id": identity_id,
        "llm_profile_id": llm_profile_id,
        "payload": payload.model_dump(mode="json", exclude_unset=True) if payload else {},
    }

    async def mutation() -> TestComposeThreadRead:
        async with get_session_factory()() as mutation_session:
            result = await generate_test_compose_draft(
                mutation_session,
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                outreach_template_id=(payload.outreach_template_id if payload else None),
                template_selection_explicit=(
                    payload is not None and "outreach_template_id" in payload.model_fields_set
                ),
                subject_template=(payload.subject if payload else None),
                body_text_template=(payload.body_text if payload else None),
                body_html_template=(payload.body_html if payload else None),
                template_content_explicit=(
                    payload is not None
                    and bool({"subject", "body_text", "body_html"} & payload.model_fields_set)
                ),
                commit=False,
                event_name="agent_cli.test_email.draft_generated",
                actor="agent_cli",
            )
            await mutation_session.commit()
            return result

    return await _run_agent_test_email_action(
        lambda: execute_agent_factory_mutation(
            get_session_factory(),
            command="test-email.generate",
            request_data=request_data,
            idempotency_key=idempotency_key,
            response_type=TestComposeThreadRead,
            mutation=mutation,
            external_execution=True,
        ),
    )


@router.put(
    "/test-email/{identity_id}/{llm_profile_id}/draft",
    response_model=TestComposeThreadRead,
)
async def save_agent_test_email_draft(
    identity_id: int,
    llm_profile_id: int,
    payload: TestComposeDraftUpdateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> TestComposeThreadRead:
    return await _run_agent_test_email_action(
        lambda: execute_agent_mutation(
            session,
            command="test-email.save",
            request_data={
                "identity_id": identity_id,
                "llm_profile_id": llm_profile_id,
                "payload": payload.model_dump(mode="json", exclude_unset=True),
            },
            idempotency_key=idempotency_key,
            response_type=TestComposeThreadRead,
            mutation=lambda: save_test_compose_draft(
                session,
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                payload=payload,
                commit=False,
                event_name="agent_cli.test_email.draft_saved",
                actor="agent_cli",
            ),
        ),
    )


@router.post(
    "/test-email/{identity_id}/{llm_profile_id}/prepare-send",
    response_model=AgentChangePlanRead,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_agent_test_email_send(
    identity_id: int,
    llm_profile_id: int,
    payload: TestComposeMessageSendRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentChangePlanRead:
    return await create_test_email_send_change_plan(
        get_session_factory(),
        identity_id,
        llm_profile_id,
        payload,
        idempotency_key=idempotency_key,
    )


@router.get("/plans/{plan_id}", response_model=AgentActionPlanRead | AgentChangePlanRead)
async def read_agent_action_plan(plan_id: str) -> AgentActionPlanRead | AgentChangePlanRead:
    if plan_id.startswith("change_"):
        return await get_change_plan(get_session_factory(), plan_id)
    return await get_email_action_plan(get_session_factory(), plan_id)


@router.post(
    "/plans/{plan_id}/execute",
    response_model=AgentActionPlanRead | AgentChangePlanRead,
)
async def execute_agent_action_plan(
    plan_id: str,
    payload: AgentPlanExecuteRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    community_service_factory: Callable[[], CommunityMentorDataService] = Depends(
        get_agent_community_mentor_data_service_factory,
    ),
) -> AgentActionPlanRead | AgentChangePlanRead:
    if plan_id.startswith("change_"):
        return await execute_agent_factory_mutation(
            get_session_factory(),
            command="plans.execute",
            request_data={"plan_id": plan_id, **payload.model_dump(mode="json")},
            idempotency_key=idempotency_key,
            response_type=AgentChangePlanRead,
            mutation=lambda: execute_change_plan(
                get_session_factory(),
                plan_id,
                payload,
                community_service_factory=community_service_factory,
            ),
            external_execution=True,
        )
    return await execute_agent_factory_mutation(
        get_session_factory(),
        command="plans.execute",
        request_data={"plan_id": plan_id, **payload.model_dump(mode="json")},
        idempotency_key=idempotency_key,
        response_type=AgentActionPlanRead,
        mutation=lambda: execute_email_action_plan(get_session_factory(), plan_id, payload),
        external_execution=True,
    )


@router.post(
    "/plans/{plan_id}/cancel",
    response_model=AgentActionPlanRead | AgentChangePlanRead,
)
async def cancel_agent_action_plan(
    plan_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentActionPlanRead | AgentChangePlanRead:
    if plan_id.startswith("change_"):
        return await execute_agent_factory_mutation(
            get_session_factory(),
            command="plans.cancel",
            request_data={"plan_id": plan_id},
            idempotency_key=idempotency_key,
            response_type=AgentChangePlanRead,
            mutation=lambda: cancel_change_plan(get_session_factory(), plan_id),
        )
    return await execute_agent_factory_mutation(
        get_session_factory(),
        command="plans.cancel",
        request_data={"plan_id": plan_id},
        idempotency_key=idempotency_key,
        response_type=AgentActionPlanRead,
        mutation=lambda: cancel_email_action_plan(get_session_factory(), plan_id),
    )


async def _run_agent_test_email_action(
    action: Callable[[], Awaitable[TestEmailActionResult]],
) -> TestEmailActionResult:
    try:
        return await action()
    except LLMRuntimeError as exc:
        raise AgentApiError(
            status_code=502,
            code="TEST_EMAIL_LLM_FAILED",
            message=str(exc),
            external_execution_unknown=True,
        ) from exc
    except ValueError as exc:
        message = str(exc)
        raise AgentApiError(
            status_code=404 if "未找到" in message else 400,
            code="TEST_EMAIL_OPERATION_REJECTED",
            message=message,
        ) from exc


async def _run_agent_task_workspace_action(
    session: AsyncSession,
    *,
    task_id: int,
    command: str,
    workspace_task_id: int | None = None,
    action: Callable[[], Awaitable[tuple[int, int, int]]],
) -> AgentWorkspaceThreadRead:
    try:
        professor_id, identity_id, llm_profile_id = await action()
    except LLMRuntimeError as exc:
        raise AgentApiError(
            status_code=502,
            code="TASK_LLM_OPERATION_FAILED",
            message=sanitize_user_visible_error(exc),
            retryable=True,
            external_execution_unknown=True,
        ) from exc
    except ValueError as exc:
        raise _agent_task_error(exc) from exc

    session.expire_all()
    workspace = (
        await build_workspace_thread_for_task(session, task_id=workspace_task_id)
        if workspace_task_id is not None
        else await build_workspace_thread(
            session,
            professor_id=professor_id,
            identity_id=identity_id,
            llm_profile_id=llm_profile_id,
        )
    )
    await record_operation_log(
        session,
        category="agent_action",
        event_name=f"agent_cli.{command.replace('-', '_')}",
        entity_type="email_task",
        entity_id=str(task_id),
        metadata={
            "actor": "agent_cli",
            "command": command,
            "task_id": task_id,
            "professor_id": professor_id,
            "identity_id": identity_id,
            "llm_profile_id": llm_profile_id,
        },
    )
    return _serialize_agent_workspace_thread(workspace)


async def _ensure_agent_campaign_item(
    session: AsyncSession,
    *,
    campaign_id: int,
    item_id: int,
) -> None:
    matched_item_id = await session.scalar(
        select(EmailTask.id).where(
            EmailTask.id == item_id,
            EmailTask.batch_task_id == campaign_id,
        ),
    )
    if matched_item_id is None:
        raise AgentApiError(
            status_code=404,
            code="CAMPAIGN_ITEM_NOT_FOUND",
            message="未找到属于该活动的邮件项。",
        )


async def _calculate_agent_task_match(
    session: AsyncSession,
    *,
    task_id: int,
    llm_profile_id: int | None,
) -> AgentTaskMatchCalculationRead:
    try:
        result = await calculate_task_match_once(
            get_session_factory(),
            task_id,
            llm_profile_id=llm_profile_id,
        )
    except MatchAnalysisAlreadyRunningError as exc:
        raise AgentApiError(
            status_code=409,
            code="TASK_MATCH_ANALYSIS_RUNNING",
            message=str(exc),
            retryable=True,
        ) from exc
    except LLMRuntimeError as exc:
        raise AgentApiError(
            status_code=502,
            code="TASK_MATCH_ANALYSIS_FAILED",
            message=sanitize_user_visible_error(exc),
            retryable=True,
            external_execution_unknown=True,
        ) from exc
    except ValueError as exc:
        raise _agent_task_error(exc) from exc

    session.expire_all()
    workspace = await build_workspace_thread_for_task(session, task_id=task_id)
    await record_operation_log(
        session,
        category="agent_action",
        event_name="agent_cli.tasks.calculate_match",
        entity_type="email_task",
        entity_id=str(task_id),
        metadata={
            "actor": "agent_cli",
            "task_id": task_id,
            "professor_id": result.professor_id,
            "identity_id": result.identity_id,
            "match_source_identity_id": result.match_source_identity_id,
            "llm_profile_id": result.llm_profile_id,
            "match_analysis_run_id": result.run_id,
            "total_tokens": result.usage.total_tokens,
        },
    )
    return AgentTaskMatchCalculationRead(
        task_id=task_id,
        thread=_serialize_agent_workspace_thread(workspace),
        usage=AgentTaskTokenUsageRead(
            prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
            total_tokens=result.usage.total_tokens,
            cached_tokens=result.usage.cached_tokens,
        ),
        run_id=result.run_id,
    )


def _agent_task_error(error: ValueError) -> AgentApiError:
    message = str(error)
    return AgentApiError(
        status_code=404 if "不存在" in message else 409,
        code="TASK_OPERATION_REJECTED",
        message=message,
    )


async def _ensure_agent_workspace_task(
    session: AsyncSession,
    *,
    professor_id: int,
    identity_id: int,
    llm_profile_id: int,
) -> AgentWorkspaceThreadRead:
    task = await ensure_workspace_task(
        session,
        professor_id=professor_id,
        identity_id=identity_id,
        llm_profile_id=llm_profile_id,
        commit=False,
    )
    workspace = await build_workspace_thread(
        session,
        professor_id=professor_id,
        identity_id=identity_id,
        llm_profile_id=llm_profile_id,
    )
    await record_operation_log(
        session,
        category="agent_action",
        event_name="agent_cli.workspace_task_ensured",
        entity_type="email_task",
        entity_id=str(task.id),
        metadata={
            "actor": "agent_cli",
            "task_id": task.id,
            "professor_id": professor_id,
            "identity_id": identity_id,
            "llm_profile_id": llm_profile_id,
            "task_status": task.status,
        },
    )
    return _serialize_agent_workspace_thread(workspace)


async def _create_agent_professor(
    session: AsyncSession,
    payload: AgentProfessorUpsertRequest,
) -> AgentProfessorRead:
    professor = await create_professor_record(
        session,
        ProfessorUpsertPayload.model_validate(payload.model_dump()),
        event_name="agent_cli.professor.created",
        actor="agent_cli",
    )
    return _serialize_professor(professor)


async def _upload_agent_material(
    session: AsyncSession,
    identity_id: int | None,
    file: UploadFile,
    material_type: str,
    display_name: str | None,
) -> AgentMaterialRead:
    material, primary_material_id = await upload_identity_material_record(
        session,
        identity_id,
        file,
        material_type,
        display_name,
        event_name="agent_cli.material.uploaded",
        actor="agent_cli",
    )
    return _serialize_material(
        material,
        include_text=False,
        primary_material_id=primary_material_id,
        target_identity_id=identity_id,
        default_for_identity_ids=(
            [identity_id]
            if identity_id is not None and primary_material_id == material.id
            else []
        ),
    )


async def _set_agent_primary_material(
    session: AsyncSession,
    material_id: int,
    identity_id: int | None,
) -> AgentMaterialRead:
    material, primary_material_id = await set_primary_material_record(
        session,
        material_id,
        identity_id=identity_id,
        event_name="agent_cli.material.primary_set",
        actor="agent_cli",
    )
    target_identity_id = identity_id if identity_id is not None else material.identity_id
    default_identity_ids = {
        identity.id for identity in material.default_for_identities
    }
    if target_identity_id is not None:
        default_identity_ids.add(target_identity_id)
    return _serialize_material(
        material,
        include_text=False,
        primary_material_id=primary_material_id,
        target_identity_id=target_identity_id,
        default_for_identity_ids=sorted(default_identity_ids),
    )


async def _create_agent_match_analysis_job(
    session: AsyncSession,
    payload: AgentMatchAnalysisJobCreateRequest,
) -> AgentMatchAnalysisJobRead:
    job = await create_match_analysis_job_record(
        session,
        identity_id=payload.identity_id,
        llm_profile_id=payload.llm_profile_id,
        professor_ids=payload.professor_ids,
        name=payload.name,
        event_name="agent_cli.match_analysis_job.created",
        actor="agent_cli",
    )
    return _serialize_match_analysis_job(job)


async def _cancel_agent_match_analysis_job(
    session: AsyncSession,
    job_id: int,
) -> AgentMatchAnalysisJobActionRead:
    job = await request_match_analysis_job_cancel_record(
        session,
        job_id,
        event_name="agent_cli.match_analysis_job.cancel_requested",
        actor="agent_cli",
    )
    return AgentMatchAnalysisJobActionRead(
        ok=True,
        job=_serialize_match_analysis_job(job),
    )


async def _retry_agent_match_analysis_job(
    session: AsyncSession,
    job_id: int,
) -> AgentMatchAnalysisJobRead:
    job = await retry_failed_match_analysis_job_record(
        session,
        job_id,
        event_name="agent_cli.match_analysis_job.retry_created",
        actor="agent_cli",
    )
    return _serialize_match_analysis_job(job)


async def _delete_agent_match_analysis_job(
    session: AsyncSession,
    job_id: int,
) -> AgentMatchAnalysisJobActionRead:
    job = await delete_match_analysis_job_record(
        session,
        job_id,
        event_name="agent_cli.match_analysis_job.deleted",
        actor="agent_cli",
    )
    return AgentMatchAnalysisJobActionRead(
        ok=True,
        job=_serialize_match_analysis_job(job),
    )


async def _restore_agent_match_analysis_job(
    session: AsyncSession,
    job_id: int,
) -> AgentMatchAnalysisJobActionRead:
    job = await restore_match_analysis_job_record(
        session,
        job_id,
        event_name="agent_cli.match_analysis_job.restored",
        actor="agent_cli",
    )
    return AgentMatchAnalysisJobActionRead(
        ok=True,
        job=_serialize_match_analysis_job(job),
    )


def _agent_match_analysis_error(error: ValueError) -> AgentApiError:
    message = str(error)
    return AgentApiError(
        status_code=404 if "不存在" in message else 409,
        code="MATCH_ANALYSIS_OPERATION_REJECTED",
        message=message,
    )


async def _create_agent_communication_group(
    session: AsyncSession,
    payload: IdentityCommunicationGroupWrite,
) -> IdentityCommunicationGroupRead:
    group = await create_communication_group_record(
        session,
        payload,
        event_name="agent_cli.communication_group.created",
        actor="agent_cli",
    )
    return await get_communication_group_record(session, group.id)


async def _update_agent_communication_group(
    session: AsyncSession,
    group_id: int,
    payload: IdentityCommunicationGroupWrite,
) -> IdentityCommunicationGroupRead:
    group = await update_communication_group_record(
        session,
        group_id,
        payload,
        event_name="agent_cli.communication_group.updated",
        actor="agent_cli",
    )
    return await get_communication_group_record(session, group.id)


async def _update_agent_communication_group_with_revision(
    session: AsyncSession,
    group_id: int,
    payload: IdentityCommunicationGroupWrite,
    *,
    if_revision: str | None,
) -> IdentityCommunicationGroupRead:
    if if_revision:
        current = await get_communication_group_record(session, group_id)
        ensure_revision(
            if_revision,
            current.revision,
            resource="communication-groups",
            resource_id=group_id,
            latest=current.model_dump(mode="json"),
        )
    return await _update_agent_communication_group(session, group_id, payload)


async def _delete_agent_communication_group(
    session: AsyncSession,
    group_id: int,
) -> AgentCommunicationGroupDeleteRead:
    await delete_communication_group_record(
        session,
        group_id,
        event_name="agent_cli.communication_group.deleted",
        actor="agent_cli",
    )
    return AgentCommunicationGroupDeleteRead(ok=True, group_id=group_id)


def _agent_communication_group_error(
    error: CommunicationGroupMutationError,
) -> AgentApiError:
    return AgentApiError(
        status_code=error.status_code,
        code=error.code,
        message=error.message,
        details=error.details or {},
    )


async def _set_agent_default_llm_profile(
    session: AsyncSession,
    profile_id: int,
) -> AgentLLMProfileRead:
    profile = await _get_agent_llm_profile_or_raise(session, profile_id)
    profiles = list(await session.scalars(select(LLMProfile)))
    now = utc_now()
    for candidate in profiles:
        is_default = candidate.id == profile.id
        if candidate.is_default != is_default:
            candidate.is_default = is_default
            candidate.updated_at = now
    await _record_agent_llm_profile_event(
        session,
        profile,
        "agent_cli.llm_profile.default_set",
    )
    return _serialize_llm_profile(profile)


async def _set_agent_default_llm_profile_with_revision(
    session: AsyncSession,
    profile_id: int,
    *,
    if_revision: str | None,
) -> AgentLLMProfileRead:
    await _ensure_llm_profile_revision(session, profile_id, if_revision)
    return await _set_agent_default_llm_profile(session, profile_id)


async def _update_agent_llm_profile_settings(
    session: AsyncSession,
    profile_id: int,
    payload: AgentLLMProfileSettingsUpdateRequest,
) -> AgentLLMProfileRead:
    profile = await _get_agent_llm_profile_or_raise(session, profile_id)
    updates = payload.model_dump(exclude_unset=True)
    if "name" in updates:
        profile.name = str(updates["name"]).strip()
    if "model_name" in updates:
        profile.model_name = str(updates["model_name"]).strip()
    if "temperature" in updates:
        profile.temperature = updates["temperature"]
    if "max_tokens" in updates:
        profile.max_tokens = updates["max_tokens"]
    profile.updated_at = utc_now()
    await record_operation_log(
        session,
        category="user_action",
        event_name="agent_cli.llm_profile.settings_updated",
        level="info",
        entity_type="llm_profile",
        entity_id=str(profile.id),
        metadata={
            "changed_fields": sorted(updates),
            "actor": "agent_cli",
        },
    )
    return _serialize_llm_profile(profile)


async def _update_agent_llm_profile_settings_with_revision(
    session: AsyncSession,
    profile_id: int,
    payload: AgentLLMProfileSettingsUpdateRequest,
    *,
    if_revision: str | None,
) -> AgentLLMProfileRead:
    await _ensure_llm_profile_revision(session, profile_id, if_revision)
    return await _update_agent_llm_profile_settings(session, profile_id, payload)


async def _test_agent_llm_profile(
    session: AsyncSession,
    profile_id: int,
) -> AgentLLMProfileTestRead:
    profile = await _get_agent_llm_profile_or_raise(session, profile_id)
    try:
        adaptation = await ensure_llm_runtime_adaptation(session, profile)
    except (LLMRuntimeError, ThinkingAdaptationFailed) as exc:
        result = _build_agent_llm_adaptation_failure_probe_result(profile, exc)
    else:
        result = await probe_llm_profile(
            profile,
            session=session,
            adaptation=adaptation,
        )
    await _record_agent_llm_profile_event(
        session,
        profile,
        "agent_cli.llm_profile.tested",
        level="info" if result.ok else "warning",
        metadata={
            "ok": result.ok,
            "result": "ok" if result.ok else "failed",
            "status_code": result.status_code,
            "duration_ms": result.duration_ms,
            "endpoint_kind": result.endpoint_kind,
            "consumes_tokens": result.consumes_tokens,
        },
    )
    return _serialize_agent_llm_profile_test(profile.id, result)


async def _get_agent_llm_profile_or_raise(
    session: AsyncSession,
    profile_id: int,
) -> LLMProfile:
    profile = await session.get(LLMProfile, profile_id)
    if profile is None:
        raise ValueError("未找到 LLM 配置")
    return profile


async def _record_agent_llm_profile_event(
    session: AsyncSession,
    profile: LLMProfile,
    event_name: str,
    *,
    level: str = "info",
    metadata: dict[str, object] | None = None,
) -> None:
    event_metadata: dict[str, object] = {
        "id": profile.id,
        "name": profile.name,
        "provider": profile.provider,
        "model_name": profile.model_name,
        "is_default": profile.is_default,
        "actor": "agent_cli",
    }
    if metadata:
        event_metadata.update(metadata)
    await record_operation_log(
        session,
        category="user_action",
        event_name=event_name,
        level=level,
        entity_type="llm_profile",
        entity_id=str(profile.id),
        metadata=event_metadata,
    )


def _build_agent_llm_adaptation_failure_probe_result(
    profile: LLMProfile,
    exc: LLMRuntimeError | ThinkingAdaptationFailed,
) -> LLMProbeResult:
    runtime_error = exc.last_error if isinstance(exc, ThinkingAdaptationFailed) else exc
    if runtime_error is not None:
        return LLMProbeResult(
            ok=False,
            message=str(runtime_error),
            resolved_base_url=resolve_base_url(profile.api_base_url),
            request_url=runtime_error.request_url,
            attempted_urls=runtime_error.attempted_urls,
            endpoint_kind=runtime_error.endpoint_kind,
            status_code=runtime_error.status_code,
            duration_ms=runtime_error.duration_ms,
            consumes_tokens=True,
        )
    return LLMProbeResult(
        ok=False,
        message=str(exc),
        resolved_base_url=resolve_base_url(profile.api_base_url),
        consumes_tokens=True,
    )


def _serialize_agent_llm_profile_models(
    profile_id: int,
    result: LLMModelCatalogResult,
) -> AgentLLMProfileModelsRead:
    return AgentLLMProfileModelsRead(
        profile_id=profile_id,
        ok=result.ok,
        message=sanitize_user_visible_error(result.message),
        resolved_base_url=_sanitize_agent_llm_url(result.resolved_base_url),
        request_url=_sanitize_agent_llm_url(result.request_url),
        attempted_urls=_sanitize_agent_llm_urls(result.attempted_urls),
        endpoint_kind=result.endpoint_kind,
        status_code=result.status_code,
        duration_ms=result.duration_ms,
        consumes_tokens=result.consumes_tokens,
        models=result.models,
        selected_model_available=result.selected_model_available,
    )


def _serialize_agent_llm_profile_test(
    profile_id: int,
    result: LLMProbeResult,
) -> AgentLLMProfileTestRead:
    return AgentLLMProfileTestRead(
        profile_id=profile_id,
        ok=result.ok,
        message=sanitize_user_visible_error(result.message),
        resolved_base_url=_sanitize_agent_llm_url(result.resolved_base_url),
        request_url=_sanitize_agent_llm_url(result.request_url),
        attempted_urls=_sanitize_agent_llm_urls(result.attempted_urls),
        endpoint_kind=result.endpoint_kind,
        status_code=result.status_code,
        duration_ms=result.duration_ms,
        consumes_tokens=result.consumes_tokens,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        total_tokens=result.total_tokens,
    )


def _sanitize_agent_llm_url(url: str | None) -> str | None:
    if url is None:
        return None
    parsed = urlsplit(url)
    hostname = parsed.hostname
    if hostname is None:
        netloc = parsed.netloc.rsplit("@", 1)[-1]
    else:
        netloc = f"[{hostname}]" if ":" in hostname else hostname
        try:
            port = parsed.port
        except ValueError:
            port = None
        if port is not None:
            netloc = f"{netloc}:{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _sanitize_agent_llm_urls(urls: list[str]) -> list[str]:
    return [
        sanitized
        for url in urls
        if (sanitized := _sanitize_agent_llm_url(url)) is not None
    ]


def _agent_llm_profile_error(error: ValueError) -> AgentApiError:
    return AgentApiError(
        status_code=404 if "未找到" in str(error) else 422,
        code="LLM_PROFILE_OPERATION_REJECTED",
        message=str(error),
    )


async def _set_agent_default_identity(
    session: AsyncSession,
    identity_id: int,
) -> AgentIdentityRead:
    identity = await _get_agent_identity_or_raise(session, identity_id)
    identities = list(await session.scalars(select(IdentityProfile)))
    now = utc_now()
    for candidate in identities:
        is_default = candidate.id == identity.id
        if candidate.is_default != is_default:
            candidate.is_default = is_default
            candidate.updated_at = now
    await _record_agent_identity_event(
        session,
        identity,
        "agent_cli.identity.default_set",
    )
    return _serialize_identity(identity)


async def _set_agent_default_identity_with_revision(
    session: AsyncSession,
    identity_id: int,
    *,
    if_revision: str | None,
) -> AgentIdentityRead:
    await _ensure_identity_revision(session, identity_id, if_revision)
    return await _set_agent_default_identity(session, identity_id)


async def _update_agent_identity_settings(
    session: AsyncSession,
    identity_id: int,
    payload: AgentIdentitySettingsUpdateRequest,
) -> AgentIdentityRead:
    identity = await _get_agent_identity_or_raise(session, identity_id)
    updates = payload.model_dump(exclude_unset=True)
    send_interval_min = updates.get("send_interval_min", identity.send_interval_min)
    send_interval_max = updates.get("send_interval_max", identity.send_interval_max)
    if (
        send_interval_min is not None
        and send_interval_max is not None
        and send_interval_min > send_interval_max
    ):
        raise ValueError("send_interval_min 不能大于 send_interval_max")

    if "profile_name" in updates:
        profile_name = str(updates["profile_name"]).strip()
        identity.profile_name = profile_name
        identity.name = profile_name
    if "sender_name" in updates:
        identity.sender_name = str(updates["sender_name"]).strip()
    if "default_language" in updates:
        identity.default_language = str(updates["default_language"]).strip()
    if "outreach_generation_mode" in updates:
        identity.outreach_generation_mode = str(updates["outreach_generation_mode"])
    for field_name in (
        "match_threshold",
        "daily_send_limit",
        "send_interval_min",
        "send_interval_max",
        "same_domain_cooldown_minutes",
    ):
        if field_name in updates:
            setattr(identity, field_name, updates[field_name])
    identity.updated_at = utc_now()
    await record_operation_log(
        session,
        category="user_action",
        event_name="agent_cli.identity.settings_updated",
        level="info",
        entity_type="identity",
        entity_id=str(identity.id),
        metadata={
            "changed_fields": sorted(updates),
            "actor": "agent_cli",
        },
    )
    return _serialize_identity(identity)


async def _update_agent_identity_settings_with_revision(
    session: AsyncSession,
    identity_id: int,
    payload: AgentIdentitySettingsUpdateRequest,
    *,
    if_revision: str | None,
) -> AgentIdentityRead:
    await _ensure_identity_revision(session, identity_id, if_revision)
    return await _update_agent_identity_settings(session, identity_id, payload)


async def _set_agent_identity_default_template(
    session: AsyncSession,
    identity_id: int,
    payload: IdentityDefaultOutreachTemplateUpdate,
) -> AgentIdentityRead:
    identity = await _get_agent_identity_or_raise(session, identity_id)
    if payload.template_id is None:
        clear_identity_default_template(identity)
    else:
        try:
            template = await get_outreach_template(session, payload.template_id)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        apply_template_to_identity_legacy_fields(identity, template)
    identity.updated_at = utc_now()
    await _record_agent_identity_event(
        session,
        identity,
        "agent_cli.identity.default_outreach_template_updated",
        metadata={"default_outreach_template_id": identity.default_outreach_template_id},
    )
    return _serialize_identity(identity)


async def _set_agent_identity_default_template_with_revision(
    session: AsyncSession,
    identity_id: int,
    payload: IdentityDefaultOutreachTemplateUpdate,
    *,
    if_revision: str | None,
) -> AgentIdentityRead:
    await _ensure_identity_revision(session, identity_id, if_revision)
    return await _set_agent_identity_default_template(session, identity_id, payload)


async def _test_agent_identity_smtp(
    session: AsyncSession,
    identity_id: int,
) -> ConnectionTestResult:
    identity = await _get_agent_identity_or_raise(session, identity_id)
    started_at = perf_counter()
    ok, message = await test_smtp_connection(identity)
    await _record_agent_identity_event(
        session,
        identity,
        "agent_cli.identity.smtp_tested",
        level="info" if ok else "warning",
        metadata={
            "ok": ok,
            "result": "ok" if ok else "failed",
            "duration_ms": int((perf_counter() - started_at) * 1000),
            "host": identity.smtp_host,
        },
    )
    safe_message = sanitize_user_visible_error(message)
    return ConnectionTestResult(
        ok=ok,
        message=safe_message,
        host=identity.smtp_host,
        possible_cause=explain_smtp_error(safe_message) if not ok else None,
    )


async def _test_agent_identity_imap(
    session: AsyncSession,
    identity_id: int,
) -> ConnectionTestResult:
    identity = await _get_agent_identity_or_raise(session, identity_id)
    started_at = perf_counter()
    ok, message = await test_imap_connection(identity)
    await _record_agent_identity_event(
        session,
        identity,
        "agent_cli.identity.imap_tested",
        level="info" if ok else "warning",
        metadata={
            "ok": ok,
            "result": "ok" if ok else "failed",
            "duration_ms": int((perf_counter() - started_at) * 1000),
            "host": identity.imap_host,
        },
    )
    return ConnectionTestResult(
        ok=ok,
        message=sanitize_user_visible_error(message),
        host=identity.imap_host,
    )


async def _get_agent_identity_or_raise(
    session: AsyncSession,
    identity_id: int,
) -> IdentityProfile:
    identity = await session.get(IdentityProfile, identity_id)
    if identity is None:
        raise ValueError("未找到身份配置")
    return identity


async def _record_agent_identity_event(
    session: AsyncSession,
    identity: IdentityProfile,
    event_name: str,
    *,
    level: str = "info",
    metadata: dict[str, object] | None = None,
) -> None:
    event_metadata: dict[str, object] = {
        "id": identity.id,
        "name": identity.name,
        "profile_name": identity.profile_name,
        "sender_name": identity.sender_name,
        "email_address": identity.email_address,
        "smtp_host": identity.smtp_host,
        "imap_host": identity.imap_host,
        "is_default": identity.is_default,
        "actor": "agent_cli",
    }
    if metadata:
        event_metadata.update(metadata)
    await record_operation_log(
        session,
        category="user_action",
        event_name=event_name,
        level=level,
        entity_type="identity",
        entity_id=str(identity.id),
        metadata=event_metadata,
    )


def _agent_identity_error(error: ValueError) -> AgentApiError:
    message = str(error)
    return AgentApiError(
        status_code=404 if "未找到" in message or "不存在" in message else 422,
        code="IDENTITY_OPERATION_REJECTED",
        message=message,
    )


async def _create_agent_professor_information_enrichment_job(
    session: AsyncSession,
    payload: CreateProfessorInformationEnrichmentJobRequest,
) -> ProfessorInformationEnrichmentJobRead:
    job = await create_professor_information_enrichment_job_record(
        session,
        professor_ids=payload.professor_ids,
        llm_profile_id=payload.llm_profile_id,
        trigger_mode=CrawlJobTriggerMode.BATCH.value,
        name=payload.name,
        event_name="agent_cli.professor_information_enrichment_job.created",
        actor="agent_cli",
    )
    result = await get_professor_information_enrichment_job(session, job.id)
    if result is None:  # pragma: no cover - the record was just created in this transaction
        raise ValueError("信息补全任务不存在")
    return result


async def _cancel_agent_professor_information_enrichment_job(
    session: AsyncSession,
    job_id: int,
) -> ProfessorInformationEnrichmentJobActionRead:
    existing = await get_professor_information_enrichment_job(session, job_id)
    if existing is None:
        raise ValueError("信息补全任务不存在")
    job = await session.get(CrawlJob, job_id)
    if job is None:  # pragma: no cover - checked immediately above
        raise ValueError("信息补全任务不存在")
    await request_professor_information_enrichment_cancel(
        session,
        job,
        event_name="agent_cli.professor_information_enrichment_job.cancel_requested",
        actor="agent_cli",
    )
    result = await get_professor_information_enrichment_job(session, job_id)
    if result is None:  # pragma: no cover - the record cannot disappear in this transaction
        raise ValueError("信息补全任务不存在")
    return ProfessorInformationEnrichmentJobActionRead(ok=True, job=result)


async def _retry_agent_professor_information_enrichment_job(
    session: AsyncSession,
    job_id: int,
) -> ProfessorInformationEnrichmentJobRead:
    job = await retry_failed_professor_information_enrichment_job_record(
        session,
        job_id,
        actor="agent_cli",
    )
    result = await get_professor_information_enrichment_job(session, job.id)
    if result is None:  # pragma: no cover - the record was just created in this transaction
        raise ValueError("信息补全任务不存在")
    return result


async def _delete_agent_professor_information_enrichment_job(
    session: AsyncSession,
    job_id: int,
) -> ProfessorInformationEnrichmentJobActionRead:
    job = await delete_professor_information_enrichment_job_record(
        session,
        job_id,
        event_name="agent_cli.professor_information_enrichment_job.deleted",
        actor="agent_cli",
    )
    result = await get_professor_information_enrichment_job(session, job.id)
    if result is None:  # pragma: no cover - the record cannot disappear in this transaction
        raise ValueError("信息补全任务不存在")
    return ProfessorInformationEnrichmentJobActionRead(ok=True, job=result)


async def _restore_agent_professor_information_enrichment_job(
    session: AsyncSession,
    job_id: int,
) -> ProfessorInformationEnrichmentJobActionRead:
    job = await restore_professor_information_enrichment_job_record(
        session,
        job_id,
        event_name="agent_cli.professor_information_enrichment_job.restored",
        actor="agent_cli",
    )
    result = await get_professor_information_enrichment_job(session, job.id)
    if result is None:  # pragma: no cover - the record cannot disappear in this transaction
        raise ValueError("信息补全任务不存在")
    return ProfessorInformationEnrichmentJobActionRead(ok=True, job=result)


def _agent_information_enrichment_error(
    error: ValueError | RuntimeError,
    *,
    status_code: int | None = None,
) -> AgentApiError:
    message = str(error)
    if status_code is None:
        if "不存在" in message:
            status_code = 404
        elif "已有" in message or "请先取消" in message:
            status_code = 409
        else:
            status_code = 422
    return AgentApiError(
        status_code=status_code,
        code="INFORMATION_ENRICHMENT_OPERATION_REJECTED",
        message=message,
    )


async def _create_agent_faculty_crawl_job(
    session: AsyncSession,
    payload: CrawlJobCreatePayload,
) -> CrawlJobSummaryRead:
    job = await create_faculty_crawl_job_record(
        session,
        payload,
        event_name="agent_cli.crawl_job.created",
        actor="agent_cli",
    )
    return await get_faculty_crawl_job_summary(session, job.id)


async def _create_many_agent_faculty_crawl_jobs(
    session: AsyncSession,
    raw_items: list[dict[str, object]],
) -> AgentCrawlJobBatchCreateRead:
    created_job_ids: list[int] = []
    failures: list[dict[str, object]] = []
    for index, raw_item in enumerate(raw_items):
        try:
            payload = CrawlJobCreatePayload.model_validate(raw_item)
        except ValidationError as exc:
            failures.append(
                {
                    "index": index,
                    "code": "INVALID_BATCH_ITEM",
                    "message": _batch_validation_message(exc),
                    "retryable": False,
                },
            )
            continue
        try:
            async with session.begin_nested():
                job = await create_faculty_crawl_job_record(
                    session,
                    payload,
                    event_name="agent_cli.crawl_job.created",
                    actor="agent_cli",
                )
                created_job_ids.append(job.id)
        except CrawlJobRecordError as exc:
            failures.append(
                {
                    "index": index,
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": exc.status_code >= 500,
                },
            )
    return AgentCrawlJobBatchCreateRead(
        requested_count=len(raw_items),
        created_count=len(created_job_ids),
        failed_count=len(failures),
        created_job_ids=created_job_ids,
        failures=failures,
    )


async def _enrich_many_agent_crawl_candidates(
    session: AsyncSession,
    raw_items: list[dict[str, object]],
) -> AgentCrawlJobBatchEnrichRead:
    items: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    for index, raw_item in enumerate(raw_items):
        resource_id = raw_item.get("job_id")
        normalized_resource_id = (
            resource_id
            if isinstance(resource_id, int) and not isinstance(resource_id, bool) and resource_id > 0
            else None
        )
        try:
            payload = AgentCrawlJobBatchEnrichItem.model_validate(raw_item)
        except ValidationError as exc:
            failures.append(
                {
                    "index": index,
                    "resource_id": normalized_resource_id,
                    "code": "INVALID_BATCH_ITEM",
                    "message": _batch_validation_message(exc),
                    "retryable": False,
                },
            )
            continue
        try:
            async with session.begin_nested():
                result = await enqueue_faculty_crawl_candidate_enrichment_records(
                    session,
                    payload.job_id,
                    payload.selection,
                    llm_profile_id=payload.llm_profile_id,
                    event_name="agent_cli.crawl_candidate_enrichment.queued",
                    actor="agent_cli",
                )
        except CrawlJobRecordError as exc:
            failures.append(
                {
                    "index": index,
                    "resource_id": payload.job_id,
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": exc.code == "CRAWL_CANDIDATE_ENRICHMENT_RUNNING",
                },
            )
            continue
        submission = result.submission
        observation = result.observation
        items.append(
            {
                "job_id": payload.job_id,
                "queued_count": submission.queued_count if submission is not None else 0,
                "already_active_count": (
                    submission.already_active_count if submission is not None else 0
                ),
                "already_completed_count": (
                    submission.already_completed_count if submission is not None else 0
                ),
                "skipped_count": result.skipped_count,
                "status": observation.status if observation is not None else "unknown",
            },
        )
    return AgentCrawlJobBatchEnrichRead(
        requested_count=len(raw_items),
        accepted_count=len(items),
        failed_count=len(failures),
        queued_count=sum(int(item["queued_count"]) for item in items),
        skipped_count=sum(int(item["skipped_count"]) for item in items),
        items=items,
        failures=failures,
    )


def _batch_validation_message(error: ValidationError) -> str:
    first_error = error.errors(include_url=False)[0]
    location = ".".join(str(item) for item in first_error.get("loc", ()))
    message = str(first_error.get("msg") or "批量项格式无效")
    return f"{location}: {message}" if location else message


async def _update_agent_faculty_crawl_candidate(
    session: AsyncSession,
    candidate_id: int,
    payload: AgentCrawlCandidateUpdateRequest,
    if_revision: str | None = None,
) -> AgentCrawlCandidateRead:
    candidate = await get_faculty_crawl_candidate_or_raise(session, candidate_id)
    current_read = _serialize_crawl_candidate(candidate)
    ensure_revision(
        if_revision,
        current_read.revision,
        resource="crawler.candidates",
        resource_id=candidate_id,
        latest=current_read.model_dump(mode="json"),
    )
    current = {
        "name": candidate.name,
        "email": candidate.email,
        "title": candidate.title,
        "university": candidate.university,
        "school": candidate.school,
        "department": candidate.department,
        "research_direction": candidate.research_direction,
        "recent_papers": candidate.recent_papers or [],
        "profile_url": candidate.profile_url,
        "source_url": candidate.source_url,
        "review_status": candidate.review_status,
    }
    current.update(payload.model_dump(exclude_unset=True))
    updated = await update_faculty_crawl_candidate_record(
        session,
        candidate_id,
        CrawlCandidateUpdatePayload.model_validate(current),
        event_name="agent_cli.crawl_candidate.updated",
        actor="agent_cli",
    )
    return _serialize_crawl_candidate(updated)


async def _pause_agent_faculty_crawl_job(
    session: AsyncSession,
    job_id: int,
) -> CrawlJobSummaryRead:
    job = await pause_faculty_crawl_job_record(
        session,
        job_id,
        event_name="agent_cli.crawl_job.paused",
        actor="agent_cli",
    )
    return await get_faculty_crawl_job_summary(session, job.id)


async def _resume_agent_faculty_crawl_job(
    session: AsyncSession,
    job_id: int,
    payload: CrawlJobResumePayload | None,
) -> CrawlJobSummaryRead:
    job = await resume_faculty_crawl_job_record(
        session,
        job_id,
        payload,
        event_name="agent_cli.crawl_job.resumed",
        actor="agent_cli",
    )
    return await get_faculty_crawl_job_summary(session, job.id)


async def _cancel_agent_faculty_crawl_job(
    session: AsyncSession,
    job_id: int,
) -> CrawlJobSummaryRead:
    job = await cancel_faculty_crawl_job_record(
        session,
        job_id,
        event_name="agent_cli.crawl_job.canceled",
        actor="agent_cli",
    )
    return await get_faculty_crawl_job_summary(session, job.id)


async def _resume_agent_faculty_crawl_job_review(
    session: AsyncSession,
    job_id: int,
) -> CrawlJobSummaryRead:
    job = await resume_faculty_crawl_job_review_record(
        session,
        job_id,
        event_name="agent_cli.crawl_job.review_resumed",
        actor="agent_cli",
    )
    return await get_faculty_crawl_job_summary(session, job.id)


async def _delete_agent_faculty_crawl_job(
    session: AsyncSession,
    job_id: int,
) -> CrawlJobSummaryRead:
    job = await delete_faculty_crawl_job_record(
        session,
        job_id,
        event_name="agent_cli.crawl_job.deleted",
        actor="agent_cli",
    )
    return await get_faculty_crawl_job_summary(session, job.id)


async def _restore_agent_faculty_crawl_job(
    session: AsyncSession,
    job_id: int,
) -> CrawlJobSummaryRead:
    job = await restore_faculty_crawl_job_record(
        session,
        job_id,
        event_name="agent_cli.crawl_job.restored",
        actor="agent_cli",
    )
    return await get_faculty_crawl_job_summary(session, job.id)


def _agent_crawl_job_error(error: CrawlJobRecordError) -> AgentApiError:
    return AgentApiError(
        status_code=error.status_code,
        code=error.code,
        message=error.message,
    )


async def _update_agent_runtime_settings(
    session: AsyncSession,
    payload: RuntimeSettingsUpdate,
) -> RuntimeSettingsRead:
    settings = await update_runtime_settings(
        session,
        payload,
        event_name="agent_cli.runtime_settings.updated",
        actor="agent_cli",
    )
    return serialize_runtime_settings(settings)


async def _update_agent_runtime_settings_with_revision(
    session: AsyncSession,
    payload: RuntimeSettingsUpdate,
    *,
    if_revision: str | None,
) -> RuntimeSettingsRead:
    if if_revision:
        settings = await get_runtime_settings(session)
        current = serialize_runtime_settings(settings)
        ensure_revision(
            if_revision,
            current.revision,
            resource="settings",
            resource_id="1",
            latest=current.model_dump(mode="json"),
        )
    return await _update_agent_runtime_settings(session, payload)


def _agent_material_error(error: MaterialMutationError) -> AgentApiError:
    return AgentApiError(
        status_code=error.status_code,
        code=error.code,
        message=error.message,
    )


async def _update_agent_professor(
    session: AsyncSession,
    professor_id: int,
    payload: AgentProfessorUpdateRequest,
) -> AgentProfessorRead:
    existing = await get_professor_with_tags_or_raise(session, professor_id)
    merged_payload = {
        "name": existing.name,
        "email": existing.email,
        "title": existing.title,
        "university": existing.university,
        "school": existing.school,
        "department": existing.department,
        "research_direction": existing.research_direction,
        "recent_papers": existing.recent_papers or [],
        "profile_url": existing.profile_url,
        "source_url": existing.source_url,
        "personal_note": existing.personal_note,
        "tag_ids": [tag.id for tag in existing.tags],
    }
    merged_payload.update(payload.model_dump(exclude_unset=True))
    professor = await update_professor_record(
        session,
        professor_id,
        ProfessorUpsertPayload.model_validate(merged_payload),
        event_name="agent_cli.professor.updated",
        actor="agent_cli",
    )
    return _serialize_professor(professor)


async def _ensure_professor_revision(
    session: AsyncSession,
    professor_id: int,
    if_revision: str | None,
) -> None:
    if not if_revision:
        return
    current = await get_professor_with_tags_or_raise(session, professor_id)
    current_read = _serialize_professor(current)
    ensure_revision(
        if_revision,
        current_read.revision,
        resource="professors",
        resource_id=professor_id,
        latest=current_read.model_dump(mode="json"),
    )


async def _update_agent_professor_with_revision(
    session: AsyncSession,
    professor_id: int,
    payload: AgentProfessorUpdateRequest,
    *,
    if_revision: str | None,
) -> AgentProfessorRead:
    await _ensure_professor_revision(session, professor_id, if_revision)
    return await _update_agent_professor(session, professor_id, payload)


async def _archive_agent_professor(
    session: AsyncSession,
    professor_id: int,
) -> AgentProfessorRead:
    professor, _ = await archive_professor_record(
        session,
        professor_id,
        event_name="agent_cli.professor.archived",
        actor="agent_cli",
    )
    return _serialize_professor(professor)


async def _archive_agent_professor_with_revision(
    session: AsyncSession,
    professor_id: int,
    *,
    if_revision: str | None,
) -> AgentProfessorRead:
    await _ensure_professor_revision(session, professor_id, if_revision)
    return await _archive_agent_professor(session, professor_id)


async def _restore_agent_professor(
    session: AsyncSession,
    professor_id: int,
) -> AgentProfessorRead:
    professor, _ = await restore_professor_record(
        session,
        professor_id,
        event_name="agent_cli.professor.restored",
        actor="agent_cli",
    )
    return _serialize_professor(professor)


async def _restore_agent_professor_with_revision(
    session: AsyncSession,
    professor_id: int,
    *,
    if_revision: str | None,
) -> AgentProfessorRead:
    await _ensure_professor_revision(session, professor_id, if_revision)
    return await _restore_agent_professor(session, professor_id)


async def _set_agent_professor_tags(
    session: AsyncSession,
    professor_id: int,
    payload: AgentProfessorTagSetRequest,
) -> AgentProfessorRead:
    professor = await set_professor_tags_record(
        session,
        professor_id,
        ProfessorTagUpdatePayload.model_validate(payload.model_dump()),
        event_name="agent_cli.professor.tags_set",
        actor="agent_cli",
    )
    return _serialize_professor(professor)


async def _set_agent_professor_tags_with_revision(
    session: AsyncSession,
    professor_id: int,
    payload: AgentProfessorTagSetRequest,
    *,
    if_revision: str | None,
) -> AgentProfessorRead:
    await _ensure_professor_revision(session, professor_id, if_revision)
    return await _set_agent_professor_tags(session, professor_id, payload)


async def _create_agent_professor_tag(
    session: AsyncSession,
    payload: AgentProfessorTagCreateRequest,
) -> AgentProfessorTagRead:
    tag = await create_professor_tag_record(
        session,
        ProfessorTagPayload.model_validate(payload.model_dump()),
        event_name="agent_cli.professor.tag_created",
        actor="agent_cli",
    )
    return _serialize_tag(tag)


def _agent_professor_error(error: ProfessorMutationError) -> AgentApiError:
    return AgentApiError(
        status_code=error.status_code,
        code=error.code,
        message=error.message,
    )


def _parse_agent_professor_ids(value: str) -> list[int]:
    parts = [part.strip() for part in value.split(",")]
    if not parts or any(not part.isdigit() for part in parts):
        raise ValueError("导师 ID 列表无效")
    ids = [int(part) for part in parts]
    if any(professor_id <= 0 for professor_id in ids):
        raise ValueError("导师 ID 必须为正整数")
    if len(ids) > 500:
        raise ValueError("一次最多导出 500 位导师")
    if len(ids) != len(set(ids)):
        raise ValueError("导师 ID 不能重复")
    return ids


def _agent_community_mentor_error(error: CommunityDataError) -> AgentApiError:
    if error.code in {
        "COMMUNITY_DATA_VERSION_CHANGED",
        "COMMUNITY_DATA_REQUIRES_NEWER_APP",
        "COMMUNITY_DATA_IDENTITY_CONFLICT",
        "COMMUNITY_DATA_LIFECYCLE_BLOCKED",
        "COMMUNITY_DATA_PREVIEW_STALE",
    }:
        status_code = 409
    elif error.code in {
        "COMMUNITY_DATA_SELECTION_INVALID",
        "COMMUNITY_DATA_PATH_INVALID",
        "COMMUNITY_DATA_CONFIG_INVALID",
        "COMMUNITY_DATA_FIELD_CHOICE_INVALID",
        "COMMUNITY_DATA_TOO_LARGE",
    }:
        status_code = 400
    elif error.code == "COMMUNITY_DATA_UNAVAILABLE":
        status_code = 503
    else:
        status_code = 502
    return AgentApiError(
        status_code=status_code,
        code=error.code,
        message=str(error),
    )


async def _create_agent_template(
    session: AsyncSession,
    payload: AgentTemplateCreateRequest,
) -> AgentTemplateRead:
    template = await create_outreach_template_record(
        session,
        OutreachTemplateCreate.model_validate(payload.model_dump()),
        event_name="agent_cli.template.created",
        actor="agent_cli",
    )
    return _serialize_template(template)


async def _update_agent_template(
    session: AsyncSession,
    template_id: int,
    payload: AgentTemplateUpdateRequest,
) -> AgentTemplateRead:
    template = await update_outreach_template_record(
        session,
        template_id,
        OutreachTemplateUpdate.model_validate(payload.model_dump(exclude_unset=True)),
        event_name="agent_cli.template.updated",
        actor="agent_cli",
    )
    return _serialize_template(template)


async def _ensure_template_revision(
    session: AsyncSession,
    template_id: int,
    if_revision: str | None,
) -> None:
    if not if_revision:
        return
    template = await session.get(OutreachTemplate, template_id)
    if template is None:
        raise OutreachTemplateMutationError(404, "TEMPLATE_NOT_FOUND", "未找到邮件模板")
    current = _serialize_template(template)
    ensure_revision(
        if_revision,
        current.revision,
        resource="templates",
        resource_id=template_id,
        latest=current.model_dump(mode="json"),
    )


async def _update_agent_template_with_revision(
    session: AsyncSession,
    template_id: int,
    payload: AgentTemplateUpdateRequest,
    *,
    if_revision: str | None,
) -> AgentTemplateRead:
    await _ensure_template_revision(session, template_id, if_revision)
    return await _update_agent_template(session, template_id, payload)


async def _duplicate_agent_template(
    session: AsyncSession,
    template_id: int,
) -> AgentTemplateRead:
    template = await duplicate_outreach_template_record(
        session,
        template_id,
        event_name="agent_cli.template.duplicated",
        actor="agent_cli",
    )
    return _serialize_template(template)


async def _set_agent_template_default(
    session: AsyncSession,
    template_id: int,
) -> AgentTemplateRead:
    template = await set_default_outreach_template_record(
        session,
        template_id,
        event_name="agent_cli.template.default_set",
        actor="agent_cli",
    )
    return _serialize_template(template)


async def _set_agent_template_default_with_revision(
    session: AsyncSession,
    template_id: int,
    *,
    if_revision: str | None,
) -> AgentTemplateRead:
    await _ensure_template_revision(session, template_id, if_revision)
    return await _set_agent_template_default(session, template_id)


async def _restore_agent_template(
    session: AsyncSession,
    template_id: int,
) -> AgentTemplateRead:
    template = await restore_outreach_template_record(
        session,
        template_id,
        event_name="agent_cli.template.restored",
        actor="agent_cli",
    )
    return _serialize_template(template)


async def _restore_agent_template_with_revision(
    session: AsyncSession,
    template_id: int,
    *,
    if_revision: str | None,
) -> AgentTemplateRead:
    await _ensure_template_revision(session, template_id, if_revision)
    return await _restore_agent_template(session, template_id)


def _agent_template_error(error: OutreachTemplateMutationError) -> AgentApiError:
    return AgentApiError(
        status_code=error.status_code,
        code=error.code,
        message=error.message,
    )


def _agent_operation_log_filters(
    *,
    level: str | None,
    category: str | None,
    event_name: str | None,
    request_id: str | None,
    entity_type: str | None,
    entity_id: str | None,
    start_at: datetime | None,
    end_at: datetime | None,
) -> list[object]:
    filters: list[object] = []
    if level is not None:
        filters.append(OperationLog.level == level)
    if category is not None:
        filters.append(OperationLog.category == category)
    if event_name is not None:
        filters.append(OperationLog.event_name == event_name)
    if request_id is not None:
        filters.append(OperationLog.request_id == request_id)
    if entity_type is not None:
        filters.append(OperationLog.entity_type == entity_type)
    if entity_id is not None:
        filters.append(OperationLog.entity_id == entity_id)
    if start_at is not None:
        filters.append(OperationLog.created_at >= start_at)
    if end_at is not None:
        filters.append(OperationLog.created_at < end_at)
    return filters


def _serialize_agent_operation_log(log: OperationLog) -> OperationLogRead:
    metadata = sanitize_diagnostic_metadata(log.event_metadata)
    return OperationLogRead(
        id=log.id,
        request_id=log.request_id,
        category=log.category,
        event_name=log.event_name,
        level=log.level,
        message=sanitize_diagnostic_text(log.message) or None,
        entity_type=log.entity_type,
        entity_id=log.entity_id,
        metadata=metadata if isinstance(metadata, dict | list) else None,
        created_at=log.created_at,
    )


def _read_agent_startup_logs() -> list[DiagnosticFileRead]:
    log_dir = get_settings().data_dir / "logs"
    diagnostic_files: list[DiagnosticFileRead] = []
    for log_name in ("startup.log", "backend-errors.log"):
        log_path = log_dir / log_name
        if not log_path.is_file():
            continue
        try:
            content = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        diagnostic_files.append(
            DiagnosticFileRead(
                name=log_path.name,
                relative_path=f"logs/{log_path.name}",
                content=sanitize_diagnostic_text(content),
            ),
        )
    return diagnostic_files


async def _query_threads(
    session: AsyncSession,
    *,
    identity_id: int | None,
    professor_id: int | None,
    sent: bool | None,
    replied: bool | None,
    cursor: int,
    limit: int,
) -> Sequence[object]:
    sent_count = func.sum(case((EmailLog.direction == "sent", 1), else_=0))
    received_count = func.sum(case((EmailLog.direction == "received", 1), else_=0))
    last_message_at = func.max(EmailLog.created_at)
    statement = (
        select(
            EmailLog.identity_id.label("identity_id"),
            IdentityProfile.name.label("identity_name"),
            IdentityProfile.email_address.label("identity_email_address"),
            EmailLog.professor_id.label("professor_id"),
            Professor.name.label("professor_name"),
            Professor.email.label("professor_email"),
            sent_count.label("sent_count"),
            received_count.label("received_count"),
            last_message_at.label("last_message_at"),
        )
        .join(IdentityProfile, IdentityProfile.id == EmailLog.identity_id)
        .join(Professor, Professor.id == EmailLog.professor_id)
        .where(
            EmailLog.direction.in_(["sent", "received"]),
            EmailLog.record_state == EmailLogRecordState.CANONICAL.value,
        )
        .group_by(
            EmailLog.identity_id,
            IdentityProfile.name,
            IdentityProfile.email_address,
            EmailLog.professor_id,
            Professor.name,
            Professor.email,
        )
    )
    if identity_id is not None:
        statement = statement.where(EmailLog.identity_id == identity_id)
    if professor_id is not None:
        statement = statement.where(EmailLog.professor_id == professor_id)
    if sent is not None:
        statement = statement.having(sent_count > 0 if sent else sent_count == 0)
    if replied is not None:
        statement = statement.having(received_count > 0 if replied else received_count == 0)
    result = await session.execute(
        statement.order_by(
            last_message_at.desc(),
            EmailLog.identity_id.asc(),
            EmailLog.professor_id.asc(),
        )
        .offset(cursor)
        .limit(limit + 1),
    )
    return result.all()


def _slice_page(
    items: Sequence[PageItem],
    *,
    cursor: int,
    limit: int,
) -> tuple[Sequence[PageItem], str | None, bool]:
    has_more = len(items) > limit
    page = items[:limit]
    next_cursor = str(cursor + len(page)) if has_more else None
    return page, next_cursor, has_more


def _project_agent_collection_response(
    response: BaseModel,
    fields: str | None,
) -> BaseModel | Response:
    """Apply an additive DTO-only projection for Agent collection reads."""

    if fields is None:
        return response
    selected = list(
        dict.fromkeys(
            field.strip()
            for field in fields.split(",")
            if field.strip()
        ),
    )
    if not selected or any(
        len(field) > 100 or not field.replace("_", "").isalnum()
        for field in selected
    ):
        raise AgentApiError(
            status_code=422,
            code="INVALID_FIELD_SELECTION",
            message="fields 必须是非空、逗号分隔的 DTO 字段名。",
        )
    payload = response.model_dump(mode="json")
    collection_key = next(
        (
            key
            for key in ("items", "records")
            if isinstance(payload.get(key), list)
        ),
        None,
    )
    if collection_key is None:
        raise AgentApiError(
            status_code=422,
            code="FIELD_SELECTION_NOT_SUPPORTED",
            message="当前响应不是可投影集合。",
        )
    payload[collection_key] = [
        {
            field: item[field]
            for field in selected
            if isinstance(item, dict) and field in item
        }
        if isinstance(item, dict)
        else item
        for item in payload[collection_key]
    ]
    return JSONResponse(content=payload)


def _parse_thread_id(thread_id: str) -> tuple[int, int]:
    identity_raw, separator, professor_raw = thread_id.partition(":")
    try:
        identity_id = int(identity_raw)
        professor_id = int(professor_raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="通信线程 ID 无效") from exc
    if not separator or identity_id < 1 or professor_id < 1:
        raise HTTPException(status_code=400, detail="通信线程 ID 无效")
    return identity_id, professor_id


def _serialize_tag(tag: ProfessorTag) -> AgentProfessorTagRead:
    return AgentProfessorTagRead(
        id=tag.id,
        name=tag.name,
        text_color=tag.text_color,
        background_color=tag.background_color,
    )


def _serialize_crawl_candidate(candidate: CrawlCandidate | object) -> AgentCrawlCandidateRead:
    result = AgentCrawlCandidateRead.model_validate(candidate)
    return result.model_copy(update={"revision": revision_for(result)})


def _serialize_professor(professor: Professor) -> AgentProfessorRead:
    result = AgentProfessorRead(
        id=professor.id,
        name=professor.name,
        email=professor.email,
        title=professor.title,
        university=professor.university,
        school=professor.school,
        department=professor.department,
        research_direction=professor.research_direction,
        recent_papers=professor.recent_papers or [],
        profile_url=professor.profile_url,
        source_url=professor.source_url,
        crawl_status=professor.crawl_status,
        skip_reason=professor.skip_reason,
        personal_note=professor.personal_note,
        archived_at=professor.archived_at,
        created_at=professor.created_at,
        updated_at=professor.updated_at,
        tags=[_serialize_tag(tag) for tag in professor.tags],
    )
    return result.model_copy(update={"revision": revision_for(result)})


def _serialize_thread_row(row: object) -> AgentCommunicationThreadRead:
    sent_count = int(getattr(row, "sent_count") or 0)
    received_count = int(getattr(row, "received_count") or 0)
    identity_id = int(getattr(row, "identity_id"))
    professor_id = int(getattr(row, "professor_id"))
    return AgentCommunicationThreadRead(
        id=f"{identity_id}:{professor_id}",
        identity_id=identity_id,
        identity_name=str(getattr(row, "identity_name")),
        identity_email_address=str(getattr(row, "identity_email_address")),
        professor_id=professor_id,
        professor_name=str(getattr(row, "professor_name")),
        professor_email=getattr(row, "professor_email"),
        sent_count=sent_count,
        received_count=received_count,
        has_sent=sent_count > 0,
        has_reply=received_count > 0,
        last_message_at=getattr(row, "last_message_at"),
    )


def _serialize_agent_workspace_thread(
    workspace: WorkspaceThreadRead,
) -> AgentWorkspaceThreadRead:
    def serialize_identity(identity: object) -> dict[str, object]:
        return {
            "id": getattr(identity, "id"),
            "name": getattr(identity, "name"),
            "profile_name": getattr(identity, "profile_name"),
            "sender_name": getattr(identity, "sender_name"),
            "email_address": getattr(identity, "email_address"),
        }

    def serialize_material(material: object | None) -> dict[str, object] | None:
        if material is None:
            return None
        return {
            "id": getattr(material, "id"),
            "display_name": getattr(material, "display_name"),
            "original_filename": getattr(material, "original_filename"),
            "mime_type": getattr(material, "mime_type"),
            "size_bytes": getattr(material, "size_bytes"),
            "material_type": getattr(material, "material_type"),
            "is_primary": getattr(material, "is_primary"),
            "created_at": getattr(material, "created_at"),
        }

    def sanitize_optional_error(value: object | None) -> str | None:
        if value is None or not str(value).strip():
            return None
        return sanitize_user_visible_error(value)

    task = workspace.current_task
    draft = task.draft
    return AgentWorkspaceThreadRead.model_validate(
        {
            "professor": {
                "id": workspace.professor.id,
                "name": workspace.professor.name,
                "email": workspace.professor.email,
                "title": workspace.professor.title,
                "university": workspace.professor.university,
                "school": workspace.professor.school,
                "research_direction": workspace.professor.research_direction,
                "recent_papers": workspace.professor.recent_papers,
                "profile_url": workspace.professor.profile_url,
            },
            "identity": serialize_identity(workspace.identity),
            "llm_profile": {
                "id": workspace.llm_profile.id,
                "name": workspace.llm_profile.name,
                "provider": workspace.llm_profile.provider,
                "model_name": workspace.llm_profile.model_name,
            },
            "material_options": [
                serialize_material(material) for material in workspace.material_options
            ],
            "current_task": {
                "id": task.id,
                "source": task.source,
                "batch_task_id": task.batch_task_id,
                "parent_task_id": task.parent_task_id,
                "status": task.status,
                "cancellation_reason": task.cancellation_reason,
                "can_continue_manually": task.can_continue_manually,
                "can_write_follow_up": task.can_write_follow_up,
                "outreach_template_id": task.outreach_template_id,
                "outreach_generation_mode": task.outreach_generation_mode,
                "outreach_template_subject": task.outreach_template_subject,
                "outreach_template_body_text": task.outreach_template_body_text,
                "outreach_template_body_html": task.outreach_template_body_html,
                "rendered_template_subject": task.rendered_template_subject,
                "rendered_template_body_text": task.rendered_template_body_text,
                "rendered_template_body_html": task.rendered_template_body_html,
                "match_score": task.match_score,
                "match_reason": task.match_reason,
                "fit_points": task.fit_points,
                "risk_points": task.risk_points,
                "match_keywords": task.match_keywords,
                "generated_subject": task.generated_subject,
                "generated_content_text": task.generated_content_text,
                "generated_content_html": task.generated_content_html,
                "approved_subject": task.approved_subject,
                "approved_body_text": task.approved_body_text,
                "approved_body_html": task.approved_body_html,
                "primary_material_id": task.primary_material_id,
                "primary_material": serialize_material(task.primary_material),
                "selected_material_ids": task.selected_material_ids,
                "approved_at": task.approved_at,
                "scheduled_at": task.scheduled_at,
                "last_send_attempt_at": task.last_send_attempt_at,
                "sent_at": task.sent_at,
                "last_rfc_message_id": task.last_rfc_message_id,
                "retry_count": task.retry_count,
                "last_error": sanitize_optional_error(task.last_error),
                "is_replied": task.is_replied,
                "estimated_prompt_tokens": task.estimated_prompt_tokens,
                "estimated_completion_tokens_upper_bound": (
                    task.estimated_completion_tokens_upper_bound
                ),
                "estimated_total_tokens_upper_bound": task.estimated_total_tokens_upper_bound,
                "last_draft_prompt_tokens": task.last_draft_prompt_tokens,
                "last_draft_completion_tokens": task.last_draft_completion_tokens,
                "last_draft_total_tokens": task.last_draft_total_tokens,
                "draft": {
                    "subject": draft.subject,
                    "body_text": draft.body_text,
                    "body_html": draft.body_html,
                    "source": draft.source,
                    "sendable": draft.sendable,
                    "editable": draft.editable,
                },
            },
            "match_source_identity": serialize_identity(
                workspace.match_source_identity,
            ),
            "match_source_material_id": workspace.match_source_material_id,
            "match_source_material_name": workspace.match_source_material_name,
            "match_result_id": workspace.match_result_id,
            "match_analyzed_at": workspace.match_analyzed_at,
            "match_uses_group_source": workspace.match_uses_group_source,
            "match_is_stale": workspace.match_is_stale,
            "messages": [
                {
                    "id": message.id,
                    "direction": message.direction,
                    "subject": message.subject,
                    "content": message.content,
                    "content_html": message.content_html,
                    "rfc_message_id": message.rfc_message_id,
                    "failure_summary": sanitize_optional_error(message.failure_summary),
                    "delivery_status": message.delivery_status,
                    "prompt_tokens": message.prompt_tokens,
                    "completion_tokens": message.completion_tokens,
                    "total_tokens": message.total_tokens,
                    "created_at": message.created_at,
                    "source_identities": [
                        serialize_identity(identity)
                        for identity in message.source_identities
                    ],
                }
                for message in workspace.messages
            ],
            "communication_scope": [
                serialize_identity(identity) for identity in workspace.communication_scope
            ],
            "sync_warnings": [
                {
                    "identity_id": warning.identity_id,
                    "identity_name": warning.identity_name,
                    "message": sanitize_user_visible_error(warning.message),
                }
                for warning in workspace.sync_warnings
            ],
        },
    )


def _serialize_message(message: EmailLog, *, include_body: bool) -> AgentMessageRead:
    return AgentMessageRead(
        id=message.id,
        thread_id=f"{message.identity_id}:{message.professor_id}",
        email_task_id=message.email_task_id,
        identity_id=message.identity_id,
        professor_id=message.professor_id,
        direction=message.direction,  # type: ignore[arg-type]
        subject=message.subject,
        content=message.content if include_body else None,
        content_html=message.content_html if include_body else None,
        body_included=include_body,
        from_email=message.from_email,
        to_emails=message.to_emails or [],
        cc_emails=message.cc_emails or [],
        bcc_emails=message.bcc_emails or [],
        rfc_message_id=message.rfc_message_id,
        failure_summary=message.failure_summary,
        created_at=message.created_at,
    )


def _serialize_identity(identity: IdentityProfile) -> AgentIdentityRead:
    result = AgentIdentityRead(
        id=identity.id,
        name=identity.name,
        profile_name=identity.profile_name,
        sender_name=identity.sender_name,
        email_address=identity.email_address,
        default_language=identity.default_language,
        outreach_generation_mode=identity.outreach_generation_mode,
        default_outreach_template_id=identity.default_outreach_template_id,
        current_primary_material_id=identity.current_primary_material_id,
        communication_group_id=identity.communication_group_id,
        match_threshold=identity.match_threshold,
        daily_send_limit=identity.daily_send_limit,
        send_interval_min=identity.send_interval_min,
        send_interval_max=identity.send_interval_max,
        same_domain_cooldown_minutes=identity.same_domain_cooldown_minutes,
        smtp_configured=bool(
            identity.smtp_host and identity.smtp_username and identity.smtp_password
        ),
        imap_configured=_identity_has_imap_config(identity),
        is_default=identity.is_default,
        created_at=identity.created_at,
        updated_at=identity.updated_at,
    )
    return result.model_copy(update={"revision": revision_for(result)})


def _identity_has_imap_config(identity: IdentityProfile) -> bool:
    return bool(
        identity.imap_host
        and str(identity.imap_host).strip()
        and identity.imap_port
        and identity.imap_username
        and str(identity.imap_username).strip()
        and identity.imap_password
    )


def _serialize_llm_profile(profile: LLMProfile) -> AgentLLMProfileRead:
    result = AgentLLMProfileRead(
        id=profile.id,
        name=profile.name,
        provider=profile.provider,
        model_name=profile.model_name,
        temperature=profile.temperature,
        max_tokens=profile.max_tokens,
        credential_configured=bool(profile.api_key),
        is_default=profile.is_default,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )
    return result.model_copy(update={"revision": revision_for(result)})


def _serialize_match_analysis_job(job: MatchAnalysisJob) -> AgentMatchAnalysisJobRead:
    return AgentMatchAnalysisJobRead(
        id=job.id,
        name=job.name,
        status=job.status,
        target_count=job.target_count,
        succeeded_count=job.succeeded_count,
        failed_count=job.failed_count,
        skipped_count=job.skipped_count,
        total_prompt_tokens=job.total_prompt_tokens,
        total_completion_tokens=job.total_completion_tokens,
        total_cached_tokens=job.total_cached_tokens,
        total_tokens=job.total_tokens,
        identity_id=job.identity_id,
        match_source_identity_id=job.match_source_identity_id,
        llm_profile_id=job.llm_profile_id,
        cancel_requested_at=job.cancel_requested_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
        deleted_at=job.deleted_at,
        last_error=job.last_error,
    )


def _serialize_match_analysis_job_item(
    item: MatchAnalysisJobItem,
) -> AgentMatchAnalysisJobItemRead:
    return AgentMatchAnalysisJobItemRead(
        id=item.id,
        job_id=item.job_id,
        professor_id=item.professor_id,
        professor_name=item.professor.name,
        professor_email=item.professor.email,
        professor_title=item.professor.title,
        professor_university=item.professor.university,
        professor_school=item.professor.school,
        email_task_id=item.email_task_id,
        status=item.status,
        match_score=match_analysis_job_item_score(item),
        match_analysis_run_id=item.match_analysis_run_id,
        error_message=item.error_message,
        skip_reason=item.skip_reason,
        prompt_tokens=item.prompt_tokens,
        completion_tokens=item.completion_tokens,
        cached_tokens=item.cached_tokens,
        total_tokens=item.total_tokens,
        started_at=item.started_at,
        finished_at=item.finished_at,
        updated_at=item.updated_at,
    )


def _serialize_material(
    material: IdentityMaterial,
    *,
    include_text: bool,
    primary_material_id: int | None = None,
    target_identity_id: int | None = None,
    default_for_identity_ids: list[int] | None = None,
) -> AgentMaterialRead:
    resolved_default_identity_ids = sorted(set(default_for_identity_ids or []))
    if primary_material_id == material.id and target_identity_id is not None:
        resolved_default_identity_ids = sorted(
            set(resolved_default_identity_ids) | {target_identity_id},
        )
    is_primary = (
        target_identity_id in resolved_default_identity_ids
        if target_identity_id is not None
        else primary_material_id == material.id or bool(resolved_default_identity_ids)
    )
    result = AgentMaterialRead(
        id=material.id,
        source_identity_id=material.identity_id,
        identity_id=material.identity_id,
        display_name=material.display_name,
        original_filename=material.original_filename,
        mime_type=material.mime_type,
        size_bytes=material.size_bytes,
        material_type=material.material_type,
        is_primary=is_primary,
        default_for_identity_ids=resolved_default_identity_ids,
        has_extracted_text=bool(material.extracted_text),
        extracted_text=material.extracted_text if include_text else None,
        created_at=material.created_at,
    )
    return result.model_copy(update={"revision": revision_for(result)})


def _serialize_template(template: OutreachTemplate) -> AgentTemplateRead:
    result = AgentTemplateRead(
        id=template.id,
        name=template.name,
        recommended_generation_mode=template.recommended_generation_mode,
        subject=template.subject,
        body_text=template.body_text,
        body_html=template.body_html,
        is_default=template.is_default,
        archived_at=template.archived_at,
        created_at=template.created_at,
        updated_at=template.updated_at,
    )
    return result.model_copy(update={"revision": revision_for(result)})


def _serialize_draft(task: EmailTask) -> AgentDraftRead:
    raw_mode = (task.outreach_generation_mode or "llm").lower()
    generation_mode: Literal["template", "ai_rewrite", "manual"]
    if raw_mode == "template":
        generation_mode = "template"
    elif raw_mode == "manual":
        generation_mode = "manual"
    else:
        generation_mode = "ai_rewrite"
    result = AgentDraftRead(
        task_id=task.id,
        source=task.source,
        batch_task_id=task.batch_task_id,
        parent_task_id=task.parent_task_id,
        identity_id=task.identity_id,
        professor_id=task.professor_id,
        professor_name=task.professor.name,
        professor_email=task.professor.email,
        llm_profile_id=task.llm_profile_id,
        status=task.status,
        generation_mode=generation_mode,
        template_id=task.outreach_template_id,
        reference_material_id=task.primary_material_id,
        attachment_material_ids=task.selected_material_ids or [],
        generated_subject=task.generated_subject,
        generated_body_text=task.generated_content_text,
        generated_body_html=task.generated_content_html,
        approved_subject=task.approved_subject,
        approved_body_text=task.approved_body_text,
        approved_body_html=task.approved_body_html,
        approved_at=task.approved_at,
        scheduled_at=task.scheduled_at,
        sent_at=task.sent_at,
        last_error=task.last_error,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )
    return result.model_copy(update={"revision": revision_for(result)})


async def _ensure_draft_revision(task_id: int, if_revision: str | None) -> None:
    if not if_revision:
        return
    session_factory = get_session_factory()
    async with session_factory() as session:
        task = await session.scalar(
            select(EmailTask)
            .options(selectinload(EmailTask.professor))
            .where(EmailTask.id == task_id),
        )
        if task is None:
            raise AgentApiError(
                status_code=404,
                code="DRAFT_NOT_FOUND",
                message="未找到邮件任务。",
            )
        current = _serialize_draft(task)
    ensure_revision(
        if_revision,
        current.revision,
        resource="drafts",
        resource_id=task_id,
        latest=current.model_dump(mode="json"),
    )


async def _ensure_identity_revision(
    session: AsyncSession,
    identity_id: int,
    if_revision: str | None,
) -> None:
    if not if_revision:
        return
    identity = await _get_agent_identity_or_raise(session, identity_id)
    current = _serialize_identity(identity)
    ensure_revision(
        if_revision,
        current.revision,
        resource="identities",
        resource_id=identity_id,
        latest=current.model_dump(mode="json"),
    )


async def _ensure_llm_profile_revision(
    session: AsyncSession,
    profile_id: int,
    if_revision: str | None,
) -> None:
    if not if_revision:
        return
    profile = await _get_agent_llm_profile_or_raise(session, profile_id)
    current = _serialize_llm_profile(profile)
    ensure_revision(
        if_revision,
        current.revision,
        resource="llm-profiles",
        resource_id=profile_id,
        latest=current.model_dump(mode="json"),
    )
