"""Standardisation: display values, match forms, placeholders and the sample hash (B.9.1)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime

import pytest

from mdm.engine.blocking import blocking_keys
from mdm.engine.standardise import (
    clean_text,
    code,
    email,
    fold,
    is_placeholder,
    iso_date,
    org_name,
    phone_e164,
    postcode,
    sample_hash,
    standardise_record,
    url_domain,
)
from mdm.models.entity_model import EntityModel, PlaceholderSpec
from mdm.models.records import SourceKey
from tests.test_engine_support import change, org_reg, person_ref, std

# ------------------------------------------------------------------------------------------------ text


def test_clean_text() -> None:
    assert clean_text(None) is None
    assert clean_text("") is None
    assert clean_text("   \t ") is None
    assert clean_text("  Ann \t  Brisk\n") == "Ann Brisk"
    assert clean_text("Ａｎｎ") == "Ann"  # NFKC folds full-width letters
    assert clean_text("A\u0000n​n") == "Ann"  # control and format characters dropped
    assert clean_text(42) == "42"
    assert clean_text({"a": 1}) is None


def test_fold() -> None:
    assert fold("Élodie-Ånné") == "elodie anne"
    assert fold("STRAẞE") == "strasse"
    assert fold("Søren  O'Dell") == "soren o dell"
    assert fold("  a,b;c  ") == "a b c"
    assert fold("Łukasz Æther") == "lukasz aether"


def test_org_name_legal_forms_and_stop_words() -> None:
    a = org_name("Brindle & Sons Holdings Ltd.")
    b = org_name("brindle and sons holdings limited")
    assert a is not None and b is not None
    assert a.core == b.core == "brindle sons holdings"
    assert a.legal == b.legal == "ltd"
    assert a.first == "brindle"
    assert a.tokens == ("brindle", "sons", "holdings")
    c = org_name("The Quarrow Works B.V.")
    assert c is not None and c.core == "quarrow works" and c.legal == "bv"
    d = org_name("Veltmar Pty Ltd")
    assert d is not None and d.core == "veltmar" and d.legal == "pty ltd"


def test_org_name_keeps_a_name_that_is_only_a_legal_form_or_a_stop_word() -> None:
    only = org_name("Company")
    assert only is not None and only.core == "company" and only.legal == ""
    the = org_name("The")
    assert the is not None and the.core == "the"
    assert org_name("  ") is None
    assert org_name("...") is None


# ------------------------------------------------------------------------------------------------ contact data


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+999 1234 5678", "+99912345678"),
        ("00999 1234 5678", "+99912345678"),
        ("01234 5678", "+99912345678"),
        ("1234 5678", "+99912345678"),
        ("(0123) 456-78", "+99912345678"),
        ("+999 (0) 1234 5678", "+99912345678"),
        (99912345678, "+99999912345678"),
        ("+999 12", None),  # five digits
        ("+999 1234 5678 9012 34", None),  # seventeen digits
        ("", None),
        (None, None),
    ],
)
def test_phone_e164(raw: object, expected: str | None) -> None:
    assert phone_e164(raw, "999") == expected


def test_phone_without_a_calling_code_needs_an_international_number() -> None:
    assert phone_e164("+999 1234 5678", "") == "+99912345678"
    assert phone_e164("00999 1234 5678", "") == "+99912345678"
    assert phone_e164("01234 5678", "") is None
    assert phone_e164("1234 5678", "") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Ann.Brisk@Example.ORG ", "ann.brisk@example.org"),
        ("ann@example", None),
        ("ann brisk@example.org", None),
        ("ann@@example.org", None),
        ("@example.org", None),
        ("ann@.example.org", None),
        ("ann@mail..example.org", None),
        ("ann@example.org.", None),
        (None, None),
    ],
)
def test_email(raw: object, expected: str | None) -> None:
    assert email(raw) == expected


def test_postcode_url_and_code() -> None:
    assert postcode(" ab1-2 cd ") == "AB12CD"
    assert postcode("   ") is None
    assert url_domain("https://www.Quarrow.example/about?x=1") == "quarrow.example"
    assert url_domain("quarrow.example:8080/path") == "quarrow.example"
    assert url_domain("WWW.quarrow.example") == "quarrow.example"
    assert url_domain(None) is None
    assert code(" xa ") == "XA"
    assert code("") is None


# ------------------------------------------------------------------------------------------------ dates


@pytest.mark.parametrize(
    ("raw", "order", "expected"),
    [
        ("1990-05-17", "ymd", date(1990, 5, 17)),
        ("1990-5-7", "ymd", date(1990, 5, 7)),
        ("1990-05-17T10:00:00Z", "ymd", date(1990, 5, 17)),
        ("19900517", "ymd", date(1990, 5, 17)),
        (19900517, "ymd", date(1990, 5, 17)),
        ("1990/05/17", "ymd", date(1990, 5, 17)),
        ("17/05/1990", "dmy", date(1990, 5, 17)),
        ("05/17/1990", "mdy", date(1990, 5, 17)),
        ("03/04/1990", "dmy", date(1990, 4, 3)),
        ("03/04/1990", "mdy", date(1990, 3, 4)),
        ("17-05-1990", "dmy", date(1990, 5, 17)),
        ("17.05.1990", "mdy", date(1990, 5, 17)),  # dotted is always day first
        ("1990-02-30", "ymd", None),
        ("31/04/1990", "dmy", None),
        ("yesterday", "ymd", None),
        (True, "ymd", None),
        (None, "ymd", None),
    ],
)
def test_iso_date(raw: object, order: str, expected: date | None) -> None:
    assert iso_date(raw, order) == expected


def test_iso_date_from_date_and_datetime() -> None:
    assert iso_date(date(2001, 2, 3), "dmy") == date(2001, 2, 3)
    assert iso_date(datetime(2001, 2, 3, 4, 5, tzinfo=UTC), "dmy") == date(2001, 2, 3)


def test_is_placeholder_per_source() -> None:
    specs = (
        PlaceholderSpec(value="1900-01-01"),
        PlaceholderSpec(pattern="*-01-01", sources=("student_records",)),
    )
    assert is_placeholder(date(1900, 1, 1), specs, "crm")
    assert is_placeholder(date(1987, 1, 1), specs, "student_records")
    assert not is_placeholder(date(1987, 1, 1), specs, "crm")
    assert not is_placeholder(date(1987, 1, 2), specs, "student_records")
    assert is_placeholder(date(2000, 7, 1), (PlaceholderSpec(pattern="2000-*-01"),), "hr")


# ------------------------------------------------------------------------------------------------ one record


def _payload() -> dict[str, object]:
    return {
        "given_name": "  Élodie ",
        "family_name": "O'Varrow",
        "birth_date": "1990-05-17",
        "email": " Elodie.Ovarrow7@Example.org ",
        "phone": "01234 5678",
        "postcode": "ab1 2cd",
        "city": " Tarnwick ",
        "country": "xa",
        "person_ref": " " + person_ref("31415926") + " ",
        "addresses": [
            {"kind": "home", "line1": " 1  Kestrel Row ", "extra": "dropped"},
            {"kind": None},
            "junk",
        ],
        "employer": " F000123 ",
        "master_id": "PER-000001",
        "unknown_attribute": "ignored",
    }


def test_standardise_person_record(person_model: EntityModel) -> None:
    record = std(person_model, "hr", "H000001", _payload())
    assert record.entity == "person"
    assert record.source == SourceKey("hr", "H000001")
    ref = person_ref("31415926")
    assert record.values == {
        "given_name": "Élodie",
        "family_name": "O'Varrow",
        "birth_date": date(1990, 5, 17),
        "email": "elodie.ovarrow7@example.org",
        "phone": "+99912345678",
        "postcode": "ab1 2cd",
        "city": "Tarnwick",
        "country": "XA",
        "person_ref": ref,
        "addresses": [
            {"kind": "home", "line1": "1 Kestrel Row", "city": None, "postcode": None, "country": None}
        ],
    }
    match = record.match
    assert match["given_name"] == "elodie"
    assert match["family_name"] == "o varrow"
    assert match["family_name.metaphone"] and match["family_name.nysiis"]
    assert match["birth_date"] == "1990-05-17" and match["birth_date.year"] == "1990"
    assert match["email.local"] == "elodie.ovarrow7" and match["email.domain"] == "example.org"
    assert match["phone.last7"] == "2345678"
    assert match["postcode"] == "AB12CD" and match["postcode.prefix"] == "AB1"
    assert match["city"] == "tarnwick"
    assert match["person_ref"] == ref and match["person_ref.valid"] is True
    assert "employer" not in record.values and "employer" not in match
    assert "addresses" not in match
    assert record.references == {"employer": "F000123"}
    assert [(i.scheme, i.value, i.valid) for i in record.ids] == [("PERSON_REF", ref, True)]
    assert record.placeholders == ()
    assert record.invalid == ("addresses",)  # the entry that is not a mapping
    assert record.keys == blocking_keys(person_model.match, record)
    assert record.sample_hash == sample_hash("person", SourceKey("hr", "H000001"))


def test_phonetic_forms_ignore_spaces_and_punctuation(person_model: EntityModel) -> None:
    a = std(person_model, "hr", "H1", {"family_name": "O'Varrow"})
    b = std(person_model, "crm", "C1", {"family_name": "Ovarrow"})
    c = std(person_model, "crm", "C2", {"family_name": "O Varrow"})
    assert (
        a.match["family_name.metaphone"]
        == b.match["family_name.metaphone"]
        == c.match["family_name.metaphone"]
    )
    assert a.match["family_name.nysiis"] == b.match["family_name.nysiis"]


def test_match_forms_survive_a_json_round_trip(person_model: EntityModel, org_model: EntityModel) -> None:
    """Forms are stored as JSON and read back; a form must equal its stored copy."""
    person = std(person_model, "hr", "H1", _payload())
    org = std(
        org_model,
        "crm",
        "C1",
        {"name": "Brindle & Sons Ltd", "registered_id": org_reg("12345678"), "website": "brindle.example"},
    )
    for record in (person, org):
        assert json.loads(json.dumps(dict(record.match))) == dict(record.match)


def test_placeholder_date_is_absent_and_listed(person_model: EntityModel) -> None:
    student = std(
        person_model, "student_records", "S1", {"family_name": "Varrow", "birth_date": "01/01/1987"}
    )
    assert "birth_date" not in student.values and "birth_date" not in student.match
    assert student.placeholders == ("birth_date",)
    assert student.keys["family_birth_year"] == ()
    crm = std(person_model, "crm", "C1", {"family_name": "Varrow", "birth_date": "1987-01-01"})
    assert crm.values["birth_date"] == date(1987, 1, 1) and crm.placeholders == ()
    everyone = std(person_model, "crm", "C2", {"birth_date": "1900-01-01"})
    assert everyone.placeholders == ("birth_date",)


def test_date_order_per_source(person_model: EntityModel) -> None:
    student = std(person_model, "student_records", "S1", {"birth_date": "03/04/1990"})
    assert student.values["birth_date"] == date(1990, 4, 3)


def test_values_that_fail_are_listed_as_invalid(person_model: EntityModel) -> None:
    record = std(
        person_model,
        "crm",
        "C1",
        {
            "given_name": "Ann",
            "email": "ann-at-example.org",
            "phone": "12",
            "birth_date": "not a date",
            "family_name": {"nested": "value"},
            "addresses": "not a list",
            "employer": ["F1"],
        },
    )
    assert set(record.invalid) == {"email", "phone", "birth_date", "family_name", "addresses", "employer"}
    for name in record.invalid:
        assert name not in record.values and name not in record.match
    assert record.references == {}
    assert record.values == {"given_name": "Ann"}


def test_invalid_checksum_keeps_the_value_and_marks_it(person_model: EntityModel) -> None:
    good = person_ref("27182818")
    bad = good[:-1] + str((int(good[-1]) + 1) % 10)
    record = std(person_model, "crm", "C1", {"person_ref": bad})
    assert record.values["person_ref"] == bad
    assert record.match["person_ref.valid"] is False
    assert record.ids[0].valid is False
    assert record.keys["person_ref"] == ()  # id() keys only a valid identifier


def test_organisation_record(org_model: EntityModel) -> None:
    reg = org_reg("20242025")
    record = std(
        org_model,
        "finance",
        "F000001",
        {
            "name": "The Brindle & Sons Holdings Ltd.",
            "registered_id": reg[:4] + " " + reg[4:],
            "phone": "00999 8765 4321",
            "website": "https://www.brindle.example/contact",
            "postcode": "zz9 9zz",
            "parent": "F000002",
        },
    )
    assert record.values["name"] == "The Brindle & Sons Holdings Ltd."
    assert record.match["name"] == "brindle sons holdings"
    assert record.match["name.tokens"] == ["brindle", "sons", "holdings"]
    assert record.match["name.legal"] == "ltd"
    assert record.match["name.first"] == "brindle" and record.match["name.first.metaphone"]
    assert record.values["registered_id"] == reg and record.match["registered_id.valid"] is True
    assert record.values["website"] == "https://www.brindle.example/contact"
    assert record.match["website"] == "brindle.example"
    assert record.references == {"parent": "F000002"}
    assert record.keys["registered_id"] == (f"org_reg:{reg}".lower(),)
    assert record.keys["name_tokens"] == ("brindle holdings",)


def test_untyped_and_numeric_attributes() -> None:
    model = EntityModel.from_dict(
        {
            "entity": "asset",
            "code": "AST",
            "domain": "things",
            "style": "consolidated",
            "display_name": "{label}",
            "attributes": [
                {"name": "label", "type": "text"},
                {"name": "count", "type": "integer"},
                {"name": "ratio", "type": "number"},
                {"name": "flag", "type": "boolean"},
                {"name": "seen_at", "type": "timestamp"},
                {"name": "extra", "type": "json"},
            ],
            "sources": [{"system": "erp", "trust": 1}],
            "match": {
                "version": 1,
                "mode": "consolidate",
                "prior": 0.01,
                "bands": {"upper": 90, "lower": 60},
                "blocking": [{"name": "label", "keys": ["label"]}],
                "comparisons": [{"name": "label", "attribute": "label", "comparator": "exact"}],
            },
            "survivorship": {"version": 1, "default": ["source_trust"]},
            "validation": {"version": 1, "rules": []},
        }
    )
    record = standardise_record(
        model,
        model.source("erp"),
        change(
            "erp",
            "E1",
            {
                "label": "Pump 7",
                "count": "12",
                "ratio": "0.5",
                "flag": "yes",
                "seen_at": "2026-01-02T03:04:05Z",
                "extra": {"b": 1, "a": [2]},
            },
            entity="asset",
        ),
    )
    assert record.values["count"] == 12 and record.match["count"] == 12
    assert record.values["ratio"] == 0.5
    assert record.values["flag"] is True
    assert record.values["seen_at"] == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert record.match["seen_at"] == "2026-01-02T03:04:05+00:00"
    assert record.match["extra"] == '{"a":[2],"b":1}'
    bad = standardise_record(
        model,
        model.source("erp"),
        change("erp", "E2", {"count": "many", "ratio": "nan?", "flag": "maybe"}, entity="asset"),
    )
    assert set(bad.invalid) == {"count", "ratio", "flag"}


def test_standardise_is_deterministic(person_model: EntityModel) -> None:
    assert std(person_model, "hr", "H1", _payload()) == std(person_model, "hr", "H1", _payload())


def test_sample_hash() -> None:
    source = SourceKey("crm", "C000123")
    expected = int.from_bytes(hashlib.sha256(b"person|crm|C000123").digest()[:8], "big") >> 1
    assert sample_hash("person", source) == expected
    values = {sample_hash("person", SourceKey("crm", f"C{i:06d}")) for i in range(2000)}
    assert len(values) == 2000
    assert all(0 <= value < 2**63 for value in values)
    assert sample_hash("organisation", source) != sample_hash("person", source)
