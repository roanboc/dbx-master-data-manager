"""Helpers for the services tests: an invented mini world, landing, arrival and read-back (owner: SERVICES).

Every name, key and identifier here is invented. The mini world does not need
the demo generator, so the services tests stand on their own: persons in
`hr` (versioned, with a valid `PERSON_REF`), some also in `crm` and
`student_records`; organisations in `finance` (versioned, with a valid
`ORG_REG`), some also in `crm`; employers as references.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from mdm.backend import guard
from mdm.engine.identifiers import luhn_digit, mod97_digits
from mdm.models.changes import ChangeRow
from mdm.models.records import LandingRow, SourceChange, SourceKey
from mdm.models.tasks import Task
from mdm.services.arrival import ArrivalReport
from mdm.services.context import Hub

T0 = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
GIVEN = (
    "Tamsin", "Oskar", "Liora", "Bexley", "Corvin", "Marisol", "Pellam", "Ysolde", "Dariel", "Fenwick",
    "Ilsabet", "Joram", "Kestrel", "Lunet", "Morwen", "Nyssa", "Orrin", "Perrin", "Quilla", "Rhosyn",
)  # fmt: skip
FAMILY = (
    "Quorrel", "Vellim", "Ashgrove", "Brindlemoor", "Castellane", "Dunmarrow", "Elsworthy", "Farrowdale",
    "Glimmerton", "Hollins", "Ivelle", "Jessamy", "Kettleby", "Lorrimer", "Mirefield", "Nettlebed",
)  # fmt: skip
ORG_STEMS = (
    "Brindle Works", "Corvane Supplies", "Halvern Labs", "Merrow Foundry", "Tessel Holdings",
    "Quillon Freight", "Vantor Mills", "Oxlade Instruments", "Pellow Textiles", "Rusk Engineering",
)  # fmt: skip
CITIES = ("Norvale", "Easthollow", "Brackenmere", "Silverwick")

_counter = itertools.count(1)


def person_ref(n: int) -> str:
    """A valid invented PERSON_REF: 8 digits and a Luhn check digit."""
    base = f"{n:08d}"
    return base + luhn_digit(base)


def org_reg(n: int) -> str:
    """A valid invented ORG_REG: 8 digits and two mod-97 check digits."""
    base = f"{n:08d}"
    return base + mod97_digits(base)


def row(
    system: str,
    key: str,
    entity: str,
    payload: Mapping[str, Any],
    *,
    op: str = "upsert",
    at: datetime | None = None,
    version: int | None = None,
    initial: bool = False,
    event: str | None = None,
) -> LandingRow:
    """One landing row with a unique event ID."""
    return LandingRow(
        event_id=event or f"{system}-{key}-{next(_counter):06d}",
        source_system=system,
        source_key=key,
        entity=entity,
        op=op,
        occurred_at=at or T0,
        payload=dict(payload),
        source_version=version,
        initial_load=initial,
    )


def person_payload(i: int, **changes: Any) -> dict[str, Any]:
    given = GIVEN[i % len(GIVEN)]
    family = FAMILY[(i * 7) % len(FAMILY)]
    doc: dict[str, Any] = {
        "given_name": given,
        "family_name": family,
        "birth_date": f"19{60 + i % 40:02d}-{1 + i % 12:02d}-{1 + i % 27:02d}",
        "email": f"{given.lower()}.{family.lower()}{i}@example.org",
        "phone": f"0{i:03d} 55{i:04d}",
        "postcode": f"XA{i % 9 + 1} {i % 7 + 1}QZ",
        "city": CITIES[i % len(CITIES)],
        "country": "XA",
    }
    doc.update(changes)
    return {k: v for k, v in doc.items() if v is not None}


def org_payload(i: int, **changes: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "name": f"{ORG_STEMS[i % len(ORG_STEMS)]} Ltd",
        "registered_id": org_reg(i + 1),
        "postcode": f"XB{i % 9 + 1} {i % 5 + 1}KR",
        "city": CITIES[i % len(CITIES)],
        "country": "XB",
        "website": f"www.{ORG_STEMS[i % len(ORG_STEMS)].split()[0].lower()}.example",
    }
    doc.update(changes)
    return {k: v for k, v in doc.items() if v is not None}


def hr_key(i: int) -> str:
    return f"H{i:06d}"


def crm_person_key(i: int) -> str:
    return f"C1{i:05d}"


def student_key(i: int) -> str:
    return f"S{i:07d}"


def finance_key(i: int) -> str:
    return f"F{i:06d}"


def crm_org_key(i: int) -> str:
    return f"C0{i:05d}"


@dataclass(frozen=True)
class MiniWorld:
    rows: tuple[LandingRow, ...]
    truth: Mapping[tuple[str, SourceKey], str] = field(default_factory=dict)


def mini_world(persons: int = 12, organisations: int = 4, *, initial: bool = False) -> MiniWorld:
    """Organisations first (finance; every second one also in crm), then persons (hr; every second one also
    in crm; every third also in student_records, day-first dates), employers for two in three persons."""
    rows: list[LandingRow] = []
    truth: dict[tuple[str, SourceKey], str] = {}
    at = T0
    for i in range(organisations):
        at += timedelta(seconds=1)
        rows.append(
            row("finance", finance_key(i), "organisation", org_payload(i), at=at, version=1, initial=initial)
        )
        truth[("organisation", SourceKey("finance", finance_key(i)))] = f"O{i}"
        if i % 2 == 0:
            name = org_payload(i)["name"].replace(" Ltd", " Limited")
            rows.append(
                row("crm", crm_org_key(i), "organisation", org_payload(i, name=name), at=at, initial=initial)
            )
            truth[("organisation", SourceKey("crm", crm_org_key(i)))] = f"O{i}"
    for i in range(persons):
        at += timedelta(seconds=1)
        employer = finance_key(i % organisations) if organisations and i % 3 != 2 else None
        payload = person_payload(i, person_ref=person_ref(1000 + i), employer=employer)
        rows.append(row("hr", hr_key(i), "person", payload, at=at, version=1, initial=initial))
        truth[("person", SourceKey("hr", hr_key(i)))] = f"P{i}"
        if i % 2 == 0:
            rows.append(row("crm", crm_person_key(i), "person", person_payload(i), at=at, initial=initial))
            truth[("person", SourceKey("crm", crm_person_key(i)))] = f"P{i}"
        if i % 3 == 0:
            iso = person_payload(i)["birth_date"]
            day_first = f"{iso[8:10]}/{iso[5:7]}/{iso[0:4]}"
            student = person_payload(i, birth_date=day_first, email=None, phone=None)
            rows.append(row("student_records", student_key(i), "person", student, at=at, initial=initial))
            truth[("person", SourceKey("student_records", student_key(i)))] = f"P{i}"
    return MiniWorld(tuple(rows), truth)


# ------------------------------------------------------------------------------------------ acting


def land(hub: Hub, rows: Iterable[LandingRow]) -> int:
    """Write rows to the landing table as the integration platform would (the simulator)."""
    with guard.simulating_integration_platform(hub.settings, hub.store.prefix):
        return hub.store.landing_insert(list(rows))


def arrive(hub: Hub, **options: Any) -> ArrivalReport:
    return hub.arrival.run(started_by=hub.actor, **options)


def change(landing: LandingRow, seq: int, landed: datetime | None = None) -> SourceChange:
    """A landing row as the reader would read it (for `ArrivalService.process`)."""
    return SourceChange(
        event_id=landing.event_id,
        source_system=landing.source_system,
        source_key=landing.source_key,
        entity=landing.entity,
        op=landing.op,
        occurred_at=landing.occurred_at,
        source_version=landing.source_version,
        initial_load=landing.initial_load,
        payload=landing.payload,
        landed_at=landed or landing.occurred_at,
        landing_seq=seq,
    )


# ------------------------------------------------------------------------------------------ reading back


def master_of(hub: Hub, entity: str, system: str, key: str) -> str | None:
    source = SourceKey(system, key)
    return hub.store.xrefs_for_sources(entity, [source]).get(source)


def partition(hub: Hub, entity: str) -> dict[str, tuple[SourceKey, ...]]:
    """Master ID -> its active members, from every cross-reference (paged)."""
    out: dict[str, list[SourceKey]] = {}
    after: SourceKey | None = None
    while True:
        page = hub.store.xref_page(entity, after, 500)
        for xref in page:
            out.setdefault(xref.master_id, []).append(xref.source)
        if len(page) < 500:
            break
        after = page[-1].source
    return {m: tuple(sorted(s)) for m, s in sorted(out.items())}


def clusters(hub: Hub, entity: str) -> set[frozenset[SourceKey]]:
    """The partition without master IDs, for comparing runs that allocate IDs differently."""
    return {frozenset(members) for members in partition(hub, entity).values()}


def golden_rows(hub: Hub, entity: str, status: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    after: str | None = None
    while True:
        page = hub.store.golden_page(entity, after, 500, status=status)
        out.update({g.master_id: g for g in page})
        if len(page) < 500:
            return out
        after = page[-1].master_id


def open_tasks(hub: Hub, entity: str | None = None, kind: str | None = None) -> list[Task]:
    out: list[Task] = []
    after: str | None = None
    while True:
        page = hub.store.tasks(entity, kind, "open", 500, after)
        out.extend(page)
        if len(page) < 500:
            return out
        after = page[-1].task_id


def all_changes(hub: Hub, entity: str | None = None, page_rows: int = 500) -> list[ChangeRow]:
    """Every change row through the feed reader, as a listener reads it."""
    out: list[ChangeRow] = []
    since, cursor = 0, None
    while True:
        page = hub.feed.read(since, cursor=cursor, entity=entity, max_rows=page_rows)
        out.extend(page.changes)
        if page.cursor is None:
            return out
        since, cursor = page.next_watermark, page.cursor


def test_the_mini_world_is_invented_and_valid() -> None:
    world = mini_world()
    assert len({r.event_id for r in world.rows}) == len(world.rows)
    assert all(r.payload.get("email", "@example.org").endswith("@example.org") for r in world.rows)
    assert {r.entity for r in world.rows} == {"person", "organisation"}
    assert set(world.truth) == {(r.entity, SourceKey(r.source_system, r.source_key)) for r in world.rows}
