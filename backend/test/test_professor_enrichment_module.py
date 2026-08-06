from __future__ import annotations

import unittest


class ProfessorEnrichmentModuleBoundaryTest(unittest.TestCase):
    def test_professor_facade_reexports_enrichment_contracts(self) -> None:
        from app.modules.professors import public
        from app.modules.professors.enrichment import schemas, service

        self.assertIs(
            public.ProfessorInformationEnrichmentJobRead,
            schemas.ProfessorInformationEnrichmentJobRead,
        )
        self.assertIs(
            public.create_professor_information_enrichment_job,
            service.create_professor_information_enrichment_job,
        )
        self.assertIs(
            public.apply_enrichment_to_professor,
            service.apply_enrichment_to_professor,
        )


if __name__ == "__main__":
    unittest.main()
