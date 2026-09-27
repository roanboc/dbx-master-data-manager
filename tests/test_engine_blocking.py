"""Blocking: key expressions, keys per record, candidate ranking (B.9.2)."""

from __future__ import annotations

from dataclasses import replace

import jellyfish
import pytest

from mdm.engine.blocking import (
    KeyExpr,
    blocking_keys,
    fixed_comparisons,
    key_attributes,
    keys_from_forms,
    parse_key,
    rank_candidates,
)
from mdm.models.entity_model import BlockingPass, EntityModel
from mdm.models.errors import ModelError
from mdm.models.records import RegisteredId, SourceKey
from tests.test_engine_support import org_reg, person_ref, std


def test_parse_key() -> None:
    assert parse_key("email") == KeyExpr(None, "email", None)
    assert parse_key(" metaphone( family_name ) ") == KeyExpr("metaphone", "family_name", None)
    assert parse_key("sorted_tokens(name.tokens, 2)") == KeyExpr("sorted_tokens", "name.tokens", 2)
    assert parse_key("first(postcode, 3)").attribute == "postcode"
    assert parse_key("name.first.metaphone").attribute == "name"


@pytest.mark.parametrize(
    "bad", ["", "Email", "first(postcode)", "metaphone(name, 2)", "shout(name)", "a b", "x(,)"]
)
def test_parse_key_refuses(bad: str) -> None:
    with pytest.raises(ModelError):
        parse_key(bad)


def test_key_attributes_and_fixed_comparisons(person_model: EntityModel, org_model: EntityModel) -> None:
    assert key_attributes(person_model.match) == {
        "family_birth_year": frozenset({"family_name", "birth_date"}),
        "email": frozenset({"email"}),
        "person_ref": frozenset({"person_ref"}),
        "names_postcode": frozenset({"given_name", "family_name", "postcode"}),
    }
    fixed = fixed_comparisons(person_model.match)
    assert fixed["family_birth_year"] == frozenset({"family_name", "birth_date"})
    assert fixed["names_postcode"] == frozenset({"given_name", "family_name", "postcode"})
    assert key_attributes(org_model.match)["name_tokens"] == frozenset({"name"})


def test_person_keys(person_model: EntityModel) -> None:
    ref = person_ref("16180339")
    record = std(
        person_model,
        "hr",
        "H1",
        {
            "given_name": "Tamsin",
            "family_name": "Quellby",
            "birth_date": "1984-02-29",
            "email": "t.q@example.org",
            "postcode": "ab1 2cd",
            "person_ref": ref,
        },
    )
    metaphone = jellyfish.metaphone("quellby").lower()
    assert record.keys == {
        "family_birth_year": (f"{metaphone}|1984",),
        "email": ("t.q@example.org",),
        "person_ref": (f"person_ref:{ref}",),
        "names_postcode": (f"{jellyfish.metaphone('tamsin').lower()}|{metaphone}|ab12cd",),
    }


def test_a_missing_part_gives_no_key(person_model: EntityModel) -> None:
    record = std(person_model, "crm", "C1", {"given_name": "Tamsin", "family_name": "Quellby"})
    assert record.keys == {"family_birth_year": (), "email": (), "person_ref": (), "names_postcode": ()}


def test_id_keys_only_a_valid_identifier(person_model: EntityModel, org_model: EntityModel) -> None:
    ref = person_ref("16180339")
    wrong = ref[:-1] + str((int(ref[-1]) + 3) % 10)
    assert std(person_model, "crm", "C1", {"person_ref": wrong}).keys["person_ref"] == ()
    reg = org_reg("55512345")
    assert std(org_model, "crm", "C1", {"registered_id": reg}).keys["registered_id"] == (f"org_reg:{reg}",)
    # an identifier without a checksum (valid None) is not keyed either
    forms = {"person_ref": "123", "person_ref.valid": None}
    ids = (RegisteredId("PERSON_REF", "123", None),)
    assert keys_from_forms(person_model.match, forms, ids)["person_ref"] == ()


def test_key_functions(person_model: EntityModel) -> None:
    rules = replace(
        person_model.match,
        blocking=(
            BlockingPass("first_last", ("first(postcode, 3)", "last(phone, 4)")),
            BlockingPass("sounds", ("soundex(family_name)", "nysiis(given_name)")),
            BlockingPass("plain_bool", ("person_ref.valid",)),
        ),
    )
    forms = {
        "postcode": "AB12CD",
        "phone": "+99912345678",
        "family_name": "quellby",
        "given_name": "tamsin",
        "given_name.nysiis": "PRECOMPUTED",
        "person_ref.valid": True,
    }
    keys = keys_from_forms(rules, forms, ())
    assert keys["first_last"] == ("ab1|5678",)
    # soundex has no precomputed form: computed; nysiis has one: used as it is
    assert keys["sounds"] == (f"{jellyfish.soundex('quellby').lower()}|precomputed",)
    assert keys["plain_bool"] == ("true",)


def test_sorted_tokens(org_model: EntityModel) -> None:
    record = std(org_model, "crm", "C1", {"name": "Zephyr Anvil Works Ltd"})
    assert record.keys["name_tokens"] == ("anvil works",)
    variant = std(org_model, "finance", "F1", {"name": "Anvil Zephyr Works Limited"})
    assert variant.keys["name_tokens"] == record.keys["name_tokens"]


def test_blocking_keys_equals_the_keys_standardisation_stored(person_model: EntityModel) -> None:
    record = std(person_model, "hr", "H1", {"family_name": "Quellby", "birth_date": "1984-02-29"})
    assert blocking_keys(person_model.match, record) == record.keys


def test_rank_candidates() -> None:
    shared = {
        SourceKey("hr", "H3"): 1,
        SourceKey("crm", "C9"): 3,
        SourceKey("hr", "H1"): 2,
        SourceKey("crm", "C1"): 2,
        SourceKey("student_records", "S1"): 1,
    }
    kept, dropped = rank_candidates(shared, 3)
    assert kept == [SourceKey("crm", "C9"), SourceKey("crm", "C1"), SourceKey("hr", "H1")]
    assert dropped == 2
    kept_all, none = rank_candidates(shared, 200)
    assert len(kept_all) == 5 and none == 0
    assert rank_candidates({}, 5) == ([], 0)
    assert rank_candidates(shared, 0) == ([], 5)


def test_rank_candidates_ignores_input_order() -> None:
    items = [(SourceKey("crm", f"C{i:03d}"), i % 4) for i in range(50)]
    forward = rank_candidates(dict(items), 10)
    backward = rank_candidates(dict(reversed(items)), 10)
    assert forward == backward
