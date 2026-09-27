"""The arrival job end to end, on every engine (owner: SERVICES, B.10 and B.17)."""

from __future__ import annotations

from collections import Counter
from datetime import timedelta

import pytest

from mdm.models.errors import Forbidden
from mdm.models.records import SourceKey
from tests.test_services_fixtures import (
    T0,
    all_changes,
    arrive,
    change,
    clusters,
    crm_person_key,
    finance_key,
    golden_rows,
    hr_key,
    land,
    master_of,
    mini_world,
    open_tasks,
    org_payload,
    partition,
    person_payload,
    person_ref,
    row,
)


def _truth_of(world, entity, members):
    return {world.truth[(entity, s)] for s in members}


def test_end_to_end_arrival_to_the_change_feed(hub) -> None:
    world = mini_world(persons=12, organisations=4)
    land(hub, world.rows)
    report = arrive(hub)

    assert report.read == len(world.rows)
    assert report.rejected == 0
    assert report.queued == len(world.rows) and report.settled == len(world.rows)
    assert report.commits >= 2 and report.first_version == 1
    for entity in ("organisation", "person"):
        groups = partition(hub, entity)
        # no golden record mixes two true entities, and every record of the trusted source is linked
        assert all(len(_truth_of(world, entity, members)) == 1 for members in groups.values())
    assert len(partition(hub, "organisation")) == 4
    assert len(partition(hub, "person")) == 12
    assert all(master_of(hub, "person", "hr", hr_key(i)) for i in range(12))
    # a person with an employer works at the organisation's golden record
    person = master_of(hub, "person", "hr", hr_key(0))
    employer = master_of(hub, "organisation", "finance", finance_key(0))
    relationships = hub.store.relationships_of([person], 10)
    assert [(r.rel_type, r.to_master_id, r.status) for r in relationships] == [
        ("works_at", employer, "active")
    ]

    changes = all_changes(hub)
    versions = [c.commit_version for c in changes]
    assert versions == sorted(versions)
    assert sorted(set(versions)) == list(range(1, report.last_version + 1))  # gap-free
    commits = hub.store.commits_by_version(sorted(set(versions)))
    per_version = Counter(versions)
    assert all(c.change_count == per_version[c.commit_version] for c in commits)
    assert all(c.actor_role == "automated-matcher" and c.authority_kind == "rule_version" for c in commits)
    created = {c.master_id for c in changes if c.change_kind == "created"}
    assert created == set(partition(hub, "organisation")) | set(partition(hub, "person"))
    # every golden row carries the version of its latest change
    rows = {**golden_rows(hub, "person"), **golden_rows(hub, "organisation")}
    latest: dict[str, int] = {}
    for c in changes:
        if set(c.parts) - {"relationship"}:
            latest[c.master_id] = c.commit_version
    assert all(rows[m].commit_version == v for m, v in latest.items())


def test_a_second_run_commits_nothing(hub) -> None:
    land(hub, mini_world(persons=6, organisations=2).rows)
    first = arrive(hub)
    second = arrive(hub)
    assert first.commits >= 1
    assert (second.read, second.commits, second.settled) == (0, 0, 0)
    assert hub.store.last_commit_version() == first.last_version
    assert hub.store.queue_size() == 0


def test_duplicate_events_have_no_double_effect(hub) -> None:
    world = mini_world(persons=4, organisations=1)
    assert land(hub, world.rows) == len(world.rows)
    assert land(hub, world.rows) == 0  # redelivery reuses the event ID: the landing table ignores it
    arrive(hub)
    before = (hub.store.last_commit_version(), partition(hub, "person"))
    # the same events read again (a replay, a re-probe) move nothing
    again = hub.arrival.process(hub.store.landing_above(0, 1000))
    assert again.versions == 0
    assert again.queued == 0 and again.commits == 0
    assert again.stale == len(world.rows)
    # even with another landing position, a known event never moves a state twice
    twice = hub.arrival.process([change(r, 900 + n) for n, r in enumerate(world.rows)])
    assert (twice.versions, twice.queued, twice.commits) == (0, 0, 0)
    assert (hub.store.last_commit_version(), partition(hub, "person")) == before


def test_an_update_recomputes_the_golden_record_with_provenance(hub) -> None:
    land(hub, mini_world(persons=3, organisations=1).rows)
    arrive(hub)
    master = master_of(hub, "person", "hr", hr_key(1))
    before = golden_rows(hub, "person")[master]
    land(
        hub,
        [
            row(
                "hr",
                hr_key(1),
                "person",
                person_payload(1, person_ref=person_ref(1001), city="Silverwick", employer=finance_key(0)),
                at=T0 + timedelta(days=1),
                version=2,
            )
        ],
    )
    report = arrive(hub)
    after = golden_rows(hub, "person")[master]
    assert report.updated == 1 and report.commits == 1
    assert after.values["city"] == "Silverwick" != before.values["city"]
    assert after.row_version == before.row_version + 1
    assert after.commit_version == report.last_version
    provenance = hub.store.provenance("person", [master])[master]
    assert provenance["city"]["winner"]["source"] == f"hr:{hr_key(1)}"
    assert provenance["given_name"]["winner"]["value"].keys() == {"$vault"}  # personal: a vault reference
    kinds = [(c.change_kind, c.parts) for c in all_changes(hub) if c.commit_version == report.last_version]
    assert kinds == [("updated", ("values",))]


def test_an_older_version_is_history_only(hub) -> None:
    land(hub, [row("hr", hr_key(1), "person", person_payload(1, person_ref=person_ref(1001)), version=5)])
    arrive(hub)
    master = master_of(hub, "person", "hr", hr_key(1))
    before = golden_rows(hub, "person")[master]
    land(
        hub,
        [
            row(
                "hr",
                hr_key(1),
                "person",
                person_payload(1, person_ref=person_ref(1001), city="Brackenmere"),
                at=T0 + timedelta(days=2),
                version=4,
            )
        ],
    )
    report = arrive(hub)
    assert report.versions == 1 and report.stale == 1 and report.queued == 0 and report.commits == 0
    assert golden_rows(hub, "person")[master] == before
    assert len(hub.store.source_versions("person", SourceKey("hr", hr_key(1)), 10)) == 2


def test_a_delete_from_a_versioned_source_needs_a_version(hub) -> None:
    land(hub, [row("hr", hr_key(1), "person", person_payload(1, person_ref=person_ref(1001)), version=1)])
    arrive(hub)
    land(hub, [row("hr", hr_key(1), "person", {}, op="delete", at=T0 + timedelta(days=1))])
    report = arrive(hub)
    assert report.rejected == 1
    [reject] = hub.store.rejects(10)
    assert reject.reason == "missing_version" and reject.attributes == ()
    assert master_of(hub, "person", "hr", hr_key(1)) is not None


def test_a_delete_from_an_unversioned_source_detaches_and_an_orphan_becomes_a_task(hub) -> None:
    land(hub, [row("crm", crm_person_key(3), "person", person_payload(3))])
    arrive(hub)
    master = master_of(hub, "person", "crm", crm_person_key(3))
    land(hub, [row("crm", crm_person_key(3), "person", {}, op="delete", at=T0 + timedelta(days=1))])
    report = arrive(hub)
    assert report.detached == 1 and report.tasks == {"orphan": 1}
    assert master_of(hub, "person", "crm", crm_person_key(3)) is None
    [task] = open_tasks(hub, "person", "orphan")
    assert task.master_ids == (master,) and task.source is None
    assert golden_rows(hub, "person")[master].status == "active"


def test_a_held_critical_update_changes_nothing_until_decided(hub) -> None:
    payload = person_payload(4, person_ref=person_ref(1004))
    land(hub, [row("hr", hr_key(4), "person", payload, version=1)])
    land(hub, [row("crm", crm_person_key(4), "person", person_payload(4), at=T0 + timedelta(minutes=1))])
    arrive(hub)
    master = master_of(hub, "person", "hr", hr_key(4))
    assert master_of(hub, "person", "crm", crm_person_key(4)) == master
    before = golden_rows(hub, "person")[master]
    # crm holds critical updates: a new family name (and a new e-mail) wait for a person
    held = person_payload(4, family_name="Wrenfield", email="new.address4@example.org")
    land(hub, [row("crm", crm_person_key(4), "person", held, at=T0 + timedelta(days=1))])
    report = arrive(hub)
    assert report.tasks == {"held": 1} and report.commits == 0
    after = golden_rows(hub, "person")[master]
    assert after.values == before.values and after.commit_version == before.commit_version
    [task] = open_tasks(hub, "person", "held")
    assert task.reason == "critical_update_held" and task.source == SourceKey("crm", crm_person_key(4))
    state = hub.store.source_states("person", [SourceKey("crm", crm_person_key(4))])[
        SourceKey("crm", crm_person_key(4))
    ]
    assert state.held and state.approved_values["email"] == person_payload(4)["email"]
    # another member updates: the golden record is recomputed with the held member's approved values
    land(
        hub,
        [
            row(
                "hr",
                hr_key(4),
                "person",
                {**payload, "city": "Easthollow"},
                at=T0 + timedelta(days=2),
                version=2,
            )
        ],
    )
    arrive(hub)
    latest = golden_rows(hub, "person")[master]
    assert latest.values["city"] == "Easthollow"
    assert latest.values["email"] == person_payload(4)["email"]
    assert latest.values["family_name"] == before.values["family_name"]


def test_bad_rows_are_rejected_with_reasons_and_names_only_then_replayed(hub) -> None:
    land(
        hub,
        [
            row("hr", hr_key(1), "vehicle", {"plate": "XX 111"}, version=1),
            row("crm", crm_person_key(1), "person", {"given_name": "Liora", "shoe_size": 41}),
            row("crm", crm_person_key(2), "person", {"given_name": ["not", "text"]}),
            row("ledger", "L1", "person", {"given_name": "Liora"}),
        ],
    )
    report = arrive(hub)
    assert report.rejected == 4 and report.commits == 0
    reasons = {(r.reason, r.attributes) for r in hub.store.rejects(10)}
    assert reasons == {
        ("unknown_entity", ()),
        ("bad_payload", ("shoe_size",)),
        ("bad_type", ("given_name",)),
        ("unknown_source", ()),
    }
    # nothing but names reached the reject table
    assert not any("Liora" in str(r) or "XX 111" in str(r) for r in hub.store.rejects(10))
    replay = hub.arrival.replay_rejects(started_by=hub.actor)
    assert replay.rejected == 4  # still wrong: rejected again, not marked replayed
    assert len(hub.store.rejects(10)) == 4


def test_references_become_relationships_late_ones_resolve_and_a_change_ends_the_old(hub) -> None:
    # the person arrives before the organisation it works at
    land(
        hub,
        [
            row(
                "hr",
                hr_key(1),
                "person",
                person_payload(1, person_ref=person_ref(1001), employer=finance_key(0)),
                version=1,
            )
        ],
    )
    arrive(hub)
    person = master_of(hub, "person", "hr", hr_key(1))
    assert hub.store.relationships_of([person], 10) == []
    assert len(hub.store.pending_references("person", None, 10)) == 1
    land(hub, [row("finance", finance_key(0), "organisation", org_payload(0), version=1)])
    land(hub, [row("finance", finance_key(1), "organisation", org_payload(1), version=1)])
    arrive(hub)
    first = master_of(hub, "organisation", "finance", finance_key(0))
    second = master_of(hub, "organisation", "finance", finance_key(1))
    [rel] = hub.store.relationships_of([person], 10)
    assert (rel.to_master_id, rel.status, rel.origin_attribute) == (first, "active", "employer")
    assert hub.store.pending_references("person", None, 10) == []
    # the employer changes: the old relationship ends and a new one starts
    land(
        hub,
        [
            row(
                "hr",
                hr_key(1),
                "person",
                person_payload(1, person_ref=person_ref(1001), employer=finance_key(1)),
                at=T0 + timedelta(days=3),
                version=2,
            )
        ],
    )
    arrive(hub)
    found = {r.to_master_id: r for r in hub.store.relationships_of([person], 10)}
    assert found[first].status == "ended" and found[first].valid_to is not None
    assert found[second].status == "active"


def test_bulk_mode_flags_initial_loads_and_writes_one_summary_audit_row_per_commit(hub) -> None:
    world = mini_world(persons=6, organisations=2, initial=True)
    land(hub, world.rows)
    report = arrive(hub, bulk=True)
    commits = hub.store.commits_by_version(list(range(1, report.last_version + 1)))
    assert commits and all(c.initial_load for c in commits)
    assert all(g.initial_load for g in golden_rows(hub, "person").values())
    for commit in commits:
        rows = hub.store.change_log(commit.commit_version, None, 100)
        assert [r["op"] for r in rows] == ["summary"]


def test_the_bulk_throttle_sleeps(hub, fake_clock) -> None:
    from dataclasses import replace

    from mdm.services.arrival import ArrivalService

    settings = replace(hub.settings, throttle_rows_per_hour=3600)
    service = ArrivalService(
        settings,
        hub.store,
        hub.registry,
        hub.codelists,
        hub.authority,
        hub.vault,
        hub.commit,
        hub.matching,
        sleep=fake_clock.sleep,
        monotonic=fake_clock.monotonic,
    )
    land(hub, mini_world(persons=6, organisations=2, initial=True).rows)
    report = service.run(started_by=hub.actor, bulk=True)
    assert report.commits >= 2
    assert fake_clock.slept > 0  # the second commit waited for the first one's rows


def test_a_retired_master_id_hint_routes_to_the_survivor(hub) -> None:
    land(hub, [row("hr", hr_key(1), "person", person_payload(1, person_ref=person_ref(1001)), version=1)])
    land(hub, [row("hr", hr_key(2), "person", person_payload(2, person_ref=person_ref(1002)), version=1)])
    arrive(hub)
    keep = master_of(hub, "person", "hr", hr_key(1))
    gone = master_of(hub, "person", "hr", hr_key(2))
    from mdm.models.authority import Actor

    maker = Actor("steward-one", "person", "data_steward")
    checker = Actor("steward-two", "person", "coordinating_steward")
    hub.lifecycle.merge("person", keep, gone, maker=maker, checker=checker, reason="same person")
    land(hub, [row("crm", crm_person_key(9), "person", {**person_payload(9), "master_id": gone})])
    report = arrive(hub)
    assert report.linked == 1
    assert master_of(hub, "person", "crm", crm_person_key(9)) == keep
    last = hub.store.commits_by_version([report.last_version])[0]
    assert "rule1:retired_id" in last.authority_ref


def test_forbidden_mid_run_leaves_the_records_queued(hub) -> None:
    land(hub, mini_world(persons=2, organisations=0).rows)
    original = hub.authority.check

    def refuse(cs, *, in_transaction=False):
        if in_transaction:
            raise Forbidden("rule_version_not_published", entity=cs.entity)
        return original(cs, in_transaction=in_transaction)

    hub.authority.check = refuse
    with pytest.raises(Forbidden):
        arrive(hub)
    assert hub.store.queue_size() > 0
    assert hub.store.last_commit_version() == 0
    hub.authority.check = original
    report = arrive(hub)
    assert report.commits >= 1 and hub.store.queue_size() == 0


def test_the_lease_keeps_a_second_run_out(hub) -> None:
    with hub.store.exclusive_lease("arrival") as held:
        assert held
        report = arrive(hub)
    assert report.skipped_busy and report.read == 0


def test_identify_mode_and_the_authored_and_registry_styles(hub, tmp_path) -> None:
    import yaml

    from tests.conftest import MODELS

    doc = yaml.safe_load((MODELS / "person.yaml").read_text(encoding="utf-8"))
    variants = {
        "identified": {"match": {**doc["match"], "mode": "identify"}},
        "authored": {"style": "authored"},
        "registered": {"style": "registry"},
    }
    for name, change_ in variants.items():
        variant = {**doc, **change_, "entity": name, "code": name[:3].upper()}
        variant["attributes"] = [a for a in doc["attributes"] if a["name"] != "employer"]
        model = hub.registry.load_doc(variant, hub.actor)
        hub.registry.publish(model.entity, model.version, hub.actor)
        land(
            hub,
            [
                row("hr", hr_key(i), name, person_payload(i, person_ref=person_ref(1000 + i)), version=1)
                for i in range(3)
            ],
        )
    report = arrive(hub)
    assert partition(hub, "identified") == {} and golden_rows(hub, "identified") == {}
    assert partition(hub, "authored") == {} and Counter(t.kind for t in open_tasks(hub, "authored")) == {
        "held": 3
    }
    registered = golden_rows(hub, "registered")
    assert len(registered) == 3
    assert all(all(v is None for v in g.values.values()) for g in registered.values())
    assert report.rejected == 0


def test_the_crm_twin_of_a_person_links_and_the_student_record_follows(hub) -> None:
    world = mini_world(persons=6, organisations=0)
    land(hub, world.rows)
    arrive(hub)
    found = clusters(hub, "person")
    assert frozenset({SourceKey("hr", hr_key(0)), SourceKey("crm", crm_person_key(0))}) <= next(
        c for c in found if SourceKey("hr", hr_key(0)) in c
    )


def test_a_held_record_is_released_when_its_source_takes_the_change_back(hub) -> None:
    land(hub, [row("crm", crm_person_key(5), "person", person_payload(5))])
    arrive(hub)
    source = SourceKey("crm", crm_person_key(5))
    land(
        hub,
        [
            row(
                "crm",
                crm_person_key(5),
                "person",
                person_payload(5, family_name="Wrenfield"),
                at=T0 + timedelta(days=1),
            )
        ],
    )
    arrive(hub)
    assert hub.store.source_states("person", [source])[source].held
    assert [t.kind for t in open_tasks(hub, "person")] == ["held"]
    land(hub, [row("crm", crm_person_key(5), "person", person_payload(5), at=T0 + timedelta(days=2))])
    report = arrive(hub)
    assert report.commits == 0  # the values are the approved ones again: nothing to publish
    assert not hub.store.source_states("person", [source])[source].held
    assert open_tasks(hub, "person") == []


def test_the_small_demo_world_end_to_end(arrived_world) -> None:
    from mdm.demo import evaluate

    hub, world, report = arrived_world
    assert report.rejected == 0 and report.read == len(world.rows)
    assert hub.store.queue_size() == 0
    for entity, floor in (("organisation", 0.95), ("person", 0.9)):
        scores = evaluate(hub.store, entity, world)
        assert scores["precision"] >= floor and scores["recall"] >= floor, (entity, scores)
    versions = [c.commit_version for c in all_changes(hub)]
    assert sorted(set(versions)) == list(range(1, report.last_version + 1)) and versions == sorted(versions)
    again = arrive(hub)
    assert (again.read, again.commits) == (0, 0)
