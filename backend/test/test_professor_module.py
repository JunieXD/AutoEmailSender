from __future__ import annotations

import unittest


class ProfessorModuleCompatibilityTest(unittest.TestCase):
    def test_legacy_http_exports_reference_professor_module(self) -> None:
        from app.api import professors as legacy
        from app.modules.professors import api

        self.assertIs(legacy.router, api.router)
        self.assertIs(legacy.list_professors, api.list_professors)
        self.assertIs(legacy.create_professor, api.create_professor)
        self.assertIs(legacy.bulk_update_professor_tags, api.bulk_update_professor_tags)

    def test_legacy_schema_exports_reference_professor_module(self) -> None:
        from app.modules.professors import schemas
        from app.schemas import professor as legacy

        self.assertIs(legacy.ProfessorRead, schemas.ProfessorRead)
        self.assertIs(legacy.ProfessorUpsertPayload, schemas.ProfessorUpsertPayload)
        self.assertIs(legacy.ProfessorTagRead, schemas.ProfessorTagRead)

    def test_legacy_service_exports_reference_professor_module(self) -> None:
        from app.modules.professors import management, mutations, normalization
        from app.services import professor_field_normalization as legacy_normalization
        from app.services import professor_management as legacy_management
        from app.services import professor_mutations as legacy_mutations

        self.assertIs(
            legacy_management.normalize_professor_email,
            management.normalize_professor_email,
        )
        self.assertIs(
            legacy_mutations.create_professor_record,
            mutations.create_professor_record,
        )
        self.assertIs(
            legacy_normalization.normalize_recent_papers,
            normalization.normalize_recent_papers,
        )


if __name__ == "__main__":
    unittest.main()
