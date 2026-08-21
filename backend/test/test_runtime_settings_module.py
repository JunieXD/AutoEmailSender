from __future__ import annotations

import unittest


class RuntimeSettingsModuleBoundaryTests(unittest.TestCase):
    def test_system_facade_reexports_runtime_settings_contracts(self) -> None:
        from app.modules.system import public
        from app.modules.system.runtime_settings import schemas, service

        self.assertIs(public.RuntimeSettingsRead, schemas.RuntimeSettingsRead)
        self.assertIs(public.RuntimeSettingsUpdate, schemas.RuntimeSettingsUpdate)
        self.assertIs(public.get_runtime_settings, service.get_runtime_settings)
        self.assertIs(
            public.serialize_runtime_settings, service.serialize_runtime_settings
        )
        self.assertIs(public.update_runtime_settings, service.update_runtime_settings)


if __name__ == "__main__":
    unittest.main()
