from __future__ import annotations

import secrets
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.agent_api_errors import AgentApiError
from app.core.time import as_utc_aware, serialize_api_datetime, utc_now
from app.models import (
    AgentChangePlan,
    IdentityProfile,
)
from app.modules.campaigns.public import (
    OutreachTemplateMutationError,
    get_outreach_template_or_raise,
    prepare_campaign_create_snapshot,
    prepare_campaign_restore_send_snapshot,
    prepare_campaign_resume_snapshot,
    prepare_campaign_send_snapshot,
)
from app.modules.communications.public import (
    TestComposeMessageSendRequest,
    prepare_test_compose_send_snapshot,
)
from app.modules.community.public import (
    CommunityDataError,
    CommunityImportPayload,
    CommunityMentorDataService,
)
from app.modules.crawler.public import (
    CrawlJobRecordError,
    CrawlJobRetryPayload,
    resolve_faculty_crawl_candidate_selection,
)
from app.modules.identities.public import (
    MaterialMutationError,
    prepare_material_deletion_snapshot,
)
from app.modules.professors.public import (
    ProfessorBulkTagsPayload,
    ProfessorMutationError,
    ProfessorSelectionError,
    parse_professor_import_file,
    prepare_bulk_professor_archive_snapshot,
    prepare_bulk_professor_tags_snapshot,
    prepare_professor_import_snapshot,
    prepare_professor_tag_delete_snapshot,
    resolve_professor_selection,
)
from app.schemas.agent import (
    AgentCampaignCreateRequest,
    AgentCampaignSendRequest,
    AgentChangePlanRead,
    AgentPlanExecuteRequest,
)
from app.schemas.selection import SelectionSpec
from app.services.agent_mutations import (
    fingerprint,
    normalize_idempotency_key,
)
from app.services.agent_plan_effects import resolve_agent_plan_effects
from app.services.file_storage import delete_file
from app.services.operation_logs import record_operation_log

from .agent_plan_handlers.community import (
    _community_import_error,
    _prepare_community_mentor_import_snapshot,
)
from .agent_plan_handlers.crawler import (
    _crawl_candidate_approval_snapshot_fingerprint,
    _load_approvable_crawl_job,
    _prepare_crawl_candidate_approval_snapshot,
    _prepare_crawl_job_retry_snapshot,
)
from .agent_plan_handlers.materials import (
    _material_error,
)
from .agent_plan_handlers.professors import (
    _bulk_tags_snapshot_fingerprint,
    _professor_error,
    _professor_import_error,
)
from .agent_plan_handlers.registry import execute_plan_action
from .agent_plan_handlers.shared import (
    _request_state_fingerprint,
    _request_state_summary_fingerprint,
)
from .agent_plan_handlers.templates import (
    _build_template_archive_snapshot,
    _template_error,
)
from .agent_plan_handlers.test_email import (
    _test_email_error,
)

CHANGE_PLAN_TTL = timedelta(minutes=30)
CHANGE_PLAN_AWAITING = "awaiting_confirmation"
CHANGE_PLAN_EXECUTING = "executing"
CHANGE_PLAN_EXECUTED = "executed"
CHANGE_PLAN_CANCELED = "canceled"
CHANGE_PLAN_EXPIRED = "expired"
TEMPLATE_ARCHIVE_ACTION = "template.archive"
MATERIAL_DELETE_ACTION = "material.delete"
PROFESSOR_BULK_TAGS_ACTION = "professor.tags.bulk"
PROFESSOR_BULK_ARCHIVE_ACTION = "professor.archive.bulk"
PROFESSOR_TAG_DELETE_ACTION = "professor.tag.delete"
PROFESSOR_IMPORT_ACTION = "professor.import"
CRAWL_CANDIDATE_APPROVE_ACTION = "crawler.candidates.approve"
CRAWL_JOB_RETRY_ACTION = "crawler.job.retry"
CAMPAIGN_CREATE_ACTION = "campaign.create"
CAMPAIGN_SEND_ACTION = "campaign.send"
CAMPAIGN_RESUME_ACTION = "campaign.resume"
CAMPAIGN_RESTORE_SEND_ACTION = "campaign.item_send_restore"
COMMUNITY_MENTOR_IMPORT_ACTION = "community_mentor.import"
TEST_EMAIL_SEND_ACTION = "test_email.send"


async def create_template_archive_change_plan(
    session_factory: async_sessionmaker[AsyncSession],
    template_id: int,
    *,
    idempotency_key: str | None,
) -> AgentChangePlanRead:
    normalized_key = normalize_idempotency_key(idempotency_key)
    request_fingerprint = fingerprint(
        {"action": TEMPLATE_ARCHIVE_ACTION, "template_id": template_id},
    )
    async with session_factory() as session:
        if normalized_key is not None:
            existing = await session.scalar(
                select(AgentChangePlan).where(
                    AgentChangePlan.idempotency_key == normalized_key,
                ),
            )
            if existing is not None:
                _ensure_same_idempotent_request(existing, request_fingerprint)
                await _expire_if_needed(session, existing)
                return _serialize_change_plan(existing, idempotent_replay=True)

        try:
            template = await get_outreach_template_or_raise(
                session,
                template_id,
                include_archived=True,
            )
        except OutreachTemplateMutationError as exc:
            raise _template_error(exc) from exc
        if template.archived_at is not None:
            raise AgentApiError(
                status_code=409,
                code="TEMPLATE_ALREADY_ARCHIVED",
                message="该模板已经归档，不需要再次创建归档计划。",
            )
        default_identity_count = int(
            await session.scalar(
                select(func.count(IdentityProfile.id)).where(
                    IdentityProfile.default_outreach_template_id == template.id,
                ),
            )
            or 0,
        )
        snapshot = _build_template_archive_snapshot(template, default_identity_count)
        now = utc_now()
        plan = AgentChangePlan(
            id=_new_change_plan_id(),
            action=TEMPLATE_ARCHIVE_ACTION,
            status=CHANGE_PLAN_AWAITING,
            idempotency_key=normalized_key,
            request_fingerprint=request_fingerprint,
            snapshot=snapshot,
            result=None,
            expires_at=now + CHANGE_PLAN_TTL,
            created_at=now,
            updated_at=now,
        )
        session.add(plan)
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            if normalized_key is not None:
                existing = await session.scalar(
                    select(AgentChangePlan).where(
                        AgentChangePlan.idempotency_key == normalized_key,
                    ),
                )
                if existing is not None:
                    _ensure_same_idempotent_request(existing, request_fingerprint)
                    return _serialize_change_plan(existing, idempotent_replay=True)
            raise AgentApiError(
                status_code=409,
                code="CHANGE_PLAN_CREATE_CONFLICT",
                message="变更计划创建发生冲突，请重新生成预览。",
                retryable=True,
            ) from exc
        await _record_change_plan_event(session, plan, "agent_cli.change_plan_created")
        await session.commit()
        return _serialize_change_plan(plan)


async def create_material_delete_change_plan(
    session_factory: async_sessionmaker[AsyncSession],
    material_id: int,
    *,
    idempotency_key: str | None,
) -> AgentChangePlanRead:
    normalized_key = normalize_idempotency_key(idempotency_key)
    request_fingerprint = fingerprint(
        {"action": MATERIAL_DELETE_ACTION, "material_id": material_id},
    )
    async with session_factory() as session:
        if normalized_key is not None:
            existing = await session.scalar(
                select(AgentChangePlan).where(
                    AgentChangePlan.idempotency_key == normalized_key,
                ),
            )
            if existing is not None:
                _ensure_same_idempotent_request(existing, request_fingerprint)
                await _expire_if_needed(session, existing)
                return _serialize_change_plan(existing, idempotent_replay=True)

        try:
            snapshot = await prepare_material_deletion_snapshot(session, material_id)
        except MaterialMutationError as exc:
            raise _material_error(exc) from exc
        now = utc_now()
        plan = AgentChangePlan(
            id=_new_change_plan_id(),
            action=MATERIAL_DELETE_ACTION,
            status=CHANGE_PLAN_AWAITING,
            idempotency_key=normalized_key,
            request_fingerprint=request_fingerprint,
            snapshot=snapshot,
            result=None,
            expires_at=now + CHANGE_PLAN_TTL,
            created_at=now,
            updated_at=now,
        )
        session.add(plan)
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            if normalized_key is not None:
                existing = await session.scalar(
                    select(AgentChangePlan).where(
                        AgentChangePlan.idempotency_key == normalized_key,
                    ),
                )
                if existing is not None:
                    _ensure_same_idempotent_request(existing, request_fingerprint)
                    return _serialize_change_plan(existing, idempotent_replay=True)
            raise AgentApiError(
                status_code=409,
                code="CHANGE_PLAN_CREATE_CONFLICT",
                message="变更计划创建发生冲突，请重新生成预览。",
                retryable=True,
            ) from exc
        await _record_change_plan_event(session, plan, "agent_cli.change_plan_created")
        await session.commit()
        return _serialize_change_plan(plan)


async def create_professor_bulk_tags_change_plan(
    session_factory: async_sessionmaker[AsyncSession],
    payload: ProfessorBulkTagsPayload,
    *,
    idempotency_key: str | None,
) -> AgentChangePlanRead:
    normalized_key = normalize_idempotency_key(idempotency_key)
    request_data = payload.model_dump(mode="json")
    request_fingerprint = fingerprint(
        {"action": PROFESSOR_BULK_TAGS_ACTION, "request": request_data},
    )
    async with session_factory() as session:
        if normalized_key is not None:
            existing = await session.scalar(
                select(AgentChangePlan).where(
                    AgentChangePlan.idempotency_key == normalized_key,
                ),
            )
            if existing is not None:
                _ensure_same_idempotent_request(existing, request_fingerprint)
                await _expire_if_needed(session, existing)
                return _serialize_change_plan(existing, idempotent_replay=True)

        try:
            snapshot = await prepare_bulk_professor_tags_snapshot(session, payload)
        except ProfessorMutationError as exc:
            raise _professor_error(exc) from exc
        snapshot["bulk_tags_fingerprint"] = _bulk_tags_snapshot_fingerprint(snapshot)
        now = utc_now()
        plan = AgentChangePlan(
            id=_new_change_plan_id(),
            action=PROFESSOR_BULK_TAGS_ACTION,
            status=CHANGE_PLAN_AWAITING,
            idempotency_key=normalized_key,
            request_fingerprint=request_fingerprint,
            snapshot=snapshot,
            result=None,
            expires_at=now + CHANGE_PLAN_TTL,
            created_at=now,
            updated_at=now,
        )
        session.add(plan)
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            if normalized_key is not None:
                existing = await session.scalar(
                    select(AgentChangePlan).where(
                        AgentChangePlan.idempotency_key == normalized_key,
                    ),
                )
                if existing is not None:
                    _ensure_same_idempotent_request(existing, request_fingerprint)
                    return _serialize_change_plan(existing, idempotent_replay=True)
            raise AgentApiError(
                status_code=409,
                code="CHANGE_PLAN_CREATE_CONFLICT",
                message="变更计划创建发生冲突，请重新生成预览。",
                retryable=True,
            ) from exc
        await _record_change_plan_event(session, plan, "agent_cli.change_plan_created")
        await session.commit()
        return _serialize_change_plan(plan)


async def create_professor_bulk_archive_change_plan(
    session_factory: async_sessionmaker[AsyncSession],
    selection: SelectionSpec,
    *,
    idempotency_key: str | None,
) -> AgentChangePlanRead:
    normalized_key = normalize_idempotency_key(idempotency_key)
    normalized_selection = selection.model_dump(mode="json")
    normalized_selection["ids"] = sorted(normalized_selection["ids"])
    normalized_selection["exclude_ids"] = sorted(normalized_selection["exclude_ids"])
    legacy_id_selection = selection.mode == "ids" and not selection.exclude_ids
    request_fingerprint = fingerprint(
        {
            "action": PROFESSOR_BULK_ARCHIVE_ACTION,
            **(
                {"professor_ids": selection.ids}
                if legacy_id_selection
                else {"selection": normalized_selection}
            ),
        },
    )
    async with session_factory() as session:
        if normalized_key is not None:
            existing = await session.scalar(
                select(AgentChangePlan).where(
                    AgentChangePlan.idempotency_key == normalized_key,
                ),
            )
            if existing is not None:
                _ensure_same_idempotent_request(existing, request_fingerprint)
                await _expire_if_needed(session, existing)
                return _serialize_change_plan(existing, idempotent_replay=True)
        try:
            (
                professor_ids,
                matched_count,
                excluded_count,
            ) = await resolve_professor_selection(
                session,
                selection,
            )
        except ProfessorSelectionError as exc:
            raise AgentApiError(
                status_code=exc.status_code,
                code=exc.code,
                message=exc.message,
            ) from exc
        try:
            snapshot = await prepare_bulk_professor_archive_snapshot(
                session,
                professor_ids,
            )
        except ProfessorMutationError as exc:
            raise _professor_error(exc) from exc
        snapshot["bulk_archive_fingerprint"] = _request_state_summary_fingerprint(
            snapshot
        )
        summary = snapshot.get("summary")
        if isinstance(summary, dict):
            summary["snapshot_stage"] = "preflight"
            summary["selection"] = {
                "mode": selection.mode,
                "matched_count": matched_count,
                "selected_count": len(professor_ids),
                "excluded_count": excluded_count,
                "frozen_ids_hash": fingerprint(sorted(professor_ids)),
            }
        snapshot["selection"] = normalized_selection
        return await _create_change_plan(
            session,
            action=PROFESSOR_BULK_ARCHIVE_ACTION,
            idempotency_key=normalized_key,
            request_fingerprint=request_fingerprint,
            snapshot=snapshot,
        )


async def create_professor_tag_delete_change_plan(
    session_factory: async_sessionmaker[AsyncSession],
    tag_id: int,
    *,
    idempotency_key: str | None,
) -> AgentChangePlanRead:
    normalized_key = normalize_idempotency_key(idempotency_key)
    request_fingerprint = fingerprint(
        {"action": PROFESSOR_TAG_DELETE_ACTION, "tag_id": tag_id},
    )
    async with session_factory() as session:
        if normalized_key is not None:
            existing = await session.scalar(
                select(AgentChangePlan).where(
                    AgentChangePlan.idempotency_key == normalized_key,
                ),
            )
            if existing is not None:
                _ensure_same_idempotent_request(existing, request_fingerprint)
                await _expire_if_needed(session, existing)
                return _serialize_change_plan(existing, idempotent_replay=True)
        try:
            snapshot = await prepare_professor_tag_delete_snapshot(session, tag_id)
        except ProfessorMutationError as exc:
            raise _professor_error(exc) from exc
        snapshot["tag_delete_fingerprint"] = _request_state_summary_fingerprint(
            snapshot
        )
        return await _create_change_plan(
            session,
            action=PROFESSOR_TAG_DELETE_ACTION,
            idempotency_key=normalized_key,
            request_fingerprint=request_fingerprint,
            snapshot=snapshot,
        )


async def create_professor_import_change_plan(
    session_factory: async_sessionmaker[AsyncSession],
    filename: str,
    content: bytes,
    *,
    idempotency_key: str | None,
) -> AgentChangePlanRead:
    try:
        parsed = parse_professor_import_file(filename, content)
    except ValueError as exc:
        raise _professor_import_error(exc) from exc
    normalized_key = normalize_idempotency_key(idempotency_key)
    request_fingerprint = fingerprint(
        {
            "action": PROFESSOR_IMPORT_ACTION,
            "filename": filename,
            "data": parsed.data,
            "failed_count": parsed.failed_count,
        },
    )
    async with session_factory() as session:
        if normalized_key is not None:
            existing = await session.scalar(
                select(AgentChangePlan).where(
                    AgentChangePlan.idempotency_key == normalized_key,
                ),
            )
            if existing is not None:
                _ensure_same_idempotent_request(existing, request_fingerprint)
                await _expire_if_needed(session, existing)
                return _serialize_change_plan(existing, idempotent_replay=True)

        snapshot = await prepare_professor_import_snapshot(
            session,
            parsed,
            filename=filename,
        )
        snapshot["import_fingerprint"] = _request_state_summary_fingerprint(snapshot)
        now = utc_now()
        plan = AgentChangePlan(
            id=_new_change_plan_id(),
            action=PROFESSOR_IMPORT_ACTION,
            status=CHANGE_PLAN_AWAITING,
            idempotency_key=normalized_key,
            request_fingerprint=request_fingerprint,
            snapshot=snapshot,
            result=None,
            expires_at=now + CHANGE_PLAN_TTL,
            created_at=now,
            updated_at=now,
        )
        session.add(plan)
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            if normalized_key is not None:
                existing = await session.scalar(
                    select(AgentChangePlan).where(
                        AgentChangePlan.idempotency_key == normalized_key,
                    ),
                )
                if existing is not None:
                    _ensure_same_idempotent_request(existing, request_fingerprint)
                    return _serialize_change_plan(existing, idempotent_replay=True)
            raise AgentApiError(
                status_code=409,
                code="CHANGE_PLAN_CREATE_CONFLICT",
                message="变更计划创建发生冲突，请重新生成预览。",
                retryable=True,
            ) from exc
        await _record_change_plan_event(session, plan, "agent_cli.change_plan_created")
        await session.commit()
        return _serialize_change_plan(plan)


async def create_community_mentor_import_change_plan(
    session_factory: async_sessionmaker[AsyncSession],
    payload: CommunityImportPayload,
    community_service: CommunityMentorDataService,
    *,
    idempotency_key: str | None,
) -> AgentChangePlanRead:
    normalized_key = normalize_idempotency_key(idempotency_key)
    request_data = payload.model_dump(mode="json")
    request_fingerprint = fingerprint(
        {"action": COMMUNITY_MENTOR_IMPORT_ACTION, "request": request_data},
    )
    async with session_factory() as session:
        if normalized_key is not None:
            existing = await session.scalar(
                select(AgentChangePlan).where(
                    AgentChangePlan.idempotency_key == normalized_key,
                ),
            )
            if existing is not None:
                _ensure_same_idempotent_request(existing, request_fingerprint)
                await _expire_if_needed(session, existing)
                return _serialize_change_plan(existing, idempotent_replay=True)

        try:
            snapshot, _ = await _prepare_community_mentor_import_snapshot(
                session,
                payload,
                community_service,
            )
        except CommunityDataError as exc:
            raise _community_import_error(exc) from exc
        snapshot["community_import_fingerprint"] = _request_state_fingerprint(
            snapshot,
        )
        return await _create_change_plan(
            session,
            action=COMMUNITY_MENTOR_IMPORT_ACTION,
            idempotency_key=normalized_key,
            request_fingerprint=request_fingerprint,
            snapshot=snapshot,
        )


async def create_test_email_send_change_plan(
    session_factory: async_sessionmaker[AsyncSession],
    identity_id: int,
    llm_profile_id: int,
    payload: TestComposeMessageSendRequest,
    *,
    idempotency_key: str | None,
) -> AgentChangePlanRead:
    normalized_key = normalize_idempotency_key(idempotency_key)
    request_data = {
        "identity_id": identity_id,
        "llm_profile_id": llm_profile_id,
        "payload": payload.model_dump(mode="json", exclude_unset=True),
    }
    request_fingerprint = fingerprint(
        {"action": TEST_EMAIL_SEND_ACTION, "request": request_data},
    )
    async with session_factory() as session:
        if normalized_key is not None:
            existing = await session.scalar(
                select(AgentChangePlan).where(
                    AgentChangePlan.idempotency_key == normalized_key,
                ),
            )
            if existing is not None:
                _ensure_same_idempotent_request(existing, request_fingerprint)
                await _expire_if_needed(session, existing)
                return _serialize_change_plan(existing, idempotent_replay=True)
        try:
            snapshot = await prepare_test_compose_send_snapshot(
                session,
                identity_id=identity_id,
                llm_profile_id=llm_profile_id,
                payload=payload,
            )
        except ValueError as exc:
            raise _test_email_error(exc) from exc
        snapshot["test_email_send_fingerprint"] = _request_state_summary_fingerprint(
            snapshot
        )
        warnings = snapshot.setdefault("warnings", [])
        if isinstance(warnings, list):
            warnings.append(
                "确认后会使用当前发件身份的 SMTP 配置，向该身份自己的邮箱发送一封真实测试邮件。",
            )
        return await _create_change_plan(
            session,
            action=TEST_EMAIL_SEND_ACTION,
            idempotency_key=normalized_key,
            request_fingerprint=request_fingerprint,
            snapshot=snapshot,
        )


async def create_crawl_candidate_approval_change_plan(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: int,
    selection: SelectionSpec,
    *,
    idempotency_key: str | None,
) -> AgentChangePlanRead:
    normalized_key = normalize_idempotency_key(idempotency_key)
    normalized_selection = selection.model_dump(mode="json")
    normalized_selection["ids"] = sorted(normalized_selection["ids"])
    normalized_selection["exclude_ids"] = sorted(normalized_selection["exclude_ids"])
    review_status = normalized_selection["filter"].get("review_status")
    if isinstance(review_status, list):
        normalized_selection["filter"]["review_status"] = sorted(review_status)
    request_fingerprint = fingerprint(
        {
            "action": CRAWL_CANDIDATE_APPROVE_ACTION,
            "job_id": job_id,
            "selection": normalized_selection,
        },
    )
    async with session_factory() as session:
        if normalized_key is not None:
            existing = await session.scalar(
                select(AgentChangePlan).where(
                    AgentChangePlan.idempotency_key == normalized_key,
                ),
            )
            if existing is not None:
                _ensure_same_idempotent_request(existing, request_fingerprint)
                await _expire_if_needed(session, existing)
                return _serialize_change_plan(existing, idempotent_replay=True)

        await _load_approvable_crawl_job(session, job_id)
        try:
            (
                candidates,
                excluded_count,
            ) = await resolve_faculty_crawl_candidate_selection(
                session,
                job_id=job_id,
                selection=selection,
            )
        except CrawlJobRecordError as exc:
            raise AgentApiError(
                status_code=exc.status_code,
                code=exc.code,
                message=exc.message,
            ) from exc
        if not candidates:
            raise AgentApiError(
                status_code=409,
                code="CRAWL_CANDIDATE_SELECTION_EMPTY",
                message="没有候选导师匹配当前审批选择条件。",
            )
        frozen_candidate_ids = sorted(candidate.id for candidate in candidates)
        snapshot = await _prepare_crawl_candidate_approval_snapshot(
            session,
            job_id,
            frozen_candidate_ids,
        )
        snapshot["summary"]["selection"] = {
            "mode": selection.mode,
            "matched_count": len(candidates) + excluded_count,
            "selected_count": len(candidates),
            "excluded_count": excluded_count,
            "frozen_ids_hash": fingerprint(frozen_candidate_ids),
        }
        snapshot["approval_fingerprint"] = (
            _crawl_candidate_approval_snapshot_fingerprint(snapshot)
        )
        now = utc_now()
        plan = AgentChangePlan(
            id=_new_change_plan_id(),
            action=CRAWL_CANDIDATE_APPROVE_ACTION,
            status=CHANGE_PLAN_AWAITING,
            idempotency_key=normalized_key,
            request_fingerprint=request_fingerprint,
            snapshot=snapshot,
            result=None,
            expires_at=now + CHANGE_PLAN_TTL,
            created_at=now,
            updated_at=now,
        )
        session.add(plan)
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            if normalized_key is not None:
                existing = await session.scalar(
                    select(AgentChangePlan).where(
                        AgentChangePlan.idempotency_key == normalized_key,
                    ),
                )
                if existing is not None:
                    _ensure_same_idempotent_request(existing, request_fingerprint)
                    return _serialize_change_plan(existing, idempotent_replay=True)
            raise AgentApiError(
                status_code=409,
                code="CHANGE_PLAN_CREATE_CONFLICT",
                message="变更计划创建发生冲突，请重新生成预览。",
                retryable=True,
            ) from exc
        await _record_change_plan_event(session, plan, "agent_cli.change_plan_created")
        await session.commit()
        return _serialize_change_plan(plan)


async def create_crawl_job_retry_change_plan(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: int,
    payload: CrawlJobRetryPayload,
    *,
    idempotency_key: str | None,
) -> AgentChangePlanRead:
    normalized_key = normalize_idempotency_key(idempotency_key)
    request_data = payload.model_dump(mode="json")
    request_fingerprint = fingerprint(
        {
            "action": CRAWL_JOB_RETRY_ACTION,
            "job_id": job_id,
            "request": request_data,
        },
    )
    async with session_factory() as session:
        if normalized_key is not None:
            existing = await session.scalar(
                select(AgentChangePlan).where(
                    AgentChangePlan.idempotency_key == normalized_key,
                ),
            )
            if existing is not None:
                _ensure_same_idempotent_request(existing, request_fingerprint)
                await _expire_if_needed(session, existing)
                return _serialize_change_plan(existing, idempotent_replay=True)

        snapshot = await _prepare_crawl_job_retry_snapshot(session, job_id, payload)
        snapshot["retry_fingerprint"] = _request_state_fingerprint(snapshot)
        now = utc_now()
        plan = AgentChangePlan(
            id=_new_change_plan_id(),
            action=CRAWL_JOB_RETRY_ACTION,
            status=CHANGE_PLAN_AWAITING,
            idempotency_key=normalized_key,
            request_fingerprint=request_fingerprint,
            snapshot=snapshot,
            result=None,
            expires_at=now + CHANGE_PLAN_TTL,
            created_at=now,
            updated_at=now,
        )
        session.add(plan)
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            if normalized_key is not None:
                existing = await session.scalar(
                    select(AgentChangePlan).where(
                        AgentChangePlan.idempotency_key == normalized_key,
                    ),
                )
                if existing is not None:
                    _ensure_same_idempotent_request(existing, request_fingerprint)
                    return _serialize_change_plan(existing, idempotent_replay=True)
            raise AgentApiError(
                status_code=409,
                code="CHANGE_PLAN_CREATE_CONFLICT",
                message="变更计划创建发生冲突，请重新生成预览。",
                retryable=True,
            ) from exc
        await _record_change_plan_event(session, plan, "agent_cli.change_plan_created")
        await session.commit()
        return _serialize_change_plan(plan)


async def create_campaign_create_change_plan(
    session_factory: async_sessionmaker[AsyncSession],
    payload: AgentCampaignCreateRequest,
    *,
    idempotency_key: str | None,
) -> AgentChangePlanRead:
    normalized_key = normalize_idempotency_key(idempotency_key)
    request_data = payload.model_dump(mode="json")
    request_fingerprint = fingerprint(
        {"action": CAMPAIGN_CREATE_ACTION, "request": request_data},
    )
    async with session_factory() as session:
        if normalized_key is not None:
            existing = await session.scalar(
                select(AgentChangePlan).where(
                    AgentChangePlan.idempotency_key == normalized_key,
                ),
            )
            if existing is not None:
                _ensure_same_idempotent_request(existing, request_fingerprint)
                await _expire_if_needed(session, existing)
                return _serialize_change_plan(existing, idempotent_replay=True)

        snapshot = await prepare_campaign_create_snapshot(session, payload)
        return await _create_change_plan(
            session,
            action=CAMPAIGN_CREATE_ACTION,
            idempotency_key=normalized_key,
            request_fingerprint=request_fingerprint,
            snapshot=snapshot,
        )


async def create_campaign_send_change_plan(
    session_factory: async_sessionmaker[AsyncSession],
    campaign_id: int,
    payload: AgentCampaignSendRequest,
    *,
    idempotency_key: str | None,
) -> AgentChangePlanRead:
    normalized_key = normalize_idempotency_key(idempotency_key)
    request_data = {"campaign_id": campaign_id, **payload.model_dump(mode="json")}
    request_fingerprint = fingerprint(
        {"action": CAMPAIGN_SEND_ACTION, "request": request_data},
    )
    async with session_factory() as session:
        if normalized_key is not None:
            existing = await session.scalar(
                select(AgentChangePlan).where(
                    AgentChangePlan.idempotency_key == normalized_key,
                ),
            )
            if existing is not None:
                _ensure_same_idempotent_request(existing, request_fingerprint)
                await _expire_if_needed(session, existing)
                return _serialize_change_plan(existing, idempotent_replay=True)

        snapshot = await prepare_campaign_send_snapshot(session, campaign_id, payload)
        return await _create_change_plan(
            session,
            action=CAMPAIGN_SEND_ACTION,
            idempotency_key=normalized_key,
            request_fingerprint=request_fingerprint,
            snapshot=snapshot,
        )


async def create_campaign_resume_change_plan(
    session_factory: async_sessionmaker[AsyncSession],
    campaign_id: int,
    *,
    idempotency_key: str | None,
) -> AgentChangePlanRead:
    normalized_key = normalize_idempotency_key(idempotency_key)
    request_fingerprint = fingerprint(
        {"action": CAMPAIGN_RESUME_ACTION, "campaign_id": campaign_id},
    )
    async with session_factory() as session:
        if normalized_key is not None:
            existing = await session.scalar(
                select(AgentChangePlan).where(
                    AgentChangePlan.idempotency_key == normalized_key,
                ),
            )
            if existing is not None:
                _ensure_same_idempotent_request(existing, request_fingerprint)
                await _expire_if_needed(session, existing)
                return _serialize_change_plan(existing, idempotent_replay=True)
        snapshot = await prepare_campaign_resume_snapshot(session, campaign_id)
        return await _create_change_plan(
            session,
            action=CAMPAIGN_RESUME_ACTION,
            idempotency_key=normalized_key,
            request_fingerprint=request_fingerprint,
            snapshot=snapshot,
        )


async def create_campaign_restore_send_change_plan(
    session_factory: async_sessionmaker[AsyncSession],
    campaign_id: int,
    item_id: int,
    *,
    idempotency_key: str | None,
) -> AgentChangePlanRead:
    normalized_key = normalize_idempotency_key(idempotency_key)
    request_fingerprint = fingerprint(
        {
            "action": CAMPAIGN_RESTORE_SEND_ACTION,
            "campaign_id": campaign_id,
            "item_id": item_id,
        },
    )
    async with session_factory() as session:
        if normalized_key is not None:
            existing = await session.scalar(
                select(AgentChangePlan).where(
                    AgentChangePlan.idempotency_key == normalized_key,
                ),
            )
            if existing is not None:
                _ensure_same_idempotent_request(existing, request_fingerprint)
                await _expire_if_needed(session, existing)
                return _serialize_change_plan(existing, idempotent_replay=True)
        snapshot = await prepare_campaign_restore_send_snapshot(
            session,
            campaign_id,
            item_id,
        )
        return await _create_change_plan(
            session,
            action=CAMPAIGN_RESTORE_SEND_ACTION,
            idempotency_key=normalized_key,
            request_fingerprint=request_fingerprint,
            snapshot=snapshot,
        )


async def _create_change_plan(
    session: AsyncSession,
    *,
    action: str,
    idempotency_key: str | None,
    request_fingerprint: str,
    snapshot: dict[str, object],
) -> AgentChangePlanRead:
    now = utc_now()
    plan = AgentChangePlan(
        id=_new_change_plan_id(),
        action=action,
        status=CHANGE_PLAN_AWAITING,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        snapshot=snapshot,
        result=None,
        expires_at=now + CHANGE_PLAN_TTL,
        created_at=now,
        updated_at=now,
    )
    session.add(plan)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        if idempotency_key is not None:
            existing = await session.scalar(
                select(AgentChangePlan).where(
                    AgentChangePlan.idempotency_key == idempotency_key,
                ),
            )
            if existing is not None:
                _ensure_same_idempotent_request(existing, request_fingerprint)
                return _serialize_change_plan(existing, idempotent_replay=True)
        raise AgentApiError(
            status_code=409,
            code="CHANGE_PLAN_CREATE_CONFLICT",
            message="变更计划创建发生冲突，请重新生成预览。",
            retryable=True,
        ) from exc
    await _record_change_plan_event(session, plan, "agent_cli.change_plan_created")
    await session.commit()
    return _serialize_change_plan(plan)


async def get_change_plan(
    session_factory: async_sessionmaker[AsyncSession],
    plan_id: str,
) -> AgentChangePlanRead:
    async with session_factory() as session:
        plan = await _get_change_plan_or_raise(session, plan_id)
        await _expire_if_needed(session, plan)
        return _serialize_change_plan(plan)


async def cancel_change_plan(
    session_factory: async_sessionmaker[AsyncSession],
    plan_id: str,
) -> AgentChangePlanRead:
    async with session_factory() as session:
        plan = await _get_change_plan_or_raise(session, plan_id)
        await _expire_if_needed(session, plan)
        if plan.status == CHANGE_PLAN_CANCELED:
            return _serialize_change_plan(plan, idempotent_replay=True)
        if plan.status != CHANGE_PLAN_AWAITING:
            raise AgentApiError(
                status_code=409,
                code="CHANGE_PLAN_NOT_CANCELABLE",
                message=f"当前计划状态为 {plan.status}，不能取消。",
            )
        now = utc_now()
        plan.status = CHANGE_PLAN_CANCELED
        plan.canceled_at = now
        plan.updated_at = now
        await _record_change_plan_event(session, plan, "agent_cli.change_plan_canceled")
        await session.commit()
        return _serialize_change_plan(plan)


async def execute_change_plan(
    session_factory: async_sessionmaker[AsyncSession],
    plan_id: str,
    payload: AgentPlanExecuteRequest,
    *,
    community_service: CommunityMentorDataService | None = None,
    community_service_factory: Callable[[], CommunityMentorDataService] | None = None,
) -> AgentChangePlanRead:
    if not payload.confirm:
        raise AgentApiError(
            status_code=409,
            code="PLAN_CONFIRMATION_REQUIRED",
            message="尚未执行。请向用户展示计划，并在用户明确确认后使用 --confirm。",
            suggested_command=f"auto-email-sender plans show {plan_id}",
        )

    file_path_to_delete: str | None = None
    async with session_factory() as session:
        plan = await _get_change_plan_or_raise(session, plan_id)
        await _expire_if_needed(session, plan)
        if plan.status == CHANGE_PLAN_EXECUTED:
            return _serialize_change_plan(plan, idempotent_replay=True)
        if plan.status == CHANGE_PLAN_CANCELED:
            raise AgentApiError(
                status_code=409,
                code="CHANGE_PLAN_CANCELED",
                message="该变更计划已经取消，请重新生成计划。",
            )
        if plan.status == CHANGE_PLAN_EXPIRED:
            raise _change_plan_expired_error(plan)
        if plan.status == CHANGE_PLAN_EXECUTING:
            raise AgentApiError(
                status_code=409,
                code="CHANGE_PLAN_EXECUTION_IN_PROGRESS",
                message="该变更计划正在执行，请稍后再次查看计划状态。",
                retryable=True,
                suggested_command=f"auto-email-sender plans show {plan.id}",
            )
        content_fingerprint = _change_plan_content_fingerprint(plan)
        if (
            payload.confirmed_fingerprint is not None
            and payload.confirmed_fingerprint != content_fingerprint
        ):
            raise AgentApiError(
                status_code=409,
                code="PLAN_CONFIRMATION_MISMATCH",
                message="确认指纹与当前变更计划不一致；请重新读取并展示该计划。",
                details={
                    "confirmed_fingerprint": payload.confirmed_fingerprint,
                    "current_fingerprint": content_fingerprint,
                },
                suggested_command=f"auto-email-sender plans show {plan.id}",
            )

        claim = await session.execute(
            update(AgentChangePlan)
            .where(
                AgentChangePlan.id == plan.id,
                AgentChangePlan.status == CHANGE_PLAN_AWAITING,
            )
            .values(
                status=CHANGE_PLAN_EXECUTING,
                confirmed_at=utc_now(),
                execution_started_at=utc_now(),
                updated_at=utc_now(),
            )
            .execution_options(synchronize_session=False),
        )
        if claim.rowcount != 1:
            await session.rollback()
            raise AgentApiError(
                status_code=409,
                code="CHANGE_PLAN_EXECUTION_IN_PROGRESS",
                message="该计划已被另一个执行请求领取；不要重复执行。",
                retryable=True,
            )
        now = utc_now()
        plan.status = CHANGE_PLAN_EXECUTING
        plan.confirmed_at = now
        plan.execution_started_at = now
        await _record_change_plan_event(
            session, plan, "agent_cli.change_plan_confirmed"
        )

        action_result = await execute_plan_action(
            session,
            plan,
            community_service=community_service,
            community_service_factory=community_service_factory,
        )
        result = action_result.data
        file_path_to_delete = action_result.file_path_to_delete
        plan.status = CHANGE_PLAN_EXECUTED
        plan.result = result
        plan.executed_at = utc_now()
        plan.updated_at = utc_now()
        await _record_change_plan_event(
            session,
            plan,
            "agent_cli.change_plan_executed",
            metadata={"outcome": result.get("outcome")},
        )
        await session.commit()
        response = _serialize_change_plan(plan)
    delete_file(file_path_to_delete)
    return response


def _serialize_change_plan(
    plan: AgentChangePlan,
    *,
    idempotent_replay: bool = False,
) -> AgentChangePlanRead:
    summary = plan.snapshot.get("summary")
    if not isinstance(summary, dict):
        raise AgentApiError(
            status_code=500,
            code="INVALID_CHANGE_PLAN_SNAPSHOT",
            message="变更计划快照无效，请重新生成计划。",
        )
    warnings = plan.snapshot.get("warnings")
    return AgentChangePlanRead(
        plan_id=plan.id,
        action=plan.action,
        status=plan.status,  # type: ignore[arg-type]
        content_fingerprint=_change_plan_content_fingerprint(plan),
        expires_at=plan.expires_at,
        confirmed_at=plan.confirmed_at,
        executed_at=plan.executed_at,
        canceled_at=plan.canceled_at,
        summary=summary,
        effects=resolve_agent_plan_effects(plan.action),
        warnings=[str(item) for item in warnings] if isinstance(warnings, list) else [],
        result=plan.result,
        idempotent_replay=idempotent_replay,
        confirmation_message=(
            _change_plan_confirmation_message(plan.action)
            if plan.status == CHANGE_PLAN_AWAITING
            else None
        ),
    )


def _change_plan_content_fingerprint(plan: AgentChangePlan) -> str:
    return fingerprint({"action": plan.action, "snapshot": plan.snapshot})


async def _expire_if_needed(session: AsyncSession, plan: AgentChangePlan) -> None:
    if (
        plan.status == CHANGE_PLAN_AWAITING
        and as_utc_aware(plan.expires_at) <= utc_now()
    ):
        plan.status = CHANGE_PLAN_EXPIRED
        plan.updated_at = utc_now()
        await _record_change_plan_event(session, plan, "agent_cli.change_plan_expired")
        await session.commit()


async def _get_change_plan_or_raise(
    session: AsyncSession,
    plan_id: str,
) -> AgentChangePlan:
    plan = await session.get(AgentChangePlan, plan_id)
    if plan is None:
        raise AgentApiError(
            status_code=404,
            code="CHANGE_PLAN_NOT_FOUND",
            message="未找到变更计划。",
        )
    return plan


async def _record_change_plan_event(
    session: AsyncSession,
    plan: AgentChangePlan,
    event_name: str,
    *,
    metadata: dict[str, object] | None = None,
) -> None:
    event_metadata: dict[str, object] = {
        "actor": "agent_cli",
        "plan_id": plan.id,
        "action": plan.action,
        "status": plan.status,
        "risk_level": (
            "L3"
            if plan.action
            in {
                CAMPAIGN_SEND_ACTION,
                CAMPAIGN_RESUME_ACTION,
                CAMPAIGN_RESTORE_SEND_ACTION,
                TEST_EMAIL_SEND_ACTION,
            }
            else "L2"
        ),
    }
    if metadata:
        event_metadata.update(metadata)
    await record_operation_log(
        session,
        category="agent_change",
        event_name=event_name,
        entity_type="agent_change_plan",
        entity_id=plan.id,
        metadata=event_metadata,
    )


def _ensure_same_idempotent_request(
    plan: AgentChangePlan,
    request_fingerprint: str,
) -> None:
    if plan.request_fingerprint != request_fingerprint:
        raise AgentApiError(
            status_code=409,
            code="IDEMPOTENCY_KEY_REUSED",
            message="同一个 Idempotency-Key 已用于不同的变更计划请求。",
        )


def _new_change_plan_id() -> str:
    return f"change_{secrets.token_urlsafe(18)}"


def _change_plan_expired_error(plan: AgentChangePlan) -> AgentApiError:
    return AgentApiError(
        status_code=409,
        code="PLAN_EXPIRED",
        message="变更计划已过期，请重新生成并向用户展示新的计划。",
        details={"expired_at": serialize_api_datetime(plan.expires_at)},
    )


def _change_plan_confirmation_message(action: str) -> str:
    if action == MATERIAL_DELETE_ACTION:
        return "尚未删除材料。请把以上影响范围和警告展示给用户，得到明确确认后再执行。"
    if action == PROFESSOR_BULK_TAGS_ACTION:
        return (
            "尚未修改导师标签。请把以上影响范围和警告展示给用户，得到明确确认后再执行。"
        )
    if action == PROFESSOR_BULK_ARCHIVE_ACTION:
        return "尚未批量归档导师。请把以上导师、数量和警告展示给用户，得到明确确认后再执行。"
    if action == PROFESSOR_TAG_DELETE_ACTION:
        return (
            "尚未删除标签。请把标签关联的导师和警告展示给用户，得到明确确认后再执行。"
        )
    if action == PROFESSOR_IMPORT_ACTION:
        return "尚未导入导师。请把以上新增、更新和标签影响展示给用户，得到明确确认后再执行。"
    if action == COMMUNITY_MENTOR_IMPORT_ACTION:
        return "尚未导入社区导师。请把新增、更新、关联、字段选择和冲突处理展示给用户，得到明确确认后再执行。"
    if action == TEST_EMAIL_SEND_ACTION:
        return "尚未发送测试邮件。请把收件地址、最终正文、身份和附件展示给用户，得到明确确认后再执行。"
    if action == CRAWL_CANDIDATE_APPROVE_ACTION:
        return "尚未导入抓取候选。请把以上逐项新增、覆盖和跳过情况展示给用户，得到明确确认后再执行。"
    if action == CRAWL_JOB_RETRY_ACTION:
        return "尚未重试抓取任务。请把以上清空范围、网页访问和模型调用影响展示给用户，得到明确确认后再执行。"
    if action == CAMPAIGN_CREATE_ACTION:
        return "尚未创建批量草稿活动。请把以上收件人、模板、材料和排程影响展示给用户，得到明确确认后再执行。"
    if action == CAMPAIGN_SEND_ACTION:
        return "尚未发送。请把每位收件人、最终正文、身份、模板、AI 模式、参考材料、附件和时间展示给用户，得到明确确认后再执行。"
    if action == CAMPAIGN_RESUME_ACTION:
        return "尚未恢复活动。请把可能重新进入发送调度的每位收件人、最终正文、身份、附件和时间展示给用户，得到明确确认后再执行。"
    if action == CAMPAIGN_RESTORE_SEND_ACTION:
        return "尚未恢复发送。请把收件人、最终正文、身份、附件和原定时间展示给用户，得到明确确认后再执行。"
    return "尚未归档。请把以上影响范围和警告展示给用户，得到明确确认后再执行。"
