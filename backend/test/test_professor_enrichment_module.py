from __future__ import annotations

import unittest


class ProfessorEnrichmentModuleCompatibilityTest(unittest.TestCase):
    def test_legacy_http_exports_reference_enrichment_module(self) -> None:
        from app.api import professor_information_enrichment as legacy
        from app.modules.professors.enrichment import api

        self.assertIs(legacy.router, api.router)
        self.assertIs(legacy.professor_router, api.professor_router)
        self.assertIs(
            legacy.create_batch_information_enrichment_job,
            api.create_batch_information_enrichment_job,
        )

    def test_legacy_schema_exports_reference_enrichment_module(self) -> None:
        from app.modules.professors.enrichment import schemas
        from app.schemas import professor_information_enrichment as legacy

        self.assertIs(
            legacy.ProfessorInformationEnrichmentJobRead,
            schemas.ProfessorInformationEnrichmentJobRead,
        )
        self.assertIs(
            legacy.CreateProfessorInformationEnrichmentJobRequest,
            schemas.CreateProfessorInformationEnrichmentJobRequest,
        )

    def test_legacy_service_exports_reference_enrichment_module(self) -> None:
        from app.modules.professors.enrichment import service
        from app.services import professor_information_enrichment as legacy

        self.assertIs(
            legacy.create_professor_information_enrichment_job,
            service.create_professor_information_enrichment_job,
        )
        self.assertIs(
            legacy.apply_enrichment_to_professor,
            service.apply_enrichment_to_professor,
        )


if __name__ == "__main__":
    unittest.main()
