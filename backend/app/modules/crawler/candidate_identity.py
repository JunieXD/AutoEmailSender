from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import parse_qsl, unquote, urlsplit

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.query_chunks import chunked_values, unique_positive_ids
from app.models import CrawlCandidate, CrawlCandidateIdentityKey
from app.modules.crawler.pages.domain_policy import is_same_registrable_domain
from app.modules.crawler.v2.url_utils import normalize_url, recover_embedded_absolute_url
from app.modules.professors.public import (
    is_valid_professor_email,
    normalize_professor_email,
    normalize_recent_papers,
)


EMAIL_IDENTITY_KEY = "email"
PROFILE_URL_IDENTITY_KEY = "profile_url"
PROFILE_RELATION_IDENTITY_KEY = "profile_relation"
_IDENTITY_KEY_TYPES = {
    EMAIL_IDENTITY_KEY,
    PROFILE_URL_IDENTITY_KEY,
    PROFILE_RELATION_IDENTITY_KEY,
}
_MERGEABLE_TEXT_FIELDS = (
    "name",
    "email",
    "title",
    "university",
    "school",
    "department",
    "research_direction",
    "profile_url",
    "source_url",
)
_SOURCE_PRIORITY = {
    "manual": 5,
    "profile_page": 4,
    "page_chunk": 3,
    "list_chunk": 2,
    None: 1,
}
_REVIEW_STATUS_PRIORITY = {
    "merged": 0,
    "rejected": 1,
    "pending": 2,
    "accepted": 3,
}


def canonical_candidate_clause():
    return CrawlCandidate.merged_into_candidate_id.is_(None)


def normalize_candidate_email(value: object) -> str | None:
    normalized = normalize_professor_email(str(value) if value is not None else None)
    if normalized is None or not is_valid_professor_email(normalized):
        return None
    return normalized


def normalize_candidate_profile_url(value: object) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        normalized = normalize_url(recover_embedded_absolute_url(raw))
    except (TypeError, ValueError):
        return None
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return normalized or None


def candidate_identity_values(
    *,
    name: object = None,
    email: object = None,
    profile_url: object = None,
    include_profile_relations: bool = True,
) -> tuple[tuple[str, str], ...]:
    values: list[tuple[str, str]] = []
    normalized_email = normalize_candidate_email(email)
    if normalized_email:
        values.append((EMAIL_IDENTITY_KEY, normalized_email))
    normalized_profile_url = normalize_candidate_profile_url(profile_url)
    if normalized_profile_url:
        values.append((PROFILE_URL_IDENTITY_KEY, normalized_profile_url))
        normalized_name = _normalize_candidate_name(name)
        if normalized_name and include_profile_relations:
            values.extend(
                (
                    PROFILE_RELATION_IDENTITY_KEY,
                    _profile_relation_fingerprint(normalized_name, related_url),
                )
                for related_url in _related_profile_urls(str(profile_url))
            )
    return tuple(values)


def _normalize_candidate_name(value: object) -> str:
    return "".join(str(value or "").split()).casefold()


def _profile_relation_fingerprint(name: str, profile_url: str) -> str:
    return hashlib.sha256(f"{name}\0{profile_url}".encode("utf-8")).hexdigest()


def _related_profile_urls(profile_url: str) -> tuple[str, ...]:
    normalized_primary = normalize_candidate_profile_url(profile_url)
    if normalized_primary is None:
        return ()
    related = [normalized_primary]
    parsed = urlsplit(profile_url)
    sections = [parsed.query]
    if "=" in parsed.fragment:
        sections.append(parsed.fragment)
    for section in sections:
        for _key, value in parse_qsl(section, keep_blank_values=True):
            decoded = _decode_url_parameter(value.strip())
            normalized = normalize_candidate_profile_url(decoded)
            if normalized and normalized not in related:
                related.append(normalized)
    return tuple(related)


def _decode_url_parameter(value: str) -> str:
    decoded = value
    for _ in range(10):
        parsed = urlsplit(decoded)
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            break
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    return decoded


def _preferred_related_profile_url(
    first: CrawlCandidate,
    second: CrawlCandidate,
) -> tuple[str, CrawlCandidate] | None:
    first_url = normalize_candidate_profile_url(first.profile_url)
    second_url = normalize_candidate_profile_url(second.profile_url)
    if not first_url or not second_url:
        return None
    first_related = set(_related_profile_urls(first.profile_url or ""))
    second_related = set(_related_profile_urls(second.profile_url or ""))
    if second_url not in first_related and first_url not in second_related:
        return None
    for candidate, profile_url in ((first, first_url), (second, second_url)):
        source_url = normalize_candidate_profile_url(candidate.source_url)
        if source_url and is_same_registrable_domain(profile_url, source_url):
            return profile_url, candidate
    return first_url, first


async def resolve_canonical_candidate(
    session: AsyncSession,
    candidate: CrawlCandidate,
) -> CrawlCandidate:
    current = candidate
    visited: set[int] = set()
    while current.merged_into_candidate_id is not None:
        if current.id in visited:
            raise RuntimeError("检测到循环的候选导师归并关系")
        visited.add(current.id)
        parent = await session.get(CrawlCandidate, current.merged_into_candidate_id)
        if parent is None or parent.job_id != candidate.job_id:
            raise RuntimeError("候选导师归并目标不存在或不属于同一任务")
        current = parent
    if candidate.id != current.id and candidate.merged_into_candidate_id != current.id:
        candidate.merged_into_candidate_id = current.id
    return current


async def find_canonical_candidate_for_identity(
    session: AsyncSession,
    *,
    job_id: int,
    name: object = None,
    email: object = None,
    profile_url: object = None,
) -> CrawlCandidate | None:
    identities = candidate_identity_values(
        name=name,
        email=email,
        profile_url=profile_url,
        include_profile_relations=False,
    )
    for key_type, normalized_value in identities:
        identity_key = await session.scalar(
            select(CrawlCandidateIdentityKey).where(
                CrawlCandidateIdentityKey.job_id == job_id,
                CrawlCandidateIdentityKey.key_type == key_type,
                CrawlCandidateIdentityKey.normalized_value == normalized_value,
            )
        )
        if identity_key is not None:
            candidate = await session.get(CrawlCandidate, identity_key.candidate_id)
            if candidate is not None:
                return await resolve_canonical_candidate(session, candidate)

    # Compatibility for databases created directly from metadata and for rows
    # that predate the identity-key migration.
    normalized_email = normalize_candidate_email(email)
    if normalized_email:
        candidates = list(
            await session.scalars(
                select(CrawlCandidate).where(
                    CrawlCandidate.job_id == job_id,
                    canonical_candidate_clause(),
                    CrawlCandidate.email.is_not(None),
                )
            )
        )
        for candidate in candidates:
            if normalize_candidate_email(candidate.email) == normalized_email:
                return candidate

    normalized_profile_url = normalize_candidate_profile_url(profile_url)
    if normalized_profile_url:
        candidates = list(
            await session.scalars(
                select(CrawlCandidate).where(
                    CrawlCandidate.job_id == job_id,
                    canonical_candidate_clause(),
                    CrawlCandidate.profile_url.is_not(None),
                )
            )
        )
        for candidate in candidates:
            if normalize_candidate_profile_url(candidate.profile_url) == normalized_profile_url:
                return candidate
    return None


def _merge_json_dict(current: object, incoming: object) -> dict[str, object]:
    merged: dict[str, object] = {}
    if isinstance(current, dict):
        merged.update(current)
    if isinstance(incoming, dict):
        merged.update(incoming)
    return merged


def _append_json_list(
    current: object,
    item: dict[str, object],
    *,
    limit: int = 50,
) -> list[dict[str, object]]:
    entries = list(current) if isinstance(current, list) else []
    entries.append(item)
    return entries[-limit:]


def _field_confidence(value: object, field_name: str) -> float | None:
    if not isinstance(value, dict):
        return None
    raw = value.get(field_name)
    return float(raw) if isinstance(raw, (int, float)) else None


def _field_source_entry(payload: dict[str, Any], field_name: str) -> dict[str, object]:
    field_sources = payload.get("field_sources")
    if isinstance(field_sources, dict):
        stored = field_sources.get(field_name)
        if isinstance(stored, dict):
            return dict(stored)
    return {
        "source_kind": payload.get("source_kind"),
        "source_chunk_id": payload.get("source_chunk_id"),
        "source_url": payload.get("source_url"),
        "confidence": _field_confidence(payload.get("field_confidence"), field_name),
        "boundary_risk": bool(payload.get("boundary_risk")),
    }


def _stored_field_source(candidate: CrawlCandidate, field_name: str) -> dict[str, object]:
    field_sources = candidate.field_sources
    if isinstance(field_sources, dict):
        stored = field_sources.get(field_name)
        if isinstance(stored, dict):
            return stored
    return {
        "source_kind": candidate.source_kind,
        "confidence": _field_confidence(candidate.field_confidence, field_name),
        "boundary_risk": bool(candidate.boundary_risk),
    }


def _should_replace_field(
    *,
    old_value: object,
    new_value: object,
    old_source: dict[str, object],
    new_source: dict[str, object],
) -> bool:
    if new_value in (None, ""):
        return False
    if old_value in (None, ""):
        return True
    old_kind = old_source.get("source_kind")
    new_kind = new_source.get("source_kind")
    old_priority = _SOURCE_PRIORITY.get(str(old_kind) if old_kind is not None else None, 1)
    new_priority = _SOURCE_PRIORITY.get(str(new_kind) if new_kind is not None else None, 1)
    if old_kind == "manual" and new_kind != "manual":
        return False
    if new_priority > old_priority:
        return True
    old_boundary_risk = bool(old_source.get("boundary_risk"))
    new_boundary_risk = bool(new_source.get("boundary_risk"))
    if old_boundary_risk != new_boundary_risk:
        return old_boundary_risk and not new_boundary_risk
    old_confidence = old_source.get("confidence")
    new_confidence = new_source.get("confidence")
    old_score = float(old_confidence) if isinstance(old_confidence, (int, float)) else 0.0
    new_score = float(new_confidence) if isinstance(new_confidence, (int, float)) else 0.0
    return new_score > old_score + 0.2


def merge_candidate_payload(
    existing: CrawlCandidate,
    payload: dict[str, Any],
    *,
    merged_candidate_id: int | None = None,
) -> bool:
    changed = False
    field_sources = dict(existing.field_sources) if isinstance(existing.field_sources, dict) else {}
    conflicts = dict(existing.conflicts) if isinstance(existing.conflicts, dict) else {}
    merge_event: dict[str, object] = {
        "merged_at": datetime.now(timezone.utc).isoformat(),
        "merged_candidate_id": merged_candidate_id,
        "source_kind": payload.get("source_kind"),
        "source_chunk_id": payload.get("source_chunk_id"),
        "source_url": payload.get("source_url"),
        "updated_fields": [],
        "conflict_fields": [],
    }

    for field_name in _MERGEABLE_TEXT_FIELDS:
        new_value = payload.get(field_name)
        if new_value in (None, ""):
            continue
        old_value = getattr(existing, field_name)
        new_source = _field_source_entry(payload, field_name)
        replace = _should_replace_field(
            old_value=old_value,
            new_value=new_value,
            old_source=_stored_field_source(existing, field_name),
            new_source=new_source,
        )
        if replace:
            setattr(existing, field_name, new_value)
            field_sources[field_name] = new_source
            merge_event["updated_fields"].append(field_name)  # type: ignore[index]
            changed = True
        elif field_name not in {"source_url", "profile_url"} and old_value != new_value:
            conflict = {
                "kept": old_value,
                "incoming": new_value,
                "incoming_source": new_source,
            }
            if conflicts.get(field_name) != conflict:
                conflicts[field_name] = conflict
                merge_event["conflict_fields"].append(field_name)  # type: ignore[index]
                changed = True

    existing_papers = normalize_recent_papers(existing.recent_papers)
    incoming_papers = normalize_recent_papers(payload.get("recent_papers"))
    old_papers_source = _stored_field_source(existing, "recent_papers")
    new_papers_source = _field_source_entry(payload, "recent_papers")
    if (
        old_papers_source.get("source_kind") == "manual"
        and new_papers_source.get("source_kind") != "manual"
    ):
        merged_papers = existing_papers
    elif (
        new_papers_source.get("source_kind") == "manual"
        and old_papers_source.get("source_kind") != "manual"
    ):
        merged_papers = incoming_papers
    else:
        merged_papers = normalize_recent_papers([*existing_papers, *incoming_papers])
    if merged_papers != existing_papers:
        existing.recent_papers = merged_papers
        field_sources["recent_papers"] = new_papers_source
        merge_event["updated_fields"].append("recent_papers")  # type: ignore[index]
        changed = True

    if payload.get("field_confidence"):
        merged_confidence = _merge_json_dict(existing.field_confidence, payload["field_confidence"])
        if merged_confidence != (existing.field_confidence or {}):
            existing.field_confidence = merged_confidence  # type: ignore[assignment]
            changed = True
    if payload.get("evidence"):
        merged_evidence = _merge_json_dict(existing.evidence, payload["evidence"])
        if merged_evidence != (existing.evidence or {}):
            existing.evidence = merged_evidence
            changed = True
    incoming_source_kind = payload.get("source_kind")
    if (
        incoming_source_kind
        and incoming_source_kind != existing.source_kind
        and _SOURCE_PRIORITY.get(str(incoming_source_kind), 1)
        >= _SOURCE_PRIORITY.get(existing.source_kind, 1)
    ):
        existing.source_kind = str(incoming_source_kind)
        changed = True
    if payload.get("source_chunk_id") and not existing.source_chunk_id:
        existing.source_chunk_id = str(payload["source_chunk_id"])
        changed = True
    if bool(existing.boundary_risk) and not bool(payload.get("boundary_risk")):
        existing.boundary_risk = False
        changed = True
    if field_sources != (existing.field_sources or {}):
        existing.field_sources = field_sources
        changed = True
    if conflicts != (existing.conflicts or {}):
        existing.conflicts = conflicts
        changed = True
    recorded_merge_ids = {
        item.get("merged_candidate_id")
        for item in (existing.merge_history or [])
        if isinstance(item, dict)
    }
    should_record_identity_merge = (
        merged_candidate_id is not None and merged_candidate_id not in recorded_merge_ids
    )
    if (
        merge_event["updated_fields"]
        or merge_event["conflict_fields"]
        or should_record_identity_merge
    ):
        existing.merge_history = _append_json_list(existing.merge_history, merge_event)
        changed = True
    return changed


def candidate_as_merge_payload(candidate: CrawlCandidate) -> dict[str, Any]:
    return {
        field_name: getattr(candidate, field_name)
        for field_name in _MERGEABLE_TEXT_FIELDS
    } | {
        "recent_papers": candidate.recent_papers,
        "field_confidence": candidate.field_confidence,
        "evidence": candidate.evidence,
        "field_sources": candidate.field_sources,
        "source_chunk_id": candidate.source_chunk_id,
        "source_kind": candidate.source_kind,
        "boundary_risk": candidate.boundary_risk,
    }


def apply_candidate_enrichment_values(
    candidate: CrawlCandidate,
    updates: dict[str, Any],
) -> bool:
    changed = False
    field_sources = dict(candidate.field_sources) if isinstance(candidate.field_sources, dict) else {}
    enrichment_source = {
        "source_kind": "profile_page",
        "source_chunk_id": None,
        "source_url": candidate.profile_url,
        "confidence": None,
        "boundary_risk": False,
    }
    for field_name in ("email", "title", "department", "research_direction"):
        value = updates.get(field_name)
        if field_name == "email":
            value = normalize_candidate_email(value)
        elif isinstance(value, str):
            value = value.strip() or None
        if value in (None, ""):
            continue
        stored_source = _stored_field_source(candidate, field_name)
        old_value = getattr(candidate, field_name)
        old_source_kind = stored_source.get("source_kind")
        should_replace = old_source_kind != "manual" and (
            old_value in (None, "")
            or (
                old_source_kind in {"list_chunk", "page_chunk"}
                and _should_replace_field(
                    old_value=old_value,
                    new_value=value,
                    old_source=stored_source,
                    new_source=enrichment_source,
                )
            )
        )
        if should_replace:
            setattr(candidate, field_name, value)
            field_sources[field_name] = enrichment_source
            changed = True
    recent_papers = normalize_recent_papers(updates.get("recent_papers"))
    if (
        recent_papers
        and not normalize_recent_papers(candidate.recent_papers)
        and _stored_field_source(candidate, "recent_papers").get("source_kind") != "manual"
    ):
        candidate.recent_papers = recent_papers
        field_sources["recent_papers"] = enrichment_source
        changed = True
    if changed:
        candidate.field_sources = field_sources
        candidate.source_kind = "profile_page"
        candidate.boundary_risk = False
    return changed


def mark_candidate_fields_manual(
    candidate: CrawlCandidate,
    field_names: Iterable[str],
) -> None:
    field_sources = dict(candidate.field_sources) if isinstance(candidate.field_sources, dict) else {}
    for field_name in field_names:
        field_sources[field_name] = {
            "source_kind": "manual",
            "source_chunk_id": None,
            "source_url": None,
            "confidence": 1.0,
            "boundary_risk": False,
        }
    candidate.field_sources = field_sources


async def _lock_candidate_roots(
    session: AsyncSession,
    first: CrawlCandidate,
    second: CrawlCandidate,
) -> tuple[CrawlCandidate, CrawlCandidate]:
    ids = sorted({first.id, second.id})
    rows = list(
        await session.scalars(
            select(CrawlCandidate)
            .where(CrawlCandidate.id.in_(ids))
            .order_by(CrawlCandidate.id.asc())
            .with_for_update()
        )
    )
    if len(rows) != 2 or rows[0].job_id != rows[1].job_id:
        raise RuntimeError("无法锁定同一抓取任务中的候选导师")
    return rows[0], rows[1]


async def merge_candidate_rows(
    session: AsyncSession,
    first: CrawlCandidate,
    second: CrawlCandidate,
) -> CrawlCandidate:
    first_root = await resolve_canonical_candidate(session, first)
    second_root = await resolve_canonical_candidate(session, second)
    if first_root.id == second_root.id:
        return first_root
    canonical, alias = await _lock_candidate_roots(session, first_root, second_root)
    canonical = await resolve_canonical_candidate(session, canonical)
    alias = await resolve_canonical_candidate(session, alias)
    if canonical.id == alias.id:
        return canonical
    if canonical.id > alias.id:
        canonical, alias = alias, canonical
    preferred_profile = _preferred_related_profile_url(canonical, alias)

    merge_candidate_payload(
        canonical,
        candidate_as_merge_payload(alias),
        merged_candidate_id=alias.id,
    )
    if preferred_profile is not None and canonical.profile_url != preferred_profile[0]:
        canonical.profile_url = preferred_profile[0]
        field_sources = (
            dict(canonical.field_sources)
            if isinstance(canonical.field_sources, dict)
            else {}
        )
        field_sources["profile_url"] = _stored_field_source(
            preferred_profile[1],
            "profile_url",
        )
        canonical.field_sources = field_sources
    if canonical.professor_id is None and alias.professor_id is not None:
        canonical.professor_id = alias.professor_id
    if _REVIEW_STATUS_PRIORITY.get(
        alias.review_status,
        -1,
    ) > _REVIEW_STATUS_PRIORITY.get(canonical.review_status, -1):
        canonical.review_status = alias.review_status

    alias.merged_into_candidate_id = canonical.id
    await session.execute(
        update(CrawlCandidate)
        .where(CrawlCandidate.merged_into_candidate_id == alias.id)
        .values(merged_into_candidate_id=canonical.id)
    )
    await session.execute(
        update(CrawlCandidateIdentityKey)
        .where(CrawlCandidateIdentityKey.candidate_id == alias.id)
        .values(candidate_id=canonical.id)
    )
    return canonical


async def _get_or_create_identity_key(
    session: AsyncSession,
    *,
    candidate: CrawlCandidate,
    key_type: str,
    normalized_value: str,
) -> CrawlCandidateIdentityKey:
    if key_type not in _IDENTITY_KEY_TYPES:
        raise ValueError(f"不支持的候选导师身份键类型：{key_type}")
    existing = await session.scalar(
        select(CrawlCandidateIdentityKey).where(
            CrawlCandidateIdentityKey.job_id == candidate.job_id,
            CrawlCandidateIdentityKey.key_type == key_type,
            CrawlCandidateIdentityKey.normalized_value == normalized_value,
        )
    )
    if existing is not None:
        return existing
    try:
        async with session.begin_nested():
            identity_key = CrawlCandidateIdentityKey(
                job_id=candidate.job_id,
                candidate_id=candidate.id,
                key_type=key_type,
                normalized_value=normalized_value,
            )
            session.add(identity_key)
            await session.flush()
            return identity_key
    except IntegrityError:
        existing = await session.scalar(
            select(CrawlCandidateIdentityKey).where(
                CrawlCandidateIdentityKey.job_id == candidate.job_id,
                CrawlCandidateIdentityKey.key_type == key_type,
                CrawlCandidateIdentityKey.normalized_value == normalized_value,
            )
        )
        if existing is None:
            raise
        return existing


async def consolidate_candidate_identity(
    session: AsyncSession,
    candidate: CrawlCandidate,
    *,
    additional_identities: Iterable[tuple[str, str]] = (),
) -> CrawlCandidate:
    await session.flush()
    root = await resolve_canonical_candidate(session, candidate)
    if root.id != candidate.id:
        merge_candidate_payload(
            root,
            candidate_as_merge_payload(candidate),
            merged_candidate_id=candidate.id,
        )
    identities = set(
        candidate_identity_values(
            name=candidate.name,
            email=candidate.email,
            profile_url=candidate.profile_url,
        )
    )
    identities.update(additional_identities)
    for key_type, normalized_value in sorted(identities):
        identity_key = await _get_or_create_identity_key(
            session,
            candidate=root,
            key_type=key_type,
            normalized_value=normalized_value,
        )
        keyed_candidate = await session.get(CrawlCandidate, identity_key.candidate_id)
        if keyed_candidate is None:
            identity_key.candidate_id = root.id
            continue
        keyed_root = await resolve_canonical_candidate(session, keyed_candidate)
        if keyed_root.id != root.id:
            root = await merge_candidate_rows(session, root, keyed_root)
        identity_key.candidate_id = root.id

    normalized_email = normalize_candidate_email(root.email)
    normalized_profile_url = normalize_candidate_profile_url(root.profile_url)
    root.identity_key = normalized_email or normalized_profile_url
    await session.flush()
    return root


async def consolidate_job_candidates(
    session: AsyncSession,
    job_id: int,
) -> list[CrawlCandidate]:
    candidates = list(
        await session.scalars(
            select(CrawlCandidate)
            .where(CrawlCandidate.job_id == job_id)
            .order_by(CrawlCandidate.id.asc())
        )
    )
    for candidate in candidates:
        if candidate.merged_into_candidate_id is None:
            await consolidate_candidate_identity(session, candidate)
    return list(
        await session.scalars(
            select(CrawlCandidate)
            .where(
                CrawlCandidate.job_id == job_id,
                canonical_candidate_clause(),
            )
            .order_by(CrawlCandidate.id.asc())
        )
    )


async def rebuild_candidate_identity_keys(
    session: AsyncSession,
    candidate: CrawlCandidate,
    *,
    exclude_identities: Iterable[tuple[str, str]] = (),
) -> CrawlCandidate:
    root = await resolve_canonical_candidate(session, candidate)
    job_candidates = list(
        await session.scalars(
            select(CrawlCandidate)
            .where(CrawlCandidate.job_id == root.job_id)
            .order_by(CrawlCandidate.id.asc())
        )
    )
    component: list[CrawlCandidate] = []
    for row in job_candidates:
        if (await resolve_canonical_candidate(session, row)).id == root.id:
            component.append(row)

    component_ids = [row.id for row in component]
    for component_id_chunk in chunked_values(component_ids):
        await session.execute(
            delete(CrawlCandidateIdentityKey).where(
                CrawlCandidateIdentityKey.job_id == root.job_id,
                CrawlCandidateIdentityKey.candidate_id.in_(component_id_chunk),
            )
        )
    identities = {
        identity
        for row in component
        for identity in candidate_identity_values(
            name=row.name,
            email=row.email,
            profile_url=row.profile_url,
        )
    }
    identities.difference_update(exclude_identities)
    root.identity_key = None
    await session.flush()
    return await consolidate_candidate_identity(
        session,
        root,
        additional_identities=identities,
    )


async def canonicalize_candidate_ids(
    session: AsyncSession,
    *,
    job_id: int,
    candidate_ids: Iterable[int],
) -> tuple[list[CrawlCandidate], list[int]]:
    requested_ids = unique_positive_ids(candidate_ids)
    if not requested_ids:
        return [], []
    rows: list[CrawlCandidate] = []
    for candidate_id_chunk in chunked_values(requested_ids):
        rows.extend(
            await session.scalars(
                select(CrawlCandidate).where(
                    CrawlCandidate.job_id == job_id,
                    CrawlCandidate.id.in_(candidate_id_chunk),
                )
            ),
        )
    rows_by_id = {candidate.id: candidate for candidate in rows}
    missing_ids = [candidate_id for candidate_id in requested_ids if candidate_id not in rows_by_id]
    canonical_by_id: dict[int, CrawlCandidate] = {}
    for candidate_id in requested_ids:
        candidate = rows_by_id.get(candidate_id)
        if candidate is None:
            continue
        canonical = await resolve_canonical_candidate(session, candidate)
        canonical_by_id.setdefault(canonical.id, canonical)
    return [canonical_by_id[key] for key in sorted(canonical_by_id)], missing_ids
