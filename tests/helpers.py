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
from mdm.models.authority import Actor
from mdm.models.canonical import iso
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


# ------------------------------------------------------------------------------------------ crafted cases for the workbench

#: the persona the workbench acts as by default, and a second steward for claims and staged decisions
STEWARD = Actor("persona:data_steward", "person", "data_steward", persona=True)
COORDINATOR = Actor("persona:coordinating_steward", "person", "coordinating_steward", persona=True)
OWNER = Actor("persona:data_owner", "person", "data_owner", persona=True)
CONSUMER = Actor("persona:consumer", "person", "consumer", persona=True)
NAMESAKE = "Ossiver Instruments Ltd"


def workbench_world(hub: Hub, persons: int = 8, organisations: int = 3) -> None:
    """The mini world, landed and arrived: the golden records the crafted cases meet."""
    land(hub, mini_world(persons=persons, organisations=organisations).rows)
    arrive(hub)


def organisation_close_call(hub: Hub, *, at: datetime | None = None, website: str | None = None) -> SourceKey:
    """Two finance organisations of one name in one city (other postcodes, other registered IDs, no website),
    kept apart by the registered-ID cannot-link rule; then a crm record with the name in other capitals and
    punctuation, the same city, a third postcode and no registered ID, phone or website. It scores 79.19
    against both: one review task naming both, a close call. Returns the crm record. A `website` on the crm
    record leaves the score as it is (the namesakes hold none) and changes the golden website a link makes."""
    when = at or T0 + timedelta(hours=1)
    namesake = {"name": NAMESAKE, "city": "Norvale", "country": "XB"}
    land(
        hub,
        [
            row(
                "finance",
                "F900001",
                "organisation",
                {**namesake, "registered_id": org_reg(9001), "postcode": "XB1 1AA"},
                at=when,
                version=1,
            ),
            row(
                "finance",
                "F900002",
                "organisation",
                {**namesake, "registered_id": org_reg(9002), "postcode": "XB2 2BB"},
                at=when,
                version=1,
            ),
        ],
    )
    arrive(hub)
    variant = {
        "name": NAMESAKE.upper().replace(" LTD", " ltd."),
        "city": "Norvale",
        "country": "XB",
        "postcode": "XB3 3CC",
    }
    if website:
        variant["website"] = website
    land(hub, [row("crm", "C0900003", "organisation", variant, at=when + timedelta(minutes=1))])
    arrive(hub)
    return SourceKey("crm", "C0900003")


def person_review(hub: Hub, i: int = 4, *, at: datetime | None = None) -> SourceKey:
    """A crm record with hr person `i`'s names, its birth date one digit different in the same year, and no
    postcode, e-mail, phone or person reference: it scores 88.66 against that person, a review task."""
    held = person_payload(i)
    birth = held["birth_date"]
    day = int(birth[-2:])
    other = f"{birth[:-2]}{day + 1 if day % 10 != 9 else day - 1:02d}"
    payload = {
        "given_name": held["given_name"],
        "family_name": held["family_name"],
        "birth_date": other,
        "city": held["city"],
        "country": "XA",
    }
    land(hub, [row("crm", "C1900004", "person", payload, at=at or T0 + timedelta(hours=1))])
    arrive(hub)
    return SourceKey("crm", "C1900004")


def standalone_organisation(
    hub: Hub, key: str = "C0800001", name: str = "Quillmere Optics Ltd", *, at: datetime | None = None
) -> SourceKey:
    """A crm organisation no other record resembles: a golden record with this one member."""
    land(
        hub,
        [
            row(
                "crm",
                key,
                "organisation",
                {"name": name, "postcode": "XC1 1QQ", "city": "Easthollow", "country": "XB"},
                at=at or T0 + timedelta(hours=1),
            )
        ],
    )
    arrive(hub)
    return SourceKey("crm", key)


def held_name_change(hub: Hub, source: SourceKey, name: str, *, at: datetime) -> None:
    """A crm organisation renamed: `name` is critical and crm holds critical updates, so a held task."""
    payload = (
        org_payload(0, name=name)
        if source.key == crm_org_key(0)
        else {
            "name": name,
            "postcode": "XC1 1QQ",
            "city": "Easthollow",
            "country": "XB",
        }
    )
    land(hub, [row("crm", source.key, "organisation", payload, at=at)])
    arrive(hub)


def golden_pair(hub: Hub, *, at: datetime | None = None) -> tuple[SourceKey, SourceKey]:
    """Two crm organisations of one name landing together: two new golden records and one possible duplicate
    naming both."""
    when = at or T0 + timedelta(hours=1)
    left = {"name": "Dovecote Joinery Ltd", "postcode": "XC2 2QQ", "city": "Silverwick", "country": "XB"}
    right = {**left, "name": "DOVECOTE JOINERY ltd.", "postcode": "XC3 3QQ"}
    land(
        hub,
        [
            row("crm", "C0800002", "organisation", left, at=when),
            row("crm", "C0800003", "organisation", right, at=when),
        ],
    )
    arrive(hub)
    return SourceKey("crm", "C0800002"), SourceKey("crm", "C0800003")


def seen(hub: Hub, task_id: str) -> dict[str, str | None]:
    """What a steward who opened the task's case now saw of it: the keywords `tray.stage` and
    `decisions.check` take (the record's event for a task with a source record, else the task's version)."""
    task = hub.store.tasks_by_id([task_id]).get(task_id)
    if task is None:
        return {}
    state = hub.store.source_states(task.entity, [task.source]).get(task.source) if task.source else None
    return {"seen_event": state.event_id if state is not None else None, "seen_task": iso(task.updated_at)}


def task_of(hub: Hub, *, kind: str | None = None, source: SourceKey | None = None) -> Task:
    """The one open task of this kind (and source record)."""
    found = [t for t in open_tasks(hub, kind=kind) if source is None or t.source == source]
    assert len(found) == 1, [(t.kind, t.reason, t.source) for t in found]
    return found[0]


# ------------------------------------------------------------------------------------------ signature batches (story 3.3)

#: the signature every `alike_reviews` record shows against its hr person
ALIKE_SIGNATURE = "given_name= · family_name= · birth_date≈ · email∅ · phone∅ · postcode∅ · person_ref∅"
#: the invented target `alike_reviews(..., one_target=True)` lands first: an hr person with no person reference
ONE_TARGET = SourceKey("hr", "H990000")


#: the one target's birth date; each record's differs from it in one digit of the month or the day, so no two
#: records share one, and a record linked to the target never brings the others an exact birth date
ONE_TARGET_BIRTH = "1964-05-12"


def _one_digit_births(birth: str) -> list[str]:
    """Valid dates of `birth`'s year that differ from it in one digit of the month or the day."""
    out: list[str] = []
    for position in (9, 8, 6):
        for digit in "0123456789":
            if birth[position] == digit:
                continue
            candidate = birth[:position] + digit + birth[position + 1 :]
            month, day = int(candidate[5:7]), int(candidate[8:10])
            if 1 <= month <= 12 and 1 <= day <= 28 and candidate not in out:
                out.append(candidate)
    return out


def alike_key(i: int) -> str:
    """The crm key of the i-th alike record: none of the mini world's keys, nor `person_review`'s."""
    return f"C17{i:05d}"


def _alike_payload(held: Mapping[str, Any], ref: str | None) -> dict[str, Any]:
    """A record with `held`'s names, its birth date one digit different in the same year, and no postcode,
    e-mail or phone (as `person_review` builds it); a person reference only when `ref` is given."""
    birth = held["birth_date"]
    day = int(birth[-2:])
    other = f"{birth[:-2]}{day + 1 if day % 10 != 9 else day - 1:02d}"
    payload: dict[str, Any] = {
        "given_name": held["given_name"],
        "family_name": held["family_name"],
        "birth_date": other,
        "city": held["city"],
        "country": "XA",
    }
    if ref is not None:
        payload["person_ref"] = ref
    return payload


def unique_person(i: int, **changes: Any) -> dict[str, Any]:
    """An invented person whose names no other `unique_person` below 320 shares (so a record that meets one
    never meets another), with a valid person reference, an e-mail, a phone and a postcode."""
    given = GIVEN[i % len(GIVEN)]
    family = FAMILY[(i // len(GIVEN)) % len(FAMILY)]
    doc: dict[str, Any] = {
        "given_name": given,
        "family_name": family,
        "birth_date": f"19{50 + i % 45:02d}-{1 + (i // 3) % 12:02d}-{1 + i % 27:02d}",
        "email": f"{given.lower()}.{family.lower()}.u{i}@example.org",
        "phone": f"0{i:03d} 66{i:04d}",
        "postcode": f"XA{i % 9 + 1} {i % 7 + 1}QU",
        "city": CITIES[i % len(CITIES)],
        "country": "XA",
        "person_ref": person_ref(3000 + i),
    }
    doc.update(changes)
    return {k: v for k, v in doc.items() if v is not None}


def alike_world(hub: Hub, n: int) -> None:
    """n hr persons with names of their own (`unique_person`), landed and arrived: the targets of a large
    group of alike reviews, `alike_reviews(hub, n, person=unique_person, first=0)`."""
    land(hub, [row("hr", f"H8{i:05d}", "person", unique_person(i), version=1) for i in range(n)])
    arrive(hub)


def alike_reviews(
    hub: Hub,
    n: int,
    *,
    first: int = 20,
    one_target: bool = False,
    refs: Mapping[int, str] | None = None,
    at: datetime | None = None,
    person: Any = person_payload,
) -> list[SourceKey]:
    """n crm records that each meet hr person `first + i` the way `person_review` does, so each opens a review
    task with the signature `ALIKE_SIGNATURE`; returns their source keys in order. Needs
    `workbench_world(hub, persons=first + n)` first. The mini world's persons 80 apart share names and a birth
    year, so a record would meet two golden records, a close call: with `first + n` at most 80 none does, and
    `alike_world` lands persons with names of their own for larger groups.

    With `one_target`, every record meets one golden record the helper lands first: an hr person with invented
    names of its own and no person reference (the world's hr persons all hold one); the records are arrived one
    per run, so no two form a cluster. `refs` maps a record's index to a valid person reference
    (`person_ref(n)`): against a target with no person reference its signature stays the same."""
    when = at or T0 + timedelta(hours=2)
    refs = dict(refs or {})
    keys = [SourceKey("crm", alike_key(first + i)) for i in range(n)]
    if one_target:
        held = person_payload(
            9000, given_name="Ysmena", family_name="Thrushcombe", birth_date=ONE_TARGET_BIRTH
        )
        land(hub, [row("hr", ONE_TARGET.key, "person", held, at=when, version=1)])
        arrive(hub)
        births = _one_digit_births(ONE_TARGET_BIRTH)
        for i, key in enumerate(keys):
            payload = {**_alike_payload(held, refs.get(i)), "birth_date": births[i % len(births)]}
            land(hub, [row("crm", key.key, "person", payload, at=when + timedelta(seconds=i + 1))])
            arrive(hub)
        return keys
    rows = [
        row(
            "crm",
            key.key,
            "person",
            _alike_payload(person(first + i), refs.get(i)),
            at=when + timedelta(seconds=i + 1),
        )
        for i, key in enumerate(keys)
    ]
    # records of persons 80 apart share names and a birth year: land them in separate runs, so none meets another
    for start in range(0, len(rows), 80):
        land(hub, rows[start : start + 80])
        arrive(hub)
    return keys


def decide_sample(
    hub: Hub,
    batch_id: str,
    *,
    actor: Actor,
    answers: Mapping[str, tuple[str, str | None]] | None = None,
    flush: bool = True,
) -> list[str]:
    """Decides each open sample review of the batch, one by one as a steward would: a link to its case's default,
    or the answer `answers` gives for its task, such as `("not_a_match", "birth_date")`, whose second part is the
    `split_on` code; each is flushed after its window with a fake clock before the next is staged (two decisions
    staged against one golden record at once would meet each other's commit). With `flush=False` every
    decision is staged and none flushed. Returns the task IDs decided."""
    answers = dict(answers or {})
    decided: list[str] = []
    while True:
        waiting = [
            i
            for i in hub.store.batch_items(batch_id, ("sample",), ("open",), None, 1000)
            if i.task_id not in decided
        ]
        if not waiting:
            return decided
        item = waiting[0]
        decision, split_on = answers.get(item.task_id, ("link", None))
        hub.tray.stage(item.task_id, decision, actor=actor, split_on=split_on, **seen(hub, item.task_id))
        decided.append(item.task_id)
        if flush:
            flush_past_window(hub)


def flush_past_window(hub: Hub, seconds: float | None = None) -> Any:
    """Moves the tray's (and the batches') clock past every staged decision's deadline, then flushes once."""
    offset = timedelta(seconds=seconds if seconds is not None else hub.settings.undo_seconds + 1)
    current = hub.tray.clock
    hub.tray.clock = lambda: current() + offset
    return hub.tray.flush()
