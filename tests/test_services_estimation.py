"""Weight estimation from the store: a draft rule set, the same on both engines (owner: SERVICES, B.9.5)."""

from __future__ import annotations

from itertools import combinations

import pytest

import mdm.capacity as capacity
from mdm.models.errors import EstimationError
from mdm.services import estimation as estimation_module
from tests.conftest import ENGINES, open_hub
from tests.helpers import land, mini_world, partition

WORLD = mini_world(persons=90, organisations=12)


def _intake_only(hub) -> None:
    """The world's states and blocking keys, with nothing settled (no golden record yet)."""
    land(hub, WORLD.rows)
    hub.arrival.intake(hub.store.landing_above(0, 10_000))


def _f1(hub, entity: str) -> float:
    found = {frozenset(p) for members in partition(hub, entity).values() for p in combinations(members, 2)}
    by_truth: dict[str, list] = {}
    for (e, source), key in WORLD.truth.items():
        if e == entity:
            by_truth.setdefault(key, []).append(source)
    true = {frozenset(p) for members in by_truth.values() for p in combinations(sorted(members), 2)}
    if not found and not true:
        return 1.0
    hit = len(found & true)
    precision = hit / len(found) if found else 1.0
    recall = hit / len(true) if true else 1.0
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def test_estimation_drafts_a_rule_set(hub) -> None:
    _intake_only(hub)
    version, rules = hub.estimation.estimate("person", actor=hub.actor, method="em")
    assert version == 2 and rules.version == 2
    assert [v for v, s, _, _ in hub.store.rule_set_versions("person", "match")] == [1, 2]
    assert all(abs(sum(c.m) - 1) < 1e-6 and abs(sum(c.u) - 1) < 1e-6 for c in rules.comparisons)
    assert 0 < rules.prior <= 0.5
    meta = rules.estimation
    assert meta["method"] == "em" and meta["seeds"] > 0 and meta["records"] == meta["seeds"]
    assert set(meta["passes"]) == {p.name for p in rules.blocking}
    assert all(p["converged"] for p in meta["passes"].values())
    assert any(p["pairs"] > 0 and 0 < p["in_block"] < 1 for p in meta["passes"].values())
    assert hub.store.last_commit_version() == 0  # estimation publishes nothing


def test_a_pass_that_does_not_converge_is_refused_and_nothing_is_saved(hub, monkeypatch) -> None:
    _intake_only(hub)
    original = estimation_module.em
    monkeypatch.setattr(estimation_module, "em", lambda *a, **k: original(*a, **{**k, "max_iter": 1}))
    with pytest.raises(EstimationError) as raised:
        hub.estimation.estimate("person", actor=hub.actor, method="em")
    assert raised.value.code == "em_not_converged" and raised.value.fields["iterations"] == 1
    assert [v for v, _, _, _ in hub.store.rule_set_versions("person", "match")] == [1]


def test_the_estimated_weights_are_not_worse_than_the_starter_weights(hub, make_store, engine) -> None:
    store = make_store(engine)
    starter = open_hub(store.settings, store, engine)
    _intake_only(starter)
    starter.arrival.settle()
    _intake_only(hub)
    version, _ = hub.estimation.estimate("person", actor=hub.actor, method="em")
    hub.registry.publish_rules("person", "match", version, hub.actor)
    assert hub.registry.published("person").match.version == version
    hub.arrival.settle()
    assert _f1(hub, "person") >= _f1(starter, "person") - 0.02


@pytest.mark.skipif(not {"duckdb", "postgres"} <= set(ENGINES), reason="needs both engines")
def test_the_same_numbers_on_both_engines(make_store) -> None:
    drafts = []
    for engine in ("duckdb", "postgres"):
        store = make_store(engine)
        hub = open_hub(store.settings, store, engine)
        _intake_only(hub)
        drafts.append(hub.estimation.estimate("person", actor=hub.actor, method="auto")[1])
    assert drafts[0] == drafts[1]


def test_a_capped_pass_keeps_a_sample_by_hash_that_stands_for_every_pair(hub, monkeypatch) -> None:
    """Over EM_MAX_PAIRS, a pass keeps pairs by hash, not the lowest source keys, and each kept pair's weight
    makes the kept union stand for the whole union, so the prior is not biased low."""
    _intake_only(hub)
    model = hub.registry.published("person")
    seeds, _ = hub.estimation._by_hash("person", 10_000)
    full, _, unweighted = hub.estimation._pairs(model, seeds)
    assert unweighted == {}
    name = max(full, key=lambda n: len(full[n]))
    cap = len(full[name]) // 2
    assert cap > 20
    monkeypatch.setattr(capacity, "EM_MAX_PAIRS", cap)
    capped, _, weights = hub.estimation._pairs(model, seeds)
    assert len(capped[name]) <= cap and weights and all(w > 1 for w in weights.values())
    lowest = sorted((p.left, p.right) for p in full[name])[:cap]
    assert [(p.left, p.right) for p in capped[name]] != lowest
    every = {(p.left, p.right) for levels in full.values() for p in levels}
    kept = {(p.left, p.right) for levels in capped.values() for p in levels}
    estimate = sum(weights.get(pair, 1.0) for pair in kept)
    assert abs(estimate - len(every)) / len(every) < 0.25
