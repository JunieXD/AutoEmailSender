"""Compose the versioned Agent HTTP adapters."""

from fastapi import APIRouter

from . import (
    campaigns,
    communication_groups,
    communications,
    community,
    crawler,
    drafts,
    enrichment,
    handoffs,
    identities,
    llm_profiles,
    matching,
    materials,
    plans,
    professors,
    reporting,
    system,
    templates,
    test_email,
    workspace,
)

router = APIRouter(prefix="/api/agent/v1", tags=["agent-v1"])

router.include_router(system.router)
router.include_router(professors.router)
router.include_router(workspace.router)
router.include_router(drafts.router)
router.include_router(crawler.router)
router.include_router(communications.router)
router.include_router(handoffs.router)
router.include_router(community.router)
router.include_router(templates.router)
router.include_router(materials.router)
router.include_router(identities.router)
router.include_router(llm_profiles.router)
router.include_router(communication_groups.router)
router.include_router(matching.router)
router.include_router(enrichment.router)
router.include_router(reporting.router)
router.include_router(campaigns.router)
router.include_router(test_email.router)
router.include_router(plans.router)
