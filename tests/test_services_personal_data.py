"""The backstop for RULE10: no personal value outside the vault and the value columns (owner: SERVICES)."""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest

from mdm.engine.standardise import phone_e164
from mdm.models.errors import EstimationError
from mdm.services import estimation as estimation_module
from tests.test_services_fixtures import (
    T0,
    arrive,
    crm_person_key,
    land,
    mini_world,
    person_payload,
    row,
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
