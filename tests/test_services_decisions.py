"""A task's case on both engines: shapes, candidates with their waterfalls and previews, the close call, actions
by role, the reveal with a reason, the check at staging and the cache (owner: SERVICES, B.6.5, B.9.1)."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import timedelta

import pytest

from mdm.models.canonical import utcnow
from mdm.models.changes import WorkWrites
from mdm.models.errors import Conflict, Forbidden, NotFound
from mdm.models.records import SourceKey
from mdm.models.tasks import Task, task_id, task_key
from mdm.services import decisions as decisions_module
from mdm.services.context import Hub
from tests.helpers import (
    CONSUMER,
    COORDINATOR,
    OWNER,
    STEWARD,
    arrive,
    golden_pair,
    held_name_change,
    land,
    open_tasks,
    organisation_close_call,
    person_review,
    row,
    seen,
    standalone_organisation,
    task_of,
    workbench_world,
)

WEBSITE = "www.ossiver.example"


@pytest.fixture
def close_call(hub: Hub) -> tuple[Hub, Task]:
    workbench_world(hub)
    source = organisation_close_call(hub, website=WEBSITE)
    return hub, task_of(hub, kind="review", source=source)


def offered(case) -> list[str]:
    """The decisions a case offers (not the task actions claim, snooze, escalate, undo)."""
    return sorted(
        {a.decision for a in case.actions if a.decision not in ("claim", "snooze", "escalate", "undo")}
    )


def statements_of(hub: Hub, call) -> list[str]:
    seen: list[str] = []
    remove = hub.store.add_listener(seen.append)
    try:
        call()
    finally:
        remove()
    return seen


def costly(statements: list[str]) -> list[str]:
    """Statements that score or survive: candidate retrieval and survivorship reads."""
    return [s for s in statements if "blocking_key" in s or "steward_value" in s]


# ---------------------------------------------------------------------------------------------- the close call


def test_a_close_call_shows_both_candidates_and_asks_for_a_choice(close_call) -> None:
    hub, task = close_call
    case = hub.decisions.case(task.task_id, actor=STEWARD)
    assert case.shape == "source" and case.close_call and case.default_candidate is None
    assert case.columns[0] == "Arriving · crm:C0900003"
    assert [c.master_id for c in case.candidates] == sorted(task.master_ids)
    assert [c.index for c in case.candidates] == [1, 2]
    model = hub.registry.published("organisation")
    for candidate in case.candidates:
        assert round(candidate.score, 2) == 79.19 and candidate.band == "review"
        assert math.isclose(candidate.total, 1.93, abs_tol=0.01)
        steps = candidate.steps
        assert steps[0].label == "Prior" and steps[0].comparison is None and steps[0].start == 0.0
        assert all(math.isclose(a.end, b.start, abs_tol=1e-9) for a, b in zip(steps, steps[1:], strict=False))
        assert math.isclose(steps[-1].end, candidate.total, abs_tol=1e-9)
        assert math.isclose(sum(s.weight for s in steps), candidate.total, abs_tol=1e-9)
        lower, upper = model.match.bands.lower, model.match.bands.upper
        assert candidate.thresholds == pytest.approx(
            (math.log2(lower / (100 - lower)), math.log2(upper / (100 - upper)))
        )
        assert any("postcode" in sentence.lower() for sentence in candidate.flip)
        assert candidate.hard_rule is None and candidate.blocked_by is None
        assert candidate.member.startswith("finance:F90000")
        # each candidate's own preview: linking adds the cross-reference and the crm website
        assert candidate.preview is not None and candidate.preview.master_id == candidate.master_id
        assert candidate.preview.impact.xrefs_added == 1
        assert candidate.preview.impact.golden_changed == ("Website",)
        website = next(r for r in candidate.preview.rows if r.label == "Website")
        assert (website.now, website.after, website.changed) == (None, WEBSITE, True)
    rows = {r.attribute: r for r in case.compare}
    assert rows["name"].agreement == ("agree", "agree")
    assert rows["postcode"].agreement == ("disagree", "disagree")
    assert rows["registered_id"].agreement == ("missing", "missing")
    assert rows["country"].agreement == ("", "")
    assert rows["name"].critical and not rows["name"].personal
    assert rows["registered_id"].values[0] is None and rows["registered_id"].values[1] is not None
    assert not case.masked and case.revealable == ()
    links = [a for a in case.actions if a.decision == "link"]
    assert sorted(a.target for a in links) == sorted(task.master_ids) and all(a.key == "L" for a in links)
    assert "not_a_match" in offered(case) and case.event_id is not None


def test_the_check_needs_a_choice_in_a_close_call(close_call) -> None:
    hub, task = close_call
    with pytest.raises(Forbidden) as refused:
        hub.decisions.check(task.task_id, "link", actor=STEWARD, **seen(hub, task.task_id))
    assert refused.value.code == "close_call"
    for master in task.master_ids:
        staging = hub.decisions.check(
            task.task_id, "link", actor=STEWARD, target=master, **seen(hub, task.task_id)
        )
        assert staging.target == master and staging.decision == "link"
        assert staging.locks == (f"task:{task.task_id}", "source:organisation:crm:C0900003")
        assert staging.subject["source"] == "crm:C0900003" and staging.subject["kind"] == "review"
        assert staging.signature and staging.event_id == task.event_id
    with pytest.raises(Forbidden) as elsewhere:
        hub.decisions.check(
            task.task_id, "link", actor=STEWARD, target="ORG-999999", **seen(hub, task.task_id)
        )
    assert elsewhere.value.code == "candidate_not_offered"
    with pytest.raises(Forbidden) as not_offered:
        hub.decisions.check(task.task_id, "keep_apart", actor=STEWARD, **seen(hub, task.task_id))
    assert not_offered.value.code == "decision_not_offered"
    with pytest.raises(Conflict) as moved:
        hub.decisions.check(task.task_id, "not_a_match", actor=STEWARD, seen_event="an-older-event")
    assert moved.value.code == "record_changed"
    assert hub.decisions.check(task.task_id, "not_a_match", actor=STEWARD, **seen(hub, task.task_id)).subject[
        "candidates"
    ] == sorted(task.master_ids)


def test_the_check_refuses_a_staged_a_claimed_or_a_closed_task(close_call) -> None:
    hub, task = close_call
    hub.inbox.claim(task.task_id, actor=COORDINATOR)
    with pytest.raises(Conflict) as claimed:
        hub.decisions.check(task.task_id, "not_a_match", actor=STEWARD, **seen(hub, task.task_id))
    assert claimed.value.code == "claimed_by_another"
    hub.inbox.release(task.task_id, actor=COORDINATOR)
    hub.tray.stage(task.task_id, "not_a_match", actor=STEWARD, **seen(hub, task.task_id))
    with pytest.raises(Conflict) as staged:
        hub.decisions.check(task.task_id, "not_a_match", actor=STEWARD, **seen(hub, task.task_id))
    assert (staged.value.code, staged.value.fields["mine"]) == ("already_staged", True)
    with pytest.raises(Conflict) as theirs:
        hub.decisions.check(task.task_id, "not_a_match", actor=COORDINATOR, **seen(hub, task.task_id))
    assert theirs.value.fields["mine"] is False
    case = hub.decisions.case(task.task_id, actor=STEWARD)
    assert case.staged is not None and case.staged.mine
    assert "undo" in {a.decision for a in case.actions}
    assert all(not a.enabled and "tray" in (a.why_not or "") for a in case.actions if a.decision == "link")
    hub.store.apply_work(WorkWrites("organisation", close_task_ids=(task.task_id,)))
    with pytest.raises(Conflict) as closed:
        hub.decisions.check(task.task_id, "not_a_match", actor=STEWARD, **seen(hub, task.task_id))
    assert closed.value.code == "task_closed"
    with pytest.raises(Conflict):
        hub.decisions.case(task.task_id, actor=STEWARD)
    with pytest.raises(NotFound):
        hub.decisions.case("TSK-0000000000000000", actor=STEWARD)


# ---------------------------------------------------------------------------------------------- the other shapes


def test_a_person_review_is_masked_and_links_by_default(hub: Hub) -> None:
    workbench_world(hub)
    source = person_review(hub)
    task = task_of(hub, kind="review", source=source)
    case = hub.decisions.case(task.task_id, actor=STEWARD)
    assert case.shape == "source" and not case.close_call
    assert case.default_candidate == task.master_ids[0]
    (candidate,) = case.candidates
    assert round(candidate.score, 2) == 88.66 and math.isclose(candidate.total, 2.97, abs_tol=0.01)
    assert case.masked and set(case.revealable) >= {"given_name", "family_name", "birth_date"}
    rows = {r.attribute: r for r in case.compare}
    assert rows["given_name"].values == ("C***", "C***") and rows["given_name"].agreement == ("agree",)
    assert rows["birth_date"].values == ("hidden", "hidden") and rows["birth_date"].agreement == ("partial",)
    assert candidate.title == "C*** K***" and case.row.title == "C*** K***"
    assert (
        hub.decisions.check(task.task_id, "link", actor=STEWARD, **seen(hub, task.task_id)).target
        == task.master_ids[0]
    )


def test_a_held_update_offers_approve_and_reject(hub: Hub) -> None:
    workbench_world(hub)
    source = standalone_organisation(hub)
    held_name_change(hub, source, "Quillmere Optical Ltd", at=utcnow())
    task = task_of(hub, kind="held")
    case = hub.decisions.case(task.task_id, actor=STEWARD)
    assert case.shape == "held_update" and case.notice is None
    assert offered(case) == ["approve_update", "reject_update"]
    assert {a.key for a in case.actions if a.decision in ("approve_update", "reject_update")} == {"A", "R"}
    assert case.columns[0].startswith("Approved · crm:C0800001") and case.columns[1].startswith("New · ")
    name = next(r for r in case.compare if r.attribute == "name")
    assert name.changed and name.values == (
        "Quillmere Optics Ltd",
        "Quillmere Optical Ltd",
        "Quillmere Optics Ltd",
    )
    assert case.preview is not None and case.preview.impact.golden_changed == ("Name",)
    assert case.preview.impact.held_released == 1
    staging = hub.decisions.check(task.task_id, "approve_update", actor=STEWARD, **seen(hub, task.task_id))
    assert staging.event_id == task.event_id
    # a held update of a record another source outranks for the name publishes nothing
    held_name_change(hub, SourceKey("crm", "C000000"), "Brindle Works Holdings Limited", at=utcnow())
    outranked = task_of(hub, kind="held", source=SourceKey("crm", "C000000"))
    assert hub.decisions.case(outranked.task_id, actor=STEWARD).preview.impact.golden_changed == ()


def test_a_golden_pair_an_orphan_and_the_information_shapes(hub: Hub) -> None:
    workbench_world(hub)
    golden_pair(hub)
    pair = task_of(hub, kind="possible_duplicate")
    case = hub.decisions.case(pair.task_id, actor=STEWARD)
    assert case.shape == "golden_pair" and offered(case) == ["keep_apart"]
    assert case.notice == decisions_module.NOTICE_MERGE
    assert case.columns == tuple(f"{m} · golden record" for m in pair.master_ids)
    staging = hub.decisions.check(pair.task_id, "keep_apart", actor=STEWARD, **seen(hub, pair.task_id))
    assert staging.locks == (f"task:{pair.task_id}", *(f"golden:{m}" for m in pair.master_ids))
    assert staging.event_id is None and staging.subject["master_ids"] == list(pair.master_ids)

    source = standalone_organisation(hub)
    land(hub, [row("crm", source.key, "organisation", {}, op="delete", at=utcnow())])
    arrive(hub)
    orphan = task_of(hub, kind="orphan")
    case = hub.decisions.case(orphan.task_id, actor=STEWARD)
    assert case.shape == "golden" and offered(case) == ["keep_orphan"]
    assert case.notice == decisions_module.NOTICE_RETIRE
    assert (
        hub.decisions.check(orphan.task_id, "keep_orphan", actor=STEWARD, **seen(hub, orphan.task_id)).target
        == orphan.master_ids[0]
    )

    # a held new record, and an exception: the reason in words and nothing offered
    crm = SourceKey("crm", "C000000")
    for kind, reason, notice in (
        ("held", "new_held", decisions_module.NOTICE_CREATE),
        ("exception", "unknown_master_id", decisions_module.NOTICE_INFORMATION),
    ):
        key = task_key(kind, "organisation", crm)
        state = hub.store.source_states("organisation", [crm])[crm]
        crafted = Task(
            task_id(key, state.event_id),
            key,
            "organisation",
            kind,
            "open",
            crm,
            (),
            reason,
            {},
            {},
            state.event_id,
            utcnow(),
            utcnow(),
        )
        hub.store.apply_work(WorkWrites("organisation", tasks=(crafted,)))
        case = hub.decisions.case(crafted.task_id, actor=STEWARD)
        assert offered(case) == [] and case.notice == notice
        assert case.reason_text
        with pytest.raises(Forbidden) as nothing:
            hub.decisions.check(
                crafted.task_id, "link", actor=STEWARD, target="ORG-000001", **seen(hub, crafted.task_id)
            )
        assert nothing.value.code == "decision_not_offered"


# ---------------------------------------------------------------------------------------------- roles, reveal, cache


def test_a_data_owner_sees_the_actions_disabled_with_a_reason(close_call) -> None:
    hub, task = close_call
    case = hub.decisions.case(task.task_id, actor=OWNER)
    decisions = [a for a in case.actions if a.decision in ("link", "not_a_match")]
    assert decisions and all(not a.enabled for a in decisions)
    assert all(a.why_not == "Your role, data owner, can see tasks but not decide them." for a in decisions)
    assert all(
        not a.enabled and a.why_not for a in case.actions if a.decision in ("claim", "snooze", "escalate")
    )
    with pytest.raises(Forbidden):
        hub.decisions.check(task.task_id, "not_a_match", actor=OWNER, **seen(hub, task.task_id))
    with pytest.raises(Forbidden):
        hub.decisions.case(task.task_id, actor=CONSUMER)


def test_a_reveal_asks_a_reason_code_and_logs_each_attribute(hub: Hub) -> None:
    workbench_world(hub)
    source = person_review(hub)
    task = task_of(hub, kind="review", source=source)
    with pytest.raises(Forbidden) as no_reason:
        hub.decisions.reveal(task.task_id, actor=STEWARD, reason="because I want to")
    assert no_reason.value.code == "reason_required"
    with pytest.raises(Forbidden):
        hub.decisions.reveal(task.task_id, actor=CONSUMER, reason="deciding_task")
    assert hub.store.access_log(None, 100) == []
    revealed = hub.decisions.reveal(task.task_id, actor=STEWARD, reason="deciding_task")
    # the headers come from the same read as the values, so a value never shows under another record
    assert revealed.columns == hub.decisions.case(task.task_id, actor=STEWARD).columns
    rows = {r.attribute: r for r in revealed.compare}
    assert rows["given_name"].values == ("Corvin", "Corvin")
    assert rows["family_name"].values == ("Kettleby", "Kettleby")
    assert rows["birth_date"].values[0] != rows["birth_date"].values[1]
    log = hub.store.access_log(None, 100)
    assert len(log) == revealed.logged
    assert {(r["reason"], r["action"], r["actor"]) for r in log} == {
        ("deciding_task", "reveal", STEWARD.name)
    }
    by_subject: dict[str, set[str]] = {}
    for entry in log:
        by_subject.setdefault(entry["detail"]["subject"], set()).add(entry["attribute"])
    assert by_subject["crm:C1900004"] == {"given_name", "family_name", "birth_date"}
    assert by_subject[task.master_ids[0]] >= {"given_name", "family_name", "birth_date", "email", "phone"}
    assert all(
        e["master_id"] == task.master_ids[0] for e in log if e["detail"]["subject"] == task.master_ids[0]
    )


def test_the_case_is_cached_per_version_and_role(close_call) -> None:
    hub, task = close_call
    first = statements_of(hub, lambda: hub.decisions.case(task.task_id, actor=STEWARD))
    assert costly(first)
    again = statements_of(hub, lambda: hub.decisions.case(task.task_id, actor=STEWARD))
    assert costly(again) == []
    # another role gets its own
    assert costly(statements_of(hub, lambda: hub.decisions.case(task.task_id, actor=COORDINATOR)))
    # a reveal neither reads nor fills the cache
    held = hub.decisions.cache_size_now()
    hub.decisions.reveal(task.task_id, actor=STEWARD, reason="deciding_task")
    assert hub.decisions.cache_size_now() == held
    assert costly(statements_of(hub, lambda: hub.decisions.case(task.task_id, actor=STEWARD))) == []
    # a claim refreshes only the cheap part
    hub.inbox.claim(task.task_id, actor=STEWARD)
    claimed = hub.decisions.case(task.task_id, actor=STEWARD)
    assert claimed.claimed_by == "you" and next(
        a for a in claimed.actions if a.decision == "claim"
    ).label == ("Claimed by you")
    # a commit elsewhere gives a fresh case
    land(
        hub,
        [
            row(
                "crm",
                "C0777777",
                "organisation",
                {"name": "Wrenfold Pottery Ltd", "city": "Norvale"},
                at=utcnow(),
            )
        ],
    )
    arrive(hub)
    assert costly(statements_of(hub, lambda: hub.decisions.case(task.task_id, actor=STEWARD)))
    # a task update gives a fresh case
    updated = replace(task, updated_at=utcnow() + timedelta(seconds=5), status="open")
    hub.store.apply_work(WorkWrites("organisation", tasks=(updated,)))
    assert costly(statements_of(hub, lambda: hub.decisions.case(task.task_id, actor=STEWARD)))


def test_the_cache_keeps_a_bounded_number_of_cases(close_call) -> None:
    hub, task = close_call
    hub.decisions.cache_size = 1
    hub.decisions.case(task.task_id, actor=STEWARD)
    hub.decisions.case(task.task_id, actor=COORDINATOR)
    assert hub.decisions.cache_size_now() == 1


def test_a_prefetch_never_raises(close_call) -> None:
    hub, task = close_call
    hub.decisions.prefetch("TSK-0000000000000000", actor=STEWARD)
    hub.decisions.prefetch(task.task_id, actor=CONSUMER)
    hub.decisions.prefetch(task.task_id, actor=STEWARD)
    assert costly(statements_of(hub, lambda: hub.decisions.case(task.task_id, actor=STEWARD))) == []
    assert len(open_tasks(hub)) == 1
