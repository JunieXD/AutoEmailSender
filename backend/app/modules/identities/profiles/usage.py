from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from threading import RLock
from typing import Iterator


class IdentityProfileRetiringError(ValueError):
    pass


_lock = RLock()
_active_usages: dict[int, Counter[str]] = {}
_retiring_identity_ids: set[int] = set()


@contextmanager
def track_identity_profile_usage(identity_id: int, kind: str) -> Iterator[None]:
    with _lock:
        if identity_id in _retiring_identity_ids:
            raise IdentityProfileRetiringError(
                "发件身份正在退役，请刷新后选择其他身份。"
            )
        usages = _active_usages.setdefault(identity_id, Counter())
        usages[kind] += 1
    try:
        yield
    finally:
        with _lock:
            usages = _active_usages.get(identity_id)
            if usages is not None:
                usages[kind] -= 1
                if usages[kind] <= 0:
                    del usages[kind]
                if not usages:
                    _active_usages.pop(identity_id, None)


def get_identity_profile_usage_counts(identity_id: int) -> dict[str, int]:
    with _lock:
        return dict(_active_usages.get(identity_id, {}))


def identity_profile_retirement_in_progress(identity_id: int) -> bool:
    with _lock:
        return identity_id in _retiring_identity_ids


def begin_identity_profile_retirement(identity_id: int) -> bool:
    with _lock:
        if _active_usages.get(identity_id) or identity_id in _retiring_identity_ids:
            return False
        _retiring_identity_ids.add(identity_id)
        return True


def end_identity_profile_retirement(identity_id: int) -> None:
    with _lock:
        _retiring_identity_ids.discard(identity_id)
