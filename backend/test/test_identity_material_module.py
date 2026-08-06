from __future__ import annotations

import unittest


class IdentityMaterialModuleCompatibilityTest(unittest.TestCase):
    def test_legacy_http_exports_reference_material_module(self) -> None:
        from app.api import materials as legacy
        from app.modules.identities.materials import api

        self.assertIs(legacy.router, api.router)
        self.assertIs(legacy.upload_identity_material, api.upload_identity_material)
        self.assertIs(legacy.set_primary_material, api.set_primary_material)
        self.assertIs(legacy.delete_material, api.delete_material)

    def test_legacy_mutation_exports_reference_material_module(self) -> None:
        from app.modules.identities.materials import service
        from app.services import material_mutations as legacy

        self.assertIs(legacy.MaterialMutationError, service.MaterialMutationError)
        self.assertIs(
            legacy.upload_identity_material_record,
            service.upload_identity_material_record,
        )
        self.assertIs(
            legacy.delete_identity_material_record,
            service.delete_identity_material_record,
        )

    def test_legacy_support_exports_reference_material_module(self) -> None:
        from app.modules.identities.materials import support
        from app.services import materials as legacy

        self.assertIs(legacy.material_can_be_primary, support.material_can_be_primary)
        self.assertIs(
            legacy.ensure_material_extracted_text,
            support.ensure_material_extracted_text,
        )
        self.assertIs(
            legacy.build_material_download_name,
            support.build_material_download_name,
        )


if __name__ == "__main__":
    unittest.main()
