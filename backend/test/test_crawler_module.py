from __future__ import annotations

import unittest


class CrawlerModuleBoundaryTest(unittest.TestCase):
    def test_public_facade_reexports_cross_domain_contracts(self) -> None:
        from app.modules.crawler import public, schemas
        from app.modules.crawler.jobs import records, recovery, runs
        from app.modules.crawler.pages import debug, tools
        from app.modules.crawler.v2 import profile_text_cache, scheduler

        self.assertIs(public.CrawlJobCreatePayload, schemas.CrawlJobCreatePayload)
        self.assertIs(
            public.create_faculty_crawl_job_record,
            records.create_faculty_crawl_job_record,
        )
        self.assertIs(
            public.extract_token_usage_from_llm_response,
            runs.extract_token_usage_from_llm_response,
        )
        self.assertIs(public.crawler_debug_file_path, debug.crawler_debug_file_path)
        self.assertIs(
            public.validate_safe_public_crawl_url,
            tools.validate_safe_public_crawl_url,
        )
        self.assertIs(public.profile_text_cache, profile_text_cache.profile_text_cache)
        self.assertIs(
            public.recover_interrupted_crawl_jobs,
            recovery.recover_interrupted_crawl_jobs,
        )
        self.assertIs(public.run_crawler_v2_once, scheduler.run_crawler_v2_once)


if __name__ == "__main__":
    unittest.main()
