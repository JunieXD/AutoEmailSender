"""Compatibility exports for migrated professor-enrichment schemas."""

from app.modules.professors.enrichment.schemas import (
    CreateProfessorInformationEnrichmentJobRequest,
    CreateProfessorInformationEnrichmentRequest,
    ProfessorInformationEnrichmentActiveRead,
    ProfessorInformationEnrichmentItemRead,
    ProfessorInformationEnrichmentJobActionRead,
    ProfessorInformationEnrichmentJobRead,
)

__all__ = [
    "CreateProfessorInformationEnrichmentJobRequest",
    "CreateProfessorInformationEnrichmentRequest",
    "ProfessorInformationEnrichmentActiveRead",
    "ProfessorInformationEnrichmentItemRead",
    "ProfessorInformationEnrichmentJobActionRead",
    "ProfessorInformationEnrichmentJobRead",
]
