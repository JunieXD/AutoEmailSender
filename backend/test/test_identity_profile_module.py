from __future__ import annotations

import unittest


class IdentityProfileModuleBoundaryTest(unittest.TestCase):
    def test_public_facade_reexports_profile_contracts(self) -> None:
        from app.modules.identities import public
        from app.modules.identities.profiles import schemas, serializer

        self.assertIs(public.ConnectionTestResult, schemas.ConnectionTestResult)
        self.assertIs(public.IdentityProfileRead, schemas.IdentityProfileRead)
        self.assertIs(public.serialize_identity, serializer.serialize_identity)

    def test_package_level_schema_exports_reference_domain_owners(self) -> None:
        from app import schemas
        from app.modules.identities.materials.schemas import IdentityMaterialRead
        from app.modules.identities.profiles.schemas import IdentityProfileRead

        self.assertIs(schemas.IdentityProfileRead, IdentityProfileRead)
        self.assertIs(schemas.IdentityMaterialRead, IdentityMaterialRead)


if __name__ == "__main__":
    unittest.main()
