from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from threading import RLock
from typing import Iterator


class LLMProfileRetiringError(ValueError):
    pass


_lock = RLock()
_active_usages: dict[int, Counter[str]] = {}
_retiring_profile_ids: set[int] = set()


@contextmanager
def track_llm_profile_usage(profile_id: int, kind: str) -> Iterator[None]:
    with _lock:
        if profile_id in _retiring_profile_ids:
            raise LLMProfileRetiringError(
                "模型配置正在退役，请刷新后选择其他模型。"
            )
        usages = _active_usages.setdefault(profile_id, Counter())
        usages[kind] += 1
    try:
        yield
    finally:
        with _lock:
            usages = _active_usages.get(profile_id)
            if usages is not None:
                usages[kind] -= 1
                if usages[kind] <= 0:
                    del usages[kind]
                if not usages:
                    _active_usages.pop(profile_id, None)


def get_llm_profile_usage_counts(profile_id: int) -> dict[str, int]:
    with _lock:
        return dict(_active_usages.get(profile_id, {}))


def llm_profile_retirement_in_progress(profile_id: int) -> bool:
    with _lock:
        return profile_id in _retiring_profile_ids


def begin_llm_profile_retirement(profile_id: int) -> bool:
    with _lock:
        if _active_usages.get(profile_id) or profile_id in _retiring_profile_ids:
            return False
        _retiring_profile_ids.add(profile_id)
        return True


def end_llm_profile_retirement(profile_id: int) -> None:
    with _lock:
        _retiring_profile_ids.discard(profile_id)
