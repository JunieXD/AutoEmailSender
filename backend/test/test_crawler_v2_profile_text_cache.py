from __future__ import annotations

import unittest

from app.services.crawler_v2_profile_text_cache import ProfileTextCache


class ProfileTextCacheTests(unittest.TestCase):
    def test_evicts_least_recently_used_entry_at_capacity(self) -> None:
        cache = ProfileTextCache(max_entries=2, max_characters=100)
        first = (1, 10, 100, "https://example.edu/first")
        second = (1, 10, 101, "https://example.edu/second")
        third = (1, 10, 102, "https://example.edu/third")

        cache.put(first, "first")
        cache.put(second, "second")
        self.assertEqual(cache.get(first), "first")
        cache.put(third, "third")

        self.assertIn(first, cache)
        self.assertNotIn(second, cache)
        self.assertIn(third, cache)
        self.assertEqual(len(cache), 2)

    def test_character_budget_evicts_old_entries_and_rejects_oversized_value(self) -> None:
        cache = ProfileTextCache(max_entries=10, max_characters=8)
        first = (1, 10, 100, "first")
        second = (1, 10, 101, "second")
        oversized = (1, 10, 102, "oversized")

        self.assertTrue(cache.put(first, "12345"))
        self.assertTrue(cache.put(second, "6789"))
        self.assertNotIn(first, cache)
        self.assertEqual(cache.total_characters, 4)

        self.assertFalse(cache.put(oversized, "123456789"))
        self.assertNotIn(oversized, cache)
        self.assertEqual(cache.total_characters, 4)

    def test_candidate_and_job_cleanup_do_not_remove_unrelated_entries(self) -> None:
        cache = ProfileTextCache(max_entries=10, max_characters=100)
        target = (1, 10, 100, "target")
        same_candidate_other_factory = (2, 10, 100, "other-factory")
        same_job_other_candidate = (1, 10, 101, "other-candidate")
        other_job = (1, 11, 100, "other-job")
        for key in (target, same_candidate_other_factory, same_job_other_candidate, other_job):
            cache.put(key, key[3])

        self.assertEqual(
            cache.discard_candidate(job_id=10, candidate_id=100, session_factory_id=1),
            1,
        )
        self.assertNotIn(target, cache)
        self.assertIn(same_candidate_other_factory, cache)
        self.assertIn(same_job_other_candidate, cache)

        self.assertEqual(cache.discard_job(job_id=10), 2)
        self.assertNotIn(same_candidate_other_factory, cache)
        self.assertNotIn(same_job_other_candidate, cache)
        self.assertIn(other_job, cache)


if __name__ == "__main__":
    unittest.main()
