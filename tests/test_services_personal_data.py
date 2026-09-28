"""The backstop for RULE10: no personal value outside the vault and the value columns, the workbench's tables
included (owner: SERVICES)."""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest

from mdm.engine.standardise import phone_e164
from mdm.models.errors import EstimationError
from mdm.services import estimation as estimation_module
from tests.helpers import (
    T0,
    arrive,
    crm_person_key,
    land,
    mini_world,
    person_payload,
    row,
    seen,
)

#: the value columns the classification names: they hold personal values by design
ALLOWED = {
    ("work", "source_state", "std_values"),
    ("work", "source_state", "match_forms"),
    ("work", "source_state", "ids"),
    ("work", "source_state", "approved_values"),
    ("work", "blocking_key", "key_value"),
    ("hub", "steward_value", "value"),
}


def _personal_values(rows) -> set[str]:
    found: set[str] = set()
    for landing in rows:
        if landing.entity != "person":
            continue
        payload = landing.payload
        for name in ("given_name", "family_name"):
            if len(payload.get(name, "")) >= 6:
                found.add(payload[name])
        for name in ("email", "person_ref", "phone"):
            if payload.get(name):
                found.add(payload[name])
        if payload.get("phone"):
            found.add(phone_e164(payload["phone"], "999"))
        date = payload.get("birth_date") or ""
        if len(date) == 10 and date[4] == "-":
            found.add(date)
        elif len(date) == 10 and date[2] == "/" and date[5] == "/":  # day first: its ISO form is kept too
            found.add(f"{date[6:]}-{date[3:5]}-{date[:2]}")
    return {v.lower() for v in found if v}


def _text_columns(hub) -> list[tuple[str, str, str]]:
    schemas = [hub.store.t(g, "x").split(".")[0] for g in ("work", "hub", "audit")]
    marks = ", ".join("?" for _ in schemas)
    rows = hub.store._fetch_all(
        "/*mdm:small*/ SELECT table_schema, table_name, column_name, data_type FROM information_schema.columns "
        f"WHERE table_schema IN ({marks}) ORDER BY table_schema, table_name, ordinal_position",
        schemas,
    )
    out = []
    for schema, table, column, data_type in rows:
        if data_type.lower() in ("text", "varchar", "character varying", "json", "jsonb"):
            out.append((schema, table, column))
    return out


def _decide_everything(hub) -> None:
    """The workbench over every open person task: cases, reveals with a reason, snoozes and escalations, a
    decision staged on each and flushed, and the record reader's views, so the tray, the labels, the new task
    columns and the access log are scanned too (initiative 3)."""
    from mdm.models.canonical import utcnow
    from mdm.models.errors import MdmError
    from tests.helpers import STEWARD, open_tasks

    tasks = open_tasks(hub, "person")
    assert tasks
    for number, task in enumerate(tasks):
        case = hub.decisions.case(task.task_id, actor=STEWARD)
        if case.masked:
            hub.decisions.reveal(task.task_id, actor=STEWARD, reason="deciding_task")
        offered = [
            a for a in case.actions if a.enabled and a.decision in ("link", "not_a_match", "approve_update")
        ]
        if number % 2 == 1:
            hub.inbox.snooze(task.task_id, actor=STEWARD, hours=1)
        if offered:
            chosen = offered[0]
            try:
                entry = hub.tray.stage(
                    task.task_id,
                    chosen.decision,
                    actor=STEWARD,
                    target=chosen.target,
                    **seen(hub, task.task_id),
                )
            except MdmError:
                entry = None
            if entry is not None and number == 0:  # the first is taken back, and left open, escalated
                hub.tray.undo(entry.entry_id, actor=STEWARD)
                hub.inbox.escalate(task.task_id, actor=STEWARD, reason="second_opinion")
        for candidate in case.candidates:
            hub.lookup.golden("person", candidate.master_id, actor=STEWARD, reveal=True, reason="audit_check")
            hub.lookup.why("person", candidate.master_id, "family_name", actor=STEWARD)
            hub.lookup.timeline("person", candidate.master_id, actor=STEWARD)
    moment = utcnow() + timedelta(seconds=hub.settings.undo_seconds + 1)
    hub.tray.clock = lambda: moment
    hub.tray.flush()
    assert hub.store.staged_count(10) == 0
    assert hub.tray.entries(actor=STEWARD)


@pytest.mark.parametrize("which", ["mini", "demo"])
def test_no_personal_value_outside_the_vault_and_the_value_columns(
    which, hub, small_world, caplog, monkeypatch
) -> None:
    caplog.set_level(logging.DEBUG)
    world = mini_world(persons=12, organisations=3) if which == "mini" else small_world
    extra = [
        # a reject that carries personal values in the payload
        row("crm", "C9999999", "person", {"given_name": "Tamsin", "nickname": "Tamsin Quorrel"}),
        # rule failures: a malformed e-mail and a birth date out of range
        row(
            "crm",
            crm_person_key(20),
            "person",
            person_payload(20, email="broken-address", birth_date="1850-02-02"),
        ),
        row("crm", "C7777770", "person", person_payload(30)),
    ]
    land(hub, [*world.rows, *extra])
    arrive(hub)
    # a held critical update and a review
    land(
        hub,
        [
            row(
                "crm",
                "C7777770",
                "person",
                person_payload(30, family_name="Wrenfield"),
                at=T0 + timedelta(days=1),
            )
        ],
    )
    land(
        hub,
        [
            row(
                "student_records",
                "S7777777",
                "person",
                person_payload(3, email=None, phone=None, birth_date=None),
            )
        ],
    )
    arrive(hub)
    _decide_everything(hub)
    # a failed estimation
    original = estimation_module.em
    monkeypatch.setattr(estimation_module, "em", lambda *a, **k: original(*a, **{**k, "max_iter": 1}))
    with pytest.raises(EstimationError) as raised:
        hub.estimation.estimate("person", actor=hub.actor, method="em", sample_records=50, u_pairs=20)
    messages = [str(raised.value), *(r.getMessage() for r in caplog.records)]

    landed = hub.store.landing_above(0, 100_000)
    values = _personal_values(landed) | {"tamsin quorrel", "broken-address"}
    leaks = []
    for schema, table, column in _text_columns(hub):
        group = schema.rsplit("_", 1)[1]
        if (group, table, column) in ALLOWED:
            continue
        found = hub.store._fetch_all(f"/*mdm:paged*/ SELECT {column} FROM {schema}.{table} LIMIT 100000")
        for (value,) in found:
            if value is None:
                continue
            text = str(value).lower()
            hits = [v for v in values if v in text]
            if hits:
                leaks.append((table, column, hits[:2]))
                break
    assert leaks == []
    assert not [m for m in messages if any(v in m.lower() for v in values)]
    assert hub.store.rejects(10) and hub.store.tasks("person", None, "open", 10, None)


def test_no_personal_value_in_the_checkpoints_tables_or_the_breakers_audit(
    hub, caplog: pytest.LogCaptureFixture
) -> None:
    """Quality samples and their answers, agreement counts, the breaker's state, arrival counts, the
    breaker's change sets and every log line on the way hold codes, IDs, source keys, signatures and numbers
    only (story 3.2)."""
    from mdm.models.canonical import utcnow
    from mdm.services.context import Hub
    from tests.helpers import COORDINATOR, OWNER, STEWARD, open_tasks, person_review, task_of, workbench_world

    caplog.set_level(logging.DEBUG)
    sampled = Hub.open(hub.settings.with_(sample_share=1.0), store=hub.store, as_role="data_owner")
    try:
        workbench_world(sampled)
        source = person_review(sampled)
        review = task_of(sampled, kind="review", source=source)
        sampled.tray.stage(review.task_id, "link", actor=STEWARD, **seen(sampled, review.task_id))
        samples = [t for t in open_tasks(sampled, "person", "quality_sample") if t.source is not None]
        for number, task in enumerate(samples[:4]):
            sampled.decisions.reveal(task.task_id, actor=COORDINATOR, reason="deciding_task")
            case = sampled.decisions.case(task.task_id, actor=COORDINATOR)
            target = case.choices[0].master_id if case.choices and number % 2 == 0 else None
            sampled.tray.stage(
                task.task_id,
                "blind_link" if target else "blind_none",
                actor=COORDINATOR,
                target=target,
                **seen(sampled, task.task_id),
            )
        moment = utcnow() + timedelta(seconds=sampled.settings.undo_seconds + 1)
        sampled.tray.clock = lambda: moment
        sampled.tray.flush()
        for dispute in [
            t for t in open_tasks(sampled, "person", "review") if t.reason == "blind_disagreement"
        ]:
            sampled.tray.stage(
                dispute.task_id, "keep_decision", actor=COORDINATOR, **seen(sampled, dispute.task_id)
            )
        sampled.tray.clock = lambda: moment + timedelta(minutes=5)
        sampled.tray.flush()
        sampled.breaker.demo_trip("person", figures={"agreed": 30, "reviewed": 40, "threshold": 0.95})
        land(sampled, [row("crm", crm_person_key(3), "person", person_payload(3), at=T0 + timedelta(days=2))])
        arrive(sampled)
        sampled.breaker.restore("person", actor=OWNER, reason="cause_fixed")
        arrive(sampled)
        assert sampled.store.agreement_rows("person", None, 10)
    finally:
        sampled.close()
    landed = hub.store.landing_above(0, 100_000)
    values = _personal_values(landed)
    leaks = []
    for schema, table, column in _text_columns(hub):
        group = schema.rsplit("_", 1)[1]
        if (group, table, column) in ALLOWED:
            continue
        found = hub.store._fetch_all(f"/*mdm:paged*/ SELECT {column} FROM {schema}.{table} LIMIT 100000")
        for (value,) in found:
            text = str(value).lower() if value is not None else ""
            hits = [v for v in values if v in text]
            if hits:
                leaks.append((table, column, hits[:2]))
                break
    assert leaks == []
    tables = {table for _, table, _ in _text_columns(hub)}
    assert {"quality_sample", "quality_agreement", "breaker_state", "arrival_hour"} <= tables
    lines = [r.getMessage().lower() for r in caplog.records]
    assert any("breaker_tripped" in line for line in lines)  # the log was captured
    assert not [line for line in lines if any(v in line for v in values)]
