from __future__ import annotations

import unittest

from app.services.crawler_v2_url_utils import is_same_domain, normalize_url, task_dedupe_key


class CrawlerV2UrlUtilsTests(unittest.TestCase):
    def test_normalize_url_removes_fragments_default_ports_and_tracking_query(self) -> None:
        self.assertEqual(
            normalize_url("HTTPS://Example.edu:443/faculty/?utm_source=x&b=2#team"),
            "https://example.edu/faculty/?b=2",
        )

    def test_normalize_url_resolves_relative_links(self) -> None:
        self.assertEqual(
            normalize_url("../profile/zhang.html", base_url="https://cs.example.edu/people/list/index.html"),
            "https://cs.example.edu/people/profile/zhang.html",
        )

    def test_same_domain_allows_subdomain_relationship(self) -> None:
        self.assertTrue(is_same_domain("https://cs.example.edu/a", "https://example.edu/b"))
        self.assertTrue(is_same_domain("https://example.edu/a", "https://cs.example.edu/b"))
        self.assertFalse(is_same_domain("https://evil-example.edu/a", "https://example.edu/b"))

    def test_task_dedupe_key_uses_job_and_normalized_url(self) -> None:
        self.assertEqual(
            task_dedupe_key(12, "https://EXAMPLE.edu:443/a#x"),
            "12:https://example.edu/a",
        )


if __name__ == "__main__":
    unittest.main()