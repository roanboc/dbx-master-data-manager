"""Signature batches' store on both engines (story 3.3): the four tables and a story-3.2 store gaining them,
signature groups and their capped window, the batch's conditional writes, a chunk's checks and writes inside
its commit's own transaction, the tray's committing entries, bulk rights, labels a compensation withdraws, and
the access rows a split writes; on Postgres, the lock order that makes a stop and a trip wait for a chunk."""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from mdm import capacity
from mdm.backend import ddl
from mdm.backend.store import SqlStore
from mdm.models.authority import AccessRow
from mdm.models.batch import (
    Batch,
    BatchChunkWrite,
    BatchItem,
    BatchSampleWrite,
    chunk_change_set_id,
    signature_key,
)
from mdm.models.changes import WorkWrites
from mdm.models.errors import Conflict, MdmError
from mdm.models.quality import AUTO_BAND, bulk_band
from mdm.models.records import SourceKey
from mdm.models.tasks import Task
from mdm.models.workbench import TaskQuery, TraySettlement
from tests.conftest import ONLY_POSTGRES_ENGINE, THREAD_TIMEOUT, join_all
from tests.test_backend_store import state
from tests.test_backend_workbench import a_label, a_sample, a_task, an_entry, write
from tests.test_backend_workbench import a_review as a_blind_answer

T0 = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
SIGNATURE = "given_name= · family_name= · birth_date≈ · email∅"
OTHER = "given_name= · family_name≈"
KEY = signature_key("person", 1, SIGNATURE)
OTHER_KEY = signature_key("person", 1, OTHER)
BAND = bulk_band("person", SIGNATURE)
MAKER = "persona:data_steward"
CHECKER = "persona:coordinating_steward"
BATCH_TABLES = ("batch", "batch_item", "batch_chunk", "open_batch")
BATCH_TASK_COLUMNS = ("signature", "rule_version", "signature_key")
NEW_INDEXES = (
    "batch_status_not_before_batch_id_ix",
    "batch_signature_key_status_batch_id_ix",
    "batch_maker_created_at_ix",
    "batch_checker_staged_at_ix",
    "batch_compensates_ix",
    "batch_item_task_id_status_ix",
    "batch_item_batch_id_role_status_position_ix",
    "batch_item_batch_id_chunk_no_ix",
    "task_signature_key_status_due_at_task_id_ix",
    "quality_sample_entity_origin_signature_reviewed_at_sample_id_ix",
    "match_label_entity_signature_label_ix",
)


def bid(n: int) -> str:
    """An invented batch ID: `BAT-` and 20 hexadecimal characters."""
    return f"BAT-{n:020x}"


def a_grouped_task(
    n: int, *, signature: str | None = SIGNATURE, rule_version: int | None = 1, **changes
) -> Task:
    """An open review on crm:C<n> that carries a signature and a rule version (its group key follows)."""
    return replace(a_task(n, **changes), signature=signature, rule_version=rule_version)


def a_batch(n: int = 1, *, status: str = "sampling", kind: str = "link", **changes) -> Batch:
    link = kind == "link"
    batch = Batch(
        batch_id=bid(n),
        entity="person",
        kind=kind,
        status=status,
        maker=MAKER,
        maker_role="data_steward",
        created_at=T0,
        updated_at=T0,
        rule_version=1 if link else None,
        signature=SIGNATURE if link else "",
        signature_key=KEY if link else None,
        bulk_band=BAND if link else None,
        persona=True,
        figures={"left_out": {"claimed": 1}},
    )
    return replace(batch, **changes)


def an_item(batch_id: str, n: int, *, role: str = "bulk", status: str = "candidate", **changes) -> BatchItem:
    item = BatchItem(
        batch_id=batch_id,
        task_id=f"TSK-{n:04d}",
        role=role,
        status=status,
        source=SourceKey("crm", f"C{n:04d}"),
        event_id=f"ev-{n}",
        target="PER-000001",
        target_version=3,
        score=81.5,
        band="review",
        stratum="crm/hr",
        draw=(1 << 62) + n,  # a 63-bit draw survives both engines' row documents
        position=n,
    )
    return replace(item, **changes)


def locks(*ns: int) -> list[str]:
    return [s for n in ns for s in (f"task:TSK-{n:04d}", f"source:person:crm:C{n:04d}")]


def ids(tasks: list[Task]) -> list[str]:
    return [t.task_id for t in tasks]


def chunk(
    number: int,
    ns: tuple[int, ...],
    *,
    first: bool,
    last: bool,
    seconds_per_row: float = 0.0,
    batch_id: str = bid(1),
    entry: str = "TR-B",
) -> WorkWrites:
    """The work of one chunk that links the reviews `ns`, as `BatchService` builds it: each record at its
    event, each task closed, a label per review, the chunk write, and on the first chunk the entry's
    settlement."""
    change_set = chunk_change_set_id(batch_id, number)
    task_ids = tuple(f"TSK-{n:04d}" for n in ns)
    return WorkWrites(
        "person",
        expect_events=tuple((SourceKey("crm", f"C{n:04d}"), f"ev-{n}") for n in ns),
        close_task_ids=task_ids,
        labels=tuple(
            replace(
                a_label(f"crm:C{n:04d}", "PER-000001", "match", entry=entry),
                signature=SIGNATURE,
                task_id=f"TSK-{n:04d}",
            )
            for n in ns
        ),
        batch=BatchChunkWrite(
            batch_id,
            number,
            "link",
            entry,
            first,
            last,
            task_ids,
            subjects=tuple(locks(*ns)),
            bulk_band=BAND,
            signature=SIGNATURE,
            seconds_per_row=seconds_per_row,
            change_set_id=change_set,
            commit_version=10 + number,
            rows=2 * len(ns),
        ),
        tray=(
            (
                TraySettlement(
                    entry, "committed", change_set, "committed" if last else "committing", keep_locks=not last
                ),
            )
            if first
            else ()
        ),
    )


def item_of(store: SqlStore, task_id: str, batch_id: str = bid(1)) -> BatchItem:
    (found,) = [i for i in store.items_by_task([task_id])[task_id] if i.batch_id == batch_id]
    return found


def batch_of(store: SqlStore, batch_id: str = bid(1)) -> Batch:
    return store.batches([batch_id])[batch_id]


def index_names(store: SqlStore) -> set[str]:
    schema = ddl.schema_name(store.prefix, "work")
    if store.engine == "duckdb":
        sql = "/*mdm:small*/ SELECT index_name FROM duckdb_indexes() WHERE schema_name = ?"
    else:
        sql = "/*mdm:small*/ SELECT indexname FROM pg_indexes WHERE schemaname = ?"
    return {r[0] for r in store._fetch_all(sql, [schema])}


@pytest.fixture
def staged(store: SqlStore) -> SqlStore:
    """A link batch of four planned reviews staged in the tray as one entry, TR-B, holding every review's
    task and record; the store's clock stands at T0."""
    store.clock = lambda: T0
    store.put_source_states([state(f"C{n:04d}", system="crm", event=f"ev-{n}") for n in range(1, 5)])
    write(store, *(a_grouped_task(n) for n in range(1, 5)))
    batch = a_batch(status="staged", entry_id="TR-B", staged_at=T0, decisions=4, chunks=2)
    store.insert_batch(batch, [an_item(batch.batch_id, n, status="planned") for n in range(1, 5)])
    entry = replace(
        an_entry("TR-B", batch.batch_id),
        decision="batch_link",
        target=None,
        subject={"batch_id": batch.batch_id, "decisions": 4},
        signature=SIGNATURE,
    )
    store.stage_tray(entry, locks(1, 2, 3, 4))
    return store


# ---------------------------------------------------------------------------------------------- the schema


def test_the_batch_tables_exist_and_an_older_store_gains_them(
    store: SqlStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in BATCH_TABLES:
        assert store.table_columns("work", name) == list(ddl.table("work", name).column_names())
    assert set(NEW_INDEXES) <= index_names(store)

    # a story-3.2 store: no batch tables, and the task, breaker, sample and label tables as they were
    store.drop_all()
    task, breaker = ddl.table("work", "task"), ddl.table("work", "breaker_state")
    sample, label = ddl.table("work", "quality_sample"), ddl.table("work", "match_label")
    older = {
        task: replace(
            task,
            columns=tuple(c for c in task.columns if c.name not in BATCH_TASK_COLUMNS),
            indexes=tuple(i for i in task.indexes if i[0] != "signature_key"),
        ),
        breaker: replace(breaker, columns=tuple(c for c in breaker.columns if c.name != "signature")),
        sample: replace(
            sample,
            columns=tuple(c for c in sample.columns if c.name != "checked_by"),
            indexes=tuple(i for i in sample.indexes if "signature" not in i),
        ),
        label: replace(label, indexes=(("entity", "right_ref"),)),
    }
    monkeypatch.setattr(
        ddl, "TABLES", tuple(older.get(t, t) for t in ddl.TABLES if t.name not in BATCH_TABLES)
    )
    store.init_schema(create_landing=True)
    assert store.table_columns("work", "batch") == []
    assert not set(NEW_INDEXES) & index_names(store)
    store._insert(
        older[task], [{k: v for k, v in store._task_row(a_task(1)).items() if k not in BATCH_TASK_COLUMNS}]
    )
    store._insert(
        older[breaker],
        [
            {
                "entity": "person",
                "band": AUTO_BAND,
                "state": "normal",
                "watch_since": T0,
                "figures": {},
                "updated_at": T0,
            }
        ],
    )
    store._insert(
        older[sample], [{k: v for k, v in store._sample_row(a_sample(1)).items() if k != "checked_by"}]
    )
    monkeypatch.undo()

    store.init_schema(create_landing=True)  # appends; drops nothing
    for table in (task, breaker, sample, label, *(ddl.table("work", n) for n in BATCH_TABLES)):
        assert store.table_columns("work", table.name) == list(table.column_names()), table.name
    assert set(NEW_INDEXES) <= index_names(store)
    kept = store.tasks_by_id(["TSK-0001"])["TSK-0001"]
    assert (kept.status, kept.signature, kept.rule_version, kept.signature_key) == ("open", None, None, None)
    assert store.breaker_state("person", AUTO_BAND).signature is None
    assert store.samples_by_id(["QS-0001"])["QS-0001"].checked_by is None
    assert store.open_tasks_without_signature(None, 10) == [kept]


# ---------------------------------------------------------------------------------------------- signature groups


def test_signature_groups_are_read_in_a_capped_window_with_signatures_bound(store: SqlStore) -> None:
    write(
        store,
        *(a_grouped_task(n, due=T0 + timedelta(minutes=n)) for n in range(1, 6)),
        *(a_grouped_task(n, signature=OTHER, due=T0 + timedelta(minutes=n)) for n in range(6, 9)),
        a_grouped_task(9, rule_version=2),
        a_grouped_task(10, signature=""),  # no golden record to link it to: no key
        a_grouped_task(11, kind="held"),
        a_grouped_task(12, status="closed"),
    )
    statements: list[str] = []
    remove = store.add_listener(statements.append)
    try:
        assert store.signature_groups("person", 1, 1000) == [(KEY, SIGNATURE, 5), (OTHER_KEY, OTHER, 3)]
        # the window: the six reviews due soonest
        assert store.signature_groups("person", 1, 6) == [(KEY, SIGNATURE, 5), (OTHER_KEY, OTHER, 1)]
        assert store.signature_groups("organisation", 1, 1000) == []
        assert store.older_rules_count("person", 1, 10) == 1
        assert store.older_rules_count("person", 2, 10) == 8 and store.older_rules_count("person", 2, 3) == 3
        first = store.group_members(KEY, None, 2)
        rest = store.group_members(KEY, (first[-1].due_at, first[-1].task_id), 10)
        assert ids(first + rest) == [f"TSK-{n:04d}" for n in range(1, 6)]
        group = TaskQuery(now=T0, kind="review", snoozed=None, signature_key=KEY)
        assert store.task_count(group, 100) == 5 and store.task_count(group, 3) == 3
        assert store.task_count(TaskQuery(now=T0, kind="review", snoozed=None, grouped=True), 100) == 9
        assert ids(store.task_page(group, None, 2)) == ["TSK-0001", "TSK-0002"]
    finally:
        remove()
    assert statements and not any(SIGNATURE in s or OTHER in s for s in statements)


def test_a_task_signature_goes_through_safe_signature_on_insert_and_on_update(store: SqlStore) -> None:
    with pytest.raises(ValueError, match="free text"):
        write(store, a_grouped_task(1, signature="given_name= · Pellan Quorane"))
    assert store.tasks_by_id(["TSK-0001"]) == {}
    write(store, a_grouped_task(1))
    written = store.tasks_by_id(["TSK-0001"])["TSK-0001"]
    assert (written.signature, written.rule_version, written.signature_key) == (SIGNATURE, 1, KEY)
    with pytest.raises(ValueError, match="free text"):  # the open task's update path checks it too
        write(store, a_grouped_task(1, signature="given_name≈ Quorane", event="ev-1b"))
    assert store.tasks_by_id(["TSK-0001"])["TSK-0001"].signature == SIGNATURE
    # a later event rewrites all three with the task
    write(store, a_grouped_task(1, signature="", rule_version=2, event="ev-1c"))
    again = store.tasks_by_id(["TSK-0001"])["TSK-0001"]
    assert (again.signature, again.rule_version, again.signature_key, again.event_id) == (
        "",
        2,
        None,
        "ev-1c",
    )
    # a task reopened under its old ID takes the new row's
    store.apply_work(WorkWrites("person", close_task_ids=("TSK-0001",)))
    write(store, a_grouped_task(1))
    assert store.tasks_by_id(["TSK-0001"])["TSK-0001"].signature_key == KEY


def test_the_backfill_gives_an_older_review_a_signature_once(store: SqlStore) -> None:
    write(
        store,
        a_grouped_task(1, signature=None, rule_version=None),
        a_grouped_task(2, signature=None, rule_version=None),
        a_grouped_task(3),
        a_grouped_task(4, signature=None, rule_version=None, kind="held"),  # only reviews are grouped
    )
    assert ids(store.open_tasks_without_signature(None, 10)) == ["TSK-0001", "TSK-0002"]
    assert ids(store.open_tasks_without_signature("TSK-0001", 10)) == ["TSK-0002"]
    assert (
        store.set_task_signatures([("TSK-0001", SIGNATURE, 1), ("TSK-0002", "", 1), ("TSK-0003", OTHER, 1)])
        == 2
    )
    found = store.tasks_by_id(["TSK-0001", "TSK-0002", "TSK-0003"])
    assert (found["TSK-0001"].signature, found["TSK-0001"].signature_key) == (SIGNATURE, KEY)
    assert (found["TSK-0002"].signature, found["TSK-0002"].signature_key) == ("", None)
    assert found["TSK-0003"].signature == SIGNATURE  # had one already: kept
    assert store.open_tasks_without_signature(None, 10) == []
    assert store.set_task_signatures([]) == 0
    with pytest.raises(ValueError, match="free text"):
        store.set_task_signatures([("TSK-0004", "given_name= Quorane", 1)])


def test_label_history_agreement_and_the_bulk_window_of_a_signature(store: SqlStore) -> None:
    store.put_labels(
        [replace(a_label(f"crm:C{n:04d}", "PER-000001", "match"), signature=SIGNATURE) for n in range(1, 5)]
        + [
            replace(a_label("crm:C0009", "PER-000002", "not_a_match"), signature=SIGNATURE),
            replace(a_label("crm:C0010", "PER-000001", "match"), signature=OTHER),
        ]
    )
    assert store.label_counts("person", SIGNATURE, 100) == {"match": 4, "not_a_match": 1}
    assert store.label_counts("person", SIGNATURE, 3) == {"match": 3, "not_a_match": 1}  # capped
    assert store.label_counts("organisation", SIGNATURE, 100) == {"match": 0, "not_a_match": 0}

    samples = (
        a_sample(1, signature=SIGNATURE),
        a_sample(2, signature=SIGNATURE, origin="batch", band="review", decided_at=T0),
        a_sample(3, signature=SIGNATURE, origin="batch", band="review", decided_at=T0 + timedelta(hours=2)),
        a_sample(4, signature=OTHER, origin="batch", band="review"),
    )
    store.apply_work(WorkWrites("person", samples=samples))
    store.apply_work(
        WorkWrites(
            "person",
            reviews=(
                a_blind_answer(samples[0], True),
                a_blind_answer(samples[1], True, at=T0 + timedelta(hours=1)),
                a_blind_answer(samples[2], False, at=T0 + timedelta(hours=3)),
                a_blind_answer(samples[3], False),
            ),
        )
    )
    agreement = {(r.origin, r.band): (r.agreed, r.reviewed) for r in store.agreement_of("person", SIGNATURE)}
    assert agreement == {("automated", "auto"): (1, 1), ("batch", "review"): (1, 2)}
    assert store.agreement_of("person", "given_name=") == []
    window = store.recent_reviews_of_signature("person", "batch", SIGNATURE, None, 10)
    assert [(status, sid) for status, _, sid in window] == [("disagreed", "QS-0003"), ("agreed", "QS-0002")]
    since = store.recent_reviews_of_signature("person", "batch", SIGNATURE, T0 + timedelta(hours=1), 10)
    assert [sid for _, _, sid in since] == ["QS-0003"]  # only samples first decided after a restore
    assert len(store.recent_reviews_of_signature("person", "batch", SIGNATURE, None, 1)) == 1


def test_a_batch_sample_names_its_second_steward_who_never_sees_it(store: SqlStore) -> None:
    write(store, a_task(2, kind="quality_sample"), a_task(3, kind="quality_sample"))
    store.apply_work(
        WorkWrites(
            "person",
            samples=(
                a_sample(2, task_id="TSK-0002", origin="batch", decided_by=MAKER, checked_by=CHECKER),
                a_sample(3, task_id="TSK-0003", origin="steward", decided_by=MAKER),
            ),
        )
    )
    assert store.samples_by_id(["QS-0002"])["QS-0002"].checked_by == CHECKER
    query = TaskQuery(now=T0, kind="quality_sample", snoozed=None)
    assert ids(store.task_page(replace(query, not_first_decider=CHECKER), None, 10)) == ["TSK-0003"]
    assert ids(store.task_page(replace(query, not_first_decider=MAKER), None, 10)) == []
    assert store.task_count(replace(query, not_first_decider="persona:data_owner"), 10) == 2


# ---------------------------------------------------------------------------------------------- batches and their rows


def test_a_group_holds_one_open_batch_and_a_second_draw_writes_nothing(store: SqlStore) -> None:
    first = a_batch(1)
    store.insert_batch(
        first, [an_item(first.batch_id, 1, role="sample", status="open"), an_item(first.batch_id, 2)]
    )
    with pytest.raises(Conflict) as refused:
        store.insert_batch(a_batch(2), [an_item(bid(2), 3)])
    assert refused.value.code == "batch_open" and refused.value.fields["batch"] == bid(1)
    assert store.batches([bid(1), bid(2)]) == {bid(1): first}  # read back as written
    assert store.items_by_task(["TSK-0003"]) == {}
    store.insert_batch(a_batch(3, signature=OTHER, signature_key=OTHER_KEY, status="awaiting_checker"), [])
    found = store.open_batches_of_groups([KEY, OTHER_KEY, "SIG-" + "0" * 16])
    assert {k: b.batch_id for k, b in found.items()} == {KEY: bid(1), OTHER_KEY: bid(3)}
    # an ended batch no longer holds its group, even before its row goes
    assert store.set_batch(
        bid(3), from_statuses=("awaiting_checker",), status="discarded", outcome="discarded"
    )
    assert set(store.open_batches_of_groups([KEY, OTHER_KEY])) == {KEY}
    with pytest.raises(ValueError):
        store.insert_batch(a_batch(4, signature_key=None), [])
    item = an_item(bid(1), 2)
    assert item_of(store, "TSK-0002") == replace(item, updated_at=T0)
    assert item_of(store, "TSK-0002").draw == (1 << 62) + 2


def test_a_batch_changes_only_by_its_conditional_updates(store: SqlStore) -> None:
    store.clock = lambda: T0
    store.insert_batch(
        a_batch(),
        [an_item(bid(1), n) for n in (1, 2, 3)] + [an_item(bid(1), 4, role="sample", status="open")],
    )
    assert not store.set_batch(bid(1), from_statuses=("ready",), status="awaiting_checker")
    assert store.set_batch(bid(1), from_statuses=("sampling",), status="ready", figures={"sent_back": 1})
    assert (batch_of(store).status, batch_of(store).figures) == ("ready", {"sent_back": 1})
    with pytest.raises(ValueError, match="free text"):
        store.set_batch(bid(1), from_statuses=("ready",), figures={"note": "Ada Quill"})
    with pytest.raises(MdmError):
        store.set_batch(bid(1), from_statuses=("ready",), not_a_column=1)
    with pytest.raises(ValueError):
        store.set_batch(bid(1), from_statuses=("ready' OR 1=1 --",), status="failed")
    assert store.hold_batch(bid(1), ("sampling",)) is None
    assert store.hold_batch(bid(1), ("ready",)) == batch_of(store)

    planned = [
        {"task_id": "TSK-0001", "status": "planned", "target_version": 4, "changes": ["phone"]},
        {"task_id": "TSK-0002", "status": "excluded", "target_version": None, "changes": []},
    ]
    assert store.update_items(bid(1), planned, from_status="candidate") == 2
    assert store.update_items(bid(1), planned, from_status="candidate") == 0  # moved on already
    assert item_of(store, "TSK-0001").changes == ("phone",) and item_of(store, "TSK-0001").target_version == 4
    assert (
        store.update_items(
            bid(1), [{"task_id": "TSK-0003", "role": "sample", "status": "open"}], from_status="candidate"
        )
        == 1
    )
    assert [i.task_id for i in store.batch_items(bid(1), ("sample",), ("open",), None, 10)] == [
        "TSK-0003",
        "TSK-0004",
    ]
    assert [i.task_id for i in store.batch_items(bid(1), ("sample", "bulk"), None, 2, 10)] == [
        "TSK-0003",
        "TSK-0004",
    ]
    assert [i.task_id for i in store.batch_items(bid(1), ("bulk",), ("planned", "excluded"), None, 1)] == [
        "TSK-0001"
    ]
    with pytest.raises(ValueError, match="free text"):
        store.update_items(bid(1), [{"task_id": "TSK-0001", "reason": "Ada Quill"}], from_status="planned")
    with pytest.raises(MdmError):
        store.update_items(bid(1), [{"task_id": "TSK-0001", "batch_id": bid(9)}], from_status="planned")

    assert [b.batch_id for b in store.batches_by_status(("ready",), None, 10)] == [bid(1)]
    assert store.batches_by_status(("ready",), bid(1), 10) == []
    store.insert_batch(a_batch(2, signature_key=OTHER_KEY, status="awaiting_checker", maker=CHECKER), [])
    assert store.to_confirm_count(MAKER, None, 10) == 1 and store.to_confirm_count(CHECKER, None, 10) == 0
    assert store.to_confirm_count(MAKER, "organisation", 10) == 0


def test_a_sample_outcome_is_written_to_an_open_sample_review_only(store: SqlStore) -> None:
    store.insert_batch(
        a_batch(),
        [
            an_item(bid(1), 1, role="sample", status="open"),
            an_item(bid(1), 2, role="sample", status="void"),
            an_item(bid(1), 3),
            an_item(bid(1), 4, role="split", status="open"),
        ],
    )
    store.apply_work(
        WorkWrites(
            "person",
            batch_samples=(
                BatchSampleWrite(bid(1), "TSK-0001", "disagreed", "TR-1", "birth_date"),
                BatchSampleWrite(bid(1), "TSK-0002", "agreed", "TR-2"),
                BatchSampleWrite(bid(1), "TSK-0003", "agreed", "TR-3"),
                BatchSampleWrite(bid(1), "TSK-0004", "agreed", "TR-4"),
            ),
        )
    )
    first = item_of(store, "TSK-0001")
    assert (first.status, first.entry_id, first.split_on, first.split_applied) == (
        "disagreed",
        "TR-1",
        "birth_date",
        False,
    )
    assert [item_of(store, f"TSK-000{n}").status for n in (2, 3, 4)] == ["void", "candidate", "open"]


def test_a_split_moves_its_reviews_and_appends_its_access_rows_in_one_transaction(
    store: SqlStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.insert_batch(
        a_batch(),
        [
            an_item(bid(1), 1, role="sample", status="disagreed", split_on="birth_date", entry_id="TR-1"),
            an_item(bid(1), 2, role="sample", status="agreed"),
            an_item(bid(1), 3, role="sample", status="open"),
            *(an_item(bid(1), n) for n in range(4, 9)),
        ],
    )

    def access(n: int) -> AccessRow:
        detail = {"batch_id": bid(1), "source": f"crm:C{n:04d}", "task_id": "TSK-0001"}
        return AccessRow(
            MAKER, "data_steward", "batch_split", "person", None, "birth_date", "batch_split", detail
        )

    split = [1, 2, 4, 5, 6]
    statements: list[str] = []
    monkeypatch.setattr(capacity, "WRITE_CHUNK_ROWS", 2)
    remove = store.add_listener(statements.append)
    try:
        moved = store.apply_split(
            bid(1),
            "TSK-0001",
            [f"TSK-{n:04d}" for n in split],
            "split:birth_date",
            [access(n) for n in split],
        )
    finally:
        remove()
    assert moved == 5
    inserts = [s for s in statements if "INSERT INTO" in s and "access_log" in s]
    assert len(inserts) == 3  # five rows, one row document of at most two rows each
    rows = store.access_log(None, 100)
    assert len(rows) == 5 and {r["detail"]["source"] for r in rows} == {f"crm:C{n:04d}" for n in split}
    assert {(r["actor"], r["actor_role"], r["action"], r["reason"], r["attribute"]) for r in rows} == {
        (MAKER, "data_steward", "batch_split", "batch_split", "birth_date")
    }
    first, second, fourth = (item_of(store, f"TSK-000{n}") for n in (1, 2, 4))
    assert (first.role, first.status, first.split_applied, first.reason) == (
        "split",
        "disagreed",
        True,
        "split:birth_date",
    )
    assert (second.role, second.status) == ("split", "agreed")  # a split sample review keeps its outcome
    assert (fourth.role, fourth.status) == ("split", "split")
    assert (item_of(store, "TSK-0003").role, item_of(store, "TSK-0007").role) == ("sample", "bulk")
    # a review split meanwhile stays as it is
    assert store.apply_split(bid(1), "TSK-0003", ["TSK-0004"], "split:all", []) == 0
    assert item_of(store, "TSK-0004").reason == "split:birth_date"
    # the reviews and the access rows commit together: a refused row rolls the move back
    bad = access(3)
    object.__setattr__(bad, "detail", {"note": "Ada Quill"})
    with pytest.raises(ValueError, match="free text"):
        store.apply_split(bid(1), "TSK-0003", ["TSK-0003", "TSK-0007"], "split:email", [bad])
    assert (item_of(store, "TSK-0003").role, item_of(store, "TSK-0007").role) == ("sample", "bulk")
    assert len(store.access_log(None, 100)) == 5


def test_a_batch_ends_before_the_tray_and_frees_its_group(store: SqlStore) -> None:
    later = T0 + timedelta(minutes=5)
    store.insert_batch(a_batch(), [an_item(bid(1), 1, role="sample", status="open"), an_item(bid(1), 2)])
    assert not store.end_batch(bid(1), outcome="discarded", at=later, from_statuses=("ready",))
    assert batch_of(store).status == "sampling" and set(store.open_batches_of_groups([KEY])) == {KEY}
    assert store.end_batch(bid(1), outcome="too_few_left", at=later, from_statuses=("sampling",))
    ended = batch_of(store)
    assert (ended.status, ended.outcome, ended.finished_at, ended.updated_at) == (
        "discarded",
        "too_few_left",
        later,
        later,
    )
    assert store.open_batches_of_groups([KEY]) == {}
    assert (item_of(store, "TSK-0001").status, item_of(store, "TSK-0002").status) == ("open", "candidate")
    assert not store.end_batch(bid(1), outcome="discarded", at=later)  # ended already
    store.insert_batch(a_batch(2), [])  # the group can draw again

    # every alike review split off, and the batch ended, in one transaction, the batch row first
    store.insert_batch(a_batch(3, signature_key=OTHER_KEY), [an_item(bid(3), n) for n in (3, 4)])
    with store.transaction():
        assert store.apply_split(bid(3), "TSK-0003", ["TSK-0003", "TSK-0004"], "split:all", []) == 2
        assert store.end_batch(bid(3), outcome="split_all", at=later, from_statuses=("sampling",))
    assert (batch_of(store, bid(3)).outcome, item_of(store, "TSK-0004", bid(3)).reason) == (
        "split_all",
        "split:all",
    )
    assert store.open_batches_of_groups([OTHER_KEY]) == {}

    # a batch in the tray ends only through finish_batch
    with pytest.raises(ValueError):
        store.end_batch(bid(2), outcome="discarded", at=later, from_statuses=("staged",))


def test_discarding_a_compensation_lets_its_original_be_compensated_again(staged: SqlStore) -> None:
    staged.apply_work(chunk(1, (1, 2, 3, 4), first=True, last=True))
    compensation = a_batch(2, kind="compensate", status="ready", compensates=bid(1))
    staged.insert_batch(compensation, [an_item(bid(2), n, status="planned") for n in (1, 2, 3, 4)])
    assert batch_of(staged).compensated_by == bid(2)
    assert staged.end_batch(bid(2), outcome="discarded", at=T0)
    assert batch_of(staged).compensated_by is None and batch_of(staged, bid(2)).status == "discarded"
    staged.insert_batch(replace(compensation, batch_id=bid(3)), [an_item(bid(3), 1, status="planned")])
    assert batch_of(staged).compensated_by == bid(3)


def test_failures_and_splits_write_nothing_for_a_batch_in_another_status(store: SqlStore) -> None:
    store.insert_batch(
        a_batch(status="ready"),
        [an_item(bid(1), 1, status="planned"), an_item(bid(1), 2, role="sample", status="agreed")],
    )
    access = AccessRow(MAKER, "data_steward", "batch_split", "person", None, "birth_date", "batch_split")
    assert store.fail_items(bid(1), [("TSK-0001", "failed", "blocked")], []) == 0
    assert store.apply_split(bid(1), "TSK-0002", ["TSK-0001", "TSK-0002"], "split:birth_date", [access]) == 0
    assert item_of(store, "TSK-0001").status == "planned" and item_of(store, "TSK-0002").role == "sample"
    assert store.access_log(None, 10) == []


# ---------------------------------------------------------------------------------------------- a chunk's commit


def test_a_chunk_commits_its_reviews_exactly_once_and_frees_their_locks_chunk_by_chunk(
    staged: SqlStore,
) -> None:
    staged.set_batch(bid(1), from_statuses=("staged",), attempts=2)
    staged.apply_work(chunk(1, (1, 2), first=True, last=False, seconds_per_row=36.0))
    batch = batch_of(staged)
    assert (batch.status, batch.chunks_committed, batch.rows_committed, batch.attempts) == (
        "committing",
        1,
        4,
        0,
    )
    assert batch.not_before == T0 + timedelta(seconds=4 * 36)
    entry = staged.tray_entries(["TR-B"])["TR-B"]
    assert (entry.status, entry.outcome, entry.change_set_id) == (
        "committed",
        "committing",
        chunk_change_set_id(bid(1), 1),
    )
    # the later chunks' locks still hold: the inbox, a single decision and arrival's hand-back see them
    held = staged.staged_by_locks(locks(1, 2, 3, 4))
    assert set(held) == set(locks(3, 4)) and {e.entry_id for e in held.values()} == {"TR-B"}
    assert "TR-B" in [e.entry_id for e in staged.tray_of_actor(MAKER, T0 + timedelta(hours=1), 10)]
    first = item_of(staged, "TSK-0001")
    assert (first.status, first.chunk_no, first.change_set_id) == (
        "committed",
        1,
        chunk_change_set_id(bid(1), 1),
    )
    assert item_of(staged, "TSK-0003").status == "planned"
    assert staged.tasks_by_id(["TSK-0001"])["TSK-0001"].status == "closed"
    (written,) = staged.batch_chunks(bid(1))
    assert (written.chunk_no, written.commit_version, written.items, written.rows, written.committed_at) == (
        1,
        11,
        2,
        4,
        T0,
    )

    # chunk 1 again, over the reviews still planned: refused, and nothing it wrote stays
    with pytest.raises(Conflict) as again:
        staged.apply_work(chunk(1, (3, 4), first=False, last=False))
    assert again.value.code == "chunk_committed"
    assert staged.tasks_by_id(["TSK-0003"])["TSK-0003"].status == "open"
    assert (
        staged.labels_for("person", ["crm:C0003"]) == [] and item_of(staged, "TSK-0003").status == "planned"
    )

    staged.apply_work(chunk(2, (3, 4), first=False, last=True))
    done = batch_of(staged)
    assert (done.status, done.outcome, done.finished_at, done.chunks_committed, done.not_before) == (
        "committed",
        "committed",
        T0,
        2,
        None,
    )
    entry = staged.tray_entries(["TR-B"])["TR-B"]
    assert (entry.status, entry.outcome, entry.change_set_id) == (
        "committed",
        "committed",
        chunk_change_set_id(bid(1), 1),
    )
    assert staged.staged_by_locks(locks(1, 2, 3, 4)) == {}
    assert [c.chunk_no for c in staged.batch_chunks(bid(1))] == [1, 2]
    assert [lab.signature for lab in staged.labels_for("person", ["crm:C0004"])] == [SIGNATURE]
    staged.insert_batch(a_batch(2), [])  # the group is free again
    assert staged.batches_due(T0 + timedelta(days=1), 10) == []


def test_a_chunk_that_is_first_and_last_settles_its_entry_committed_with_every_lock(staged: SqlStore) -> None:
    staged.apply_work(chunk(1, (1, 2, 3, 4), first=True, last=True))
    entry = staged.tray_entries(["TR-B"])["TR-B"]
    assert (entry.status, entry.outcome) == ("committed", "committed")
    assert (batch_of(staged).status, batch_of(staged).outcome) == ("committed", "committed")
    assert staged.staged_by_locks(locks(1, 2, 3, 4)) == {}
    assert staged.open_batches_of_groups([KEY]) == {}


def test_a_stop_a_withdrawal_or_a_moved_review_rolls_a_chunk_back(staged: SqlStore) -> None:
    staged.apply_work(chunk(1, (1,), first=True, last=False))

    def unwritten() -> None:
        assert item_of(staged, "TSK-0002").status == "planned"
        assert [c.chunk_no for c in staged.batch_chunks(bid(1))] == [1]
        assert staged.tasks_by_id(["TSK-0002"])["TSK-0002"].status == "open"
        assert staged.labels_for("person", ["crm:C0002"]) == []
        assert set(staged.staged_by_locks(locks(2))) == set(locks(2))

    staged.put_source_states([state("C0002", system="crm", event="ev-2-later")])
    with pytest.raises(Conflict) as moved:
        staged.apply_work(chunk(2, (2,), first=False, last=False))
    assert moved.value.code == "record_changed"
    unwritten()
    staged.put_source_states([state("C0002", system="crm", event="ev-2")])

    assert staged.trip_breaker("person", BAND, "agreement", {"agreed": 3, "reviewed": 5}, T0, "CS-trip")
    with pytest.raises(Conflict) as withdrawn:
        staged.apply_work(chunk(2, (2,), first=False, last=False))
    assert withdrawn.value.code == "bulk_withdrawn"
    unwritten()
    assert staged.breaker_state("person", BAND).signature == SIGNATURE  # the chunk's hold wrote the row
    assert staged.restore_breaker(
        "person", BAND, "persona:data_owner", "data_owner", "false_alarm", T0, "CS-r"
    )

    assert staged.request_stop(bid(1), CHECKER, T0)
    with pytest.raises(Conflict) as stopped:
        staged.apply_work(chunk(2, (2,), first=False, last=False))
    assert stopped.value.code == "batch_stopped"
    unwritten()


def test_a_chunk_of_a_batch_that_went_back_to_ready_publishes_nothing(staged: SqlStore) -> None:
    assert staged.undo_batch(bid(1), "TR-B", T0)
    with pytest.raises(Conflict) as changed:
        staged.apply_work(chunk(1, (1, 2), first=True, last=False))
    assert changed.value.code == "batch_changed"
    assert staged.batch_chunks(bid(1)) == [] and batch_of(staged).status == "ready"


def test_undo_takes_a_staged_batch_back_to_ready_and_only_its_own_entry(staged: SqlStore) -> None:
    staged.update_items(bid(1), [{"task_id": "TSK-0001", "review": True}], from_status="planned")
    staged.set_batch(
        bid(1), from_statuses=("staged",), checker=CHECKER, checker_role="coordinating_steward", checked_at=T0
    )
    assert not staged.undo_batch(bid(1), "TR-other", T0)  # not its entry: nothing written
    assert batch_of(staged).status == "staged" and staged.tray_entries(["TR-B"])["TR-B"].status == "staged"
    held: list[int] = []
    assert staged.undo_batch(bid(1), "TR-B", T0, after_hold=lambda: held.append(1))
    batch = batch_of(staged)
    assert (batch.status, batch.entry_id, batch.staged_at, batch.checker, batch.checked_at) == (
        "ready",
        None,
        None,
        None,
        None,
    )
    entry = staged.tray_entries(["TR-B"])["TR-B"]
    assert (entry.status, entry.outcome) == ("undone", "undone") and held == [1]
    assert staged.staged_by_locks(locks(1, 2, 3, 4)) == {} and not item_of(staged, "TSK-0001").review
    assert not staged.undo_batch(bid(1), "TR-B", T0)  # ready now


def test_an_undo_whose_entry_settled_meanwhile_is_refused_and_writes_nothing(staged: SqlStore) -> None:
    staged.settle_tray("TR-B", "failed", change_set_id=None, outcome="internal", at=T0)
    with pytest.raises(Conflict) as refused:
        staged.undo_batch(bid(1), "TR-B", T0)
    assert refused.value.code == "already_settled" and refused.value.fields["batch"] == bid(1)
    assert batch_of(staged).status == "staged"


def test_keep_locks_leaves_an_entrys_locks_to_its_batch(staged: SqlStore) -> None:
    assert staged.settle_tray(
        "TR-B", "committed", change_set_id="CS-1", outcome="committing", at=T0, keep_locks=True
    )
    assert set(staged.staged_by_locks(locks(1, 2, 3, 4))) == set(locks(1, 2, 3, 4))
    assert staged.release_locks(locks(1)) == 2
    assert staged.finish_tray_batch("TR-B", "stopped", T0 + timedelta(minutes=5))
    entry = staged.tray_entries(["TR-B"])["TR-B"]
    assert (entry.status, entry.outcome, entry.settled_at) == (
        "committed",
        "stopped",
        T0 + timedelta(minutes=5),
    )
    assert staged.staged_by_locks(locks(2)) == {}  # no longer live: its locks hold nothing
    assert not staged.finish_tray_batch("TR-none", "stopped", T0)


def test_a_throttled_batch_is_due_once_a_stop_is_asked_or_its_bulk_rights_are_withdrawn(
    staged: SqlStore,
) -> None:
    staged.apply_work(chunk(1, (1, 2), first=True, last=False, seconds_per_row=3600.0))
    assert batch_of(staged).not_before == T0 + timedelta(hours=4)
    soon = T0 + timedelta(hours=1)
    assert staged.batches_due(soon, 10) == []
    assert [b.batch_id for b in staged.batches_due(T0 + timedelta(hours=4), 10)] == [bid(1)]
    assert staged.trip_breaker("person", BAND, "agreement", {}, T0, "CS-trip")
    assert [b.batch_id for b in staged.batches_due(soon, 10)] == [bid(1)]
    staged.restore_breaker("person", BAND, "persona:data_owner", "data_owner", "false_alarm", T0, "CS-r")
    assert staged.batches_due(soon, 10) == []
    assert staged.request_stop(bid(1), CHECKER, T0)
    assert not staged.request_stop(bid(1), MAKER, T0)  # asked once
    batch = batch_of(staged)
    assert (batch.stop_requested_by, batch.stop_requested_at, batch.not_before) == (CHECKER, T0, None)
    assert [b.batch_id for b in staged.batches_due(soon, 10)] == [bid(1)]
    staged.insert_batch(a_batch(2, signature_key=OTHER_KEY, status="ready"), [])
    assert not staged.request_stop(bid(2), CHECKER, T0)  # only a committing batch stops


def test_finishing_a_batch_that_committed_part_of_its_reviews(staged: SqlStore) -> None:
    staged.insert_batch(a_batch(2, signature_key=OTHER_KEY, status="ready"), [])
    assert not staged.finish_batch(bid(2), status="stopped", outcome="stopped", at=T0, entry_id="TR-B")
    assert batch_of(staged, bid(2)).status == "ready"  # left as it is
    staged.apply_work(chunk(1, (1, 2), first=True, last=False))
    # every review after chunk 1 moved: each fails alone, and the batch ends committed
    failures = [("TSK-0003", "failed", "record_changed"), ("TSK-0004", "failed", "blocked")]
    assert staged.fail_items(bid(1), failures, locks(3, 4)) == 2
    assert staged.staged_by_locks(locks(3, 4)) == {} and item_of(staged, "TSK-0004").reason == "blocked"
    assert staged.finish_batch(bid(1), status="committed", outcome="committed", at=T0, entry_id="TR-B")
    batch = batch_of(staged)
    assert (batch.status, batch.outcome, batch.finished_at) == ("committed", "committed", T0)
    entry = staged.tray_entries(["TR-B"])["TR-B"]
    assert (entry.status, entry.outcome) == ("committed", "committed")
    assert staged.open_batches_of_groups([KEY]) == {} and staged.batches_due(T0 + timedelta(days=1), 10) == []
    assert not staged.finish_batch(bid(1), status="stopped", outcome="stopped", at=T0, entry_id="TR-B")


def test_finishing_a_stopped_batch_releases_its_reviews_and_one_that_never_began_fails(
    staged: SqlStore,
) -> None:
    staged.apply_work(chunk(1, (1,), first=True, last=False))
    staged.request_stop(bid(1), CHECKER, T0)
    assert staged.finish_batch(bid(1), status="stopped", outcome="stopped", at=T0, entry_id="TR-B")
    assert [
        (item_of(staged, f"TSK-000{n}").status, item_of(staged, f"TSK-000{n}").reason) for n in (2, 3)
    ] == [("released", "stopped")] * 2
    assert item_of(staged, "TSK-0001").status == "committed"
    assert staged.staged_by_locks(locks(1, 2, 3, 4)) == {}
    entry = staged.tray_entries(["TR-B"])["TR-B"]
    assert (entry.status, entry.outcome, batch_of(staged).status) == ("committed", "stopped", "stopped")

    # a batch that ends before its first chunk: its entry settles failed
    staged.insert_batch(
        a_batch(2, status="staged", entry_id="TR-C", staged_at=T0), [an_item(bid(2), 9, status="planned")]
    )
    staged.stage_tray(replace(an_entry("TR-C", bid(2)), decision="batch_link", target=None), locks(9))
    assert staged.finish_batch(bid(2), status="failed", outcome="nothing_left", at=T0, entry_id="TR-C")
    entry = staged.tray_entries(["TR-C"])["TR-C"]
    assert (entry.status, entry.outcome) == ("failed", "nothing_left")
    assert (item_of(staged, "TSK-0009", bid(2)).status, staged.staged_by_locks(locks(9))) == ("released", {})


def test_the_tray_lists_a_batch_for_its_maker_and_its_second_steward_each_once(staged: SqlStore) -> None:
    staged.set_batch(
        bid(1), from_statuses=("staged",), checker=CHECKER, checker_role="coordinating_steward", checked_at=T0
    )
    write(staged, a_task(9))
    staged.stage_tray(an_entry("TR-9", "TSK-0009", actor=CHECKER), ["task:TSK-0009"])
    assert [e.entry_id for e in staged.tray_of_actor(MAKER, T0, 10)] == ["TR-B"]
    theirs = staged.tray_of_actor(CHECKER, T0, 10)
    assert sorted(e.entry_id for e in theirs) == ["TR-9", "TR-B"]
    assert len(staged.tray_of_actor(CHECKER, T0, 1)) == 1  # one limit over both branches
    assert (staged.tray_live_count(MAKER, T0, 10), staged.tray_live_count(CHECKER, T0, 10)) == (1, 2)
    assert staged.tray_live_count(CHECKER, T0, 1) == 1  # capped
    assert staged.tray_live_count("persona:data_owner", T0, 10) == 0
    # while its chunks commit it stays live in both trays; once it ends it is live in neither
    staged.apply_work(chunk(1, (1, 2), first=True, last=False))
    later = T0 + timedelta(hours=1)
    for actor in (MAKER, CHECKER):
        assert "TR-B" in [e.entry_id for e in staged.tray_of_actor(actor, later, 10)]
    assert (staged.tray_live_count(MAKER, later, 10), staged.tray_live_count(CHECKER, later, 10)) == (1, 2)
    staged.apply_work(chunk(2, (3, 4), first=False, last=True))
    assert (staged.tray_live_count(MAKER, later, 10), staged.tray_live_count(CHECKER, later, 10)) == (0, 1)
    assert [e.entry_id for e in staged.tray_of_actor(MAKER, T0, 10)] == ["TR-B"]  # settled since T0


# ---------------------------------------------------------------------------------------------- compensation


def test_a_compensation_marks_its_original_once_and_undoes_a_chunk_its_own_way(staged: SqlStore) -> None:
    staged.apply_work(chunk(1, (1, 2), first=True, last=False))
    staged.apply_work(chunk(2, (3, 4), first=False, last=True))
    # a later decision labelled record 2 since: that label stays when the batch's are withdrawn
    later = replace(a_label("crm:C0002", "PER-000001", "match", entry="TR-later"), signature=SIGNATURE)
    staged.put_labels([later])

    compensation = a_batch(
        2, kind="compensate", status="staged", compensates=bid(1), entry_id="TR-C", staged_at=T0
    )
    staged.insert_batch(compensation, [an_item(bid(2), n, status="planned") for n in (1, 2, 3, 4)])
    assert batch_of(staged).compensated_by == bid(2)
    second = a_batch(3, kind="compensate", status="ready", compensates=bid(1))
    with pytest.raises(Conflict) as marked:
        staged.insert_batch(second, [an_item(bid(3), 1, status="planned")])
    assert marked.value.code == "already_compensated"
    assert staged.batches([bid(3)]) == {} and item_of(staged, "TSK-0001", bid(1)).status == "committed"
    assert not staged.clear_compensated_by(bid(1), bid(3))  # not its mark

    staged.stage_tray(
        replace(an_entry("TR-C", bid(2)), decision="batch_compensate", target=None), locks(1, 2, 3, 4)
    )
    change_set = chunk_change_set_id(bid(2), 1)
    staged.apply_work(
        WorkWrites(
            "person",
            unlabel=tuple((f"crm:C000{n}", "PER-000001", "TR-B") for n in (1, 2)),
            batch=BatchChunkWrite(
                bid(2),
                1,
                "compensate",
                "TR-C",
                True,
                False,
                ("TSK-0001", "TSK-0002"),
                subjects=tuple(locks(1, 2)),
                compensates=bid(1),
                undoes_chunk=1,
                change_set_id=change_set,
                commit_version=20,
                rows=2,
            ),
            tray=(TraySettlement("TR-C", "committed", change_set, "committing", keep_locks=True),),
        )
    )
    assert [item_of(staged, f"TSK-000{n}", bid(1)).status for n in (1, 2, 3, 4)] == ["compensated"] * 2 + [
        "committed"
    ] * 2
    undone, kept = staged.batch_chunks(bid(1))
    assert (undone.compensated_by, undone.compensated_at, kept.compensated_by) == (bid(2), T0, None)
    assert staged.labels_for("person", ["crm:C0001"]) == []
    assert [lab.entry_id for lab in staged.labels_for("person", ["crm:C0002", "crm:C0003"])] == [
        "TR-later",
        "TR-B",
    ]

    # stopped after its first chunk: the original can be compensated again; the chunk it undid stays undone
    staged.request_stop(bid(2), CHECKER, T0)
    assert staged.finish_batch(bid(2), status="stopped", outcome="stopped", at=T0, entry_id="TR-C")
    assert batch_of(staged).compensated_by is None
    assert staged.batch_chunks(bid(1))[0].compensated_by == bid(2)
    assert [item_of(staged, f"TSK-000{n}", bid(2)).status for n in (1, 3)] == ["committed", "released"]
    staged.insert_batch(second, [an_item(bid(3), n, status="planned") for n in (3, 4)])
    assert batch_of(staged).compensated_by == bid(3)
    assert staged.clear_compensated_by(bid(1), bid(3)) and batch_of(staged).compensated_by is None


# ---------------------------------------------------------------------------------------------- bulk rights


def test_the_automatic_band_reads_one_row_whatever_the_bulk_rows(store: SqlStore) -> None:
    store.ensure_breaker_rows(["person"], T0)
    signatures = [f"c{n:02d}=" for n in range(30)]
    for signature in signatures:
        assert store.ensure_breaker_rows(["person"], T0, bulk_band("person", signature), signature) == 1
    assert list(store.breaker_states(["person"])) == [("person", AUTO_BAND)]
    one = store.breaker_state("person", bulk_band("person", "c03="))
    assert (one.signature, one.state) == ("c03=", "normal")
    assert store.breaker_state("person", "bulk:" + "0" * 16) is None
    assert store.breaker_state("person", AUTO_BAND).signature is None
    withdrawn = sorted(bulk_band("person", signatures[n]) for n in (3, 7, 11, 19, 23))
    for band in withdrawn:
        assert store.trip_breaker("person", band, "agreement", {"agreed": 1, "reviewed": 5}, T0, "CS-1")
    assert store.trip_breaker("person", AUTO_BAND, "agreement", {}, T0, "CS-2")
    statements: list[str] = []
    remove = store.add_listener(statements.append)
    try:
        pages, after = [], None
        while True:
            page = store.withdrawn_bulk("person", after, 2)
            pages.append(page)
            if len(page) < 2:
                break
            after = page[-1].band
    finally:
        remove()
    assert [b.band for page in pages for b in page] == withdrawn  # never the automatic band
    assert [len(p) for p in pages] == [2, 2, 1]
    assert all(" LIKE ?" in s and "band <" not in s for s in statements)  # no bound on how punctuation sorts
    assert store.withdrawn_bulk("organisation", None, 10) == []
    with pytest.raises(ValueError, match="free text"):
        store.ensure_breaker_rows(["person"], T0, "bulk:" + "1" * 16, "given_name= Quorane")
    with store.transaction():
        assert not store.hold_band("person", withdrawn[0], signatures[3])
        assert store.hold_band("person", bulk_band("person", "c04="), "c04=")


# ---------------------------------------------------------------------------------------------- the inbox's filters


def test_the_inbox_lists_a_batchs_sample_and_a_groups_reviews_whoever_holds_them(store: SqlStore) -> None:
    write(store, *(a_grouped_task(n) for n in range(1, 5)), a_grouped_task(5, signature=OTHER))
    store.insert_batch(
        a_batch(),
        [
            an_item(bid(1), 1, role="sample", status="open"),
            an_item(bid(1), 2, role="sample", status="agreed"),
            an_item(bid(1), 3),
            an_item(bid(1), 4, role="split", status="open"),
        ],
    )
    store.snooze_task("TSK-0002", MAKER, T0 + timedelta(hours=4))
    store.claim_task("TSK-0001", CHECKER, T0, T0 - timedelta(minutes=10))
    sample = TaskQuery(now=T0, kind="review", snoozed=None, batch_id=bid(1))
    assert ids(store.task_page(sample, None, 10)) == ["TSK-0001", "TSK-0002"]
    assert (
        store.task_count(sample, 10) == 2
        and store.task_page(replace(sample, batch_id=bid(9)), None, 10) == []
    )
    group = TaskQuery(now=T0, kind="review", snoozed=None, signature_key=KEY)
    assert ids(store.task_page(group, None, 10)) == ["TSK-0001", "TSK-0002", "TSK-0003", "TSK-0004"]


# ---------------------------------------------------------------------------------------------- the lock order


@ONLY_POSTGRES_ENGINE
@pytest.mark.parametrize("interruption", ["stop", "trip"])
def test_a_stop_or_a_trip_waits_for_a_chunk_in_flight_and_stops_the_next(
    staged: SqlStore, interruption: str
) -> None:
    staged.apply_work(chunk(1, (1,), first=True, last=False))
    order: list[str] = []
    held = threading.Event()
    errors: list[BaseException] = []

    def commit() -> None:
        try:
            with staged.transaction():
                staged.apply_work(chunk(2, (2,), first=False, last=False))
                held.set()
                time.sleep(1.0)  # the stop or the trip waits on the batch row or the bulk row meanwhile
                order.append("chunk")
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    def interrupt() -> None:
        try:
            held.wait(THREAD_TIMEOUT)
            with staged.transaction():
                if interruption == "stop":
                    assert staged.request_stop(bid(1), CHECKER, T0)
                else:
                    assert staged.trip_breaker("person", BAND, "agreement", {}, T0, "CS-trip")
            order.append(interruption)
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=commit), threading.Thread(target=interrupt)]
    for thread in threads:
        thread.start()
    join_all(threads)
    assert not errors, errors
    assert order == ["chunk", interruption]
    assert [c.chunk_no for c in staged.batch_chunks(bid(1))] == [1, 2]
    with pytest.raises(Conflict) as next_chunk:
        staged.apply_work(chunk(3, (3,), first=False, last=False))
    assert next_chunk.value.code == ("batch_stopped" if interruption == "stop" else "bulk_withdrawn")
