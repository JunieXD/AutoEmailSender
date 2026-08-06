from importlib import import_module


_SCHEMA_EXPORT_MODULES = {
    "BatchTaskActionResponse": "app.modules.campaigns.schemas",
    "BatchTaskCardRead": "app.modules.campaigns.schemas",
    "BatchTaskItemRead": "app.modules.campaigns.schemas",
    "ConnectionTestResult": "app.modules.identities.profiles.schemas",
    "CreateBatchTaskRequest": "app.modules.campaigns.schemas",
    "CreateMatchAnalysisJobRequest": "app.modules.matching.schemas",
    "EmailTaskApprovalRequest": "app.schemas.email_task",
    "EmailTaskPrimaryMaterialRequest": "app.schemas.email_task",
    "EmailTaskScheduleRequest": "app.schemas.email_task",
    "IdentityCommunicationGroupMemberRead": (
        "app.modules.identities.communication_groups.schemas"
    ),
    "IdentityCommunicationGroupRead": "app.modules.identities.communication_groups.schemas",
    "IdentityCommunicationGroupWrite": "app.modules.identities.communication_groups.schemas",
    "IdentityMaterialRead": "app.modules.identities.materials.schemas",
    "IdentityProfileCreate": "app.modules.identities.profiles.schemas",
    "IdentityProfileRead": "app.modules.identities.profiles.schemas",
    "IdentityProfileUpdate": "app.modules.identities.profiles.schemas",
    "LLMProfileCreate": "app.modules.llm.schemas",
    "LLMProfileModelsResult": "app.modules.llm.schemas",
    "LLMProfileRead": "app.modules.llm.schemas",
    "LLMProfileTestResult": "app.modules.llm.schemas",
    "LLMProfileUpdate": "app.modules.llm.schemas",
    "MatchAnalysisJobActionResponse": "app.modules.matching.schemas",
    "MatchAnalysisJobItemRead": "app.modules.matching.schemas",
    "MatchAnalysisJobRead": "app.modules.matching.schemas",
    "OperationLogExportResponse": "app.schemas.diagnostics",
    "OperationLogListResponse": "app.schemas.diagnostics",
    "OperationLogRead": "app.schemas.diagnostics",
    "ProfessorDashboardItemRead": "app.modules.professors.schemas",
    "ProfessorImportResult": "app.modules.professors.schemas",
    "ProfessorRead": "app.modules.professors.schemas",
    "WorkspaceMessageRead": "app.schemas.workspace",
    "WorkspaceThreadRead": "app.schemas.workspace",
}


def __getattr__(name: str) -> object:
    module_name = _SCHEMA_EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


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
