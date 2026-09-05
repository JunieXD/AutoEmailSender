from __future__ import annotations

import unittest

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
