"""The store's API on both engines: round trips, keyed and paged reads, work, core and audit writes (B.5.5)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from mdm import capacity
from mdm.backend import guard
from mdm.backend.store import SqlStore, encode
from mdm.models.authority import AUTOMATED_MATCHER, Actor, Authority
from mdm.models.changes import ChangeRow, CommitLogRow, CreateGolden, WorkWrites, new_change_set
from mdm.models.entity_model import EntityModel
from mdm.models.errors import Conflict, MdmError, NotFound
from mdm.models.match import Band, Contribution, Explanation, PairScore
from mdm.models.records import (
    Gap,
    GoldenRow,
    LandingRow,
    RegisteredId,
    Reject,
    RelationshipRow,
    RetiredRow,
    RuleResult,
    SourceKey,
    SourceState,
    StewardValue,
    XrefRow,
)
from mdm.models.tasks import Task, task_id, task_key

T0 = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
OWNER = Actor("persona:data_owner", "person", "data_owner", persona=True)


def state(
    key: str,
    *,
    system: str = "hr",
    entity: str = "person",
    event: str | None = None,
    values: Mapping[str, Any] | None = None,
    sample_hash: int = 7,
    status: str = "active",
    updated_at: datetime = T0,
) -> SourceState:
    return SourceState(
        entity=entity,
        source=SourceKey(system, key),
        status=status,
        values=dict(values or {"given_name": "Given01", "birth_date": "1990-04-01"}),
        match={"given_name": "given01", "given_name.metaphone": "JFN"},
        ids=(RegisteredId("PERSON_REF", "79927398713", True),),
        references={"employer": "F0001"},
        value_ids={"given_name": "pv_x"},
        sample_hash=sample_hash,
        source_version=3,
        occurred_at=T0,
        landing_seq=11,
        event_id=event or f"ev-{system}-{key}",
        initial_load=False,
        held=False,
        approved_values=None,
        approved_event_id=None,
        rules_checked=4,
        rules_failed=1,
        updated_at=updated_at,
    )


def explanation(score: float, version: int = 1) -> Explanation:
    return Explanation(
        prior=0.001,
        contributions=(
            Contribution("given_name", 0, "exact", 7.3),
            Contribution("birth_date", -1, "null", 0.0),
        ),
        hard_rule=None,
        weight=7.3,
        score=score,
        band=Band.REVIEW,
        counterfactuals=(),
        signature="given_name=0",
        rule_version=version,
    )


def task(key_source: SourceKey, event: str, *, kind: str = "review", reason: str = "review_band") -> Task:
    key = task_key(kind, "person", key_source)
    return Task(
        task_id=task_id(key, event),
        task_key=key,
        entity="person",
        kind=kind,
        status="open",
        source=key_source,
        master_ids=("PER-000001",),
        reason=reason,
        suggestion={"link": "PER-000001"},
        evidence={"score": 71.5},
        event_id=event,
        created_at=T0,
        updated_at=T0,
    )


def rich_model(person_model: EntityModel) -> EntityModel:
    doc = person_model.to_dict()
    doc["attributes"] = [
        *doc["attributes"],
        {"name": "score", "type": "number"},
        {"name": "visits", "type": "integer"},
        {"name": "active", "type": "boolean", "standardise": "none"},
        {"name": "seen_at", "type": "timestamp", "standardise": "none"},
        {"name": "extra", "type": "json", "standardise": "none"},
    ]
    return EntityModel.from_dict(doc)


def gold(master_id: str, values: Mapping[str, Any], *, version: int = 1, row_version: int = 1) -> GoldenRow:
    return GoldenRow("person", master_id, "active", None, dict(values), version, row_version, False)


# ------------------------------------------------------------------------------------------ model group


def test_models_and_rule_sets_round_trip(store: SqlStore) -> None:
    doc = {"entity": "person", "attributes": [{"name": "given_name"}], "nested": {"b": 1, "a": [1, 2]}}
    store.save_entity_model("person", 1, "draft", doc, "tester")
    store.save_entity_model("person", 2, "draft", {**doc, "version": 2}, "tester")
    with pytest.raises(Conflict):
        store.save_entity_model("person", 1, "draft", doc, "tester")
    assert store.entity_model_doc("person") is None
    assert store.entity_model_doc("person", 1) == (1, doc)
    store.set_entity_model_status("person", 1, "published", "owner")
    assert store.entity_model_doc("person") == (1, doc)
    assert store.published_entities() == ["person"]
    store.set_entity_model_status("person", 1, "retired", "owner")
    store.set_entity_model_status("person", 2, "published", "owner")
    assert store.entity_model_doc("person")[0] == 2
    assert [(v, s) for v, s, _ in store.entity_model_versions("person")] == [(1, "retired"), (2, "published")]
    with pytest.raises(NotFound):
        store.set_entity_model_status("person", 9, "published", "owner")
    assert store.model_entities() == ["person"]

    assert store.next_rule_set_version("person", "match") == 1
    store.save_rule_set("person", "match", 1, "published", {"bands": {"upper": 90}}, 2, "owner")
    store.save_rule_set("person", "match", 2, "draft", {"bands": {"upper": 85}}, 2, "owner")
    assert store.next_rule_set_version("person", "match") == 3
    assert store.rule_set_doc("person", "match") == (1, {"bands": {"upper": 90}})
    assert store.rule_set_doc("person", "match", 2) == (2, {"bands": {"upper": 85}})
    assert store.rule_set_doc("person", "survivorship") is None
    store.set_rule_set_status("person", "match", 2, "published", "owner")
    store.set_rule_set_status("person", "match", 1, "retired", "owner")
    assert store.rule_set_doc("person", "match")[0] == 2
    assert [v[:3] for v in store.rule_set_versions("person", "match")] == [
        (1, "retired", 2),
        (2, "published", 2),
    ]


def test_code_lists_round_trip(store: SqlStore) -> None:
    assert store.code_list("country") is None
    assert (
        store.save_code_list(
            "country", "models/codelists/country.yaml", [("ZZ", "Zedland"), ("XA", None)], "o"
        )
        == 1
    )
    assert (
        store.save_code_list("country", "file", [("ZZ", "Zedland"), ("XB", "B"), ("XB", "again")], "o") == 2
    )
    assert store.code_list("country") == (2, frozenset({"ZZ", "XB"}))
    assert store.code_list_names() == [("country", 2)]


# ------------------------------------------------------------------------------------------ work group


def test_source_states_round_trip_and_write_changed_rows_only(store: SqlStore) -> None:
    a, b = state("H1"), state("H2", sample_hash=-(2**62))
    assert store.put_source_states([a, b]) == 2
    read = store.source_states("person", [a.source, b.source, SourceKey("hr", "none")])
    assert read[a.source] == a
    assert read[b.source].sample_hash == -(2**62)
    assert read[a.source].ids == (RegisteredId("PERSON_REF", "79927398713", True),)
    # the same states later: updated_at alone is no change
    later = T0 + timedelta(hours=1)
    assert (
        store.put_source_states(
            [state("H1", updated_at=later), state("H2", sample_hash=-(2**62), updated_at=later)]
        )
        == 0
    )
    assert store.source_states("person", [a.source])[a.source].updated_at == T0
    changed = state("H1", event="ev-2", updated_at=later)
    assert store.put_source_states([changed, state("H2", sample_hash=-(2**62), updated_at=later)]) == 1
    assert store.source_states("person", [a.source])[a.source].event_id == "ev-2"
    # what the caller already read stands in for a second read
    reads: list[str] = []
    remove = store.add_listener(reads.append)
    known = store.source_states("person", [a.source])
    reads.clear()
    assert store.put_source_states([changed], stored=known) == 0
    assert store.put_source_states([state("H1", event="ev-3")], stored=known) == 1
    assert store.put_source_states([state("H9")], stored={SourceKey("hr", "H9"): None}) == 1
    assert not any(r.startswith("/*mdm:keyed*/ SELECT") for r in reads)
    remove()
    # a record given twice keeps its last state
    store.put_source_states([state("H3", event="first"), state("H3", event="second")])
    assert store.source_states("person", [SourceKey("hr", "H3")])[SourceKey("hr", "H3")].event_id == "second"


def test_keyed_reads_equal_a_plain_filter_and_pages_are_ordered(store: SqlStore) -> None:
    states = [state(f"K{i:03d}", system=("crm" if i % 3 else "hr"), sample_hash=i % 5) for i in range(40)]
    store.put_source_states(states)
    store.put_source_states([state("D1", status="deleted")])
    wanted = [s.source for s in states[::4]]
    keyed = store.source_states("person", wanted)
    plain = {s.source: s for s in store.states_page("person", None, None, 1000) if s.source in set(wanted)}
    assert keyed == plain
    pages = list(
        capacity.pages(
            lambda after, n: store.states_page("person", None, after, n), lambda s: s.source, page=7
        )
    )
    flat = [s.source for page in pages for s in page]
    assert flat == sorted(flat) and len(flat) == 41
    by_hash = list(
        capacity.pages(
            lambda after, n: store.states_by_sample_hash("person", after, n),
            lambda s: (s.sample_hash, s.source.system, s.source.key),
            page=6,
        )
    )
    order = [(s.sample_hash, s.source.system, s.source.key) for page in by_hash for s in page]
    assert order == sorted(order) and len(order) == 40  # the deleted one is left out
    only_hr = store.states_page("person", "hr", None, 100)
    assert {s.source.system for s in only_hr} == {"hr"}


def test_keyed_reads_across_chunks(store: SqlStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capacity, "KEY_CHUNK", 3)
    states = [state(f"C{i:02d}") for i in range(10)]
    store.put_source_states(states)
    read = store.source_states("person", [s.source for s in reversed(states)])
    assert set(read) == {s.source for s in states}
    rows = store._select_keyed(
        store_table("work", "source_state"),
        ("source_system", "source_key"),
        ("entity", "source_system", "source_key"),
        [("person", "hr", f"C{i:02d}") for i in (9, 1, 5, 3, 7)],
        order_by=("source_system", "source_key"),
    )
    assert rows == sorted(rows)


def store_table(group: str, name: str) -> Any:
    from mdm.backend import ddl

    return ddl.table(group, name)


def test_blocking_keys_change_by_difference(store: SqlStore, monkeypatch: pytest.MonkeyPatch) -> None:
    a, b = SourceKey("hr", "H1"), SourceKey("crm", "C1")
    store.replace_blocking_keys(
        "person", {a: {"email": ["x@example.org"], "names": ["JFN|SM"]}, b: {"names": ["JFN|SM"]}}
    )
    deleted: list[Any] = []
    inserted: list[Any] = []
    real_delete, real_insert = store._delete_keyed, store._insert

    def spy_delete(table: Any, cols: Any, keys: Any) -> int:
        deleted.extend(keys)
        return real_delete(table, cols, keys)

    def spy_insert(table: Any, rows: Any, **kw: Any) -> int:
        inserted.extend(rows)
        return real_insert(table, rows, **kw)

    monkeypatch.setattr(store, "_delete_keyed", spy_delete)
    monkeypatch.setattr(store, "_insert", spy_insert)
    store.replace_blocking_keys("person", {a: {"email": ["y@example.org"], "names": ["JFN|SM"]}})
    assert deleted == [("person", "email", "x@example.org", "hr", "H1")]
    assert [(r["pass_name"], r["key_value"]) for r in inserted] == [("email", "y@example.org")]
    assert store.blocking_keys_of("person", [a, b]) == {
        a: {"email": ["y@example.org"], "names": ["JFN|SM"]},
        b: {"names": ["JFN|SM"]},
    }
    assert store.key_counts(
        "person", [("names", "JFN|SM"), ("email", "y@example.org"), ("email", "none")]
    ) == {
        ("names", "JFN|SM"): 2,
        ("email", "y@example.org"): 1,
    }


def test_candidate_retrieval_is_ordered_and_bounded(store: SqlStore) -> None:
    keys: dict[SourceKey, dict[str, list[str]]] = {}
    for i in range(12):
        keys[SourceKey("crm" if i % 2 else "hr", f"R{i:02d}")] = {
            "names": ["K1" if i < 8 else "K2"],
            "email": [f"e{i % 3}"],
        }
    store.replace_blocking_keys("person", keys)
    found = store.records_with_keys("person", [("names", "K2"), ("email", "e1"), ("names", "K1")], 100)
    assert found == sorted(found, key=lambda r: (r[0], r[1], r[2]))
    assert len(found) == 4 + 4 + 8
    assert store.records_with_keys("person", [("names", "K1")], 5) == found[4:9]


def test_the_arrival_queue(store: SqlStore) -> None:
    a, b, c = SourceKey("hr", "H1"), SourceKey("crm", "C1"), SourceKey("hr", "H2")
    store.queue_put(
        [("person", a, "e1", 5), ("person", b, "e2", 3), ("person", c, "e3", 3), ("person", a, "e4", 9)]
    )
    page = store.queue_page("person", None, 10)
    assert page == [(b, "e2", 3), (c, "e3", 3), (a, "e4", 9)]
    assert store.queue_page("person", (3, "crm", "C1"), 10) == page[1:]
    assert store.queue_entities() == []  # entities are found through their models
    store.save_entity_model("person", 1, "draft", {}, "o")
    store.save_entity_model("organisation", 1, "draft", {}, "o")
    assert store.queue_entities() == ["person"]
    assert store.queue_size() == 3
    # settle only while the row still names the event planned
    store.apply_work(WorkWrites("person", settle=((a, "e1"), (b, "e2"))))
    assert store.queue_page("person", None, 10) == [(c, "e3", 3), (a, "e4", 9)]


def test_apply_work_approves_holds_and_releases(store: SqlStore) -> None:
    a, b = state("H1", event="e1"), state("H2", event="e2")
    store.put_source_states([a, b])
    store.apply_work(WorkWrites("person", approve=((a.source, "e1"), (b.source, "stale")), hold=(b.source,)))
    read = store.source_states("person", [a.source, b.source])
    assert read[a.source].approved_values == a.values and read[a.source].approved_event_id == "e1"
    assert read[b.source].approved_values is None and read[b.source].held is True
    store.apply_work(WorkWrites("person", release=(b.source,)))
    assert store.source_states("person", [b.source])[b.source].held is False


def test_tasks_upsert_through_open_task(store: SqlStore) -> None:
    source = SourceKey("crm", "C1")
    first = task(source, "e1")
    store.apply_work(WorkWrites("person", tasks=(first,)))
    second = _replace(task(source, "e2"), evidence={"score": 80.0})
    store.apply_work(WorkWrites("person", tasks=(second,)))
    listed = store.tasks("person", "review", "open", 10, None)
    assert len(listed) == 1
    assert listed[0].task_id == first.task_id  # the open task was updated, not added
    assert listed[0].event_id == "e2" and listed[0].evidence == {"score": 80.0}
    other = task(SourceKey("crm", "C2"), "e3", kind="held", reason="critical_update_held")
    store.apply_work(WorkWrites("person", tasks=(other,)))
    assert store.task_counts() == {("person", "held"): 1, ("person", "review"): 1}
    assert [t.task_id for t in store.tasks(None, None, "open", 10, None)] == sorted(
        [first.task_id, other.task_id]
    )
    assert store.tasks_by_key([first.task_key])[first.task_key].event_id == "e2"
    closed = _replace(listed[0], status="closed", updated_at=T0 + timedelta(days=1))
    store.apply_work(WorkWrites("person", tasks=(closed,)))
    assert store.tasks("person", "review", "open", 10, None) == []
    assert store.tasks("person", "review", "closed", 10, None)[0].task_id == first.task_id
    reopened = task(source, "e5")
    store.apply_work(WorkWrites("person", tasks=(reopened,)))
    assert store.tasks("person", "review", "open", 10, None)[0].task_id == reopened.task_id


def _replace(obj: Any, **changes: Any) -> Any:
    import dataclasses

    return dataclasses.replace(obj, **changes)


def test_candidate_pairs_are_stored_left_below_right(store: SqlStore) -> None:
    a, b = SourceKey("hr", "H1"), SourceKey("crm", "C1")
    store.apply_work(WorkWrites("person", pairs=(PairScore(a, b, explanation(71.25)),)))
    store.apply_work(WorkWrites("person", pairs=(PairScore(b, a, explanation(72.5)),)))
    (pair,) = store.candidate_pairs("person", None, 10)
    assert (pair["left_system"], pair["left_key"], pair["right_system"], pair["right_key"]) == (
        "crm",
        "C1",
        "hr",
        "H1",
    )
    assert pair["score"] == Decimal("72.5")
    assert pair["levels"] == {"given_name": 0, "birth_date": -1}
    assert Explanation.from_dict(pair["explanation"]) == explanation(72.5)
    assert store.pairs_of("person", [a]) == [pair]


def test_rule_failures_are_replaced(store: SqlStore) -> None:
    source = SourceKey("hr", "H1")
    results = [
        RuleResult("PER-V1", "family_name", "completeness", False, "missing", "warn"),
        RuleResult("PER-V6", "country", "validity", False, "not_in_code_list", "warn"),
        RuleResult("PER-V2", "given_name", "completeness", True, "ok", "warn"),
    ]
    store.put_rule_failures("person", source, results, "e1", {"PER-V6": 2})
    failures = store.rule_failures("person", [source])[source]
    assert [(f["rule_id"], f["code"], f["code_list_version"]) for f in failures] == [
        ("PER-V1", "missing", None),
        ("PER-V6", "not_in_code_list", 2),
    ]
    store.put_rule_failures("person", source, results[2:], "e2", {})
    assert store.rule_failures("person", [source]) == {}


def test_rejects(store: SqlStore) -> None:
    store.put_rejects(
        [
            Reject("ev2", 2, "person", SourceKey("hr", "H1"), "bad_type", ("birth_date",)),
            Reject("ev1", 1, None, None, "unknown_entity"),
        ]
    )
    rejects = store.rejects(10)
    assert [r.event_id for r in rejects] == ["ev1", "ev2"]
    assert rejects[1].attributes == ("birth_date",) and rejects[1].source == SourceKey("hr", "H1")
    assert store.rejects(10, after="ev1")[0].event_id == "ev2"
    store.mark_rejects_replayed(["ev1"])
    assert store.reject_count() == 1 and store.reject_count(replayed=True) == 1
    assert [r.event_id for r in store.rejects(10, replayed=True)] == ["ev1"]
    store.put_rejects([Reject("ev1", 1, None, None, "unknown_source")])
    assert store.reject_count() == 2


def test_reader_position_and_gaps(store: SqlStore) -> None:
    assert store.reader_state("arrival").high_water == 0
    first = T0
    with store.transaction():
        store.save_reader(
            "arrival", 100, 40, upsert=[Gap(41, 45, "open", first, None), Gap(60, 60, "open", first, None)]
        )
    assert store.reader_state("arrival").reconciled_at is None
    store.save_reader(
        "arrival", 120, 40, upsert=[Gap(41, 42, "lost", first, first)], delete=[60], reconciled_at=T0
    )
    store.save_reader("arrival", 130, 40)  # reconciled_at None keeps the stored one
    position = store.reader_state("arrival")
    assert (position.high_water, position.low_water, position.reconciled_at) == (130, 40, T0)
    assert store.gaps("arrival", "open", None, 10) == []
    assert store.gaps("arrival", "lost", None, 10) == [Gap(41, 42, "lost", first, first)]
    assert store.gaps("arrival", "lost", 41, 10) == []
    assert store.gap_counts("arrival") == {"lost": 1}


def test_pending_references_and_jobs(store: SqlStore) -> None:
    a = SourceKey("hr", "H1")
    target = SourceKey("finance", "F9")
    store.put_pending_references([("person", a, "employer", "organisation", target)])
    store.put_pending_references([("person", SourceKey("hr", "H2"), "employer", "organisation", target)])
    pending = store.pending_references("person", None, 10)
    assert pending == [
        ("person", a, "employer", "organisation", target),
        ("person", SourceKey("hr", "H2"), "employer", "organisation", target),
    ]
    assert store.pending_references("person", ("hr", "H1", "employer"), 10) == pending[1:]
    assert store.pending_references_to("organisation", [target]) == pending
    store.drop_pending_references([("person", a, "employer")])
    assert store.pending_references("person", None, 10) == pending[1:]

    run = store.start_job("arrival", "automated-matcher")
    store.heartbeat(run, {"read": 5})
    store.finish_job(run, "failed", "conflict")
    (job,) = store.jobs(5)
    assert (job["run_id"], job["status"], job["progress"], job["error_code"]) == (
        run,
        "failed",
        {"read": 5},
        "conflict",
    )
    assert job["finished_at"] is not None


# ------------------------------------------------------------------------------------------ hub, vault, audit


def test_source_versions_are_kept_once(store: SqlStore) -> None:
    from mdm.models.records import SourceChange

    change = SourceChange("ev1", "hr", "H1", "person", "upsert", T0, 3, False, {}, T0, 11)
    payload = {"given_name": {"$vault": "pv_1"}, "city": "Northtown"}
    assert store.put_source_versions([(change, payload), (change, payload)]) == 1
    assert store.put_source_versions([(change, payload)]) == 0
    assert store.seen_events(["ev1", "ev2"]) == {"ev1"}
    (kept,) = store.source_versions("person", SourceKey("hr", "H1"), 10)
    assert kept["payload"] == payload and kept["landing_seq"] == 11


def test_vault_reuses_equal_live_values_and_redacts(store: SqlStore) -> None:
    ids = store.vault_put(
        [
            ("person", "src:hr:H1", "given_name", "Given01"),
            ("person", "src:hr:H1", "birth_date", date(1990, 4, 1)),
            ("person", "src:hr:H1", "given_name", "Given01"),
            ("person", "src:crm:C1", "given_name", "Given01"),
        ]
    )
    assert ids[0] == ids[2] and ids[0] != ids[3] and len(set(ids)) == 3
    assert all(i.startswith("pv_") and len(i) == 27 for i in ids)
    again = store.vault_put(
        [("person", "src:hr:H1", "given_name", "Given01"), ("person", "src:hr:H1", "given_name", "Given02")]
    )
    assert again[0] == ids[0] and again[1] not in ids
    assert store.vault_get([ids[0], ids[1]]) == {ids[0]: "Given01", ids[1]: "1990-04-01"}
    emptied = store.vault_redact(["src:hr:H1"], redaction_id="RD-1")
    assert emptied == sorted({ids[0], ids[1], again[1]})
    assert store.vault_get(emptied) == {i: None for i in emptied}
    assert store.vault_get([ids[3]]) == {ids[3]: "Given01"}
    fresh = store.vault_put([("person", "src:hr:H1", "given_name", "Given01")])
    assert fresh[0] not in emptied  # a redacted value is never reused
    assert store.vault_redact(["src:hr:H1"]) == fresh


def test_audit_is_appended(store: SqlStore) -> None:
    cs = new_change_set(
        "person",
        "arrival",
        AUTOMATED_MATCHER,
        Authority("rule_version", "person: model v1; clauses hr.new=auto"),
        [CreateGolden("g1", {}, {}, (SourceKey("hr", "H1"),), "hr.new=auto")],
        planning_version=0,
        evidence={"clauses": {"hr.new=auto": 1}},
    )
    store.append_change_set(cs, 7, 1)
    row = store.change_sets([cs.change_set_id])[cs.change_set_id]
    assert (row["fingerprint"], row["commit_version"], row["actor"], row["evidence"]) == (
        cs.fingerprint,
        7,
        "automated-matcher",
        {"clauses": {"hr.new=auto": 1}},
    )
    store.append_change_log(
        [
            (
                f"{cs.change_set_id}:1",
                7,
                cs.change_set_id,
                "person",
                "PER-000001",
                "insert",
                "hr.new=auto",
                None,
                {"a": 1},
            ),
            (
                f"{cs.change_set_id}:2",
                7,
                cs.change_set_id,
                "xref",
                "hr:H1",
                "insert",
                "hr.new=auto",
                None,
                None,
            ),
        ]
    )
    log = store.change_log(7, None, 10)
    assert [(r["row_key"], r["after"]) for r in log] == [("PER-000001", {"a": 1}), ("hr:H1", None)]
    access = store.append_access(
        OWNER, "reveal", "person", "PER-000001", "given_name", "checking a duplicate", {"n": 1}
    )
    checker = Actor("admin-1", "person", "administrator")
    redaction = store.append_redaction(["src:hr:H1"], ["pv_1"], OWNER, checker, "role: data_owner", "erasure")
    assert access.startswith("AC-") and redaction.startswith("RD-")
    (entry,) = store.access_log(None, 10)
    assert (entry["actor"], entry["attribute"], entry["detail"]) == (OWNER.name, "given_name", {"n": 1})


# ------------------------------------------------------------------------------------------ core


def test_values_are_identical_on_both_engines(store: SqlStore, person_model: EntityModel) -> None:
    model = rich_model(person_model)
    store.ensure_entity_tables(model)
    values = {
        "given_name": "Ünïcode Given",
        "birth_date": date(1990, 4, 1),
        "addresses": [{"kind": "home", "line1": "1 Test Road", "n": 1.5, "flag": True, "none": None}],
        "score": Decimal("12.3456789012345"),
        "visits": 2**40,
        "active": False,
        "seen_at": datetime(2026, 3, 1, 12, 30, 15, 250000, tzinfo=UTC),
        "extra": "a JSON string",
    }
    with store.commit_scope():
        store.write_golden("person", [(None, gold("PER-000001", values)), (None, gold("PER-000002", {}))])
    row = store.golden("person", ["PER-000001"])["PER-000001"]
    assert row.values["given_name"] == "Ünïcode Given"
    assert row.values["birth_date"] == date(1990, 4, 1)
    assert row.values["addresses"] == values["addresses"]
    assert row.values["score"] == Decimal("12.3456789012")  # numeric(38,10), rounded half even
    assert row.values["visits"] == 2**40
    assert row.values["active"] is False
    assert row.values["seen_at"] == values["seen_at"] and row.values["seen_at"].tzinfo == UTC
    assert row.values["extra"] == "a JSON string"
    assert row.values["phone"] is None
    empty = store.golden("person", ["PER-000002"])["PER-000002"]
    assert all(v is None for v in empty.values.values())
    rows = store._fetch_all(
        f"/*mdm:small*/ SELECT extra IS NULL, addresses IS NULL FROM {store.t('core', 'person')} WHERE master_id = ?",
        ["PER-000002"],
    )
    assert rows == [(True, True)]  # a JSON-typed NULL stays SQL NULL, never the JSON literal null


def test_encode_refuses_what_a_column_cannot_hold() -> None:
    assert encode(1.5, "numeric") == "1.5000000000"
    assert encode(Decimal("0.00000000005"), "numeric") == "0E-10"
    assert encode(datetime(2026, 1, 1, 23, 30, tzinfo=UTC), "date") == "2026-01-01"
    assert encode("2026-01-02T03:04:05+00:00", "timestamptz") == "2026-01-02T03:04:05Z"
    assert encode(None, "json") is None
    assert encode({"b": 1, "a": 2}, "json") == '{"a":2,"b":1}'
    with pytest.raises(ValueError):
        encode(float("nan"), "numeric")
    with pytest.raises(TypeError):
        encode("yes", "boolean")
    with pytest.raises(TypeError):
        encode({"a": 1}, "text")


def test_golden_updates_write_changed_columns_and_check_row_versions(
    store: SqlStore, person_model: EntityModel
) -> None:
    store.ensure_entity_tables(person_model)
    first = gold("PER-000001", {"given_name": "Given01", "city": "Northtown"})
    with store.commit_scope():
        assert store.write_golden("person", [(None, first)]) == 1
    before = store.golden("person", ["PER-000001"])["PER-000001"]
    assert before.row_version == 1 and before.commit_version == 1
    statements: list[str] = []
    remove = store.add_listener(statements.append)
    after = GoldenRow(
        "person", "PER-000001", "active", None, {**before.values, "city": "Southtown"}, 2, 2, False
    )
    with store.commit_scope():
        assert store.write_golden("person", [(before, after)]) == 1
    remove()
    update = next(s for s in statements if "UPDATE" in s)
    assert "city = " in update and "given_name" not in update
    now = store.golden("person", ["PER-000001"])["PER-000001"]
    assert (now.values["city"], now.row_version, now.commit_version) == ("Southtown", 2, 2)
    stale = GoldenRow(
        "person", "PER-000001", "active", None, {**before.values, "city": "Westtown"}, 3, 2, False
    )
    with pytest.raises(Conflict) as refused, store.commit_scope():
        store.write_golden("person", [(before, stale)])
    assert refused.value.keys == ("PER-000001",)
    assert store.golden("person", ["PER-000001"])["PER-000001"].values["city"] == "Southtown"
    with pytest.raises(MdmError, match="unknown_column"), store.commit_scope():
        store.write_golden("person", [(None, gold("PER-000009", {"employer": "F1"}))])
    with pytest.raises(NotFound):
        store.golden("nothing", ["X"])
    page = store.golden_page("person", None, 10)
    assert [g.master_id for g in page] == ["PER-000001"]
    assert store.golden_page("person", "PER-000001", 10) == []


def test_versions_and_ids_under_the_commit_scope(store: SqlStore, person_model: EntityModel) -> None:
    store.ensure_entity_tables(person_model)
    with store.commit_scope():
        assert store.next_commit_version() == 1
        assert store.allocate_ids("person", "PER", 3) == ["PER-000001", "PER-000002", "PER-000003"]
        assert store.allocate_ids("person", "PER", 0) == []
        store.write_commit_log(commit_row(1))
    with store.commit_scope():
        assert store.next_commit_version() == 2
        assert store.allocate_ids("person", "PER", 1) == ["PER-000004"]
        assert store.allocate_ids("organisation", "ORG", 2) == ["ORG-000001", "ORG-000002"]
    assert store.last_commit_version() == 1
    with pytest.raises(MdmError, match="nested_commit_scope"), store.transaction(), store.commit_scope():
        pass


def commit_row(version: int, **changes: Any) -> CommitLogRow:
    base = dict(
        commit_version=version,
        committed_at=T0,
        change_set_id=f"CS-{version}",
        actor_kind="automated",
        actor_role="automated-matcher",
        authority_kind="rule_version",
        authority_ref="person: model v1; clauses hr.new=auto",
        initial_load=False,
        counts={"created": 1},
        row_count=2,
        change_count=1,
    )
    base.update(changes)
    return CommitLogRow(**base)


def test_cross_references_and_members(store: SqlStore) -> None:
    rows = [
        XrefRow("person", SourceKey("hr", f"H{i}"), "PER-000001" if i < 3 else "PER-000002", "active", 1)
        for i in range(5)
    ]
    with store.commit_scope():
        store.write_xrefs(rows)
        store.write_xrefs([XrefRow("person", SourceKey("hr", "H4"), "PER-000002", "detached", 2)])
    got = store.xrefs_for_sources("person", [SourceKey("hr", f"H{i}") for i in range(6)])
    assert got == {SourceKey("hr", f"H{i}"): ("PER-000001" if i < 3 else "PER-000002") for i in range(4)}
    assert store.xref_rows("person", [SourceKey("hr", "H4")])[SourceKey("hr", "H4")].status == "detached"
    members = store.members("person", ["PER-000001", "PER-000002", "PER-000003"], 2)
    assert members == {
        "PER-000001": [SourceKey("hr", "H0"), SourceKey("hr", "H1")],
        "PER-000002": [SourceKey("hr", "H3")],
    }
    assert store.member_counts("person", ["PER-000001", "PER-000002"]) == {"PER-000001": 3, "PER-000002": 1}
    page = store.xref_page("person", SourceKey("hr", "H1"), 10)
    assert [x.source.key for x in page] == ["H2", "H3"]


def test_retired_ids_and_merge_members(store: SqlStore) -> None:
    with store.commit_scope():
        store.write_retired(
            [
                RetiredRow("PER-000003", "person", "PER-000002", 5, "PER-000001", True, 5),
                RetiredRow("PER-000002", "person", "PER-000001", 6, "PER-000001", True, 6),
            ]
        )
        store.write_retired([RetiredRow("PER-000004", "person", "PER-000001", 6, "PER-000001", False, 6)])
    store.write_merge_members("person", "PER-000002", 6, [SourceKey("hr", "H2"), SourceKey("crm", "C2")])
    store.write_merge_members("person", "PER-000002", 6, [SourceKey("hr", "H2")])
    assert store.resolve_retired(["PER-000003", "PER-000004", "PER-000009"]) == {"PER-000003": "PER-000001"}
    assert store.retired_rows(["PER-000004"])["PER-000004"].active is False
    assert [r.retired_id for r in store.retired_through("PER-000001", 10)] == ["PER-000002", "PER-000003"]
    assert [r.retired_id for r in store.merged_into("PER-000002", 10)] == ["PER-000003"]
    assert store.merge_members("PER-000002", 6, 10) == [SourceKey("crm", "C2"), SourceKey("hr", "H2")]
    with store.commit_scope():  # a re-merge replaces the row
        store.write_retired([RetiredRow("PER-000003", "person", "PER-000005", 9, "PER-000005", True, 9)])
    assert store.retired_rows(["PER-000003"])["PER-000003"].merged_into == "PER-000005"


def relationship(
    rel_id: str, frm: str, to: str, origin: SourceKey | None = None, status: str = "active"
) -> RelationshipRow:
    return RelationshipRow(
        rel_id,
        "works_at",
        "person",
        frm,
        "organisation",
        to,
        date(2024, 1, 1),
        None,
        status,
        {"role": "staff"},
        origin,
        "employer" if origin else None,
        1,
        1,
    )


def test_relationships_repoint_at_both_ends(store: SqlStore) -> None:
    rows = [
        relationship("R1", "PER-000001", "ORG-000001", SourceKey("hr", "H1")),
        relationship("R2", "PER-000002", "ORG-000001", SourceKey("hr", "H2")),
        relationship("R3", "PER-000003", "ORG-000002", SourceKey("crm", "C3")),
        relationship("R4", "PER-000004", "ORG-000001", SourceKey("hr", "H4"), status="ended"),
    ]
    with store.commit_scope():
        assert store.write_relationships(rows) == 4
    assert [r.rel_id for r in store.relationships_of(["ORG-000001"], 10)] == ["R1", "R2", "R4"]
    assert [r.rel_id for r in store.relationships_of(["ORG-000001", "PER-000003"], 2)] == ["R1", "R2"]
    by_origin = store.relationships_by_origin(
        [(SourceKey("hr", "H1"), "employer"), (SourceKey("hr", "H4"), "employer")]
    )
    assert [r.rel_id for r in by_origin] == ["R1"]  # active only
    with store.commit_scope():
        others = store.repoint_relationships("ORG-000001", "ORG-000002", 7)
    assert others == ["PER-000001", "PER-000002"]
    moved = store.relationships(["R1", "R2", "R4"])
    assert (
        moved["R1"].to_master_id == "ORG-000002"
        and moved["R1"].row_version == 2
        and moved["R1"].commit_version == 7
    )
    assert moved["R4"].to_master_id == "ORG-000001"  # an ended relationship keeps its ends
    with store.commit_scope():
        back = store.repoint_relationships("ORG-000002", "ORG-000001", 8, origins=[SourceKey("hr", "H2")])
    assert back == ["PER-000002"]
    after = store.relationships(["R1", "R2", "R3"])
    assert (after["R1"].to_master_id, after["R2"].to_master_id, after["R3"].to_master_id) == (
        "ORG-000002",
        "ORG-000001",
        "ORG-000002",
    )


def test_changes_and_the_commit_log(store: SqlStore) -> None:
    with store.commit_scope():
        store.write_changes(
            [ChangeRow(1, 1, "person", "PER-000001", "created", None, ("values", "xref"))]
            + [
                ChangeRow(
                    2, i, "organisation" if i == 2 else "person", f"PER-{i:06d}", "updated", None, ("values",)
                )
                for i in (1, 2, 3)
            ]
        )
        store.write_commit_log(commit_row(1))
        store.write_commit_log(commit_row(2, change_count=3, counts={"updated": 3}))
    page = store.changes_page(0, None, 2, None, 10)
    assert [(c.commit_version, c.change_seq) for c in page] == [(1, 1), (2, 1), (2, 2), (2, 3)]
    assert page[0].parts == ("values", "xref")
    assert [(c.commit_version, c.change_seq) for c in store.changes_page(0, (2, 1), 2, None, 10)] == [
        (2, 2),
        (2, 3),
    ]
    assert [(c.commit_version, c.change_seq) for c in store.changes_page(1, None, 2, "person", 10)] == [
        (2, 1),
        (2, 3),
    ]
    assert store.changes_page(0, None, 1, None, 10)[-1].commit_version == 1
    assert len(store.changes_page(0, None, 2, None, 2)) == 2
    logs = store.commits_by_version([2, 1, 3])
    assert [c.commit_version for c in logs] == [1, 2]
    assert logs[1] == commit_row(2, change_count=3, counts={"updated": 3})


def test_provenance_and_steward_values(store: SqlStore) -> None:
    with store.commit_scope():
        store.write_provenance("person", {"PER-000001": {"city": {"from": "hr:H1"}}}, 1, 3)
        store.write_provenance("person", {"PER-000001": {"city": {"from": "crm:C1"}}}, 2, 4)
        store.write_steward_values(
            "person",
            {"PER-000001": {"city": StewardValue("Northtown", T0 + timedelta(days=30), "steward-1", T0)}},
            4,
        )
    assert store.provenance("person", ["PER-000001", "PER-000002"]) == {
        "PER-000001": {"city": {"from": "crm:C1"}}
    }
    values = store.steward_values("person", ["PER-000001"])
    assert values == {
        "PER-000001": {"city": StewardValue("Northtown", T0 + timedelta(days=30), "steward-1", T0)}
    }


def test_row_estimates(store: SqlStore, person_model: EntityModel) -> None:
    store.ensure_entity_tables(person_model)
    store.put_source_states([state(f"E{i}") for i in range(5)])
    if store.engine != "duckdb":
        with guard.ddl_scope():
            store._execute(f"ANALYZE {store.t('work', 'source_state')}")
    estimates = store.row_estimates()
    assert estimates[store.t("work", "source_state")] == 5
    assert estimates[store.t("core", "person")] == 0
    assert store.t("landing", "source_change") in estimates
    assert all(not name.startswith(store.t("read", "")) for name in estimates)


def test_landing_rows_need_the_simulator(store: SqlStore) -> None:
    row = LandingRow("ev1", "hr", "H1", "person", "upsert", T0, {"given_name": "Given01"}, 1, False)
    with guard.simulating_integration_platform(store.settings, store.prefix):
        assert store.landing_insert([row]) == 1
    (read,) = store.landing_above(0, 10)
    assert (read.event_id, read.payload, read.source_version, read.occurred_at) == (
        "ev1",
        {"given_name": "Given01"},
        1,
        T0,
    )
