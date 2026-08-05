from __future__ import annotations

import secrets
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.agent_api_errors import AgentApiError
from app.core.time import as_utc_aware, serialize_api_datetime, utc_now
from app.models import (
    AgentChangePlan,
    CrawlCandidate,
    CrawlCandidateEnrichmentTask,
    CrawlCandidateReviewStatus,
    CrawlJob,
    CrawlJobKind,
    CrawlJobStatus,
    CrawlPage,
    CrawlPageChunk,
    CrawlPageTask,
    CrawlWorkerTokenUsage,
    IdentityProfile,
    LLMProfile,
    OutreachTemplate,
    Professor,
)
from app.schemas.agent import (
    AgentCampaignCreateRequest,
    AgentCampaignSendRequest,
    AgentChangePlanRead,
    AgentPlanExecuteRequest,
)
from app.schemas.community_mentor import (
    CommunityImportItemPayload,
    CommunityImportPayload,
    CommunityMentorComparisonRead,
)
from app.schemas.crawl_job import CrawlJobRetryPayload
from app.schemas.professor import ProfessorBulkTagsPayload
from app.schemas.test_compose import TestComposeMessageSendRequest
from app.services.agent_mutations import (
    fingerprint,
    normalize_idempotency_key,
)
from app.services.file_storage import delete_file
from app.services.material_mutations import (
    MaterialMutationError,
    delete_identity_material_record,
    prepare_material_deletion_snapshot,
)
from app.services.operation_logs import record_operation_log
from app.services.agent_plan_effects import resolve_agent_plan_effects
from app.services.outreach_template_mutations import (
    OutreachTemplateMutationError,
    archive_outreach_template_record,
    get_outreach_template_or_raise,
)
from app.services.professor_mutations import (
    ProfessorMutationError,
    bulk_archive_professor_records,
    bulk_update_professor_tags_record,
    delete_professor_tag_record,
    prepare_bulk_professor_archive_snapshot,
    import_professor_records,
    prepare_bulk_professor_tags_snapshot,
    prepare_professor_tag_delete_snapshot,
    prepare_professor_import_snapshot,
)
from app.services.professor_management import (
    ParsedProfessorImport,
    is_valid_professor_email,
    normalize_professor_email,
    parse_professor_import_file,
)
from app.services.crawl_job_records import (
    CrawlJobRecordError,
    retry_faculty_crawl_job_record,
)
from app.services.community_mentor_data import (
    CommunityDataError,
    CommunityMentorDataService,
    build_community_comparisons,
    import_community_records,
    sync_community_link_lifecycle,
)
from app.services.agent_campaigns import (
    execute_campaign_create_snapshot,
    execute_campaign_restore_send_snapshot,
    execute_campaign_resume_snapshot,
    execute_campaign_send_snapshot,
    prepare_campaign_create_snapshot,
    prepare_campaign_restore_send_snapshot,
    prepare_campaign_resume_snapshot,
    prepare_campaign_send_snapshot,
)
from app.services.test_compose_runtime import (
    prepare_test_compose_send_snapshot,
    send_test_compose_message,
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
    professor_ids: list[int],
    *,
    idempotency_key: str | None,
) -> AgentChangePlanRead:
    normalized_key = normalize_idempotency_key(idempotency_key)
    request_fingerprint = fingerprint(
        {"action": PROFESSOR_BULK_ARCHIVE_ACTION, "professor_ids": professor_ids},
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
            snapshot = await prepare_bulk_professor_archive_snapshot(
                session,
                professor_ids,
            )
        except ProfessorMutationError as exc:
            raise _professor_error(exc) from exc
        snapshot["bulk_archive_fingerprint"] = _bulk_archive_snapshot_fingerprint(snapshot)
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
        snapshot["tag_delete_fingerprint"] = _tag_delete_snapshot_fingerprint(snapshot)
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
        snapshot["import_fingerprint"] = _professor_import_snapshot_fingerprint(snapshot)
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
        snapshot["community_import_fingerprint"] = _community_import_snapshot_fingerprint(
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
        snapshot["test_email_send_fingerprint"] = _test_email_send_snapshot_fingerprint(snapshot)
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
    candidate_ids: list[int],
    *,
    idempotency_key: str | None,
) -> AgentChangePlanRead:
    normalized_key = normalize_idempotency_key(idempotency_key)
    normalized_candidate_ids = sorted(candidate_ids)
    request_fingerprint = fingerprint(
        {
            "action": CRAWL_CANDIDATE_APPROVE_ACTION,
            "job_id": job_id,
            "candidate_ids": normalized_candidate_ids,
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

        snapshot = await _prepare_crawl_candidate_approval_snapshot(
            session,
            job_id,
            normalized_candidate_ids,
        )
        snapshot["approval_fingerprint"] = _crawl_candidate_approval_snapshot_fingerprint(snapshot)
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
        snapshot["retry_fingerprint"] = _crawl_job_retry_snapshot_fingerprint(snapshot)
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
        await _record_change_plan_event(session, plan, "agent_cli.change_plan_confirmed")

        if plan.action == TEMPLATE_ARCHIVE_ACTION:
            result = await _execute_template_archive(session, plan)
        elif plan.action == MATERIAL_DELETE_ACTION:
            result, file_path_to_delete = await _execute_material_delete(session, plan)
        elif plan.action == PROFESSOR_BULK_TAGS_ACTION:
            result = await _execute_professor_bulk_tags(session, plan)
        elif plan.action == PROFESSOR_BULK_ARCHIVE_ACTION:
            result = await _execute_professor_bulk_archive(session, plan)
        elif plan.action == PROFESSOR_TAG_DELETE_ACTION:
            result = await _execute_professor_tag_delete(session, plan)
        elif plan.action == PROFESSOR_IMPORT_ACTION:
            result = await _execute_professor_import(session, plan)
        elif plan.action == COMMUNITY_MENTOR_IMPORT_ACTION:
            try:
                effective_community_service = community_service or (
                    community_service_factory()
                    if community_service_factory is not None
                    else CommunityMentorDataService()
                )
            except CommunityDataError as exc:
                raise _community_import_error(exc) from exc
            result = await _execute_community_mentor_import(
                session,
                plan,
                effective_community_service,
            )
        elif plan.action == TEST_EMAIL_SEND_ACTION:
            result = await _execute_test_email_send(session, plan)
        elif plan.action == CRAWL_CANDIDATE_APPROVE_ACTION:
            result = await _execute_crawl_candidate_approval(session, plan)
        elif plan.action == CRAWL_JOB_RETRY_ACTION:
            result = await _execute_crawl_job_retry(session, plan)
        elif plan.action == CAMPAIGN_CREATE_ACTION:
            result = await execute_campaign_create_snapshot(session, plan.snapshot)
        elif plan.action == CAMPAIGN_SEND_ACTION:
            result = await execute_campaign_send_snapshot(session, plan.snapshot)
        elif plan.action == CAMPAIGN_RESUME_ACTION:
            result = await execute_campaign_resume_snapshot(session, plan.snapshot)
        elif plan.action == CAMPAIGN_RESTORE_SEND_ACTION:
            result = await execute_campaign_restore_send_snapshot(session, plan.snapshot)
        else:
            raise AgentApiError(
                status_code=500,
                code="UNSUPPORTED_CHANGE_PLAN_ACTION",
                message="该变更计划的动作类型不受支持。",
            )
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


async def _execute_template_archive(
    session: AsyncSession,
    plan: AgentChangePlan,
) -> dict[str, object]:
    snapshot = plan.snapshot
    template_id = snapshot.get("template_id")
    if not isinstance(template_id, int):
        raise AgentApiError(
            status_code=500,
            code="INVALID_CHANGE_PLAN_SNAPSHOT",
            message="变更计划快照无效，请重新生成计划。",
        )
    try:
        template = await get_outreach_template_or_raise(
            session,
            template_id,
            include_archived=True,
        )
    except OutreachTemplateMutationError as exc:
        raise AgentApiError(
            status_code=409,
            code="PLAN_STALE",
            message="计划对应的模板已不存在，请重新生成归档计划。",
            details={"changed_fields": ["template"]},
        ) from exc
    current_default_identity_count = int(
        await session.scalar(
            select(func.count(IdentityProfile.id)).where(
                IdentityProfile.default_outreach_template_id == template.id,
            ),
        )
        or 0,
    )
    expected_fingerprint = snapshot.get("template_fingerprint")
    current_fingerprint = _template_archive_snapshot_fingerprint(
        template,
        current_default_identity_count,
    )
    if (
        template.archived_at is not None
        or expected_fingerprint != current_fingerprint
    ):
        raise AgentApiError(
            status_code=409,
            code="PLAN_STALE",
            message="模板内容或状态已发生变化，请重新生成归档预览。",
            details={"changed_fields": ["template"]},
            suggested_command=f"auto-email-sender templates prepare-archive {template_id}",
        )
    await archive_outreach_template_record(
        session,
        template_id,
        event_name="agent_cli.template.archived",
        actor="agent_cli",
    )
    return {
        "outcome": "archived",
        "template_id": template_id,
        "template_name": template.name,
    }


async def _execute_material_delete(
    session: AsyncSession,
    plan: AgentChangePlan,
) -> tuple[dict[str, object], str | None]:
    snapshot = plan.snapshot
    material_id = snapshot.get("material_id")
    expected_fingerprint = snapshot.get("deletion_fingerprint")
    if not isinstance(material_id, int) or not isinstance(expected_fingerprint, str):
        raise AgentApiError(
            status_code=500,
            code="INVALID_CHANGE_PLAN_SNAPSHOT",
            message="变更计划快照无效，请重新生成计划。",
        )
    try:
        result = await delete_identity_material_record(
            session,
            material_id,
            event_name="agent_cli.material.deleted",
            actor="agent_cli",
            expected_fingerprint=expected_fingerprint,
        )
    except MaterialMutationError as exc:
        raise AgentApiError(
            status_code=409,
            code="PLAN_STALE",
            message="材料或其引用关系已发生变化，请重新生成删除预览。",
            details={"changed_fields": ["material", "material_references"]},
            suggested_command=f"auto-email-sender materials prepare-delete {material_id}",
        ) from exc
    return result.to_agent_result(), result.file_path


async def _execute_professor_bulk_tags(
    session: AsyncSession,
    plan: AgentChangePlan,
) -> dict[str, object]:
    snapshot = plan.snapshot
    request_data = snapshot.get("request")
    expected_fingerprint = snapshot.get("bulk_tags_fingerprint")
    if not isinstance(request_data, dict) or not isinstance(expected_fingerprint, str):
        raise AgentApiError(
            status_code=500,
            code="INVALID_CHANGE_PLAN_SNAPSHOT",
            message="变更计划快照无效，请重新生成计划。",
        )
    try:
        payload = ProfessorBulkTagsPayload.model_validate(request_data)
        current_snapshot = await prepare_bulk_professor_tags_snapshot(session, payload)
    except ProfessorMutationError as exc:
        raise _bulk_tags_plan_stale_error() from exc
    if expected_fingerprint != _bulk_tags_snapshot_fingerprint(current_snapshot):
        raise _bulk_tags_plan_stale_error()

    try:
        professors = await bulk_update_professor_tags_record(
            session,
            payload,
            event_name="agent_cli.professor.bulk_tags_updated",
            actor="agent_cli",
        )
    except ProfessorMutationError as exc:
        raise _bulk_tags_plan_stale_error() from exc
    summary = snapshot.get("summary")
    changed_count = summary.get("changed_count") if isinstance(summary, dict) else None
    return {
        "outcome": "tags_updated",
        "mode": payload.mode,
        "affected_count": len(professors),
        "changed_count": changed_count,
        "professor_ids": [professor.id for professor in professors],
    }


async def _execute_professor_bulk_archive(
    session: AsyncSession,
    plan: AgentChangePlan,
) -> dict[str, object]:
    snapshot = plan.snapshot
    request_data = snapshot.get("request")
    expected_fingerprint = snapshot.get("bulk_archive_fingerprint")
    professor_ids = request_data.get("professor_ids") if isinstance(request_data, dict) else None
    if (
        not isinstance(expected_fingerprint, str)
        or not isinstance(professor_ids, list)
        or any(not isinstance(professor_id, int) or isinstance(professor_id, bool) for professor_id in professor_ids)
    ):
        raise _invalid_change_plan_snapshot_error()
    try:
        current_snapshot = await prepare_bulk_professor_archive_snapshot(
            session,
            professor_ids,
        )
    except ProfessorMutationError as exc:
        raise _bulk_archive_plan_stale_error() from exc
    if expected_fingerprint != _bulk_archive_snapshot_fingerprint(current_snapshot):
        raise _bulk_archive_plan_stale_error()
    try:
        result = await bulk_archive_professor_records(
            session,
            professor_ids,
            event_name="agent_cli.professor.bulk_archived",
            actor="agent_cli",
        )
    except ProfessorMutationError as exc:
        raise _bulk_archive_plan_stale_error() from exc
    return {"outcome": "professors_archived", **result}


async def _execute_professor_tag_delete(
    session: AsyncSession,
    plan: AgentChangePlan,
) -> dict[str, object]:
    snapshot = plan.snapshot
    request_data = snapshot.get("request")
    expected_fingerprint = snapshot.get("tag_delete_fingerprint")
    tag_id = request_data.get("tag_id") if isinstance(request_data, dict) else None
    if not isinstance(expected_fingerprint, str) or not isinstance(tag_id, int) or isinstance(tag_id, bool):
        raise _invalid_change_plan_snapshot_error()
    try:
        current_snapshot = await prepare_professor_tag_delete_snapshot(session, tag_id)
    except ProfessorMutationError as exc:
        raise _tag_delete_plan_stale_error() from exc
    if expected_fingerprint != _tag_delete_snapshot_fingerprint(current_snapshot):
        raise _tag_delete_plan_stale_error()
    try:
        result = await delete_professor_tag_record(
            session,
            tag_id,
            event_name="agent_cli.professor.tag_deleted",
            actor="agent_cli",
        )
    except ProfessorMutationError as exc:
        raise _tag_delete_plan_stale_error() from exc
    return {"outcome": "tag_deleted", **result}


async def _execute_professor_import(
    session: AsyncSession,
    plan: AgentChangePlan,
) -> dict[str, object]:
    snapshot = plan.snapshot
    request_data = snapshot.get("request")
    expected_fingerprint = snapshot.get("import_fingerprint")
    if not isinstance(request_data, dict) or not isinstance(expected_fingerprint, str):
        raise AgentApiError(
            status_code=500,
            code="INVALID_CHANGE_PLAN_SNAPSHOT",
            message="变更计划快照无效，请重新生成计划。",
        )
    try:
        filename, parsed = _parsed_professor_import_from_snapshot(request_data)
        current_snapshot = await prepare_professor_import_snapshot(
            session,
            parsed,
            filename=filename,
        )
    except (ProfessorMutationError, ValueError) as exc:
        raise _professor_import_plan_stale_error() from exc
    if expected_fingerprint != _professor_import_snapshot_fingerprint(current_snapshot):
        raise _professor_import_plan_stale_error()

    try:
        result = await import_professor_records(
            session,
            parsed,
            filename=filename,
            event_name="agent_cli.professor.imported",
            actor="agent_cli",
        )
    except ProfessorMutationError as exc:
        raise _professor_import_plan_stale_error() from exc
    return {
        "outcome": "imported",
        "inserted_count": result.inserted_count,
        "updated_count": result.updated_count,
        "created_tag_count": result.created_tag_count,
        "failed_count": result.failed_count,
    }


async def _execute_community_mentor_import(
    session: AsyncSession,
    plan: AgentChangePlan,
    community_service: CommunityMentorDataService,
) -> dict[str, object]:
    snapshot = plan.snapshot
    request_data = snapshot.get("request")
    expected_fingerprint = snapshot.get("community_import_fingerprint")
    if not isinstance(request_data, dict) or not isinstance(expected_fingerprint, str):
        raise _invalid_change_plan_snapshot_error()
    try:
        payload = CommunityImportPayload.model_validate(request_data)
        current_snapshot, comparisons = await _prepare_community_mentor_import_snapshot(
            session,
            payload,
            community_service,
        )
    except CommunityDataError as exc:
        raise _community_import_error(exc) from exc
    if expected_fingerprint != _community_import_snapshot_fingerprint(current_snapshot):
        raise _community_import_plan_stale_error()

    try:
        result = await import_community_records(
            session,
            dataset_version=payload.dataset_version,
            comparisons=comparisons,
            items=payload.items,
            event_name="agent_cli.community_mentor.imported",
            actor="agent_cli",
        )
    except CommunityDataError as exc:
        raise _community_import_error(exc) from exc
    return {
        "outcome": "community_mentors_imported",
        "dataset_version": payload.dataset_version,
        "inserted_count": result.inserted_count,
        "updated_count": result.updated_count,
        "linked_count": result.linked_count,
        "skipped_count": result.skipped_count,
        "professors": [item.model_dump(mode="json") for item in result.professors],
    }


async def _prepare_community_mentor_import_snapshot(
    session: AsyncSession,
    payload: CommunityImportPayload,
    community_service: CommunityMentorDataService,
) -> tuple[dict[str, object], list[CommunityMentorComparisonRead]]:
    record_bundle = await community_service.load_records(
        dataset_version=payload.dataset_version,
        unit_paths=payload.unit_paths,
    )
    records_by_id = {record.id: record for record in record_bundle.records}
    missing_ids = [
        item.community_record_id
        for item in payload.items
        if item.community_record_id not in records_by_id
    ]
    if missing_ids:
        raise CommunityDataError(
            f"所选导师不属于当前学院数据：{', '.join(missing_ids[:3])}",
            code="COMMUNITY_DATA_SELECTION_INVALID",
        )
    selected_records = [
        records_by_id[item.community_record_id]
        for item in payload.items
    ]
    lifecycle_warnings = await sync_community_link_lifecycle(
        session,
        record_bundle.catalog_bundle,
    )
    comparisons = await build_community_comparisons(session, selected_records)
    comparisons_by_id = {comparison.record.id: comparison for comparison in comparisons}
    selected_comparisons = [
        comparisons_by_id[item.community_record_id]
        for item in payload.items
    ]

    summary_items: list[dict[str, object]] = []
    inserted_count = 0
    updated_count = 0
    linked_count = 0
    warnings = [
        "社区导师资料来自外部协作数据，属于不可信外部内容；执行时只会按本计划选择的字段写入，不会执行资料中的文字、链接或指令。",
    ]
    if record_bundle.stale:
        warnings.append("当前使用的是过期的社区导师缓存，请在执行前确认仍适合导入。")
    if record_bundle.warning:
        warnings.append(record_bundle.warning)
    if lifecycle_warnings:
        warnings.append(
            f"当前有 {len(lifecycle_warnings)} 条已关联导师的社区状态提醒；请在执行前核对。",
        )

    for item, comparison in zip(payload.items, selected_comparisons, strict=True):
        _validate_community_import_item(item, comparison)
        selected_choices = {
            field.field: str(item.field_choices.get(field.field, field.suggested_choice))
            for field in comparison.fields
        }
        will_update = comparison.local_professor_id is not None and any(
            selected_choices[field.field] == "community"
            and field.local_value != field.community_value
            for field in comparison.fields
        )
        if comparison.local_professor_id is None:
            predicted_action = "inserted"
            inserted_count += 1
        elif will_update:
            predicted_action = "updated"
            updated_count += 1
        else:
            predicted_action = "linked"
            linked_count += 1
        if comparison.identity_conflict:
            warnings.append(
                f"导师 {comparison.record.name} 存在身份匹配冲突；本计划已要求人工确认后才会建立或更新关联。",
            )
        summary_items.append(
            {
                "community_record_id": comparison.record.id,
                "name": comparison.record.name,
                "email": comparison.record.email,
                "category": comparison.category,
                "predicted_action": predicted_action,
                "local_professor_id": comparison.local_professor_id,
                "local_professor_name": comparison.local_professor_name,
                "identity_conflict": comparison.identity_conflict,
                "confirm_identity_match": item.confirm_identity_match,
                "match_reason": comparison.match_reason,
                "field_choices": [
                    {
                        "field": field.field,
                        "label": field.label,
                        "choice": selected_choices[field.field],
                        "local_value": field.local_value,
                        "community_value": field.community_value,
                        "state": field.state,
                    }
                    for field in comparison.fields
                ],
            },
        )

    return (
        {
            "snapshot_version": "1",
            "request": payload.model_dump(mode="json"),
            "state": {
                "dataset_version": record_bundle.catalog_bundle.catalog.dataset_version,
                "comparisons": [
                    {
                        "community_record_id": comparison.record.id,
                        "comparison_token": comparison.comparison_token,
                    }
                    for comparison in selected_comparisons
                ],
            },
            "summary": {
                "trust_level": "untrusted_external_content",
                "dataset_version": payload.dataset_version,
                "unit_paths": payload.unit_paths,
                "source": record_bundle.source,
                "stale": record_bundle.stale,
                "selected_count": len(summary_items),
                "inserted_count": inserted_count,
                "updated_count": updated_count,
                "linked_count": linked_count,
                "items": summary_items,
                "lifecycle_warnings": [
                    warning.model_dump(mode="json")
                    for warning in lifecycle_warnings
                ],
            },
            "warnings": warnings,
        },
        selected_comparisons,
    )


def _validate_community_import_item(
    item: CommunityImportItemPayload,
    comparison: CommunityMentorComparisonRead,
) -> None:
    if item.comparison_token != comparison.comparison_token:
        raise CommunityDataError(
            f"导师 {comparison.record.name} 的本地信息在预览后发生了变化；请重新预览后再导入",
            code="COMMUNITY_DATA_PREVIEW_STALE",
        )
    if comparison.category == "retired_or_revoked" or comparison.record.status != "active":
        raise CommunityDataError(
            f"导师 {comparison.record.name} 已退休、离职或撤销，不能作为新数据导入",
            code="COMMUNITY_DATA_LIFECYCLE_BLOCKED",
        )
    if comparison.import_blocked:
        raise CommunityDataError(
            comparison.import_blocked_reason
            or f"导师 {comparison.record.name} 暂时不能导入，请先处理本地冲突",
            code="COMMUNITY_DATA_IDENTITY_CONFLICT",
        )
    if comparison.identity_conflict and comparison.local_professor_id is None:
        raise CommunityDataError(
            f"导师 {comparison.record.name} 的邮箱在本地存在多重匹配，请先整理本地重复记录",
            code="COMMUNITY_DATA_IDENTITY_CONFLICT",
        )
    if comparison.identity_conflict and not item.confirm_identity_match:
        raise CommunityDataError(
            f"导师 {comparison.record.name} 的身份存在冲突，需要人工确认后才能生成导入计划",
            code="COMMUNITY_DATA_IDENTITY_CONFLICT",
        )
    for field in comparison.fields:
        choice = item.field_choices.get(field.field, field.suggested_choice)
        if (
            choice == "community"
            and _community_value_is_empty(field.community_value)
            and not _community_value_is_empty(field.local_value)
        ):
            raise CommunityDataError(
                f"导师 {comparison.record.name} 的社区{field.label}为空，不能清空本地已有内容；请选择保留本地后重试",
                code="COMMUNITY_DATA_FIELD_CHOICE_INVALID",
            )


def _community_value_is_empty(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return not value
    return False


async def _execute_test_email_send(
    session: AsyncSession,
    plan: AgentChangePlan,
) -> dict[str, object]:
    snapshot = plan.snapshot
    expected_fingerprint = snapshot.get("test_email_send_fingerprint")
    if not isinstance(expected_fingerprint, str):
        raise _invalid_change_plan_snapshot_error()
    identity_id, llm_profile_id, payload = _test_email_send_request_from_snapshot(snapshot)
    try:
        current_snapshot = await prepare_test_compose_send_snapshot(
            session,
            identity_id=identity_id,
            llm_profile_id=llm_profile_id,
            payload=payload,
        )
    except ValueError as exc:
        raise _test_email_error(exc) from exc
    if expected_fingerprint != _test_email_send_snapshot_fingerprint(current_snapshot):
        raise _test_email_send_plan_stale_error()

    try:
        thread = await send_test_compose_message(
            session,
            identity_id=identity_id,
            llm_profile_id=llm_profile_id,
            payload=payload,
            commit=False,
            event_name="agent_cli.test_email",
            actor="agent_cli",
        )
    except ValueError as exc:
        raise _test_email_error(exc) from exc
    message = thread.history[0] if thread.history else None
    if message is None:
        raise AgentApiError(
            status_code=500,
            code="TEST_EMAIL_SEND_RESULT_MISSING",
            message="测试邮件发送后未找到结果，请在桌面端检查发送历史。",
        )
    return {
        "outcome": "sent" if message.status == "sent" else "failed",
        "identity_id": identity_id,
        "recipient_email": message.recipient_email,
        "message_id": message.id,
        "status": message.status,
        "rfc_message_id": message.rfc_message_id,
        "failure_summary": message.failure_summary,
    }


def _test_email_send_request_from_snapshot(
    snapshot: dict[str, object],
) -> tuple[int, int, TestComposeMessageSendRequest]:
    request_data = snapshot.get("request")
    if not isinstance(request_data, dict):
        raise _invalid_change_plan_snapshot_error()
    identity_id = request_data.get("identity_id")
    llm_profile_id = request_data.get("llm_profile_id")
    payload_data = request_data.get("payload")
    if (
        not isinstance(identity_id, int)
        or isinstance(identity_id, bool)
        or identity_id < 1
        or not isinstance(llm_profile_id, int)
        or isinstance(llm_profile_id, bool)
        or llm_profile_id < 1
        or not isinstance(payload_data, dict)
    ):
        raise _invalid_change_plan_snapshot_error()
    try:
        payload = TestComposeMessageSendRequest.model_validate(payload_data)
    except ValueError as exc:
        raise _invalid_change_plan_snapshot_error() from exc
    return identity_id, llm_profile_id, payload


async def _execute_crawl_candidate_approval(
    session: AsyncSession,
    plan: AgentChangePlan,
) -> dict[str, object]:
    snapshot = plan.snapshot
    expected_fingerprint = snapshot.get("approval_fingerprint")
    if not isinstance(expected_fingerprint, str):
        raise AgentApiError(
            status_code=500,
            code="INVALID_CHANGE_PLAN_SNAPSHOT",
            message="变更计划快照无效，请重新生成计划。",
        )
    job_id, candidate_ids = _crawl_candidate_approval_request_from_snapshot(snapshot)
    try:
        current_snapshot = await _prepare_crawl_candidate_approval_snapshot(
            session,
            job_id,
            candidate_ids,
        )
    except AgentApiError as exc:
        raise _crawl_candidate_approval_plan_stale_error() from exc
    if expected_fingerprint != _crawl_candidate_approval_snapshot_fingerprint(current_snapshot):
        raise _crawl_candidate_approval_plan_stale_error()

    job = await session.scalar(
        select(CrawlJob).where(
            CrawlJob.id == job_id,
            CrawlJob.job_kind == CrawlJobKind.FACULTY_CRAWL.value,
        ),
    )
    candidates = list(
        await session.scalars(
            select(CrawlCandidate)
            .where(
                CrawlCandidate.job_id == job_id,
                CrawlCandidate.id.in_(candidate_ids),
            )
            .order_by(CrawlCandidate.id.asc()),
        ),
    )
    if job is None or len(candidates) != len(candidate_ids):
        raise _crawl_candidate_approval_plan_stale_error()

    inserted_count = 0
    updated_count = 0
    skipped_count = 0
    professor_ids: list[int] = []
    now = utc_now()
    for candidate in candidates:
        email = normalize_professor_email(candidate.email)
        if email is None or not is_valid_professor_email(email):
            skipped_count += 1
            continue

        professor = await session.scalar(select(Professor).where(Professor.email == email))
        if professor is None:
            professor = Professor(email=email)
            session.add(professor)
            inserted_count += 1
        else:
            updated_count += 1

        _apply_crawl_candidate_to_professor(professor, candidate, email=email, now=now)
        await session.flush()
        candidate.professor_id = professor.id
        candidate.review_status = CrawlCandidateReviewStatus.ACCEPTED.value
        candidate.updated_at = now
        if professor.id not in professor_ids:
            professor_ids.append(professor.id)

    await session.flush()
    if job.status in {
        CrawlJobStatus.NEEDS_REVIEW.value,
        CrawlJobStatus.PARTIALLY_COMPLETED.value,
    }:
        remaining_pending_count = await session.scalar(
            select(func.count())
            .select_from(CrawlCandidate)
            .where(
                CrawlCandidate.job_id == job_id,
                CrawlCandidate.review_status == CrawlCandidateReviewStatus.PENDING.value,
            ),
        )
        job.status = (
            CrawlJobStatus.PARTIALLY_COMPLETED.value
            if int(remaining_pending_count or 0) > 0
            else CrawlJobStatus.COMPLETED.value
        )
    job.updated_at = now
    await record_operation_log(
        session,
        category="crawler",
        event_name="agent_cli.crawl_candidates.approved",
        entity_type="crawl_job",
        entity_id=str(job.id),
        metadata={
            "actor": "agent_cli",
            "inserted_count": inserted_count,
            "updated_count": updated_count,
            "skipped_count": skipped_count,
            "candidate_count": len(candidates),
        },
    )
    return {
        "outcome": "crawl_candidates_approved",
        "job_id": job.id,
        "job_status": job.status,
        "inserted_count": inserted_count,
        "updated_count": updated_count,
        "skipped_count": skipped_count,
        "candidate_count": len(candidates),
        "professor_ids": professor_ids,
    }


async def _execute_crawl_job_retry(
    session: AsyncSession,
    plan: AgentChangePlan,
) -> dict[str, object]:
    snapshot = plan.snapshot
    expected_fingerprint = snapshot.get("retry_fingerprint")
    if not isinstance(expected_fingerprint, str):
        raise _invalid_change_plan_snapshot_error()
    job_id, payload = _crawl_job_retry_request_from_snapshot(snapshot)
    try:
        current_snapshot = await _prepare_crawl_job_retry_snapshot(session, job_id, payload)
    except AgentApiError as exc:
        raise _crawl_job_retry_plan_stale_error() from exc
    if expected_fingerprint != _crawl_job_retry_snapshot_fingerprint(current_snapshot):
        raise _crawl_job_retry_plan_stale_error()
    try:
        job = await retry_faculty_crawl_job_record(
            session,
            job_id,
            payload,
            event_name="agent_cli.crawl_job.retried",
            actor="agent_cli",
        )
    except CrawlJobRecordError as exc:
        raise _crawl_job_retry_plan_stale_error() from exc
    return {
        "outcome": "crawl_job_retry_queued",
        "job_id": job.id,
        "status": job.status,
        "clear_existing_data": payload.clear_existing_data,
        "llm_profile_id": job.llm_profile_id,
    }


async def _prepare_crawl_job_retry_snapshot(
    session: AsyncSession,
    job_id: int,
    payload: CrawlJobRetryPayload,
) -> dict[str, object]:
    job = await session.scalar(
        select(CrawlJob).where(
            CrawlJob.id == job_id,
            CrawlJob.job_kind == CrawlJobKind.FACULTY_CRAWL.value,
        ),
    )
    if job is None:
        raise AgentApiError(
            status_code=404,
            code="CRAWL_JOB_NOT_FOUND",
            message="未找到导师抓取任务。",
        )
    if job.status not in {CrawlJobStatus.FAILED.value, CrawlJobStatus.CANCELED.value}:
        raise AgentApiError(
            status_code=409,
            code="CRAWL_JOB_NOT_RETRYABLE",
            message="仅允许重试状态为“失败”或“已取消”的抓取任务。",
        )
    llm_profile = await _resolve_crawl_job_retry_llm_profile(session, job, payload)
    record_counts = await _crawl_job_retry_record_counts(session, job.id)
    state = {
        "job": {
            "id": job.id,
            "status": job.status,
            "runtime_version": job.runtime_version,
            "llm_profile_id": job.llm_profile_id,
            "start_urls": job.start_urls or [job.start_url],
            "entry_type": job.entry_type,
            "deleted_at": _serialize_optional_datetime(job.deleted_at),
            "updated_at": _serialize_optional_datetime(job.updated_at),
        },
        "effective_llm_profile": _crawl_job_retry_llm_profile_state(llm_profile),
        "records": record_counts,
    }
    warnings = [
        "确认后会重新访问该抓取任务的公开网页并调用模型；这不会发送邮件，但可能产生 Token 费用。",
    ]
    if payload.clear_existing_data:
        warnings.append(
            "确认后会永久清空本任务现有的候选、网页、网页分块、运行轨迹和（v2 任务的）Token 用量。",
        )
    elif job.runtime_version == "v2":
        warnings.append(
            "本次保留已抓取的候选和网页，但会重建 v2 抓取工作项，并清除候选补全工作项。",
        )
    return {
        "snapshot_version": "1",
        "request": {
            "job_id": job_id,
            "clear_existing_data": payload.clear_existing_data,
            "llm_profile_id": payload.llm_profile_id,
        },
        "state": state,
        "summary": {
            "job": {
                "id": job.id,
                "university": job.university,
                "school": job.school,
                "status": job.status,
            },
            "clear_existing_data": payload.clear_existing_data,
            "llm_profile": {
                "id": llm_profile.id,
                "name": llm_profile.name,
                "model_name": llm_profile.model_name,
            },
            "affected_records": record_counts,
        },
        "warnings": warnings,
    }


async def _resolve_crawl_job_retry_llm_profile(
    session: AsyncSession,
    job: CrawlJob,
    payload: CrawlJobRetryPayload,
) -> LLMProfile:
    profile_id = payload.llm_profile_id or job.llm_profile_id
    if profile_id is not None:
        profile = await session.get(LLMProfile, profile_id)
        if profile is None:
            raise AgentApiError(
                status_code=404,
                code="CRAWL_LLM_PROFILE_NOT_FOUND",
                message="本次重试指定的模型配置不存在。",
            )
        return profile
    profile = await session.scalar(
        select(LLMProfile)
        .where(LLMProfile.is_default.is_(True))
        .order_by(LLMProfile.created_at.asc(), LLMProfile.id.asc())
        .limit(1),
    )
    if profile is None:
        raise AgentApiError(
            status_code=409,
            code="CRAWL_LLM_PROFILE_REQUIRED",
            message="请先在桌面端配置可用的默认模型，再重试抓取任务。",
        )
    return profile


async def _crawl_job_retry_record_counts(
    session: AsyncSession,
    job_id: int,
) -> dict[str, int]:
    async def count_for(model: object) -> int:
        job_id_column = getattr(model, "job_id")
        return int(
            await session.scalar(
                select(func.count()).select_from(model).where(job_id_column == job_id),
            )
            or 0,
        )

    return {
        "candidate_count": await count_for(CrawlCandidate),
        "page_count": await count_for(CrawlPage),
        "page_chunk_count": await count_for(CrawlPageChunk),
        "page_task_count": await count_for(CrawlPageTask),
        "candidate_enrichment_task_count": await count_for(CrawlCandidateEnrichmentTask),
        "token_usage_count": await count_for(CrawlWorkerTokenUsage),
    }


def _crawl_job_retry_llm_profile_state(profile: LLMProfile) -> dict[str, object]:
    return {
        "id": profile.id,
        "name": profile.name,
        "model_name": profile.model_name,
        "updated_at": _serialize_optional_datetime(profile.updated_at),
    }


def _crawl_job_retry_request_from_snapshot(
    snapshot: dict[str, object],
) -> tuple[int, CrawlJobRetryPayload]:
    request_data = snapshot.get("request")
    if not isinstance(request_data, dict):
        raise _invalid_change_plan_snapshot_error()
    job_id = request_data.get("job_id")
    clear_existing_data = request_data.get("clear_existing_data")
    llm_profile_id = request_data.get("llm_profile_id")
    if (
        not isinstance(job_id, int)
        or isinstance(job_id, bool)
        or job_id < 1
        or not isinstance(clear_existing_data, bool)
        or (
            llm_profile_id is not None
            and (
                not isinstance(llm_profile_id, int)
                or isinstance(llm_profile_id, bool)
                or llm_profile_id < 1
            )
        )
    ):
        raise _invalid_change_plan_snapshot_error()
    return job_id, CrawlJobRetryPayload(
        clear_existing_data=clear_existing_data,
        llm_profile_id=llm_profile_id,
    )


def _crawl_job_retry_snapshot_fingerprint(snapshot: dict[str, object]) -> str:
    return fingerprint(
        {
            "request": snapshot.get("request"),
            "state": snapshot.get("state"),
        },
    )


async def _prepare_crawl_candidate_approval_snapshot(
    session: AsyncSession,
    job_id: int,
    candidate_ids: list[int],
) -> dict[str, object]:
    job = await session.scalar(
        select(CrawlJob).where(
            CrawlJob.id == job_id,
            CrawlJob.job_kind == CrawlJobKind.FACULTY_CRAWL.value,
        ),
    )
    if job is None:
        raise AgentApiError(
            status_code=404,
            code="CRAWL_JOB_NOT_FOUND",
            message="未找到导师抓取任务。",
        )
    if job.status not in {
        CrawlJobStatus.NEEDS_REVIEW.value,
        CrawlJobStatus.PARTIALLY_COMPLETED.value,
        CrawlJobStatus.CANCELED.value,
    }:
        raise AgentApiError(
            status_code=409,
            code="CRAWL_JOB_NOT_READY_FOR_APPROVAL",
            message="抓取任务尚未进入可审核状态，不能导入候选导师。",
        )

    candidates = list(
        await session.scalars(
            select(CrawlCandidate)
            .where(
                CrawlCandidate.job_id == job_id,
                CrawlCandidate.id.in_(candidate_ids),
            )
            .order_by(CrawlCandidate.id.asc()),
        ),
    )
    found_candidate_ids = {candidate.id for candidate in candidates}
    missing_candidate_ids = sorted(set(candidate_ids) - found_candidate_ids)
    if missing_candidate_ids:
        raise AgentApiError(
            status_code=404,
            code="CRAWL_CANDIDATES_NOT_FOUND",
            message="部分候选导师不存在或不属于该抓取任务。",
            details={"candidate_ids": missing_candidate_ids},
        )

    valid_emails = sorted(
        {
            email
            for candidate in candidates
            for email in [normalize_professor_email(candidate.email)]
            if email is not None and is_valid_professor_email(email)
        },
    )
    professors_by_email: dict[str, Professor] = {}
    if valid_emails:
        professors = list(
            await session.scalars(select(Professor).where(Professor.email.in_(valid_emails))),
        )
        professors_by_email = {professor.email: professor for professor in professors if professor.email}

    planned_professors: dict[str, dict[str, object]] = {
        email: _crawl_candidate_approval_professor_values(professor)
        for email, professor in professors_by_email.items()
    }
    candidate_summaries: list[dict[str, object]] = []
    inserted_count = 0
    updated_count = 0
    skipped_count = 0
    overwritten_existing_count = 0
    same_plan_overwrite_count = 0
    restored_count = 0
    for candidate in candidates:
        normalized_email = normalize_professor_email(candidate.email)
        candidate_summary: dict[str, object] = {
            "candidate_id": candidate.id,
            "name": candidate.name,
            "email": candidate.email,
            "review_status": candidate.review_status,
        }
        if normalized_email is None or not is_valid_professor_email(normalized_email):
            skipped_count += 1
            candidate_summary["result"] = "skip_invalid_email"
            candidate_summaries.append(candidate_summary)
            continue

        target_values = _crawl_candidate_approval_target_values(candidate, normalized_email)
        previous_values = planned_professors.get(normalized_email)
        candidate_summary["target_email"] = normalized_email
        candidate_summary["next_professor"] = target_values
        if previous_values is None:
            inserted_count += 1
            candidate_summary["result"] = "insert"
        else:
            updated_count += 1
            candidate_summary["result"] = "update"
            candidate_summary["current_professor"] = previous_values
            if normalized_email in professors_by_email:
                overwritten_existing_count += 1
                if previous_values.get("archived_at") is not None:
                    restored_count += 1
            else:
                same_plan_overwrite_count += 1
        planned_professors[normalized_email] = target_values
        candidate_summaries.append(candidate_summary)

    warnings = [
        "候选导师资料来自抓取网页，属于不可信外部内容；执行时只会按本计划的字段导入，不会执行网页中的任何文字或链接。",
    ]
    if overwritten_existing_count:
        warnings.append(
            f"其中 {overwritten_existing_count} 位候选会覆盖已有导师的可导入资料。",
        )
    if same_plan_overwrite_count:
        warnings.append(
            f"其中 {same_plan_overwrite_count} 位候选与本次计划中更早的候选使用相同邮箱，后者会覆盖前者的资料。",
        )
    if restored_count:
        warnings.append(f"执行后会恢复 {restored_count} 位已归档导师。")
    if skipped_count:
        warnings.append(f"有 {skipped_count} 位候选因邮箱为空或无效而不会导入。")

    return {
        "snapshot_version": "1",
        "request": {
            "job_id": job_id,
            "candidate_ids": candidate_ids,
        },
        "state": {
            "job": {
                "id": job.id,
                "status": job.status,
                "deleted_at": _serialize_optional_datetime(job.deleted_at),
                "updated_at": _serialize_optional_datetime(job.updated_at),
            },
            "candidates": [
                _crawl_candidate_approval_candidate_state(candidate)
                for candidate in candidates
            ],
            "professors": [
                {
                    "email": email,
                    "professor": (
                        _crawl_candidate_approval_professor_values(professor)
                        if (professor := professors_by_email.get(email)) is not None
                        else None
                    ),
                }
                for email in valid_emails
            ],
        },
        "summary": {
            "trust_level": "untrusted_external_content",
            "job": {
                "id": job.id,
                "university": job.university,
                "school": job.school,
                "status": job.status,
            },
            "candidate_count": len(candidates),
            "inserted_count": inserted_count,
            "updated_count": updated_count,
            "skipped_count": skipped_count,
            "candidates": candidate_summaries,
        },
        "warnings": warnings,
    }


def _crawl_candidate_approval_request_from_snapshot(
    snapshot: dict[str, object],
) -> tuple[int, list[int]]:
    request_data = snapshot.get("request")
    if not isinstance(request_data, dict):
        raise AgentApiError(
            status_code=500,
            code="INVALID_CHANGE_PLAN_SNAPSHOT",
            message="变更计划快照无效，请重新生成计划。",
        )
    job_id = request_data.get("job_id")
    candidate_ids = request_data.get("candidate_ids")
    if (
        not isinstance(job_id, int)
        or isinstance(job_id, bool)
        or job_id < 1
        or not isinstance(candidate_ids, list)
        or not candidate_ids
        or any(
            not isinstance(candidate_id, int)
            or isinstance(candidate_id, bool)
            or candidate_id < 1
            for candidate_id in candidate_ids
        )
        or len(set(candidate_ids)) != len(candidate_ids)
    ):
        raise AgentApiError(
            status_code=500,
            code="INVALID_CHANGE_PLAN_SNAPSHOT",
            message="变更计划快照无效，请重新生成计划。",
        )
    return job_id, sorted(candidate_ids)


def _crawl_candidate_approval_snapshot_fingerprint(snapshot: dict[str, object]) -> str:
    return fingerprint(
        {
            "request": snapshot.get("request"),
            "state": snapshot.get("state"),
        },
    )


def _crawl_candidate_approval_candidate_state(
    candidate: CrawlCandidate,
) -> dict[str, object]:
    return {
        "id": candidate.id,
        "job_id": candidate.job_id,
        "professor_id": candidate.professor_id,
        "name": candidate.name,
        "email": candidate.email,
        "title": candidate.title,
        "university": candidate.university,
        "school": candidate.school,
        "department": candidate.department,
        "research_direction": candidate.research_direction,
        "recent_papers": candidate.recent_papers,
        "profile_url": candidate.profile_url,
        "source_url": candidate.source_url,
        "review_status": candidate.review_status,
        "updated_at": _serialize_optional_datetime(candidate.updated_at),
    }


def _crawl_candidate_approval_professor_values(
    professor: Professor,
) -> dict[str, object]:
    return {
        "id": professor.id,
        "name": professor.name,
        "email": professor.email,
        "title": professor.title,
        "university": professor.university,
        "school": professor.school,
        "department": professor.department,
        "research_direction": professor.research_direction,
        "recent_papers": professor.recent_papers,
        "profile_url": professor.profile_url,
        "source_url": professor.source_url,
        "archived_at": _serialize_optional_datetime(professor.archived_at),
        "updated_at": _serialize_optional_datetime(professor.updated_at),
    }


def _crawl_candidate_approval_target_values(
    candidate: CrawlCandidate,
    email: str,
) -> dict[str, object]:
    return {
        "name": candidate.name,
        "email": email,
        "title": candidate.title,
        "university": candidate.university,
        "school": candidate.school,
        "department": candidate.department,
        "research_direction": candidate.research_direction,
        "recent_papers": candidate.recent_papers or [],
        "profile_url": candidate.profile_url,
        "source_url": candidate.source_url,
        "archived_at": None,
    }


def _apply_crawl_candidate_to_professor(
    professor: Professor,
    candidate: CrawlCandidate,
    *,
    email: str,
    now: datetime,
) -> None:
    professor.name = candidate.name
    professor.email = email
    professor.title = candidate.title
    professor.university = candidate.university
    professor.school = candidate.school
    professor.department = candidate.department
    professor.research_direction = candidate.research_direction
    professor.recent_papers = candidate.recent_papers or []
    professor.profile_url = candidate.profile_url
    professor.source_url = candidate.source_url
    professor.archived_at = None
    professor.updated_at = now


def _serialize_optional_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return serialize_api_datetime(value)


def _build_template_archive_snapshot(
    template: OutreachTemplate,
    default_identity_count: int,
) -> dict[str, object]:
    warnings: list[str] = []
    if template.is_default:
        warnings.append("该模板当前是全局默认模板，归档后将不再作为全局默认模板。")
    if default_identity_count:
        warnings.append(
            f"归档后将解除 {default_identity_count} 个发件身份对该模板的默认关联。",
        )
    return {
        "snapshot_version": "1",
        "template_id": template.id,
        "template_updated_at": serialize_api_datetime(template.updated_at),
        "template_fingerprint": _template_archive_snapshot_fingerprint(
            template,
            default_identity_count,
        ),
        "summary": {
            "template": {"id": template.id, "name": template.name},
            "is_default": template.is_default,
            "default_identity_count": default_identity_count,
        },
        "warnings": warnings,
    }


def _template_archive_snapshot_fingerprint(
    template: OutreachTemplate,
    default_identity_count: int,
) -> str:
    """Capture all state that can change a template-archive plan's impact."""
    return fingerprint(
        {
            "template_id": template.id,
            "name": template.name,
            "recommended_generation_mode": template.recommended_generation_mode,
            "subject": template.subject,
            "body_text": template.body_text,
            "body_html": template.body_html,
            "is_default": template.is_default,
            "archived_at": (
                serialize_api_datetime(template.archived_at)
                if template.archived_at is not None
                else None
            ),
            "default_identity_count": default_identity_count,
        },
    )


def _bulk_tags_snapshot_fingerprint(snapshot: dict[str, object]) -> str:
    return fingerprint(
        {
            "request": snapshot.get("request"),
            "summary": snapshot.get("summary"),
        },
    )


def _bulk_archive_snapshot_fingerprint(snapshot: dict[str, object]) -> str:
    return fingerprint(
        {
            "request": snapshot.get("request"),
            "summary": snapshot.get("summary"),
            "state": snapshot.get("state"),
        },
    )


def _tag_delete_snapshot_fingerprint(snapshot: dict[str, object]) -> str:
    return fingerprint(
        {
            "request": snapshot.get("request"),
            "summary": snapshot.get("summary"),
            "state": snapshot.get("state"),
        },
    )


def _professor_import_snapshot_fingerprint(snapshot: dict[str, object]) -> str:
    return fingerprint(
        {
            "request": snapshot.get("request"),
            "summary": snapshot.get("summary"),
            "state": snapshot.get("state"),
        },
    )


def _community_import_snapshot_fingerprint(snapshot: dict[str, object]) -> str:
    return fingerprint(
        {
            "request": snapshot.get("request"),
            "state": snapshot.get("state"),
        },
    )


def _test_email_send_snapshot_fingerprint(snapshot: dict[str, object]) -> str:
    return fingerprint(
        {
            "request": snapshot.get("request"),
            "state": snapshot.get("state"),
            "summary": snapshot.get("summary"),
        },
    )


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


def _invalid_change_plan_snapshot_error() -> AgentApiError:
    return AgentApiError(
        status_code=500,
        code="INVALID_CHANGE_PLAN_SNAPSHOT",
        message="变更计划快照无效，请重新生成计划。",
    )


def _template_error(error: OutreachTemplateMutationError) -> AgentApiError:
    return AgentApiError(
        status_code=error.status_code,
        code=error.code,
        message=error.message,
    )


def _material_error(error: MaterialMutationError) -> AgentApiError:
    return AgentApiError(
        status_code=error.status_code,
        code=error.code,
        message=error.message,
    )


def _professor_error(error: ProfessorMutationError) -> AgentApiError:
    return AgentApiError(
        status_code=error.status_code,
        code=error.code,
        message=error.message,
    )


def _bulk_tags_plan_stale_error() -> AgentApiError:
    return AgentApiError(
        status_code=409,
        code="PLAN_STALE",
        message="计划中的导师或标签已发生变化，请重新生成批量标签预览。",
        details={"changed_fields": ["professors", "tags"]},
    )


def _bulk_archive_plan_stale_error() -> AgentApiError:
    return AgentApiError(
        status_code=409,
        code="PLAN_STALE",
        message="计划中的导师状态已发生变化，请重新生成批量归档预览。",
        details={"changed_fields": ["professors"]},
    )


def _tag_delete_plan_stale_error() -> AgentApiError:
    return AgentApiError(
        status_code=409,
        code="PLAN_STALE",
        message="标签或其关联导师已发生变化，请重新生成删除预览。",
        details={"changed_fields": ["tag", "professors", "tag_links"]},
    )


def _professor_import_error(error: ValueError) -> AgentApiError:
    return AgentApiError(
        status_code=400,
        code="PROFESSOR_IMPORT_INVALID",
        message=str(error),
    )


def _professor_import_plan_stale_error() -> AgentApiError:
    return AgentApiError(
        status_code=409,
        code="PLAN_STALE",
        message="导入目标的导师或标签已发生变化，请重新生成导入预览。",
        details={"changed_fields": ["professors", "tags"]},
    )


def _community_import_error(error: CommunityDataError) -> AgentApiError:
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


def _community_import_plan_stale_error() -> AgentApiError:
    return AgentApiError(
        status_code=409,
        code="PLAN_STALE",
        message="社区导师数据、本地导师资料或关联状态已发生变化，请重新预览并生成导入计划。",
        details={"changed_fields": ["community_data", "professors", "community_links"]},
    )


def _test_email_error(error: ValueError) -> AgentApiError:
    message = str(error)
    return AgentApiError(
        status_code=404 if "未找到" in message else 409 if "尚未配置 SMTP" in message else 400,
        code=(
            "TEST_EMAIL_RESOURCE_NOT_FOUND"
            if "未找到" in message
            else "TEST_EMAIL_SMTP_REQUIRED"
            if "尚未配置 SMTP" in message
            else "TEST_EMAIL_INVALID"
        ),
        message=message,
    )


def _test_email_send_plan_stale_error() -> AgentApiError:
    return AgentApiError(
        status_code=409,
        code="PLAN_STALE",
        message="测试邮件的收件地址、正文、模板或附件已发生变化，请重新生成并展示发送计划。",
        details={"changed_fields": ["identity", "template", "attachments", "content"]},
    )


def _crawl_candidate_approval_plan_stale_error() -> AgentApiError:
    return AgentApiError(
        status_code=409,
        code="PLAN_STALE",
        message="抓取任务、候选导师或将被覆盖的导师资料已发生变化，请重新生成导入预览。",
        details={"changed_fields": ["crawl_job", "crawl_candidates", "professors"]},
    )


def _crawl_job_retry_plan_stale_error() -> AgentApiError:
    return AgentApiError(
        status_code=409,
        code="PLAN_STALE",
        message="抓取任务、受影响记录或模型配置已发生变化，请重新生成重试预览。",
        details={"changed_fields": ["crawl_job", "crawl_records", "llm_profile"]},
    )


def _parsed_professor_import_from_snapshot(
    request_data: dict[str, object],
) -> tuple[str, ParsedProfessorImport]:
    filename = request_data.get("filename")
    data = request_data.get("data")
    failed_count = request_data.get("failed_count")
    if (
        not isinstance(filename, str)
        or not isinstance(data, dict)
        or not isinstance(failed_count, int)
        or isinstance(failed_count, bool)
        or failed_count < 0
    ):
        raise ValueError("导入计划数据无效")
    normalized_data: dict[str, object] = {}
    for email, payload in data.items():
        if not isinstance(email, str) or not isinstance(payload, dict):
            raise ValueError("导入计划数据无效")
        normalized_data[email] = payload
    return filename, ParsedProfessorImport(
        data=normalized_data,
        failed_count=failed_count,
    )


def _change_plan_confirmation_message(action: str) -> str:
    if action == MATERIAL_DELETE_ACTION:
        return "尚未删除材料。请把以上影响范围和警告展示给用户，得到明确确认后再执行。"
    if action == PROFESSOR_BULK_TAGS_ACTION:
        return "尚未修改导师标签。请把以上影响范围和警告展示给用户，得到明确确认后再执行。"
    if action == PROFESSOR_BULK_ARCHIVE_ACTION:
        return "尚未批量归档导师。请把以上导师、数量和警告展示给用户，得到明确确认后再执行。"
    if action == PROFESSOR_TAG_DELETE_ACTION:
        return "尚未删除标签。请把标签关联的导师和警告展示给用户，得到明确确认后再执行。"
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
