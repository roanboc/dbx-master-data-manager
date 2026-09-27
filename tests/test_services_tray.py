"""The undo tray's promises on both engines: an undo never reaches the published tables, a flush commits once
through the commit path, a crash never loses or doubles a decision, and the checks that matter run inside the
commit's own transaction (owner: SERVICES, B.6.6, decision 19, B.9.1)."""

from __future__ import annotations

import threading
from datetime import timedelta

import pytest

from mdm.engine.cluster import Resolution
from mdm.models.canonical import utcnow
from mdm.models.changes import WorkWrites
from mdm.models.errors import Conflict, Forbidden
from mdm.models.records import SourceKey
from mdm.models.safety import SAFE_TEXT_RE, safe
from mdm.services.arrival import _PagePlan
from mdm.services.context import Hub
from tests.conftest import THREAD_TIMEOUT
from tests.helpers import (
    COORDINATOR,
    STEWARD,
    all_changes,
    arrive,
    golden_pair,
    golden_rows,
    held_name_change,
    land,
    master_of,
    open_tasks,
    organisation_close_call,
    person_payload,
    person_review,
    row,
    seen,
    standalone_organisation,
    task_of,
    workbench_world,
)


class Crash(RuntimeError):
    pass


def window_passed(hub: Hub, seconds: float | None = None) -> None:
    """Moves the tray's clock past every staged decision's deadline."""
    offset = timedelta(seconds=seconds if seconds is not None else hub.settings.undo_seconds + 1)
    current = hub.tray.clock
    hub.tray.clock = lambda: current() + offset


def published(hub: Hub, entity: str) -> tuple:
    """What a listener can see: the last commit version, the change rows, and every golden row."""
    return (
        hub.store.last_commit_version(),
        len(all_changes(hub)),
        {m: (g.row_version, dict(g.values)) for m, g in golden_rows(hub, entity).items()},
    )


@pytest.fixture
def review(hub: Hub):
    """The Person review: (hub, its task, the crm record)."""
    workbench_world(hub)
    source = person_review(hub)
    return hub, task_of(hub, kind="review", source=source), source


def audit_of(hub: Hub, entry_id: str) -> dict:
    entry = hub.store.tray_entries([entry_id])[entry_id]
    return hub.store.change_sets([entry.change_set_id])[entry.change_set_id]


# ---------------------------------------------------------------------------------------------- stage, undo, flush


def test_an_undo_before_the_deadline_never_reaches_the_published_tables(review) -> None:
    hub, task, _ = review
    before = published(hub, "person")
    entry = hub.tray.stage(task.task_id, "link", actor=STEWARD, **seen(hub, task.task_id))
    assert entry.deadline - entry.staged_at == timedelta(seconds=hub.settings.undo_seconds)
    assert hub.inbox.row(task.task_id, actor=STEWARD).claimed_by == "you"
    undone = hub.tray.undo(entry.entry_id, actor=STEWARD)
    assert (undone.status, undone.outcome) == ("undone", "undone")
    window_passed(hub)
    assert hub.tray.flush().committed == 0
    assert published(hub, "person") == before
    assert hub.store.tasks_by_id([task.task_id])[task.task_id].status == "open"
    assert hub.inbox.row(task.task_id, actor=STEWARD).claimed_by == "you"  # the claim stays
    with pytest.raises(Conflict) as again:
        hub.tray.undo(entry.entry_id, actor=STEWARD)
    assert again.value.code == "already_settled" and again.value.fields["status"] == "undone"
    # the lock is free: the same decision stages again
    assert hub.tray.stage(task.task_id, "link", actor=STEWARD, **seen(hub, task.task_id)).status == "staged"


def test_a_flush_after_the_deadline_commits_once(review) -> None:
    hub, task, source = review
    version = hub.store.last_commit_version()
    entry = hub.tray.stage(task.task_id, "link", actor=STEWARD, **seen(hub, task.task_id))
    assert hub.tray.flush().committed == 0  # before the deadline: nothing
    assert hub.store.last_commit_version() == version
    window_passed(hub)
    report = hub.tray.flush(started_by=COORDINATOR)
    assert (report.committed, report.failed, report.outcomes) == (1, 0, {"committed": 1})
    assert hub.store.last_commit_version() == version + 1
    assert master_of(hub, "person", source.system, source.key) == task.master_ids[0]
    settled = hub.store.tray_entries([entry.entry_id])[entry.entry_id]
    assert (settled.status, settled.outcome, settled.commit_version) == (
        "committed",
        "committed",
        version + 1,
    )
    assert hub.store.tasks_by_id([task.task_id])[task.task_id].status == "closed"
    (label,) = hub.store.labels_for("person", [source.text()])
    assert (label.label, label.right_ref, label.entry_id, label.decided_by) == (
        "match",
        task.master_ids[0],
        entry.entry_id,
        STEWARD.name,
    )
    audit = audit_of(hub, entry.entry_id)
    assert (audit["action"], audit["actor"], audit["reason"], audit["commit_version"]) == (
        "link",
        STEWARD.name,
        "workbench:link",
        version + 1,
    )
    assert audit["evidence"]["entry_id"] == entry.entry_id and audit["evidence"]["task_id"] == task.task_id
    assert hub.tray.flush().committed == 0  # a second flush finds nothing due
    assert hub.store.last_commit_version() == version + 1
    with pytest.raises(Conflict) as late:
        hub.tray.undo(entry.entry_id, actor=STEWARD)
    assert (late.value.code, late.value.fields["status"], late.value.fields["version"]) == (
        "already_settled",
        "committed",
        version + 1,
    )
    views = hub.tray.entries(actor=STEWARD)
    assert [(v.label, v.status, v.commit_version) for v in views] == [
        (f"Link {source.text()} to {task.master_ids[0]}", "committed", version + 1)
    ]


def test_undo_is_the_stewards_own(review) -> None:
    hub, task, _ = review
    entry = hub.tray.stage(task.task_id, "not_a_match", actor=STEWARD, **seen(hub, task.task_id))
    with pytest.raises(Forbidden) as not_yours:
        hub.tray.undo(entry.entry_id, actor=COORDINATOR)
    assert not_yours.value.code == "not_yours"
    assert hub.tray.undo_for_task(task.task_id, actor=COORDINATOR) is None
    assert hub.tray.undo_last(actor=COORDINATOR) is None
    assert hub.tray.undo_last(actor=STEWARD).entry_id == entry.entry_id
    assert hub.tray.undo_for_task(task.task_id, actor=STEWARD) is None  # nothing waits any more
    again = hub.tray.stage(task.task_id, "not_a_match", actor=STEWARD, **seen(hub, task.task_id))
    assert hub.tray.undo_for_task(task.task_id, actor=STEWARD).entry_id == again.entry_id


def test_two_flushes_at_once_commit_once(review) -> None:
    hub, task, _ = review
    version = hub.store.last_commit_version()
    hub.tray.stage(task.task_id, "link", actor=STEWARD, **seen(hub, task.task_id))
    window_passed(hub)
    other: list = []

    def while_committing(point: str) -> None:
        if point == "before_write" and not other:
            thread = threading.Thread(target=lambda: other.append(hub.tray.flush()))
            thread.start()
            thread.join(THREAD_TIMEOUT)
            assert not thread.is_alive()

    hub.commit.fault = while_committing
    first = hub.tray.flush()
    assert first.committed == 1
    assert other and other[0].skipped_busy and other[0].committed == 0
    assert hub.store.last_commit_version() == version + 1


def test_a_crash_inside_the_commit_leaves_the_decision_staged_and_the_next_flush_commits_it_once(
    review,
) -> None:
    hub, task, source = review
    version = hub.store.last_commit_version()
    entry = hub.tray.stage(task.task_id, "link", actor=STEWARD, **seen(hub, task.task_id))
    window_passed(hub)
    fired: list[str] = []

    def crash(point: str) -> None:
        if point == "in_commit" and not fired:
            fired.append(point)
            raise Crash(point)

    hub.commit.fault = crash
    first = hub.tray.flush()
    assert fired and (first.committed, first.failed) == (0, 0)
    assert hub.store.last_commit_version() == version
    staged = hub.store.tray_entries([entry.entry_id])[entry.entry_id]
    assert (staged.status, staged.attempts) == ("staged", 1)
    assert hub.store.tasks_by_id([task.task_id])[task.task_id].status == "open"
    assert master_of(hub, "person", source.system, source.key) is None
    second = hub.tray.flush()
    assert second.committed == 1 and hub.store.last_commit_version() == version + 1
    assert hub.tray.flush().committed == 0


def test_an_unexpected_failure_settles_after_the_last_attempt(
    review, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub, task, _ = review
    entry = hub.tray.stage(task.task_id, "link", actor=STEWARD, **seen(hub, task.task_id))
    window_passed(hub)

    def broken(*args, **kwargs):
        raise Crash("broken")

    monkeypatch.setattr(hub.decisions, "execute", broken)
    assert hub.tray.flush().outcomes == {}
    assert hub.tray.flush().outcomes == {}
    assert hub.tray.flush().outcomes == {"internal": 1}
    failed = hub.store.tray_entries([entry.entry_id])[entry.entry_id]
    assert (failed.status, failed.outcome, failed.attempts) == ("failed", "internal", 3)
    assert hub.inbox.row(task.task_id, actor=STEWARD).claimed_by is None


# ---------------------------------------------------------------------------------------------- the race


def _review_payload(**changes) -> dict:
    """The Person review's crm record again (its birth date one digit off), with `changes`."""
    held = person_payload(4)
    day = int(held["birth_date"][-2:])
    birth = f"{held['birth_date'][:-2]}{day + 1 if day % 10 != 9 else day - 1:02d}"
    payload = {
        "given_name": held["given_name"],
        "family_name": held["family_name"],
        "birth_date": birth,
        "city": held["city"],
        "country": "XA",
    }
    return {**payload, **changes}


def _land_and_take_in(hub: Hub, source: SourceKey, **changes) -> None:
    """A new event for the record, landed and taken in by arrival's intake (states, not settled)."""
    high = hub.store.landing_max_seq()
    land(hub, [row(source.system, source.key, "person", _review_payload(**changes), at=utcnow())])
    hub.arrival.intake(hub.store.landing_above(high, 10))


def test_a_record_that_moves_inside_the_commit_fails_the_decision_and_publishes_nothing(review) -> None:
    hub, task, source = review
    entry = hub.tray.stage(task.task_id, "link", actor=STEWARD, **seen(hub, task.task_id))
    before = published(hub, "person")
    approved = hub.store.source_states("person", [source])[source].approved_event_id
    window_passed(hub)
    fired: list[str] = []

    def race(point: str) -> None:
        if point == "before_write" and not fired:
            fired.append(point)
            _land_and_take_in(hub, source, city="Brackenmere")

    hub.commit.fault = race
    report = hub.tray.flush()
    assert fired and (report.committed, report.failed, report.outcomes) == (0, 1, {"record_changed": 1})
    failed = hub.store.tray_entries([entry.entry_id])[entry.entry_id]
    assert (failed.status, failed.outcome) == ("failed", "record_changed")
    assert published(hub, "person") == before
    assert hub.store.source_states("person", [source])[source].approved_event_id == approved
    assert hub.store.labels_for("person", [source.text()]) == []
    assert hub.store.tasks_by_id([task.task_id])[task.task_id].status == "open"
    assert hub.inbox.row(task.task_id, actor=STEWARD).claimed_by is None


def test_a_task_closed_inside_the_commit_fails_the_decision(review) -> None:
    hub, task, _ = review
    entry = hub.tray.stage(task.task_id, "not_a_match", actor=STEWARD, **seen(hub, task.task_id))
    window_passed(hub)
    fired: list[str] = []

    def close(point: str) -> None:
        if point == "before_write" and not fired:
            fired.append(point)
            hub.store.apply_work(WorkWrites("person", close_task_ids=(task.task_id,)))

    hub.commit.fault = close
    assert hub.tray.flush().outcomes == {"task_closed": 1}
    assert hub.store.tray_entries([entry.entry_id])[entry.entry_id].outcome == "task_closed"
    assert hub.store.labels_for("person", ["crm:C1900004"]) == []


def test_a_record_that_moved_while_the_decision_waited_hands_the_task_back(review) -> None:
    hub, task, source = review
    entry = hub.tray.stage(task.task_id, "link", actor=STEWARD, **seen(hub, task.task_id))
    land(hub, [row(source.system, source.key, "person", _review_payload(city="Silverwick"), at=utcnow())])
    arrive(hub)  # still a review: the task takes the new event
    before = hub.store.last_commit_version()
    window_passed(hub)
    assert hub.tray.flush().outcomes == {"record_changed": 1}
    assert hub.store.tray_entries([entry.entry_id])[entry.entry_id].status == "failed"
    reopened = hub.store.tasks_by_id([task.task_id])[task.task_id]
    assert reopened.status == "open" and reopened.claimed_by is None
    assert hub.store.last_commit_version() == before
    assert master_of(hub, "person", source.system, source.key) is None


def test_a_target_merged_meanwhile_fails_the_link(review) -> None:
    hub, task, _ = review
    target = task.master_ids[0]
    entry = hub.tray.stage(task.task_id, "link", actor=STEWARD, target=target, **seen(hub, task.task_id))
    survivor = next(m for m in golden_rows(hub, "person") if m != target)
    hub.lifecycle.merge("person", survivor, target, maker=COORDINATOR, checker=STEWARD, reason="duplicate")
    window_passed(hub)
    assert hub.tray.flush().outcomes == {"target_changed": 1}
    assert hub.store.tray_entries([entry.entry_id])[entry.entry_id].outcome == "target_changed"


def test_a_persona_is_refused_on_a_shared_store(review) -> None:
    hub, task, _ = review
    entry = hub.tray.stage(task.task_id, "link", actor=STEWARD, **seen(hub, task.task_id))
    hub.decisions.settings = hub.settings.with_(platform_signals=("DATABRICKS_APP_PORT",))
    window_passed(hub)
    assert hub.tray.flush().outcomes == {"persona_refused": 1}
    assert hub.store.tray_entries([entry.entry_id])[entry.entry_id].outcome == "persona_refused"


# ---------------------------------------------------------------------------------------------- every decision audited


def test_a_held_update_another_source_outranks_is_approved_publishing_nothing(hub: Hub) -> None:
    workbench_world(hub)
    crm = SourceKey("crm", "C000000")
    held_name_change(hub, crm, "Brindle Works Holdings Limited", at=utcnow())
    task = task_of(hub, kind="held", source=crm)
    state = hub.store.source_states("organisation", [crm])[crm]
    assert state.held and state.approved_event_id != state.event_id
    before = published(hub, "organisation")
    entry = hub.tray.stage(task.task_id, "approve_update", actor=STEWARD, **seen(hub, task.task_id))
    window_passed(hub)
    assert hub.tray.flush().outcomes == {"committed": 1}
    assert published(hub, "organisation") == before  # finance outranks crm for the name
    after = hub.store.source_states("organisation", [crm])[crm]
    assert not after.held and after.approved_event_id == state.event_id
    assert after.approved_values["name"] == "Brindle Works Holdings Limited"
    audit = audit_of(hub, entry.entry_id)
    assert (audit["action"], audit["commit_version"], audit["item_count"]) == ("approve_update", None, 1)
    assert hub.store.tasks_by_id([task.task_id])[task.task_id].status == "closed"
    assert hub.tray.flush().committed == 0


def test_approve_and_reject_a_held_update_that_publishes(hub: Hub) -> None:
    workbench_world(hub)
    source = standalone_organisation(hub)
    master = master_of(hub, "organisation", source.system, source.key)
    held_name_change(hub, source, "Quillmere Optical Ltd", at=utcnow())
    task = task_of(hub, kind="held", source=source)
    hub.tray.stage(task.task_id, "approve_update", actor=STEWARD, **seen(hub, task.task_id))
    window_passed(hub)
    assert hub.tray.flush().outcomes == {"committed": 1}
    assert golden_rows(hub, "organisation")[master].values["name"] == "Quillmere Optical Ltd"
    assert not hub.store.source_states("organisation", [source])[source].held

    held_name_change(hub, source, "Quillmere Lenses Ltd", at=utcnow() + timedelta(hours=1))
    rejected = task_of(hub, kind="held", source=source)
    entry = hub.tray.stage(rejected.task_id, "reject_update", actor=STEWARD, **seen(hub, rejected.task_id))
    window_passed(hub)
    assert hub.tray.flush().outcomes == {"committed": 1}
    assert golden_rows(hub, "organisation")[master].values["name"] == "Quillmere Optical Ltd"
    state = hub.store.source_states("organisation", [source])[source]
    assert not state.held and state.approved_values["name"] == "Quillmere Optical Ltd"
    assert audit_of(hub, entry.entry_id)["commit_version"] is None
    # the next event from that source holds the same change again
    held_name_change(hub, source, "Quillmere Lenses Ltd", at=utcnow() + timedelta(hours=2))
    again = task_of(hub, kind="held", source=source)
    assert again.reason == "critical_update_held" and again.task_id != rejected.task_id


def test_a_link_that_holds_already_commits_an_audited_change_set_with_no_items(review) -> None:
    hub, task, source = review
    entry = hub.tray.stage(task.task_id, "link", actor=STEWARD, **seen(hub, task.task_id))
    hub.lifecycle.link(
        "person", source, task.master_ids[0], actor=COORDINATOR, reason="from the command line"
    )
    version = hub.store.last_commit_version()
    window_passed(hub)
    assert hub.tray.flush().outcomes == {"committed": 1}
    assert hub.store.last_commit_version() == version
    audit = audit_of(hub, entry.entry_id)
    assert (audit["action"], audit["commit_version"], audit["item_count"]) == ("link", None, 0)
    assert hub.store.tasks_by_id([task.task_id])[task.task_id].status == "closed"
    assert hub.tray.flush().committed == 0


def test_a_decision_committed_without_its_settlement_is_never_flushed_twice(
    review, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub, task, _ = review
    entry = hub.tray.stage(task.task_id, "not_a_match", actor=STEWARD, **seen(hub, task.task_id))
    original = hub.lifecycle.decide_only

    def dropping_the_work(entity, action, *, actor, reason, work, evidence=None):
        return original(
            entity, action, actor=actor, reason=reason, work=WorkWrites(entity), evidence=evidence
        )

    monkeypatch.setattr(hub.lifecycle, "decide_only", dropping_the_work)
    window_passed(hub)
    assert hub.tray.flush().outcomes == {"not_settled": 1}
    assert hub.store.tray_entries([entry.entry_id])[entry.entry_id].status == "failed"
    assert hub.tray.flush().outcomes == {}


def test_arrival_failing_after_the_commit_leaves_the_decision_committed(review, monkeypatch) -> None:
    hub, task, source = review
    entry = hub.tray.stage(task.task_id, "not_a_match", actor=STEWARD, **seen(hub, task.task_id))

    def broken(*args, **kwargs):
        raise Crash("arrival")

    monkeypatch.setattr(hub.arrival, "settle_records", broken)
    window_passed(hub)
    report = hub.tray.flush()
    assert (report.committed, report.requeued) == (1, 1)
    assert hub.store.tray_entries([entry.entry_id])[entry.entry_id].status == "committed"
    assert hub.store.tasks_by_id([task.task_id])[task.task_id].claimed_by == STEWARD.name
    assert [r[0] for r in hub.store.queue_rows("person", [source])] == [source]
    monkeypatch.undo()
    arrive(hub)  # the next arrival run settles it
    assert hub.store.queue_rows("person", [source]) == []
    assert master_of(hub, "person", source.system, source.key) not in (None, task.master_ids[0])


# ---------------------------------------------------------------------------------------------- labels bind the matcher


def test_not_a_match_hands_the_record_back_to_arrival_which_creates_it(review) -> None:
    hub, task, source = review
    declined = task.master_ids[0]
    entry = hub.tray.stage(task.task_id, "not_a_match", actor=STEWARD, **seen(hub, task.task_id))
    window_passed(hub)
    report = hub.tray.flush()
    assert (report.committed, report.requeued) == (1, 1)
    assert [(lab.label, lab.right_ref) for lab in hub.store.labels_for("person", [source.text()])] == [
        ("not_a_match", declined)
    ]
    assert hub.store.tasks_by_id([task.task_id])[task.task_id].status == "closed"
    assert audit_of(hub, entry.entry_id)["commit_version"] is None
    created = master_of(hub, "person", source.system, source.key)
    assert created is not None and created != declined
    assert hub.store.queue_rows("person", [source]) == []
    commit = next(
        c
        for c in hub.store.commits_by_version(range(1, hub.store.last_commit_version() + 1))
        if c.actor_kind == "automated"
        and hub.store.change_sets([c.change_set_id])[c.change_set_id]["evidence"].get("declined_by")
    )
    audit = hub.store.change_sets([commit.change_set_id])[commit.change_set_id]
    assert audit["evidence"]["declined_by"] == [entry.entry_id]
    assert "crm.new=auto" in commit.authority_ref
    # a later update that scores automatically against the declined record never links it there
    email = person_payload(4)["email"]
    land(hub, [row(source.system, source.key, "person", _review_payload(email=email), at=utcnow())])
    arrive(hub)
    assert master_of(hub, "person", source.system, source.key) == created
    assert [t for t in open_tasks(hub) if t.source == source and t.reason == "auto_elsewhere"] == []


def test_not_a_match_in_a_coexistence_domain_creates_automatically_too(hub: Hub) -> None:
    workbench_world(hub)
    source = organisation_close_call(hub)
    task = task_of(hub, kind="review", source=source)
    hub.tray.stage(task.task_id, "not_a_match", actor=STEWARD, **seen(hub, task.task_id))
    window_passed(hub)
    assert hub.tray.flush().requeued == 1
    created = master_of(hub, "organisation", source.system, source.key)
    assert created is not None and created not in task.master_ids
    labels = hub.store.labels_for("organisation", [source.text()])
    assert sorted(lab.right_ref for lab in labels) == sorted(task.master_ids)


def test_keep_apart_binds_the_matcher(hub: Hub) -> None:
    workbench_world(hub)
    left, right = golden_pair(hub)
    task = task_of(hub, kind="possible_duplicate")
    entry = hub.tray.stage(task.task_id, "keep_apart", actor=STEWARD, **seen(hub, task.task_id))
    window_passed(hub)
    assert hub.tray.flush().outcomes == {"committed": 1}
    lower, higher = sorted(task.master_ids)
    (label,) = hub.store.labels_for("organisation", [lower])
    assert (label.label, label.right_ref, label.entry_id) == ("keep_apart", higher, entry.entry_id)
    assert audit_of(hub, entry.entry_id)["action"] == "keep_apart"
    # the pair a later arrival would raise between those two golden records is not opened again
    states = hub.store.source_states("organisation", [left, right])
    pair = Resolution(
        "review", (left, right), (), None, None, None, reason="possible_duplicate", clusters=(higher, lower)
    )
    planned = _PagePlan()
    hub.arrival._cluster_pair_task("organisation", pair, states, utcnow(), planned)
    assert planned.tasks == []
    other = Resolution(
        "review",
        (left, right),
        (),
        None,
        None,
        None,
        reason="possible_duplicate",
        clusters=(lower, "ORG-999999"),
    )
    hub.arrival._cluster_pair_task("organisation", other, states, utcnow(), planned)
    assert len(planned.tasks) == 1


def test_keep_an_orphan(hub: Hub) -> None:
    workbench_world(hub)
    source = standalone_organisation(hub)
    land(hub, [row("crm", source.key, "organisation", {}, op="delete", at=utcnow())])
    arrive(hub)
    task = task_of(hub, kind="orphan")
    entry = hub.tray.stage(task.task_id, "keep_orphan", actor=STEWARD, **seen(hub, task.task_id))
    assert entry.target == task.master_ids[0]
    window_passed(hub)
    assert hub.tray.flush().outcomes == {"committed": 1}
    assert hub.store.tasks_by_id([task.task_id])[task.task_id].status == "closed"
    assert audit_of(hub, entry.entry_id)["action"] == "keep_orphan"


# ---------------------------------------------------------------------------------------------- nothing personal


def test_the_tray_stores_codes_and_ids_only(review) -> None:
    hub, task, source = review
    entry = hub.tray.stage(task.task_id, "link", actor=STEWARD, **seen(hub, task.task_id))
    assert safe(dict(entry.subject)) == dict(entry.subject)
    window_passed(hub)
    hub.tray.flush()
    rows = hub.store._fetch_all(
        f"/*mdm:paged*/ SELECT subject, outcome, signature FROM {hub.store.t('work', 'tray_entry')} LIMIT 100"
    )
    held = person_payload(4)
    for subject, outcome, signature in rows:
        document = hub.store._decode_json(subject)
        assert safe(document) == document and SAFE_TEXT_RE.match(outcome or "")
        text = f"{document} {signature}".lower()
        assert not any(held[name].lower() in text for name in ("given_name", "family_name", "birth_date"))
    for view in hub.tray.entries(actor=STEWARD):
        words = view.label.split()
        assert all(SAFE_TEXT_RE.match(w) for w in words)
        assert held["given_name"] not in view.label and held["family_name"] not in view.label
