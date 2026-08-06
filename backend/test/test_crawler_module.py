from __future__ import annotations

import ast
import importlib
from pathlib import Path
import subprocess
import sys
import unittest


LEGACY_MODULE_OWNERS = {
    "app.schemas.crawl_job": "app.modules.crawler.schemas",
    "app.services.crawl_job_events": "app.modules.crawler.jobs.events",
    "app.services.crawl_job_metrics": "app.modules.crawler.jobs.metrics",
    "app.services.crawl_job_records": "app.modules.crawler.jobs.records",
    "app.services.crawl_job_runs": "app.modules.crawler.jobs.runs",
    "app.services.crawler_chunk_runtime": "app.modules.crawler.pages.chunk_runtime",
    "app.services.crawler_chunking": "app.modules.crawler.pages.chunking",
    "app.services.crawler_debug": "app.modules.crawler.pages.debug",
    "app.services.crawler_domain_policy": "app.modules.crawler.pages.domain_policy",
    "app.services.crawler_llm_endpoint_retry": "app.modules.crawler.llm.endpoint_retry",
    "app.services.crawler_page_fetch_ledger": "app.modules.crawler.pages.fetch_ledger",
    "app.services.crawler_structured_output": "app.modules.crawler.llm.structured_output",
    "app.services.crawler_tools": "app.modules.crawler.pages.tools",
    "app.services.crawler_v2_models": "app.modules.crawler.v2.models",
    "app.services.crawler_v2_profile_extraction": "app.modules.crawler.v2.profile_extraction",
    "app.services.crawler_v2_profile_text_cache": "app.modules.crawler.v2.profile_text_cache",
    "app.services.crawler_v2_profile_url_policy": "app.modules.crawler.v2.profile_url_policy",
    "app.services.crawler_v2_retry": "app.modules.crawler.v2.retry",
    "app.services.crawler_v2_routing": "app.modules.crawler.v2.routing",
    "app.services.crawler_v2_token_usage": "app.modules.crawler.v2.token_usage",
    "app.services.crawler_v2_url_utils": "app.modules.crawler.v2.url_utils",
}


def _owned_public_names(module: object) -> set[str]:
    module_path = Path(str(getattr(module, "__file__")))
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    names.add(target.id)
    return names


class CrawlerModuleCompatibilityTest(unittest.TestCase):
    def test_all_legacy_exports_reference_crawler_module_owners(self) -> None:
        for legacy_name, owner_name in LEGACY_MODULE_OWNERS.items():
            with self.subTest(legacy=legacy_name):
                legacy = importlib.import_module(legacy_name)
                owner = importlib.import_module(owner_name)
                public_names = _owned_public_names(owner)
                self.assertTrue(public_names, owner_name)
                for name in public_names:
                    self.assertIs(
                        getattr(legacy, name),
                        getattr(owner, name),
                        msg=f"{legacy_name}.{name} must reference {owner_name}.{name}",
                    )

    def test_public_facade_reexports_cross_domain_contracts(self) -> None:
        from app.modules.crawler import public, schemas
        from app.modules.crawler.jobs import records, runs
        from app.modules.crawler.pages import debug, tools
        from app.modules.crawler.v2 import profile_text_cache

        self.assertIs(public.CrawlJobCreatePayload, schemas.CrawlJobCreatePayload)
        self.assertIs(
            public.create_faculty_crawl_job_record,
            records.create_faculty_crawl_job_record,
        )
        self.assertIs(public.extract_token_usage, runs.extract_token_usage)
        self.assertIs(public.crawler_debug_file_path, debug.crawler_debug_file_path)
        self.assertIs(
            public.validate_safe_public_crawl_url,
            tools.validate_safe_public_crawl_url,
        )
        self.assertIs(public.profile_text_cache, profile_text_cache.profile_text_cache)

    def test_critical_legacy_modules_import_from_clean_processes(self) -> None:
        modules = (
            "app.schemas.crawl_job",
            "app.services.crawler_tools",
            "app.services.crawl_job_records",
            "app.services.crawler_v2_routing",
        )
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
