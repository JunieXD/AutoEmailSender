from importlib import import_module


_SCHEMA_EXPORT_MODULES = {
    "BatchTaskActionResponse": "app.schemas.batch_task",
    "BatchTaskCardRead": "app.schemas.batch_task",
    "BatchTaskItemRead": "app.schemas.batch_task",
    "ConnectionTestResult": "app.modules.identities.profiles.schemas",
    "CreateBatchTaskRequest": "app.schemas.batch_task",
    "CreateMatchAnalysisJobRequest": "app.schemas.match_analysis_job",
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
    "LLMProfileCreate": "app.schemas.llm_profile",
    "LLMProfileModelsResult": "app.schemas.llm_profile",
    "LLMProfileRead": "app.schemas.llm_profile",
    "LLMProfileTestResult": "app.schemas.llm_profile",
    "LLMProfileUpdate": "app.schemas.llm_profile",
    "MatchAnalysisJobActionResponse": "app.schemas.match_analysis_job",
    "MatchAnalysisJobItemRead": "app.schemas.match_analysis_job",
    "MatchAnalysisJobRead": "app.schemas.match_analysis_job",
    "OperationLogExportResponse": "app.schemas.diagnostics",
    "OperationLogListResponse": "app.schemas.diagnostics",
    "OperationLogRead": "app.schemas.diagnostics",
    "ProfessorDashboardItemRead": "app.schemas.professor",
    "ProfessorImportResult": "app.schemas.professor",
    "ProfessorRead": "app.schemas.professor",
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
