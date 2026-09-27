"""The same input gives the same golden records on DuckDB and on Postgres (owner: SERVICES, B.17)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from mdm.models.records import SourceKey
from tests.conftest import ENGINES, open_hub
from tests.helpers import (
    T0,
    all_changes,
    arrive,
    golden_rows,
    hr_key,
    land,
    mini_world,
    open_tasks,
    partition,
    person_payload,
    person_ref,
    row,
)

pytestmark = pytest.mark.skipif(
    not {"duckdb", "postgres"} <= set(ENGINES), reason="parity needs both engines in the run"
)


def _snapshot(hub) -> dict:
    """What must be identical on both engines: timestamps, change-set IDs and vault IDs left out."""
    golden = {
        entity: {
            m: (
                g.status,
                g.survivor_id,
                {k: str(v) if isinstance(v, Decimal) else v for k, v in g.values.items()},
                g.commit_version,
                g.row_version,
                g.initial_load,
            )
            for m, g in golden_rows(hub, entity).items()
        }
        for entity in ("organisation", "person")
    }
    pairs = {}
    for entity in ("organisation", "person"):
        for pair in hub.store.candidate_pairs(entity, None, 10_000):
            key = (entity, pair["left_system"], pair["left_key"], pair["right_system"], pair["right_key"])
            pairs[key] = (float(pair["score"]), pair["band"], pair["levels"], pair["signature"])
    changes = [
        (c.commit_version, c.change_seq, c.entity, c.master_id, c.change_kind, c.survivor_id, c.parts)
        for c in all_changes(hub)
    ]
    commits = [
        (c.commit_version, c.actor_role, c.authority_ref, dict(c.counts), c.row_count, c.change_count)
        for c in hub.store.commits_by_version([c[0] for c in changes])
    ]
    tasks = sorted((t.task_key, t.kind, t.reason, t.master_ids, t.task_id) for t in open_tasks(hub))
    return {
        "xref": {e: partition(hub, e) for e in ("organisation", "person")},
        "golden": golden,
        "pairs": pairs,
        "changes": changes,
        "commits": commits,
        "tasks": tasks,
        "relationships": sorted(
            (r.rel_id, r.from_master_id, r.to_master_id, r.status, r.valid_from, r.valid_to)
            for m in golden["person"]
            for r in hub.store.relationships_of([m], 100)
        ),
    }


def _fingerprints(hub) -> list[str]:
    ids = [
        c.change_set_id
        for c in hub.store.commits_by_version(list(range(1, hub.store.last_commit_version() + 1)))
    ]
    rows = hub.store.change_sets(ids)
    return [rows[i]["fingerprint"] for i in ids]


def test_the_same_world_gives_identical_golden_records_on_both_engines(make_store) -> None:
    world = mini_world(persons=16, organisations=5)
    update = row(
        "hr",
        hr_key(2),
        "person",
        person_payload(2, person_ref=person_ref(1002), city="Norvale"),
        at=T0.replace(day=9),
        version=2,
    )
    delete = row("crm", "C100004", "person", {}, op="delete", at=T0.replace(day=9))
    hubs = []
    for engine in ("duckdb", "postgres"):
        store = make_store(engine)
        hub = open_hub(store.settings, store, engine)
        land(hub, world.rows)
        arrive(hub)
        land(hub, [update, delete])
        arrive(hub)
        hubs.append(hub)
    duck, pg = (_snapshot(h) for h in hubs)
    for part in duck:
        assert duck[part] == pg[part], part
    assert _fingerprints(hubs[0]) == _fingerprints(hubs[1])
    assert partition(hubs[0], "person")  # not vacuous
    assert SourceKey("hr", hr_key(0)) in {s for ms in duck["xref"]["person"].values() for s in ms}
