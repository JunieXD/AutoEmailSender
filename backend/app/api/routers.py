from __future__ import annotations

from fastapi import APIRouter

from app.api.agent_v1 import router as agent_v1_router
from app.api.batch_tasks import router as batch_tasks_router
from app.modules.identities.communication_groups.api import (
    router as communication_groups_router,
)
from app.api.community_mentors import router as community_mentors_router
from app.api.dashboard import router as dashboard_router
from app.api.crawl_jobs import router as crawl_jobs_router
from app.api.diagnostics import router as diagnostics_router
from app.api.email_tasks import router as email_tasks_router
from app.modules.identities.profiles.api import router as identities_router
from app.api.llm_profiles import router as llm_profiles_router
from app.modules.identities.materials.api import router as materials_router
from app.api.outreach_templates import router as outreach_templates_router
from app.api.match_analysis_jobs import router as match_analysis_jobs_router
from app.api.professors import router as professors_router
from app.api.professor_information_enrichment import (
    professor_router as professor_information_enrichment_professor_router,
    router as professor_information_enrichment_jobs_router,
)
from app.modules.system.runtime_settings.api import router as runtime_settings_router
from app.api.test_compose import router as test_compose_router
from app.api.token_usage import router as token_usage_router
from app.api.workspaces import router as workspaces_router

API_ROUTERS: tuple[APIRouter, ...] = (
    agent_v1_router,
    identities_router,
    communication_groups_router,
    materials_router,
    outreach_templates_router,
    match_analysis_jobs_router,
    llm_profiles_router,
    professors_router,
    community_mentors_router,
    professor_information_enrichment_professor_router,
    professor_information_enrichment_jobs_router,
    test_compose_router,
    crawl_jobs_router,
    diagnostics_router,
    batch_tasks_router,
    dashboard_router,
    email_tasks_router,
    workspaces_router,
    token_usage_router,
    runtime_settings_router,
)
