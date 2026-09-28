"""The quality breaker (story 3.2, decision 3): its two triggers, demotion in arrival and at the commit, the
restore and the hand-back, its authority and its settings, on both engines (owner: SERVICES)."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta

import pytest

from mdm import capacity
from mdm.config import Settings
from mdm.models.authority import ACTIONS, AUTOMATED_MATCHER, QUALITY_BREAKER, Actor, allowed
from mdm.models.canonical import iso
from mdm.models.changes import WorkWrites
from mdm.models.errors import ConfigError, Conflict, Forbidden, NotFound, PlatformRefused
from mdm.models.quality import QualitySample, SampleReview
from mdm.models.records import SourceKey
from mdm.models.tasks import WAITS_FOR_RESTORE
from mdm.services.authority import require
from mdm.services.breaker import BreakerService, floor_hour
from mdm.services.context import Hub
from mdm.services.decisions import NOTICE_BREAKER_WAIT
from tests.conftest import FakeClock, open_hub
from tests.helpers import (
    CONSUMER,
    COORDINATOR,
    OWNER,
    STEWARD,
    T0,
    arrive,
    clusters,
    crm_person_key,
    hr_key,
    land,
    master_of,
    mini_world,
    open_tasks,
    person_payload,
    person_ref,
    row,
    seen,
    workbench_world,
)
from tests.test_services_tray import window_passed

DEMO = {"agreed": 30, "reviewed": 40, "threshold": 0.95, "window": 100}
TECHNICAL = Actor("persona:technical_steward", "person", "technical_steward", persona=True)
ADMINISTRATOR = Actor("persona:administrator", "person", "administrator", persona=True)


def rehub(hub: Hub, *, clock=None, **changes) -> Hub:
    """Another hub over the same store, with settings changed (and a fake clock when given)."""
    settings = hub.settings.with_(**changes) if changes else hub.settings
    if clock is None:
        return Hub.open(settings, store=hub.store, as_role="data_owner")
    return Hub.open(settings, store=hub.store, as_role="data_owner", clock=clock)


def reviewed(
    hub: Hub,
    outcomes: Sequence[bool],
    *,
    decided_at: datetime,
    reviewed_at: datetime,
    entity: str = "person",
    tag: str = "A",
) -> None:
    """Blind reviews of automatic links, written as the tray writes them: one sample each, then its answer."""
    samples = [
        QualitySample(
            sample_id=f"QS-{tag}{n:05d}",
            entity=entity,
            origin="automated",
            decision="auto_link",
            source=SourceKey("crm", f"C9{tag}{n:05d}"),
            master_ids=(),
            event_id=f"ev-{tag}{n}",
            target="PER-000001",
            declined=(),
            band="auto",
            signature="given_name= · family_name=",
            score=97.0,
            rule_version=1,
            decided_by="automated-matcher",
            decided_role="data_steward",
            decided_at=decided_at,
            entry_id=None,
            task_id=f"TSK-{tag}{n:05d}",
            drawn_at=decided_at,
        )
        for n in range(len(outcomes))
    ]
    hub.store.apply_work(WorkWrites(entity, samples=tuple(samples)))
    reviews = tuple(
        SampleReview(
            sample_id=s.sample_id,
            entity=entity,
            origin="automated",
            band="auto",
            signature=s.signature,
            answer="PER-000001" if agreed else "none",
            agreed=agreed,
            reviewed_by=COORDINATOR.name,
            reviewed_role=COORDINATOR.role,
            reviewed_at=reviewed_at + timedelta(seconds=n),
            review_entry_id=None,
        )
        for n, (s, agreed) in enumerate(zip(samples, outcomes, strict=True))
    )
    hub.store.apply_work(WorkWrites(entity, reviews=reviews))


def trip_audits(hub: Hub, after_version: int) -> list[dict]:
    """The audit change sets of every commit after `after_version`."""
    versions = list(range(after_version + 1, hub.store.last_commit_version() + 1))
    ids = [c.change_set_id for c in hub.store.commits_by_version(versions)]
    return list(hub.store.change_sets(ids).values())


@pytest.fixture
def clocked(hub: Hub) -> Iterator[tuple[Hub, FakeClock]]:
    clock = FakeClock(current=datetime(2026, 3, 2, 14, 30, tzinfo=UTC))
    opened = rehub(hub, clock=clock)
    try:
        yield opened, clock
    finally:
        opened.close()


# ---------------------------------------------------------------------------------------------- agreement


def test_two_disagreements_in_twenty_do_not_trip_and_three_do(hub: Hub) -> None:
    now = datetime.now(UTC)
    reviewed(hub, [True] * 18 + [False] * 2, decided_at=now, reviewed_at=now, entity="person")
    reviewed(hub, [True] * 17 + [False] * 3, decided_at=now, reviewed_at=now, entity="organisation", tag="B")
    assert hub.breaker.check_agreement("person") is None
    assert hub.breaker.demoted("person") is None
    tripped = hub.breaker.check_agreement("organisation")
    assert tripped is not None and tripped.state == "demoted" and tripped.trigger == "agreement"
    assert tripped.figures == {"agreed": 17, "reviewed": 20, "threshold": 0.95, "window": 100, "bound": 0.938}
    audit = hub.store.change_sets([tripped.trip_change_set])[tripped.trip_change_set]
    assert (
        audit["action"],
        audit["actor"],
        audit["actor_kind"],
        audit["reason"],
        audit["commit_version"],
    ) == (
        "breaker_trip",
        "quality-breaker",
        "automated",
        "agreement_low",
        None,
    )
    assert audit["evidence"]["band"] == "auto" and audit["evidence"]["reviewed"] == 20
    assert hub.breaker.check_agreement("organisation") is None  # a demoted band is not tripped again


def test_the_window_and_the_bound_hold(hub: Hub) -> None:
    narrow = rehub(hub, breaker_window=50)
    try:
        long_ago = datetime.now(UTC) - timedelta(days=30)
        now = datetime.now(UTC)
        reviewed(narrow, [False] * 40, decided_at=long_ago, reviewed_at=long_ago, tag="O")
        reviewed(narrow, [True] * 45 + [False] * 5, decided_at=now, reviewed_at=now, tag="N")
        assert narrow.breaker.agreement("person") == (
            45,
            50,
        )  # the older disagreements fell out of the window
        assert narrow.breaker.check_agreement("person") is None  # 5 of 50: not confidently below 95%
        reviewed(narrow, [False], decided_at=now, reviewed_at=now + timedelta(minutes=5), tag="M")
        assert narrow.breaker.agreement("person") == (44, 50)
        assert narrow.breaker.check_agreement("person") is not None  # 6 of 50
    finally:
        narrow.close()


def test_reviews_of_decisions_made_before_a_restore_never_trip_the_band_again(clocked) -> None:
    hub, clock = clocked
    hub.breaker.demo_trip("person", figures=DEMO)
    before = clock.current
    clock.advance(3600)
    hub.breaker.restore("person", actor=OWNER, reason="cause_fixed")
    clock.advance(3600)
    reviewed(hub, [False] * 20, decided_at=before, reviewed_at=clock.current, tag="P")
    assert hub.breaker.agreement("person") == (0, 0)
    assert hub.breaker.check_agreement("person") is None
    reviewed(hub, [True] * 17 + [False] * 3, decided_at=clock.current, reviewed_at=clock.current, tag="Q")
    assert hub.breaker.check_agreement("person") is not None


# ---------------------------------------------------------------------------------------------- volume


def test_volume_needs_its_history_its_minimum_and_its_multiple(clocked) -> None:
    hub, clock = clocked
    breaker = hub.breaker
    start = clock.current
    breaker.count_arrivals({"person": 50_000}, start)
    assert breaker.check_volume("person", start) is None  # no history yet
    for day in range(1, 8):
        at = start + timedelta(days=day)
        breaker.count_arrivals({"person": 100}, at)
        assert breaker.check_volume("person", at) is None
    quiet = start + timedelta(days=8)
    breaker.count_arrivals({"person": 900}, quiet)
    assert breaker.check_volume("person", quiet) is None  # more than 5 times the mean, below 1,000
    spike = start + timedelta(days=8, hours=0, minutes=10)
    breaker.count_arrivals({"person": 600}, spike)
    clock.current = spike
    tripped = breaker.check_volume("person", spike)
    assert tripped is not None and tripped.trigger == "volume"
    # the mean of the same clock hour over the 7 days before: the first day's 50,000 is 8 days back
    assert tripped.figures == {
        "arrivals": 1500,
        "mean": 100.0,
        "multiple": 5.0,
        "days": 7,
        "hour": iso(floor_hour(spike)),
    }
    audit = hub.store.change_sets([tripped.trip_change_set])[tripped.trip_change_set]
    assert audit["reason"] == "volume_spike" and audit["evidence"]["arrivals"] == 1500


def test_a_nightly_batch_never_trips(clocked) -> None:
    hub, clock = clocked
    night = datetime(2026, 3, 2, 2, 10, tzinfo=UTC)
    for day in range(9):
        at = night + timedelta(days=day)
        clock.current = at
        hub.breaker.count_arrivals({"person": 5000}, at)
        assert hub.breaker.check_volume("person", at) is None
    assert hub.breaker.demoted("person") is None


def test_a_load_that_goes_on_after_a_load_expected_restore_does_not_trip_again(clocked) -> None:
    hub, clock = clocked
    start = clock.current
    for day in range(8):
        hub.breaker.count_arrivals({"person": 100}, start + timedelta(days=day))
    load = start + timedelta(days=8)
    clock.current = load
    hub.breaker.count_arrivals({"person": 5000}, load)
    assert hub.breaker.check_volume("person", load) is not None
    clock.advance(600)
    restored = hub.breaker.restore("person", actor=OWNER, reason="load_expected")
    assert restored.watch_since == clock.current and restored.restore_reason == "load_expected"
    for hour in range(1, 72):
        at = load + timedelta(hours=hour)
        clock.current = at
        hub.breaker.count_arrivals({"person": 5000}, at)
        assert hub.breaker.check_volume("person", at) is None
    assert hub.breaker.demoted("person") is None


def test_a_restore_after_an_agreement_trip_leaves_the_volume_trigger_watching(clocked) -> None:
    hub, clock = clocked
    start = clock.current
    for day in range(8):
        hub.breaker.count_arrivals({"person": 100}, start + timedelta(days=day))
    clock.current = start + timedelta(days=8) - timedelta(hours=2)
    hub.breaker.demo_trip("person", figures=DEMO)
    clock.advance(3600)
    restored = hub.breaker.restore("person", actor=OWNER, reason="cause_fixed")
    assert restored.watch_since == start  # the history of the volume trigger is kept
    reload = start + timedelta(days=9)  # a history reload the next day, in the same clock hour
    clock.current = reload
    hub.breaker.count_arrivals({"person": 5000}, reload)
    tripped = hub.breaker.check_volume("person", reload)
    assert tripped is not None and tripped.trigger == "volume"


def test_a_volume_restore_for_a_fixed_cause_rests_for_its_hour_only(clocked) -> None:
    hub, clock = clocked
    start = clock.current
    for day in range(8):
        hub.breaker.count_arrivals({"person": 100}, start + timedelta(days=day))
    spike = start + timedelta(days=8)
    clock.current = spike
    hub.breaker.count_arrivals({"person": 5000}, spike)
    assert hub.breaker.check_volume("person", spike) is not None
    clock.advance(600)
    restored = hub.breaker.restore("person", actor=OWNER, reason="cause_fixed")
    assert restored.watch_since == start
    hub.breaker.count_arrivals({"person": 1000}, clock.current)
    assert hub.breaker.check_volume("person", clock.current) is None  # the hour it was restored in
    later = start + timedelta(days=9)  # the next day's same hour: the spike is part of its mean now
    clock.current = later
    hub.breaker.count_arrivals({"person": 5000}, later)
    assert hub.breaker.check_volume("person", later) is not None


def test_days_with_only_an_initial_load_are_no_history(clocked) -> None:
    hub, clock = clocked
    start = clock.current
    hub.breaker.count_arrivals({"person": 0}, start)  # an initial load: the row, and nothing counted
    assert hub.breaker.state("person") is not None
    ordinary = start + timedelta(days=8)
    clock.current = ordinary
    hub.breaker.count_arrivals({"person": 1500}, ordinary)
    assert hub.breaker.volume("person", ordinary)[2] is False
    assert hub.breaker.check_volume("person", ordinary) is None
    for day in range(1, 8):  # a week of counted arrivals makes the history
        at = ordinary + timedelta(days=day)
        clock.current = at
        hub.breaker.count_arrivals({"person": 1500}, at)
        assert hub.breaker.check_volume("person", at) is None
    assert hub.breaker.volume("person", clock.current)[2] is True


def test_arrival_counts_its_hour_and_leaves_initial_loads_aside(hub: Hub) -> None:
    land(hub, mini_world(persons=4, organisations=1, initial=True).rows)
    arrive(hub)
    hour = floor_hour(datetime.now(UTC))
    hours = [hour - timedelta(hours=1), hour]
    assert hub.store.arrival_hours("person", hours) == {}
    assert hub.breaker.state("person") is not None  # watched from its first arrival
    land(hub, [row("crm", crm_person_key(1), "person", person_payload(1), at=T0 + timedelta(hours=2))])
    arrive(hub)
    assert sum(hub.store.arrival_hours("person", hours).values()) == 1


# ---------------------------------------------------------------------------------------------- demotion


def test_while_demoted_automatic_band_arrivals_wait_for_a_steward(hub: Hub) -> None:
    workbench_world(hub)
    bands = hub.registry.published("person").match.bands
    versions = hub.store.rule_set_versions("person", "match")
    tripped = hub.breaker.demo_trip("person", figures=DEMO)
    assert tripped is not None
    before = hub.store.last_commit_version()
    distinct = person_payload(40, given_name="Ysmay", family_name="Thornbury", person_ref=person_ref(1040))
    land(
        hub,
        [
            # an automatic link to an existing golden record
            row("crm", crm_person_key(1), "person", person_payload(1), at=T0 + timedelta(hours=2)),
            # two new records the matcher would have joined
            row(
                "hr",
                hr_key(50),
                "person",
                person_payload(50, person_ref=person_ref(1050)),
                at=T0 + timedelta(hours=2),
                version=1,
            ),
            row("crm", crm_person_key(50), "person", person_payload(50), at=T0 + timedelta(hours=2)),
            # a distinct arrival, and an update from a linked record
            row("hr", hr_key(40), "person", distinct, at=T0 + timedelta(hours=2), version=1),
            row(
                "hr",
                hr_key(2),
                "person",
                person_payload(2, person_ref=person_ref(1002), city="Silverwick"),
                at=T0 + timedelta(hours=2),
                version=2,
            ),
        ],
    )
    report = arrive(hub)
    assert (report.demoted, report.created, report.updated, report.linked) == (3, 1, 1, 0)
    waiting = {t.source: t for t in open_tasks(hub, "person") if t.reason == "breaker_demoted"}
    assert set(waiting) == {
        SourceKey("crm", crm_person_key(1)),
        SourceKey("hr", hr_key(50)),
        SourceKey("crm", crm_person_key(50)),
    }
    assert all(
        t.kind == "review" and t.evidence["breaker"] == tripped.trip_change_set for t in waiting.values()
    )
    assert waiting[SourceKey("crm", crm_person_key(1))].master_ids == (
        master_of(hub, "person", "hr", hr_key(1)),
    )
    for audit in trip_audits(hub, before):
        assert "rule1:auto_band" not in audit["evidence"]["clauses"]
    # two new records have no golden record to decline: "Not a match" is off, and the case says why
    alone = waiting[SourceKey("hr", hr_key(50))]
    case = hub.decisions.case(alone.task_id, actor=STEWARD)
    assert case.candidates == () and case.notice == NOTICE_BREAKER_WAIT
    assert case.reason_text.startswith(
        f"This record would have formed a golden record with crm:{crm_person_key(50)} automatically"
    )
    # nobody can settle it before a restore, so it waits for a data owner and never breaches
    assert alone.due_at == WAITS_FOR_RESTORE and not case.row.breaching
    assert waiting[SourceKey("crm", crm_person_key(1))].due_at < WAITS_FOR_RESTORE  # a steward can link it
    clock = hub.inbox.clock
    later = clock() + timedelta(days=30)
    hub.inbox.clock = lambda: later
    try:
        breaching = {r.task_id for r in hub.inbox.page("breaching", actor=STEWARD).rows}
    finally:
        hub.inbox.clock = clock
    assert (
        alone.task_id not in breaching and waiting[SourceKey("crm", crm_person_key(1))].task_id in breaching
    )
    not_a_match = next(a for a in case.actions if a.decision == "not_a_match")
    assert (not_a_match.enabled, not_a_match.why_not) == (False, NOTICE_BREAKER_WAIT)
    assert (
        case.paused is not None and case.paused.entity == "person" and case.paused.figures["reviewed"] == 40
    )
    assert case.row.reason == "paused by the breaker"
    assert hub.inbox.health(actor=STEWARD).paused == (case.paused,)
    # the breaker never touched a band or a rule set
    assert hub.registry.published("person").match.bands == bands
    assert hub.store.rule_set_versions("person", "match") == versions
    assert hub.breaker.demoted("organisation") is None


def test_a_trip_between_chunks_stops_the_automatic_links_that_follow(
    hub: Hub, monkeypatch: pytest.MonkeyPatch
) -> None:
    workbench_world(hub, persons=12)
    monkeypatch.setattr(capacity, "COMMIT_CHUNK_ROWS", 3)
    copies = [
        row("crm", crm_person_key(i), "person", person_payload(i), at=T0 + timedelta(hours=2))
        for i in (1, 3, 5, 7, 9, 11)
    ]
    land(hub, copies)
    trips: list[int] = []

    def trip(point: str) -> None:
        if point == "after_chunk" and not trips:
            trips.append(hub.store.last_commit_version())
            hub.breaker.demo_trip("person", figures=DEMO)

    hub.arrival.fault = trip
    report = arrive(hub)
    hub.arrival.fault = None
    assert trips and report.demoted >= 1  # the page planned again: what chunk 1 linked stays linked
    for audit in trip_audits(hub, trips[0]):
        assert "rule1:auto_band" not in audit["evidence"]["clauses"]
    linked = [c for c in copies if master_of(hub, "person", c.source_system, c.source_key)]
    waiting = {t.source for t in open_tasks(hub, "person") if t.reason == "breaker_demoted"}
    assert len(linked) + len(waiting) == len(copies) and linked and waiting


def test_the_commit_refuses_an_automatic_link_while_the_band_is_demoted(hub: Hub) -> None:
    from mdm.models.changes import LinkSource
    from mdm.services.arrival import automated_change_set

    workbench_world(hub)
    hub.breaker.demo_trip("person", figures=DEMO)
    model = hub.registry.published("person")
    target = master_of(hub, "person", "hr", hr_key(1))
    assert target is not None
    item = LinkSource(SourceKey("crm", crm_person_key(1)), target, None, "rule1:auto_band")
    cs = automated_change_set(
        "person",
        hub.authority.automated_authority(model, ["rule1:auto_band"]),
        [item],
        planning_version=hub.store.last_commit_version(),
    )
    with pytest.raises(Conflict) as refused:
        hub.commit.apply(cs)
    assert refused.value.code == "breaker_demoted"
    # inside the transaction too: the band is held, and a demoted one refused
    with hub.store.commit_scope():
        assert hub.store.hold_band("person", "auto") is False
        assert hub.store.hold_band("organisation", "auto") is True


# ---------------------------------------------------------------------------------------------- restore


def test_only_a_data_owner_restores_with_a_reason_code(hub: Hub) -> None:
    tripped = hub.breaker.demo_trip("person", figures=DEMO)
    for actor in (STEWARD, COORDINATOR, TECHNICAL, ADMINISTRATOR, CONSUMER):
        with pytest.raises(Forbidden) as refused:
            hub.breaker.restore("person", actor=actor, reason="cause_fixed")
        assert refused.value.code == "forbidden"
    with pytest.raises(Forbidden) as bad:
        hub.breaker.restore("person", actor=OWNER, reason="because I say so")
    assert bad.value.code == "bad_restore_reason"
    with pytest.raises(NotFound) as unknown:
        hub.breaker.restore("vessel", actor=OWNER, reason="cause_fixed")
    assert unknown.value.code == "unknown_entity"
    with pytest.raises(Conflict) as normal:
        hub.breaker.restore("organisation", actor=OWNER, reason="cause_fixed")
    assert normal.value.code == "not_demoted"
    restored = hub.breaker.restore("person", actor=OWNER, reason="false_alarm")
    assert (restored.state, restored.restored_by, restored.restored_role, restored.restore_reason) == (
        "normal",
        OWNER.name,
        "data_owner",
        "false_alarm",
    )
    audit = hub.store.change_sets([restored.restore_change_set])[restored.restore_change_set]
    assert (audit["action"], audit["actor"], audit["actor_role"], audit["reason"]) == (
        "breaker_restore",
        OWNER.name,
        "data_owner",
        "false_alarm",
    )
    assert audit["evidence"]["trip_change_set"] == tripped.trip_change_set
    assert hub.breaker.demoted("person") is None


def test_the_next_arrival_hands_the_waiting_records_back(hub: Hub) -> None:
    workbench_world(hub)
    hub.breaker.demo_trip("person", figures=DEMO)
    pair = [
        row(
            "hr",
            hr_key(50),
            "person",
            person_payload(50, person_ref=person_ref(1050)),
            at=T0 + timedelta(hours=2),
            version=1,
        ),
        row("crm", crm_person_key(50), "person", person_payload(50), at=T0 + timedelta(hours=2)),
    ]
    copy = row("crm", crm_person_key(1), "person", person_payload(1), at=T0 + timedelta(hours=2))
    land(hub, [*pair, copy])
    arrive(hub)
    waiting = {t.source: t for t in open_tasks(hub, "person") if t.reason == "breaker_demoted"}
    staged_task = waiting[SourceKey("crm", crm_person_key(1))]
    staged = hub.tray.stage(staged_task.task_id, "link", actor=STEWARD, **seen(hub, staged_task.task_id))
    hub.breaker.restore("person", actor=OWNER, reason="cause_fixed")
    report = arrive(hub)
    assert report.handed_back == 2
    for source in (SourceKey("hr", hr_key(50)), SourceKey("crm", crm_person_key(50))):
        assert hub.store.tasks_by_id([waiting[source].task_id])[waiting[source].task_id].status == "closed"
    # the pair settles as the restored band would have settled it: one golden record
    assert master_of(hub, "person", "hr", hr_key(50)) == master_of(hub, "person", "crm", crm_person_key(50))
    assert master_of(hub, "person", "hr", hr_key(50)) is not None
    # the task whose decision waits in the tray is left to it
    assert hub.store.tasks_by_id([staged_task.task_id])[staged_task.task_id].status == "open"
    window_passed(hub)
    assert hub.tray.flush().committed == 1
    assert hub.store.tray_entries([staged.entry_id])[staged.entry_id].status == "committed"
    assert arrive(hub).handed_back == 0


def test_arrivals_after_a_restore_settle_as_on_a_store_that_never_tripped(make_store, engine: str) -> None:
    world = mini_world(persons=10, organisations=3)
    later = [
        row(
            "hr",
            hr_key(60),
            "person",
            person_payload(60, person_ref=person_ref(1060)),
            at=T0.replace(day=8),
            version=1,
        ),
        row("crm", crm_person_key(60), "person", person_payload(60), at=T0.replace(day=8)),
        row("crm", crm_person_key(3), "person", person_payload(3), at=T0.replace(day=8)),
    ]
    hubs = []
    for tripped in (True, False):
        store = make_store(engine)
        hub = open_hub(store.settings, store, engine)
        land(hub, world.rows)
        arrive(hub)
        if tripped:
            hub.breaker.demo_trip("person", figures=DEMO)
            hub.breaker.restore("person", actor=OWNER, reason="cause_fixed")
        land(hub, later)
        arrive(hub)
        hubs.append(hub)
    for entity in ("person", "organisation"):
        assert clusters(hubs[0], entity) == clusters(hubs[1], entity)
    assert sorted((t.kind, t.reason) for t in open_tasks(hubs[0])) == sorted(
        (t.kind, t.reason) for t in open_tasks(hubs[1])
    )


# ---------------------------------------------------------------------------------------------- authority


def test_the_breaker_and_the_matcher_take_only_what_names_them() -> None:
    for action in ("link", "detach", "merge", "restore_breaker", "arrival"):
        with pytest.raises(Forbidden):
            require(QUALITY_BREAKER, action)
    require(QUALITY_BREAKER, "trip_breaker")
    for action in ACTIONS:
        assert allowed(AUTOMATED_MATCHER, action) == (action == "arrival"), action
        assert allowed(QUALITY_BREAKER, action) == (action == "trip_breaker"), action
    # a person is never the breaker, whatever the name
    impostor = Actor(QUALITY_BREAKER.name, "person", "coordinating_steward")
    with pytest.raises(Forbidden):
        require(impostor, "trip_breaker")


def test_the_demo_trip_is_local_only(hub: Hub) -> None:
    shared = Settings(duckdb_path=":memory:", platform_signals=("DATABRICKS_APP_NAME",), sample_share=0.0)
    breaker = BreakerService(shared, hub.store, hub.registry)
    with pytest.raises(PlatformRefused) as refused:
        breaker.demo_trip("person", figures=DEMO)
    assert refused.value.code == "demo_only"
    assert hub.breaker.demoted("person") is None


def test_the_status_says_what_trips_the_band(hub: Hub) -> None:
    workbench_world(hub)
    hub.breaker.demo_trip("organisation", figures=DEMO)
    with pytest.raises(Forbidden):
        hub.breaker.status(["person"], actor=CONSUMER)
    person, organisation = hub.breaker.status(["person", "organisation"], actor=STEWARD)
    assert (person.state, person.reviewed, person.threshold, person.min_samples, person.history_ready) == (
        "normal",
        0,
        0.95,
        20,
        False,
    )
    assert person.samples_open == 0 and person.watch_since is not None
    assert organisation.state == "demoted" and organisation.figures["reviewed"] == 40


# ---------------------------------------------------------------------------------------------- the settings


CHECKPOINT_SETTINGS = {
    "MDM_SAMPLE_SHARE": ("sample_share", "0.1", 0.1, ["-0.1", "1.5", "nan", "x"]),
    "MDM_SAMPLE_OPEN_CAP": ("sample_open_cap", "300", 300, ["19", "10001", "x"]),
    "MDM_BREAKER_AGREEMENT": ("breaker_agreement", "0.9", 0.9, ["1.2", "-1", "x"]),
    "MDM_BREAKER_WINDOW": ("breaker_window", "200", 200, ["19", "1001", "x"]),
    "MDM_BREAKER_MIN_SAMPLES": ("breaker_min_samples", "10", 10, ["0", "x"]),
    "MDM_BREAKER_SPIKE_MULTIPLE": ("breaker_spike_multiple", "3", 3.0, ["1", "0.5", "1001", "x"]),
    "MDM_BREAKER_SPIKE_DAYS": ("breaker_spike_days", "3", 3, ["0", "8", "x"]),
    "MDM_BREAKER_SPIKE_MIN": ("breaker_spike_min", "50", 50, ["0", "x"]),
}


def test_on_a_shared_store_the_checkpoint_cannot_be_switched_off(hub: Hub) -> None:
    local = Settings(duckdb_path=":memory:", sample_share=0.0, breaker_agreement=0.0)
    local.validate_checkpoint_in_force()  # a local store may do without it
    key = "k" * 16
    shared = Settings(duckdb_path=":memory:", platform_signals=("DATABRICKS_APP_NAME",), sample_key=key)
    shared.validate_checkpoint_in_force()
    for changes, variable in (
        ({"sample_share": 0.0}, "MDM_SAMPLE_SHARE"),
        ({"breaker_agreement": 0.4}, "MDM_BREAKER_AGREEMENT"),
        ({"sample_key": ""}, "MDM_SAMPLE_KEY"),
        ({"sample_key": "k" * 15}, "MDM_SAMPLE_KEY"),
    ):
        with pytest.raises(ConfigError) as refused:
            shared.with_(**changes).validate_checkpoint_in_force()
        assert refused.value.fields == {"variable": variable}
    # arrival and the tray's flush refuse to run while it is off, and no decision leaves the tray
    off = Settings.from_env({"DATABRICKS_APP_NAME": "mdm", "MDM_DUCKDB_PATH": ":memory:"})
    assert key not in repr(shared) and off.sample_key == ""
    hub.arrival.settings, hub.tray.settings = off, off
    try:
        with pytest.raises(ConfigError):
            hub.arrival.run(started_by=OWNER)
        with pytest.raises(ConfigError):
            hub.tray.flush()
    finally:
        hub.arrival.settings, hub.tray.settings = hub.settings, hub.settings


def test_the_checkpoint_settings_parse_and_their_bounds_are_refused_by_name() -> None:
    default = Settings.from_env({})
    assert (
        default.sample_share,
        default.sample_open_cap,
        default.breaker_agreement,
        default.breaker_window,
        default.breaker_min_samples,
        default.breaker_spike_multiple,
        default.breaker_spike_days,
        default.breaker_spike_min,
    ) == (0.02, 200, 0.95, 100, 20, 5.0, 7, 1000)
    assert dict(default.sla_hours)["quality_sample"] == 72
    for variable, (field, good, parsed, bad) in CHECKPOINT_SETTINGS.items():
        assert getattr(Settings.from_env({variable: good}), field) == parsed
        for value in bad:
            with pytest.raises(ConfigError) as refused:
                Settings.from_env({variable: value})
            assert refused.value.fields == {"variable": variable}, (variable, value)
    with pytest.raises(ConfigError) as window:
        Settings().with_(breaker_min_samples=150)  # the window and the cap hold at least the minimum
    assert window.value.fields["variable"] in ("MDM_SAMPLE_OPEN_CAP", "MDM_BREAKER_WINDOW")
