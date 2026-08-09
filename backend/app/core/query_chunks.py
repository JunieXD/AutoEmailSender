from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import TypeVar


ValueT = TypeVar("ValueT")

# Keep ample headroom below SQLite's build-dependent bind-parameter limit and
# below common limits used by other SQL engines. Callers may use several bound
# parameters per item without accidentally producing an oversized statement.
DEFAULT_SQL_IN_CHUNK_SIZE = 400


def chunked_values(
    values: Iterable[ValueT],
    *,
    size: int = DEFAULT_SQL_IN_CHUNK_SIZE,
) -> Iterator[tuple[ValueT, ...]]:
    if size < 1:
        raise ValueError("size 必须大于 0")
    chunk: list[ValueT] = []
    for value in values:
        chunk.append(value)
        if len(chunk) == size:
            yield tuple(chunk)
            chunk = []
    if chunk:
        yield tuple(chunk)


def unique_positive_ids(values: Iterable[int]) -> list[int]:
    return list(dict.fromkeys(int(value) for value in values if int(value) > 0))
