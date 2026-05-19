from __future__ import annotations

import importlib
import json
import subprocess
import sys
import unittest


class ApiImportBoundaryTest(unittest.TestCase):
    def test_identity_serializers_import_does_not_load_route_modules(self) -> None:
        script = """
import importlib
import json
import sys

importlib.import_module("app.api.identity_serializers")
print(json.dumps({name: name in sys.modules for name in [
    "app.api.batch_tasks",
    "app.api.crawl_jobs",
    "app.api.test_compose",
    "app.api.workspaces",
]}))
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
        )
        loaded_modules = json.loads(result.stdout)

        self.assertFalse(loaded_modules["app.api.batch_tasks"])
        self.assertFalse(loaded_modules["app.api.crawl_jobs"])
        self.assertFalse(loaded_modules["app.api.test_compose"])
        self.assertFalse(loaded_modules["app.api.workspaces"])

    def test_router_aggregation_loads_expected_routers(self) -> None:
        routers = importlib.import_module("app.api.routers")

        self.assertGreaterEqual(len(routers.API_ROUTERS), 10)
        self.assertTrue(all(hasattr(router, "routes") for router in routers.API_ROUTERS))


if __name__ == "__main__":
    unittest.main()
