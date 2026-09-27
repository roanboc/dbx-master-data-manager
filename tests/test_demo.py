"""The demo world: deterministic, invented, shaped like the sources it stands in for (owner: CLI, B.14).

When `MDM_PUBLIC_SAFE_TERMS` names the private denylist, no generated text may
contain one of its terms; the generated names live only in `.duckdb` files,
which the repository scan never reads, so this test is where they are checked.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from pathlib import Path

import pytest
import yaml
from scripts.scan_public_safe import ALLOWED, BUILT_IN

from mdm.backend.factory import open_store
from mdm.config import Settings
from mdm.demo import DemoConfig, DemoWorld, evaluate, generate, land, names
from mdm.demo.generator import COUNTRIES, EMAIL_DOMAIN, UNKNOWN_BIRTH_DATE, UNLISTED_COUNTRY
from mdm.engine.identifiers import luhn_valid, mod97_valid
from mdm.engine.standardise import standardise_record
from mdm.models.entity_model import EntityModel
from mdm.models.errors import PlatformRefused
from mdm.models.records import SourceChange, SourceKey

ROOT = Path(__file__).resolve().parents[1]
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+)")


@pytest.fixture(scope="module")
def world() -> DemoWorld:
    return generate(DemoConfig(persons=2000, organisations=500, seed=7))


def _texts(world: DemoWorld) -> list[str]:
    """Every text the world would land: keys, event IDs and every payload value, nested ones too."""
    out: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, dict):
            for key, item in value.items():
                out.append(str(key))
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    for row in world.rows:
        out.extend((row.event_id, row.source_system, row.source_key, row.entity, row.op))
        walk(row.payload)
    return out


def _change(row, seq: int) -> SourceChange:
    return SourceChange(
        event_id=row.event_id,
        source_system=row.source_system,
        source_key=row.source_key,
        entity=row.entity,
        op=row.op,
        occurred_at=row.occurred_at,
        source_version=row.source_version,
        initial_load=row.initial_load,
        payload=row.payload,
        landed_at=row.occurred_at,
        landing_seq=seq,
    )


# ---------------------------------------------------------------------------------------------- determinism


def test_same_seed_same_rows(world: DemoWorld) -> None:
    again = generate(DemoConfig(persons=2000, organisations=500, seed=7))
    assert again.rows == world.rows
    assert again.truth == world.truth


def test_another_seed_other_rows(world: DemoWorld) -> None:
    other = generate(DemoConfig(persons=2000, organisations=500, seed=8))
    assert other.rows != world.rows
    first = {r.payload.get("family_name") for r in world.rows[:2000]}
    assert {r.payload.get("family_name") for r in other.rows[:2000]} != first


def test_creations_do_not_depend_on_updates_or_deletes(world: DemoWorld) -> None:
    """A world generated again with more updates lands the same creations, then only new events."""
    base = generate(DemoConfig(persons=2000, organisations=500, seed=7, updates=0, deletes=0))
    more = generate(DemoConfig(persons=2000, organisations=500, seed=7, updates=0.3, deletes=0.05))
    assert more.rows[: len(base.rows)] == base.rows
    assert world.rows[: len(base.rows)] == base.rows
    assert len(more.rows) > len(world.rows) > len(base.rows)
    assert {r.op for r in base.rows} == {"upsert"}


def test_event_ids_unique_and_shaped(world: DemoWorld) -> None:
    ids = [r.event_id for r in world.rows]
    assert len(set(ids)) == len(ids)
    for n, row in enumerate(world.rows, start=1):
        assert row.event_id == f"{row.source_system}-7-{n:08d}"


def test_no_clock_occurred_at_from_the_start(world: DemoWorld) -> None:
    start = DemoConfig().start
    assert all(r.occurred_at > start for r in world.rows)
    creations = [r for r in world.rows if r.op == "upsert" and (r.source_version or 1) == 1]
    assert creations[0].occurred_at.isoformat() == "2026-01-05T00:00:01+00:00"


# ---------------------------------------------------------------------------------------------- the truth


def test_truth_covers_every_source_record(world: DemoWorld) -> None:
    landed = {(r.entity, SourceKey(r.source_system, r.source_key)) for r in world.rows}
    assert landed == set(world.truth)
    assert all(re.fullmatch(r"[PO]\d{7}", key) for key in world.truth.values())


def test_crm_keys_name_one_record_whatever_the_entity(world: DemoWorld) -> None:
    crm = [(entity, s.key) for entity, s in world.truth if s.system == "crm"]
    keys = [k for _, k in crm]
    assert len(keys) == len(set(keys))
    assert {entity for entity, _ in crm} == {"person", "organisation"}


def test_source_shares_and_key_shapes(world: DemoWorld) -> None:
    per_system = Counter((entity, s.system) for entity, s in world.truth)
    orgs, persons = 500, 2000
    assert 0.85 * orgs < per_system[("organisation", "finance")] < 0.97 * orgs
    assert 0.62 * orgs < per_system[("organisation", "crm")] < 0.8 * orgs
    assert 0.44 * persons < per_system[("person", "hr")] < 0.6 * persons
    assert 0.54 * persons < per_system[("person", "student_records")] < 0.7 * persons
    shapes = {"finance": r"F\d{6}", "crm": r"C\d{6}", "hr": r"H\d{6}", "student_records": r"S\d{7}"}
    for _, source in world.truth:
        assert re.fullmatch(shapes[source.system], source.key)
    true_persons = {key for (entity, _), key in world.truth.items() if entity == "person"}
    assert len(true_persons) == persons


def test_versioned_sources_carry_a_version_on_every_row(world: DemoWorld) -> None:
    for row in world.rows:
        if row.source_system in ("hr", "finance"):
            assert isinstance(row.source_version, int) and row.source_version >= 1
        else:
            assert row.source_version is None
    versions: dict[tuple[str, str], list[int]] = {}
    for row in world.rows:
        if row.source_version is not None:
            versions.setdefault((row.source_system, row.source_key), []).append(row.source_version)
    assert all(v == list(range(1, len(v) + 1)) for v in versions.values())


def test_updates_then_deletes_as_later_events(world: DemoWorld) -> None:
    records = len(world.truth)
    creations = records
    updates = world.rows[creations : len(world.rows) - round(0.01 * records)]
    deletes = world.rows[len(world.rows) - round(0.01 * records) :]
    assert all(r.op == "upsert" for r in updates) and all(r.op == "delete" for r in deletes)
    assert 0.08 * records < len(updates) <= 0.1 * records
    assert all(r.payload == {} for r in deletes)
    assert max(r.occurred_at for r in world.rows[:creations]) < min(r.occurred_at for r in updates)
    assert len(world.deleted()) == len(deletes)
    employer_changes = [r for r in updates if r.entity == "person" and "employer" in r.payload]
    assert employer_changes, "some updates change an employer"


# ---------------------------------------------------------------------------------------------- values


def test_every_email_at_example_org(world: DemoWorld) -> None:
    domains = Counter(m.group(1) for text in _texts(world) for m in _EMAIL.finditer(text))
    assert domains and set(domains) == {EMAIL_DOMAIN}


def test_websites_and_phones_are_reserved(world: DemoWorld) -> None:
    for row in world.rows:
        site = row.payload.get("website")
        if site:
            assert re.search(r"[a-z0-9-]+\.example(/|$)", site)
        phone = row.payload.get("phone")
        if phone:
            digits = re.sub(r"\D", "", phone)
            assert phone.startswith(("+999 ", "00999", "0")) and len(digits) in (10, 12, 14)


def test_registered_ids_and_person_refs(world: DemoWorld) -> None:
    finance = [r.payload["registered_id"] for r in world.rows if r.source_system == "finance" and r.payload]
    assert finance and all(mod97_valid(v) and len(v) == 10 for v in finance)
    crm_ids = [
        r.payload["registered_id"]
        for r in world.rows
        if r.entity == "organisation" and r.source_system == "crm" and "registered_id" in r.payload
    ]
    bad = sum(1 for v in crm_ids if not mod97_valid(v))
    assert 0 < bad < 0.12 * len(crm_ids)
    refs = [r.payload.get("person_ref") for r in world.rows if r.entity == "person" and r.payload]
    hr = [r for r in world.rows if r.source_system == "hr" and r.op == "upsert"]
    assert all("person_ref" in r.payload for r in hr)
    assert all(luhn_valid(v) and len(v) == 10 for v in refs if v)


def test_organisation_names_vary_across_sources(world: DemoWorld) -> None:
    by_org: dict[str, dict[str, str]] = {}
    for (entity, source), true_key in world.truth.items():
        if entity == "organisation":
            by_org.setdefault(true_key, {})[source.system] = source.key
    names_of = {
        (r.source_system, r.source_key): r.payload["name"]
        for r in world.rows
        if r.entity == "organisation" and r.op == "upsert" and (r.source_version or 1) == 1
    }
    both = [
        (names_of[("finance", s["finance"])], names_of[("crm", s["crm"])])
        for s in by_org.values()
        if len(s) == 2
    ]
    assert sum(1 for f, c in both if f != c) > 0.3 * len(both)
    assert any("Limited" in c for _, c in both) and any(" and " in c for _, c in both)
    parents = [
        r.payload["parent"] for r in world.rows if r.entity == "organisation" and "parent" in r.payload
    ]
    assert parents and all(re.fullmatch(r"F\d{6}", p) for p in parents)


def test_student_records_write_day_first_and_year_only_dates(world: DemoWorld) -> None:
    dates = [
        r.payload["birth_date"] for r in world.rows if r.source_system == "student_records" and r.payload
    ]
    assert all(re.fullmatch(r"\d{2}/\d{2}/\d{4}", d) for d in dates)
    first_of_january = sum(1 for d in dates if d.startswith("01/01/") and not d.endswith("/1900"))
    assert 0.02 * len(dates) < first_of_january < 0.09 * len(dates)
    iso = [
        r.payload["birth_date"]
        for r in world.rows
        if r.source_system in ("hr", "crm") and "birth_date" in r.payload
    ]
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) for d in iso)


def test_twins_share_family_address_and_placeholder_birth_date(world: DemoWorld) -> None:
    by_person: dict[str, list[dict]] = {}
    for row in world.rows:
        if row.entity == "person" and row.op == "upsert" and (row.source_version or 1) == 1:
            by_person.setdefault(
                world.truth[("person", SourceKey(row.source_system, row.source_key))], []
            ).append(row.payload)
    placeholder = {UNKNOWN_BIRTH_DATE.isoformat(), UNKNOWN_BIRTH_DATE.strftime("%d/%m/%Y")}
    twins = [k for k, rows in by_person.items() if all(p["birth_date"] in placeholder for p in rows)]
    assert 10 <= len(twins) <= 60, "about 1% of persons, two of each pair"
    homes = Counter((by_person[k][0]["addresses"][0]["line1"], by_person[k][0]["postcode"]) for k in twins)
    assert any(n >= 2 for n in homes.values())


def test_missing_values_and_typos_at_their_rates(world: DemoWorld) -> None:
    persons = [r.payload for r in world.rows if r.entity == "person" and r.op == "upsert"]
    assert 0.22 < sum(1 for p in persons if "email" not in p) / len(persons) < 0.38
    assert 0.32 < sum(1 for p in persons if "phone" not in p) / len(persons) < 0.48
    assert 0.3 < sum(1 for p in persons if "employer" in p) / len(persons) < 0.6
    countries = Counter(r.payload.get("country") for r in world.rows if r.payload)
    assert set(countries) <= {*COUNTRIES, UNLISTED_COUNTRY}
    assert 0 < countries[UNLISTED_COUNTRY] < 0.03 * sum(countries.values())


def test_names_follow_a_zipf_shape() -> None:
    import random

    rng = random.Random(11)
    counts = sorted(Counter(names.family_name(rng) for _ in range(40_000)).values(), reverse=True)
    # rank-frequency: f(1) / f(10) is about 10**1.1 (12.6), f(10) / f(100) likewise
    assert 9 < counts[0] / counts[9] < 17
    assert 8 < counts[9] / counts[99] < 18
    assert all(a >= b for a, b in zip(counts[:5], counts[1:6], strict=True))
    assert counts[0] / 40_000 > 0.1, "the most common family name forms a large block"


def test_zipf_choice_is_deterministic() -> None:
    import random

    items = list(range(50))
    first = [names.zipf_choice(random.Random(3), items) for _ in range(5)]
    again = [names.zipf_choice(random.Random(3), items) for _ in range(5)]
    assert first == again
    with pytest.raises(ValueError):
        names.zipf_choice(random.Random(3), [])


def test_typo_changes_one_thing_and_keeps_the_first_letter() -> None:
    import random

    rng = random.Random(5)
    for word in ("Ingov", "Briette", "Daskling", "Holdings"):
        changed = names.typo(rng, word)
        assert changed != word and changed[0] == word[0] and abs(len(changed) - len(word)) <= 1
    assert names.typo(rng, "Al") == "Al"
    assert names.slug("Quorix & Velane Ltd") == "quorix-velane-ltd"


def test_every_row_standardises_with_the_starter_models(world: DemoWorld) -> None:
    models = {}
    for name in ("person", "organisation"):
        with (ROOT / "models" / f"{name}.yaml").open(encoding="utf-8") as handle:
            models[name] = EntityModel.from_dict(yaml.safe_load(handle))
    placeholders = 0
    for seq, row in enumerate(world.rows, start=1):
        if row.op != "upsert":
            continue
        model = models[row.entity]
        record = standardise_record(model, model.source(row.source_system), _change(row, seq))
        assert record.invalid == (), (row.entity, row.source_system, record.invalid)
        placeholders += "birth_date" in record.placeholders
        if row.entity == "person":
            assert record.values["phone"].startswith("+999") if "phone" in row.payload else True
            if row.payload.get("employer"):
                assert record.references == {"employer": row.payload["employer"]}
    assert placeholders > 0


# ---------------------------------------------------------------------------------------------- public safety


def test_generated_text_passes_the_public_safety_scan(world: DemoWorld) -> None:
    """The built-in patterns (with the scan's allowed literals), and the private terms when named."""
    texts = _texts(world)
    hits = []
    for label, pattern in BUILT_IN.items():
        rule = re.compile(pattern)
        for text in texts:
            for found in rule.finditer(text):
                if not any(allowed in found.group(0).lower() for allowed in ALLOWED):
                    hits.append(label)
    assert hits == []
    terms_file = os.environ.get("MDM_PUBLIC_SAFE_TERMS")
    if not terms_file:
        pytest.skip("MDM_PUBLIC_SAFE_TERMS names the private denylist; the built-in patterns passed")
    terms = [
        line.strip()
        for line in Path(terms_file).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    rule = re.compile("|".join(re.escape(t) for t in terms), re.IGNORECASE)
    vocabulary = [
        *names.GIVEN_NAMES,
        *names.FAMILY_NAMES,
        *names.ORG_STEMS,
        *names.CITIES,
        *names.STREETS,
        *names.SECTORS,
    ]
    # count only: the test output never prints a term or the text it was found in
    assert sum(1 for text in texts if rule.search(text)) == 0
    assert sum(1 for word in vocabulary if rule.search(word)) == 0


# ---------------------------------------------------------------------------------------------- landing and evaluation


def test_lander_writes_once_and_refuses_a_shared_store(on_platform: str) -> None:
    small = generate(DemoConfig(persons=50, organisations=20, seed=3))
    local = Settings(duckdb_path=":memory:")
    store = open_store(local)
    try:
        store.init_schema(create_landing=True)
        assert land(store, local, small, batch=40) == len(small.rows)
        assert land(store, local, small) == 0, "a redelivered event is ignored"
        assert store.landing_max_seq() >= len(small.rows)
        shared = Settings.from_env({"DATABRICKS_APP_PORT": "8000", "MDM_DUCKDB_PATH": ":memory:"})
        assert shared.shared_store
        with pytest.raises(PlatformRefused):
            land(store, shared, small)
        with pytest.raises(PlatformRefused):
            land(store, local.with_(lakebase_endpoint="projects/p/branches/b/endpoints/e"), small)
        scores = evaluate(store, "person", small)
        assert scores["pairs"] == 0 and scores["recall"] == 0.0 and scores["linked"] == 0
        assert scores["records"] == len(
            [s for s in small.sources("person") if ("person", s) not in small.deleted()]
        )
    finally:
        store.close()
