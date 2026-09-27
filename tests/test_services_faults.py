"""A crash anywhere leaves the work queued; the next run reaches what an uninterrupted run reaches (owner: SERVICES)."""

from __future__ import annotations

import threading
from collections import Counter

import pytest

import mdm.capacity as capacity
from mdm.models.records import SourceKey
from tests.conftest import ENGINES, open_hub
from tests.test_services_fixtures import (
    arrive,
    clusters,
    land,
    mini_world,
    open_tasks,
    partition,
)

WORLD = mini_world(persons=14, organisations=4)


class Crash(RuntimeError):
    pass


def _fresh_hub(make_store, engine):
    store = make_store(engine)
    return open_hub(store.settings, store, engine)


def _outcome(hub) -> tuple:
    """What a run decides, without the IDs a run allocates: clusters, tasks by kind and subject, relationships."""
    cluster_of: dict[str, frozenset[SourceKey]] = {}
    found: dict[str, set[frozenset[SourceKey]]] = {}
    for entity in ("organisation", "person"):
        groups = partition(hub, entity)
        cluster_of.update({m: frozenset(s) for m, s in groups.items()})
        found[entity] = clusters(hub, entity)
    tasks = Counter(
        (
            t.kind,
            t.source.text()
            if t.source
            else tuple(sorted(str(sorted(cluster_of.get(m, ()))) for m in t.master_ids)),
        )
        for t in open_tasks(hub)
    )
    relationships = set()
    for master in [m for m in cluster_of if m.startswith("PER-")]:
        for rel in hub.store.relationships_of([master], 100):
            relationships.add(
                (
                    rel.rel_type,
                    rel.origin.text() if rel.origin else None,
                    rel.status,
                    cluster_of.get(rel.to_master_id),
                )
            )
    return found["organisation"], found["person"], tasks, relationships


@pytest.mark.parametrize("point", ["after_versions", "after_intake", "in_commit", "after_chunk"])
def test_a_crash_leaves_the_work_queued_and_the_next_run_finishes_it(
    point, engine, make_store, monkeypatch
) -> None:
    monkeypatch.setattr(capacity, "COMMIT_CHUNK_ROWS", 12)
    baseline = _fresh_hub(make_store, engine)
    land(baseline, WORLD.rows)
    arrive(baseline)
    expected = _outcome(baseline)
    assert baseline.store.queue_size() == 0

    hub = _fresh_hub(make_store, engine)
    land(hub, WORLD.rows)
    fired: list[str] = []

    def fault(at: str) -> None:
        if at == point and not fired:
            fired.append(at)
            raise Crash(at)

    hub.arrival.fault = fault
    with pytest.raises(Crash):
        arrive(hub)
    assert fired == [point]
    hub.arrival.fault = None
    arrive(hub)
    assert hub.store.queue_size() == 0
    assert _outcome(hub) == expected


def test_a_forbidden_commit_leaves_its_records_queued(hub) -> None:
    from mdm.models.errors import Forbidden

    land(hub, WORLD.rows)
    original = hub.authority.check

    def unpublished(cs, *, in_transaction=False):
        if in_transaction and cs.entity == "person":
            raise Forbidden("rule_version_not_published", entity=cs.entity)
        return original(cs, in_transaction=in_transaction)

    hub.authority.check = unpublished
    with pytest.raises(Forbidden):
        arrive(hub)
    queued = hub.store.queue_page("person", None, 1000)
    assert len(queued) == sum(1 for r in WORLD.rows if r.entity == "person")
    hub.authority.check = original
    arrive(hub)
    assert hub.store.queue_size() == 0


@pytest.mark.skipif("postgres" not in ENGINES, reason="the engines of this run leave Postgres out")
@pytest.mark.parametrize("engine", ["postgres"], indirect=True)
def test_two_planners_at_once_the_second_conflicts_and_links_to_the_first(hub, make_store, engine) -> None:
    """With the lease bypassed, the expected-master check turns the second commit into a conflict; its re-plan
    links to the first golden record, and no golden record is left without members."""
    land(hub, WORLD.rows)
    rows = hub.store.landing_above(0, 1000)
    hub.arrival.intake(rows)
    errors: list[BaseException] = []
    gate = threading.Barrier(2)

    def settle() -> None:
        try:
            gate.wait()
            hub.arrival.settle()
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=settle) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors, errors
    assert hub.store.queue_size() == 0
    for entity in ("organisation", "person"):
        members = hub.store.member_counts(entity, list(partition(hub, entity)))
        golden = hub.store.golden_page(entity, None, 1000)
        assert all(members.get(g.master_id, 0) > 0 for g in golden)  # no golden record without members
    # the same partition as one planner alone
    alone = _fresh_hub(make_store, engine)
    land(alone, WORLD.rows)
    arrive(alone)
    assert clusters(hub, "person") == clusters(alone, "person")
    assert clusters(hub, "organisation") == clusters(alone, "organisation")
