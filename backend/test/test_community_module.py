from __future__ import annotations

import unittest


class CommunityModuleBoundaryTest(unittest.TestCase):
    def test_public_facade_reexports_mentor_capabilities(self) -> None:
        from app.modules.community import public
        from app.modules.community.mentors import schemas, service

        self.assertIs(public.CommunityImportPayload, schemas.CommunityImportPayload)
        self.assertIs(public.CommunityDataError, service.CommunityDataError)
        self.assertIs(
            public.build_community_share_package,
            service.build_community_share_package,
        )


if __name__ == "__main__":
    unittest.main()
