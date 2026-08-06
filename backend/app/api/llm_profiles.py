"""Compatibility exports for the migrated LLM profile HTTP adapter."""

from app.modules.llm.api import (
    create_llm_profile,
    delete_llm_profile,
    fetch_models_for_llm_profile,
    list_llm_profiles,
    preview_llm_profile_models,
    preview_llm_profile_test,
    router,
    set_default_llm_profile,
    test_llm_profile,
    update_llm_profile,
)

__all__ = [
    "create_llm_profile",
    "delete_llm_profile",
    "fetch_models_for_llm_profile",
    "list_llm_profiles",
    "preview_llm_profile_models",
    "preview_llm_profile_test",
    "router",
    "set_default_llm_profile",
    "test_llm_profile",
    "update_llm_profile",
]
