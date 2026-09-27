"""The commit transaction (owner: SERVICES, B.7)."""

from __future__ import annotations

import threading
from collections import Counter

import pytest

from mdm.models.authority import AUTOMATED_MATCHER, Actor, Authority
from mdm.models.changes import (
    CreateGolden,
    LinkSource,
    MergeGolden,
    UpdateGolden,
    WorkWrites,
    new_change_set,
)
from mdm.models.errors import Conflict, Forbidden
from mdm.models.records import SourceKey
from mdm.models.tasks import Task, task_id, task_key
from mdm.services.commit import RateLimiter
from tests.test_services_fixtures import (
    T0,
    all_changes,
    arrive,
    crm_person_key,
    golden_rows,
    hr_key,
    land,
    master_of,
    mini_world,
    person_payload,
    person_ref,
    row,
)

STEWARD = Actor("steward-one", "person", "data_steward")


def _create(hub, n: int, clause: str = "hr.new=auto", **values):
    model = hub.registry.published("person")
    items = [CreateGolden(f"new:t:{n}", {"given_name": "Liora", **values}, {}, (), clause)]
    return new_change_set(
        "person",
        "arrival",
        AUTOMATED_MATCHER,
        hub.authority.automated_authority(model, [clause]),
        items,
        planning_version=hub.store.last_commit_version(),
    )


def test_versions_are_one_two_three(hub) -> None:
    results = [hub.commit.apply(_create(hub, n)) for n in range(3)]
    assert [r.commit_version for r in results] == [1, 2, 3]
    assert [next(iter(r.created.values())) for r in results] == ["PER-000001", "PER-000002", "PER-000003"]
    assert [c.change_count for c in hub.store.commits_by_version([1, 2, 3])] == [1, 1, 1]


def test_a_no_op_uses_no_version_but_still_applies_its_work(hub) -> None:
    created = hub.commit.apply(_create(hub, 1))
    master = created.created["new:t:1"]
    row_now = golden_rows(hub, "person")[master]
    model = hub.registry.published("person")
    same = UpdateGolden(master, row_now.row_version, dict(row_now.values), {}, "hr.update=auto")
    cs = new_change_set(
        "person",
        "arrival",
        AUTOMATED_MATCHER,
        hub.authority.automated_authority(model, [same.clause]),
        [same],
        planning_version=1,
    )
    key = task_key("review", "person", SourceKey("crm", "C1"))
    task = Task(
        task_id(key, "e1"),
        key,
        "person",
        "review",
        "open",
        SourceKey("crm", "C1"),
        (),
        "review_band",
        {},
        {},
        "e1",
        T0,
        T0,
    )
    result = hub.commit.apply(cs, WorkWrites("person", tasks=(task,)))
    assert result.commit_version is None
    assert hub.store.last_commit_version() == 1
    assert [t.task_key for t in hub.store.tasks("person", "review", "open", 10, None)] == [key]


def test_a_stale_row_version_rolls_everything_back(hub) -> None:
    master = hub.commit.apply(_create(hub, 1)).created["new:t:1"]
    model = hub.registry.published("person")
    items = [
        CreateGolden("new:t:2", {"given_name": "Oskar"}, {}, (), "hr.new=auto"),
        UpdateGolden(master, 7, {"given_name": "Quilla"}, {}, "hr.update=auto"),
    ]
    cs = new_change_set(
        "person",
        "arrival",
        AUTOMATED_MATCHER,
        hub.authority.automated_authority(model, ["hr.new=auto", "hr.update=auto"]),
        items,
        planning_version=1,
    )
    with pytest.raises(Conflict) as raised:
        hub.commit.apply(cs)
    assert raised.value.keys == (master,)
    assert hub.store.last_commit_version() == 1
    assert list(golden_rows(hub, "person")) == [master]


def test_a_stale_expected_master_id_is_a_conflict(hub) -> None:
    land(hub, [row("crm", crm_person_key(1), "person", person_payload(1))])
    arrive(hub)
    source = SourceKey("crm", crm_person_key(1))
    model = hub.registry.published("person")
    item = LinkSource(source, "new:t:9", None, "rule1:auto_band")
    create = CreateGolden("new:t:9", {"given_name": "Oskar"}, {}, (source,), "crm.new=auto")
    cs = new_change_set(
        "person",
        "arrival",
        AUTOMATED_MATCHER,
        hub.authority.automated_authority(model, ["crm.new=auto", "rule1:auto_band"]),
        [create, item],
        planning_version=1,
    )
    with pytest.raises(Conflict) as raised:
        hub.commit.apply(cs)
    assert raised.value.keys == (source.text(),)


def test_the_authority_is_checked(hub) -> None:
    land(
        hub,
        [
            row("hr", hr_key(i), "person", person_payload(i, person_ref=person_ref(1000 + i)), version=1)
            for i in (1, 2)
        ],
    )
    arrive(hub)
    x, y = master_of(hub, "person", "hr", hr_key(1)), master_of(hub, "person", "hr", hr_key(2))
    rows = golden_rows(hub, "person")
    model = hub.registry.published("person")
    merge = MergeGolden(x, y, (rows[x].row_version, rows[y].row_version), dict(rows[x].values), {})
    # an automated merge is never allowed
    automated = new_change_set(
        "person",
        "merge",
        AUTOMATED_MATCHER,
        hub.authority.automated_authority(model, []),
        [merge],
        planning_version=1,
    )
    with pytest.raises(Forbidden):
        hub.commit.apply(automated)
    # a merge without a checker, or with the maker as checker
    role = Authority("role", "data_steward")
    for checker in (None, STEWARD):
        cs = new_change_set("person", "merge", STEWARD, role, [merge], planning_version=1, checker=checker)
        with pytest.raises(Forbidden):
            hub.commit.apply(cs)
    # an automated item without a clause, and one whose clause the model does not hold
    for clause in ("", "crm.critical_update=auto"):
        cs = new_change_set(
            "person",
            "arrival",
            AUTOMATED_MATCHER,
            hub.authority.automated_authority(model, [clause]),
            [CreateGolden("new:t:1", {}, {}, (), clause)],
            planning_version=1,
        )
        with pytest.raises(Forbidden):
            hub.commit.apply(cs)
    # a consumer may not link
    consumer = Actor("reader", "person", "consumer")
    cs = new_change_set(
        "person",
        "link",
        consumer,
        Authority("role", "consumer"),
        [LinkSource(SourceKey("hr", hr_key(1)), y, x)],
        planning_version=1,
    )
    with pytest.raises(Forbidden):
        hub.commit.apply(cs)
    assert hub.store.last_commit_version() == 1


def test_the_commit_log_names_a_role_and_the_audit_the_person(hub) -> None:
    land(hub, [row("hr", hr_key(1), "person", person_payload(1, person_ref=person_ref(1001)), version=1)])
    land(hub, [row("crm", crm_person_key(1), "person", person_payload(1))])
    arrive(hub)
    commit = hub.store.commits_by_version([1])[0]
    assert commit.actor_kind == "automated" and commit.actor_role == "automated-matcher"
    assert "match v1" in commit.authority_ref and "hr.new=auto" in commit.authority_ref
    result = hub.lifecycle.detach(
        "person", SourceKey("crm", crm_person_key(1)), actor=STEWARD, reason="not them"
    )
    logged = hub.store.commits_by_version([result.commit_version])[0]
    assert logged.actor_role == "data_steward" and STEWARD.name not in str(logged)
    audit = hub.store.change_sets([result.change_set_id])[result.change_set_id]
    assert audit["actor"] == STEWARD.name and audit["commit_version"] == result.commit_version
    assert audit["reason"] == "not them"


def test_the_audit_holds_vault_references_and_reuses_the_members_value_ids(hub) -> None:
    payload = person_payload(1, person_ref=person_ref(1001))
    land(hub, [row("hr", hr_key(1), "person", payload, version=1)])
    arrive(hub)
    master = master_of(hub, "person", "hr", hr_key(1))
    land(
        hub,
        [
            row(
                "hr",
                hr_key(1),
                "person",
                {**payload, "given_name": "Ysolde"},
                at=T0.replace(day=6),
                version=2,
            )
        ],
    )
    report = arrive(hub)
    log = hub.store.change_log(report.last_version, None, 100)
    golden = next(r for r in log if r["table_name"] == "person")
    assert golden["op"] == "update" and golden["clause"] == "hr.update=auto"
    before, after = golden["before"]["values"]["given_name"], golden["after"]["values"]["given_name"]
    assert set(before) == {"$vault"} and set(after) == {"$vault"} and before != after
    state = hub.store.source_states("person", [SourceKey("hr", hr_key(1))])[SourceKey("hr", hr_key(1))]
    assert after["$vault"] == state.value_ids["given_name"]  # no second vault copy
    assert hub.store.vault_get([after["$vault"]]) == {after["$vault"]: "Ysolde"}
    provenance = [r for r in log if r["table_name"] == "provenance"]
    assert [r["row_key"] for r in provenance] == [master]
    assert "Ysolde" not in str(log)


def test_chunking_keeps_a_create_with_its_links(hub) -> None:
    world = mini_world(persons=8, organisations=0)
    land(hub, world.rows)
    from mdm.services import arrival as arrival_module

    original = arrival_module.capacity.COMMIT_CHUNK_ROWS
    arrival_module.capacity.COMMIT_CHUNK_ROWS = 3
    try:
        report = arrive(hub)
    finally:
        arrival_module.capacity.COMMIT_CHUNK_ROWS = original
    assert report.commits > 2
    changes = all_changes(hub)
    by_version: dict[int, set[str]] = {}
    for c in changes:
        by_version.setdefault(c.commit_version, set()).add(c.change_kind)
    # every golden record was created in the commit that also linked its members
    for master, members in {m: ms for m, ms in _members(hub).items()}.items():
        created = next(
            c.commit_version for c in changes if c.master_id == master and c.change_kind == "created"
        )
        assert all(x.commit_version == created for x in hub.store.xref_rows("person", list(members)).values())


def _members(hub):
    from tests.test_services_fixtures import partition

    return partition(hub, "person")


def test_the_rate_limiter_spaces_bulk_chunks(fake_clock) -> None:
    limiter = RateLimiter(3600, sleep=fake_clock.sleep, clock=fake_clock.monotonic)
    assert limiter.wait(10) == 0.0
    assert limiter.wait(10) == pytest.approx(10.0)
    assert limiter.wait(5) == pytest.approx(10.0)
    assert RateLimiter(0).wait(10**6) == 0.0


def test_concurrent_commits_get_gap_free_versions_and_readers_see_a_prefix(hub) -> None:
    # 8 threads x 20 commits on Postgres; DuckDB serialises every commit, so a smaller run proves the same
    threads, per_thread = (8, 20) if hub.store.engine == "postgres" else (4, 10)
    errors: list[BaseException] = []
    seen: list[tuple[int, int]] = []
    done = threading.Event()

    def writer(n: int) -> None:
        try:
            for i in range(per_thread):
                hub.commit.apply(_create(hub, n * 1000 + i))
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    def reader() -> None:
        try:
            while not done.is_set():
                hi = hub.store.last_commit_version()
                versions = {c.commit_version for c in hub.store.changes_page(0, None, hi, None, 10_000)}
                logged = [c.commit_version for c in hub.store.commits_by_version(list(range(1, hi + 1)))]
                if versions != set(range(1, hi + 1)) or logged != list(range(1, hi + 1)):
                    errors.append(AssertionError(f"not a prefix at {hi}"))
                    return
                seen.append((hi, len(versions)))
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    watcher = threading.Thread(target=reader)
    watcher.start()
    workers = [threading.Thread(target=writer, args=(n,)) for n in range(threads)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    done.set()
    watcher.join()
    assert not errors, errors[:3]
    total = threads * per_thread
    assert hub.store.last_commit_version() == total
    versions = [c.commit_version for c in all_changes(hub, page_rows=50)]
    assert versions == list(range(1, total + 1))
    assert len(golden_rows(hub, "person")) == total
    assert Counter(m[:4] for m in golden_rows(hub, "person")) == {"PER-": total}
    assert seen


def test_the_fast_fingerprint_is_the_documented_one(hub) -> None:
    from datetime import date

    from mdm.models.changes import EndRelationship, UpsertRelationship, change_set_fingerprint
    from mdm.services.support import fingerprint

    model = hub.registry.published("person")
    items = [
        CreateGolden(
            "new:hr:H1",
            {"given_name": "Liora", "birth_date": "1980-01-02"},
            {
                "city": {
                    "winner": {"source": "hr:H1", "value": "Norvale"},
                    "runners_up": [],
                    "strategy": ["source_trust"],
                    "rule_version": 1,
                }
            },
            (SourceKey("hr", "H1"),),
            "hr.new=auto",
        ),
        LinkSource(SourceKey("hr", "H1"), "new:hr:H1", None, "hr.new=auto"),
        UpsertRelationship(
            "REL-1",
            "works_at",
            "new:hr:H1",
            "organisation",
            "ORG-000001",
            date(2026, 1, 5),
            {},
            SourceKey("hr", "H1"),
            "employer",
            "hr.new=auto",
        ),
        EndRelationship("REL-0", date(2026, 1, 5), "hr.update=auto"),
    ]
    authority = hub.authority.automated_authority(model, ["hr.new=auto", "hr.update=auto"])
    for actor in (AUTOMATED_MATCHER, STEWARD):
        assert fingerprint("person", "arrival", actor, authority, items, 7) == change_set_fingerprint(
            "person", "arrival", actor, authority, items, 7
        )
