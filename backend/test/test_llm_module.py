from __future__ import annotations

import unittest


class LLMModuleBoundaryTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
