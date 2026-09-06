from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.models.crawl_job import (
    CrawlCandidate,
    CrawlJob,
    CrawlJobStatus,
    CrawlPage,
    CrawlPageTask,
)
from app.modules.crawler.candidate_identity import (
    candidate_identity_values,
    canonical_candidate_clause,
    consolidate_candidate_identity,
    find_canonical_candidate_for_identity,
    merge_candidate_payload as merge_candidate_payload_shared,
)
from app.modules.professors.public import (
    RECENT_PAPERS_MAX_ITEMS,
    normalize_professor_title,
    normalize_recent_papers,
)

from ..runtime.lease import fence_crawler_claim
from ..runtime.url_utils import recover_embedded_absolute_url
from .browser import extract_first_email_from_text as extract_first_email_from_text
from .payloads import (
    CandidateBatchFailure as CandidateBatchFailure,
    CandidatePersistenceResult as CandidatePersistenceResult,
    PageSnapshot as PageSnapshot,
    ProfessorCandidatePayload as ProfessorCandidatePayload,
    SharedCandidateSaveResult as SharedCandidateSaveResult,
    _clamp_confidence as _clamp_confidence,
    _clean_optional as _clean_optional,
    _clean_required as _clean_required,
)
from .snapshots import MAX_TEXT_CHARS as MAX_TEXT_CHARS

if TYPE_CHECKING:
    from .tools import CrawlToolContext


def _is_spa_route_fragment(fragment: str) -> bool:
    return fragment.startswith("/") or fragment.startswith("!/")


def normalize_navigable_url(
    value: object, *, base_url: str | None = None
) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    absolute = urljoin(base_url or "", raw) if base_url else raw
    parsed = urlparse(absolute)
    if not _is_spa_route_fragment(parsed.fragment):
        parsed = parsed._replace(fragment="")
    normalized = parsed.geturl().rstrip("/")
    return normalized or None


def normalize_candidate_profile_url(
    value: object, *, base_url: str | None = None
) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if base_url and _looks_like_hostname_without_scheme(raw):
        base_scheme = urlparse(base_url).scheme.lower()
        raw = f"{base_scheme if base_scheme in {'http', 'https'} else 'https'}://{raw}"
    absolute = urljoin(base_url or "", raw) if base_url else raw
    return normalize_navigable_url(recover_embedded_absolute_url(absolute))


def _looks_like_hostname_without_scheme(value: str) -> bool:
    if value.startswith(("/", "./", "../", "//", "#", "?")) or "://" in value:
        return False
    authority = re.split(r"[/#?]", value, maxsplit=1)[0]
    if "@" in authority:
        return False
    host = (
        authority.rsplit(":", 1)[0]
        if authority.rsplit(":", 1)[-1].isdigit()
        else authority
    )
    labels = host.rstrip(".").split(".")
    if len(labels) < 3:
        return False
    return all(
        label
        and len(label) <= 63
        and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
        for label in labels
    )


def _normalize_listing_url(value: object, *, base_url: str | None = None) -> str | None:
    return normalize_navigable_url(value, base_url=base_url)


def _candidate_profile_url_matches_known_listing_url(
    profile_url: str | None, listing_urls: set[str]
) -> bool:
    return bool(profile_url and profile_url in listing_urls)


def _clear_listing_profile_url(
    payload: dict[str, Any], removed_profile_url: str
) -> None:
    payload["profile_url"] = None
    field_confidence = payload.get("field_confidence")
    if isinstance(field_confidence, dict):
        field_confidence.pop("profile_url", None)
    evidence = payload.get("evidence")
    if not isinstance(evidence, dict):
        evidence = {}
    evidence["profile_url_removed_reason"] = "matches_known_listing_url"
    evidence["removed_profile_url"] = removed_profile_url
    payload["evidence"] = evidence


def _candidate_missing_contact_path(payload: dict[str, Any]) -> bool:
    email = str(payload.get("email") or "").strip()
    profile_url = str(payload.get("profile_url") or "").strip()
    return not email and not profile_url


_MERGEABLE_TEXT_FIELDS = (
    "email",
    "title",
    "university",
    "school",
    "department",
    "research_direction",
    "profile_url",
    "source_url",
)


def _field_source_entry(payload: dict[str, Any], field_name: str) -> dict[str, object]:
    return {
        "source_kind": payload.get("source_kind"),
        "source_chunk_id": payload.get("source_chunk_id"),
        "source_url": payload.get("source_url"),
        "confidence": _field_confidence(payload.get("field_confidence"), field_name),
        "boundary_risk": bool(payload.get("boundary_risk")),
    }


def _field_confidence(value: object, field_name: str) -> float | None:
    if not isinstance(value, dict):
        return None
    raw = value.get(field_name)
    return float(raw) if isinstance(raw, (int, float)) else None


def _merge_candidate_payload(existing: CrawlCandidate, payload: dict[str, Any]) -> bool:
    return merge_candidate_payload_shared(existing, payload)


async def _known_listing_urls_for_job(
    session: AsyncSession, *, job_id: int, start_url: str
) -> set[str]:
    listing_urls: set[str] = set()
    job = await session.get(CrawlJob, job_id)
    if job is not None:
        for url in [job.start_url, *(job.start_urls or [])]:
            normalized = _normalize_listing_url(url, base_url=start_url)
            if normalized:
                listing_urls.add(normalized)
    else:
        normalized = _normalize_listing_url(start_url, base_url=start_url)
        if normalized:
            listing_urls.add(normalized)

    rows = await session.scalars(
        select(CrawlPageTask.normalized_url).where(CrawlPageTask.job_id == job_id)
    )
    for url in rows:
        normalized = _normalize_listing_url(url, base_url=start_url)
        if normalized:
            listing_urls.add(normalized)
    return listing_urls


async def _find_existing_candidate_for_payload(
    session: AsyncSession,
    *,
    job_id: int,
    name: str | None,
    email: str | None,
    profile_url: str | None,
    identity_key: str | None = None,
) -> CrawlCandidate | None:
    row = await find_canonical_candidate_for_identity(
        session,
        job_id=job_id,
        name=name,
        email=email,
        profile_url=profile_url,
    )
    if row is not None:
        return row
    if identity_key:
        return await session.scalar(
            select(CrawlCandidate).where(
                CrawlCandidate.job_id == job_id,
                CrawlCandidate.identity_key == identity_key,
                canonical_candidate_clause(),
            )
        )
    return None


class CrawlJobPaused(RuntimeError):
    """Raised internally when a crawl job is paused at a safe checkpoint."""


class CrawlJobCanceled(RuntimeError):
    """Raised internally when a crawl job is canceled at a safe checkpoint."""


def normalize_candidate_payload(
    candidate: ProfessorCandidatePayload,
    *,
    university: str,
    school: str,
) -> dict[str, Any]:
    papers = normalize_recent_papers(
        candidate.recent_papers, max_items=RECENT_PAPERS_MAX_ITEMS
    )
    field_confidence = None
    if candidate.field_confidence is not None:
        field_confidence = {
            str(key).strip(): _clamp_confidence(value)
            for key, value in candidate.field_confidence.items()
            if str(key).strip()
        }

    return {
        "name": _clean_required(candidate.name),
        "email": _first_valid_email(candidate.email),
        "title": normalize_professor_title(_clean_optional(candidate.title)),
        "university": _clean_optional(candidate.university)
        or _clean_required(university),
        "school": _clean_optional(candidate.school) or _clean_required(school),
        "department": _clean_optional(candidate.department),
        "research_direction": _clean_optional(candidate.research_direction),
        "recent_papers": papers,
        "profile_url": _clean_optional(candidate.profile_url),
        "source_url": _clean_optional(candidate.source_url),
        "confidence": _clamp_confidence(candidate.confidence),
        "field_confidence": field_confidence,
        "evidence": candidate.evidence,
        "source_chunk_id": getattr(candidate, "source_chunk_id", None),
        "source_kind": getattr(candidate, "source_kind", None),
        "boundary_risk": bool(getattr(candidate, "boundary_risk", False)),
        "identity_key": getattr(candidate, "identity_key", None),
        "merge_history": getattr(candidate, "merge_history", None),
        "field_sources": getattr(candidate, "field_sources", None),
        "conflicts": getattr(candidate, "conflicts", None),
    }


def _first_valid_email(value: str | None) -> str | None:
    cleaned = _clean_optional(value)
    if not cleaned:
        return None
    return extract_first_email_from_text(cleaned)


def _normalize_candidate_payloads_for_save(
    ctx: CrawlToolContext,
    candidates: Sequence[ProfessorCandidatePayload | dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[CandidateBatchFailure]]:
    payloads: list[dict[str, Any]] = []
    failed_items: list[CandidateBatchFailure] = []
    for index, candidate in enumerate(candidates):
        try:
            payload = normalize_candidate_payload(
                candidate,
                university=ctx.university,
                school=ctx.school,
            )
            if payload.get("source_kind") in (None, ""):
                payload["source_kind"] = (
                    "profile_page" if ctx.entry_type == "profile" else "list_chunk"
                )
            payloads.append(payload)
        except (TypeError, ValueError) as exc:
            failed_items.append(
                {
                    "index": index,
                    "name": _clean_optional(getattr(candidate, "name", None)),
                    "reason": str(exc),
                }
            )
    return payloads, failed_items


def _filter_accepted_candidate_payloads(
    payloads: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[CandidateBatchFailure]]:
    accepted_payloads: list[dict[str, Any]] = []
    rejected_items: list[CandidateBatchFailure] = []
    for index, payload in enumerate(payloads):
        if _candidate_missing_contact_path(payload):
            rejected_items.append(
                {
                    "index": index,
                    "name": _clean_optional(payload.get("name")),
                    "reason": "缺少邮箱和详情页链接，无法用于联系或后续补全",
                }
            )
            continue
        accepted_payloads.append(payload)
    return accepted_payloads, rejected_items


async def _normalize_candidate_profile_urls_for_save(
    ctx: CrawlToolContext,
    payloads: Sequence[dict[str, Any]],
) -> None:
    """Normalize candidate profile URLs before contact-path validation."""

    known_listing_urls: set[str] = set(ctx.known_listing_urls)
    if ctx.entry_type != "profile":
        async with ctx.session_factory() as session:
            known_listing_urls.update(
                await _known_listing_urls_for_job(
                    session,
                    job_id=ctx.job_id,
                    start_url=ctx.start_url,
                )
            )

    for payload in payloads:
        normalized_profile_url = normalize_candidate_profile_url(
            payload.get("profile_url"),
            base_url=ctx.start_url,
        )
        if _candidate_profile_url_matches_known_listing_url(
            normalized_profile_url,
            known_listing_urls,
        ):
            _clear_listing_profile_url(payload, normalized_profile_url or "")
        else:
            payload["profile_url"] = normalized_profile_url


async def save_candidate_payloads_shared(
    ctx: CrawlToolContext,
    candidates: Sequence[ProfessorCandidatePayload | dict[str, Any]],
) -> SharedCandidateSaveResult:
    payloads, failed_items = _normalize_candidate_payloads_for_save(ctx, candidates)
    if failed_items:
        return {
            "attempted_count": len(candidates),
            "saved_count": 0,
            "merged_count": 0,
            "skipped_duplicate_count": 0,
            "rejected_count": 0,
            "rejected_items": failed_items,
            "saved": [],
        }
    await _normalize_candidate_profile_urls_for_save(ctx, payloads)
    accepted_payloads, rejected_items = _filter_accepted_candidate_payloads(payloads)
    persistence = await _save_normalized_candidate_payloads(ctx, accepted_payloads)
    return {
        "attempted_count": len(candidates),
        "saved_count": len(persistence.saved),
        "merged_count": persistence.merged_count,
        "skipped_duplicate_count": persistence.skipped_duplicate_count,
        "rejected_count": len(rejected_items),
        "rejected_items": rejected_items,
        "saved": persistence.saved,
    }


async def _save_normalized_candidate_payloads(
    ctx: CrawlToolContext,
    payloads: Sequence[dict[str, Any]],
) -> CandidatePersistenceResult:
    saved: list[CrawlCandidate] = []
    merged_count = 0
    skipped_duplicate_count = 0
    async with ctx.session_factory() as session:
        if ctx.claim_fence is not None and not await fence_crawler_claim(
            session,
            ctx.claim_fence,
        ):
            await session.rollback()
            return CandidatePersistenceResult(saved=[])
        if await _is_crawl_job_stopped(session, ctx.job_id):
            return CandidatePersistenceResult(saved=[])

        for payload in payloads:
            payload["recent_papers"] = normalize_recent_papers(
                payload.get("recent_papers")
            )
            email = payload["email"]
            normalized_email = str(email).lower() if email else None
            normalized_profile_url = payload.get("profile_url")
            identity_key = (
                payload.get("identity_key")
                or normalized_email
                or normalized_profile_url
            )

            existing = await _find_existing_candidate_for_payload(
                session,
                job_id=ctx.job_id,
                name=payload.get("name"),
                email=normalized_email,
                profile_url=normalized_profile_url,
                identity_key=identity_key,
            )
            identities = candidate_identity_values(
                name=payload.get("name"),
                email=normalized_email,
                profile_url=normalized_profile_url,
            )
            if existing is not None:
                if _merge_candidate_payload(existing, payload):
                    merged_count += 1
                else:
                    skipped_duplicate_count += 1
                await consolidate_candidate_identity(
                    session,
                    existing,
                    additional_identities=identities,
                )
                continue

            if not payload.get("identity_key"):
                payload["identity_key"] = identity_key
            if not payload.get("field_sources"):
                payload["field_sources"] = {
                    field_name: _field_source_entry(payload, field_name)
                    for field_name in (*_MERGEABLE_TEXT_FIELDS, "recent_papers")
                    if payload.get(field_name) not in (None, "", [])
                }

            if await _is_crawl_job_stopped(session, ctx.job_id):
                await session.rollback()
                return CandidatePersistenceResult(saved=[])

            row = CrawlCandidate(job_id=ctx.job_id, **payload)
            try:
                async with session.begin_nested():
                    session.add(row)
                    await session.flush()
            except IntegrityError:
                existing = await _find_existing_candidate_for_payload(
                    session,
                    job_id=ctx.job_id,
                    name=payload.get("name"),
                    email=normalized_email,
                    profile_url=normalized_profile_url,
                    identity_key=identity_key,
                )
                if existing is None:
                    raise
                if _merge_candidate_payload(existing, payload):
                    merged_count += 1
                else:
                    skipped_duplicate_count += 1
                await consolidate_candidate_identity(
                    session,
                    existing,
                    additional_identities=identities,
                )
                continue
            canonical = await consolidate_candidate_identity(
                session,
                row,
                additional_identities=identities,
            )
            if canonical.id == row.id:
                saved.append(row)
            else:
                merged_count += 1

        if await _is_crawl_job_stopped(session, ctx.job_id):
            await session.rollback()
            return CandidatePersistenceResult(saved=[])

        await session.commit()
        for row in saved:
            await session.refresh(row)
    return CandidatePersistenceResult(
        saved=saved,
        merged_count=merged_count,
        skipped_duplicate_count=skipped_duplicate_count,
    )


async def record_page_snapshot(
    ctx: CrawlToolContext, snapshot: PageSnapshot
) -> CrawlPage | None:
    row = CrawlPage(
        job_id=ctx.job_id,
        url=snapshot.url,
        parent_url=None,
        fetch_method=snapshot.fetch_method,
        page_type="unknown",
        status=snapshot.status,
        title=snapshot.title,
        text_excerpt=snapshot.text[:MAX_TEXT_CHARS] or None,
        error_message=snapshot.error_message,
        created_at=utc_now(),
    )
    async with ctx.session_factory() as session:
        if await _is_crawl_job_stopped(session, ctx.job_id):
            return None

        session.add(row)
        if await _is_crawl_job_stopped(session, ctx.job_id):
            await session.rollback()
            return None

        await session.commit()
        await session.refresh(row)
        snapshot.page_id = row.id
        return row


async def ensure_crawl_job_can_continue(session: AsyncSession, job_id: int) -> None:
    status = await _get_job_status(session, job_id)
    if status == CrawlJobStatus.PAUSED.value:
        raise CrawlJobPaused()
    if status == CrawlJobStatus.CANCELED.value:
        raise CrawlJobCanceled()


async def _is_crawl_job_stopped(session: AsyncSession, job_id: int) -> bool:
    status = await _get_job_status(session, job_id)
    return status in {CrawlJobStatus.PAUSED.value, CrawlJobStatus.CANCELED.value}


async def _get_job_status(session: AsyncSession, job_id: int) -> str | None:
    return await session.scalar(select(CrawlJob.status).where(CrawlJob.id == job_id))
