"""Signature batches on both engines (story 3.3, decision 23): grouping, the forced sample and its split, every
row's change, the tray and the second steward, chunks committed exactly once, Stop and the throttle, blind review
of 2% of a batch, bulk rights, and compensation (owner: SERVICES)."""

from __future__ import annotations

import threading
import time
from collections import Counter
from dataclasses import replace
from datetime import timedelta

import pytest

from mdm import capacity
from mdm.engine.batch import Member, pick
from mdm.engine.sample import draw_value
from mdm.models.batch import Batch, signature_key
from mdm.models.errors import Conflict, Forbidden, NotFound
from mdm.models.quality import bulk_band
from mdm.models.records import SourceKey
from mdm.models.workbench import TaskQuery
from mdm.services.arrival import _PagePlan
from mdm.services.context import Hub
from mdm.services.policy import Plan
from mdm.services.support import plain
from tests.conftest import ONLY_POSTGRES_ENGINE, THREAD_TIMEOUT, join_all
from tests.helpers import (
    ALIKE_SIGNATURE,
    COORDINATOR,
    OWNER,
    SECOND_STEWARD,
    STEWARD,
    T0,
    TECHNICAL,
    alike_reviews,
    arrive,
    decide_sample,
    flush_past_window,
    land,
    master_of,
    open_tasks,
    org_payload,
    person_payload,
    person_ref,
    row,
    seen,
    workbench_world,
)


class Crash(RuntimeError):
    pass


def rehub(hub: Hub, **changes) -> Hub:
    """The same store under other settings (the checkpoint's thresholds, the throttle, …)."""
    return Hub.open(hub.settings.with_(**changes), store=hub.store, as_role="data_owner")


def alike(hub: Hub, n: int = 12, **options) -> list[SourceKey]:
    """The mini world and `n` alike reviews of one person pattern."""
    first = options.pop("first", 20)
    workbench_world(hub, persons=first + n)
    return alike_reviews(hub, n, first=first, **options)


def group_key(hub: Hub, entity: str = "person") -> str:
    found = [g for g in hub.batches.groups(actor=STEWARD, entity=entity).groups]
    assert found, "no group"
    return found[0].group_key


def draw(hub: Hub, actor=STEWARD) -> Batch:
    return hub.batches.draw(group_key(hub), actor=actor, entity="person")


def items(hub: Hub, batch_id: str, *roles: str):
    return hub.store.batch_items(batch_id, roles or ("sample", "bulk", "split"), None, None, 1000)


def ready(hub: Hub, **changes) -> Batch:
    """A drawn batch whose forced sample agreed: `ready`."""
    batch = draw(hub)
    decide_sample(hub, batch.batch_id, actor=STEWARD)
    found = hub.batches.refresh(batch.batch_id)
    assert found.status == "ready", (found.status, [(i.role, i.status) for i in items(hub, batch.batch_id)])
    return found


def prepared(hub: Hub) -> Batch:
    batch = ready(hub)
    hub.batches.prepare(batch.batch_id, actor=STEWARD)
    return hub.store.batches([batch.batch_id])[batch.batch_id]


def fetch(hub: Hub, batch_id: str) -> Batch:
    return hub.store.batches([batch_id])[batch_id]


def task(hub: Hub, task_id: str):
    return hub.store.tasks_by_id([task_id])[task_id]


# ---------------------------------------------------------------------------------------------- grouping


def test_arrival_stores_the_signature_rule_version_and_key_and_an_update_rewrites_them(hub: Hub) -> None:
    keys = alike(hub, 3)
    tasks = [t for t in open_tasks(hub, "person", "review") if t.source in keys]
    assert len(tasks) == 3
    version = hub.registry.published("person").match.version
    for found in tasks:
        assert (found.signature, found.rule_version) == (ALIKE_SIGNATURE, version)
        assert found.signature_key == signature_key("person", version, ALIKE_SIGNATURE)
    # an update with a postcode changes the pattern: the task is rewritten, signature and key with it
    first = keys[0]
    state = hub.store.source_states("person", [first])[first]
    land(
        hub,
        [
            row(
                "crm",
                first.key,
                "person",
                {**dict(state.values), "postcode": "XA9 9ZZ"},
                at=state.occurred_at + timedelta(hours=1),
            )
        ],
    )
    arrive(hub)
    again = [t for t in open_tasks(hub, "person", "review") if t.source == first]
    assert len(again) == 1 and again[0].signature != ALIKE_SIGNATURE
    assert again[0].signature_key != tasks[0].signature_key


@pytest.mark.parametrize(
    ("reason", "named", "grouped"),
    [
        ("review_band", True, True),
        ("breaker_demoted", True, True),
        ("breaker_demoted", False, False),  # a breaker wait with no golden record
        ("unlinked_candidate", False, False),
        ("batch_candidate", False, False),
        ("cannot_link_conflict", True, False),  # a blocked candidate
        ("master_id_held", True, False),  # a held hint
    ],
)
def test_only_a_review_a_steward_could_link_to_a_golden_record_is_grouped(
    hub: Hub, reason: str, named: bool, grouped: bool
) -> None:
    workbench_world(hub, persons=4)
    source = SourceKey("hr", "H000001")
    state = hub.store.source_states("person", [source])[source]
    master = master_of(hub, "person", "hr", "H000001")
    plan = Plan(
        "task",
        source,
        None,
        "review",
        reason,
        "rule1:auto_band",
        {"master_ids": [master]} if named else {},
        signature=ALIKE_SIGNATURE,
        rule_version=1,
    )
    out = _PagePlan()
    hub.arrival._task("person", plan, state, state.occurred_at, out)  # noqa: SLF001 - the task arrival writes
    (written,) = out.tasks
    if grouped:
        assert (written.signature, written.rule_version) == (ALIKE_SIGNATURE, 1)
        assert written.signature_key == signature_key("person", 1, ALIKE_SIGNATURE)
    else:
        assert written.signature == "" and written.signature_key is None


def test_the_backfill_gives_an_older_review_its_signature_once(hub: Hub) -> None:
    keys = alike(hub, 2)
    found = next(t for t in open_tasks(hub, "person", "review") if t.source == keys[0])
    # as a story-3.2 store left it: no signature, no rule version, no key
    hub.store.set_task_signatures([])  # nothing to do
    with hub.store.transaction():
        hub.store._write_tasks([replace(found, signature=None, rule_version=None)])  # noqa: SLF001 - a story-3.2 row
    older = hub.store.tasks_by_id([found.task_id])[found.task_id]
    assert older.signature is None and older.signature_key is None
    assert hub.batches.backfill_signatures() == 1
    again = hub.store.tasks_by_id([found.task_id])[found.task_id]
    assert again.signature == ALIKE_SIGNATURE and again.signature_key == found.signature_key
    assert hub.batches.backfill_signatures() == 0  # once


def test_the_groups_page_counts_labels_agreement_and_the_window(hub: Hub) -> None:
    alike(hub, 12)
    page = hub.batches.groups(actor=STEWARD)
    (group,) = [g for g in page.groups if g.entity == "person"]
    assert group.count == 12 and not group.too_small and group.batch_id is None
    assert [m.comparison for m in group.marks][:3] == ["given_name", "family_name", "birth_date"]
    assert [(m.mark, m.words) for m in group.marks][:3] == [
        ("=", "the same"),
        ("=", "the same"),
        ("≈", "similar"),
    ]
    assert (group.labels.matched, group.labels.not_matched) == (0, 0)
    assert (group.agreement.agreed, group.agreement.reviewed) == (0, 0)
    assert page.window == capacity.GROUP_WINDOW and page.older_rules == 0
    assert hub.batches.group(group.group_key, actor=STEWARD, entity="person") == group
    # the count is the group's reviews in the inbox, whoever holds them
    listed = hub.inbox.page(actor=STEWARD, group=group.group_key, limit=50)
    assert len(listed.rows) == 12


def test_the_groups_page_caps_its_counts_and_lists_the_largest(
    hub: Hub, monkeypatch: pytest.MonkeyPatch
) -> None:
    alike(hub, 6)
    monkeypatch.setattr(capacity, "COUNT_CAP", 4)
    monkeypatch.setattr(capacity, "GROUPS_SHOWN", 1)
    page = hub.batches.groups(actor=STEWARD)
    assert len(page.groups) == 1 and page.groups[0].count == 4  # capped


def test_a_group_under_an_earlier_rule_version_is_counted_not_grouped(hub: Hub) -> None:
    keys = alike(hub, 3)
    tasks = [t for t in open_tasks(hub, "person", "review") if t.source in keys]
    with hub.store.transaction():
        hub.store._write_tasks([replace(t, rule_version=0) for t in tasks])  # noqa: SLF001 - scored under v0
    page = hub.batches.groups(actor=STEWARD, entity="person")
    assert page.groups == () and page.older_rules == 3


def test_a_consumer_sees_no_groups(hub: Hub) -> None:
    alike(hub, 3)
    from tests.helpers import CONSUMER

    with pytest.raises(Forbidden):
        hub.batches.groups(actor=CONSUMER)


# ---------------------------------------------------------------------------------------------- the draw


def test_the_draw_sizes_the_sample_and_shares_it_over_strata(hub: Hub) -> None:
    alike(hub, 12)
    batch = draw(hub)
    assert (batch.status, batch.population, batch.sample_size) == ("sampling", 12, 5)
    assert batch.bulk_band == bulk_band("person", ALIKE_SIGNATURE)
    sample = items(hub, batch.batch_id, "sample")
    bulk = items(hub, batch.batch_id, "bulk")
    assert len(sample) == 5 and len(bulk) == 7
    assert {i.status for i in sample} == {"open"} and {i.status for i in bulk} == {"candidate"}
    strata = Counter(i.stratum for i in (*sample, *bulk))
    assert set(Counter(i.stratum for i in sample)) == set(strata)  # every stratum holds a sample review
    # the draw is the smallest keyed draws per stratum, so both engines draw the same reviews for the same tasks
    for item in (*sample, *bulk):
        assert item.draw == draw_value("person", item.task_id, "forced_sample", batch.signature_key, "")
    members = [Member(i.task_id, i.stratum, i.draw) for i in (*sample, *bulk)]
    assert set(pick(members, 5, {})) == {i.task_id for i in sample}
    # one open batch per group
    with pytest.raises(Conflict) as again:
        draw(hub)
    assert again.value.code == "batch_open" and again.value.fields["batch"] == batch.batch_id
    # the batch's sample reviews, in the inbox, whoever holds them
    listed = hub.inbox.page(actor=COORDINATOR, batch=batch.batch_id, limit=50)
    assert {r.task_id for r in listed.rows} == {i.task_id for i in sample}


def test_the_same_reviews_are_drawn_again_after_a_discard(hub: Hub) -> None:
    alike(hub, 12)
    first = draw(hub)
    chosen = {i.task_id for i in items(hub, first.batch_id, "sample")}
    hub.batches.discard(first.batch_id, actor=STEWARD)
    assert fetch(hub, first.batch_id).status == "discarded"
    second = draw(hub)
    assert {i.task_id for i in items(hub, second.batch_id, "sample")} == chosen


def test_a_group_too_small_is_refused(hub: Hub) -> None:
    alike(hub, 5)
    with pytest.raises(Conflict) as small:
        draw(hub)
    assert small.value.code == "group_too_small"
    assert hub.batches.groups(actor=STEWARD).groups[0].too_small


def test_the_draw_leaves_out_claimed_snoozed_escalated_staged_and_changed_reviews(hub: Hub) -> None:
    keys = alike(hub, 12)
    tasks = {t.source: t for t in open_tasks(hub, "person", "review") if t.source in keys}
    hub.inbox.claim(tasks[keys[0]].task_id, actor=COORDINATOR)
    hub.inbox.snooze(tasks[keys[1]].task_id, actor=COORDINATOR, hours=4)
    hub.inbox.escalate(tasks[keys[2]].task_id, actor=COORDINATOR, reason="second_opinion")
    hub.tray.stage(tasks[keys[3]].task_id, "link", actor=COORDINATOR, **seen(hub, tasks[keys[3]].task_id))
    batch = hub.batches.draw(group_key(hub), actor=STEWARD, entity="person")
    assert batch.population == 8
    left = batch.figures["left_out_at_draw"]
    assert left == {"claimed": 1, "escalated": 1, "snoozed": 1, "staged": 1}


def test_the_draw_leaves_out_close_calls(hub: Hub) -> None:
    # persons 80 apart share names and a birth year: a record meets two golden records as closely
    workbench_world(hub, persons=112)
    alike_reviews(hub, 12, first=100)
    batch = draw(hub)
    assert batch.figures["left_out_at_draw"].get("close_call", 0) > 0
    assert batch.population + batch.figures["left_out_at_draw"]["close_call"] == 12


def test_the_draw_leaves_out_a_review_a_cannot_link_rule_now_blocks(hub: Hub) -> None:
    workbench_world(hub, persons=4)
    keys = alike_reviews(hub, 8, one_target=True, refs={0: person_ref(5001), 1: person_ref(5002)})
    first = next(t for t in open_tasks(hub, "person", "review") if t.source == keys[0])
    hub.tray.stage(first.task_id, "link", actor=STEWARD, **seen(hub, first.task_id))
    flush_past_window(hub)  # the target now holds a member with another person reference
    batch = draw(hub)
    assert batch.figures["left_out_at_draw"] == {"blocked": 1}
    assert keys[1] not in {i.source for i in items(hub, batch.batch_id)}


def test_a_review_split_off_earlier_stays_out_until_a_new_event(hub: Hub) -> None:
    alike(hub, 12)
    batch = draw(hub)
    victim = items(hub, batch.batch_id, "bulk")[0]
    hub.store.apply_split(
        batch.batch_id,
        items(hub, batch.batch_id, "sample")[0].task_id,
        [victim.task_id],
        "split:birth_date",
        (),
    )
    hub.batches.discard(batch.batch_id, actor=STEWARD)
    again = draw(hub)
    assert victim.task_id not in {i.task_id for i in items(hub, again.batch_id)}
    assert again.figures["left_out_at_draw"]["split_earlier"] >= 1


def test_a_review_whose_live_signature_changed_is_left_out(hub: Hub) -> None:
    keys = alike(hub, 12)
    tasks = [t for t in open_tasks(hub, "person", "review") if t.source in keys]
    with hub.store.transaction():  # as if arrival had seen another pattern than the case shows now
        hub.store._write_tasks([replace(tasks[0], signature="given_name= · family_name=")])  # noqa: SLF001
    # the task leaves the group: it no longer carries the group's key
    batch = draw(hub)
    assert batch.population == 11
    hub.batches.discard(batch.batch_id, actor=STEWARD)
    # the other way round: the stored pattern is the group's, but the case, scored live, shows another one (an
    # update with a postcode, taken in, then the task tampered back to what arrival had seen before)
    # not the review tampered above: which one sorts first depends on the event IDs earlier tests drew
    moved = next(key for key in keys if key != tasks[0].source)
    state = hub.store.source_states("person", [moved])[moved]
    land(
        hub,
        [
            row(
                "crm",
                moved.key,
                "person",
                {**dict(state.values), "postcode": "XA9 9ZZ"},
                at=state.occurred_at + timedelta(hours=1),
            )
        ],
    )
    arrive(hub)
    (rewritten,) = [t for t in open_tasks(hub, "person", "review") if t.source == moved]
    assert rewritten.signature != ALIKE_SIGNATURE
    with hub.store.transaction():
        hub.store._write_tasks([replace(rewritten, signature=ALIKE_SIGNATURE)])  # noqa: SLF001 - stale pattern
    again = draw(hub)
    assert again.population == 10 and again.figures["left_out_at_draw"] == {"signature_changed": 1}
    assert rewritten.task_id not in {i.task_id for i in items(hub, again.batch_id)}


def test_drawing_is_refused_while_bulk_rights_are_withdrawn(hub: Hub) -> None:
    alike(hub, 12)
    key = group_key(hub)
    hub.breaker.demo_withdraw("person", key)
    with pytest.raises(Conflict) as refused:
        hub.batches.draw(key, actor=STEWARD, entity="person")
    assert refused.value.code == "bulk_withdrawn"
    row_ = hub.batches.groups(actor=STEWARD).groups[0]
    assert row_.withdrawn is not None and row_.withdrawn.key == bulk_band("person", ALIKE_SIGNATURE)


def test_a_data_owner_cannot_draw(hub: Hub) -> None:
    alike(hub, 12)
    with pytest.raises(Forbidden):
        hub.batches.draw(group_key(hub), actor=OWNER, entity="person")


# ---------------------------------------------------------------------------------------------- the forced sample

#: invented persons with a birth date of their own, and the crm records that meet them: a record's birth date
#: is `NEAR`, one digit from `BIRTH`, so every such review shares its birth-date form with the others
BIRTH, NEAR = "1975-04-10", "1975-04-11"
NAMES = (
    ("Aldric", "Pennywhistle"),
    ("Brisa", "Quillfeather"),
    ("Caddock", "Ravensmere"),
    ("Delphine", "Stoatley"),
    ("Eskel", "Thornquist"),
    ("Fiora", "Umberlane"),
    ("Gawen", "Vexworth"),
    ("Hesper", "Wickhamby"),
)


def same_birth(hub: Hub, n: int) -> list[SourceKey]:
    """n more reviews of the alike pattern whose records share one birth-date form (`NEAR`)."""
    people = NAMES[:n]
    land(
        hub,
        [
            row(
                "hr",
                f"H95{k:04d}",
                "person",
                {
                    "given_name": given,
                    "family_name": family,
                    "birth_date": BIRTH,
                    "email": f"{given.lower()}.{family.lower()}@example.org",
                    "phone": f"0999 55{k:04d}",
                    "postcode": "XA7 7QQ",
                    "city": "Norvale",
                    "country": "XA",
                    "person_ref": person_ref(5100 + k),
                },
                version=1,
            )
            for k, (given, family) in enumerate(people)
        ],
    )
    arrive(hub)
    keys = [SourceKey("crm", f"C15{k:05d}") for k in range(len(people))]
    land(
        hub,
        [
            row(
                "crm",
                key.key,
                "person",
                {
                    "given_name": given,
                    "family_name": family,
                    "birth_date": NEAR,
                    "city": "Norvale",
                    "country": "XA",
                },
            )
            for key, (given, family) in zip(keys, people, strict=True)
        ],
    )
    arrive(hub)
    return keys


def a_sample_review_among(hub: Hub, batch_id: str, sources: list[SourceKey]):
    """A sample review whose record is one of `sources`: one is moved into the sample when the draw took none."""
    sample = items(hub, batch_id, "sample")
    found = next((i for i in sample if i.source in sources), None)
    if found is not None:
        return found
    bulk = next(i for i in items(hub, batch_id, "bulk") if i.source in sources)
    out = sample[0]
    hub.store.update_items(
        batch_id, [{"task_id": bulk.task_id, "role": "sample", "status": "open"}], from_status="candidate"
    )
    hub.store.update_items(
        batch_id, [{"task_id": out.task_id, "role": "bulk", "status": "candidate"}], from_status="open"
    )
    return next(i for i in items(hub, batch_id, "sample") if i.task_id == bulk.task_id)


def test_a_link_to_the_default_agrees_and_not_a_match_disagrees(hub: Hub) -> None:
    alike(hub, 12)
    batch = draw(hub)
    sample = items(hub, batch.batch_id, "sample")
    first, second = sample[0], sample[1]
    answers = {second.task_id: ("not_a_match", "birth_date")}
    decide_sample(hub, batch.batch_id, actor=STEWARD, answers=answers)
    outcomes = {i.task_id: (i.status, i.split_on) for i in items(hub, batch.batch_id)}
    assert outcomes[first.task_id] == ("agreed", None)
    assert outcomes[second.task_id] == ("disagreed", "birth_date")
    # a link to another candidate than the case's default disagrees, and a link on a close call is void
    entry = hub.store.tray_entries(
        [next(iter(hub.store.items_by_task([first.task_id])[first.task_id])).entry_id]
    )
    staged = next(iter(entry.values()))
    fresh = draw_after_discard(hub, batch.batch_id)
    item = items(hub, fresh.batch_id, "sample")[0]
    other = replace(staged, task_id=item.task_id, target="PER-999999")
    context = {"sample_batch": fresh.batch_id}
    outcome = hub.decisions._sample_outcome  # noqa: SLF001
    (write,) = outcome(other, {**context, "default": "PER-000001", "split_on": "birth_date"})
    assert (write.status, write.split_on) == ("disagreed", "birth_date")
    (void,) = outcome(other, {**context, "default": None})
    assert void.status == "void" and void.split_on is None
    # a decision staged outside this batch's sample pane is no outcome of it
    assert outcome(other, {"default": "PER-000001", "split_on": "birth_date"}) == ()
    assert outcome(other, {"sample_batch": batch.batch_id, "default": "PER-000001"}) == ()


def draw_after_discard(hub: Hub, batch_id: str) -> Batch:
    if fetch(hub, batch_id).status in ("sampling", "ready", "awaiting_checker"):
        hub.batches.discard(batch_id, actor=STEWARD)
    return draw(hub)


def test_a_decision_still_in_the_tray_keeps_the_batch_sampling(hub: Hub) -> None:
    alike(hub, 12)
    batch = draw(hub)
    decide_sample(hub, batch.batch_id, actor=STEWARD, flush=False)
    assert hub.batches.refresh(batch.batch_id).status == "sampling"
    view = hub.batches.batch(batch.batch_id, actor=STEWARD)
    assert view.waiting == 5 and view.decided == 0
    flush_past_window(hub)
    assert hub.batches.refresh(batch.batch_id).status == "ready"
    view = hub.batches.batch(batch.batch_id, actor=STEWARD)
    assert (view.decided, view.agreed, view.disagreed, view.waiting) == (5, 5, 0, 0)
    assert [a.decision for a in view.actions] == ["prepare", "discard"]
    # the five links wrote labels with the pattern: the group's label history counts them
    group = hub.batches.group(batch.signature_key, actor=STEWARD, entity="person")
    assert group is not None and (group.labels.matched, group.labels.not_matched) == (5, 0)
    assert (group.batch_id, group.batch_words) == (batch.batch_id, "sample agreed")


def test_only_a_role_that_decides_tasks_is_sent_to_decide_the_sample(hub: Hub) -> None:
    """A data owner or a technical steward reads a sampling batch's page, but its "Decide the sample in the
    inbox" is disabled for them, with why (review 3.3)."""
    alike(hub, 12)
    batch = draw(hub)
    steward = hub.batches.batch(batch.batch_id, actor=STEWARD)
    assert [(a.decision, a.enabled) for a in steward.actions][0] == ("decide_sample", True)
    for reader, role in ((OWNER, "data owner"), (TECHNICAL, "technical steward")):
        view = hub.batches.batch(batch.batch_id, actor=reader)
        (decide,) = [a for a in view.actions if a.decision == "decide_sample"]
        assert (decide.enabled, decide.why_not) == (
            False,
            f"Your role, {role}, can see tasks but not decide them.",
        )


def test_a_sample_review_closed_meanwhile_or_moved_is_void_and_replaced(hub: Hub) -> None:
    alike(hub, 12)
    batch = draw(hub)
    closed, moved = items(hub, batch.batch_id, "sample")[:2]
    # the breaker's hand-back closes a task and queues its record again
    from mdm.models.changes import WorkWrites

    with hub.store.transaction():
        hub.store.apply_work(WorkWrites("person", close_task_ids=(closed.task_id,)))
    # a new event of the other record, taken in: its task now names another event
    state = hub.store.source_states("person", [moved.source])[moved.source]
    land(
        hub,
        [
            row(
                "crm",
                moved.source.key,
                "person",
                dict(state.values),
                at=state.occurred_at + timedelta(minutes=5),
            )
        ],
    )
    arrive(hub)
    hub.batches.refresh(batch.batch_id)
    by_task = {i.task_id: i for i in items(hub, batch.batch_id)}
    assert (by_task[closed.task_id].status, by_task[closed.task_id].reason) == ("void", "task_closed")
    assert (by_task[moved.task_id].status, by_task[moved.task_id].reason) == ("void", "record_changed")
    open_ = [i for i in items(hub, batch.batch_id, "sample") if i.status == "open"]
    assert len(open_) == 5  # two replaced by the next draws
    view = hub.batches.batch(batch.batch_id, actor=STEWARD)
    assert {s.words for s in view.sample if s.status == "void"} == {"Void: another review replaces it"}
    # a void review has left the batch already: a split of every alike review neither moves nor reads it
    decide_sample(hub, batch.batch_id, actor=STEWARD, answers={open_[0].task_id: ("not_a_match", "all")})
    assert fetch(hub, batch.batch_id).outcome == "split_all"
    after = {i.task_id: i for i in items(hub, batch.batch_id)}
    assert (after[closed.task_id].role, after[moved.task_id].role) == ("sample", "sample")
    assert all(i.role == "split" for i in after.values() if i.status != "void")
    # "every alike review" compares no value, so it logs no record
    assert not [a for a in hub.store.access_log(None, 5000) if a["action"] == "batch_split"]


def test_a_sample_review_another_split_took_no_longer_counts(hub: Hub) -> None:
    alike(hub, 12)
    batch = draw(hub)
    disagreeing, taken = items(hub, batch.batch_id, "sample")[:2]
    hub.store.apply_split(batch.batch_id, disagreeing.task_id, [taken.task_id], "split:birth_date", ())
    view = hub.batches.batch(batch.batch_id, actor=STEWARD)  # refreshed: the sample is topped up
    line = next(s for s in view.sample if s.task_id == taken.task_id)
    assert not line.open and line.words == "Left the batch with a split: decided one by one"
    assert view.waiting == 5 and view.decided == 0
    assert taken.task_id not in {r.task_id for r in hub.inbox.page(actor=STEWARD, batch=batch.batch_id).rows}


def test_the_inbox_lists_a_batchs_sample_and_a_groups_reviews_snoozed_or_not(hub: Hub) -> None:
    alike(hub, 12)
    key = group_key(hub)
    batch = draw(hub)
    sample = items(hub, batch.batch_id, "sample")
    hub.inbox.snooze(sample[0].task_id, actor=COORDINATOR, hours=4)
    listed = hub.inbox.page(actor=STEWARD, batch=batch.batch_id)
    assert {r.task_id for r in listed.rows} == {i.task_id for i in sample}
    grouped = hub.inbox.page(actor=STEWARD, group=key)
    counted = hub.store.task_count(
        TaskQuery(hub.inbox.clock(), None, "review", snoozed=None, signature_key=key), 1000
    )
    assert len(grouped.rows) == counted == 12
    with pytest.raises(Forbidden) as bad:
        hub.inbox.page(actor=STEWARD, group="given_name=")
    assert bad.value.code == "bad_filter"
    counts = hub.inbox.counts(actor=STEWARD)
    assert counts.alike == 12


def test_the_decide_pane_says_a_review_belongs_to_a_forced_sample(hub: Hub) -> None:
    alike(hub, 12)
    batch = draw(hub)
    item = items(hub, batch.batch_id, "sample")[0]
    case = hub.decisions.case(item.task_id, actor=STEWARD)
    assert case.sample is not None and case.sample.batch_id == batch.batch_id
    assert case.sample.size == 5 and case.sample.decided == 0 and 1 <= case.sample.position <= 5
    assert [m.comparison for m in case.sample.choices][:3] == ["given_name", "family_name", "birth_date"]


# ---------------------------------------------------------------------------------------------- the split


def test_a_disagreeing_sample_decision_names_the_comparison_that_misled(hub: Hub) -> None:
    alike(hub, 12)
    batch = draw(hub)
    item = items(hub, batch.batch_id, "sample")[0]
    context = seen(hub, item.task_id)
    with pytest.raises(Forbidden) as missing:
        hub.tray.stage(item.task_id, "not_a_match", actor=STEWARD, **context)
    assert missing.value.code == "split_choice_needed"
    with pytest.raises(Forbidden) as bad:
        hub.tray.stage(item.task_id, "not_a_match", actor=STEWARD, split_on="city", **context)
    assert bad.value.code == "bad_split_choice"
    with pytest.raises(Forbidden) as agreeing:
        hub.tray.stage(item.task_id, "link", actor=STEWARD, split_on="birth_date", **context)
    assert agreeing.value.code == "bad_split_choice"
    entry = hub.tray.stage(item.task_id, "not_a_match", actor=STEWARD, split_on="all", **context)
    assert entry.subject["split_on"] == "all" and entry.subject["default"] is not None
    # a review outside the sample takes no comparison
    other = items(hub, batch.batch_id, "bulk")[0]
    with pytest.raises(Forbidden) as none:
        hub.tray.stage(
            other.task_id, "not_a_match", actor=STEWARD, split_on="birth_date", **seen(hub, other.task_id)
        )
    assert none.value.code == "bad_split_choice"


def test_the_split_takes_the_reviews_that_share_the_flagged_value_and_logs_each_record(hub: Hub) -> None:
    alike(hub, 12)
    shared = same_birth(hub, 6)
    batch = draw(hub)
    assert batch.population == 18
    flagged = a_sample_review_among(hub, batch.batch_id, shared)
    drawn_first = {i.task_id for i in items(hub, batch.batch_id, "sample")}
    before = hub.store.access_log(None, 5000)
    decide_sample(
        hub, batch.batch_id, actor=STEWARD, answers={flagged.task_id: ("not_a_match", "birth_date")}
    )
    after = [i for i in items(hub, batch.batch_id)]
    split = {i.task_id for i in after if i.role == "split"}
    expected = {i.task_id for i in after if i.source in shared}
    assert flagged.task_id in split and split == expected and len(split) == 6
    assert {i.reason for i in after if i.role == "split"} == {"split:birth_date"}
    logged = [a for a in hub.store.access_log(None, 5000) if a not in before and a["action"] == "batch_split"]
    assert len(logged) == 6
    assert {(a["actor"], a["actor_role"], a["attribute"], a["reason"]) for a in logged} == {
        (STEWARD.name, "data_steward", "birth_date", "batch_split")
    }
    assert {a["detail"]["task_id"] for a in logged} == {flagged.task_id}
    # the top-up follows the split: the sample holds 5 again, every one still in the batch, and agreed
    batch = hub.batches.refresh(batch.batch_id)
    kept = [i for i in items(hub, batch.batch_id, "sample") if i.status in ("open", "agreed")]
    assert batch.status == "ready" and len(kept) == 5
    assert {i.task_id for i in kept} - drawn_first  # a next draw joined the sample
    view = hub.batches.batch(batch.batch_id, actor=STEWARD)
    # the disagreeing review left with its split: "5 of 5 decided · 5 agreed · 1 disagreed", never 6 of 5
    assert (view.decided, view.sample_size, view.agreed, view.disagreed) == (5, 5, 5, 1)
    (line,) = view.splits
    assert (line.task_id, line.on_label, line.count, line.decision_words) == (
        flagged.task_id,
        "birth date",
        6,
        "Not a match",
    )


def test_an_email_split_takes_every_record_missing_one_and_logs_each(hub: Hub) -> None:
    alike(hub, 12)
    batch = draw(hub)
    flagged = items(hub, batch.batch_id, "sample")[0]
    before = len(hub.store.access_log(None, 5000))
    decide_sample(hub, batch.batch_id, actor=STEWARD, answers={flagged.task_id: ("not_a_match", "email")})
    # every record lacks an e-mail, and a missing form matches only a missing one: every review leaves
    assert fetch(hub, batch.batch_id).outcome == "too_few_left"
    logged = [a for a in hub.store.access_log(None, 5000)[before:] if a["action"] == "batch_split"]
    assert len(logged) == 12  # e-mail is personal: one row per record read
    assert hub.store.open_batches_of_groups([batch.signature_key]) == {}  # the group is free to draw again


def test_an_organisation_split_on_a_comparison_that_is_not_personal_writes_no_access_row(hub: Hub) -> None:
    """The product owner's answer: access rows only when the comparison's attributes are personal. An
    Organisation batch split on its city moves the reviews that share the flagged record's city, and logs no
    record (review 3.3)."""
    workbench_world(hub, persons=4, organisations=10)
    landed = {
        f"C07{i:05d}": {
            "name": org_payload(i)["name"],
            "city": org_payload(i)["city"],
            "country": "XB",
            "postcode": f"XB9 {i % 5 + 1}ZZ",
        }
        for i in range(10)
    }
    land(
        hub,
        [
            row("crm", key, "organisation", values, at=T0 + timedelta(hours=3, seconds=n))
            for n, (key, values) in enumerate(landed.items())
        ],
    )
    arrive(hub)
    model = hub.registry.published("organisation")
    assert not model.attribute("city").personal
    (group,) = hub.batches.groups(actor=STEWARD, entity="organisation").groups
    batch = hub.batches.draw(group.group_key, actor=STEWARD, entity="organisation")
    flagged = items(hub, batch.batch_id, "sample")[0]
    city = landed[flagged.source.key]["city"]
    before = len(hub.store.access_log(None, 5000))
    decide_sample(hub, batch.batch_id, actor=STEWARD, answers={flagged.task_id: ("not_a_match", "city")})
    after = items(hub, batch.batch_id)
    split = {i.source.key for i in after if i.role == "split"}
    assert flagged.source.key in split
    assert split == {i.source.key for i in after if landed[i.source.key]["city"] == city}
    assert {i.reason for i in after if i.role == "split"} == {"split:city"}
    logged = [a for a in hub.store.access_log(None, 5000)[before:] if a["action"] == "batch_split"]
    assert logged == []  # the city of an organisation is not personal: nothing is logged
    (line,) = hub.batches.batch(batch.batch_id, actor=STEWARD).splits
    assert (line.on_label, line.count) == ("city", len(split))


def test_a_disagreement_undone_in_its_window_splits_nothing(hub: Hub) -> None:
    alike(hub, 12)
    batch = draw(hub)
    flagged = items(hub, batch.batch_id, "sample")[0]
    entry = hub.tray.stage(
        flagged.task_id, "not_a_match", actor=STEWARD, split_on="birth_date", **seen(hub, flagged.task_id)
    )
    hub.tray.undo(entry.entry_id, actor=STEWARD)
    flush_past_window(hub)
    hub.batches.refresh(batch.batch_id)
    assert not [i for i in items(hub, batch.batch_id) if i.role == "split"]
    assert next(i for i in items(hub, batch.batch_id) if i.task_id == flagged.task_id).status == "open"


def test_a_split_interrupted_after_the_decision_applies_at_the_next_refresh_once(
    hub: Hub, monkeypatch: pytest.MonkeyPatch
) -> None:
    alike(hub, 12)
    shared = same_birth(hub, 6)
    batch = draw(hub)
    flagged = a_sample_review_among(hub, batch.batch_id, shared)
    original = hub.batches._split  # noqa: SLF001

    def crash(*args, **kwargs):
        raise Crash("split")

    monkeypatch.setattr(hub.batches, "_split", crash)
    decide_sample(
        hub, batch.batch_id, actor=STEWARD, answers={flagged.task_id: ("not_a_match", "birth_date")}
    )
    waiting = next(i for i in items(hub, batch.batch_id) if i.task_id == flagged.task_id)
    assert (waiting.status, waiting.split_applied) == ("disagreed", False)
    view = hub.batches.batch(batch.batch_id, actor=STEWARD)
    assert view.splits[0].count is None  # its split waits to apply
    monkeypatch.setattr(hub.batches, "_split", original)
    before = len(hub.store.access_log(None, 5000))
    hub.batches.refresh(batch.batch_id)
    hub.batches.refresh(batch.batch_id)
    assert len([i for i in items(hub, batch.batch_id) if i.role == "split"]) == 6
    assert len([a for a in hub.store.access_log(None, 5000)[before:] if a["action"] == "batch_split"]) == 6


def pending_split(hub: Hub, monkeypatch: pytest.MonkeyPatch) -> tuple[Batch, str]:
    """18 alike reviews, 6 of them sharing one birth-date form, and a sample review among the 6 decided "Not a
    match" on birth date, its split still pending (the flush's own refresh crashed): (the batch, its task)."""
    alike(hub, 12)
    shared = same_birth(hub, 6)
    batch = draw(hub)
    flagged = a_sample_review_among(hub, batch.batch_id, shared)
    original = hub.batches._split  # noqa: SLF001

    def crash(*args, **kwargs):
        raise Crash("split")

    monkeypatch.setattr(hub.batches, "_split", crash)
    decide_sample(
        hub, batch.batch_id, actor=STEWARD, answers={flagged.task_id: ("not_a_match", "birth_date")}
    )
    monkeypatch.setattr(hub.batches, "_split", original)
    return fetch(hub, batch.batch_id), flagged.task_id


def split_logged(hub: Hub) -> list[dict]:
    return [a for a in hub.store.access_log(None, 5000) if a["action"] == "batch_split"]


def test_two_refreshes_racing_one_pending_split_apply_it_once(
    hub: Hub, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The flush's refresh and a batch page's poll both read the split as pending, then meet: one applies it,
    the other finds it applied under the batch row's lock and writes nothing (review 3.3, the split race)."""
    batch, flagged = pending_split(hub, monkeypatch)
    service = hub.batches
    original = service._split  # noqa: SLF001
    barrier = threading.Barrier(2, timeout=THREAD_TIMEOUT)
    moved: list[int] = []
    errors: list[BaseException] = []

    def meet(batch_, item_):
        barrier.wait()  # both refreshes have read the split as pending
        found = original(batch_, item_)
        moved.append(found)
        return found

    monkeypatch.setattr(service, "_split", meet)

    def run() -> None:
        try:
            service.refresh(batch.batch_id)
        except BaseException as error:  # noqa: BLE001 - reported below
            errors.append(error)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    join_all(threads)
    monkeypatch.setattr(service, "_split", original)
    assert not errors, errors
    assert sorted(moved) == [0, 6]  # applied once
    assert len([i for i in items(hub, batch.batch_id) if i.role == "split"]) == 6
    assert len(split_logged(hub)) == 6  # one access row per split record, never twice
    assert fetch(hub, batch.batch_id).figures["splits"] == {flagged: 6}  # never overwritten with 0
    view = service.batch(batch.batch_id, actor=STEWARD)
    assert [line.count for line in view.splits] == [6]


def test_a_refresh_that_read_the_split_before_another_applied_it_writes_nothing(
    hub: Hub, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same race, interleaved step by step: a refresh read the batch and the review, another refresh
    applied the split, and the first then reaches it; a second disagreement's split keeps the first count."""
    batch, flagged = pending_split(hub, monkeypatch)
    stale_batch = fetch(hub, batch.batch_id)
    stale_item = next(i for i in items(hub, batch.batch_id) if i.task_id == flagged)
    assert (stale_item.status, stale_item.split_applied) == ("disagreed", False)
    hub.batches.refresh(batch.batch_id)  # another refresh applies it
    assert fetch(hub, batch.batch_id).figures["splits"] == {flagged: 6}
    assert hub.batches._split(stale_batch, stale_item) == 0  # noqa: SLF001
    assert len(split_logged(hub)) == 6
    assert fetch(hub, batch.batch_id).figures["splits"] == {flagged: 6}
    # a second disagreement's split, reached with the batch read before the first split: both counts stay
    second = next(i for i in items(hub, batch.batch_id, "sample") if i.status == "open")
    hub.store.update_items(
        batch.batch_id,
        [{"task_id": second.task_id, "status": "disagreed", "split_on": "given_name"}],
        from_status="open",
    )
    stale_second = next(i for i in items(hub, batch.batch_id) if i.task_id == second.task_id)
    moved = hub.batches._split(stale_batch, stale_second)  # noqa: SLF001
    assert moved >= 1
    assert fetch(hub, batch.batch_id).figures["splits"] == {flagged: 6, second.task_id: moved}


def test_a_disagreement_that_names_no_comparison_never_splits_the_whole_batch(hub: Hub) -> None:
    """A disagreeing sample outcome without a comparison turns void and is replaced; it is never read as
    "every alike review" (review 3.3)."""
    alike(hub, 12)
    batch = draw(hub)
    item = items(hub, batch.batch_id, "sample")[0]
    # what an outcome written without its comparison would leave
    hub.store.update_items(
        batch.batch_id, [{"task_id": item.task_id, "status": "disagreed"}], from_status="open"
    )
    refreshed = hub.batches.refresh(batch.batch_id)
    assert (refreshed.status, refreshed.outcome) == ("sampling", None)
    found = next(i for i in items(hub, batch.batch_id) if i.task_id == item.task_id)
    assert (found.role, found.status, found.reason) == ("sample", "void", "no_comparison")
    assert not [i for i in items(hub, batch.batch_id) if i.role == "split"]
    assert len([i for i in items(hub, batch.batch_id, "sample") if i.status == "open"]) == 5  # replaced


def next_top_up(hub: Hub, batch_id: str, leaving: str) -> str:
    """The candidate the top-up would draw next once `leaving` leaves the sample."""
    sample = items(hub, batch_id, "sample")
    kept = [i for i in sample if i.task_id != leaving]
    candidates = [i for i in items(hub, batch_id, "bulk") if i.status == "candidate"]
    (found,) = pick(
        [Member(i.task_id, i.stratum, i.draw) for i in candidates],
        len(sample),
        Counter(i.stratum for i in kept),
    )
    return found


@pytest.mark.parametrize("decision", ["not_a_match", "link"])
def test_the_top_up_never_draws_a_review_with_a_decision_staged_outside_the_sample(
    hub: Hub, decision: str
) -> None:
    """A steward working the ordinary inbox stages a decision on the bulk candidate the top-up would draw next,
    while a split in the same flush pass makes the sample top up: the top-up passes that review by, its
    decision is no sample outcome, and the batch goes on sampling (review 3.3)."""
    alike(hub, 12)
    batch = draw(hub)
    flagged = items(hub, batch.batch_id, "sample")[0]
    following = next_top_up(hub, batch.batch_id, flagged.task_id)
    hub.tray.stage(
        flagged.task_id, "not_a_match", actor=STEWARD, split_on="birth_date", **seen(hub, flagged.task_id)
    )
    entry = hub.tray.stage(following, decision, actor=COORDINATOR, **seen(hub, following))
    assert entry.subject.get("split_on") is None and entry.subject.get("sample_batch") is None
    flush_past_window(hub)
    ended = fetch(hub, batch.batch_id)
    assert (ended.status, ended.outcome) == ("sampling", None)
    row = next(i for i in items(hub, batch.batch_id) if i.task_id == following)
    # never drawn; once its own decision commits, it leaves the batch
    assert (row.role, row.status, row.reason) == ("bulk", "excluded", "task_closed")
    assert len([i for i in items(hub, batch.batch_id, "sample") if i.status == "open"]) == 5


def test_a_decision_staged_before_a_top_up_drew_its_review_is_no_sample_outcome(hub: Hub) -> None:
    """A top-up that draws a review a steward staged a decision on meanwhile (outside the sample pane): the
    decision commits but writes no outcome, so the review turns void and is replaced, never splitting the
    batch (review 3.3)."""
    alike(hub, 12)
    batch = draw(hub)
    candidate = items(hub, batch.batch_id, "bulk")[0]
    entry = hub.tray.stage(
        candidate.task_id, "not_a_match", actor=COORDINATOR, **seen(hub, candidate.task_id)
    )
    assert "sample_batch" not in entry.subject or entry.subject["sample_batch"] is None
    # a top-up that checked the review just before the stage took its locks
    hub.store.update_items(
        batch.batch_id,
        [{"task_id": candidate.task_id, "role": "sample", "status": "open"}],
        from_status="candidate",
    )
    flush_past_window(hub)
    ended = hub.batches.refresh(batch.batch_id)
    assert (ended.status, ended.outcome) == ("sampling", None)
    row = next(i for i in items(hub, batch.batch_id) if i.task_id == candidate.task_id)
    assert (row.role, row.status, row.reason, row.split_on) == ("sample", "void", "task_closed", None)
    # a decision staged on the sample pane carries the batch, and counts
    sample = next(i for i in items(hub, batch.batch_id, "sample") if i.status == "open")
    staged = hub.tray.stage(sample.task_id, "link", actor=STEWARD, **seen(hub, sample.task_id))
    assert staged.subject["sample_batch"] == batch.batch_id


def test_a_candidate_closed_or_moved_outside_the_batch_leaves_it_and_a_staged_one_waits(hub: Hub) -> None:
    """A bulk candidate whose task closed, or whose record moved to another event, leaves a sampling batch at
    its next refresh, so n′ counts only reviews that can still be decided; one with a decision staged in the
    ordinary inbox stays, counted but never drawn, until that decision commits (review 3.3)."""
    from mdm.models.changes import WorkWrites

    alike(hub, 12)
    batch = draw(hub)
    closed, moved, staged = [i for i in items(hub, batch.batch_id, "bulk") if i.status == "candidate"][:3]
    with hub.store.transaction():
        hub.store.apply_work(WorkWrites("person", close_task_ids=(closed.task_id,)))
    state = hub.store.source_states("person", [moved.source])[moved.source]
    land(
        hub,
        [
            row(
                "crm",
                moved.source.key,
                "person",
                dict(state.values),
                at=state.occurred_at + timedelta(minutes=5),
            )
        ],
    )
    arrive(hub)
    hub.tray.stage(staged.task_id, "link", actor=COORDINATOR, **seen(hub, staged.task_id))
    assert hub.batches.refresh(batch.batch_id).status == "sampling"
    by_task = {i.task_id: i for i in items(hub, batch.batch_id)}
    assert (by_task[closed.task_id].status, by_task[closed.task_id].reason) == ("excluded", "task_closed")
    assert (by_task[moved.task_id].status, by_task[moved.task_id].reason) == ("excluded", "record_changed")
    assert by_task[staged.task_id].status == "candidate"  # counted, not drawn
    assert hub.batches.batch(batch.batch_id, actor=STEWARD).counts["excluded"] == 2
    flush_past_window(hub)  # the staged decision commits: its review leaves the batch in the same pass
    after = next(i for i in items(hub, batch.batch_id) if i.task_id == staged.task_id)
    assert (after.role, after.status, after.reason) == ("bulk", "excluded", "task_closed")
    assert fetch(hub, batch.batch_id).status == "sampling"


def test_a_batch_whose_candidates_were_decided_one_by_one_ends_too_few_left(hub: Hub) -> None:
    """Stewards link a sampling batch's bulk candidates one by one in the ordinary inbox, then a sample review
    disagrees: the sample is short, and with the decided candidates out of n′ the batch ends `too_few_left`
    and frees its group, rather than sampling for ever (review 3.3)."""
    alike(hub, 12)
    batch = draw(hub)
    candidates = [i for i in items(hub, batch.batch_id, "bulk") if i.status == "candidate"]
    assert len(candidates) == 7
    for item in candidates[:-1]:
        hub.tray.stage(item.task_id, "link", actor=COORDINATOR, **seen(hub, item.task_id))
        flush_past_window(hub)
    assert fetch(hub, batch.batch_id).status == "sampling"  # n′ is 6, above the sample of 5
    flagged = items(hub, batch.batch_id, "sample")[0]
    decide_sample(
        hub, batch.batch_id, actor=STEWARD, answers={flagged.task_id: ("not_a_match", "birth_date")}
    )
    ended = fetch(hub, batch.batch_id)
    assert (ended.status, ended.outcome) == ("discarded", "too_few_left")
    assert hub.store.open_batches_of_groups([batch.signature_key]) == {}
    gone = {
        i.task_id: i for i in items(hub, batch.batch_id) if i.task_id in {c.task_id for c in candidates[:-1]}
    }
    assert {(i.status, i.reason) for i in gone.values()} == {("excluded", "task_closed")}


def test_every_alike_review_discards_the_batch(hub: Hub) -> None:
    alike(hub, 12)
    batch = draw(hub)
    flagged = items(hub, batch.batch_id, "sample")[0]
    decide_sample(hub, batch.batch_id, actor=STEWARD, answers={flagged.task_id: ("not_a_match", "all")})
    ended = fetch(hub, batch.batch_id)
    assert (ended.status, ended.outcome) == ("discarded", "split_all")
    assert all(i.role == "split" for i in items(hub, batch.batch_id))
    assert hub.store.open_batches_of_groups([batch.signature_key]) == {}


def test_a_second_disagreement_gets_its_own_split(hub: Hub) -> None:
    alike(hub, 12)
    shared = same_birth(hub, 6)
    batch = draw(hub)
    first = a_sample_review_among(hub, batch.batch_id, shared)
    second = next(i for i in items(hub, batch.batch_id, "sample") if i.source not in shared)
    decide_sample(
        hub,
        batch.batch_id,
        actor=STEWARD,
        answers={first.task_id: ("not_a_match", "birth_date"), second.task_id: ("not_a_match", "given_name")},
    )
    after = items(hub, batch.batch_id)
    assert {i.reason for i in after if i.role == "split"} == {"split:birth_date", "split:given_name"}
    assert next(i for i in after if i.task_id == second.task_id).reason == "split:given_name"
    view = hub.batches.batch(batch.batch_id, actor=STEWARD)
    assert {line.on_label for line in view.splits} == {"birth date", "given name"}
    assert all(line.count for line in view.splits)


def test_too_few_left_after_a_split_ends_the_batch(hub: Hub) -> None:
    alike(hub, 5)
    shared = same_birth(hub, 6)
    batch = draw(hub)
    assert batch.population == 11
    flagged = a_sample_review_among(hub, batch.batch_id, shared)
    decide_sample(
        hub, batch.batch_id, actor=STEWARD, answers={flagged.task_id: ("not_a_match", "birth_date")}
    )
    ended = fetch(hub, batch.batch_id)
    assert (ended.status, ended.outcome) == ("discarded", "too_few_left")
    assert hub.store.open_batches_of_groups([batch.signature_key]) == {}
    with pytest.raises(Conflict) as again:  # free to draw, though too few are eligible now
        draw(hub)
    assert again.value.code == "group_too_small"


# ---------------------------------------------------------------------------------------------- every row's change


def test_preparation_shows_every_rows_change_and_one_target_takes_its_joiners_at_once(hub: Hub) -> None:
    workbench_world(hub, persons=4)
    alike_reviews(hub, 12, one_target=True)
    batch = ready(hub)
    view = hub.batches.prepare(batch.batch_id, actor=STEWARD)
    planned = [i for i in items(hub, batch.batch_id, "bulk") if i.status == "planned"]
    assert len(planned) == 7 and len({i.target for i in planned}) == 1
    target = planned[0].target
    assert view.summary is not None
    assert (view.summary.xrefs, view.summary.golden, view.summary.chunks) == (7, 1, 1)
    assert view.summary.left_out == {}
    page = hub.batches.rows(batch.batch_id, actor=STEWARD)
    assert len(page.rows) == 7 and page.after is None
    assert {r.joins for r in page.rows} == {6}
    assert all(r.impact.xrefs_added == 1 and r.status == "planned" for r in page.rows)
    assert [r.source for r in page.rows] == [i.source.text() for i in planned]  # by target, then due order
    expected = hub.lifecycle.values_with("person", target, [i.source for i in planned])
    hub.batches.stage(batch.batch_id, actor=STEWARD)
    flush_past_window(hub)
    assert fetch(hub, batch.batch_id).status == "committed"
    assert plain(dict(hub.store.golden("person", [target])[target].values)) == plain(expected)
    # the same values as linking them one by one: each single link recomputes the golden record from every member
    assert plain(expected) == plain(hub.lifecycle.values_with("person", target))
    # paged by position, never an offset
    first = hub.batches.rows(batch.batch_id, actor=STEWARD, limit=4)
    rest = hub.batches.rows(batch.batch_id, actor=STEWARD, after=first.after)
    assert [r.task_id for r in (*first.rows, *rest.rows)] == [r.task_id for r in page.rows]
    assert {r.status for r in rest.rows} == {"committed"}


def test_preparation_leaves_out_what_changed_since_the_draw_with_its_reason(hub: Hub) -> None:
    alike(hub, 12)
    batch = ready(hub)
    claimed, snoozed, escalated = items(hub, batch.batch_id, "bulk")[:3]
    hub.inbox.claim(claimed.task_id, actor=COORDINATOR)
    hub.inbox.snooze(snoozed.task_id, actor=COORDINATOR, hours=4)
    hub.inbox.escalate(escalated.task_id, actor=COORDINATOR, reason="second_opinion")
    view = hub.batches.prepare(batch.batch_id, actor=STEWARD)
    assert view.summary is not None and view.summary.xrefs == 4
    assert view.summary.left_out == {"claimed": 1, "escalated": 1, "snoozed": 1}
    assert view.counts["excluded"] == 3
    reasons = {i.task_id: i.reason for i in items(hub, batch.batch_id, "bulk") if i.status == "excluded"}
    assert reasons == {
        claimed.task_id: "claimed",
        snoozed.task_id: "snoozed",
        escalated.task_id: "escalated",
    }
    # the rows list what is planned; the left-out reviews stay in the inbox
    assert len(hub.batches.rows(batch.batch_id, actor=STEWARD).rows) == 4
    assert task(hub, claimed.task_id).status == "open"


def test_a_cannot_link_rule_keeps_two_joiners_of_one_target_apart(hub: Hub) -> None:
    workbench_world(hub, persons=4)
    keys = alike_reviews(hub, 6, one_target=True, refs={1: person_ref(5001), 2: person_ref(5002)})
    local = rehub(hub, forced_sample_base=1)
    batch = local.batches.draw(group_key(local), actor=STEWARD, entity="person")
    assert batch.sample_size == 1
    decide_sample(local, batch.batch_id, actor=STEWARD)
    assert local.batches.refresh(batch.batch_id).status == "ready"
    local.batches.prepare(batch.batch_id, actor=STEWARD)
    by_source = {i.source: i for i in items(local, batch.batch_id)}
    refs = [by_source[keys[1]], by_source[keys[2]]]
    left_out = [i for i in refs if i.status == "excluded"]
    assert len(left_out) == 1 and left_out[0].reason == "blocked"
    # tampered back to planned, it fails alone at its chunk's pre-check
    blocked = left_out[0]
    target = next(i.target for i in items(local, batch.batch_id) if i.status == "planned")
    version = local.store.golden("person", [target])[target].row_version
    local.store.update_items(
        batch.batch_id,
        [
            {
                "task_id": blocked.task_id,
                "status": "planned",
                "reason": None,
                "target": target,
                "target_version": version,
            }
        ],
        from_status="excluded",
    )
    local.batches.stage(batch.batch_id, actor=STEWARD)
    flush_past_window(local)
    after = {i.source: i for i in items(local, batch.batch_id)}
    assert (after[blocked.source].status, after[blocked.source].reason) == ("failed", "blocked")
    linked = [k for k in (keys[1], keys[2]) if master_of(local, "person", k.system, k.key) is not None]
    assert len(linked) == 1
    task_ = task(local, blocked.task_id)
    assert task_.status == "open" and task_.claimed_by is None
    assert not local.store.staged_by_locks([f"task:{blocked.task_id}"])


# ---------------------------------------------------------------------------------------------- staging and the tray


def locks_of(hub: Hub, batch_id: str) -> list[str]:
    return [
        s
        for i in items(hub, batch_id, "bulk")
        if i.status == "planned"
        for s in (f"task:{i.task_id}", f"source:person:{i.source.system}:{i.source.key}")
    ]


def test_staging_locks_every_review_and_labels_the_tray_entry(hub: Hub) -> None:
    alike(hub, 12)
    batch = prepared(hub)
    staged = hub.batches.stage(batch.batch_id, actor=STEWARD)
    assert staged.status == "staged" and staged.entry_id is not None
    held = hub.store.staged_by_locks(locks_of(hub, batch.batch_id))
    assert len(held) == 14 and {e.entry_id for e in held.values()} == {staged.entry_id}
    (entry,) = [e for e in hub.tray.entries(actor=STEWARD) if e.batch_id]
    assert entry.label == f"Link 7 alike reviews ({batch.batch_id})"
    assert (entry.status, entry.progress, entry.mine, entry.second_steward) == ("staged", (0, 1), True, None)
    # while it waits, nobody claims, snoozes, escalates or decides its reviews
    review = items(hub, batch.batch_id, "bulk")[0]
    for act in (
        lambda: hub.inbox.claim(review.task_id, actor=COORDINATOR),
        lambda: hub.inbox.snooze(review.task_id, actor=COORDINATOR, hours=1),
        lambda: hub.inbox.escalate(review.task_id, actor=COORDINATOR, reason="second_opinion"),
        lambda: hub.tray.stage(review.task_id, "link", actor=COORDINATOR, **seen(hub, review.task_id)),
    ):
        with pytest.raises(Conflict) as refused:
            act()
        assert refused.value.code == "already_staged" and refused.value.fields["batch"] == batch.batch_id
    row_ = hub.inbox.row(review.task_id, actor=STEWARD)
    assert row_.staged is not None and row_.staged.batch_id == batch.batch_id and row_.staged.mine
    case = hub.decisions.case(review.task_id, actor=COORDINATOR)
    assert all(not a.enabled for a in case.actions if a.decision == "link")


def test_a_review_claimed_or_held_since_preparation_is_left_out_at_staging(hub: Hub) -> None:
    alike(hub, 12)
    batch = prepared(hub)
    planned = [i for i in items(hub, batch.batch_id, "bulk") if i.status == "planned"]
    hub.inbox.claim(planned[0].task_id, actor=COORDINATOR)
    hub.tray.stage(planned[1].task_id, "link", actor=COORDINATOR, **seen(hub, planned[1].task_id))
    staged = hub.batches.stage(batch.batch_id, actor=STEWARD)
    assert staged.decisions == 5
    reasons = {i.task_id: i.reason for i in items(hub, batch.batch_id) if i.status == "excluded"}
    assert reasons == {planned[0].task_id: "claimed", planned[1].task_id: "staged"}
    assert staged.figures["left_out"] == {"claimed": 1, "staged": 1}


def test_a_preparation_racing_the_stage_waits_and_leaves_no_lock_behind(
    hub: Hub, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The maker's second tab prepares the batch again while it stages, just as another steward claims a planned
    review: the stage holds the batch row before it reads its reviews, so the preparation cannot commit in
    between, and no lock outlives the batch (review 3.3)."""
    alike(hub, 12)
    monkeypatch.setattr(capacity, "COMMIT_CHUNK_ROWS", 6)
    batch = prepared(hub)
    victim = next(i for i in items(hub, batch.batch_id, "bulk") if i.status == "planned")
    real = hub.store.staged_by_locks
    refused: list[str] = []

    def second_tab() -> None:
        try:
            hub.batches.prepare(batch.batch_id, actor=STEWARD)
        except Conflict as error:
            refused.append(error.code)

    tabs: list[threading.Thread] = []

    def meanwhile(locks):
        found = real(locks)
        if not tabs:
            tabs.append(threading.Thread(target=second_tab))
            hub.inbox.claim(victim.task_id, actor=COORDINATOR)
            tabs[0].start()
            tabs[0].join(0.5)  # time enough to commit, were it not held back
        return found

    monkeypatch.setattr(hub.store, "staged_by_locks", meanwhile)
    staged = hub.batches.stage(batch.batch_id, actor=STEWARD)
    monkeypatch.setattr(hub.store, "staged_by_locks", real)
    join_all(tabs)
    assert refused and refused[0] in ("not_ready", "batch_changed")
    assert staged.status == "staged"
    for _ in range(6):
        flush_past_window(hub)
        if fetch(hub, batch.batch_id).status == "committed":
            break
    assert fetch(hub, batch.batch_id).status == "committed"
    for item in items(hub, batch.batch_id, "bulk"):
        if task(hub, item.task_id).status == "open":  # its task can be claimed again: no lock is left
            assert hub.store.claim_task(item.task_id, COORDINATOR.name, hub.inbox.clock(), hub.inbox.clock())


def test_undo_from_a_review_the_tray_or_the_page_returns_the_batch_to_ready(hub: Hub) -> None:
    alike(hub, 12)
    batch = prepared(hub)
    review = items(hub, batch.batch_id, "bulk")[0]
    for undo in (
        lambda entry: hub.tray.undo_for_task(review.task_id, actor=STEWARD),
        lambda entry: hub.tray.undo(entry, actor=STEWARD),
    ):
        staged = hub.batches.stage(batch.batch_id, actor=STEWARD)
        done = undo(staged.entry_id)
        assert done is not None and (done.status, done.outcome) == ("undone", "undone")
        again = fetch(hub, batch.batch_id)
        assert (again.status, again.entry_id, again.checker) == ("ready", None, None)
        assert not hub.store.staged_by_locks(locks_of(hub, batch.batch_id))
    # another steward may not undo it, and U on one of its reviews undoes nothing of theirs instead: it is
    # refused as another steward's batch (review 3.3); nothing commits after an undo
    staged = hub.batches.stage(batch.batch_id, actor=STEWARD)
    with pytest.raises(Forbidden):
        hub.tray.undo(staged.entry_id, actor=COORDINATOR)
    elsewhere = person_review_elsewhere(hub)
    own = hub.tray.stage(elsewhere, "not_a_match", actor=COORDINATOR, **seen(hub, elsewhere))
    with pytest.raises(Conflict) as other:
        hub.tray.undo_for_task(review.task_id, actor=COORDINATOR)
    assert (other.value.code, other.value.fields["mine"], other.value.fields["batch"]) == (
        "already_staged",
        False,
        batch.batch_id,
    )
    assert hub.store.tray_entries([own.entry_id])[own.entry_id].status == "staged"  # theirs still waits
    case = hub.decisions.case(review.task_id, actor=COORDINATOR)
    assert case.staged is not None and case.staged.batch_id == batch.batch_id and not case.staged.mine
    why = {a.decision: a.why_not for a in case.actions}
    assert why["link"] == f"It is part of another steward's batch, {batch.batch_id}. Pick another task."
    mine = hub.decisions.case(review.task_id, actor=STEWARD)
    assert mine.staged is not None and mine.staged.mine
    assert {a.decision: a.why_not for a in mine.actions}["link"] == (
        f"It is part of batch {batch.batch_id}. Undo the batch to decide it on its own."
    )
    hub.tray.undo(own.entry_id, actor=COORDINATOR)
    hub.tray.undo(staged.entry_id, actor=STEWARD)
    version = hub.store.last_commit_version()
    report = flush_past_window(hub)
    assert report.chunks == 0 and hub.store.last_commit_version() == version


def test_undo_last_never_reaches_a_batch(hub: Hub) -> None:
    alike(hub, 12)
    batch = prepared(hub)
    hub.batches.stage(batch.batch_id, actor=STEWARD)
    assert hub.tray.undo_last(actor=STEWARD) is None  # the batch is the steward's newest: nothing else waits
    assert fetch(hub, batch.batch_id).status == "staged"


def test_undo_last_undoes_the_makers_single_decision_not_a_batch_a_second_steward_confirmed(hub: Hub) -> None:
    alike(hub, 12)
    other = task(hub, person_review_elsewhere(hub))
    local = rehub(hub, batch_checker_above=3)
    batch = prepared(local)
    single = local.tray.stage(
        other.task_id, "link", actor=STEWARD, target=other.master_ids[0], **seen(local, other.task_id)
    )
    later = single.staged_at + timedelta(seconds=5)
    local.batches.clock = lambda: later  # the tray's clock ran ahead through the sample's flushes
    local.batches.stage(batch.batch_id, actor=STEWARD)
    confirmed = local.batches.confirm(batch.batch_id, actor=COORDINATOR)  # now the maker's newest entry
    newest = local.tray.entries(actor=STEWARD)[0]
    assert newest.batch_id == batch.batch_id and newest.second_steward == "coordinating_steward"
    undone = local.tray.undo_last(actor=STEWARD)
    assert undone is not None and undone.entry_id == single.entry_id
    assert fetch(local, batch.batch_id).status == "staged"
    # the second steward's U with nothing of their own staged never reaches the batch either
    assert local.tray.undo_last(actor=COORDINATOR) is None
    assert local.store.tray_entries([confirmed.entry_id])[confirmed.entry_id].status == "staged"
    # the batch is theirs to undo, from one of its rows: its refusal says so; to anyone else it is another's
    review = next(i for i in items(local, batch.batch_id, "bulk") if i.status == "planned")
    for actor, mine in ((COORDINATOR, True), (STEWARD, True), (SECOND_STEWARD, False)):
        with pytest.raises(Conflict) as held:
            local.inbox.claim(review.task_id, actor=actor)
        assert held.value.fields["mine"] is mine and held.value.fields["batch"] == batch.batch_id
        with pytest.raises(Conflict) as decided:
            local.tray.stage(review.task_id, "link", actor=actor, **seen(local, review.task_id))
        assert decided.value.fields["mine"] is mine
    assert local.inbox.row(review.task_id, actor=COORDINATOR).staged.mine


def test_after_the_first_chunk_undo_is_refused_and_both_trays_list_the_batch(
    hub: Hub, monkeypatch: pytest.MonkeyPatch
) -> None:
    alike(hub, 12)
    local = rehub(hub, batch_checker_above=3)
    monkeypatch.setattr(capacity, "COMMIT_CHUNK_ROWS", 6)
    batch = prepared(local)
    local.batches.stage(batch.batch_id, actor=STEWARD)
    confirmed = local.batches.confirm(batch.batch_id, actor=COORDINATOR)
    report = flush_past_window(local)
    assert (report.chunks, report.committed) == (1, 1)
    committing = fetch(local, batch.batch_id)
    assert (committing.status, committing.chunks_committed) == ("committing", 1)
    entry = local.store.tray_entries([confirmed.entry_id])[confirmed.entry_id]
    assert (entry.status, entry.outcome) == ("committed", "committing")
    with pytest.raises(Conflict) as late:
        local.tray.undo(entry.entry_id, actor=STEWARD)
    assert late.value.code == "already_settled" and late.value.fields["batch"] == batch.batch_id
    mine = [e for e in local.tray.entries(actor=STEWARD) if e.batch_id == batch.batch_id]
    theirs = [e for e in local.tray.entries(actor=COORDINATOR) if e.batch_id == batch.batch_id]
    assert len(mine) == len(theirs) == 1
    assert mine[0].mine and not theirs[0].mine and theirs[0].second_steward == "coordinating_steward"
    assert local.inbox.counts(actor=COORDINATOR).tray_live == 1
    assert local.inbox.counts(actor=STEWARD).tray_live == 1


# ---------------------------------------------------------------------------------------------- the second steward


def test_above_the_threshold_a_second_steward_confirms(hub: Hub) -> None:
    alike(hub, 12)
    local = rehub(hub, batch_checker_above=3)
    batch = prepared(local)
    waiting = local.batches.stage(batch.batch_id, actor=STEWARD)
    assert waiting.status == "awaiting_checker"
    view = local.batches.batch(batch.batch_id, actor=STEWARD)
    assert [(a.decision, a.enabled) for a in view.actions] == [("confirm", False), ("discard", True)]
    with pytest.raises(Forbidden) as own:
        local.batches.confirm(batch.batch_id, actor=STEWARD)
    assert own.value.code == "checker_is_maker"
    assert local.inbox.counts(actor=COORDINATOR).batches_to_confirm == 1
    listed = local.batches.groups(actor=COORDINATOR).to_confirm
    assert [b.batch_id for b in listed] == [batch.batch_id]
    # a role that may not confirm hears of no batch waiting for it (review 3.3)
    for reader in (OWNER, TECHNICAL):
        assert local.inbox.counts(actor=reader).batches_to_confirm == 0
        assert local.batches.groups(actor=reader).to_confirm == ()
    sent = local.batches.send_back(batch.batch_id, actor=COORDINATOR)
    assert sent.status == "ready" and sent.figures["sent_back"] == 1
    # the second steward may undo the batch they confirmed, from their tray: it is ready again
    local.batches.stage(batch.batch_id, actor=STEWARD)
    undone = local.batches.confirm(batch.batch_id, actor=COORDINATOR)
    assert undone.entry_id is not None
    assert local.tray.undo(undone.entry_id, actor=COORDINATOR).status == "undone"
    assert fetch(local, batch.batch_id).status == "ready"
    # the undone entry stays in both trays with its outcome, as the maker's always did (review 3.3)
    for actor, mine in ((STEWARD, True), (COORDINATOR, False)):
        line = next(e for e in local.tray.entries(actor=actor) if e.entry_id == undone.entry_id)
        assert (line.status, line.batch_id, line.mine) == ("undone", batch.batch_id, mine)
    # a stale Undo from the second steward's tray reads "already undone", never "not yours"
    with pytest.raises(Conflict) as again:
        local.tray.undo(undone.entry_id, actor=COORDINATOR)
    assert (again.value.code, again.value.fields["status"]) == ("already_settled", "undone")
    local.batches.stage(batch.batch_id, actor=STEWARD)
    confirmed = local.batches.confirm(batch.batch_id, actor=COORDINATOR)
    assert (confirmed.status, confirmed.checker, confirmed.checker_role) == (
        "staged",
        COORDINATOR.name,
        "coordinating_steward",
    )
    flush_past_window(local)
    done = fetch(local, batch.batch_id)
    assert done.status == "committed"
    for chunk_ in local.store.batch_chunks(batch.batch_id):
        audit = local.store.change_sets([chunk_.change_set_id])[chunk_.change_set_id]
        assert audit["checker"] == COORDINATOR.name
        (log,) = local.store.commits_by_version([chunk_.commit_version])
        assert log.authority_ref == "data_steward; checker coordinating_steward"


def test_a_chunk_without_the_recorded_checker_is_refused_at_commit(hub: Hub) -> None:
    alike(hub, 12)
    local = rehub(hub, batch_checker_above=3)
    batch = prepared(local)
    local.batches.stage(batch.batch_id, actor=STEWARD)
    local.batches.confirm(batch.batch_id, actor=COORDINATOR)
    assert local.store.set_batch(batch.batch_id, from_statuses=("staged",), checker=None, checker_role=None)
    version = local.store.last_commit_version()
    flush_past_window(local)
    ended = fetch(local, batch.batch_id)
    assert (ended.status, ended.outcome) == ("failed", "checker_required")
    assert local.store.last_commit_version() == version


# ---------------------------------------------------------------------------------------------- the commit


def test_chunks_commit_one_a_pass_each_its_own_change_set(hub: Hub, monkeypatch: pytest.MonkeyPatch) -> None:
    alike(hub, 12)
    monkeypatch.setattr(capacity, "COMMIT_CHUNK_ROWS", 6)
    batch = prepared(hub)
    assert batch.chunks == 4  # 7 links to 7 golden records, each 3 rows at most: two a chunk
    staged = hub.batches.stage(batch.batch_id, actor=STEWARD)
    hex_ = batch.batch_id[4:]
    versions = []
    for number in range(1, 5):
        # a single decision flushes between two chunks of the batch
        other = next(
            (
                t
                for t in open_tasks(hub, "person", "review")
                if t.task_id not in {i.task_id for i in items(hub, batch.batch_id)}
            ),
            None,
        )
        report = flush_past_window(hub)
        assert report.chunks == 1, number
        chunks = hub.store.batch_chunks(batch.batch_id)
        assert [c.chunk_no for c in chunks] == list(range(1, number + 1))
        latest = chunks[-1]
        assert latest.change_set_id == f"CS-{hex_}-{number}" and latest.rows <= 6 and latest.items <= 6
        versions.append(latest.commit_version)
        committed = [i for i in items(hub, batch.batch_id, "bulk") if i.status == "committed"]
        assert all(task(hub, i.task_id).status == "closed" for i in committed)
        assert not hub.store.staged_by_locks([f"task:{i.task_id}" for i in committed])
        del other
    assert len(set(versions)) == 4
    done = fetch(hub, batch.batch_id)
    assert (done.status, done.outcome, done.chunks_committed) == ("committed", "committed", 4)
    entry = hub.store.tray_entries([staged.entry_id])[staged.entry_id]
    assert (entry.status, entry.outcome) == ("committed", "committed")
    labels = hub.store.labels_for("person", [i.source.text() for i in items(hub, batch.batch_id, "bulk")])
    assert len(labels) == 7 and {lab.signature for lab in labels} == {ALIKE_SIGNATURE}
    assert {lab.entry_id for lab in labels} == {staged.entry_id}
    assert hub.store.open_batches_of_groups([batch.signature_key]) == {}
    target = items(hub, batch.batch_id, "bulk")[0].target
    timeline = hub.lookup.timeline("person", target, actor=STEWARD)
    # the chunk's event names its batch and chunk, whether its headline is a members change or updated values
    (event,) = [e for e in timeline.events if f"in batch {batch.batch_id}, chunk " in e.authority]
    assert event.authority.startswith("Data steward") and event.authority.endswith(" of 4")
    assert "Decided; nothing published" not in event.headline


def test_single_decisions_flush_between_a_batchs_chunks(hub: Hub, monkeypatch: pytest.MonkeyPatch) -> None:
    alike(hub, 12)
    workbench_world_extra = person_review_elsewhere(hub)
    monkeypatch.setattr(capacity, "COMMIT_CHUNK_ROWS", 6)
    batch = prepared(hub)
    hub.batches.stage(batch.batch_id, actor=STEWARD)
    first = flush_past_window(hub)
    assert first.chunks == 1
    other = task(hub, workbench_world_extra)
    hub.tray.stage(
        other.task_id, "link", actor=COORDINATOR, target=other.master_ids[0], **seen(hub, other.task_id)
    )
    second = flush_past_window(hub)
    assert second.committed == 1 and second.chunks == 1
    assert fetch(hub, batch.batch_id).chunks_committed == 2


def person_review_elsewhere(hub: Hub) -> str:
    """A review of another entity, outside the batch (an organisation close call): its task ID."""
    from tests.helpers import organisation_close_call

    source = organisation_close_call(hub)
    return next(t.task_id for t in open_tasks(hub, "organisation", "review") if t.source == source)


# ---------------------------------------------------------------------------------------------- exactly once


def test_a_crash_after_a_chunk_resumes_at_the_next_chunk(hub: Hub, monkeypatch: pytest.MonkeyPatch) -> None:
    alike(hub, 12)
    monkeypatch.setattr(capacity, "COMMIT_CHUNK_ROWS", 6)
    batch = prepared(hub)
    hub.batches.stage(batch.batch_id, actor=STEWARD)

    def fault(point: str) -> None:
        if point == "after_chunk":
            raise Crash(point)

    hub.batches.fault = fault
    with pytest.raises(Crash):
        flush_past_window(hub)
    hub.batches.fault = None
    assert [c.chunk_no for c in hub.store.batch_chunks(batch.batch_id)] == [1]
    flush_past_window(hub)
    assert [c.chunk_no for c in hub.store.batch_chunks(batch.batch_id)] == [1, 2]


def test_a_review_that_moved_after_preparation_fails_alone(hub: Hub) -> None:
    alike(hub, 12)
    batch = prepared(hub)
    moved = next(i for i in items(hub, batch.batch_id, "bulk") if i.status == "planned")
    state = hub.store.source_states("person", [moved.source])[moved.source]
    land(
        hub,
        [
            row(
                "crm",
                moved.source.key,
                "person",
                dict(state.values),
                at=state.occurred_at + timedelta(minutes=5),
            )
        ],
    )
    hub.arrival.intake(hub.store.landing_above(0, 100_000))  # its event moves; its task waits for arrival
    hub.batches.stage(batch.batch_id, actor=STEWARD)
    flush_past_window(hub)
    after = next(i for i in items(hub, batch.batch_id) if i.task_id == moved.task_id)
    assert (after.status, after.reason) == ("failed", "record_changed")
    assert fetch(hub, batch.batch_id).status == "committed"
    assert task(hub, moved.task_id).status == "open"
    assert not hub.store.staged_by_locks([f"task:{moved.task_id}"])
    view = hub.batches.batch(batch.batch_id, actor=STEWARD)
    assert view.counts["failed"] == 1 and view.counts["committed"] == 6


def test_when_every_review_moves_after_the_first_chunk_the_batch_ends_committed(
    hub: Hub, monkeypatch: pytest.MonkeyPatch
) -> None:
    alike(hub, 12)
    monkeypatch.setattr(capacity, "COMMIT_CHUNK_ROWS", 6)
    batch = prepared(hub)
    staged = hub.batches.stage(batch.batch_id, actor=STEWARD)
    flush_past_window(hub)
    rest = [i for i in items(hub, batch.batch_id, "bulk") if i.status == "planned"]
    assert rest
    for item in rest:
        state = hub.store.source_states("person", [item.source])[item.source]
        land(
            hub,
            [
                row(
                    "crm",
                    item.source.key,
                    "person",
                    dict(state.values),
                    at=state.occurred_at + timedelta(minutes=5),
                )
            ],
        )
    hub.arrival.intake(hub.store.landing_above(0, 100_000))
    flush_past_window(hub)
    done = fetch(hub, batch.batch_id)
    assert (done.status, done.outcome, done.chunks_committed) == ("committed", "committed", 1)
    entry = hub.store.tray_entries([staged.entry_id])[staged.entry_id]
    assert (entry.status, entry.outcome) == ("committed", "committed")
    assert hub.store.open_batches_of_groups([batch.signature_key]) == {}
    assert not hub.store.staged_by_locks(locks_of(hub, batch.batch_id) + [f"task:{i.task_id}" for i in rest])
    assert hub.store.batches_due(hub.tray.clock() + timedelta(days=1), 10) == []


def test_a_conflict_inside_a_chunk_is_planned_again(hub: Hub, monkeypatch: pytest.MonkeyPatch) -> None:
    alike(hub, 12)
    batch = prepared(hub)
    hub.batches.stage(batch.batch_id, actor=STEWARD)
    original = hub.commit.apply
    calls = {"n": 0}

    def once(cs, work=None, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise Conflict(["x"], code="stale_row")
        return original(cs, work, **kwargs)

    monkeypatch.setattr(hub.commit, "apply", once)
    flush_past_window(hub)
    assert calls["n"] == 2 and fetch(hub, batch.batch_id).status == "committed"


def test_three_failed_passes_in_a_row_stop_the_batch(hub: Hub, monkeypatch: pytest.MonkeyPatch) -> None:
    alike(hub, 12)
    monkeypatch.setattr(capacity, "COMMIT_CHUNK_ROWS", 6)
    batch = prepared(hub)
    hub.batches.stage(batch.batch_id, actor=STEWARD)
    original = hub.commit.apply
    failing = {"on": False}

    def maybe(cs, work=None, **kwargs):
        if failing["on"]:
            raise Crash("deadlock")
        return original(cs, work, **kwargs)

    monkeypatch.setattr(hub.commit, "apply", maybe)
    flush_past_window(hub)  # chunk 1
    failing["on"] = True
    flush_past_window(hub)
    flush_past_window(hub)
    assert fetch(hub, batch.batch_id).attempts == 2
    failing["on"] = False
    flush_past_window(hub)  # chunk 2 commits and sets the count back to 0
    assert fetch(hub, batch.batch_id).attempts == 0
    failing["on"] = True
    flush_past_window(hub)
    flush_past_window(hub)
    assert fetch(hub, batch.batch_id).status == "committing"
    flush_past_window(hub)
    stopped = fetch(hub, batch.batch_id)
    assert (stopped.status, stopped.outcome, stopped.chunks_committed) == ("stopped", "chunk_failed", 2)
    released = [i for i in items(hub, batch.batch_id, "bulk") if i.status == "released"]
    assert released and all(task(hub, i.task_id).status == "open" for i in released)


def test_a_stale_first_chunk_refused_after_an_undo_ends_nothing_staged_since(hub: Hub) -> None:
    """While the first chunk of entry E1 is on its way, the maker undoes E1, stages the batch again and another
    steward confirms it (E2): the stale chunk is refused for its checker, and that refusal ends E1's staging
    only, never E2 (review 3.3)."""
    alike(hub, 12)
    local = rehub(hub, batch_checker_above=3)
    batch = prepared(local)
    local.batches.stage(batch.batch_id, actor=STEWARD)
    first = local.batches.confirm(batch.batch_id, actor=COORDINATOR).entry_id
    assert first is not None
    again: dict[str, str | None] = {}

    def meanwhile(point: str) -> None:
        if point == "before_write" and not again:
            again["entry"] = None
            local.tray.undo(first, actor=STEWARD)
            local.batches.stage(batch.batch_id, actor=STEWARD)
            again["entry"] = local.batches.confirm(batch.batch_id, actor=SECOND_STEWARD).entry_id

    local.commit.fault = meanwhile
    flush_past_window(local)
    local.commit.fault = None
    second = again["entry"]
    assert second is not None
    after = fetch(local, batch.batch_id)
    assert (after.status, after.entry_id, after.checker, after.outcome) == (
        "staged",
        second,
        SECOND_STEWARD.name,
        None,
    )
    entries = local.store.tray_entries([first, second])
    assert (entries[first].status, entries[second].status) == ("undone", "staged")
    assert local.store.open_batches_of_groups([batch.signature_key])  # the group still holds the batch
    flush_past_window(local)  # E2's own window passes: it commits
    assert fetch(local, batch.batch_id).status == "committed"


def test_an_undo_clears_the_failed_passes_of_its_staging(hub: Hub, monkeypatch: pytest.MonkeyPatch) -> None:
    """Failed passes count against one staging only: after an undo, a new staging starts from none (review
    3.3)."""
    alike(hub, 12)
    batch = prepared(hub)
    first = hub.batches.stage(batch.batch_id, actor=STEWARD).entry_id
    assert first is not None
    original = hub.commit.apply
    failing = {"passes": 2}

    def flaky(cs, work=None, **kwargs):
        if failing["passes"] > 0:
            failing["passes"] -= 1
            raise Crash("transient")
        return original(cs, work, **kwargs)

    monkeypatch.setattr(hub.commit, "apply", flaky)
    flush_past_window(hub)
    flush_past_window(hub)
    assert fetch(hub, batch.batch_id).attempts == 2
    hub.tray.undo(first, actor=STEWARD)
    assert fetch(hub, batch.batch_id).attempts == 0
    hub.batches.stage(batch.batch_id, actor=STEWARD)
    failing["passes"] = 1
    flush_past_window(hub)
    after = fetch(hub, batch.batch_id)
    assert (after.status, after.attempts) == ("staged", 1)
    flush_past_window(hub)
    assert fetch(hub, batch.batch_id).status == "committed"


def test_a_personas_batch_is_refused_on_a_shared_store(hub: Hub) -> None:
    alike(hub, 12)
    batch = prepared(hub)
    staged = hub.batches.stage(batch.batch_id, actor=STEWARD)

    class Shared:
        local_mode = False

        def __getattr__(self, name):
            return getattr(hub.settings, name)

    hub.batches.settings = Shared()
    flush_past_window(hub)
    ended = fetch(hub, batch.batch_id)
    assert (ended.status, ended.outcome) == ("failed", "persona_refused")
    entry = hub.store.tray_entries([staged.entry_id])[staged.entry_id]
    assert (entry.status, entry.outcome) == ("failed", "persona_refused")


# ---------------------------------------------------------------------------------------------- Stop and the throttle


def no_sleep(seconds: float) -> None:
    raise AssertionError("the flush never sleeps")


def throttled(hub: Hub, monkeypatch: pytest.MonkeyPatch) -> tuple[Hub, Batch]:
    """A batch in chunks of two links whose first chunk committed, the throttle holding the next for hours."""
    alike(hub, 12)
    local = rehub(hub, throttle_rows_per_hour=1)
    monkeypatch.setattr(capacity, "COMMIT_CHUNK_ROWS", 6)
    monkeypatch.setattr(time, "sleep", no_sleep)
    batch = prepared(local)
    local.batches.stage(batch.batch_id, actor=STEWARD)
    with pytest.raises(Conflict) as early:
        local.batches.stop(batch.batch_id, actor=STEWARD)
    assert early.value.code == "still_in_tray"
    assert flush_past_window(local).chunks == 1
    held = fetch(local, batch.batch_id)
    assert held.not_before is not None and held.not_before > local.tray.clock() + timedelta(hours=1)
    assert flush_past_window(local).chunks == 0  # the throttle holds the next chunk; the flush never sleeps
    assert fetch(local, batch.batch_id).chunks_committed == 1
    return local, batch


def test_stop_ends_a_throttled_batch_at_the_next_pass(hub: Hub, monkeypatch: pytest.MonkeyPatch) -> None:
    local, batch = throttled(hub, monkeypatch)
    asked = local.batches.stop(batch.batch_id, actor=COORDINATOR)  # any steward may stop it
    assert asked.stop_requested_by == COORDINATOR.name and asked.not_before is None
    view = local.batches.batch(batch.batch_id, actor=STEWARD)
    assert view.progress is not None and view.progress.stop_requested
    assert [(a.decision, a.enabled) for a in view.actions] == [("stop", False)]
    report = flush_past_window(local)
    assert report.batches_finished == 1
    stopped = fetch(local, batch.batch_id)
    assert (stopped.status, stopped.outcome, stopped.chunks_committed) == ("stopped", "stopped", 1)
    released = [i for i in items(local, batch.batch_id, "bulk") if i.status == "released"]
    committed = [i for i in items(local, batch.batch_id, "bulk") if i.status == "committed"]
    assert len(committed) == 2 and len(released) == 5
    for item in released:
        found = task(local, item.task_id)
        assert found.status == "open" and found.claimed_by is None
    assert not local.store.staged_by_locks([f"task:{i.task_id}" for i in released])
    entry = local.store.tray_entries([stopped.entry_id])[stopped.entry_id]
    assert (entry.status, entry.outcome) == ("committed", "stopped")
    with pytest.raises(Conflict) as done:
        local.batches.stop(batch.batch_id, actor=STEWARD)
    assert done.value.code == "not_committing"


def test_a_withdrawal_of_bulk_rights_stops_a_throttled_batch(
    hub: Hub, monkeypatch: pytest.MonkeyPatch
) -> None:
    local, batch = throttled(hub, monkeypatch)
    local.breaker.demo_withdraw("person", batch.signature_key)
    flush_past_window(local)
    stopped = fetch(local, batch.batch_id)
    assert (stopped.status, stopped.outcome, stopped.chunks_committed) == ("stopped", "bulk_withdrawn", 1)


# ---------------------------------------------------------------------------------------------- 2% to blind review


def test_a_share_of_a_batch_goes_to_blind_review_neither_steward_answers(hub: Hub) -> None:
    alike(hub, 12)
    local = rehub(hub, sample_share=0.3, batch_checker_above=3)
    batch = prepared(local)
    assert batch.figures["reviews"] == 3  # ⌈0.3 × 7⌉
    local.batches.stage(batch.batch_id, actor=STEWARD)
    confirmed = local.batches.confirm(batch.batch_id, actor=COORDINATOR)
    assert sum(1 for i in items(local, batch.batch_id, "bulk") if i.review) == 3
    flush_past_window(local)
    assert fetch(local, batch.batch_id).status == "committed"
    sources = [i.source for i in items(local, batch.batch_id, "bulk")]
    drawn = [s for s in local.store.open_samples_for("person", sources) if s.origin == "batch"]
    assert len(drawn) == 3
    for sample in drawn:
        assert (sample.decided_by, sample.checked_by, sample.entry_id) == (
            STEWARD.name,
            COORDINATOR.name,
            confirmed.entry_id,
        )
        assert sample.signature == ALIKE_SIGNATURE and sample.decision == "link"
    sample = drawn[0]
    with pytest.raises(Forbidden) as maker:
        local.tray.stage(sample.task_id, "blind_none", actor=STEWARD, **seen(local, sample.task_id))
    assert maker.value.code == "own_decision"
    with pytest.raises(Forbidden) as checker:
        local.tray.stage(sample.task_id, "blind_none", actor=COORDINATOR, **seen(local, sample.task_id))
    assert checker.value.code == "own_batch"
    for actor in (STEWARD, COORDINATOR):
        listed = local.inbox.page("samples", actor=actor)
        assert not {r.task_id for r in listed.rows} & {s.task_id for s in drawn}
    # a third steward answers them on a local store: the second data-steward persona, as the workbench's persona
    # menu and `--as data_steward_2` produce it (review 3.3)
    third = local.authority.actor_for_request(persona="data_steward_2", forwarded_user=None)
    assert third == SECOND_STEWARD
    listed = local.inbox.page("samples", actor=third)
    assert {s.task_id for s in drawn} <= {r.task_id for r in listed.rows}
    answer_blind(local, sample, agree=True)
    answered = local.store.samples_by_id([sample.sample_id])[sample.sample_id]
    assert answered.status != "open"


def test_a_review_drawn_for_blind_review_that_fails_passes_its_draw_on(hub: Hub) -> None:
    """The one review of a small batch drawn for blind review moves before the flush: it fails alone, and the
    planned review with the next smallest draw value goes to blind review instead (review 3.3)."""
    alike(hub, 12)
    local = rehub(hub, sample_share=0.02)
    batch = prepared(local)
    local.batches.stage(batch.batch_id, actor=STEWARD)
    (flagged,) = [i for i in items(local, batch.batch_id, "bulk") if i.review]
    planned = [i for i in items(local, batch.batch_id, "bulk") if i.status == "planned"]
    key = local.settings.sample_key
    heir = min(
        (i for i in planned if i.task_id != flagged.task_id),
        key=lambda i: (draw_value("person", i.source.text(), "batch_link", batch.batch_id, key), i.task_id),
    )
    state = local.store.source_states("person", [flagged.source])[flagged.source]
    land(
        local,
        [
            row(
                "crm",
                flagged.source.key,
                "person",
                dict(state.values),
                at=state.occurred_at + timedelta(minutes=5),
            )
        ],
    )
    local.arrival.intake(local.store.landing_above(0, 100_000))
    flush_past_window(local)
    assert fetch(local, batch.batch_id).status == "committed"
    after = {i.task_id: i for i in items(local, batch.batch_id, "bulk")}
    assert (after[flagged.task_id].status, after[flagged.task_id].reason) == ("failed", "record_changed")
    linked = [i for i in after.values() if i.status == "committed"]
    assert len(linked) == len(planned) - 1
    assert [i.task_id for i in linked if i.review] == [heir.task_id]
    drawn = [
        s for s in local.store.open_samples_for("person", [i.source for i in linked]) if s.origin == "batch"
    ]
    assert [s.source for s in drawn] == [heir.source]  # ⌈2% × 6⌉ = 1 of the committed links
    assert local.batches.batch(batch.batch_id, actor=STEWARD).blind_reviews == 1


# ---------------------------------------------------------------------------------------------- bulk rights


def answer_blind(hub: Hub, sample, *, agree: bool) -> None:
    decision = "blind_link" if agree else "blind_none"
    target = sample.target if agree else None
    hub.tray.stage(sample.task_id, decision, actor=SECOND_STEWARD, target=target, **seen(hub, sample.task_id))
    flush_past_window(hub)


def test_two_disagreeing_blind_answers_in_five_withdraw_bulk_rights(hub: Hub) -> None:
    alike(hub, 12)
    local = rehub(hub, sample_share=1.0, bulk_min_samples=5)
    batch = prepared(local)
    local.batches.stage(batch.batch_id, actor=STEWARD)
    flush_past_window(local)
    sources = [i.source for i in items(local, batch.batch_id, "bulk")]
    drawn = sorted(
        (s for s in local.store.open_samples_for("person", sources) if s.origin == "batch"),
        key=lambda s: s.sample_id,
    )
    assert len(drawn) == 7
    band = bulk_band("person", ALIKE_SIGNATURE)
    for sample, agree in zip(drawn[:5], (True, True, True, False, False), strict=True):
        assert (
            local.breaker.bulk("person", ALIKE_SIGNATURE) is None
            or not local.breaker.bulk("person", ALIKE_SIGNATURE).demoted
        )
        answer_blind(local, sample, agree=agree)
    withdrawn = local.store.breaker_state("person", band)
    assert withdrawn is not None and withdrawn.demoted and withdrawn.signature == ALIKE_SIGNATURE
    # the group's agreement figure counts the answers by origin
    version = local.registry.published("person").match.version
    figures = local.batches._group_row(batch.signature_key, "person", ALIKE_SIGNATURE, version, None)  # noqa: SLF001
    assert figures.agreement.by_origin["batch"] == (3, 5)
    assert (figures.agreement.agreed, figures.agreement.reviewed) >= (3, 5)
    assert figures.withdrawn is not None and figures.withdrawn.key == band
    audit = local.store.change_sets([withdrawn.trip_change_set])[withdrawn.trip_change_set]
    assert (audit["action"], audit["reason"]) == ("breaker_trip", "bulk_agreement_low")
    assert audit["evidence"]["band"] == band and audit["evidence"]["reviewed"] == 5
    assert "given_name" not in str(audit["evidence"])  # the key, never the signature
    status = local.breaker.status(["person"], actor=OWNER)[0]
    assert [b.key for b in status.withdrawn] == [band] and status.state == "normal"
    # only a data owner restores them, for a cause fixed or a false alarm
    with pytest.raises(Forbidden):
        local.breaker.restore("person", actor=STEWARD, reason="cause_fixed", bulk=band)
    with pytest.raises(Forbidden) as load:
        local.breaker.restore("person", actor=OWNER, reason="load_expected", bulk=band)
    assert load.value.code == "bad_restore_reason"
    with pytest.raises(Forbidden) as key:
        local.breaker.restore("person", actor=OWNER, reason="cause_fixed", bulk="bulk:nothex")
    assert key.value.code == "bad_bulk_key"
    local.breaker.clock = local.tray.clock  # the restore comes after every answer, on the flush's clock
    restored = local.breaker.restore("person", actor=OWNER, reason="cause_fixed", bulk=band)
    assert restored.state == "normal" and restored.restore_reason == "cause_fixed"
    # after a restore the older answers no longer count
    assert local.breaker.bulk_agreement("person", ALIKE_SIGNATURE) == (0, 0)
    assert local.breaker.check_signature("person", ALIKE_SIGNATURE) is None


def test_while_withdrawn_staging_is_refused_and_a_committing_batch_stops(
    hub: Hub, monkeypatch: pytest.MonkeyPatch
) -> None:
    alike(hub, 12)
    monkeypatch.setattr(capacity, "COMMIT_CHUNK_ROWS", 6)
    batch = prepared(hub)
    hub.breaker.demo_withdraw("person", batch.signature_key)
    with pytest.raises(Conflict) as refused:
        hub.batches.stage(batch.batch_id, actor=STEWARD)
    assert refused.value.code == "bulk_withdrawn"
    view = hub.batches.batch(batch.batch_id, actor=STEWARD)
    assert view.withdrawn is not None and view.withdrawn.figures["reviewed"] == 5
    hub.breaker.restore("person", actor=OWNER, reason="false_alarm", bulk=batch.bulk_band)
    hub.batches.stage(batch.batch_id, actor=STEWARD)
    flush_past_window(hub)
    hub.breaker.demo_withdraw("person", batch.signature_key)
    flush_past_window(hub)
    stopped = fetch(hub, batch.batch_id)
    assert (stopped.status, stopped.outcome, stopped.chunks_committed) == ("stopped", "bulk_withdrawn", 1)
    assert len([i for i in items(hub, batch.batch_id, "bulk") if i.status == "committed"]) == 2


def test_the_automatic_band_and_bulk_rights_never_touch_each_other(hub: Hub) -> None:
    alike(hub, 12)
    key = group_key(hub)
    hub.breaker.demo_trip(
        "person",
        figures={"arrivals": 5000, "mean": 10.0, "multiple": 5, "days": 7, "hour": "x"},
        trigger="volume",
    )
    assert hub.breaker.bulk("person", ALIKE_SIGNATURE) is None  # a volume trip never withdraws bulk rights
    assert hub.breaker.state("person").demoted
    # while the automatic band is demoted, batches go on (decision 3): a steward's batch is no automated change
    batch = ready(hub)
    hub.batches.prepare(batch.batch_id, actor=STEWARD)
    hub.batches.stage(batch.batch_id, actor=STEWARD)
    flush_past_window(hub)
    assert fetch(hub, batch.batch_id).status == "committed"
    assert hub.breaker.state("person").demoted  # the chunk held its bulk row, which a chunk writes normal
    assert hub.breaker.bulk_withdrawn("person", ALIKE_SIGNATURE) is None
    assert (
        hub.breaker.paused(["person"])[0].entity == "person"
    )  # one automatic row per entity, never a bulk one
    del key


def test_breaker_demoted_reviews_with_a_golden_record_batch_and_the_hand_back_skips_them(hub: Hub) -> None:
    workbench_world(hub, persons=24)
    hub.breaker.demo_trip("person", figures={"agreed": 1, "reviewed": 20, "threshold": 0.95, "window": 100})
    # crm twins of hr persons, with every value but the person reference: the automatic band would link them
    land(
        hub,
        [
            row("crm", f"C14{i:05d}", "person", {k: v for k, v in person_payload(i).items()})
            for i in range(1, 24, 2)
        ],
    )
    arrive(hub)
    waiting = [t for t in open_tasks(hub, "person", "review") if t.reason == "breaker_demoted"]
    assert len(waiting) == 12 and all(t.signature and t.master_ids for t in waiting)
    batch = hub.batches.draw(group_key(hub), actor=STEWARD, entity="person")
    decide_sample(hub, batch.batch_id, actor=STEWARD)
    assert hub.batches.refresh(batch.batch_id).status == "ready"
    hub.batches.prepare(batch.batch_id, actor=STEWARD)
    hub.batches.stage(batch.batch_id, actor=STEWARD)
    hub.breaker.restore("person", actor=OWNER, reason="cause_fixed")
    arrive(hub)  # the hand-back skips the reviews the batch holds
    planned = [i for i in items(hub, batch.batch_id, "bulk") if i.status == "planned"]
    assert planned and all(task(hub, i.task_id).status == "open" for i in planned)
    flush_past_window(hub)
    assert fetch(hub, batch.batch_id).status == "committed"


# ---------------------------------------------------------------------------------------------- compensation


def committed(hub: Hub, monkeypatch: pytest.MonkeyPatch, **changes) -> tuple[Hub, Batch]:
    """A batch of 7 links committed in 4 chunks of at most two."""
    alike(hub, 12)
    local = rehub(hub, **changes) if changes else hub
    monkeypatch.setattr(capacity, "COMMIT_CHUNK_ROWS", 6)
    batch = prepared(local)
    local.batches.stage(batch.batch_id, actor=STEWARD)
    for _ in range(4):
        flush_past_window(local)
    done = fetch(local, batch.batch_id)
    assert (done.status, done.chunks_committed) == ("committed", 4)
    return local, done


def run_compensation(hub: Hub, compensation: Batch, actor=COORDINATOR) -> Batch:
    hub.batches.stage(compensation.batch_id, actor=actor)
    for _ in range(compensation.chunks):
        flush_past_window(hub)
    return fetch(hub, compensation.batch_id)


def test_a_compensation_undoes_a_batchs_links_chunk_by_chunk(
    hub: Hub, monkeypatch: pytest.MonkeyPatch
) -> None:
    local, batch = committed(hub, monkeypatch, sample_share=1.0)
    linked = [i for i in items(local, batch.batch_id, "bulk") if i.status == "committed"]
    samples = [
        s for s in local.store.open_samples_for("person", [i.source for i in linked]) if s.origin == "batch"
    ]
    assert len(samples) == 7
    later = linked[0]
    kept_label = replace(
        local.store.labels_for("person", [later.source.text()])[0],
        entry_id="TR-later",
        decided_by=COORDINATOR.name,
    )
    local.store.put_labels([kept_label])  # a later decision's label on the same pair
    with pytest.raises(Forbidden) as reason:
        local.batches.compensate(batch.batch_id, actor=COORDINATOR, reason="because")
    assert reason.value.code == "bad_compensate_reason"
    compensation = local.batches.compensate(batch.batch_id, actor=COORDINATOR, reason="pattern_wrong")
    assert (compensation.kind, compensation.status, compensation.decisions, compensation.chunks) == (
        "compensate",
        "ready",
        7,
        4,
    )
    assert fetch(local, batch.batch_id).compensated_by == compensation.batch_id
    page = local.batches.rows(compensation.batch_id, actor=COORDINATOR)
    assert len(page.rows) == 7 and all(r.impact.xrefs_removed == 1 for r in page.rows)
    assert fetch(local, compensation.batch_id).status == "ready"  # staged only by stage
    # until the audit screen, its page offers nothing before the tray: it is staged and discarded on the command line
    assert local.batches.batch(compensation.batch_id, actor=COORDINATOR).actions == ()
    done = run_compensation(local, compensation)
    assert (done.status, done.chunks_committed) == ("committed", 4)
    hex_ = compensation.batch_id[4:]
    chunks = local.store.batch_chunks(compensation.batch_id)
    assert sorted(c.change_set_id for c in chunks) == sorted(f"CS-{hex_}-{n}" for n in range(1, 5))
    for chunk_ in chunks:
        assert (
            local.store.change_sets([chunk_.change_set_id])[chunk_.change_set_id]["action"]
            == "batch_compensate"
        )
    assert {c.compensated_by for c in local.store.batch_chunks(batch.batch_id)} == {compensation.batch_id}
    for item in linked:
        assert master_of(local, "person", item.source.system, item.source.key) is None
    reopened = [t for t in open_tasks(local, "person", "review") if t.source in {i.source for i in linked}]
    assert len(reopened) == 7  # arrival settled them again: back in review
    labels = local.store.labels_for("person", [i.source.text() for i in linked])
    assert [lab.entry_id for lab in labels] == [
        "TR-later"
    ]  # the batch's labels withdrawn, the later one kept
    assert all(s.status == "void" for s in local.store.samples_by_id([s.sample_id for s in samples]).values())
    view = local.batches.batch(batch.batch_id, actor=STEWARD)
    assert view.undone_by == (compensation.batch_id,) and view.counts["compensated"] == 7


def test_discarding_a_compensation_lets_the_batch_be_compensated_again(
    hub: Hub, monkeypatch: pytest.MonkeyPatch
) -> None:
    local, batch = committed(hub, monkeypatch)
    first = local.batches.compensate(batch.batch_id, actor=COORDINATOR, reason="source_defect")
    with pytest.raises(Conflict) as twice:
        local.batches.compensate(batch.batch_id, actor=COORDINATOR, reason="source_defect")
    assert twice.value.code == "already_compensated"
    local.batches.discard(first.batch_id, actor=COORDINATOR)
    assert fetch(local, batch.batch_id).compensated_by is None
    again = local.batches.compensate(batch.batch_id, actor=COORDINATOR, reason="source_defect")
    assert fetch(local, batch.batch_id).compensated_by == again.batch_id


def test_a_compensation_stopped_after_its_first_chunk_leaves_the_rest_to_another(
    hub: Hub, monkeypatch: pytest.MonkeyPatch
) -> None:
    local, batch = committed(hub, monkeypatch)
    first = local.batches.compensate(batch.batch_id, actor=COORDINATOR, reason="pattern_wrong")
    local.batches.stage(first.batch_id, actor=COORDINATOR)
    flush_past_window(local)
    local.batches.stop(first.batch_id, actor=STEWARD)
    flush_past_window(local)
    stopped = fetch(local, first.batch_id)
    assert (stopped.status, stopped.chunks_committed) == ("stopped", 1)
    assert fetch(local, batch.batch_id).compensated_by is None
    second = local.batches.compensate(batch.batch_id, actor=COORDINATOR, reason="pattern_wrong")
    assert second.decisions == 7 - len(
        [i for i in items(local, first.batch_id, "bulk") if i.status == "committed"]
    )
    done = run_compensation(local, second)
    assert done.status == "committed"
    view = local.batches.batch(batch.batch_id, actor=STEWARD)
    assert set(view.undone_by) == {first.batch_id, second.batch_id} and view.counts["compensated"] == 7
    # each compensation is credited with its own links, and the stopped one says who stopped it (review 3.3)
    undid_first = len([i for i in items(local, first.batch_id, "bulk") if i.status == "committed"])
    assert [(c.batch_id, c.status, c.undone) for c in view.compensations] == [
        (first.batch_id, "stopped", undid_first),
        (second.batch_id, "committed", 7 - undid_first),
    ]
    stopped_view = local.batches.batch(first.batch_id, actor=COORDINATOR)
    assert stopped_view.stopped_by == "Data steward"
    assert local.batches.batch(first.batch_id, actor=STEWARD).stopped_by == "you"
    assert local.batches.batch(second.batch_id, actor=STEWARD).stopped_by is None


def test_the_original_names_a_compensation_still_committing(
    hub: Hub, monkeypatch: pytest.MonkeyPatch
) -> None:
    """While a compensation's chunks still commit, the original's page names it with its status and the links
    it has undone so far (review 3.3)."""
    local, batch = committed(hub, monkeypatch)
    compensation = local.batches.compensate(batch.batch_id, actor=COORDINATOR, reason="pattern_wrong")
    view = local.batches.batch(batch.batch_id, actor=STEWARD)
    assert [(c.batch_id, c.status, c.undone) for c in view.compensations] == [
        (compensation.batch_id, "ready", 0)
    ]
    local.batches.stage(compensation.batch_id, actor=COORDINATOR)
    flush_past_window(local)
    view = local.batches.batch(batch.batch_id, actor=STEWARD)
    (line,) = view.compensations
    assert (line.status, line.undone) == ("committing", view.counts["compensated"])
    assert 0 < line.undone < 7


def test_a_review_touched_since_is_kept_and_reported(hub: Hub, monkeypatch: pytest.MonkeyPatch) -> None:
    local, batch = committed(hub, monkeypatch)
    touched = next(i for i in items(local, batch.batch_id, "bulk") if i.status == "committed")
    local.lifecycle.detach("person", touched.source, actor=COORDINATOR, reason="source_defect")
    compensation = local.batches.compensate(batch.batch_id, actor=COORDINATOR, reason="pattern_wrong")
    done = run_compensation(local, compensation)
    kept = next(i for i in items(local, compensation.batch_id) if i.task_id == touched.task_id)
    assert (kept.status, kept.reason) == ("kept", "moved_since")
    assert done.status == "committed"
    assert local.batches.batch(compensation.batch_id, actor=COORDINATOR).counts["kept"] == 1


def test_a_record_deleted_but_not_yet_settled_is_kept_alone_by_a_compensation(
    hub: Hub, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A linked record whose delete arrival took in but has not settled keeps its link a while: the compensation
    keeps that one review, with its reason, and undoes the rest (review 3.3)."""
    local, batch = committed(hub, monkeypatch)
    linked = [i for i in items(local, batch.batch_id, "bulk") if i.status == "committed"]
    gone = linked[0]
    state = local.store.source_states("person", [gone.source])[gone.source]
    land(
        local,
        [row("crm", gone.source.key, "person", {}, op="delete", at=state.occurred_at + timedelta(minutes=5))],
    )
    local.arrival.intake(local.store.landing_above(0, 100_000))  # taken in, not settled
    assert local.store.source_states("person", [gone.source])[gone.source].status != "active"
    compensation = local.batches.compensate(batch.batch_id, actor=COORDINATOR, reason="pattern_wrong")
    local.batches.stage(compensation.batch_id, actor=COORDINATOR)
    for _ in range(compensation.chunks + 1):
        flush_past_window(local)
    done = fetch(local, compensation.batch_id)
    assert (done.status, done.outcome) == ("committed", "committed")
    after = {i.task_id: (i.status, i.reason) for i in items(local, compensation.batch_id)}
    assert after.pop(gone.task_id) == ("kept", "record_changed")
    assert set(after.values()) == {("committed", None)} and len(after) == len(linked) - 1


def test_a_large_compensation_needs_a_second_steward(hub: Hub, monkeypatch: pytest.MonkeyPatch) -> None:
    local, batch = committed(hub, monkeypatch)
    strict = rehub(local, batch_checker_above=3)
    compensation = strict.batches.compensate(batch.batch_id, actor=COORDINATOR, reason="sample_missed")
    assert strict.batches.stage(compensation.batch_id, actor=COORDINATOR).status == "awaiting_checker"
    # its page offers the second steward what they need, and its maker nothing to discard there
    view = strict.batches.batch(compensation.batch_id, actor=STEWARD)
    assert [(a.decision, a.enabled) for a in view.actions] == [("confirm", True), ("send_back", True)]
    own = strict.batches.batch(compensation.batch_id, actor=COORDINATOR)
    assert [(a.decision, a.enabled) for a in own.actions] == [("confirm", False)]
    assert strict.batches.confirm(compensation.batch_id, actor=STEWARD).status == "staged"


def test_compensation_is_refused_after_its_days_for_a_compensation_and_before_any_chunk(
    hub: Hub, monkeypatch: pytest.MonkeyPatch
) -> None:
    local, batch = committed(hub, monkeypatch)
    now = local.batches.clock
    local.batches.clock = lambda: now() + timedelta(days=31)
    with pytest.raises(Conflict) as late:
        local.batches.compensate(batch.batch_id, actor=COORDINATOR, reason="pattern_wrong")
    assert late.value.code == "undo_window_passed"
    local.batches.clock = now
    compensation = local.batches.compensate(batch.batch_id, actor=COORDINATOR, reason="pattern_wrong")
    with pytest.raises(Conflict) as of_one:
        local.batches.compensate(compensation.batch_id, actor=COORDINATOR, reason="pattern_wrong")
    assert of_one.value.code == "not_compensable"
    nothing = replace(
        batch, batch_id="BAT-" + "0" * 19 + "1", status="failed", chunks_committed=0, signature_key=None
    )
    local.store.insert_batch(
        replace(nothing, kind="compensate", compensates=None), []
    )  # no group row to take
    with pytest.raises(Conflict) as empty:
        local.batches.compensate(nothing.batch_id, actor=COORDINATOR, reason="pattern_wrong")
    assert empty.value.code == "not_compensable"
    with pytest.raises(NotFound):
        local.batches.compensate("BAT-" + "0" * 20, actor=COORDINATOR, reason="pattern_wrong")


def test_a_record_linked_again_at_the_same_event_gets_a_new_blind_review(
    hub: Hub, monkeypatch: pytest.MonkeyPatch
) -> None:
    local, batch = committed(hub, monkeypatch, sample_share=1.0)
    first_samples = {
        s.sample_id: s
        for s in local.store.open_samples_for(
            "person", [i.source for i in items(local, batch.batch_id, "bulk")]
        )
        if s.origin == "batch"
    }
    one = next(iter(first_samples.values()))
    answer_blind(local, one, agree=True)
    compensation = local.batches.compensate(batch.batch_id, actor=COORDINATOR, reason="pattern_wrong")
    run_compensation(local, compensation)
    again = draw(local)
    mine = next(i for i in items(local, again.batch_id) if i.source == one.source)
    if mine.role == "sample":  # keep the record in bulk, so the second batch links it
        other = next(i for i in items(local, again.batch_id, "bulk") if i.status == "candidate")
        local.store.update_items(
            again.batch_id,
            [{"task_id": other.task_id, "role": "sample", "status": "open"}],
            from_status="candidate",
        )
        local.store.update_items(
            again.batch_id,
            [{"task_id": mine.task_id, "role": "bulk", "status": "candidate"}],
            from_status="open",
        )
    decide_sample(local, again.batch_id, actor=STEWARD)
    local.batches.refresh(again.batch_id)
    local.batches.prepare(again.batch_id, actor=STEWARD)
    local.batches.stage(again.batch_id, actor=STEWARD)
    for _ in range(4):
        flush_past_window(local)
    second = [
        s
        for s in local.store.open_samples_for("person", [one.source])
        if s.origin == "batch" and s.entry_id == fetch(local, again.batch_id).entry_id
    ]
    assert len(second) == 1
    assert second[0].sample_id != one.sample_id and second[0].task_id != one.task_id
    assert second[0].event_id == one.event_id  # the same record at the same event
    old = local.store.samples_by_id([one.sample_id])[one.sample_id]
    assert old.status == "agreed" and task(local, one.task_id).status == "closed"


# ---------------------------------------------------------------------------------------------- the lock order (Postgres)


@ONLY_POSTGRES_ENGINE
def test_an_undo_against_the_first_chunk_waits_then_is_refused(hub: Hub) -> None:
    alike(hub, 12)
    batch = prepared(hub)
    staged = hub.batches.stage(batch.batch_id, actor=STEWARD)
    held = threading.Event()
    order: list[str] = []
    errors: list[BaseException] = []

    moments: dict[str, float] = {}

    def pause(point: str) -> None:
        if point == "in_commit" and not held.is_set():
            held.set()
            time.sleep(1.0)  # the undo waits on the batch row meanwhile
            moments["chunk"] = time.monotonic()

    hub.commit.fault = pause

    def chunk() -> None:
        try:
            flush_past_window(hub)
            order.append("chunk")
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    def undo() -> None:
        try:
            held.wait(THREAD_TIMEOUT)
            with pytest.raises(Conflict) as late:
                hub.tray.undo(staged.entry_id, actor=STEWARD)
            moments["undo"] = time.monotonic()
            assert late.value.code == "already_settled"
            order.append("undo refused")
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=chunk), threading.Thread(target=undo)]
    for thread in threads:
        thread.start()
    join_all(threads)
    hub.commit.fault = None
    assert not errors, errors
    assert sorted(order) == ["chunk", "undo refused"]
    assert moments["undo"] >= moments["chunk"]  # the undo waited for the chunk, then found it committing
    assert fetch(hub, batch.batch_id).status == "committed"


@ONLY_POSTGRES_ENGINE
def test_a_chunk_against_an_undo_waits_then_finds_the_batch_ready(hub: Hub) -> None:
    alike(hub, 12)
    batch = prepared(hub)
    staged = hub.batches.stage(batch.batch_id, actor=STEWARD)
    held = threading.Event()
    errors: list[BaseException] = []
    version = hub.store.last_commit_version()

    def pause(point: str) -> None:
        if point == "in_undo":
            held.set()
            time.sleep(1.0)  # the chunk waits on the batch row meanwhile

    hub.batches.fault = pause

    def undo() -> None:
        try:
            hub.tray.undo(staged.entry_id, actor=STEWARD)
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    def chunk() -> None:
        try:
            held.wait(THREAD_TIMEOUT)
            hub.batches.fault = None
            flush_past_window(hub)
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=undo), threading.Thread(target=chunk)]
    for thread in threads:
        thread.start()
    join_all(threads)
    assert not errors, errors
    after = fetch(hub, batch.batch_id)
    assert (after.status, after.chunks_committed) == ("ready", 0)
    assert hub.store.last_commit_version() == version
    entry = hub.store.tray_entries([staged.entry_id])[staged.entry_id]
    assert (entry.status, entry.outcome) == ("undone", "undone")


# ---------------------------------------------------------------------------------------------- at the declared figures


@pytest.mark.slow
def test_a_large_batch_at_the_declared_figures_asks_a_second_steward_and_commits_in_two_chunks(
    hub: Hub,
) -> None:
    from tests.helpers import alike_world, unique_person

    alike_world(hub, 260)
    alike_reviews(hub, 260, first=0, person=unique_person)
    batch = draw(hub)
    assert (batch.population, batch.sample_size) == (260, 6)
    decide_sample(hub, batch.batch_id, actor=STEWARD)
    assert hub.batches.refresh(batch.batch_id).status == "ready"
    view = hub.batches.prepare(batch.batch_id, actor=STEWARD)
    assert view.summary is not None and view.summary.xrefs == 254 and view.summary.chunks == 2
    assert hub.batches.stage(batch.batch_id, actor=STEWARD).status == "awaiting_checker"
    hub.batches.confirm(batch.batch_id, actor=COORDINATOR)
    flush_past_window(hub)
    flush_past_window(hub)
    done = fetch(hub, batch.batch_id)
    assert (done.status, done.chunks_committed) == ("committed", 2)
    chunks = hub.store.batch_chunks(batch.batch_id)
    assert all(c.rows <= capacity.COMMIT_CHUNK_ROWS for c in chunks) and sum(c.items for c in chunks) == 254
