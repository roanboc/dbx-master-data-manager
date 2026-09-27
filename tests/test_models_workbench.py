"""The workbench's contract: its dataclasses, the impact line, the work a decision carries, the action
table without raising, and the workbench's settings (initiative 3, story 3.1)."""

from __future__ import annotations

import dataclasses
import inspect
from datetime import UTC, datetime, timedelta

import pytest

import mdm.models.workbench as workbench
from mdm.config import DEFAULT_SLA_HOURS, Settings
from mdm.models.authority import ACTIONS, AUTOMATED_MATCHER, ROLE_LABELS, ROLES, Actor, allowed
from mdm.models.changes import WorkWrites
from mdm.models.errors import ConfigError, Forbidden
from mdm.models.records import SourceKey
from mdm.models.tasks import KIND_LABELS, TASK_KINDS, Task
from mdm.services.authority import require
from tests import workbench_samples as samples

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def test_every_dataclass_of_the_contract_is_frozen_with_slots_and_has_a_sample() -> None:
    classes = {
        obj
        for _, obj in inspect.getmembers(workbench, inspect.isclass)
        if obj.__module__ == workbench.__name__ and dataclasses.is_dataclass(obj)
    }
    assert classes
    for cls in classes:
        assert cls.__dataclass_params__.frozen, cls.__name__  # type: ignore[attr-defined]
        assert hasattr(cls, "__slots__"), cls.__name__
    sampled = {type(sample) for sample in samples.ALL}
    assert classes - sampled == set()


def test_the_code_lists_of_the_contract() -> None:
    assert set(KIND_LABELS) == set(TASK_KINDS)
    assert set(ROLE_LABELS) == set(ROLES)
    assert len(set(workbench.DECISIONS)) == len(workbench.DECISIONS)
    assert not set(workbench.DECISIONS) & set(workbench.TASK_ACTIONS)
    assert set(workbench.LABELS) <= {"match", *workbench.DECISIONS}
    for decision in (*workbench.DECISIONS, "work_tasks", "view_tasks", "flush_tray"):
        assert decision in ACTIONS


@pytest.mark.parametrize(
    ("impact", "sentence"),
    [
        (
            workbench.Impact(xrefs_added=1, golden_changed=("Phone",)),
            "+1 cross-reference · golden phone changes · no ID retired",
        ),
        (workbench.Impact(records_created=1, xrefs_added=1), "1 record created · +1 cross-reference"),
        (
            workbench.Impact(xrefs_removed=1, golden_changed=("Name", "Phone")),
            "−1 cross-reference · golden name and phone change",
        ),
        (
            workbench.Impact(golden_changed=("Name", "Registered ID", "Email"), held_released=1),
            "golden name, registered ID and email change · 1 hold released",
        ),
        (
            workbench.Impact(xrefs_added=2, relationships_changed=2, ids_retired=1),
            "+2 cross-references · 2 relationships change · 1 ID retired",
        ),
        (workbench.Impact(), "no change to published records"),
    ],
)
def test_the_impact_line_is_built_from_counts_and_labels(impact: workbench.Impact, sentence: str) -> None:
    assert impact.sentence() == sentence


def test_service_levels_give_a_due_time_per_kind() -> None:
    levels = workbench.ServiceLevels(DEFAULT_SLA_HOURS)
    assert levels.due("review", NOW) == NOW + timedelta(hours=8)
    assert levels.due("orphan", NOW) == NOW + timedelta(hours=72)
    assert levels.due("not_a_kind", NOW) == NOW + timedelta(hours=workbench.FALLBACK_SERVICE_HOURS)


def test_a_task_built_before_the_workbench_reads_as_before() -> None:
    task = Task("TSK-1", "TK-1", "person", "review", "open", None, (), "review_band", {}, {}, None, NOW, NOW)
    assert (task.due_at, task.claimed_by, task.snoozed_until, task.escalated_at, task.escalation) == (
        None,
    ) * 5


def test_work_carries_the_decision_merges_and_goes_with_the_last_chunk() -> None:
    source, other = SourceKey("crm", "C000812"), SourceKey("crm", "C000900")
    decision = WorkWrites(
        "organisation",
        labels=(samples.MATCH_LABEL,),
        requeue=((source, "crm-7-1", 41),),
        tray=(samples.TRAY_SETTLEMENT,),
        expect_events=((source, "crm-7-1"),),
        close_task_ids=("TSK-1",),
    )
    assert WorkWrites("organisation").empty()
    for name in ("labels", "requeue", "tray", "expect_events", "close_task_ids"):
        assert not WorkWrites("organisation", **{name: getattr(decision, name)}).empty(), name
    own = WorkWrites("organisation", settle=((other, "crm-7-2"),), release=(source,))
    merged = own.merged(decision)
    assert merged.settle == own.settle and merged.release == own.release
    assert merged.labels == decision.labels and merged.close_task_ids == ("TSK-1",)
    part, rest = merged.split([source, other])
    assert part.settle == own.settle and part.release == (source,)
    assert (part.labels, part.requeue, part.tray, part.expect_events, part.close_task_ids) == ((),) * 5
    assert rest.tray == decision.tray and rest.expect_events == decision.expect_events
    with pytest.raises(ValueError):
        own.merged(WorkWrites("person"))


def test_allowed_is_require_without_raising() -> None:
    actors = [Actor(f"persona:{role}", "person", role, persona=True) for role in ROLES] + [AUTOMATED_MATCHER]
    for actor in actors:
        for action in (*ACTIONS, "not_an_action"):
            try:
                require(actor, action)
                expected = True
            except Forbidden:
                expected = False
            assert allowed(actor, action) is expected, (actor.role, action)
    steward = Actor("persona:data_steward", "person", "data_steward", persona=True)
    owner = Actor("persona:data_owner", "person", "data_owner", persona=True)
    consumer = Actor("persona:consumer", "person", "consumer", persona=True)
    assert allowed(steward, "approve_update") and allowed(steward, "work_tasks")
    assert allowed(owner, "view_tasks") and not allowed(owner, "work_tasks")
    assert not allowed(consumer, "view_tasks") and not allowed(consumer, "flush_tray")


def test_the_workbench_settings_and_their_refusals() -> None:
    default = Settings.from_env({})
    assert default == Settings()
    assert (default.undo_seconds, default.claim_minutes, default.ui_port, default.app_port) == (
        60,
        10,
        8050,
        0,
    )
    assert default.tray_worker_on and not default.in_databricks_app
    assert default.service_level_hours("possible_duplicate") == 24
    assert default.service_level_hours("not_a_kind") == 24
    given = Settings.from_env(
        {
            "MDM_UNDO_SECONDS": "4",
            "MDM_SLA_HOURS": " review=2, orphan=48 ",
            "MDM_CLOSE_CALL_POINTS": "5.5",
            "MDM_TRAY_WORKER": "OFF",
        }
    )
    assert given.undo_seconds == 4 and given.close_call_points == 5.5 and not given.tray_worker_on
    assert dict(given.sla_hours) == {**dict(DEFAULT_SLA_HOURS), "review": 2, "orphan": 48}
    app = Settings.from_env({"DATABRICKS_APP_PORT": "8000"})
    assert app.in_databricks_app and app.app_port == 8000 and not app.local_mode and not app.tray_worker_on
    assert Settings.from_env({"MDM_TRAY_WORKER": "on", "DATABRICKS_APP_NAME": "x"}).tray_worker_on
    for variable, value in [
        ("MDM_UNDO_SECONDS", "0"),
        ("MDM_CLAIM_MINUTES", "soon"),
        ("MDM_SLA_HOURS", "review"),
        ("MDM_SLA_HOURS", "review=0"),
        ("MDM_SLA_HOURS", "someday=4"),
        ("MDM_CLOSE_CALL_POINTS", "101"),
        ("MDM_CLOSE_CALL_POINTS", "nan"),
        ("MDM_TRAY_WORKER", "sometimes"),
        ("MDM_UI_PORT", "0"),
    ]:
        with pytest.raises(ConfigError) as refused:
            Settings.from_env({variable: value})
        assert refused.value.fields == {"variable": variable}
    with pytest.raises(ConfigError):
        Settings().with_(sla_hours=(("review", 8),))
