from __future__ import annotations

import importlib
import sys
import unittest


class ApiImportBoundaryTest(unittest.TestCase):
    def test_identity_serializers_import_does_not_load_route_modules(self) -> None:
        for module_name in list(sys.modules):
            if module_name == "app.api" or module_name.startswith("app.api."):
                sys.modules.pop(module_name)

        importlib.import_module("app.api.identity_serializers")

        self.assertNotIn("app.api.batch_tasks", sys.modules)
        self.assertNotIn("app.api.crawl_jobs", sys.modules)
        self.assertNotIn("app.api.test_compose", sys.modules)
        self.assertNotIn("app.api.workspaces", sys.modules)

    def test_router_aggregation_loads_expected_routers(self) -> None:
        routers = importlib.import_module("app.api.routers")

        self.assertGreaterEqual(len(routers.API_ROUTERS), 10)
        self.assertTrue(all(hasattr(router, "routes") for router in routers.API_ROUTERS))


if __name__ == "__main__":
    unittest.main()
