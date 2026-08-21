from __future__ import annotations

import unittest


class ProfessorModuleBoundaryTest(unittest.TestCase):
    def test_public_facade_reexports_professor_contracts(self) -> None:
        from app.modules.professors import (
            management,
            mutations,
            normalization,
            public,
            schemas,
        )

        self.assertIs(public.ProfessorRead, schemas.ProfessorRead)
        self.assertIs(
            public.normalize_professor_email, management.normalize_professor_email
        )
        self.assertIs(public.create_professor_record, mutations.create_professor_record)
        self.assertIs(
            public.normalize_recent_papers, normalization.normalize_recent_papers
        )

    def test_schema_aggregate_references_professor_owner(self) -> None:
        from app import schemas as aggregate
        from app.modules.professors import schemas

        self.assertIs(aggregate.ProfessorRead, schemas.ProfessorRead)
        self.assertIs(aggregate.ProfessorImportResult, schemas.ProfessorImportResult)


if __name__ == "__main__":
    unittest.main()
