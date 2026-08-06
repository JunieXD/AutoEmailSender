from __future__ import annotations

import importlib
import subprocess
import sys
import unittest


class LLMModuleCompatibilityTest(unittest.TestCase):
    def assert_exports_identical(self, legacy_name: str, owner_name: str) -> None:
        legacy = importlib.import_module(legacy_name)
        owner = importlib.import_module(owner_name)

        self.assertEqual(set(legacy.__all__), set(owner.__all__))
        for name in owner.__all__:
            self.assertIs(
                getattr(legacy, name),
                getattr(owner, name),
                msg=f"{legacy_name}.{name} must reference {owner_name}.{name}",
            )

    def test_legacy_http_exports_reference_llm_module(self) -> None:
        self.assert_exports_identical("app.api.llm_profiles", "app.modules.llm.api")

    def test_legacy_schema_exports_reference_llm_module(self) -> None:
        self.assert_exports_identical("app.schemas.llm_profile", "app.modules.llm.schemas")

    def test_legacy_runtime_exports_reference_llm_module(self) -> None:
        self.assert_exports_identical("app.services.llm_runtime", "app.modules.llm.runtime")

    def test_legacy_endpoint_adaptation_exports_reference_llm_module(self) -> None:
        self.assert_exports_identical(
            "app.services.llm_endpoint_adaptation",
            "app.modules.llm.adaptation.endpoint",
        )

    def test_legacy_thinking_adaptation_exports_reference_llm_module(self) -> None:
        self.assert_exports_identical(
            "app.services.thinking_adaptation",
            "app.modules.llm.adaptation.thinking",
        )

    def test_legacy_structured_output_exports_reference_llm_module(self) -> None:
        self.assert_exports_identical(
            "app.services.structured_output_adaptation",
            "app.modules.llm.adaptation.structured_output",
        )

    def test_schema_aggregate_and_public_facade_reference_domain_owners(self) -> None:
        from app import schemas as aggregate
        from app.modules.llm import public, runtime, schemas
        from app.modules.llm.adaptation import endpoint, structured_output, thinking

        self.assertIs(aggregate.LLMProfileRead, schemas.LLMProfileRead)
        self.assertIs(public.LLMProfileRead, schemas.LLMProfileRead)
        self.assertIs(public.LLMRuntimeError, runtime.LLMRuntimeError)
        self.assertIs(public.endpoint_candidates, endpoint.endpoint_candidates)
        self.assertIs(public.ThinkingAdaptationFailed, thinking.ThinkingAdaptationFailed)
        self.assertIs(
            public.ensure_structured_output_adaptation,
            structured_output.ensure_structured_output_adaptation,
        )

    def test_legacy_modules_import_independently(self) -> None:
        modules = [
            "app.api.llm_profiles",
            "app.schemas.llm_profile",
            "app.services.llm_runtime",
            "app.services.llm_endpoint_adaptation",
            "app.services.thinking_adaptation",
            "app.services.structured_output_adaptation",
        ]
        for module in modules:
            with self.subTest(module=module):
                completed = subprocess.run(
                    [sys.executable, "-c", f"import {module}"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
