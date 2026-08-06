from __future__ import annotations

import unittest


class IdentityMaterialModuleBoundaryTest(unittest.TestCase):
    def test_public_facade_reexports_material_contracts(self) -> None:
        from app.modules.identities import public
        from app.modules.identities.materials import schemas, service, support

        self.assertIs(public.IdentityMaterialRead, schemas.IdentityMaterialRead)
        self.assertIs(public.MaterialMutationError, service.MaterialMutationError)
        self.assertIs(
            public.upload_identity_material_record,
            service.upload_identity_material_record,
        )
        self.assertIs(public.material_can_be_primary, support.material_can_be_primary)
        self.assertIs(
            public.ensure_material_extracted_text,
            support.ensure_material_extracted_text,
        )


if __name__ == "__main__":
    unittest.main()
