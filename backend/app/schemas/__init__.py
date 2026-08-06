from app.schemas.batch_task import (
    BatchTaskActionResponse,
    BatchTaskCardRead,
    BatchTaskItemRead,
    CreateBatchTaskRequest,
)
from app.schemas.email_task import (
    EmailTaskApprovalRequest,
    EmailTaskPrimaryMaterialRequest,
    EmailTaskScheduleRequest,
)
from app.schemas.diagnostics import (
    OperationLogExportResponse,
    OperationLogListResponse,
    OperationLogRead,
)
from app.schemas.identity import (
    ConnectionTestResult,
    IdentityMaterialRead,
    IdentityProfileCreate,
    IdentityProfileRead,
    IdentityProfileUpdate,
)
from app.schemas.llm_profile import (
    LLMProfileCreate,
    LLMProfileModelsResult,
    LLMProfileRead,
    LLMProfileTestResult,
    LLMProfileUpdate,
)
from app.schemas.match_analysis_job import (
    CreateMatchAnalysisJobRequest,
    MatchAnalysisJobActionResponse,
    MatchAnalysisJobItemRead,
    MatchAnalysisJobRead,
)
from app.schemas.professor import (
    ProfessorDashboardItemRead,
    ProfessorImportResult,
    ProfessorRead,
)
from app.schemas.workspace import (
    WorkspaceMessageRead,
    WorkspaceThreadRead,
)


_COMMUNICATION_GROUP_SCHEMA_EXPORTS = frozenset(
    {
        "IdentityCommunicationGroupMemberRead",
        "IdentityCommunicationGroupRead",
        "IdentityCommunicationGroupWrite",
    },
)


def __getattr__(name: str) -> object:
    if name not in _COMMUNICATION_GROUP_SCHEMA_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from app.modules.identities.communication_groups import schemas

    return getattr(schemas, name)


__all__ = [
    "BatchTaskActionResponse",
    "BatchTaskCardRead",
    "BatchTaskItemRead",
    "ConnectionTestResult",
    "CreateBatchTaskRequest",
    "CreateMatchAnalysisJobRequest",
    "IdentityCommunicationGroupMemberRead",
    "IdentityCommunicationGroupRead",
    "IdentityCommunicationGroupWrite",
    "EmailTaskApprovalRequest",
    "EmailTaskPrimaryMaterialRequest",
    "EmailTaskScheduleRequest",
    "OperationLogExportResponse",
    "OperationLogListResponse",
    "OperationLogRead",
    "IdentityMaterialRead",
    "IdentityProfileCreate",
    "IdentityProfileRead",
    "IdentityProfileUpdate",
    "LLMProfileCreate",
    "LLMProfileModelsResult",
    "LLMProfileRead",
    "LLMProfileTestResult",
    "LLMProfileUpdate",
    "MatchAnalysisJobActionResponse",
    "MatchAnalysisJobItemRead",
    "MatchAnalysisJobRead",
    "ProfessorDashboardItemRead",
    "ProfessorImportResult",
    "ProfessorRead",
    "WorkspaceMessageRead",
    "WorkspaceThreadRead",
]
