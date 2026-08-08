from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InformationEnrichmentSkipReason:
    code: str
    legacy_message: str
    message: str
    recoverable: bool
    suggested_action: str | None = None


PROFESSOR_ARCHIVED_SKIP_REASON = InformationEnrichmentSkipReason(
    code="PROFESSOR_ARCHIVED",
    legacy_message="导师已在回收站",
    message="导师已在回收站",
    recoverable=True,
    suggested_action="professors.restore",
)
MISSING_PROFILE_URL_SKIP_REASON = InformationEnrichmentSkipReason(
    code="MISSING_PROFILE_URL",
    legacy_message="缺少有效的导师主页链接",
    message="缺少有效个人主页",
    recoverable=True,
    suggested_action="professors.update",
)
ALREADY_COMPLETE_SKIP_REASON = InformationEnrichmentSkipReason(
    code="ALREADY_COMPLETE",
    legacy_message="资料已完整，无需补全",
    message="资料已完整，无需补全",
    recoverable=False,
)
ENRICHMENT_IN_PROGRESS_SKIP_REASON = InformationEnrichmentSkipReason(
    code="ENRICHMENT_IN_PROGRESS",
    legacy_message="已有信息补全正在进行",
    message="已有信息补全正在进行",
    recoverable=True,
    suggested_action="enrichment.jobs.list",
)
NO_NEW_INFORMATION_SKIP_REASON = InformationEnrichmentSkipReason(
    code="NO_NEW_INFORMATION",
    legacy_message="个人主页未提供可补全的新信息",
    message="个人主页未提供可补全的新信息",
    recoverable=True,
    suggested_action="professors.update",
)
UNCLASSIFIED_SKIP_REASON = InformationEnrichmentSkipReason(
    code="UNCLASSIFIED",
    legacy_message="未分类的跳过原因",
    message="未分类的跳过原因",
    recoverable=False,
)

_SKIP_REASON_BY_LEGACY_MESSAGE = {
    reason.legacy_message: reason
    for reason in (
        PROFESSOR_ARCHIVED_SKIP_REASON,
        MISSING_PROFILE_URL_SKIP_REASON,
        ALREADY_COMPLETE_SKIP_REASON,
        ENRICHMENT_IN_PROGRESS_SKIP_REASON,
        NO_NEW_INFORMATION_SKIP_REASON,
    )
}


def resolve_information_enrichment_skip_reason(
    legacy_message: str | None,
) -> InformationEnrichmentSkipReason | None:
    if legacy_message is None:
        return None
    return _SKIP_REASON_BY_LEGACY_MESSAGE.get(
        legacy_message,
        UNCLASSIFIED_SKIP_REASON,
    )
