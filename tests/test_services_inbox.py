"""The inbox on both engines: pages by due time, views and capped counts, claims, snoozes, escalations, masked
titles and the health strip (owner: SERVICES, B.6.4, B.9.1)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from mdm import capacity
from mdm.models.canonical import utcnow
from mdm.models.changes import WorkWrites
from mdm.models.errors import Conflict, Forbidden, NotFound
from mdm.models.tasks import Task
from mdm.models.workbench import ServiceLevels
from mdm.services.context import Hub
from tests.helpers import (
    CONSUMER,
    COORDINATOR,
    OWNER,
    STEWARD,
    golden_pair,
    held_name_change,
    open_tasks,
    organisation_close_call,
    person_review,
    standalone_organisation,
    task_of,
    workbench_world,
)


@pytest.fixture
def world(hub: Hub) -> Hub:
    """The mini world and four tasks: an Organisation close call, a Person review, a possible duplicate of two
    golden records, and a held critical update."""
    workbench_world(hub)
    organisation_close_call(hub)
    person_review(hub)
    golden_pair(hub)
    source = standalone_organisation(hub)
    held_name_change(hub, source, "Quillmere Optical Ltd", at=utcnow())
    assert len(open_tasks(hub)) == 4
    return hub


def at(hub: Hub, **delta: float) -> None:
    """Moves the inbox's clock to now plus `delta`."""
    moment = utcnow() + timedelta(**delta)
    hub.inbox.clock = lambda: moment


def test_a_new_task_is_due_by_the_service_level_of_its_kind(world: Hub) -> None:
    levels = ServiceLevels(world.settings.sla_hours)
    for task in open_tasks(world):
        assert task.due_at == levels.due(task.kind, task.created_at), task.kind
    review = open_tasks(world, kind="review")[0]
    assert review.due_at - review.created_at == timedelta(hours=8)
    pair = task_of(world, kind="possible_duplicate")
    assert pair.due_at - pair.created_at == timedelta(hours=24)


def test_pages_follow_due_time_then_task_id_without_an_offset(world: Hub) -> None:
    everything = world.inbox.page("team", actor=STEWARD)
    assert everything.after is None and len(everything.rows) == 4
    order = [(r.due_at, r.task_id) for r in everything.rows]
    assert order == sorted(order)
    first = world.inbox.page("team", actor=STEWARD, limit=3)
    assert len(first.rows) == 3 and first.after is not None
    second = world.inbox.page("team", actor=STEWARD, limit=3, after=first.after)
    assert second.after is None
    assert [r.task_id for r in first.rows + second.rows] == [r.task_id for r in everything.rows]
    held = world.inbox.page("team", actor=STEWARD, kind="held")
    assert [r.kind for r in held.rows] == ["held"]
    people = world.inbox.page("team", actor=STEWARD, entity="person")
    assert [r.entity for r in people.rows] == ["person"]
    with pytest.raises(NotFound):
        world.inbox.page("nowhere", actor=STEWARD)


def test_rows_carry_the_stored_evidence_and_masked_titles(world: Hub) -> None:
    rows = {r.subject: r for r in world.inbox.page("team", actor=STEWARD).rows}
    person = rows["crm:C1900004"]
    review = open_tasks(world, entity="person")[0]
    assert (person.kind_label, person.band, person.suggestion) == (
        "Review",
        "review",
        f"Link to {review.master_ids[0]}",
    )
    assert round(person.score, 2) == 88.66
    assert "***" in person.title and all(len(part) == 4 for part in person.title.split())  # "T*** Q***"
    assert person.reason == "hinges on postcode"
    organisation = rows["crm:C0900003"]
    assert organisation.title == "OSSIVER INSTRUMENTS ltd." and "***" not in organisation.title
    assert organisation.suggestion == "Choose among 2 records"
    pair = next(r for r in rows.values() if r.kind == "possible_duplicate")
    assert pair.title == "Dovecote Joinery Ltd · DOVECOTE JOINERY ltd." and " · " in pair.subject
    held = next(r for r in rows.values() if r.kind == "held")
    assert (held.suggestion, held.reason) == ("Approve or reject", "critical: name")
    assert all(r.claimed_by is None and r.staged is None and not r.breaching for r in rows.values())


def test_views_and_capped_counts(world: Hub, monkeypatch: pytest.MonkeyPatch) -> None:
    counts = world.inbox.counts(actor=STEWARD)
    assert counts.views == {"mine": 4, "team": 4, "breaching": 0, "snoozed": 0, "escalated": 0}
    assert counts.kinds == {
        "review": 2,
        "possible_duplicate": 1,
        "held": 1,
        "exception": 0,
        "orphan": 0,
        "unresolved_reference": 0,
    }
    assert world.inbox.counts(actor=STEWARD, entity="person").views["team"] == 1
    # nine hours on, the reviews and the held update are past their service level
    at(world, hours=9)
    breaching = world.inbox.page("breaching", actor=STEWARD)
    assert {r.kind for r in breaching.rows} == {"review", "held"} and all(r.breaching for r in breaching.rows)
    assert world.inbox.counts(actor=STEWARD).views["breaching"] == 3
    # a count reads at most the cap
    monkeypatch.setattr(capacity, "COUNT_CAP", 2)
    assert world.inbox.counts(actor=STEWARD).views["team"] == 2


def test_claims_lapse_and_name_nobody(world: Hub) -> None:
    task = open_tasks(world, kind="review")[0].task_id
    expiry = world.inbox.claim(task, actor=STEWARD)
    assert expiry - utcnow() <= timedelta(minutes=world.settings.claim_minutes)
    assert world.inbox.row(task, actor=STEWARD).claimed_by == "you"
    assert world.inbox.row(task, actor=COORDINATOR).claimed_by == "Data steward (persona)"
    with pytest.raises(Conflict) as refused:
        world.inbox.claim(task, actor=COORDINATOR)
    assert refused.value.code == "claimed_by_another" and "until" in refused.value.fields
    assert STEWARD.name not in str(refused.value) and "data_steward" not in str(refused.value)
    # My queue of the other steward leaves it out while the claim runs
    assert task not in {r.task_id for r in world.inbox.page("mine", actor=COORDINATOR).rows}
    assert task in {r.task_id for r in world.inbox.page("mine", actor=STEWARD).rows}
    # the claim lapses by itself
    at(world, minutes=world.settings.claim_minutes + 1)
    assert world.inbox.row(task, actor=COORDINATOR).claimed_by is None
    assert task in {r.task_id for r in world.inbox.page("mine", actor=COORDINATOR).rows}
    world.inbox.claim(task, actor=COORDINATOR)
    world.inbox.release(task, actor=STEWARD)  # holds none: nothing
    assert world.inbox.row(task, actor=STEWARD).claimed_by == "Coordinating steward (persona)"
    world.inbox.release(task, actor=COORDINATOR)
    assert world.inbox.row(task, actor=STEWARD).claimed_by is None


def test_snooze_and_escalation(world: Hub) -> None:
    task = task_of(world, kind="held")
    wakes = world.inbox.snooze(task.task_id, actor=STEWARD, hours=4)
    assert wakes - utcnow() <= timedelta(hours=4)
    assert task.task_id not in {r.task_id for r in world.inbox.page("mine", actor=STEWARD).rows}
    snoozed = world.inbox.page("snoozed", actor=STEWARD).rows
    assert [r.task_id for r in snoozed] == [task.task_id] and snoozed[0].snoozed_until is not None
    assert world.inbox.task(task.task_id).due_at == task.due_at  # the due time stays
    with pytest.raises(Forbidden) as bad:
        world.inbox.snooze(task.task_id, actor=STEWARD, hours=3)
    assert bad.value.code == "bad_snooze"
    world.inbox.claim(task.task_id, actor=STEWARD)  # a claim wakes it
    assert task.task_id in {r.task_id for r in world.inbox.page("mine", actor=STEWARD).rows}
    world.inbox.escalate(task.task_id, actor=STEWARD, reason="policy_question")
    row = world.inbox.row(task.task_id, actor=STEWARD)
    assert row.escalated and row.claimed_by is None  # an escalation releases the claim
    assert [r.task_id for r in world.inbox.page("escalated", actor=STEWARD).rows] == [task.task_id]
    with pytest.raises(Forbidden) as bad_reason:
        world.inbox.escalate(task.task_id, actor=STEWARD, reason="because")
    assert bad_reason.value.code == "bad_escalation"


def test_roles(world: Hub) -> None:
    for read in (
        lambda: world.inbox.page("team", actor=CONSUMER),
        lambda: world.inbox.counts(actor=CONSUMER),
        lambda: world.inbox.health(actor=CONSUMER),
    ):
        with pytest.raises(Forbidden):
            read()
    assert len(world.inbox.page("team", actor=OWNER).rows) == 4
    task = open_tasks(world)[0].task_id
    for work in (
        lambda: world.inbox.claim(task, actor=OWNER),
        lambda: world.inbox.snooze(task, actor=OWNER, hours=1),
        lambda: world.inbox.escalate(task, actor=OWNER, reason="second_opinion"),
    ):
        with pytest.raises(Forbidden) as refused:
            work()
        assert refused.value.fields["action"] == "work_tasks"


def test_unknown_and_closed_tasks(world: Hub) -> None:
    with pytest.raises(NotFound):
        world.inbox.row("TSK-0000000000000000", actor=STEWARD)
    with pytest.raises(NotFound):
        world.inbox.claim("TSK-0000000000000000", actor=STEWARD)
    task = task_of(world, kind="held")
    world.store.apply_work(_closing(task))
    with pytest.raises(Conflict) as closed:
        world.inbox.claim(task.task_id, actor=STEWARD)
    assert closed.value.code == "task_closed"
    with pytest.raises(Conflict):
        world.inbox.row(task.task_id, actor=STEWARD)


def _closing(task: Task) -> WorkWrites:
    return WorkWrites(task.entity, close_task_ids=(task.task_id,))


def test_due_times_are_backfilled_once(world: Hub) -> None:
    task = open_tasks(world)[0]
    # an older store's open task: no due time
    world.store._execute(
        f"/*mdm:keyed*/ UPDATE {world.store.t('work', 'task')} SET due_at = NULL WHERE task_id = ?",
        [task.task_id],
    )
    assert world.inbox.task(task.task_id).due_at is None
    assert world.inbox.backfill_due_times() == 1
    assert world.inbox.backfill_due_times() == 0
    assert world.inbox.task(task.task_id).due_at == ServiceLevels(world.settings.sla_hours).due(
        task.kind, task.created_at
    )


def test_the_health_strip(world: Hub) -> None:
    health = world.inbox.health(actor=STEWARD)
    assert (health.open_tasks, health.breaching, health.staged) == (4, 0, 0)
    assert health.last_commit_version == world.store.last_commit_version()
    assert health.last_commit_at is not None and health.last_arrival_at is not None
    run = world.store.jobs(1, "arrival")[0]
    assert health.arrival_read == run["progress"]["read"]
    assert health.arrival_tasks == run["progress"]["tasks"]
    assert health.arrival_automatic is not None and 0.0 <= health.arrival_automatic <= 1.0
