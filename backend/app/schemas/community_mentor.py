from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.base import ApiSchema


DATASET_VERSION_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{6}Z-[a-f0-9]{12}$"
MENTOR_ID_PATTERN = r"^mentor_[a-z0-9][a-z0-9_-]{7,63}$"
ORGANIZATION_ID_PATTERN = r"^org_[a-z0-9][a-z0-9_-]{2,63}$"
AFFILIATION_ID_PATTERN = r"^aff_[a-z0-9][a-z0-9_-]{7,63}$"
DATA_FILE_PATTERN = r"^data/[a-z0-9_-]+/[a-z0-9_-]+\.json$"
MANIFEST_FILE_PATTERN = r"^(catalog|revocations|data/[a-z0-9_-]+/[a-z0-9_-]+)\.json$"

COMMUNITY_IMPORT_FIELDS = (
    "name",
    "email",
    "title",
    "university",
    "school",
    "department",
    "research_direction",
    "recent_papers",
    "profile_url",
    "source_url",
)

CommunityMentorStatus = Literal[
    "active",
    "retired",
    "departed",
    "deceased",
    "stale",
    "disputed",
    "removed",
]
CommunityRevokedStatus = Literal[
    "retired",
    "departed",
    "deceased",
    "stale",
    "disputed",
    "removed",
]
CommunityComparisonCategory = Literal[
    "new",
    "linked_unchanged",
    "fill_available",
    "local_modified",
    "remote_modified",
    "conflict",
    "archived_local",
    "retired_or_revoked",
]
CommunityFieldState = Literal[
    "new",
    "same",
    "fill_available",
    "local_only",
    "local_modified",
    "remote_modified",
    "conflict",
]
CommunityFieldChoice = Literal["community", "local"]


class CommunityDatasetSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CommunityLatestDocument(CommunityDatasetSchema):
    schema_version: Literal[1]
    dataset_version: str = Field(pattern=DATASET_VERSION_PATTERN)
    generated_at: datetime
    manifest_path: str = Field(max_length=256)
    catalog_path: str = Field(max_length=256)


class CommunityManifestFile(CommunityDatasetSchema):
    path: str = Field(pattern=MANIFEST_FILE_PATTERN)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bytes: int = Field(ge=0)


class CommunityManifestDocument(CommunityDatasetSchema):
    schema_version: Literal[1]
    dataset_version: str = Field(pattern=DATASET_VERSION_PATTERN)
    generated_at: datetime
    minimum_app_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    files: list[CommunityManifestFile] = Field(max_length=10_000)

    @model_validator(mode="after")
    def _validate_unique_paths(self) -> "CommunityManifestDocument":
        paths = [item.path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("Manifest 包含重复文件路径")
        if "catalog.json" not in paths or "revocations.json" not in paths:
            raise ValueError("Manifest 缺少 catalog.json 或 revocations.json")
        return self


class CommunityCatalogUnit(CommunityDatasetSchema):
    id: str = Field(pattern=ORGANIZATION_ID_PATTERN)
    name: str = Field(min_length=1, max_length=255)
    type: Literal[
        "university",
        "school",
        "institute",
        "department",
        "center",
        "laboratory",
    ]
    record_count: int = Field(ge=0)
    path: str = Field(pattern=DATA_FILE_PATTERN)


class CommunityCatalogUniversity(CommunityDatasetSchema):
    id: str = Field(pattern=ORGANIZATION_ID_PATTERN)
    name: str = Field(min_length=1, max_length=255)
    record_count: int = Field(ge=0)
    units: list[CommunityCatalogUnit] = Field(max_length=10_000)


class CommunityCatalogDocument(CommunityDatasetSchema):
    schema_version: Literal[1]
    dataset_version: str = Field(pattern=DATASET_VERSION_PATTERN)
    generated_at: datetime
    record_count: int = Field(ge=0)
    universities: list[CommunityCatalogUniversity] = Field(max_length=10_000)

    @model_validator(mode="after")
    def _validate_catalog_counts_and_paths(self) -> "CommunityCatalogDocument":
        university_ids: set[str] = set()
        paths: set[str] = set()
        calculated_total = 0
        for university in self.universities:
            if university.id in university_ids:
                raise ValueError("Catalog 包含重复学校 ID")
            university_ids.add(university.id)
            unit_total = 0
            for unit in university.units:
                if unit.path in paths:
                    raise ValueError("Catalog 包含重复学院分片路径")
                paths.add(unit.path)
                expected_prefix = f"data/{university.id}/"
                if not unit.path.startswith(expected_prefix):
                    raise ValueError("Catalog 学校 ID 与学院分片路径不一致")
                unit_total += unit.record_count
            if unit_total != university.record_count:
                raise ValueError("Catalog 学校记录数与学院合计不一致")
            calculated_total += university.record_count
        if calculated_total != self.record_count:
            raise ValueError("Catalog 总记录数与学校合计不一致")
        return self


class CommunityMentorContact(CommunityDatasetSchema):
    email: str = Field(min_length=3, max_length=255)
    is_primary: bool
    affiliation_id: str | None = Field(default=None, pattern=AFFILIATION_ID_PATTERN)
    source_url: str = Field(min_length=8, max_length=500)
    observed_at: datetime

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized.count("@") != 1 or any(character.isspace() for character in normalized):
            raise ValueError("社区邮箱格式无效")
        return normalized

    @field_validator("source_url")
    @classmethod
    def _validate_source_url(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("社区来源链接必须使用 HTTPS")
        return value


class CommunityMentorAffiliation(CommunityDatasetSchema):
    id: str = Field(pattern=AFFILIATION_ID_PATTERN)
    organization_id: str = Field(pattern=ORGANIZATION_ID_PATTERN)
    status: Literal["current", "former"]
    is_primary: bool
    title: str | None = Field(default=None, max_length=255)
    university: str = Field(min_length=1, max_length=255)
    school: str | None = Field(default=None, max_length=255)
    department: str | None = Field(default=None, max_length=255)
    source_url: str = Field(min_length=8, max_length=500)
    observed_at: datetime

    @field_validator("source_url")
    @classmethod
    def _validate_source_url(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("社区任职来源链接必须使用 HTTPS")
        return value


class CommunityMentorContributor(CommunityDatasetSchema):
    github_user_id: int = Field(gt=0)
    github_login_at_submission: str = Field(pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
    issue_urls: list[str] = Field(min_length=1, max_length=100)

    @field_validator("issue_urls")
    @classmethod
    def _validate_issue_urls(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("贡献者 Issue URL 重复")
        for value in values:
            if not value.startswith("https://github.com/") or len(value) > 500:
                raise ValueError("贡献者 Issue URL 无效")
        return values


class CommunityMentorRecord(CommunityDatasetSchema):
    id: str = Field(pattern=MENTOR_ID_PATTERN)
    name: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=255)
    title: str | None = Field(default=None, max_length=255)
    university: str = Field(min_length=1, max_length=255)
    school: str | None = Field(default=None, max_length=255)
    department: str | None = Field(default=None, max_length=255)
    research_direction: str | None = Field(default=None, max_length=10_000)
    recent_papers: list[str] = Field(default_factory=list, max_length=8)
    profile_url: str | None = Field(default=None, max_length=500)
    source_url: str = Field(min_length=8, max_length=500)
    status: CommunityMentorStatus
    last_verified_at: datetime | None = None
    contacts: list[CommunityMentorContact] = Field(max_length=100)
    affiliations: list[CommunityMentorAffiliation] = Field(max_length=100)
    contributors: list[CommunityMentorContributor] = Field(max_length=1_000)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized.count("@") != 1 or any(character.isspace() for character in normalized):
            raise ValueError("社区主邮箱格式无效")
        return normalized

    @field_validator("profile_url", "source_url")
    @classmethod
    def _validate_urls(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("https://"):
            raise ValueError("社区链接必须使用 HTTPS")
        return value

    @field_validator("recent_papers")
    @classmethod
    def _validate_recent_papers(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value or len(value) > 2_000 for value in normalized):
            raise ValueError("社区论文条目无效")
        if len(normalized) != len(set(normalized)):
            raise ValueError("社区论文条目重复")
        return normalized

    @model_validator(mode="after")
    def _validate_primary_values(self) -> "CommunityMentorRecord":
        primary_contacts = [item for item in self.contacts if item.is_primary]
        primary_affiliations = [item for item in self.affiliations if item.is_primary]
        contact_emails = [item.email for item in self.contacts]
        affiliation_ids = [item.id for item in self.affiliations]
        contributor_ids = [item.github_user_id for item in self.contributors]
        if len(contact_emails) != len(set(contact_emails)):
            raise ValueError("社区记录包含重复邮箱")
        if len(affiliation_ids) != len(set(affiliation_ids)):
            raise ValueError("社区记录包含重复任职 ID")
        if len(contributor_ids) != len(set(contributor_ids)):
            raise ValueError("社区记录包含重复贡献者")
        if any(item.status != "current" for item in self.affiliations):
            raise ValueError("社区发布投影只能包含当前任职")
        if len(primary_contacts) != 1 or primary_contacts[0].email != self.email:
            raise ValueError("社区记录主邮箱投影不一致")
        if len(primary_affiliations) != 1:
            raise ValueError("社区记录必须且只能有一个主要任职")
        primary_affiliation = primary_affiliations[0]
        if (
            primary_affiliation.university != self.university
            or primary_affiliation.school != self.school
            or primary_affiliation.department != self.department
        ):
            raise ValueError("社区记录主要任职投影不一致")
        return self


class CommunityShardOrganization(CommunityDatasetSchema):
    id: str = Field(pattern=ORGANIZATION_ID_PATTERN)
    name: str = Field(min_length=1, max_length=255)


class CommunityShardUnit(CommunityShardOrganization):
    type: Literal[
        "university",
        "school",
        "institute",
        "department",
        "center",
        "laboratory",
    ]


class CommunityShardDocument(CommunityDatasetSchema):
    schema_version: Literal[1]
    dataset_version: str = Field(pattern=DATASET_VERSION_PATTERN)
    generated_at: datetime
    university: CommunityShardOrganization
    unit: CommunityShardUnit
    records: list[CommunityMentorRecord] = Field(max_length=50_000)

    @model_validator(mode="after")
    def _validate_unique_records(self) -> "CommunityShardDocument":
        record_ids = [item.id for item in self.records]
        if len(record_ids) != len(set(record_ids)):
            raise ValueError("学院分片包含重复导师 ID")
        return self


class CommunityRevocationRecord(CommunityDatasetSchema):
    community_record_id: str = Field(pattern=MENTOR_ID_PATTERN)
    status: CommunityRevokedStatus
    reason: str | None = Field(default=None, max_length=1_000)
    source_url: str | None = Field(default=None, max_length=500)
    observed_at: datetime | None = None

    @field_validator("source_url")
    @classmethod
    def _validate_source_url(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("https://"):
            raise ValueError("生命周期来源链接必须使用 HTTPS")
        return value


class CommunityRevocationsDocument(CommunityDatasetSchema):
    schema_version: Literal[1]
    dataset_version: str = Field(pattern=DATASET_VERSION_PATTERN)
    generated_at: datetime
    records: list[CommunityRevocationRecord] = Field(max_length=100_000)
    events: list[dict[str, Any]] = Field(max_length=100_000)

    @model_validator(mode="after")
    def _validate_unique_records(self) -> "CommunityRevocationsDocument":
        record_ids = [item.community_record_id for item in self.records]
        if len(record_ids) != len(set(record_ids)):
            raise ValueError("生命周期文件包含重复导师 ID")
        return self


class CommunityLifecycleWarningRead(ApiSchema):
    community_record_id: str
    professor_id: int
    professor_name: str
    status: CommunityMentorStatus
    reason: str | None
    source_url: str | None
    observed_at: datetime | None


class CommunityCatalogRead(ApiSchema):
    schema_version: Literal[1]
    dataset_version: str
    generated_at: datetime
    record_count: int
    universities: list[CommunityCatalogUniversity]
    source: Literal["network", "cache"]
    stale: bool
    warning: str | None
    verified_at: datetime
    lifecycle_warnings: list[CommunityLifecycleWarningRead] = Field(default_factory=list)


class CommunityRecordSelectionPayload(BaseModel):
    dataset_version: str = Field(pattern=DATASET_VERSION_PATTERN)
    unit_paths: list[str] = Field(min_length=1, max_length=20)

    @field_validator("unit_paths")
    @classmethod
    def _validate_unit_paths(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("学院分片不能重复")
        import re

        if any(re.fullmatch(DATA_FILE_PATTERN, value) is None for value in values):
            raise ValueError("学院分片路径无效")
        return values


class CommunityPreviewPayload(CommunityRecordSelectionPayload):
    record_ids: list[str] = Field(min_length=1, max_length=500)

    @field_validator("record_ids")
    @classmethod
    def _validate_record_ids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("导师 ID 不能重复")
        import re

        if any(re.fullmatch(MENTOR_ID_PATTERN, value) is None for value in values):
            raise ValueError("导师 ID 无效")
        return values


class CommunityFieldComparisonRead(ApiSchema):
    field: str
    label: str
    local_value: Any
    community_value: Any
    baseline_present: bool
    baseline_value: Any
    state: CommunityFieldState
    suggested_choice: CommunityFieldChoice


class CommunityMentorComparisonRead(ApiSchema):
    record: CommunityMentorRecord
    category: CommunityComparisonCategory
    local_professor_id: int | None
    local_professor_name: str | None
    local_archived: bool
    linked: bool
    identity_conflict: bool
    match_reason: str | None
    import_blocked: bool = False
    import_blocked_reason: str | None = None
    fields: list[CommunityFieldComparisonRead]


class CommunityRecordsRead(ApiSchema):
    dataset_version: str
    source: Literal["network", "cache"]
    stale: bool
    warning: str | None
    records: list[CommunityMentorComparisonRead]
    lifecycle_warnings: list[CommunityLifecycleWarningRead] = Field(default_factory=list)


class CommunityImportItemPayload(BaseModel):
    community_record_id: str = Field(pattern=MENTOR_ID_PATTERN)
    field_choices: dict[str, CommunityFieldChoice] = Field(default_factory=dict)
    confirm_identity_match: bool = False

    @field_validator("field_choices")
    @classmethod
    def _validate_field_choices(
        cls,
        value: dict[str, CommunityFieldChoice],
    ) -> dict[str, CommunityFieldChoice]:
        unknown = set(value) - set(COMMUNITY_IMPORT_FIELDS)
        if unknown:
            raise ValueError(f"包含未知导入字段：{', '.join(sorted(unknown))}")
        return value


class CommunityImportPayload(CommunityRecordSelectionPayload):
    items: list[CommunityImportItemPayload] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _validate_unique_items(self) -> "CommunityImportPayload":
        record_ids = [item.community_record_id for item in self.items]
        if len(record_ids) != len(set(record_ids)):
            raise ValueError("导入导师 ID 不能重复")
        return self


class CommunityImportedProfessorRead(ApiSchema):
    community_record_id: str
    professor_id: int
    action: Literal["inserted", "updated", "linked"]


class CommunityImportResultRead(ApiSchema):
    inserted_count: int
    updated_count: int
    linked_count: int
    skipped_count: int
    message: str
    professors: list[CommunityImportedProfessorRead]
