"""The landing table as the hub reads it: idempotent inserts, gaps, late commits, range probes (B.5.5, B.6.1)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from mdm.backend import guard
from mdm.backend.store import SqlStore
from mdm.models.records import LandingRow
from tests.conftest import ENGINES

T0 = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)


def row(event: str, key: str = "H1", *, op: str = "upsert", version: int | None = None) -> LandingRow:
    payload = {} if op == "delete" else {"given_name": "Given01", "birth_date": "1990-04-01"}
    return LandingRow(event, "hr", key, "person", op, T0 + timedelta(seconds=1), payload, version, False)


def land(store: SqlStore, *rows: LandingRow) -> int:
    with guard.simulating_integration_platform(store.settings, store.prefix):
        return store.landing_insert(list(rows))


def test_landing_insert_is_idempotent(store: SqlStore) -> None:
    assert land(store, row("e1"), row("e2", "H2")) == 2
    assert land(store, row("e1"), row("e2", "H2")) == 0
    assert land(store, row("e3", "H3"), row("e3", "H9")) == 1  # a repeated event keeps its first row
    read = store.landing_above(0, 10)
    assert [r.event_id for r in read] == ["e1", "e2", "e3"]
    assert read[2].source_key == "H3"
    assert read[0].payload == {"given_name": "Given01", "birth_date": "1990-04-01"}
    assert read[0].landed_at.tzinfo == UTC
    assert read[0].initial_load is False


def test_a_conflicting_insert_leaves_a_gap(store: SqlStore) -> None:
    land(store, row("e1"))
    land(store, row("e1"))  # redelivered: the sequence value it drew is lost
    land(store, row("e2", "H2"))
    seqs = [r.landing_seq for r in store.landing_above(0, 10)]
    assert len(seqs) == 2 and seqs[1] > seqs[0] + 1
    assert store.landing_max_seq() == seqs[1]


def test_reading_above_a_high_water_mark_is_ordered_and_bounded(store: SqlStore) -> None:
    land(store, *[row(f"e{i:02d}", f"H{i:02d}") for i in range(12)])
    first = store.landing_above(0, 5)
    assert [r.landing_seq for r in first] == sorted(r.landing_seq for r in first)
    rest = store.landing_above(first[-1].landing_seq, 100)
    assert len(first) + len(rest) == 12
    assert rest[0].landing_seq > first[-1].landing_seq
    assert store.landing_above(store.landing_max_seq(), 10) == []


def test_ranges_find_rows_inside_them_only(store: SqlStore, monkeypatch: pytest.MonkeyPatch) -> None:
    land(store, *[row(f"e{i:02d}", f"H{i:02d}") for i in range(10)])
    seqs = [r.landing_seq for r in store.landing_above(0, 100)]
    ranges = [(seqs[7], seqs[8]), (seqs[1], seqs[1]), (seqs[4], seqs[5]), (10_000, 20_000)]
    found = [r.landing_seq for r in store.landing_in_ranges(ranges, 100)]
    assert found == [seqs[1], seqs[4], seqs[5], seqs[7], seqs[8]]
    assert [r.landing_seq for r in store.landing_in_ranges(ranges, 2)] == [seqs[1], seqs[4]]
    from mdm import capacity

    monkeypatch.setattr(capacity, "GAP_PROBE_RANGES", 1)  # one range per statement, same answer
    assert [r.landing_seq for r in store.landing_in_ranges(ranges, 100)] == found
    assert store.landing_in_ranges([], 10) == []


def test_deletes_and_versions_are_read_as_landed(store: SqlStore) -> None:
    land(store, row("e1", version=4), row("e2", op="delete", version=5))
    upsert, delete = store.landing_above(0, 10)
    assert (upsert.op, upsert.source_version) == ("upsert", 4)
    assert (delete.op, delete.source_version, delete.payload) == ("delete", 5, {})


@pytest.mark.postgres
@pytest.mark.skipif("postgres" not in ENGINES, reason="the run leaves Postgres out")
def test_an_uncommitted_row_is_invisible_until_it_commits(make_store: Callable[..., SqlStore]) -> None:
    import psycopg

    store = make_store("postgres")
    table = store.t("landing", "source_change")
    insert = (
        f"INSERT INTO {table} (event_id, source_system, source_key, entity, op, occurred_at, payload) "
        "VALUES (%s, 'hr', %s, 'person', 'upsert', now(), '{}'::jsonb) RETURNING landing_seq"
    )
    dsn = store.settings.postgres_dsn
    with psycopg.connect(dsn) as slow, psycopg.connect(dsn, autocommit=True) as fast:
        (slow_seq,) = slow.execute(insert, ["late", "H1"]).fetchone()  # open transaction
        (fast_seq,) = fast.execute(insert, ["early", "H2"]).fetchone()
        assert slow_seq < fast_seq
        assert [r.event_id for r in store.landing_above(0, 10)] == ["early"]
        slow.commit()
    read = store.landing_above(0, 10)
    assert [(r.event_id, r.landing_seq) for r in read] == [("late", slow_seq), ("early", fast_seq)]
    assert [r.event_id for r in store.landing_in_ranges([(slow_seq, slow_seq)], 10)] == ["late"]
