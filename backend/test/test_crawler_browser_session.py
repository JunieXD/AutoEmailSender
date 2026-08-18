from __future__ import annotations

import time
import unittest

from app.modules.crawler.pages.browser_session import BrowserCookieSessionCache


class BrowserCookieSessionCacheTests(unittest.TestCase):
    def test_returns_only_unexpired_cookies_matching_target_hostname(self) -> None:
        cache = BrowserCookieSessionCache()
        scope = ("run", 17)
        cache.remember(
            scope,
            [
                {
                    "name": "school_challenge",
                    "value": "school",
                    "domain": ".example.edu",
                    "path": "/",
                    "expires": -1,
                },
                {
                    "name": "other_challenge",
                    "value": "other",
                    "domain": "other.edu",
                    "path": "/",
                    "expires": -1,
                },
                {
                    "name": "expired",
                    "value": "old",
                    "domain": ".example.edu",
                    "path": "/",
                    "expires": time.time() - 1,
                },
            ],
        )

        school = cache.get_for_url(scope, "https://cs.example.edu/faculty")
        other = cache.get_for_url(scope, "https://other.edu/faculty")

        self.assertEqual([cookie["name"] for cookie in school], ["school_challenge"])
        self.assertEqual([cookie["name"] for cookie in other], ["other_challenge"])

    def test_merges_cookie_updates_and_discards_one_scope(self) -> None:
        cache = BrowserCookieSessionCache()
        scope = ("run", 17)
        other_scope = ("run", 18)
        original = {
            "name": "challenge",
            "value": "old",
            "domain": "example.edu",
            "path": "/",
            "expires": -1,
        }
        updated = {**original, "value": "new"}
        cache.remember(scope, [original])
        cache.remember(scope, [updated])
        cache.remember(other_scope, [original])

        self.assertEqual(
            cache.get_for_url(scope, "https://example.edu")[0]["value"],
            "new",
        )

        cache.discard_scope(scope)

        self.assertEqual(cache.get_for_url(scope, "https://example.edu"), ())
        self.assertEqual(
            cache.get_for_url(other_scope, "https://example.edu")[0]["value"],
            "old",
        )
