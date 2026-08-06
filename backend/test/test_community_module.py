from __future__ import annotations

import unittest


class CommunityModuleCompatibilityTest(unittest.TestCase):
    def test_legacy_http_exports_reference_community_module(self) -> None:
        from app.api import community_mentors as legacy
        from app.modules.community.mentors import api

        self.assertIs(legacy.router, api.router)
        self.assertIs(
            legacy.get_community_mentor_data_service,
            api.get_community_mentor_data_service,
        )
        self.assertIs(legacy.import_from_community, api.import_from_community)

    def test_legacy_schema_exports_reference_community_module(self) -> None:
        from app.modules.community.mentors import schemas
        from app.schemas import community_mentor as legacy

        self.assertIs(legacy.CommunityMentorRecord, schemas.CommunityMentorRecord)
        self.assertIs(legacy.CommunityImportPayload, schemas.CommunityImportPayload)
        self.assertIs(legacy.CommunityRecordsRead, schemas.CommunityRecordsRead)

    def test_legacy_service_exports_reference_community_module(self) -> None:
        from app.modules.community.mentors import service
        from app.services import community_mentor_data as legacy

        self.assertIs(legacy.CommunityMentorDataService, service.CommunityMentorDataService)
        self.assertIs(legacy.build_community_comparisons, service.build_community_comparisons)
        self.assertIs(legacy.import_community_records, service.import_community_records)

    def test_root_public_entry_reexports_mentor_capabilities(self) -> None:
        from app.modules.community import public
        from app.modules.community.mentors import schemas, service

        self.assertIs(public.CommunityImportPayload, schemas.CommunityImportPayload)
        self.assertIs(public.CommunityDataError, service.CommunityDataError)
        self.assertIs(public.build_community_share_package, service.build_community_share_package)


if __name__ == "__main__":
    unittest.main()
