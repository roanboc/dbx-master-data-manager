"""The guards on a steward's decision, on both engines: it is taken on the case the steward saw; a candidate
a cannot-link rule blocks is shown but never linked; nobody claims, snoozes or escalates a task whose
decision waits in the tray; the flush checks the golden records again; a declined record's survivor stays
declined; and a decision that publishes nothing still shows on the record's timeline (owner: SERVICES)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from mdm.models.canonical import iso, utcnow
from mdm.models.errors import Conflict, Forbidden
from mdm.models.records import SourceKey
from mdm.models.workbench import MatchLabel
from mdm.services.context import Hub
from tests.helpers import (
    COORDINATOR,
    STEWARD,
    T0,
    arrive,
    finance_key,
    golden_pair,
    golden_rows,
    hr_key,
    land,
    master_of,
    open_tasks,
    org_payload,
    org_reg,
    person_payload,
    person_ref,
    person_review,
    row,
    seen,
    standalone_organisation,
    task_of,
    workbench_world,
)


def window_passed(hub: Hub) -> None:
    offset = timedelta(seconds=hub.settings.undo_seconds + 1)
    current = hub.tray.clock
    hub.tray.clock = lambda: current() + offset


@pytest.fixture
def review(hub: Hub):
    workbench_world(hub)
    source = person_review(hub)
    return hub, task_of(hub, kind="review", source=source), source


def blocked_review(hub: Hub):
    """An Organisation record whose registered ID a cannot-link rule keeps apart from the golden record its
    name matches: (its task, the record)."""
    workbench_world(hub)
    base = org_payload(1)
    base.pop("registered_id")
    land(hub, [row("crm", "C0900100", "organisation", base, at=T0 + timedelta(hours=2))])
    arrive(hub)
    conflicting = {**base, "registered_id": org_reg(778)}
    land(hub, [row("crm", "C0900101", "organisation", conflicting, at=T0 + timedelta(hours=3))])
    arrive(hub)
    source = SourceKey("crm", "C0900101")
    tasks = [t for t in open_tasks(hub) if t.source == source]
    assert tasks, "the conflicting record opens a task"
    return tasks[0], source


# ---------------------------------------------------------------------------------------------- the case seen


def test_a_decision_needs_the_case_the_steward_saw(review) -> None:
    hub, task, _ = review
    for missing in ({}, {"seen_event": None}, {"seen_event": "an-older-event"}):
        with pytest.raises(Conflict) as stale:
            hub.tray.stage(task.task_id, "link", actor=STEWARD, **missing)
        assert stale.value.code == "record_changed"
    assert hub.inbox.row(task.task_id, actor=STEWARD).claimed_by is None  # nothing was claimed
    case = hub.decisions.case(task.task_id, actor=STEWARD)
    assert case.task_version == iso(task.updated_at)
    entry = hub.tray.stage(task.task_id, "link", actor=STEWARD, seen_event=case.event_id)
    assert entry.status == "staged"


def test_a_decision_on_golden_records_needs_the_task_version_seen(hub: Hub) -> None:
    workbench_world(hub)
    golden_pair(hub)
    task = task_of(hub, kind="possible_duplicate")
    for wrong in ({}, {"seen_task": iso(task.updated_at - timedelta(seconds=1))}):
        with pytest.raises(Conflict) as stale:
            hub.tray.stage(task.task_id, "keep_apart", actor=STEWARD, **wrong)
        assert stale.value.code == "task_changed"
    case = hub.decisions.case(task.task_id, actor=STEWARD)
    assert (
        hub.tray.stage(task.task_id, "keep_apart", actor=STEWARD, seen_task=case.task_version).target is None
    )


# ---------------------------------------------------------------------------------------------- a blocked candidate


def test_a_candidate_a_cannot_link_rule_blocks_is_shown_but_never_linked(hub: Hub) -> None:
    task, _source = blocked_review(hub)
    case = hub.decisions.case(task.task_id, actor=STEWARD)
    blocked = [c for c in case.candidates if c.blocked_by is not None]
    assert blocked, "the golden record with the other registered ID is shown"
    assert "registered IDs differ" in blocked[0].blocked_by
    assert case.default_candidate != blocked[0].master_id
    link = next(a for a in case.actions if a.decision == "link" and a.target == blocked[0].master_id)
    assert not link.enabled and "cannot-link rule" in (link.why_not or "")
    with pytest.raises(Forbidden) as refused:
        hub.tray.stage(
            task.task_id, "link", actor=STEWARD, target=blocked[0].master_id, **seen(hub, task.task_id)
        )
    assert refused.value.code == "cannot_link"
    if case.default_candidate is None:
        with pytest.raises(Forbidden) as by_default:
            hub.tray.stage(task.task_id, "link", actor=STEWARD, **seen(hub, task.task_id))
        assert by_default.value.code in ("cannot_link", "candidate_not_offered", "close_call")
    # "Not a match" stays open to the steward
    assert (
        hub.tray.stage(task.task_id, "not_a_match", actor=STEWARD, **seen(hub, task.task_id)).status
        == "staged"
    )


def test_the_flush_checks_a_cannot_link_rule_again(review) -> None:
    hub, task, source = review
    case = hub.decisions.case(task.task_id, actor=STEWARD)
    target = case.default_candidate
    assert target is not None
    entry = hub.tray.stage(task.task_id, "link", actor=STEWARD, **seen(hub, task.task_id))
    calls: list[str] = []

    def blocked(entity, record, master_id):
        calls.append(master_id)
        return "cannot_link:person_ref"

    hub.decisions.matching.blocked_by = blocked
    window_passed(hub)
    assert hub.tray.flush().outcomes == {"target_changed": 1}
    assert calls == [target] and master_of(hub, "person", source.system, source.key) is None
    assert hub.store.tray_entries([entry.entry_id])[entry.entry_id].status == "failed"


# ---------------------------------------------------------------------------------------------- the tray holds the task


def test_nobody_claims_snoozes_or_escalates_a_task_whose_decision_waits(review) -> None:
    hub, task, _ = review
    hub.tray.stage(task.task_id, "link", actor=STEWARD, **seen(hub, task.task_id))
    case = hub.decisions.case(task.task_id, actor=STEWARD)
    held = {a.decision: a for a in case.actions}
    for name in ("claim", "snooze", "escalate"):
        assert not held[name].enabled and "in the tray" in (held[name].why_not or "")
    assert held["undo"].enabled
    for actor in (STEWARD, COORDINATOR):
        with pytest.raises(Conflict) as claim:
            hub.inbox.claim(task.task_id, actor=actor)
        assert claim.value.code == "already_staged" and claim.value.fields["mine"] == (actor is STEWARD)
        with pytest.raises(Conflict) as snooze:
            hub.inbox.snooze(task.task_id, actor=actor, hours=4)
        assert snooze.value.code == "already_staged"
        with pytest.raises(Conflict) as escalate:
            hub.inbox.escalate(task.task_id, actor=actor, reason="second_opinion")
        assert escalate.value.code == "already_staged"
    stored = hub.store.tasks_by_id([task.task_id])[task.task_id]
    assert (stored.claimed_by, stored.snoozed_until, stored.escalated_at) == (STEWARD.name, None, None)


def test_a_stage_that_loses_the_lock_leaves_no_claim(review, monkeypatch) -> None:
    hub, task, _ = review

    def taken(entry, locks):
        raise Conflict(["x"], code="already_staged", mine=False)

    monkeypatch.setattr(hub.store, "stage_tray", taken)
    with pytest.raises(Conflict):
        hub.tray.stage(task.task_id, "link", actor=STEWARD, **seen(hub, task.task_id))
    assert hub.store.tasks_by_id([task.task_id])[task.task_id].claimed_by is None
    # a claim the steward held before stays theirs
    hub.inbox.claim(task.task_id, actor=STEWARD)
    with pytest.raises(Conflict):
        hub.tray.stage(task.task_id, "link", actor=STEWARD, **seen(hub, task.task_id))
    assert hub.store.tasks_by_id([task.task_id])[task.task_id].claimed_by == STEWARD.name


def test_a_failed_decision_releases_only_its_own_stewards_claim(review, monkeypatch) -> None:
    hub, task, _ = review
    entry = hub.tray.stage(task.task_id, "link", actor=STEWARD, **seen(hub, task.task_id))
    hub.store.release_task(task.task_id, STEWARD.name)
    now = utcnow()
    assert hub.store.claim_task(task.task_id, COORDINATOR.name, now, now - timedelta(minutes=1), staging=True)

    def moved(staged, *, now):
        raise Conflict(["x"], code="record_changed")

    monkeypatch.setattr(hub.decisions, "execute", moved)
    window_passed(hub)
    assert hub.tray.flush().outcomes == {"record_changed": 1}
    assert hub.store.tray_entries([entry.entry_id])[entry.entry_id].status == "failed"
    assert hub.store.tasks_by_id([task.task_id])[task.task_id].claimed_by == COORDINATOR.name


# ---------------------------------------------------------------------------------------------- the target at flush


def test_a_target_that_changed_while_the_decision_waited_fails_it(review) -> None:
    hub, task, source = review
    case = hub.decisions.case(task.task_id, actor=STEWARD)
    target = case.default_candidate
    entry = hub.tray.stage(task.task_id, "link", actor=STEWARD, **seen(hub, task.task_id))
    assert entry.subject["row_versions"] == {target: hub.store.golden("person", [target])[target].row_version}
    before = hub.store.golden("person", [target])[target].row_version
    payload = person_payload(4, phone="0999 5550123", person_ref=person_ref(1004), employer=finance_key(1))
    land(hub, [row("hr", hr_key(4), "person", payload, version=2, at=utcnow())])
    arrive(hub)
    assert hub.store.golden("person", [target])[target].row_version != before
    window_passed(hub)
    assert hub.tray.flush().outcomes == {"target_changed": 1}
    assert master_of(hub, "person", source.system, source.key) is None


def test_keep_an_orphan_fails_when_its_record_is_no_longer_active(hub: Hub) -> None:
    workbench_world(hub)
    kept = standalone_organisation(hub)
    land(hub, [row("crm", kept.key, "organisation", {}, op="delete", at=utcnow())])
    arrive(hub)
    task = task_of(hub, kind="orphan")
    entry = hub.tray.stage(task.task_id, "keep_orphan", actor=STEWARD, **seen(hub, task.task_id))
    assert set(entry.subject["row_versions"]) == {task.master_ids[0]}
    hub.lifecycle.retire(
        "organisation", task.master_ids[0], maker=STEWARD, checker=COORDINATOR, reason="gone"
    )
    window_passed(hub)
    assert hub.tray.flush().outcomes in ({"target_changed": 1}, {"task_closed": 1})


# ---------------------------------------------------------------------------------------------- declined, then merged


def test_a_declined_golden_records_survivor_is_declined_too(review) -> None:
    hub, task, source = review
    case = hub.decisions.case(task.task_id, actor=STEWARD)
    declined = case.default_candidate
    survivor = next(m for m in golden_rows(hub, "person") if m != declined)
    hub.store.put_labels(
        [
            MatchLabel(
                "person",
                source.text(),
                declined,
                "not_a_match",
                1,
                None,
                None,
                None,
                None,
                None,
                STEWARD.name,
                STEWARD.role,
                utcnow(),
            )
        ]
    )
    hub.lifecycle.merge("person", survivor, declined, maker=STEWARD, checker=COORDINATOR, reason="duplicate")
    again = hub.decisions.case(task.task_id, actor=STEWARD)
    assert survivor not in [c.master_id for c in again.candidates]
    assert declined not in [c.master_id for c in again.candidates]


# ---------------------------------------------------------------------------------------------- the timeline


def test_a_decision_that_publishes_nothing_shows_on_the_records_timeline(hub: Hub) -> None:
    workbench_world(hub)
    golden_pair(hub)
    task = task_of(hub, kind="possible_duplicate")
    lower, higher = sorted(task.master_ids)
    version = hub.store.last_commit_version()
    hub.tray.stage(task.task_id, "keep_apart", actor=STEWARD, **seen(hub, task.task_id))
    window_passed(hub)
    assert hub.tray.flush().outcomes == {"committed": 1}
    assert hub.store.last_commit_version() == version  # nothing was published
    for master, other in ((lower, higher), (higher, lower)):
        events = hub.lookup.timeline("organisation", master, actor=STEWARD).events
        decided = [e for e in events if e.kind == "decided"]
        assert [(e.headline, e.published, e.actor, e.automated) for e in decided] == [
            (f"Kept apart from {other}", False, "Data steward", False)
        ]
        assert events[0] is decided[0]  # the newest first
        assert STEWARD.name not in decided[0].authority


def test_not_a_match_shows_on_each_declined_candidates_timeline(review) -> None:
    hub, task, source = review
    declined = [c.master_id for c in hub.decisions.case(task.task_id, actor=STEWARD).candidates]
    hub.tray.stage(task.task_id, "not_a_match", actor=STEWARD, **seen(hub, task.task_id))
    window_passed(hub)
    hub.tray.flush()
    for master in declined:
        events = hub.lookup.timeline("person", master, actor=STEWARD).events
        assert f"Not a match: {source.text()} kept out" in [e.headline for e in events if not e.published]
