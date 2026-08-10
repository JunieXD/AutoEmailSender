from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable


# session factory, job, persistent job run, candidate, normalized profile URL
ProfileTextCacheKey = tuple[int, int, int | None, int, str]

PROFILE_TEXT_CACHE_MAX_ENTRIES = 128
PROFILE_TEXT_CACHE_MAX_CHARACTERS = 8 * 1024 * 1024


class ProfileTextCache:
    """Small in-process LRU used only across retries of enrichment tasks."""

    def __init__(self, *, max_entries: int, max_characters: int) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        if max_characters <= 0:
            raise ValueError("max_characters must be positive")
        self.max_entries = max_entries
        self.max_characters = max_characters
        self._entries: OrderedDict[ProfileTextCacheKey, str] = OrderedDict()
        self._total_characters = 0

    def get(self, key: ProfileTextCacheKey) -> str | None:
        value = self._entries.get(key)
        if value is None:
            return None
        self._entries.move_to_end(key)
        return value

    def put(self, key: ProfileTextCacheKey, value: str) -> bool:
        previous = self._entries.pop(key, None)
        if previous is not None:
            self._total_characters -= len(previous)

        value_characters = len(value)
        if value_characters > self.max_characters:
            return False

        while self._entries and (
            len(self._entries) >= self.max_entries
            or self._total_characters + value_characters > self.max_characters
        ):
            _, evicted = self._entries.popitem(last=False)
            self._total_characters -= len(evicted)

        self._entries[key] = value
        self._total_characters += value_characters
        return True

    def discard_candidate(
        self,
        *,
        job_id: int,
        candidate_id: int,
        session_factory_id: int | None = None,
    ) -> int:
        return self._discard_matching(
            lambda key: (
                key[1] == job_id
                and key[3] == candidate_id
                and (session_factory_id is None or key[0] == session_factory_id)
            )
        )

    def discard_job(
        self,
        *,
        job_id: int,
        session_factory_id: int | None = None,
    ) -> int:
        return self._discard_matching(
            lambda key: (
                key[1] == job_id
                and (session_factory_id is None or key[0] == session_factory_id)
            )
        )

    def clear(self) -> None:
        self._entries.clear()
        self._total_characters = 0

    @property
    def total_characters(self) -> int:
        return self._total_characters

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, key: object) -> bool:
        return key in self._entries

    def _discard_matching(self, predicate: Callable[[ProfileTextCacheKey], bool]) -> int:
        matching_keys = [key for key in self._entries if predicate(key)]
        for key in matching_keys:
            self._total_characters -= len(self._entries.pop(key))
        return len(matching_keys)


profile_text_cache = ProfileTextCache(
    max_entries=PROFILE_TEXT_CACHE_MAX_ENTRIES,
    max_characters=PROFILE_TEXT_CACHE_MAX_CHARACTERS,
)
