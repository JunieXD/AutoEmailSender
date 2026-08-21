from __future__ import annotations

import ast
import inspect
import unittest
from pathlib import Path

from pydantic import BaseModel

from app.modules.crawler.llm.structured_output import (
    CandidateEmailSelectionWirePayload,
    CandidateEnrichmentWirePayload,
    CandidateFieldConfidenceWire,
    ProfessorCandidateWirePayload,
    CrawlerChunkWirePayload,
    CrawlerProfileExtractionWirePayload,
    ProfileLinkSelectionWirePayload,
    professor_candidate_wire_to_dict,
)
from app.modules.llm.runtime import (
    DraftGenerationWireResult,
    DraftRewriteResult,
    MatchEvaluationWireResult,
    _prepare_strict_json_schema,
)


class _LooseObjectResult(BaseModel):
    payload: dict[str, object]


class StructuredOutputWireContractTests(unittest.TestCase):
    def test_all_known_non_agent_json_calls_use_the_shared_adaptation(self) -> None:
        from app.modules.llm import runtime as llm_runtime
        from app.modules.crawler.runtime import (
            chunk_worker as crawler_runtime_chunk_worker,
            enrichment_worker as crawler_runtime_enrichment_worker,
            profile_extraction as crawler_runtime_profile_extraction,
            routing as crawler_runtime_routing,
        )

        self.assertIn(
            "request_structured_completion(",
            inspect.getsource(llm_runtime.generate_match_evaluation),
        )
        self.assertGreaterEqual(
            inspect.getsource(llm_runtime.generate_draft_content).count(
                "request_structured_completion("
            ),
            2,
        )
        for function in (
            crawler_runtime_chunk_worker.invoke_chunk_agent,
            crawler_runtime_enrichment_worker.enrich_candidate_profile_with_llm_with_usage,
            crawler_runtime_profile_extraction.invoke_profile_extraction_agent,
            crawler_runtime_routing._invoke_structured_routing_phase,
        ):
            with self.subTest(function=function.__name__):
                self.assertIn(
                    "request_crawler_structured_completion(",
                    inspect.getsource(function),
                )

    def test_business_services_do_not_parse_llm_json_outside_central_runtime(
        self,
    ) -> None:
        services_root = Path(__file__).resolve().parents[1] / "app" / "services"
        violations: list[str] = []
        for path in services_root.glob("*.py"):
            if path.name == "llm_runtime.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                function = node.func
                name = (
                    function.id
                    if isinstance(function, ast.Name)
                    else function.attr
                    if isinstance(function, ast.Attribute)
                    else None
                )
                if name == "parse_structured_result":
                    violations.append(f"{path.name}:{node.lineno}")

        self.assertEqual(violations, [])

    def test_every_production_wire_model_is_strict_schema_compatible(self) -> None:
        from app.modules.crawler.runtime.routing import (
            EntryRoutingPayload,
            PaginationRoutingPayload,
        )

        models = (
            MatchEvaluationWireResult,
            DraftGenerationWireResult,
            DraftRewriteResult,
            ProfessorCandidateWirePayload,
            CandidateEnrichmentWirePayload,
            CandidateEmailSelectionWirePayload,
            ProfileLinkSelectionWirePayload,
            CrawlerChunkWirePayload,
            CrawlerProfileExtractionWirePayload,
            EntryRoutingPayload,
            PaginationRoutingPayload,
        )

        for model in models:
            with self.subTest(model=model.__name__):
                schema = _prepare_strict_json_schema(model.model_json_schema())
                self._assert_all_objects_are_closed_and_required(schema)

    def test_open_ended_dictionary_cannot_be_sent_as_strict_schema(self) -> None:
        with self.assertRaisesRegex(ValueError, "禁止额外字段"):
            _prepare_strict_json_schema(_LooseObjectResult.model_json_schema())

    def test_candidate_wire_metadata_converts_to_existing_business_shape(self) -> None:
        wire = ProfessorCandidateWirePayload(
            name="张三",
            email="zhang@example.edu",
            title="教授",
            university="示例大学",
            school="计算机学院",
            department="软件工程系",
            research_direction="软件工程",
            recent_papers=["Paper A"],
            profile_url="https://example.edu/zhang",
            source_url="https://example.edu/faculty",
            confidence=0.9,
            field_confidence=[
                CandidateFieldConfidenceWire(field="name", confidence=0.95),
                CandidateFieldConfidenceWire(field="email", confidence=0.9),
            ],
            evidence_summary="页面明确列出姓名和邮箱",
        )

        payload = professor_candidate_wire_to_dict(wire)

        self.assertEqual(payload["field_confidence"], {"name": 0.95, "email": 0.9})
        self.assertEqual(payload["evidence"], {"summary": "页面明确列出姓名和邮箱"})
        self.assertNotIn("evidence_summary", payload)

    def _assert_all_objects_are_closed_and_required(self, value: object) -> None:
        if isinstance(value, dict):
            if value.get("type") == "object":
                properties = value.get("properties")
                self.assertIsInstance(properties, dict)
                self.assertIs(value.get("additionalProperties"), False)
                self.assertEqual(
                    set(value.get("required") or []), set(properties or {})
                )
            for item in value.values():
                self._assert_all_objects_are_closed_and_required(item)
        elif isinstance(value, list):
            for item in value:
                self._assert_all_objects_are_closed_and_required(item)


if __name__ == "__main__":
    unittest.main()
