from __future__ import annotations

import unittest


class RuntimeSettingsModuleCompatibilityTests(unittest.TestCase):
    def test_legacy_api_path_reexports_module_router(self) -> None:
        from app.api.runtime_settings import router as legacy_router
        from app.modules.system.runtime_settings.api import router

        self.assertIs(legacy_router, router)

    def test_legacy_schema_path_reexports_module_types(self) -> None:
        from app.modules.system.public import (
            RuntimeSettingsRead,
            RuntimeSettingsUpdate,
        )
        from app.schemas.runtime_settings import (
            RuntimeSettingsRead as LegacyRuntimeSettingsRead,
            RuntimeSettingsUpdate as LegacyRuntimeSettingsUpdate,
        )

        self.assertIs(LegacyRuntimeSettingsRead, RuntimeSettingsRead)
        self.assertIs(LegacyRuntimeSettingsUpdate, RuntimeSettingsUpdate)

    def test_legacy_service_paths_reexport_module_functions(self) -> None:
        from app.modules.system.public import (
            get_runtime_settings,
            serialize_runtime_settings,
            update_runtime_settings,
        )
        from app.modules.system.runtime_settings.service import (
            get_or_create_app_settings,
        )
        from app.services.runtime_settings import (
            get_runtime_settings as legacy_get_runtime_settings,
            serialize_runtime_settings as legacy_serialize_runtime_settings,
            update_runtime_settings as legacy_update_runtime_settings,
        )
        from app.services.system_settings import (
            get_or_create_app_settings as legacy_get_or_create_app_settings,
        )

        self.assertIs(legacy_get_runtime_settings, get_runtime_settings)
        self.assertIs(legacy_serialize_runtime_settings, serialize_runtime_settings)
        self.assertIs(legacy_update_runtime_settings, update_runtime_settings)
        self.assertIs(legacy_get_or_create_app_settings, get_or_create_app_settings)


if __name__ == "__main__":
    unittest.main()
