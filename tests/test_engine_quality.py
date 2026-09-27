"""Quality rules on arrival: every kind and code, and no value in any result (B.9.8)."""

from __future__ import annotations

from dataclasses import astuple, replace
from datetime import date

import pytest

from mdm.engine.quality import NAMED_PATTERNS, check
from mdm.models.entity_model import PATTERN_NAMES, EntityModel, ValidationRule, ValidationRules
from mdm.models.records import RULE_RESULT_CODES, RuleResult
from mdm.models.safety import safe
from tests.test_engine_support import org_reg, person_ref, std

TODAY = date(2026, 9, 27)
CODES = {"country": frozenset({"XA", "XB", "XC", "XD"})}


def results(model: EntityModel, record, code_lists=CODES, today=TODAY) -> dict[str, RuleResult]:
    return {r.rule_id: r for r in check(model.validation, model, record, code_lists, today)}


def test_named_patterns_cover_the_model_names() -> None:
    assert set(NAMED_PATTERNS) == set(PATTERN_NAMES)


def test_a_clean_person_passes_every_rule(person_model: EntityModel) -> None:
    record = std(
        person_model,
        "hr",
        "H1",
        {
            "given_name": "Tamsin",
            "family_name": "Quellby",
            "birth_date": "1984-02-29",
            "person_ref": person_ref("10203040"),
            "country": "xb",
            "email": "tq@example.org",
        },
    )
    found = results(person_model, record)
    assert set(found) == {r.rule_id for r in person_model.validation.rules}
    assert all(r.passed and r.code == "ok" for r in found.values())


def test_every_failure_code(person_model: EntityModel) -> None:
    bad_ref = person_ref("10203040")
    bad_ref = bad_ref[:-1] + str((int(bad_ref[-1]) + 1) % 10)
    record = std(
        person_model,
        "student_records",
        "S1",
        {
            "given_name": " ",
            "birth_date": "01/01/1987",
            "person_ref": bad_ref,
            "country": "QQ",
            "email": "not-an-email",
        },
    )
    found = results(person_model, record)
    assert (found["PER-V1"].passed, found["PER-V1"].code) == (False, "missing")  # family name
    assert (found["PER-V2"].passed, found["PER-V2"].code) == (False, "missing")  # blank given name
    assert found["PER-V3"].code == "ok"  # a placeholder is absent: range has nothing to check
    assert (found["PER-V4"].passed, found["PER-V4"].code) == (False, "placeholder")
    assert (found["PER-V5"].passed, found["PER-V5"].code) == (False, "bad_checksum")
    assert (found["PER-V6"].passed, found["PER-V6"].code) == (False, "not_in_code_list")
    assert (found["PER-V7"].passed, found["PER-V7"].code) == (False, "bad_pattern")  # failed standardisation
    assert {r.code for r in found.values()} <= set(RULE_RESULT_CODES)


def test_required_on_a_placeholder(person_model: EntityModel) -> None:
    rules = ValidationRules(1, (ValidationRule("R1", "birth_date", "required", "completeness"),))
    record = std(person_model, "crm", "C1", {"birth_date": "1900-01-01"})
    (result,) = check(rules, person_model, record, CODES, TODAY)
    assert (result.passed, result.code) == (False, "placeholder")


@pytest.mark.parametrize(
    ("birth", "code"),
    [
        ("1900-01-02", "ok"),
        ("1899-12-31", "out_of_range"),
        ("2026-09-27", "ok"),
        ("2026-09-28", "out_of_range"),
    ],
)
def test_range_with_today(person_model: EntityModel, birth: str, code: str) -> None:
    record = std(person_model, "hr", "H1", {"birth_date": birth})
    assert results(person_model, record)["PER-V3"].code == code


def test_numeric_range() -> None:
    model = EntityModel.from_dict(
        {
            "entity": "asset",
            "code": "AST",
            "domain": "things",
            "style": "consolidated",
            "display_name": "{label}",
            "attributes": [{"name": "label", "type": "text"}, {"name": "weight_kg", "type": "number"}],
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
            "validation": {
                "version": 1,
                "rules": [
                    {
                        "rule_id": "A1",
                        "attribute": "weight_kg",
                        "kind": "range",
                        "dimension": "validity",
                        "params": {"min": 0, "max": 500},
                        "severity": "hold",
                    },
                ],
            },
        }
    )
    ok = std(model, "erp", "E1", {"weight_kg": 12.5})
    heavy = std(model, "erp", "E2", {"weight_kg": "501"})
    (first,) = check(model.validation, model, ok, {}, TODAY)
    (second,) = check(model.validation, model, heavy, {}, TODAY)
    assert first.passed and not second.passed
    assert second.code == "out_of_range" and second.severity == "hold" and second.dimension == "validity"


def test_patterns_test_the_comparison_form(org_model: EntityModel) -> None:
    good = std(org_model, "crm", "C1", {"name": "Brindle", "website": "HTTPS://WWW.Brindle.example/x"})
    assert results(org_model, good)["ORG-V4"].code == "ok"
    bad = std(org_model, "crm", "C2", {"name": "Brindle", "website": "brindle_works"})
    assert results(org_model, bad)["ORG-V4"].code == "bad_pattern"
    rules = ValidationRules(
        1, (ValidationRule("P", "postcode", "pattern", "validity", {"pattern": "postcode"}),)
    )
    postcode = std(org_model, "crm", "C3", {"postcode": "ab1 2cd"})
    assert check(rules, org_model, postcode, {}, TODAY)[0].code == "ok"


def test_checksum_without_a_value_or_without_a_checksum_passes(org_model: EntityModel) -> None:
    absent = std(org_model, "crm", "C1", {"name": "Brindle"})
    assert results(org_model, absent)["ORG-V2"].code == "ok"
    valid = std(org_model, "crm", "C2", {"name": "Brindle", "registered_id": org_reg("44556677")})
    assert results(org_model, valid)["ORG-V2"].code == "ok"
    unchecked_model = replace(
        org_model,
        attributes=tuple(
            replace(a, checksum=None, strong=False) if a.name == "registered_id" else a
            for a in org_model.attributes
        ),
    )
    unchecked = std(unchecked_model, "crm", "C3", {"name": "Brindle", "registered_id": "12345"})
    assert unchecked.ids[0].valid is None
    assert results(unchecked_model, unchecked)["ORG-V2"].code == "ok"


def test_a_code_list_with_no_copy_loaded_fails(person_model: EntityModel) -> None:
    record = std(person_model, "hr", "H1", {"country": "XA"})
    assert results(person_model, record, code_lists={})["PER-V6"].code == "not_in_code_list"
    assert results(person_model, record)["PER-V6"].code == "ok"


def test_results_carry_codes_never_values(person_model: EntityModel) -> None:
    payload = {
        "given_name": "Tamsin",
        "family_name": "Quellby",
        "birth_date": "1899-01-02",
        "person_ref": "123456789",
        "country": "Neverland",
        "email": "tamsin quellby@example.org",
    }
    record = std(person_model, "crm", "C1", payload)
    found = check(person_model.validation, person_model, record, CODES, TODAY)
    assert len(found) == len(person_model.validation.rules)
    for result in found:
        fields = astuple(result)
        safe(list(fields))  # every field passes the safety check for details
        text = " ".join(str(field) for field in fields).lower()
        for value in ("tamsin", "quellby", "1899", "123456789", "neverland", "example.org"):
            assert value not in text
