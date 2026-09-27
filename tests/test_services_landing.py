"""The landing reader: every committed row read at least once, nothing stalls (owner: SERVICES, B.6.2)."""

from __future__ import annotations

import threading
from datetime import timedelta

import pytest

from mdm.backend import guard
from mdm.models.canonical import canonical_json, iso
from mdm.models.records import Gap
from mdm.services.landing import Batch, LandingReader, missing_ranges, split_gap
from tests.conftest import ENGINES
from tests.test_services_fixtures import (
    T0,
    arrive,
    crm_person_key,
    land,
    master_of,
    person_payload,
    row,
)


def _land_at(hub, landing_row, seq: int) -> None:
    """A landing row with an explicit sequence number: as if its transaction took the number and committed late."""
    store = hub.store
    table = store.t("landing", "source_change")
    json_param = store._json_param()
    with guard.simulating_integration_platform(hub.settings, store.prefix):
        store._execute(
            f"INSERT INTO {table} (event_id, source_system, source_key, entity, op, occurred_at, source_version, "
            f"initial_load, payload, landed_at, landing_seq) VALUES (?, ?, ?, ?, ?, "
            f"CAST(? AS TIMESTAMP WITH TIME ZONE), ?, ?, {json_param}, CAST(? AS TIMESTAMP WITH TIME ZONE), ?)",
            [
                landing_row.event_id,
                landing_row.source_system,
                landing_row.source_key,
                landing_row.entity,
                landing_row.op,
                iso(landing_row.occurred_at),
                landing_row.source_version,
                landing_row.initial_load,
                canonical_json(dict(landing_row.payload)),
                iso(landing_row.occurred_at),
                seq,
            ],
        )


ONLY_POSTGRES = pytest.mark.skipif(
    "postgres" not in ENGINES, reason="the engines of this run leave Postgres out"
)


def _crm(i: int):
    return row("crm", crm_person_key(i), "person", person_payload(i))


def test_missing_ranges_and_splitting() -> None:
    assert missing_ranges(0, [1, 2, 5, 9]) == [(3, 4), (6, 8)]
    assert missing_ranges(3, []) == []
    gap = Gap(10, 20, "open", T0, None)
    assert [(g.lo, g.hi) for g in split_gap(gap, [10, 15, 20, 30])] == [(11, 14), (16, 19)]
    assert split_gap(Gap(5, 5, "lost", T0, T0), [5]) == []


def test_a_batch_and_its_record(hub, fake_clock) -> None:
    reader = LandingReader(hub.store, clock=fake_clock)
    land(hub, [_crm(i) for i in range(3)])
    batch = reader.next_batch(2)
    assert [c.landing_seq for c in batch.rows] == [1, 2] and batch.high_water == 2 and batch.new_gaps == ()
    with hub.store.transaction():
        reader.record(batch)
    assert reader.high_water == 2
    rest = reader.next_batch(10)
    assert [c.landing_seq for c in rest.rows] == [3]
    # a new reader object picks the position up where the last one left it
    assert LandingReader(hub.store, clock=fake_clock).high_water == 2


def test_rows_above_a_permanent_gap_are_read_without_waiting(hub, fake_clock) -> None:
    _land_at(hub, _crm(0), 1)
    rows = [_crm(i) for i in range(1, 61)]
    for n, landing in enumerate(rows):
        _land_at(hub, landing, 3 + n)  # number 2 never commits
    hub.arrival.reader = LandingReader(hub.store, clock=fake_clock)
    report = hub.arrival.run(started_by=hub.actor, batch_size=7)
    assert report.read == 61 and report.queued == 61 and hub.store.queue_size() == 0
    assert report.high_water == 62 and report.gaps_open == 1 and report.low_water == 1
    # an empty run after the timeout moves the low-water mark: the gap is declared lost
    fake_clock.advance(601)
    empty = hub.arrival.run(started_by=hub.actor)
    assert empty.read == 0 and empty.gaps_open == 0 and empty.gaps_lost == 1 and empty.low_water == 62


def test_a_jump_of_a_million_numbers_stores_one_gap(hub, fake_clock) -> None:
    _land_at(hub, _crm(0), 1)
    _land_at(hub, _crm(1), 1_000_002)
    reader = LandingReader(hub.store, clock=fake_clock)
    batch = reader.next_batch(10)
    assert batch.new_gaps == ((2, 1_000_001),)
    with hub.store.transaction():
        reader.record(batch)
    assert hub.store.gaps(reader.reader, "open", None, 10) == [Gap(2, 1_000_001, "open", fake_clock(), None)]


def test_a_row_committed_late_in_an_open_gap_is_read_next_time(hub, fake_clock) -> None:
    hub.arrival.reader = LandingReader(hub.store, clock=fake_clock)
    _land_at(hub, _crm(0), 1)
    _land_at(hub, _crm(2), 3)
    first = hub.arrival.run(started_by=hub.actor)
    assert first.read == 2 and first.gaps_open == 1
    _land_at(hub, _crm(1), 2)  # the slow transaction commits
    second = hub.arrival.run(started_by=hub.actor)
    assert second.read == 1 and second.gaps_open == 0 and second.low_water == 3
    assert master_of(hub, "person", "crm", crm_person_key(1)) is not None


def test_a_row_committed_after_its_gap_was_lost_is_found_by_reconciliation(hub, fake_clock) -> None:
    hub.arrival.reader = LandingReader(hub.store, clock=fake_clock)
    _land_at(hub, _crm(0), 1)
    _land_at(hub, _crm(2), 3)
    hub.arrival.run(started_by=hub.actor)
    fake_clock.advance(601)
    lost = hub.arrival.run(started_by=hub.actor)
    assert lost.gaps_lost == 1
    _land_at(hub, _crm(1), 2)
    not_yet = hub.arrival.run(started_by=hub.actor)  # reconciled less than a day ago: not probed yet
    assert not_yet.read == 0
    fake_clock.advance(timedelta(hours=25).total_seconds())
    found = hub.arrival.run(started_by=hub.actor)
    assert found.read == 1 and found.gaps_lost == 0
    assert master_of(hub, "person", "crm", crm_person_key(1)) is not None


def test_a_lost_range_past_the_retention_is_dropped(hub, fake_clock) -> None:
    reader = LandingReader(hub.store, clock=fake_clock)
    _land_at(hub, _crm(0), 1)
    _land_at(hub, _crm(2), 5)
    batch = reader.next_batch(10)
    with hub.store.transaction():
        reader.record(batch)
    fake_clock.advance(601)
    assert reader.tick().newly_lost == 1
    fake_clock.advance(timedelta(days=15).total_seconds())
    result = reader.reconcile(force=True)
    assert isinstance(result, Batch) and result.rows == () and result.dropped == 1
    assert hub.store.gaps(reader.reader, "lost", None, 10) == []
    assert not reader.due_for_reconcile()


@ONLY_POSTGRES
@pytest.mark.parametrize("engine", ["postgres"], indirect=True)
def test_a_late_commit_from_another_connection_is_processed_exactly_once(hub, fake_clock) -> None:
    import psycopg

    hub.arrival.reader = LandingReader(hub.store, clock=fake_clock)
    land(hub, [_crm(0)])
    slow = psycopg.connect(hub.settings.postgres_dsn, autocommit=False)
    try:
        landing = _crm(1)
        with slow.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {hub.store.t('landing', 'source_change')} (event_id, source_system, source_key, "
                "entity, op, occurred_at, payload) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)",
                [
                    landing.event_id,
                    landing.source_system,
                    landing.source_key,
                    landing.entity,
                    landing.op,
                    landing.occurred_at,
                    canonical_json(dict(landing.payload)),
                ],
            )
        land(hub, [_crm(2)])  # commits above the slow transaction's number
        first = hub.arrival.run(started_by=hub.actor)
        assert first.read == 2 and first.gaps_open == 1
        slow.commit()
    finally:
        slow.close()
    second = hub.arrival.run(started_by=hub.actor)
    third = hub.arrival.run(started_by=hub.actor)
    assert second.read == 1 and third.read == 0
    assert master_of(hub, "person", "crm", crm_person_key(1)) is not None
    assert len(hub.store.source_versions("person", landing_source(landing), 10)) == 1


def landing_source(landing):
    from mdm.models.records import SourceKey

    return SourceKey(landing.source_system, landing.source_key)


@ONLY_POSTGRES
@pytest.mark.parametrize("engine", ["postgres"], indirect=True)
def test_two_runs_at_once_one_holds_the_lease(hub) -> None:
    land(hub, [_crm(i) for i in range(20)])
    reports = []
    gate = threading.Barrier(2)

    def run() -> None:
        gate.wait()
        reports.append(arrive(hub))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert hub.store.queue_size() == 0
    assert sum(r.read for r in reports) == 20
    assert all(master_of(hub, "person", "crm", crm_person_key(i)) for i in range(20))
    assert len({master_of(hub, "person", "crm", crm_person_key(i)) for i in range(20)}) == 20
    assert all(r.skipped_busy or r.read in (0, 20) for r in reports)
