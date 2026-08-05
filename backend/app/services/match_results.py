from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.time import utc_now
from app.models import (
    EmailTask,
    EmailTaskCancellationReason,
    EmailTaskStatus,
    IdentityCommunicationGroup,
    IdentityMaterial,
    IdentityProfile,
    IdentityProfessorMatchResult,
    MatchAnalysisRun,
)


@dataclass(frozen=True, slots=True)
class IdentityMatchScope:
    active_identity: IdentityProfile
    source_identity: IdentityProfile
    communication_group_id: int | None
    uses_group_match_source: bool

    @property
    def active_identity_id(self) -> int:
        return self.active_identity.id

    @property
    def source_identity_id(self) -> int:
        return self.source_identity.id


@dataclass(frozen=True, slots=True)
class MatchResultView:
    result_id: int | None
    identity_id: int
    professor_id: int
    llm_profile_id: int | None
    primary_material_id: int | None
    primary_material_name: str | None
    source_email_task_id: int | None
    latest_analysis_run_id: int | None
    match_score: int
    match_reason: str
    fit_points: tuple[str, ...]
    risk_points: tuple[str, ...]
    match_keywords: tuple[str, ...]
    analyzed_at: datetime
    updated_at: datetime
    legacy_task_snapshot: bool = False


@dataclass(frozen=True, slots=True)
class ResolvedMatchResults:
    scope: IdentityMatchScope
    by_professor_id: dict[int, MatchResultView]

    def get(self, professor_id: int) -> MatchResultView | None:
        return self.by_professor_id.get(professor_id)


async def resolve_identity_match_scope(
    session: AsyncSession,
    *,
    active_identity_id: int,
    match_source_identity_id: int | None = None,
) -> IdentityMatchScope:
    """Resolve the identity whose materials and canonical results should be used.

    ``match_source_identity_id`` is an internal frozen-source override used by
    background jobs. Interactive callers should omit it so the current group
    setting is resolved transactionally on the server.
    """

    active_identity = await _load_identity_for_matching(session, active_identity_id)
    if active_identity is None:
        raise ValueError("未找到身份配置")

    if match_source_identity_id is not None:
        source_identity = await _load_identity_for_matching(
            session,
            match_source_identity_id,
        )
        if source_identity is None:
            raise ValueError("匹配依据身份不存在")
        return IdentityMatchScope(
            active_identity=active_identity,
            source_identity=source_identity,
            communication_group_id=active_identity.communication_group_id,
            uses_group_match_source=(source_identity.id != active_identity.id),
        )

    group_id = active_identity.communication_group_id
    if group_id is None:
        return IdentityMatchScope(
            active_identity=active_identity,
            source_identity=active_identity,
            communication_group_id=None,
            uses_group_match_source=False,
        )

    group = await session.get(IdentityCommunicationGroup, group_id)
    source_identity_id = group.match_source_identity_id if group is not None else None
    if source_identity_id is None:
        return IdentityMatchScope(
            active_identity=active_identity,
            source_identity=active_identity,
            communication_group_id=group_id,
            uses_group_match_source=False,
        )

    source_identity = await _load_identity_for_matching(session, source_identity_id)
    if (
        source_identity is None
        or source_identity.communication_group_id != group_id
    ):
        # Corrupt or partially migrated configuration must never leak another
        # identity's data. Fall back to the active identity safely.
        return IdentityMatchScope(
            active_identity=active_identity,
            source_identity=active_identity,
            communication_group_id=group_id,
            uses_group_match_source=False,
        )

    return IdentityMatchScope(
        active_identity=active_identity,
        source_identity=source_identity,
        communication_group_id=group_id,
        uses_group_match_source=True,
    )


async def load_resolved_match_results(
    session: AsyncSession,
    *,
    active_identity_id: int,
    professor_ids: Iterable[int],
    match_source_identity_id: int | None = None,
    include_legacy_task_snapshots: bool = True,
) -> ResolvedMatchResults:
    scope = await resolve_identity_match_scope(
        session,
        active_identity_id=active_identity_id,
        match_source_identity_id=match_source_identity_id,
    )
    unique_professor_ids = tuple(dict.fromkeys(int(item) for item in professor_ids))
    if not unique_professor_ids:
        return ResolvedMatchResults(scope=scope, by_professor_id={})

    canonical_rows = list(
        await session.scalars(
            select(IdentityProfessorMatchResult)
            .options(selectinload(IdentityProfessorMatchResult.primary_material))
            .where(
                IdentityProfessorMatchResult.identity_id == scope.source_identity_id,
                IdentityProfessorMatchResult.professor_id.in_(unique_professor_ids),
            ),
        ),
    )
    result_by_professor = {
        row.professor_id: _view_from_canonical_result(row)
        for row in canonical_rows
    }

    if include_legacy_task_snapshots:
        missing_professor_ids = [
            professor_id
            for professor_id in unique_professor_ids
            if professor_id not in result_by_professor
        ]
        if missing_professor_ids:
            legacy_rows = list(
                (
                    await session.execute(
                        select(
                            EmailTask.id.label("task_id"),
                            EmailTask.identity_id,
                            EmailTask.professor_id,
                            EmailTask.llm_profile_id,
                            EmailTask.primary_material_id,
                            IdentityMaterial.display_name.label(
                                "primary_material_name"
                            ),
                            EmailTask.match_score,
                            EmailTask.match_reason,
                            EmailTask.fit_points,
                            EmailTask.risk_points,
                            EmailTask.match_keywords,
                            EmailTask.created_at,
                            EmailTask.updated_at,
                        )
                        .outerjoin(
                            IdentityMaterial,
                            IdentityMaterial.id == EmailTask.primary_material_id,
                        )
                        .where(
                            EmailTask.identity_id == scope.source_identity_id,
                            EmailTask.professor_id.in_(missing_professor_ids),
                            EmailTask.match_score.is_not(None),
                            (
                                EmailTask.match_source_identity_id.is_(None)
                                | (
                                    EmailTask.match_source_identity_id
                                    == scope.source_identity_id
                                )
                            ),
                            ~(
                                (EmailTask.status == EmailTaskStatus.CANCELED.value)
                                & (
                                    EmailTask.cancellation_reason
                                    == EmailTaskCancellationReason.USER_REMOVED.value
                                )
                            ),
                        )
                        .order_by(
                            EmailTask.professor_id.asc(),
                            EmailTask.updated_at.desc(),
                            EmailTask.created_at.desc(),
                            EmailTask.id.desc(),
                        ),
                    )
                ).mappings()
            )
            for task in legacy_rows:
                professor_id = task["professor_id"]
                if professor_id in result_by_professor:
                    continue
                result_by_professor[professor_id] = _view_from_legacy_task(task)

    return ResolvedMatchResults(
        scope=scope,
        by_professor_id=result_by_professor,
    )


async def load_resolved_match_result(
    session: AsyncSession,
    *,
    active_identity_id: int,
    professor_id: int,
    match_source_identity_id: int | None = None,
    include_legacy_task_snapshots: bool = True,
) -> tuple[IdentityMatchScope, MatchResultView | None]:
    resolved = await load_resolved_match_results(
        session,
        active_identity_id=active_identity_id,
        professor_ids=[professor_id],
        match_source_identity_id=match_source_identity_id,
        include_legacy_task_snapshots=include_legacy_task_snapshots,
    )
    return resolved.scope, resolved.get(professor_id)


async def upsert_identity_professor_match_result(
    session: AsyncSession,
    *,
    identity_id: int,
    professor_id: int,
    llm_profile_id: int | None,
    primary_material_id: int | None,
    source_email_task_id: int | None,
    analysis_run: MatchAnalysisRun,
    match_score: int,
    match_reason: str,
    fit_points: list[str],
    risk_points: list[str],
    match_keywords: list[str],
) -> IdentityProfessorMatchResult:
    current = await session.scalar(
        select(IdentityProfessorMatchResult).where(
            IdentityProfessorMatchResult.identity_id == identity_id,
            IdentityProfessorMatchResult.professor_id == professor_id,
        ),
    )
    now = analysis_run.finished_at or utc_now()
    if current is None:
        current = IdentityProfessorMatchResult(
            identity_id=identity_id,
            professor_id=professor_id,
            created_at=now,
        )
        session.add(current)

    current.llm_profile_id = llm_profile_id
    current.primary_material_id = primary_material_id
    current.source_email_task_id = source_email_task_id
    current.latest_analysis_run_id = analysis_run.id
    current.match_score = match_score
    current.match_reason = match_reason
    current.fit_points = list(fit_points)
    current.risk_points = list(risk_points)
    current.match_keywords = list(match_keywords)
    current.analyzed_at = now
    current.updated_at = now
    await session.flush()
    return current


def apply_match_result_snapshot_to_task(
    task: EmailTask,
    *,
    match_source_identity_id: int,
    match_score: int,
    match_reason: str,
    fit_points: Iterable[str],
    risk_points: Iterable[str],
    match_keywords: Iterable[str],
) -> None:
    """Write an immutable-at-task-time compatibility snapshot.

    This snapshot supports historical task rendering and the existing workflow
    state machine. It is not the source used by current mentor-level views.
    """

    task.match_source_identity_id = match_source_identity_id
    task.match_score = match_score
    task.match_reason = match_reason
    task.fit_points = list(fit_points)
    task.risk_points = list(risk_points)
    task.match_keywords = list(match_keywords)
    if task.status == EmailTaskStatus.DISCOVERED.value:
        task.status = EmailTaskStatus.MATCHED.value


def apply_match_result_view_to_task(task: EmailTask, view: MatchResultView) -> None:
    apply_match_result_snapshot_to_task(
        task,
        match_source_identity_id=view.identity_id,
        match_score=view.match_score,
        match_reason=view.match_reason,
        fit_points=view.fit_points,
        risk_points=view.risk_points,
        match_keywords=view.match_keywords,
    )


def match_result_is_stale(
    view: MatchResultView | None,
    source_identity: IdentityProfile,
) -> bool:
    if view is None:
        return False
    return (
        view.primary_material_id is None
        or view.primary_material_id != source_identity.current_primary_material_id
    )


async def _load_identity_for_matching(
    session: AsyncSession,
    identity_id: int,
) -> IdentityProfile | None:
    return await session.scalar(
        select(IdentityProfile)
        .options(
            selectinload(IdentityProfile.materials),
            selectinload(IdentityProfile.current_primary_material),
        )
        .where(IdentityProfile.id == identity_id),
    )


def _view_from_canonical_result(
    result: IdentityProfessorMatchResult,
) -> MatchResultView:
    return MatchResultView(
        result_id=result.id,
        identity_id=result.identity_id,
        professor_id=result.professor_id,
        llm_profile_id=result.llm_profile_id,
        primary_material_id=result.primary_material_id,
        primary_material_name=(
            result.primary_material.display_name
            if result.primary_material is not None
            else None
        ),
        source_email_task_id=result.source_email_task_id,
        latest_analysis_run_id=result.latest_analysis_run_id,
        match_score=result.match_score,
        match_reason=result.match_reason,
        fit_points=tuple(result.fit_points or []),
        risk_points=tuple(result.risk_points or []),
        match_keywords=tuple(result.match_keywords or []),
        analyzed_at=result.analyzed_at,
        updated_at=result.updated_at,
        legacy_task_snapshot=False,
    )


def _view_from_legacy_task(task: Mapping[str, Any]) -> MatchResultView:
    assert task["match_score"] is not None
    return MatchResultView(
        result_id=None,
        identity_id=task["identity_id"],
        professor_id=task["professor_id"],
        llm_profile_id=task["llm_profile_id"],
        primary_material_id=task["primary_material_id"],
        primary_material_name=task["primary_material_name"],
        source_email_task_id=task["task_id"],
        latest_analysis_run_id=None,
        match_score=task["match_score"],
        match_reason=task["match_reason"] or "",
        fit_points=tuple(task["fit_points"] or []),
        risk_points=tuple(task["risk_points"] or []),
        match_keywords=tuple(task["match_keywords"] or []),
        analyzed_at=task["updated_at"],
        updated_at=task["updated_at"],
        legacy_task_snapshot=True,
    )
