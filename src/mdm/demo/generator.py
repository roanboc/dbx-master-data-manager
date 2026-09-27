"""The demo generator: a deterministic world of source records with its truth (owner: CLI, B.14).

`random.Random(seed)` only; no clock; `occurred_at = start + deterministic
offsets`; `event_id = f"{system}-{seed}-{n:08d}"`. Organisations (finance,
versioned; crm), persons (hr, versioned; student_records; crm), then updates,
then deletes. E-mail addresses only at example.org; phone numbers under the
reserved calling code 999.

Every record's creation depends only on the seed and the two counts: updates
and deletes draw from random streams of their own, so a world generated again
with more updates lands the same creations (the landing table ignores them by
event ID) followed by the new events.

Organisations: finance (p 0.9, versioned, keys `F000123`) and crm (p 0.7, keys
`C000123`), at least one; a registered ID of the scheme ORG_REG, 8 digits and
two mod-97 check digits, always in finance and with p 0.7 in crm, 5% of those
mistyped so the checksum fails; crm writes the name differently (Ltd and
Limited, & and "and", case, a typo with p 0.1); phones as `+999 …`, `0…` or
`00999…`; websites under `.example`; 10% name a parent (another organisation's
finance key).

Persons: hr (p 0.5, keys `H123456`, ISO dates, versioned), student_records
(p 0.6, keys `S1234567`, `DD/MM/YYYY`, 5% of birth dates known only by year and
written as `01/01/<year>`), crm (p 0.5, keys `C…`, not versioned), at least
one; e-mail `<given>.<family><n>@example.org`; PERSON_REF 9 digits and a Luhn
digit (hr always, the others p 0.4); an employer (a finance organisation key)
for 60%, in hr and crm; name typos with p 0.08; e-mail missing with p 0.3,
phone with p 0.4; a second crm key for the same person with p 0.02; 1% twins,
two true persons sharing family name, address and the placeholder birth date
1900-01-01, with different given names and different valid PERSON_REFs.

crm numbers its keys once for both entities, so a crm key names one record
whatever its entity (the vault's subject key `src:<system>:<key>` carries no
entity).

Hard cases for a steward (`hard_cases`, a share; 0 generates exactly the world
generated before they existed), from a random stream of their own and with keys
allocated after every regular key, so the regular rows never change:
Organisation namesakes (creations, after every person: a second finance
organisation with the name and city of one whose crm record, if any, carries its
registered ID and a postcode; another postcode, a new registered ID, no website
or phone), Organisation close calls (later events, after the deletes: a crm
record with a namesake pair's name in other capitals and punctuation, the same
city, a third postcode, and no registered ID, phone or website, which scores in
the review band against both), and Person reviews (later events: a crm record
with a person's names, the birth date one digit different in the same year, and
no postcode, e-mail, phone or person reference).
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from mdm.demo import names
from mdm.engine.identifiers import luhn_digit, mod97_digits
from mdm.models.records import LandingRow, SourceKey

PERSON = "person"
ORGANISATION = "organisation"
EMAIL_DOMAIN = "example.org"
CALLING_CODE = "999"
#: the invented country codes of models/codelists/country.yaml, and one code outside the list
COUNTRIES = ("XA", "XB", "XC", "XD")
UNLISTED_COUNTRY = "XZ"
#: the date every source uses for an unknown birth date (a placeholder in the person model)
UNKNOWN_BIRTH_DATE = date(1900, 1, 1)
PERSON_SYSTEMS = (("hr", 0.5), ("student_records", 0.6), ("crm", 0.5))
#: each system's key letter and the digits of its number
_KEY_FORMS = {"finance": ("F", 6), "crm": ("C", 6), "hr": ("H", 6), "student_records": ("S", 7)}
_VERSIONED = frozenset({"finance", "hr"})
_UPDATES_AFTER = timedelta(days=30)
_DELETES_AFTER = timedelta(days=45)
_POSTCODE_LETTERS = "ABDEFGHJLNPQRSTUWXYZ"
_UPDATE_STREAM = 1
_DELETE_STREAM = 2
_HARD_STREAM = 3
_HARD_AFTER = timedelta(days=60)


@dataclass(frozen=True, slots=True)
class DemoConfig:
    persons: int = 2000
    organisations: int = 500
    seed: int = 7
    updates: float = 0.1
    deletes: float = 0.01
    initial_load: bool = False
    start: datetime = datetime(2026, 1, 5, tzinfo=UTC)
    #: the share of records given an invented hard case for a steward (namesakes, close calls, reviews);
    #: 0 generates exactly the world generated before hard cases existed
    hard_cases: float = 0.0
    #: creations only, no later event: land these first and the full world later, so updates and deletes
    #: reach records already settled (held and orphan tasks)
    creations_only: bool = False

    def __post_init__(self) -> None:
        if self.persons < 0 or self.organisations < 0:
            raise ValueError("counts must not be negative")
        if not (0.0 <= self.updates <= 1.0 and 0.0 <= self.deletes <= 1.0):
            raise ValueError("updates and deletes are shares between 0 and 1")
        if not 0.0 <= self.hard_cases <= 1.0:
            raise ValueError("hard cases are a share between 0 and 1")


@dataclass(frozen=True, slots=True)
class DemoWorld:
    rows: tuple[LandingRow, ...]
    truth: Mapping[tuple[str, SourceKey], str]  # (entity, source) -> true entity key; never landed

    def sources(self, entity: str) -> list[SourceKey]:
        """Every source record of `entity`, in (system, key) order."""
        return sorted(source for kind, source in self.truth if kind == entity)

    def deleted(self) -> frozenset[tuple[str, SourceKey]]:
        """The source records whose last event is a delete."""
        last: dict[tuple[str, SourceKey], str] = {}
        for row in self.rows:
            last[(row.entity, SourceKey(row.source_system, row.source_key))] = row.op
        return frozenset(record for record, op in last.items() if op == "delete")

    def counts(self) -> dict[str, int]:
        """Rows per `<entity>.<system>.<op>`, for reports."""
        return dict(sorted(Counter(f"{r.entity}.{r.source_system}.{r.op}" for r in self.rows).items()))


@dataclass(slots=True)
class _Record:
    """One source record's current state while the world is generated."""

    entity: str
    system: str
    key: str
    payload: dict[str, Any]
    version: int | None  # None: an unversioned source


@dataclass(frozen=True, slots=True)
class _Address:
    line1: str
    city: str
    postcode: str
    country: str

    def group(self, kind: str) -> list[dict[str, Any]]:
        """The repeating group `addresses` with this address as its one entry."""
        return [
            {
                "kind": kind,
                "line1": self.line1,
                "city": self.city,
                "postcode": self.postcode,
                "country": self.country,
            }
        ]


@dataclass(slots=True)
class _Org:
    truth: str
    words: str  # stem and sector, or two stems joined by "&"
    legal: str
    registered_id: str
    phone: str  # national number, 9 digits
    site: str  # the website's domain
    address: _Address
    finance: str | None = None  # the finance key
    crm: str | None = None  # the crm key
    parent: str | None = None  # another organisation's finance key


@dataclass(slots=True)
class _Person:
    truth: str
    given: str
    family: str
    birth: date
    email: str
    phone: str  # national number, 9 digits
    person_ref: str
    address: _Address
    employer: str | None = None  # a finance organisation key
    systems: tuple[str, ...] = field(default_factory=tuple)


class _World:
    """The generator's state: the random stream of the creations, the counters and the rows so far."""

    def __init__(self, config: DemoConfig) -> None:
        self.config = config
        self.rng = random.Random(config.seed)
        self.rows: list[LandingRow] = []
        self.truth: dict[tuple[str, SourceKey], str] = {}
        self.records: list[_Record] = []
        self._events = 0
        self._keys: Counter[str] = Counter()
        self._used: dict[str, set[str]] = {}

    def key(self, system: str) -> str:
        """The system's next key: `F000001`, `C000001`, `H000001`, `S0000001`."""
        self._keys[system] += 1
        letter, digits = _KEY_FORMS[system]
        return f"{letter}{self._keys[system]:0{digits}d}"

    def unique(self, kind: str, make: Callable[[], str]) -> str:
        """A value of `kind` that `make()` returns and no earlier call for that kind returned."""
        used = self._used.setdefault(kind, set())
        for _ in range(10_000):
            value = make()
            if value not in used:
                used.add(value)
                return value
        raise RuntimeError(f"no unique {kind} left")

    def emit(self, record: _Record, op: str, occurred_at: datetime, *, initial: bool) -> None:
        self._events += 1
        self.rows.append(
            LandingRow(
                event_id=f"{record.system}-{self.config.seed}-{self._events:08d}",
                source_system=record.system,
                source_key=record.key,
                entity=record.entity,
                op=op,
                occurred_at=occurred_at,
                payload=dict(record.payload),
                source_version=record.version,
                initial_load=initial,
            )
        )

    def create(self, entity: str, system: str, key: str, truth: str, payload: dict[str, Any]) -> None:
        """A new source record and the row that lands it."""
        record = _Record(entity, system, key, _drop_empty(payload), 1 if system in _VERSIONED else None)
        self.records.append(record)
        self.truth[(entity, SourceKey(system, key))] = truth
        occurred = self.config.start + timedelta(seconds=self._events + 1)
        self.emit(record, "upsert", occurred, initial=self.config.initial_load)

    def address(self, rng: random.Random) -> _Address:
        """An invented address; the postcode's first three characters name the town's area."""
        town = names.city(rng)
        letters = len(_POSTCODE_LETTERS)
        area = sum(ord(c) for c in town) % (letters * letters)
        postcode = (
            f"{_POSTCODE_LETTERS[area // letters]}{_POSTCODE_LETTERS[area % letters]}{rng.randrange(1, 10)} "
            f"{rng.randrange(1, 10)}{rng.choice(_POSTCODE_LETTERS)}{rng.choice(_POSTCODE_LETTERS)}"
        )
        country = UNLISTED_COUNTRY if rng.random() < 0.01 else names.zipf_choice(rng, COUNTRIES)
        return _Address(f"{rng.randrange(1, 200)} {names.street(rng)}", town, postcode, country)


def phone_text(rng: random.Random, national: str) -> str:
    """The same number written as `+999 …`, `0…` or `00999…`."""
    style = rng.randrange(3)
    if style == 0:
        return f"+{CALLING_CODE} {national[:3]} {national[3:6]} {national[6:]}"
    if style == 1:
        return f"0{national[:4]} {national[4:]}"
    return f"00{CALLING_CODE}{national}"


def _drop_empty(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if v is not None}


def _mistype(rng: random.Random, value: str) -> str:
    """One digit changed: a mod-97 or Luhn check catches every single-digit error."""
    at = rng.randrange(len(value))
    digit = rng.choice([d for d in "0123456789" if d != value[at]])
    return value[:at] + digit + value[at + 1 :]


# ---------------------------------------------------------------------------------------------- organisations


def _organisations(world: _World) -> list[_Org]:
    rng = world.rng
    orgs: list[_Org] = []
    for index in range(world.config.organisations):
        words, legal = names.organisation_name(rng)
        base = world.unique("org_reg", lambda: str(rng.randrange(10**7, 10**8)))
        site = _site(world, names.slug(words))
        in_finance = rng.random() < 0.9
        in_crm = rng.random() < 0.7
        if not (in_finance or in_crm):
            in_finance = rng.random() < 0.5
            in_crm = not in_finance
        orgs.append(
            _Org(
                truth=f"O{index + 1:07d}",
                words=words,
                legal=legal,
                registered_id=base + mod97_digits(base),
                phone=world.unique("phone", lambda: f"{rng.randrange(2, 6)}{rng.randrange(10**7, 10**8)}"),
                site=f"{site}.example",
                address=world.address(rng),
                finance=world.key("finance") if in_finance else None,
                crm=world.key("crm") if in_crm else None,
            )
        )
    known = [org for org in orgs if org.finance]
    for org in orgs:
        if org.finance and len(known) > 1 and rng.random() < 0.1:
            parent = rng.choice(known)
            if parent is not org:
                org.parent = parent.finance
    return orgs


def _site(world: _World, stem: str) -> str:
    """A website stem no other organisation has: the name's slug, numbered when it is taken."""
    tries = iter(range(10_000))
    return world.unique("site", lambda: stem if next(tries) == 0 else f"{stem}{world.rng.randrange(2, 1000)}")


def _crm_name(rng: random.Random, org: _Org) -> str:
    """The name as crm writes it: legal form long or short, "&" as "and", a typo, upper or lower case."""
    words = org.words
    legal = names.long_legal_form(org.legal) if rng.random() < 0.5 else org.legal
    if "&" in words and rng.random() < 0.6:
        words = words.replace("&", "and")
    if rng.random() < 0.1:
        first, _, rest = words.partition(" ")
        words = f"{names.typo(rng, first)} {rest}".strip()
    name = f"{words} {legal}"
    case = rng.random()
    if case < 0.15:
        return name.upper()
    if case < 0.25:
        return name.lower()
    return name


def _emit_organisations(world: _World, orgs: list[_Org]) -> None:
    rng = world.rng
    for org in orgs:
        email = f"contact.{org.site.removesuffix('.example')}@{EMAIL_DOMAIN}"
        if org.finance:
            world.create(
                ORGANISATION,
                "finance",
                org.finance,
                org.truth,
                {
                    "name": f"{org.words} {org.legal}",
                    "registered_id": org.registered_id,
                    "phone": phone_text(rng, org.phone),
                    "email": email,
                    "website": f"https://www.{org.site}" if rng.random() < 0.5 else org.site,
                    "postcode": org.address.postcode,
                    "city": org.address.city,
                    "country": org.address.country,
                    "addresses": org.address.group("registered"),
                    "parent": org.parent,
                },
            )
        if org.crm:
            registered = None
            if rng.random() < 0.7:
                registered = _mistype(rng, org.registered_id) if rng.random() < 0.05 else org.registered_id
            world.create(
                ORGANISATION,
                "crm",
                org.crm,
                org.truth,
                {
                    "name": _crm_name(rng, org),
                    "registered_id": registered,
                    "phone": phone_text(rng, org.phone),
                    "email": email if rng.random() < 0.7 else None,
                    "website": f"http://{org.site}/" if rng.random() < 0.6 else None,
                    "postcode": org.address.postcode if rng.random() < 0.9 else None,
                    "city": org.address.city,
                    "country": org.address.country,
                    "addresses": org.address.group("registered"),
                },
            )


# ---------------------------------------------------------------------------------------------- persons


def _email(world: _World, given: str, family: str) -> str:
    local = f"{names.slug(given)}.{names.slug(family)}"
    return world.unique("email", lambda: f"{local}{world.rng.randrange(1, 10000)}@{EMAIL_DOMAIN}")


def _new_person(world: _World, index: int, employers: list[str]) -> _Person:
    rng = world.rng
    given = names.given_name(rng)
    family = names.family_name(rng)
    birth = date(1940, 1, 1) + timedelta(days=rng.randrange(0, 67 * 365))
    ref_base = world.unique("person_ref", lambda: str(rng.randrange(10**8, 10**9)))
    person = _Person(
        truth=f"P{index + 1:07d}",
        given=given,
        family=family,
        birth=birth,
        email=_email(world, given, family),
        phone=world.unique("phone", lambda: f"7{rng.randrange(10**7, 10**8)}"),
        person_ref=ref_base + luhn_digit(ref_base),
        address=world.address(rng),
    )
    if employers and rng.random() < 0.6:
        person.employer = rng.choice(employers)
    return person


def _twin(world: _World, index: int, of: _Person, employers: list[str]) -> _Person:
    """A second true person with the same family name and address, both born on the placeholder date."""
    twin = _new_person(world, index, employers)
    while twin.given == of.given:
        twin.given = names.given_name(world.rng)
    twin.family = of.family
    twin.email = _email(world, twin.given, twin.family)
    twin.address = of.address
    twin.birth = UNKNOWN_BIRTH_DATE
    of.birth = UNKNOWN_BIRTH_DATE
    return twin


def _persons(world: _World, employers: list[str]) -> list[_Person]:
    rng = world.rng
    persons: list[_Person] = []
    while len(persons) < world.config.persons:
        person = _new_person(world, len(persons), employers)
        persons.append(person)
        if len(persons) < world.config.persons and rng.random() < 0.01:
            persons.append(_twin(world, len(persons), person, employers))
    for person in persons:
        systems = tuple(system for system, share in PERSON_SYSTEMS if rng.random() < share)
        person.systems = systems or (rng.choice(PERSON_SYSTEMS)[0],)
    return persons


def _person_payload(rng: random.Random, person: _Person, system: str) -> dict[str, Any]:
    given, family = person.given, person.family
    if rng.random() < 0.08:
        if rng.random() < 0.5:
            given = names.typo(rng, given)
        else:
            family = names.typo(rng, family)
    birth = person.birth
    if system == "student_records":
        if birth != UNKNOWN_BIRTH_DATE and rng.random() < 0.05:
            birth_text = f"01/01/{birth.year:04d}"  # only the year is known
        else:
            birth_text = f"{birth.day:02d}/{birth.month:02d}/{birth.year:04d}"
    else:
        birth_text = birth.isoformat()
    return {
        "given_name": given,
        "family_name": family,
        "birth_date": birth_text,
        "email": person.email if rng.random() >= 0.3 else None,
        "phone": phone_text(rng, person.phone) if rng.random() >= 0.4 else None,
        "postcode": person.address.postcode,
        "city": person.address.city,
        "country": person.address.country,
        "person_ref": person.person_ref if system == "hr" or rng.random() < 0.4 else None,
        "addresses": person.address.group("home"),
        "employer": person.employer if system in ("hr", "crm") else None,
    }


def _emit_persons(world: _World, persons: list[_Person]) -> None:
    rng = world.rng
    for person in persons:
        for system in person.systems:
            world.create(
                PERSON, system, world.key(system), person.truth, _person_payload(rng, person, system)
            )
            if system == "crm" and rng.random() < 0.02:  # the same person keyed twice in crm
                world.create(
                    PERSON, "crm", world.key("crm"), person.truth, _person_payload(rng, person, system)
                )


# ---------------------------------------------------------------------------------------------- later events


def _changed_person(rng: random.Random, world: _World, payload: dict[str, Any], employers: list[str]) -> None:
    roll = rng.random()
    if "employer" in payload and roll < 0.2 and len(employers) > 1:
        payload["employer"] = rng.choice([e for e in employers if e != payload["employer"]])
    elif roll < 0.5:
        payload["phone"] = phone_text(rng, f"7{rng.randrange(10**7, 10**8)}")
    elif roll < 0.7:
        local = f"{names.slug(str(payload.get('given_name', 'a')))}.{names.slug(str(payload.get('family_name', 'b')))}"
        payload["email"] = f"{local}{rng.randrange(10000, 100000)}@{EMAIL_DOMAIN}"
    elif roll < 0.9:
        moved = world.address(rng)
        payload.update(
            postcode=moved.postcode, city=moved.city, country=moved.country, addresses=moved.group("home")
        )
    else:  # a new family name: a critical attribute
        payload["family_name"] = names.family_name(rng)


def _changed_organisation(rng: random.Random, world: _World, payload: dict[str, Any]) -> None:
    roll = rng.random()
    site = names.slug(str(payload.get("name", "office")))
    if roll < 0.4:
        payload["phone"] = phone_text(rng, f"{rng.randrange(2, 6)}{rng.randrange(10**7, 10**8)}")
    elif roll < 0.7:
        payload["email"] = f"office{rng.randrange(1, 100)}.{site}@{EMAIL_DOMAIN}"
    elif roll < 0.8:
        payload["website"] = f"{site}-{rng.randrange(1, 100)}.example"
    elif roll < 0.9:
        moved = world.address(rng)
        payload.update(
            postcode=moved.postcode,
            city=moved.city,
            country=moved.country,
            addresses=moved.group("registered"),
        )
    else:  # a new name: a critical attribute
        words, legal = names.organisation_name(rng)
        payload["name"] = f"{words} {legal}"


def _sample(rng: random.Random, population: int, share: float) -> Iterator[int]:
    """round(share * population) distinct indexes, in the stream's order."""
    count = min(population, round(share * population))
    return iter(rng.sample(range(population), count)) if count else iter(())


def _later_events(world: _World, employers: list[str], records: list[_Record]) -> None:
    """Updates, then deletes, of the regular `records` (never of a hard case)."""
    config = world.config
    updates = random.Random(config.seed * 1_000_003 + _UPDATE_STREAM)
    for n, index in enumerate(_sample(updates, len(records), config.updates)):
        record = records[index]
        payload = dict(record.payload)
        if record.entity == PERSON:
            _changed_person(updates, world, payload, employers)
        else:
            _changed_organisation(updates, world, payload)
        if payload == record.payload:
            continue
        record.payload = payload
        if record.version is not None:
            record.version += 1
        world.emit(record, "upsert", config.start + _UPDATES_AFTER + timedelta(seconds=30 * n), initial=False)
    deletes = random.Random(config.seed * 1_000_003 + _DELETE_STREAM)
    for n, index in enumerate(_sample(deletes, len(records), config.deletes)):
        record = records[index]
        record.payload = {}
        if record.version is not None:
            record.version += 1
        world.emit(record, "delete", config.start + _DELETES_AFTER + timedelta(seconds=30 * n), initial=False)


# ---------------------------------------------------------------------------------------------- the world


# ---------------------------------------------------------------------------------------------- hard cases


def _other_postcode(rng: random.Random, taken: set[str], like: str) -> str:
    """A postcode of the same area as `like` (its two letters) whose first three characters differ from every
    postcode in `taken`, so the postcode comparison reads "else", never "same prefix"."""
    prefixes = {code[:3] for code in taken}
    for _ in range(1_000):
        digit = rng.randrange(1, 10)
        code = (
            f"{like[:2]}{digit} {rng.randrange(1, 10)}{rng.choice(_POSTCODE_LETTERS)}"
            f"{rng.choice(_POSTCODE_LETTERS)}"
        )
        if code[:3] not in prefixes:
            return code
    raise RuntimeError("no postcode left in the area")


def _one_digit_off(day: date) -> date:
    """The date with one digit of its day changed, in the same month and year: the units digit up or down,
    else the tens digit."""
    tens, units = divmod(day.day, 10)
    for candidate in (
        tens * 10 + units + 1,
        tens * 10 + units - 1,
        (tens - 1) * 10 + units,
        (tens + 1) * 10 + units,
    ):
        if units == 9 and candidate == day.day + 1 or units == 0 and candidate == day.day - 1:
            continue  # 19 -> 20 or 20 -> 19 would change both digits
        try:
            return day.replace(day=candidate)
        except ValueError:
            continue
    raise ValueError("no date one digit off")  # pragma: no cover - every day has one


@dataclass(slots=True)
class _Namesake:
    original: _Org
    name: str
    postcodes: set[str]


def _namesakes(world: _World, orgs: list[_Org], rng: random.Random) -> list[_Namesake]:
    """For the share of organisations with a finance record whose crm record, when there is one, carries the
    registered ID and a postcode, a second finance organisation with the same name and city: another postcode,
    a new valid registered ID, no website or phone. The cannot-link rule keeps it a golden record of its own."""
    crm = {r.key: r.payload for r in world.records if r.entity == ORGANISATION and r.system == "crm"}
    eligible = [
        org
        for org in orgs
        if org.finance
        and (
            org.crm is None
            or (crm[org.crm].get("registered_id") == org.registered_id and crm[org.crm].get("postcode"))
        )
    ]
    chosen = _sample(rng, len(eligible), world.config.hard_cases)
    out: list[_Namesake] = []
    for number, index in enumerate(sorted(chosen)):
        org = eligible[index]
        name = f"{org.words} {org.legal}"
        postcode = _other_postcode(rng, {org.address.postcode}, org.address.postcode)
        base = world.unique("org_reg", lambda: str(rng.randrange(10**7, 10**8)))
        world.create(
            ORGANISATION,
            "finance",
            world.key("finance"),
            f"O{world.config.organisations + number + 1:07d}",
            {
                "name": name,
                "registered_id": base + mod97_digits(base),
                "postcode": postcode,
                "city": org.address.city,
                "country": org.address.country,
            },
        )
        out.append(_Namesake(org, name, {org.address.postcode, postcode}))
    return out


def _close_calls(world: _World, namesakes: list[_Namesake], rng: random.Random, at: datetime) -> datetime:
    """For each namesake pair, a new crm record with the name in other capitals and punctuation ("BRINDLE WORKS
    ltd." for "Brindle Works Ltd"), the same city, a third postcode, and no registered ID, phone or website."""
    for pair in namesakes:
        org = pair.original
        record = _Record(
            ORGANISATION,
            "crm",
            world.key("crm"),
            {
                "name": f"{org.words.upper()} {org.legal.lower()}.",
                "postcode": _other_postcode(rng, pair.postcodes, org.address.postcode),
                "city": org.address.city,
                "country": org.address.country,
            },
            None,
        )
        world.truth[(ORGANISATION, SourceKey("crm", record.key))] = org.truth
        at += timedelta(seconds=30)
        world.emit(record, "upsert", at, initial=False)
    return at


def _reviews(world: _World, persons: list[_Person], rng: random.Random, at: datetime) -> None:
    """For the share of persons with a real birth date, a new crm record with the names as held, the birth
    date one digit different in the same year, and no postcode, e-mail, phone or person reference."""
    eligible = [p for p in persons if p.birth != UNKNOWN_BIRTH_DATE]
    for index in sorted(_sample(rng, len(eligible), world.config.hard_cases)):
        person = eligible[index]
        record = _Record(
            PERSON,
            "crm",
            world.key("crm"),
            {
                "given_name": person.given,
                "family_name": person.family,
                "birth_date": _one_digit_off(person.birth).isoformat(),
                "city": person.address.city,
                "country": person.address.country,
            },
            None,
        )
        world.truth[(PERSON, SourceKey("crm", record.key))] = person.truth
        at += timedelta(seconds=30)
        world.emit(record, "upsert", at, initial=False)


# ---------------------------------------------------------------------------------------------- the world


def generate(config: DemoConfig) -> DemoWorld:
    """The world of `config`: landing rows in landing order (organisations, persons, hard-case namesakes,
    updates, deletes, hard-case close calls and reviews) and the truth, which is never landed."""
    world = _World(config)
    orgs = _organisations(world)
    _emit_organisations(world, orgs)
    employers = [org.finance for org in orgs if org.finance]
    persons = _persons(world, employers)
    _emit_persons(world, persons)
    regular = list(world.records)
    hard = random.Random(config.seed * 1_000_003 + _HARD_STREAM)
    namesakes = _namesakes(world, orgs, hard) if config.hard_cases > 0 else []
    if not config.creations_only:
        _later_events(world, employers, regular)
        if config.hard_cases > 0:
            at = _close_calls(world, namesakes, hard, config.start + _HARD_AFTER)
            _reviews(world, persons, hard, at)
    return DemoWorld(rows=tuple(world.rows), truth=dict(world.truth))
