"""Declared capacity, and the paging helpers that keep every read bounded (B.16).

Owner: SERVICES (the skeleton wrote the constants and the helpers first).
`backend` and `engine` import this module; it imports only `mdm.models`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from typing import TypeVar

from mdm.models.errors import CapacityError

T = TypeVar("T")
K = TypeVar("K")

DECLARED_GOLDEN_PER_ENTITY = 1_000_000
PATH_TO_GOLDEN_PER_ENTITY = 5_000_000
DECLARED_ARRIVALS_PER_DAY = 200_000
#: later bulk changes reach the published tables no faster than this (the listener interface's proposal);
#: the setting MDM_THROTTLE_ROWS_PER_HOUR applies it, 0 = off
PROPOSED_BULK_ROWS_PER_HOUR = 200_000

READ_PAGE = 5_000
KEY_CHUNK = 5_000
WRITE_CHUNK_ROWS = 10_000
ARRIVAL_BATCH = 1_000
BULK_BATCH = 10_000
COMMIT_CHUNK_ROWS = 500
BULK_COMMIT_CHUNK_ROWS = 10_000
MAX_CANDIDATES = 200
STOP_KEY_RECORDS = 1_000
MAX_MEMBERS_CHECKED = 500
GAP_PROBE_RANGES = 100
LANDING_RETENTION_DAYS = 14
RECONCILE_EVERY_HOURS = 24
U_SAMPLE_PAIRS = 100_000
EM_SAMPLE_RECORDS = 20_000
EM_MAX_PAIRS = 200_000
EM_MAX_ITERATIONS = 50
MIN_ID_PAIRS = 200
FREQUENCY_SAMPLE_RECORDS = 100_000
PROFILE_DISTINCT_CAP = 100_000
FEED_PAGE_ROWS = 5_000
#: the landing contract's size limits: a row over any of them is rejected `too_large` (comparing and
#: standardising cost grows with a value's length, so one oversized row must not slow arrival)
MAX_TEXT_CHARS = 4_000
MAX_GROUP_ITEMS = 100
MAX_PAYLOAD_BYTES = 64 * 1024

#: tables no statement may read without a key document, a full key or a LIMIT; + every entity table
LARGE_TABLES = (
    "source_state",
    "blocking_key",
    "candidate_pair",
    "task",
    "source_version",
    "personal_value",
    "change_log",
    "source_change",
    "xref",
    "change",
    "relationship",
    "arrival_queue",
    "arrival_gap",
)


def pages(
    fetch: Callable[[K | None, int], list[T]], key: Callable[[T], K], page: int = READ_PAGE
) -> Iterator[list[T]]:
    """Keyset paging, never OFFSET: `fetch(after, limit)` until a short page; `key(last row)` is the next `after`."""
    after: K | None = None
    while True:
        rows = fetch(after, page)
        if rows:
            yield rows
        if len(rows) < page:
            return
        after = key(rows[-1])


def chunks(items: Sequence[T], size: int = KEY_CHUNK) -> Iterator[Sequence[T]]:
    """`items` in slices of at most `size`."""
    if size < 1:
        raise CapacityError("bad_chunk_size", size=size)
    for start in range(0, len(items), size):
        yield items[start : start + size]


def require_limit(limit: int, ceiling: int) -> int:
    """`limit` when 1 <= limit <= ceiling; else `CapacityError` (an unbounded or oversized read)."""
    if limit < 1 or limit > ceiling:
        raise CapacityError("limit_out_of_range", limit=limit, ceiling=ceiling)
    return limit
