"""Blind review, the automated matcher's checkpoint (story 3.2): what is drawn, the blind case, the answer, its
agreement, the review a disagreement opens, voids, the Quality samples view and the timelines, on both
engines (owner: SERVICES)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from datetime import timedelta

import pytest

from mdm import capacity
from mdm.engine.sample import draw_value, drawn
from mdm.models.authority import Actor
from mdm.models.canonical import utcnow
from mdm.models.errors import Forbidden
from mdm.models.quality import QualitySample
from mdm.models.records import SourceKey
from mdm.models.tasks import Task
from mdm.models.workbench import TrayEntry
from mdm.services import display
from mdm.services.context import Hub
from mdm.services.decisions import (
    NOTICE_BLIND_NONE,
    NOTICE_DISPUTE_DETACH,
    NOTICE_MERGE,
    NOTICE_OWN_ANSWER,
    NOTICE_OWN_DECISION,
    NOTICE_PAIR_GONE,
)
from tests.conftest import ENGINES, open_hub
from tests.helpers import (
    COORDINATOR,
    STEWARD,
    T0,
    arrive,
    crm_person_key,
    golden_pair,
    hr_key,
    land,
    master_of,
    mini_world,
    open_tasks,
    partition,
    person_payload,
    person_ref,
    person_review,
    row,
    seen,
    task_of,
    workbench_world,
)
from tests.test_services_tray import window_passed

ENTITIES = ("organisation", "person")


def rehub(hub: Hub, **changes) -> Hub:
    """Another hub over the same store, with settings changed (the models are published already)."""
    return Hub.open(hub.settings.with_(**changes), store=hub.store, as_role="data_owner")


@pytest.fixture
def sampled(hub: Hub) -> Iterator[Hub]:
    """The hub's store with every decision drawn for blind review (a share of 1)."""
    opened = rehub(hub, sample_share=1.0)
    try:
        yield opened
    finally:
        opened.close()


def all_samples(hub: Hub, entity: str | None = None) -> list[QualitySample]:
    """Every sample, through its task (open or closed)."""
    ids: list[str] = []
    for status in ("open", "closed"):
        after = None
        while True:
            page = hub.store.tasks(entity, "quality_sample", status, 500, after)
            ids.extend(t.evidence["sample_id"] for t in page)
            if len(page) < 500:
                break
            after = page[-1].task_id
    return sorted(hub.store.samples_by_id(ids).values(), key=lambda s: s.sample_id)


def sample_task(hub: Hub, sample: QualitySample) -> Task:
    return hub.store.tasks_by_id([sample.task_id])[sample.task_id]


def decide(
    hub: Hub, task_id: str, decision: str, *, actor=COORDINATOR, target: str | None = None
) -> TrayEntry:
    """Stage a decision as the actor saw the case, let the undo window pass, and flush it."""
    entry = hub.tray.stage(task_id, decision, actor=actor, target=target, **seen(hub, task_id))
    window_passed(hub)
    hub.tray.flush()
    return hub.store.tray_entries([entry.entry_id])[entry.entry_id]


def join_sample(hub: Hub, entity: str = "person") -> QualitySample:
    """An automated link of a crm record that joined another record's new golden record."""
    joins = [s for s in all_samples(hub, entity) if s.decision == "auto_link" and s.status == "open"]
    assert joins
    return joins[0]


# ---------------------------------------------------------------------------------------------- automated draws


def test_every_automated_decision_is_drawn_at_a_share_of_one(sampled: Hub) -> None:
    land(sampled, mini_world(persons=8, organisations=3).rows)
    report = arrive(sampled)
    found = all_samples(sampled)
    linked = {(e, s) for e in ENTITIES for members in partition(sampled, e).values() for s in members}
    assert {(s.entity, s.source) for s in found} == linked
    assert report.samples == len(found) and report.samples_skipped == 0
    assert report.tasks == {}  # a sample's task never counts among the tasks arrival opened
    kinds = Counter(s.decision for s in found)
    assert kinds["auto_create"] == report.created and kinds["auto_link"] == len(found) - report.created
    for sample in found:
        task = sample_task(sampled, sample)
        assert (task.kind, task.status, task.reason, task.source) == (
            "quality_sample",
            "open",
            "blind_sample",
            sample.source,
        )
        assert dict(task.evidence) == {"sample_id": sample.sample_id}  # nothing of the first decision
        assert sample.origin == "automated" and sample.decided_by == "automated-matcher"
        assert sample.target == master_of(sampled, sample.entity, sample.source.system, sample.source.key)
        if sample.decision == "auto_create":
            assert (sample.band, sample.signature, sample.score) == ("distinct", "", None)
            continue
        # a record that joined a new cluster carries the pair that joined it: a signature with its marks
        assert sample.band == "auto" and sample.score >= 90 and " · " in sample.signature
        members = partition(sampled, sample.entity)[sample.target]
        states = sampled.store.source_states(sample.entity, list(members))
        edges = {
            sampled.matching.explain_pair(sample.entity, states[sample.source], states[m]).signature
            for m in members
            if m != sample.source
        }
        assert sample.signature in edges

    # a record linked to an existing golden record: an automatic link with its signature arrives unharmed
    land(sampled, [row("crm", crm_person_key(1), "person", person_payload(1), at=T0 + timedelta(hours=2))])
    later = arrive(sampled)
    (linked_now,) = [
        s for s in all_samples(sampled, "person") if s.source == SourceKey("crm", crm_person_key(1))
    ]
    assert later.samples == 1 and linked_now.decision == "auto_link"
    assert linked_now.target == master_of(sampled, "person", "hr", hr_key(1)) and linked_now.signature


def test_a_retired_id_routed_to_its_survivor_is_drawn_as_a_hint_link(sampled: Hub) -> None:
    workbench_world(sampled)
    survivor = master_of(sampled, "person", "hr", hr_key(1))
    retired = master_of(sampled, "person", "hr", hr_key(3))
    sampled.lifecycle.merge("person", survivor, retired, maker=STEWARD, checker=COORDINATOR, reason="test")
    hinted = {**person_payload(90, person_ref=person_ref(1090)), "master_id": retired}
    land(sampled, [row("crm", "C1900090", "person", hinted, at=T0 + timedelta(hours=3))])
    arrive(sampled)
    (sample,) = [s for s in all_samples(sampled, "person") if s.source == SourceKey("crm", "C1900090")]
    assert (sample.decision, sample.band, sample.signature, sample.score) == ("hint_link", "", "", None)
    assert sample.target == survivor


def test_a_share_of_nothing_draws_nothing(hub: Hub) -> None:
    land(hub, mini_world(persons=6, organisations=2).rows)
    report = arrive(hub)
    assert report.samples == 0 and all_samples(hub) == []


def test_a_share_draws_exactly_the_records_the_hash_names(make_store, engine: str) -> None:
    rows = mini_world(persons=16, organisations=5).rows
    hubs = {}
    for share in (1.0, 0.3):
        store = make_store(engine, sample_share=share)
        hubs[share] = open_hub(store.settings, store, engine)
        land(hubs[share], rows)
        arrive(hubs[share])
    every = all_samples(hubs[1.0])
    predicted = {
        s.sample_id
        for s in every
        if drawn(draw_value(s.entity, s.source.text(), s.decision, s.event_id or ""), 0.3)
    }
    assert 0 < len(predicted) < len(every)
    assert {s.sample_id for s in all_samples(hubs[0.3])} == predicted


@pytest.mark.skipif(not {"duckdb", "postgres"} <= set(ENGINES), reason="parity needs both engines in the run")
def test_the_same_world_draws_the_same_samples_on_both_engines(make_store) -> None:
    rows = mini_world(persons=12, organisations=4).rows
    drawn_ids = []
    for engine in ("duckdb", "postgres"):
        store = make_store(engine, sample_share=0.5, sample_key="parity-key-of-the-run")
        hub = open_hub(store.settings, store, engine)
        land(hub, rows)
        arrive(hub)
        drawn_ids.append([(s.sample_id, s.decision, s.band, s.signature, s.score) for s in all_samples(hub)])
    assert drawn_ids[0] == drawn_ids[1] and drawn_ids[0]
    every = all_samples(hub)
    keyed = {
        s.sample_id
        for s in every
        if drawn(
            draw_value(s.entity, s.source.text(), s.decision, s.event_id or "", "parity-key-of-the-run"), 0.5
        )
    }
    assert keyed == {s.sample_id for s in every}  # the key the settings name draws them


def test_samples_commit_with_their_records_across_chunks(
    sampled: Hub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(capacity, "COMMIT_CHUNK_ROWS", 4)
    land(sampled, mini_world(persons=10, organisations=3).rows)

    class Crash(RuntimeError):
        pass

    def crash(point: str) -> None:
        if point == "after_chunk":
            raise Crash

    sampled.arrival.fault = crash
    with pytest.raises(Crash):
        arrive(sampled)
    sampled.arrival.fault = None
    first = all_samples(sampled)
    assert first  # the first chunk committed its records and their samples together
    for sample in first:
        assert master_of(sampled, sample.entity, sample.source.system, sample.source.key) == sample.target
    arrive(sampled)
    every = all_samples(sampled)
    linked = {(e, s) for e in ENTITIES for members in partition(sampled, e).values() for s in members}
    assert {(s.entity, s.source) for s in every} == linked
    assert len({s.sample_id for s in every}) == len(every) > len(first)


def test_automated_samples_open_at_once_are_capped_per_entity(hub: Hub) -> None:
    capped = rehub(hub, sample_share=1.0, sample_open_cap=5, breaker_min_samples=5, breaker_window=5)
    try:
        stems = (
            "Quartz",
            "Umber",
            "Tallow",
            "Juniper",
            "Marram",
            "Fescue",
            "Heron",
            "Sorrel",
            "Wicker",
            "Yarrow",
        )
        rows = [
            row(
                "crm",
                f"C08{i:05d}",
                "organisation",
                {
                    "name": f"{stems[i % 10]} {stems[(i // 10) % 10]}{i} Cooperage",
                    "postcode": f"XD{i % 9 + 1} {i % 8 + 1}Q{chr(65 + i % 26)}",
                    "city": "Brackenmere",
                    "country": "XB",
                },
                at=T0 + timedelta(seconds=i),
            )
            for i in range(50)
        ]
        land(capped, rows)
        report = arrive(capped)
        assert report.samples == 5 and report.samples_skipped == 45
        drawn_now = all_samples(capped, "organisation")
        assert len(drawn_now) == 5
        # the first draws in landing order are kept; the rest are counted, not opened
        first_five = sorted(s.source.key for s in drawn_now)
        assert first_five == [f"C08{i:05d}" for i in range(5)]
        assert capped.store.open_sample_count("organisation", "automated", 100) == 5
    finally:
        capped.close()


# ---------------------------------------------------------------------------------------------- steward draws


def test_a_stewards_link_not_a_match_and_keep_apart_are_drawn(sampled: Hub) -> None:
    workbench_world(sampled)
    source = person_review(sampled)
    task = task_of(sampled, kind="review", source=source)
    case = sampled.decisions.case(task.task_id, actor=STEWARD)
    entry = decide(sampled, task.task_id, "link", actor=STEWARD)
    assert entry.status == "committed"
    (link,) = [s for s in all_samples(sampled, "person") if s.origin == "steward"]
    assert (link.decision, link.source, link.target, link.decided_by, link.entry_id) == (
        "link",
        source,
        case.candidates[0].master_id,
        STEWARD.name,
        entry.entry_id,
    )
    assert link.band == case.candidates[0].band and link.signature == case.candidates[0].signature

    left, right = golden_pair(sampled)
    pair_task = task_of(sampled, kind="possible_duplicate")
    decide(sampled, pair_task.task_id, "keep_apart", actor=STEWARD)
    (kept,) = [s for s in all_samples(sampled, "organisation") if s.decision == "keep_apart"]
    assert kept.source is None and kept.master_ids == tuple(sorted(pair_task.master_ids))
    assert set(kept.pair_sources) == {left.text(), right.text()}
    assert sample_task(sampled, kept).master_ids == kept.master_ids


def test_what_blind_review_cannot_ask_again_is_never_drawn(sampled: Hub) -> None:
    workbench_world(sampled)
    source = person_review(sampled)
    task = task_of(sampled, kind="review", source=source)
    state = sampled.store.source_states("person", [source])[source]
    now = utcnow()

    def entry(decision: str, subject: dict) -> TrayEntry:
        return TrayEntry(
            entry_id="TR-0001",
            task_id=task.task_id,
            entity="person",
            decision=decision,
            target=None,
            subject=subject,
            signature=None,
            actor=STEWARD.name,
            actor_role=STEWARD.role,
            persona=True,
            event_id=state.event_id,
            planning_version=0,
            staged_at=now,
            deadline=now,
            status="staged",
        )

    quality = sampled.quality
    assert (
        quality.steward(entry("not_a_match", {"candidates": []}), task, state, {"candidates": []}, now)
        is None
    )
    for decision in ("approve_update", "reject_update", "keep_orphan", "blind_link", "keep_decision"):
        assert quality.steward(entry(decision, {}), task, state, {}, now) is None
    declined = {"candidates": ["PER-000001"]}
    assert quality.steward(entry("not_a_match", declined), task, state, declined, now) is not None


# ---------------------------------------------------------------------------------------------- the blind case


def test_the_blind_case_hides_the_first_decision(sampled: Hub) -> None:
    workbench_world(sampled)
    sample = join_sample(sampled)
    case = sampled.decisions.case(sample.task_id, actor=COORDINATOR)
    assert (case.shape, case.blind) == ("blind", True)
    assert case.candidates == () and case.preview is None and case.default_candidate is None
    assert not case.close_call
    assert (case.row.score, case.row.band) == (None, None)
    assert (case.row.suggestion, case.row.reason, case.row.kind_label) == (
        "Decide blind",
        "",  # the suggestion says it: no reason repeats it
        "Quality sample",
    )
    assert case.reason_text.startswith("Blind review: decide where this record belongs")
    assert case.columns[0] == f"Record · {sample.source.text()}"
    masters = [c.master_id for c in case.choices]
    assert masters == sorted(masters) and sample.target in masters
    assert [c.index for c in case.choices] == list(range(1, len(masters) + 1))
    decisions = [a.decision for a in case.actions if a.decision not in ("claim", "snooze", "escalate")]
    assert decisions == ["blind_link"] * len(masters) + ["blind_none"]
    assert [a.label for a in case.actions if a.decision == "blind_link"] == [
        f"Belongs to {m}" for m in masters
    ]
    entry = sampled.tray.stage(
        sample.task_id, "blind_link", actor=COORDINATOR, target=masters[0], **seen(sampled, sample.task_id)
    )
    assert set(entry.subject) == {"source", "shown", "master_ids", "sample_id", "kind"}
    assert entry.subject["shown"] == masters and entry.signature is None
    assert display.tray_label(entry.decision, entry.subject, entry.target) == (
        f"Quality sample: {sample.source.text()} belongs to {masters[0]}"
    )


def test_the_golden_record_that_holds_the_record_shows_its_other_members_only(sampled: Hub) -> None:
    workbench_world(sampled)
    # the crm record brings the e-mail and phone the hr record lacks, and joins its golden record
    land(
        sampled,
        [
            row(
                "hr",
                hr_key(70),
                "person",
                person_payload(70, person_ref=person_ref(1070), email=None, phone=None),
                at=T0 + timedelta(hours=2),
                version=1,
            )
        ],
    )
    arrive(sampled)
    land(sampled, [row("crm", crm_person_key(70), "person", person_payload(70), at=T0 + timedelta(hours=3))])
    arrive(sampled)
    source = SourceKey("crm", crm_person_key(70))
    (sample,) = [s for s in all_samples(sampled, "person") if s.source == source]
    holder = master_of(sampled, "person", "hr", hr_key(70))
    assert sample.decision == "auto_link" and sample.target == holder
    golden = sampled.store.golden("person", [holder])[holder]
    assert golden.values.get("email")  # the golden record's e-mail is the crm record's
    case = sampled.decisions.case(sample.task_id, actor=COORDINATOR)
    column = 1 + [c.master_id for c in case.choices].index(holder)
    rows = {r.attribute: r for r in case.compare}
    for attribute in ("email", "phone"):
        assert rows[attribute].values[0] is not None  # the record's own value
        assert rows[attribute].values[column] is None  # the golden record without it
        assert rows[attribute].agreement[column - 1] != "agree"  # no "=" because the record won it
    model = sampled.registry.published("person")
    without = sampled.lifecycle.values_without("person", holder, source)
    choice = next(c for c in case.choices if c.master_id == holder)
    assert choice.title == display.display_name(model, without, set(model.personal_attributes()), holder)
    # revealed, the golden record still shows its other members only: the placement is not given away
    revealed = sampled.decisions.reveal(sample.task_id, actor=COORDINATOR, reason="deciding_task")
    shown = {r.attribute: r for r in revealed.compare}
    for attribute in ("email", "phone"):
        assert shown[attribute].values[0] is not None and shown[attribute].values[column] is None


def test_an_auto_create_near_miss_is_offered_and_choosing_it_opens_a_disputed_review(sampled: Hub) -> None:
    workbench_world(sampled)
    held = person_payload(2)
    near = {
        "given_name": "Quillon",
        "family_name": held["family_name"],
        "birth_date": held["birth_date"][:4] + "-11-23",
        "postcode": "XZ9 9ZZ",
        "city": "Norvale",
        "country": "XA",
        "person_ref": person_ref(1099),
    }
    land(sampled, [row("hr", hr_key(99), "person", near, at=T0 + timedelta(hours=2), version=1)])
    arrive(sampled)
    source = SourceKey("hr", hr_key(99))
    (sample,) = [s for s in all_samples(sampled, "person") if s.source == source]
    assert sample.decision == "auto_create"
    neighbour = master_of(sampled, "person", "hr", hr_key(2))
    case = sampled.decisions.case(sample.task_id, actor=COORDINATOR)
    assert [c.master_id for c in case.choices] == [neighbour]  # a distinct-band near miss, offered
    decide(sampled, sample.task_id, "blind_link", target=neighbour)
    answered = sampled.store.samples_by_id([sample.sample_id])[sample.sample_id]
    assert (answered.status, answered.answer) == ("disagreed", neighbour)
    dispute = sampled.store.tasks_by_id([answered.dispute_task_id])[answered.dispute_task_id]
    own = master_of(sampled, "person", "hr", hr_key(99))
    assert (dispute.kind, dispute.reason, dispute.source, dispute.master_ids) == (
        "review",
        "blind_disagreement",
        source,
        (own, neighbour),
    )
    disputed = sampled.decisions.case(dispute.task_id, actor=COORDINATOR)
    assert disputed.shape == "disputed" and not disputed.blind
    assert disputed.columns == (f"Record · {source.text()}", f"Now · {own}", f"Blind review · {neighbour}")
    assert [a.decision for a in disputed.actions][:1] == ["keep_decision"]
    assert "link" not in [a.decision for a in disputed.actions]
    assert disputed.notice is not None and neighbour in disputed.notice and "merging" in disputed.notice


def test_a_case_with_no_golden_record_near_says_so(sampled: Hub) -> None:
    workbench_world(sampled)
    land(
        sampled,
        [
            row(
                "hr",
                hr_key(98),
                "person",
                {
                    "given_name": "Ysmay",
                    "family_name": "Thornbury",
                    "birth_date": "1931-07-17",
                    "country": "XA",
                },
                at=T0 + timedelta(hours=2),
                version=1,
            )
        ],
    )
    arrive(sampled)
    (sample,) = [s for s in all_samples(sampled, "person") if s.source == SourceKey("hr", hr_key(98))]
    case = sampled.decisions.case(sample.task_id, actor=COORDINATOR)
    assert case.choices == () and case.notice == NOTICE_BLIND_NONE
    assert [a.decision for a in case.actions][:1] == ["blind_none"]


# ---------------------------------------------------------------------------------------------- refusals


def test_the_first_decider_never_reviews_their_own_decision(sampled: Hub) -> None:
    workbench_world(sampled)
    source = person_review(sampled)
    task = task_of(sampled, kind="review", source=source)
    decide(sampled, task.task_id, "link", actor=STEWARD)
    (sample,) = [s for s in all_samples(sampled, "person") if s.origin == "steward"]
    case = sampled.decisions.case(sample.task_id, actor=STEWARD)
    assert case.actions and all(not a.enabled for a in case.actions)
    assert {a.why_not for a in case.actions} == {NOTICE_OWN_DECISION}
    with pytest.raises(Forbidden) as refused:
        sampled.decisions.check(
            sample.task_id,
            "blind_none",
            actor=STEWARD,
            **seen(sampled, sample.task_id),
        )
    assert refused.value.code == "own_decision"
    mine = {r.task_id for r in sampled.inbox.page("samples", actor=STEWARD).rows}
    theirs = {r.task_id for r in sampled.inbox.page("samples", actor=COORDINATOR).rows}
    assert sample.task_id not in mine and sample.task_id in theirs
    # another steward answers it, and a blind answer on a record names a choice
    with pytest.raises(Forbidden) as unchosen:
        sampled.tray.stage(sample.task_id, "blind_link", actor=COORDINATOR, **seen(sampled, sample.task_id))
    assert unchosen.value.code == "choose_first"


# ---------------------------------------------------------------------------------------------- agreement


def test_an_agreeing_answer_counts_and_writes_no_label(sampled: Hub) -> None:
    workbench_world(sampled)
    sample = join_sample(sampled)
    entry = decide(sampled, sample.task_id, "blind_link", target=sample.target)
    assert (entry.status, entry.outcome) == ("committed", "agreed")
    answered = sampled.store.samples_by_id([sample.sample_id])[sample.sample_id]
    assert (answered.status, answered.answer, answered.reviewed_by, answered.dispute_task_id) == (
        "agreed",
        sample.target,
        COORDINATOR.name,
        None,
    )
    assert sample_task(sampled, sample).status == "closed"
    rows = sampled.store.agreement_rows("person", None, 100)
    assert [(r.origin, r.band, r.signature, r.reviewed, r.agreed) for r in rows] == [
        ("automated", "auto", sample.signature, 1, 1)
    ]
    assert sampled.store.labels_for("person", [sample.source.text()]) == []  # an answer binds nothing
    audit = sampled.store.change_sets([entry.change_set_id])[entry.change_set_id]
    assert (audit["action"], audit["commit_version"], audit["evidence"]["agreed"]) == (
        "blind_link",
        None,
        True,
    )
    headlines = [e.headline for e in sampled.lookup.timeline("person", sample.target, actor=STEWARD).events]
    assert f"Blind review placed {sample.source.text()} here" in headlines


def test_an_answer_is_measured_against_the_first_decision_not_where_the_record_is_now(sampled: Hub) -> None:
    workbench_world(sampled)
    source = SourceKey("crm", crm_person_key(0))
    (sample,) = [s for s in all_samples(sampled, "person") if s.source == source]
    assert sample.decision == "auto_link"  # it joined the hr record's new golden record
    # a near miss of the same person gets a golden record of its own, and a steward moves the crm record there
    held = person_payload(0)
    near = person_payload(
        0,
        given_name="Quillon",
        birth_date=held["birth_date"][:4] + "-11-23",
        postcode="XZ8 8ZZ",
        person_ref=person_ref(1097),
        email=None,
        phone=None,
    )
    land(sampled, [row("hr", hr_key(97), "person", near, at=T0 + timedelta(hours=2), version=1)])
    arrive(sampled)
    elsewhere = master_of(sampled, "person", "hr", hr_key(97))
    assert elsewhere is not None and elsewhere != sample.target
    sampled.lifecycle.link("person", source, elsewhere, actor=STEWARD, reason="test")
    case = sampled.decisions.case(sample.task_id, actor=COORDINATOR)
    assert {sample.target, elsewhere} <= {c.master_id for c in case.choices}
    entry = decide(sampled, sample.task_id, "blind_link", target=elsewhere)
    assert entry.outcome == "disagreed"  # the first decision placed it in its target, not where it is now
    answered = sampled.store.samples_by_id([sample.sample_id])[sample.sample_id]
    dispute = sampled.store.tasks_by_id([answered.dispute_task_id])[answered.dispute_task_id]
    assert (dispute.kind, dispute.reason, dispute.master_ids) == (
        "review",
        "blind_disagreement",
        (elsewhere,),
    )
    disputed = sampled.decisions.case(dispute.task_id, actor=COORDINATOR)
    assert disputed.columns == (f"Record · {source.text()}", f"Now · {elsewhere}")
    assert disputed.notice == NOTICE_DISPUTE_DETACH


def test_a_disagreement_opens_a_review_that_keep_the_first_decision_closes(sampled: Hub) -> None:
    workbench_world(sampled)
    sample = join_sample(sampled)
    entry = decide(sampled, sample.task_id, "blind_none")
    assert entry.outcome == "disagreed"
    source = sample.source.text()
    headlines = [e.headline for e in sampled.lookup.timeline("person", sample.target, actor=STEWARD).events]
    assert f"Blind review placed {source} in none of those shown" in headlines
    answered = sampled.store.samples_by_id([sample.sample_id])[sample.sample_id]
    dispute = sampled.store.tasks_by_id([answered.dispute_task_id])[answered.dispute_task_id]
    assert dispute.evidence["first"] == "auto_link" and dispute.evidence["answer"] == "none"
    assert "signature" not in dispute.evidence
    row_ = sampled.inbox.row(dispute.task_id, actor=COORDINATOR)
    assert row_.suggestion == "Keep or correct the first decision"
    case = sampled.decisions.case(dispute.task_id, actor=COORDINATOR)
    assert [a.decision for a in case.actions if a.enabled][:1] == ["keep_decision"]
    # what each side decided, in codes and IDs: the first decision and the blind answer
    assert dispute.evidence["target"] == sample.target
    assert case.reason_text == (
        f"The matcher linked it to {sample.target}; a blind review placed it in none of the golden records shown."
    )
    # the golden record that holds it shows the record's own values as the same, never as missing
    for compared in case.compare:
        if compared.values[0] is not None and compared.values[0] == compared.values[1]:
            assert compared.agreement[0] != "missing", compared.attribute
    kept = decide(sampled, dispute.task_id, "keep_decision")
    assert kept.status == "committed"
    assert sampled.store.tasks_by_id([dispute.task_id])[dispute.task_id].status == "closed"
    audit = sampled.store.change_sets([kept.change_set_id])[kept.change_set_id]
    assert (audit["action"], audit["commit_version"]) == ("keep_decision", None)
    assert sampled.store.labels_for("person", [source]) == []
    headlines = [e.headline for e in sampled.lookup.timeline("person", sample.target, actor=STEWARD).events]
    assert f"First decision on {source} kept after a blind review" in headlines
    # the agreement counts the disagreement once
    (counted,) = sampled.store.agreement_rows("person", None, 100)
    assert (counted.reviewed, counted.agreed) == (1, 0)


def test_a_not_a_match_answered_with_a_declined_record_disagrees_and_offers_link(sampled: Hub) -> None:
    workbench_world(sampled)
    source = person_review(sampled)
    task = task_of(sampled, kind="review", source=source)
    declined = sampled.decisions.case(task.task_id, actor=STEWARD).candidates[0].master_id
    sampled.tray.stage(task.task_id, "not_a_match", actor=STEWARD, **seen(sampled, task.task_id))
    window_passed(sampled)
    with sampled.store.exclusive_lease("arrival") as held:  # arrival is busy: the record waits, unlinked
        assert held
        assert sampled.tray.flush().committed == 1
    (sample,) = [s for s in all_samples(sampled, "person") if s.origin == "steward"]
    assert (sample.decision, sample.declined, sample.target) == ("not_a_match", (declined,), None)
    case = sampled.decisions.case(sample.task_id, actor=COORDINATOR)
    assert declined in [c.master_id for c in case.choices]
    entry = decide(sampled, sample.task_id, "blind_link", target=declined)
    assert entry.outcome == "disagreed"
    answered = sampled.store.samples_by_id([sample.sample_id])[sample.sample_id]
    dispute_id = answered.dispute_task_id
    third = Actor("tester:steward-3", "person", "data_steward")
    disputed = sampled.decisions.case(dispute_id, actor=third)
    assert disputed.columns == (f"Record · {source.text()}", f"Blind review · {declined}")
    link = next(a for a in disputed.actions if a.decision == "link")
    assert (link.label, link.target, link.enabled) == (f"Link to {declined}", declined, True)
    assert disputed.notice is None
    # the steward who declined it cannot settle the dispute
    assert all(not a.enabled for a in sampled.decisions.case(dispute_id, actor=STEWARD).actions)
    # the steward who gave the blind answer may keep the first decision, never link to their own answer
    theirs = {a.decision: a for a in sampled.decisions.case(dispute_id, actor=COORDINATOR).actions}
    assert theirs["keep_decision"].enabled
    assert (theirs["link"].enabled, theirs["link"].why_not) == (False, NOTICE_OWN_ANSWER)
    with pytest.raises(Forbidden) as refused:
        sampled.tray.stage(
            dispute_id, "link", actor=COORDINATOR, target=declined, **seen(sampled, dispute_id)
        )
    assert refused.value.code == "own_answer"
    linked = decide(sampled, dispute_id, "link", actor=third, target=declined)
    assert linked.status == "committed"
    assert master_of(sampled, "person", source.system, source.key) == declined


def test_a_keep_apart_found_the_same_opens_a_disputed_pair(sampled: Hub) -> None:
    workbench_world(sampled)
    golden_pair(sampled)
    pair_task = task_of(sampled, kind="possible_duplicate")
    decide(sampled, pair_task.task_id, "keep_apart", actor=STEWARD)
    (sample,) = [s for s in all_samples(sampled, "organisation") if s.decision == "keep_apart"]
    case = sampled.decisions.case(sample.task_id, actor=COORDINATOR)
    assert (case.shape, case.blind, case.choices, case.candidates) == ("blind_pair", True, (), ())
    offered = [(a.decision, a.label) for a in case.actions if a.decision.startswith("blind")]
    assert offered == [("blind_link", "They are the same"), ("blind_none", "They are not the same")]
    entry = decide(sampled, sample.task_id, "blind_link")  # a pair needs no choice
    assert entry.outcome == "disagreed"
    assert display.tray_label(entry.decision, entry.subject, entry.target) == (
        f"Quality sample: {sample.master_ids[0]} and {sample.master_ids[1]} are the same"
    )
    answered = sampled.store.samples_by_id([sample.sample_id])[sample.sample_id]
    dispute = sampled.store.tasks_by_id([answered.dispute_task_id])[answered.dispute_task_id]
    assert (dispute.kind, dispute.reason, dispute.master_ids) == (
        "possible_duplicate",
        "blind_disagreement",
        sample.master_ids,
    )
    assert dispute.task_key != pair_task.task_key
    disputed = sampled.decisions.case(dispute.task_id, actor=COORDINATOR)
    assert disputed.shape == "disputed_pair"
    assert [a.decision for a in disputed.actions if a.enabled][:1] == ["keep_decision"]
    assert disputed.notice is not None and disputed.notice.endswith(NOTICE_MERGE)
    with pytest.raises(Forbidden) as refused:
        sampled.decisions.check(
            dispute.task_id, "keep_decision", actor=STEWARD, **seen(sampled, dispute.task_id)
        )
    assert refused.value.code == "own_decision"


# ---------------------------------------------------------------------------------------------- voids


def test_a_deleted_record_voids_its_sample_and_a_staged_answer_fails(sampled: Hub) -> None:
    workbench_world(sampled)
    sample = join_sample(sampled)
    sampled.tray.stage(
        sample.task_id, "blind_link", actor=COORDINATOR, target=sample.target, **seen(sampled, sample.task_id)
    )
    land(sampled, [row(sample.source.system, sample.source.key, "person", {}, op="delete", at=utcnow())])
    arrive(sampled)
    voided = sampled.store.samples_by_id([sample.sample_id])[sample.sample_id]
    assert voided.status == "void" and sample_task(sampled, sample).status == "closed"
    window_passed(sampled)
    assert sampled.tray.flush().outcomes == {"sample_void": 1}
    assert sampled.store.agreement_rows("person", None, 10) == []  # a void counts nothing
    assert sampled.store.sample_counts("person", 100)["void"] == 1


def test_a_deleted_record_closes_the_review_its_blind_disagreement_opened(sampled: Hub) -> None:
    workbench_world(sampled)
    sample = join_sample(sampled)
    assert decide(sampled, sample.task_id, "blind_none").outcome == "disagreed"
    answered = sampled.store.samples_by_id([sample.sample_id])[sample.sample_id]
    dispute_id = answered.dispute_task_id
    assert sampled.store.tasks_by_id([dispute_id])[dispute_id].status == "open"
    land(sampled, [row(sample.source.system, sample.source.key, "person", {}, op="delete", at=utcnow())])
    arrive(sampled)
    assert sampled.store.tasks_by_id([dispute_id])[dispute_id].status == "closed"
    # the answer stands: its sample is not voided, and the disagreement still counts once
    assert sampled.store.samples_by_id([sample.sample_id])[sample.sample_id].status == "disagreed"
    (counted,) = sampled.store.agreement_rows("person", None, 10)
    assert (counted.reviewed, counted.agreed) == (1, 0)


def test_a_keep_apart_sample_whose_pair_merged_since_no_longer_counts(sampled: Hub) -> None:
    workbench_world(sampled)
    golden_pair(sampled)
    pair_task = task_of(sampled, kind="possible_duplicate")
    decide(sampled, pair_task.task_id, "keep_apart", actor=STEWARD)
    (sample,) = [s for s in all_samples(sampled, "organisation") if s.decision == "keep_apart"]
    first, second = sample.master_ids
    sampled.lifecycle.merge("organisation", first, second, maker=STEWARD, checker=COORDINATOR, reason="test")
    case = sampled.decisions.case(sample.task_id, actor=COORDINATOR)
    assert (case.shape, case.notice) == ("blind_pair", NOTICE_PAIR_GONE)
    entry = decide(sampled, sample.task_id, "blind_none")
    assert (entry.status, entry.outcome) == ("failed", "sample_void")
    assert sampled.store.samples_by_id([sample.sample_id])[sample.sample_id].status == "void"
    assert sample_task(sampled, sample).status == "closed"
    assert sampled.store.agreement_rows("organisation", None, 10) == []  # a void counts nothing


# ---------------------------------------------------------------------------------------------- the views


def test_samples_live_in_their_own_view_and_out_of_the_other_figures(sampled: Hub) -> None:
    workbench_world(sampled)
    samples = all_samples(sampled)
    assert samples and not [t for t in open_tasks(sampled) if t.kind != "quality_sample"]
    counts = sampled.inbox.counts(actor=STEWARD)
    assert counts.views == {
        "mine": 0,
        "team": 0,
        "breaching": 0,
        "snoozed": 0,
        "escalated": 0,
        "samples": len(samples),
    }
    assert counts.kinds["quality_sample"] == len(samples) and counts.samples_breaching == 0
    health = sampled.inbox.health(actor=STEWARD)
    assert (health.open_tasks, health.breaching, health.paused) == (0, 0, ())
    assert sampled.inbox.page("mine", actor=STEWARD).rows == ()
    snoozed = samples[0].task_id
    sampled.inbox.snooze(snoozed, actor=COORDINATOR, hours=4)
    assert snoozed in {r.task_id for r in sampled.inbox.page("samples", actor=STEWARD).rows}
    assert sampled.inbox.counts(actor=STEWARD).views["snoozed"] == 0
    # past the 72 hours of a quality sample's service level
    later = utcnow() + timedelta(hours=73)
    sampled.inbox.clock = lambda: later
    past = sampled.inbox.counts(actor=STEWARD)
    assert past.samples_breaching == min(len(samples), capacity.COUNT_CAP)
    assert past.views["breaching"] == 0
