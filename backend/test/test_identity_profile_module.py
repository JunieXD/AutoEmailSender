from __future__ import annotations

import unittest


class IdentityProfileModuleCompatibilityTest(unittest.TestCase):
    def test_legacy_http_exports_reference_profile_module(self) -> None:
        from app.api import identities as legacy
        from app.modules.identities.profiles import api

        self.assertIs(legacy.router, api.router)
        self.assertIs(legacy.create_identity, api.create_identity)
        self.assertIs(legacy.update_identity, api.update_identity)
        self.assertIs(legacy.delete_identity, api.delete_identity)

    def test_legacy_schema_exports_reference_domain_schemas(self) -> None:
        from app.modules.identities.materials import schemas as material_schemas
        from app.modules.identities.profiles import schemas as profile_schemas
        from app.schemas import identity as legacy

        self.assertIs(legacy.ConnectionTestResult, profile_schemas.ConnectionTestResult)
        self.assertIs(legacy.IdentityProfileRead, profile_schemas.IdentityProfileRead)
        self.assertIs(legacy.IdentityMaterialRead, material_schemas.IdentityMaterialRead)

    def test_legacy_serializer_exports_reference_domain_serializers(self) -> None:
        from app.api import identity_serializers as legacy
        from app.modules.identities.materials import serializer as material_serializer
        from app.modules.identities.profiles import serializer as profile_serializer

        self.assertIs(legacy.serialize_identity, profile_serializer.serialize_identity)
        self.assertIs(legacy.serialize_material, material_serializer.serialize_material)

    def test_package_level_schema_exports_are_lazy_compatible(self) -> None:
        from app import schemas
        from app.modules.identities.materials.schemas import IdentityMaterialRead
        from app.modules.identities.profiles.schemas import IdentityProfileRead

        self.assertIs(schemas.IdentityProfileRead, IdentityProfileRead)
        self.assertIs(schemas.IdentityMaterialRead, IdentityMaterialRead)


if __name__ == "__main__":
    unittest.main()
