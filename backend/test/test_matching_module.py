from __future__ import annotations

import unittest


class MatchingModuleCompatibilityTest(unittest.TestCase):
    def test_legacy_http_exports_reference_matching_module(self) -> None:
        from app.api import match_analysis_jobs as legacy
        from app.modules.matching import api

        self.assertIs(legacy.router, api.router)
        self.assertIs(legacy.create_job, api.create_job)
        self.assertIs(legacy.restore_match_analysis_job, api.restore_match_analysis_job)

    def test_legacy_schema_exports_reference_matching_module(self) -> None:
        from app.modules.matching import schemas
        from app.schemas import match_analysis_job as legacy

        self.assertIs(legacy.MatchAnalysisJobRead, schemas.MatchAnalysisJobRead)
        self.assertIs(legacy.MatchAnalysisJobItemRead, schemas.MatchAnalysisJobItemRead)
        self.assertIs(
            legacy.CreateMatchAnalysisJobRequest,
            schemas.CreateMatchAnalysisJobRequest,
        )

    def test_legacy_job_runtime_exports_reference_matching_module(self) -> None:
        from app.modules.matching import job_runtime
        from app.services import match_analysis_job_runtime as legacy

        self.assertIs(
            legacy.create_match_analysis_job,
            job_runtime.create_match_analysis_job,
        )
        self.assertIs(
            legacy.run_queued_match_analysis_jobs_once,
            job_runtime.run_queued_match_analysis_jobs_once,
        )

    def test_legacy_scoring_exports_reference_matching_module(self) -> None:
        from app.modules.matching import scoring
        from app.services import matching as legacy

        self.assertIs(legacy.estimate_match_score, scoring.estimate_match_score)
        self.assertIs(legacy.build_draft_email, scoring.build_draft_email)

    def test_task_runtime_analysis_exports_are_compatibility_aliases(self) -> None:
        from app.modules.matching import task_analysis
        from app.services import task_runtime

        export_names = (
            "MatchAnalysisAlreadyRunningError",
            "MatchCalculationActionResult",
            "MatchCalculationCanceledError",
            "MatchUsageSummary",
            "calculate_task_match",
            "calculate_task_match_once",
            "recover_interrupted_match_analysis_runs",
        )
        for name in export_names:
            with self.subTest(name=name):
                self.assertIs(getattr(task_runtime, name), getattr(task_analysis, name))


if __name__ == "__main__":
    unittest.main()
