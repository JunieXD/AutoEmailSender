from __future__ import annotations

import unittest


class MatchingModuleBoundaryTest(unittest.TestCase):
    def test_public_facade_reexports_matching_contracts(self) -> None:
        from app.modules.matching import (
            job_runtime,
            public,
            schemas,
            scoring,
            task_analysis,
        )

        self.assertIs(public.MatchAnalysisJobRead, schemas.MatchAnalysisJobRead)
        self.assertIs(
            public.create_match_analysis_job,
            job_runtime.create_match_analysis_job,
        )
        self.assertIs(public.estimate_match_score, scoring.estimate_match_score)
        self.assertIs(public.calculate_task_match, task_analysis.calculate_task_match)

    def test_schema_aggregate_references_matching_owner(self) -> None:
        from app import schemas as aggregate
        from app.modules.matching import schemas

        self.assertIs(aggregate.MatchAnalysisJobRead, schemas.MatchAnalysisJobRead)
        self.assertIs(
            aggregate.CreateMatchAnalysisJobRequest,
            schemas.CreateMatchAnalysisJobRequest,
        )


if __name__ == "__main__":
    unittest.main()
