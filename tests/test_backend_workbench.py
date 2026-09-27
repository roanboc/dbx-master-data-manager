"""The workbench's store on both engines: tasks by due time, claims, the tray, labels, reads for the record view,
and the checks a steward's commit makes inside its own transaction (owner: SERVICES, B.5, B.9.1)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from mdm.backend import ddl
from mdm.backend.store import SqlStore
from mdm.models.changes import WorkWrites
from mdm.models.errors import Conflict
from mdm.models.records import SourceKey
from mdm.models.tasks import Task
from mdm.models.workbench import MatchLabel, TaskQuery, TrayEntry, TraySettlement
from tests.helpers import arrive, land, mini_world
from tests.test_backend_store import state

T0 = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
LAPSED = T0 - timedelta(minutes=10)
NEW_TASK_COLUMNS = ("snoozed_until", "snoozed_by", "escalated_at", "escalated_by", "escalation")
NEW_TABLES = ("tray_entry", "tray_lock", "match_label")


def a_task(
    n: int,
    *,
    due: datetime | None = None,
    entity: str = "person",
    kind: str = "review",
    event: str | None = None,
    status: str = "open",
) -> Task:
    """An invented open task on crm:C<n>, due `due` (default: T0 + n hours)."""
    return Task(
        task_id=f"TSK-{n:04d}",
        task_key=f"TK-{n:04d}",
        entity=entity,
        kind=kind,
        status=status,
        source=SourceKey("crm", f"C{n:04d}"),
        master_ids=(),
        reason="review_band",
        suggestion={"action": "decide_link_or_create"},
        evidence={"score": 71.0, "band": "review"},
        event_id=event or f"ev-{n}",
        created_at=T0,
        updated_at=T0,
        due_at=due if due is not None else T0 + timedelta(hours=n),
    )


def decided_at(store: SqlStore, task_id: str) -> datetime | None:
    """The task's decision time (a column the Task dataclass does not carry)."""
    rows = store._fetch_all(
        f"/*mdm:keyed*/ SELECT decided_at FROM {store.t('work', 'task')} WHERE task_id = ?", [task_id]
    )
    return rows[0][0] if rows else None


def write(store: SqlStore, *tasks: Task, entity: str = "person") -> None:
    store.apply_work(WorkWrites(entity, tasks=tuple(tasks)))


def an_entry(
    entry_id: str, task_id: str, *, actor: str = "persona:data_steward", deadline: datetime = T0
) -> TrayEntry:
    return TrayEntry(
        entry_id=entry_id,
        task_id=task_id,
        entity="person",
        decision="link",
        target="PER-000001",
        subject={"source": "crm:C0001", "candidates": ["PER-000001"]},
        signature="given_name= · family_name=",
        actor=actor,
        actor_role="data_steward",
        persona=True,
        event_id="ev-1",
        planning_version=0,
        staged_at=T0,
        deadline=deadline,
        status="staged",
    )


def a_label(left: str, right: str, label: str, *, entry: str | None = "TR-1") -> MatchLabel:
    return MatchLabel(
        entity="person",
        left_ref=left,
        right_ref=right,
        label=label,
        rule_version=1,
        score=81.5,
        band="review",
        signature="given_name=",
        task_id="TSK-0001",
        entry_id=entry,
        decided_by="persona:data_steward",
        decided_role="data_steward",
        decided_at=T0,
    )


# ---------------------------------------------------------------------------------------------- the schema


def test_the_workbench_tables_exist_and_an_older_store_gains_them(
    store: SqlStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in NEW_TABLES:
        assert store.table_columns("work", name) == list(ddl.table("work", name).column_names())
    assert store.table_columns("work", "task")[-len(NEW_TASK_COLUMNS) :] == list(NEW_TASK_COLUMNS)

    # an initiative-2 store: the task table without the workbench's columns and indexes, no tray tables
    store.drop_all()
    task = ddl.table("work", "task")
    older = replace(
        task,
        columns=tuple(c for c in task.columns if c.name not in NEW_TASK_COLUMNS),
        indexes=(("entity", "status", "kind"), ("task_key",)),
    )
    monkeypatch.setattr(
        ddl,
        "TABLES",
        tuple(older if t is task else t for t in ddl.TABLES if t.name not in NEW_TABLES),
    )
    store.init_schema(create_landing=True)
    old_row = {
        k: v for k, v in store._task_row(replace(a_task(1), due_at=None)).items() if k not in NEW_TASK_COLUMNS
    }
    store._insert(older, [old_row])
    assert store.table_columns("work", "tray_entry") == []
    monkeypatch.undo()

    store.init_schema(create_landing=True)  # appends; drops nothing
    assert store.table_columns("work", "task") == list(task.column_names())
    for name in NEW_TABLES:
        assert store.table_columns("work", name) == list(ddl.table("work", name).column_names())
    kept = store.tasks_by_id(["TSK-0001"])["TSK-0001"]
    assert (kept.status, kept.due_at, kept.snoozed_until, kept.escalation) == ("open", None, None, None)


# ---------------------------------------------------------------------------------------------- tasks


def test_task_pages_are_keyed_by_due_time_and_task_id_and_filtered(store: SqlStore) -> None:
    write(store, *(a_task(n, due=T0 + timedelta(hours=n % 3)) for n in range(1, 8)))
    write(store, a_task(20, entity="organisation", kind="held"), entity="organisation")
    query = TaskQuery(now=T0)
    first = store.task_page(query, None, 3)
    order = [(t.due_at, t.task_id) for t in first]
    assert order == sorted(order) and len(first) == 3
    rest = store.task_page(query, (first[-1].due_at, first[-1].task_id), 100)
    everything = [t.task_id for t in first + rest]
    assert len(everything) == len(set(everything)) == 8
    assert [(t.due_at, t.task_id) for t in first + rest] == sorted(
        (t.due_at, t.task_id) for t in first + rest
    )

    assert {t.task_id for t in store.task_page(TaskQuery(now=T0, entity="organisation"), None, 10)} == {
        "TSK-0020"
    }
    assert {t.task_id for t in store.task_page(TaskQuery(now=T0, kind="held"), None, 10)} == {"TSK-0020"}
    # breaching: due before now
    late = store.task_page(TaskQuery(now=T0 + timedelta(minutes=90), breaching=True), None, 10)
    assert {t.task_id for t in late} == {"TSK-0003", "TSK-0006", "TSK-0001", "TSK-0004", "TSK-0007"}
    # mine: unclaimed, lapsed, or claimed by me
    assert store.claim_task("TSK-0001", "persona:coordinating_steward", T0, LAPSED)
    mine = TaskQuery(now=T0, mine="persona:data_steward", lapsed_before=LAPSED)
    assert "TSK-0001" not in {t.task_id for t in store.task_page(mine, None, 100)}
    later = TaskQuery(
        now=T0 + timedelta(minutes=11), mine="persona:data_steward", lapsed_before=T0 + timedelta(minutes=1)
    )
    assert "TSK-0001" in {t.task_id for t in store.task_page(later, None, 100)}
    # snoozed and escalated
    assert store.snooze_task("TSK-0002", "persona:data_steward", T0 + timedelta(hours=4))
    assert "TSK-0002" not in {t.task_id for t in store.task_page(TaskQuery(now=T0), None, 100)}
    assert {t.task_id for t in store.task_page(TaskQuery(now=T0, snoozed=True), None, 100)} == {"TSK-0002"}
    assert store.escalate_task("TSK-0005", "persona:data_steward", "second_opinion", T0)
    escalated = store.task_page(TaskQuery(now=T0, snoozed=None, escalated=True), None, 100)
    assert [(t.task_id, t.escalation, t.escalated_by) for t in escalated] == [
        ("TSK-0005", "second_opinion", "persona:data_steward")
    ]


def test_counts_stop_at_the_cap(store: SqlStore) -> None:
    cap = 10
    write(store, *(a_task(n) for n in range(1, cap + 6)))
    assert store.task_count(TaskQuery(now=T0), cap) == cap
    assert store.task_count(TaskQuery(now=T0), 100) == cap + 5
    assert store.task_count(TaskQuery(now=T0, kind="held"), cap) == 0
    for n in range(1, cap + 6):
        store.stage_tray(an_entry(f"TR-{n}", f"TSK-{n:04d}"), [f"task:TSK-{n:04d}"])
    assert store.staged_count(cap) == cap
    assert store.staged_count(100) == cap + 5


def test_claims_lapse_and_a_claim_wakes_a_snoozed_task(store: SqlStore) -> None:
    write(store, a_task(1))
    assert store.claim_task("TSK-0001", "persona:data_steward", T0, LAPSED)
    assert store.claim_task("TSK-0001", "persona:data_steward", T0, LAPSED)  # again, the same actor
    assert not store.claim_task("TSK-0001", "persona:coordinating_steward", T0, LAPSED)
    # the claim lapses: another actor takes it
    later = T0 + timedelta(minutes=11)
    assert store.claim_task("TSK-0001", "persona:coordinating_steward", later, later - timedelta(minutes=10))
    assert not store.release_task("TSK-0001", "persona:data_steward")
    assert store.release_task("TSK-0001", "persona:coordinating_steward")
    assert store.snooze_task("TSK-0001", "persona:data_steward", T0 + timedelta(hours=1))
    assert store.tasks_by_id(["TSK-0001"])["TSK-0001"].snoozed_until == T0 + timedelta(hours=1)
    assert store.claim_task("TSK-0001", "persona:data_steward", T0, LAPSED)
    woken = store.tasks_by_id(["TSK-0001"])["TSK-0001"]
    assert (woken.snoozed_until, woken.snoozed_by, woken.claimed_by) == (None, None, "persona:data_steward")
    # a snooze or an escalation refuses a task another actor holds, when asked to
    assert not store.snooze_task("TSK-0001", "persona:coordinating_steward", T0, lapsed_before=LAPSED)
    assert not store.escalate_task("TSK-0001", "persona:coordinating_steward", "second_opinion", T0, LAPSED)
    assert store.escalate_task("TSK-0001", "persona:data_steward", "outside_my_data", T0, LAPSED)
    assert store.tasks_by_id(["TSK-0001"])["TSK-0001"].claimed_by is None  # an escalation releases it


def test_a_reopened_task_starts_afresh(store: SqlStore) -> None:
    write(store, a_task(1))
    store.claim_task("TSK-0001", "persona:data_steward", T0, LAPSED)
    store.snooze_task("TSK-0001", "persona:data_steward", T0 + timedelta(hours=4))
    store.escalate_task("TSK-0001", "persona:data_steward", "second_opinion", T0)
    store.apply_work(WorkWrites("person", close_task_ids=("TSK-0001",)))
    closed = store.tasks_by_id(["TSK-0001"])["TSK-0001"]
    assert closed.status == "closed" and closed.escalation == "second_opinion"
    assert decided_at(store, "TSK-0001") is not None
    # arrival opens the same task again (same key, same event): the same task ID
    again = replace(a_task(1), created_at=T0 + timedelta(days=1), updated_at=T0 + timedelta(days=1))
    again = replace(again, due_at=T0 + timedelta(days=1, hours=8))
    write(store, again)
    fresh = store.tasks_by_id(["TSK-0001"])["TSK-0001"]
    assert fresh.status == "open" and fresh.due_at == T0 + timedelta(days=1, hours=8)
    assert (fresh.claimed_by, fresh.claimed_at, fresh.snoozed_until, fresh.snoozed_by) == (None,) * 4
    assert (fresh.escalated_at, fresh.escalated_by, fresh.escalation) == (None,) * 3
    assert fresh.created_at == T0  # the first opening's
    assert decided_at(store, "TSK-0001") is None
    assert store.task_page(TaskQuery(now=T0), None, 10)[0].task_id == "TSK-0001"


def test_open_tasks_without_a_due_time_are_given_one(store: SqlStore) -> None:
    write(store, replace(a_task(1), due_at=None), replace(a_task(2), due_at=None), a_task(3))
    missing = store.open_tasks_without_due(None, 10)
    assert [t.task_id for t in missing] == ["TSK-0001", "TSK-0002"]
    assert [t.task_id for t in store.open_tasks_without_due("TSK-0001", 10)] == ["TSK-0002"]
    assert store.set_due_times([("TSK-0001", T0), ("TSK-0002", T0), ("TSK-0003", T0)]) == 2
    assert store.open_tasks_without_due(None, 10) == []
    assert store.tasks_by_id(["TSK-0003"])["TSK-0003"].due_at == T0 + timedelta(hours=3)  # kept


def test_tasks_of_source_records(store: SqlStore) -> None:
    write(store, a_task(1), a_task(2))
    write(store, a_task(3, entity="organisation"), entity="organisation")
    found = store.tasks_for_sources("person", [SourceKey("crm", "C0001"), SourceKey("crm", "C0003")])
    assert [t.task_id for t in found] == ["TSK-0001"]


# ---------------------------------------------------------------------------------------------- the tray


def test_a_lock_held_refuses_a_second_stage_and_writes_nothing(store: SqlStore) -> None:
    write(store, a_task(1), a_task(2))
    store.stage_tray(an_entry("TR-1", "TSK-0001"), ["task:TSK-0001", "source:person:crm:C0001"])
    with pytest.raises(Conflict) as mine:
        store.stage_tray(an_entry("TR-2", "TSK-0002"), ["task:TSK-0002", "source:person:crm:C0001"])
    assert mine.value.code == "already_staged" and mine.value.fields["mine"] is True
    with pytest.raises(Conflict) as theirs:
        store.stage_tray(
            an_entry("TR-3", "TSK-0001", actor="persona:coordinating_steward"), ["task:TSK-0001"]
        )
    assert theirs.value.fields["mine"] is False
    assert set(store.tray_entries(["TR-1", "TR-2", "TR-3"])) == {"TR-1"}
    assert set(store.staged_by_locks(["task:TSK-0002", "task:TSK-0001"])) == {"task:TSK-0001"}


def test_settling_happens_once_and_frees_the_locks(store: SqlStore) -> None:
    write(store, a_task(1))
    store.stage_tray(an_entry("TR-1", "TSK-0001"), ["task:TSK-0001"])
    assert store.bump_tray_attempts("TR-1") == 1
    assert store.settle_tray("TR-1", "undone", change_set_id=None, outcome="undone", at=T0)
    assert not store.settle_tray("TR-1", "committed", change_set_id="CS-x", outcome="committed", at=T0)
    assert store.bump_tray_attempts("TR-1") == 0
    entry = store.tray_entries(["TR-1"])["TR-1"]
    assert (entry.status, entry.outcome, entry.settled_at, entry.attempts) == ("undone", "undone", T0, 1)
    assert store.staged_by_locks(["task:TSK-0001"]) == {}
    store.stage_tray(an_entry("TR-2", "TSK-0001"), ["task:TSK-0001"])  # the lock is free again
    # while a decision waits, nobody claims, snoozes or escalates the task; the tray itself claims first
    assert not store.claim_task("TSK-0001", "persona:data_steward", T0, LAPSED)
    assert not store.snooze_task("TSK-0001", "persona:data_steward", T0 + timedelta(hours=1), LAPSED)
    assert not store.escalate_task("TSK-0001", "persona:data_steward", "second_opinion", T0, LAPSED)
    # fail_tray releases the claim only when it settled the entry, and only the entry's actor's
    assert store.claim_task("TSK-0001", "persona:data_steward", T0, LAPSED, staging=True)
    assert store.fail_tray("TR-2", "record_changed", T0, "TSK-0001", "persona:data_steward")
    assert store.tasks_by_id(["TSK-0001"])["TSK-0001"].claimed_by is None
    store.stage_tray(an_entry("TR-3", "TSK-0001"), ["task:TSK-0001"])
    assert store.claim_task("TSK-0001", "persona:coordinating_steward", T0, LAPSED, staging=True)
    assert store.fail_tray("TR-3", "record_changed", T0, "TSK-0001", "persona:data_steward")
    assert store.tasks_by_id(["TSK-0001"])["TSK-0001"].claimed_by == "persona:coordinating_steward"
    store.release_task("TSK-0001", "persona:coordinating_steward")
    store.claim_task("TSK-0001", "persona:data_steward", T0, LAPSED)
    assert not store.fail_tray("TR-2", "record_changed", T0, "TSK-0001", "persona:data_steward")
    assert store.tasks_by_id(["TSK-0001"])["TSK-0001"].claimed_by == "persona:data_steward"
    assert store.tray_entries(["TR-2"])["TR-2"].outcome == "record_changed"


def test_the_tray_is_read_due_first_and_per_actor_newest_first(store: SqlStore) -> None:
    write(store, a_task(1), a_task(2), a_task(3))
    store.stage_tray(an_entry("TR-a", "TSK-0001", deadline=T0 + timedelta(seconds=60)), ["task:TSK-0001"])
    store.stage_tray(
        replace(
            an_entry("TR-b", "TSK-0002", deadline=T0 + timedelta(seconds=30)),
            staged_at=T0 + timedelta(seconds=5),
        ),
        ["task:TSK-0002"],
    )
    store.stage_tray(
        an_entry("TR-c", "TSK-0003", actor="persona:coordinating_steward", deadline=T0), ["task:TSK-0003"]
    )
    assert [e.entry_id for e in store.due_tray(T0 + timedelta(seconds=60), 10)] == ["TR-c", "TR-b", "TR-a"]
    assert [e.entry_id for e in store.due_tray(T0 + timedelta(seconds=30), 1)] == ["TR-c"]
    assert [e.entry_id for e in store.tray_of_actor("persona:data_steward", T0, 10)] == ["TR-b", "TR-a"]
    # the read walks the actor's entries staged since a day before `since`, never their whole history
    later = T0 + timedelta(days=2)
    assert store.tray_of_actor("persona:data_steward", later, 10) == []
    staged = store.tray_of_actor("persona:data_steward", later, 10, staged_since=T0 - timedelta(days=1))
    assert [e.entry_id for e in staged] == ["TR-b", "TR-a"]
    store.settle_tray("TR-a", "undone", change_set_id=None, outcome="undone", at=T0 + timedelta(seconds=10))
    assert [
        e.entry_id for e in store.tray_of_actor("persona:data_steward", T0 + timedelta(seconds=20), 10)
    ] == ["TR-b"]
    assert [e.entry_id for e in store.tray_of_actor("persona:data_steward", T0, 10)] == ["TR-b", "TR-a"]


# ---------------------------------------------------------------------------------------------- labels and the queue


def test_labels_keep_the_latest_decision_per_pair(store: SqlStore) -> None:
    store.put_labels(
        [a_label("crm:C0001", "PER-000001", "not_a_match"), a_label("crm:C0001", "PER-000002", "match")]
    )
    store.put_labels([a_label("crm:C0001", "PER-000001", "match", entry="TR-2")])
    found = store.labels_for("person", ["crm:C0001", "crm:C0009"])
    assert [(lab.right_ref, lab.label, lab.entry_id) for lab in found] == [
        ("PER-000001", "match", "TR-2"),
        ("PER-000002", "match", "TR-1"),
    ]
    assert found[0].score == 81.5 and found[0].decided_at == T0
    assert store.labels_for("organisation", ["crm:C0001"]) == []


def test_a_label_or_staged_decision_with_free_text_in_its_signature_is_refused(store: SqlStore) -> None:
    bad = replace(a_label("crm:C0001", "PER-000001", "match"), signature="given_name= · Pellan Quorane")
    with pytest.raises(ValueError, match="free text"):
        store.put_labels([bad])
    assert store.labels_for("person", ["crm:C0001"]) == []
    with pytest.raises(ValueError, match="free text"):
        store.stage_tray(replace(an_entry("TR-9", "TSK-0009"), signature="name≈ Quorane"), ["task:TSK-0009"])
    assert store.staged_by_locks(["task:TSK-0009"]) == {}


def test_queue_rows_of_records(store: SqlStore) -> None:
    a, b = SourceKey("crm", "C0001"), SourceKey("crm", "C0002")
    store.queue_put([("person", b, "ev-b", 9), ("person", a, "ev-a", 12)])
    assert store.queue_rows("person", [a, b, SourceKey("crm", "C0003")]) == [(b, "ev-b", 9), (a, "ev-a", 12)]


# ---------------------------------------------------------------------------------------------- the checks inside the commit


def _work(**changes) -> WorkWrites:
    return WorkWrites(
        "person",
        labels=(a_label("crm:C0001", "PER-000001", "not_a_match"),),
        requeue=((SourceKey("hr", "H1"), "ev-hr-H1", 11),),
        tray=(TraySettlement("TR-1", "committed", "CS-1", "committed"),),
        expect_events=((SourceKey("hr", "H1"), "ev-hr-H1"),),
        close_task_ids=("TSK-0001",),
        **changes,
    )


def _unchanged(store: SqlStore) -> None:
    """Nothing the work wrote stayed: no label, no queue row, the entry staged, the task open."""
    assert store.labels_for("person", ["crm:C0001"]) == []
    assert store.queue_rows("person", [SourceKey("hr", "H1")]) == []
    assert store.tray_entries(["TR-1"])["TR-1"].status == "staged"
    assert store.tasks_by_id(["TSK-0001"])["TSK-0001"].status == "open"


@pytest.fixture
def staged(store: SqlStore) -> SqlStore:
    store.put_source_states([state("H1")])
    write(store, a_task(1))
    store.stage_tray(an_entry("TR-1", "TSK-0001"), ["task:TSK-0001"])
    return store


def test_the_work_of_a_decision_commits_whole(staged: SqlStore) -> None:
    staged.apply_work(_work(approve=((SourceKey("hr", "H1"), "ev-hr-H1"),)))
    assert [lab.label for lab in staged.labels_for("person", ["crm:C0001"])] == ["not_a_match"]
    assert staged.queue_rows("person", [SourceKey("hr", "H1")]) == [(SourceKey("hr", "H1"), "ev-hr-H1", 11)]
    entry = staged.tray_entries(["TR-1"])["TR-1"]
    assert (entry.status, entry.change_set_id, entry.outcome) == ("committed", "CS-1", "committed")
    closed = staged.tasks_by_id(["TSK-0001"])["TSK-0001"]
    assert closed.status == "closed" and decided_at(staged, "TSK-0001") is not None
    assert staged.tasks_by_key(["TK-0001"]) == {}
    assert (
        staged.source_states("person", [SourceKey("hr", "H1")])[SourceKey("hr", "H1")].approved_event_id
        == "ev-hr-H1"
    )


def test_a_record_that_moved_rolls_the_decision_back(staged: SqlStore) -> None:
    staged.put_source_states([state("H1", event="ev-hr-H1-later")])
    with pytest.raises(Conflict) as raised:
        staged.apply_work(_work())
    assert raised.value.code == "record_changed" and raised.value.keys == ("hr:H1",)
    _unchanged(staged)


def test_a_task_no_longer_open_rolls_the_decision_back(staged: SqlStore) -> None:
    write(staged, replace(a_task(1), status="closed"))
    with pytest.raises(Conflict) as raised:
        staged.apply_work(_work())
    assert raised.value.code == "task_closed"
    assert staged.labels_for("person", ["crm:C0001"]) == []
    assert staged.queue_rows("person", [SourceKey("hr", "H1")]) == []
    assert staged.tray_entries(["TR-1"])["TR-1"].status == "staged"


def test_a_settled_entry_rolls_the_decision_back(staged: SqlStore) -> None:
    staged.settle_tray("TR-1", "undone", change_set_id=None, outcome="undone", at=T0)
    with pytest.raises(Conflict) as raised:
        staged.apply_work(_work())
    assert raised.value.code == "tray_entry_settled"
    assert staged.labels_for("person", ["crm:C0001"]) == []
    assert staged.queue_rows("person", [SourceKey("hr", "H1")]) == []
    assert staged.tasks_by_id(["TSK-0001"])["TSK-0001"].status == "open"
    assert staged.tray_entries(["TR-1"])["TR-1"].status == "undone"


# ---------------------------------------------------------------------------------------------- reads for the record view


def test_a_records_changes_are_read_newest_first_by_cursor(hub) -> None:
    land(hub, mini_world(persons=6, organisations=2).rows)
    arrive(hub)
    master = hub.store.xrefs_for_sources("person", [SourceKey("hr", "H000000")])[SourceKey("hr", "H000000")]
    everything = hub.store.changes_of_record("person", master, None, 100)
    assert everything and all(c.master_id == master for c in everything)
    keys = [(c.commit_version, c.change_seq) for c in everything]
    assert keys == sorted(keys, reverse=True)
    first = hub.store.changes_of_record("person", master, None, 1)
    rest = hub.store.changes_of_record("person", master, (first[0].commit_version, first[0].change_seq), 100)
    assert first + rest == everything
    logs = hub.store.change_log_rows([(everything[0].commit_version, master), (999, master)])
    assert set(logs) <= {(everything[0].commit_version, master)}
    commit = hub.store.commits_by_version([everything[0].commit_version])[0]
    assert (
        hub.store.change_set_evidence([commit.change_set_id, "CS-none"])[commit.change_set_id]["records"] > 0
    )
