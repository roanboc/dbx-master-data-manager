"""The change feed read: a consumer's view, keyset-paged (owner: SERVICES, B.8).

`hi` = the last committed version now; change rows with `since < version <= hi`
(or after the cursor), ordered by (commit_version, change_seq), a page at a
time; the commit-log rows of the versions seen; the current rows of the master
IDs. `next_watermark`: `hi` when the page ran short (every commit up to `hi`
is read), else the last version whose rows all came back, with the cursor at
the last row read. The hub keeps no watermark of a consumer (P1).
"""

from __future__ import annotations

import mdm.capacity as capacity
from mdm.backend.store import SqlStore
from mdm.models.changes import FeedPage


class FeedReader:
    def __init__(self, store: SqlStore) -> None:
        self.store = store

    def read(
        self,
        since: int,
        *,
        cursor: tuple[int, int] | None = None,
        entity: str | None = None,
        max_rows: int = capacity.FEED_PAGE_ROWS,
    ) -> FeedPage:
        max_rows = capacity.require_limit(max_rows, capacity.FEED_PAGE_ROWS * 10)
        since = max(int(since), 0)
        hi = self.store.last_commit_version()
        changes = self.store.changes_page(since, cursor, hi, entity, max_rows)
        versions = sorted({c.commit_version for c in changes})
        commits = tuple(self.store.commits_by_version(versions)) if versions else ()
        by_entity: dict[str, list[str]] = {}
        for change in changes:
            by_entity.setdefault(change.entity, []).append(change.master_id)
        rows = {
            name: self.store.current_rows(name, sorted(set(ids))) for name, ids in sorted(by_entity.items())
        }
        if len(changes) < max_rows:
            return FeedPage(commits, tuple(changes), rows, max(hi, since), None)
        last = changes[-1]
        return FeedPage(
            commits,
            tuple(changes),
            rows,
            max(since, last.commit_version - 1),
            (last.commit_version, last.change_seq),
        )
