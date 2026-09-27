"""Pairwise precision, recall and F1 of the cross-references against the demo truth (owner: CLI, B.14).

Every source record the world still holds (its last event is not a delete) is
one item. The hub's partition puts two items together when their active
cross-references name the same master ID; an item with no active
cross-reference (held, in review, rejected) stands alone. The truth puts two
items together when they describe the same invented person or organisation.
Precision is the share of the hub's pairs that are true; recall the share of
true pairs the hub found. The cross-references are read in keyset pages.
"""

from __future__ import annotations

from collections import Counter

from mdm import capacity
from mdm.backend.store import SqlStore
from mdm.demo.generator import DemoWorld
from mdm.models.records import SourceKey


def _pairs(sizes: Counter) -> int:
    return sum(n * (n - 1) // 2 for n in sizes.values())


def linked(store: SqlStore, entity: str) -> dict[SourceKey, str]:
    """Every active cross-reference of `entity`: source record -> master ID, read page by page."""
    out: dict[SourceKey, str] = {}
    after: SourceKey | None = None
    while True:
        page = store.xref_page(entity, after, capacity.READ_PAGE)
        out.update((row.source, row.master_id) for row in page)
        if len(page) < capacity.READ_PAGE:
            return out
        after = page[-1].source


def evaluate(store: SqlStore, entity: str, world: DemoWorld) -> dict[str, float]:
    """{"precision", "recall", "f1", "pairs"} (pairs: the hub's pairs), with "true_pairs",
    "true_positives", "records" and "linked" beside them; paged reads only."""
    gone = world.deleted()
    items = [s for s in world.sources(entity) if (entity, s) not in gone]
    masters = linked(store, entity)
    hub: Counter = Counter()
    truth: Counter = Counter()
    both: Counter = Counter()
    for source in items:
        master = masters.get(source, f"alone:{source.text()}")
        true_key = world.truth[(entity, source)]
        hub[master] += 1
        truth[true_key] += 1
        both[(master, true_key)] += 1
    pairs, true_pairs, true_positives = _pairs(hub), _pairs(truth), _pairs(both)
    precision = true_positives / pairs if pairs else 1.0
    recall = true_positives / true_pairs if true_pairs else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "pairs": pairs,
        "true_pairs": true_pairs,
        "true_positives": true_positives,
        "records": len(items),
        "linked": sum(1 for s in items if s in masters),
    }
