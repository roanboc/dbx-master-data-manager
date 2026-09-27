"""Survivorship: strategies, tie-break, steward values and pins, groups, provenance (B.9.7)."""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from mdm.engine.survive import Member, survive
from mdm.models.entity_model import EntityModel, SurvivorshipRules, SurvivorshipSpec
from mdm.models.records import SourceKey, StewardValue
from tests.test_engine_support import key

T0 = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def member(text: str, values: dict[str, Any], *, days: int = 0, seq: int = 1) -> Member:
    return Member(key(text), values, T0 + timedelta(days=days), seq)


def rules_with(model: EntityModel, **attributes: tuple[str, ...]) -> SurvivorshipRules:
    specs = dict(model.survivorship.attributes)
    for name, strategies in attributes.items():
        specs[name] = replace(specs.get(name, SurvivorshipSpec(strategies)), strategies=strategies)
    return replace(model.survivorship, attributes=specs)


def run(model: EntityModel, members: list[Member], rules: SurvivorshipRules | None = None, steward=None):
    return survive(model, rules or model.survivorship, members, steward or {}, NOW, 3)


# ------------------------------------------------------------------------------------------------ strategies


def test_source_trust_keeps_the_most_trusted(person_model: EntityModel) -> None:
    members = [
        member("crm:C1", {"given_name": "Tam"}, days=9),
        member("hr:H1", {"given_name": "Tamsin"}, days=1),
        member("student_records:S1", {"given_name": "Tamsyn"}, days=5),
    ]
    values, provenance = run(person_model, members)
    assert values["given_name"] == "Tamsin"  # hr trust 1 < student_records 2 < crm 3
    assert provenance["given_name"]["winner"] == {"source": "hr:H1", "value": "Tamsin"}
    assert provenance["given_name"]["strategy"] == ["source_trust", "recency"]
    assert provenance["given_name"]["rule_version"] == 3
    assert [r["source"] for r in provenance["given_name"]["runners_up"]] == ["student_records:S1", "crm:C1"]


def test_trust_by_attribute(person_model: EntityModel) -> None:
    rules = rules_with(person_model, email=("source_trust", "recency"))
    members = [
        member("hr:H1", {"email": "tam@example.org"}, days=9),
        member("crm:C1", {"email": "tamsin@example.org"}, days=1),
    ]
    values, _ = run(person_model, members, rules)
    # crm's email trust is 1, as good as hr's 1: recency decides
    assert values["email"] == "tam@example.org"
    newer = [
        member("hr:H1", {"email": "tam@example.org"}, days=1),
        member("crm:C1", {"email": "t@example.org"}, days=9),
    ]
    assert run(person_model, newer, rules)[0]["email"] == "t@example.org"


def test_recency_then_landing_sequence(person_model: EntityModel) -> None:
    rules = rules_with(person_model, city=("recency",))
    members = [
        member("crm:C1", {"city": "Tarnwick"}, days=3, seq=10),
        member("crm:C2", {"city": "Ellsmoor"}, days=3, seq=12),
        member("hr:H1", {"city": "Quenby"}, days=2, seq=99),
    ]
    values, provenance = run(person_model, members, rules)
    assert values["city"] == "Ellsmoor"
    assert provenance["city"]["strategy"] == ["recency"]


def test_completeness_keeps_the_longest(person_model: EntityModel) -> None:
    rules = rules_with(person_model, city=("completeness",))
    members = [member("hr:H1", {"city": "Tarn"}), member("crm:C1", {"city": "Tarnwick Vale"})]
    assert run(person_model, members, rules)[0]["city"] == "Tarnwick Vale"


def test_frequency_compares_folded_values(person_model: EntityModel) -> None:
    rules = rules_with(person_model, city=("frequency", "source_trust"))
    members = [
        member("hr:H1", {"city": "Quenby"}),
        member("crm:C1", {"city": "TARNWICK"}),
        member("crm:C2", {"city": "tarnwick"}),
        member("student_records:S1", {"city": "Tárnwick"}),
    ]
    values, _ = run(person_model, members, rules)
    # three hold tarnwick once folded; among them student_records (trust 2) beats crm (trust 3)
    assert values["city"] == "Tárnwick"


def test_tie_break_by_system_then_key(person_model: EntityModel) -> None:
    rules = rules_with(person_model, city=("source_trust",))
    members = [member("crm:C9", {"city": "Quenby"}), member("crm:C10", {"city": "Tarnwick"})]
    values, provenance = run(person_model, members, rules)
    assert values["city"] == "Tarnwick"  # "C10" < "C9" as text
    assert provenance["city"]["runners_up"] == [{"source": "crm:C9", "value": "Quenby"}]


def test_members_without_a_value_do_not_compete(person_model: EntityModel) -> None:
    members = [member("hr:H1", {"city": "  "}), member("crm:C1", {"city": "Tarnwick", "phone": None})]
    values, provenance = run(person_model, members)
    assert values["city"] == "Tarnwick"
    assert values["phone"] is None and "phone" not in provenance


def test_runners_up_are_at_most_five(person_model: EntityModel) -> None:
    members = [member(f"crm:C{i}", {"city": f"Town{i}"}, days=i) for i in range(8)]
    _, provenance = run(person_model, members)
    assert len(provenance["city"]["runners_up"]) == 5


def test_references_are_never_survived(person_model: EntityModel) -> None:
    values, provenance = run(person_model, [member("hr:H1", {"employer": "F000001", "city": "Tarnwick"})])
    assert "employer" not in values and "employer" not in provenance
    assert set(values) == {a.name for a in person_model.column_attributes()}


def test_registry_style_survives_nothing(person_model: EntityModel) -> None:
    registry = replace(person_model, style="registry")
    assert run(registry, [member("hr:H1", {"city": "Tarnwick"})]) == ({}, {})


# ------------------------------------------------------------------------------------------------ steward values


def test_an_unpinned_steward_value_competes_with_its_rank(person_model: EntityModel) -> None:
    members = [member("hr:H1", {"city": "Tarnwick"}, days=10)]
    steward = {"city": StewardValue("Quenby", None, "persona:data_steward", T0)}
    values, provenance = run(person_model, members, steward=steward)
    assert values["city"] == "Quenby"  # steward_rank 0 beats hr's trust 1
    assert provenance["city"]["winner"]["source"] == "steward:city"
    ranked_low = replace(person_model.survivorship, steward_rank=5)
    assert run(person_model, members, ranked_low, steward)[0]["city"] == "Tarnwick"


def test_steward_recency_is_set_at(person_model: EntityModel) -> None:
    rules = rules_with(person_model, city=("recency",))
    members = [member("hr:H1", {"city": "Tarnwick"}, days=10)]
    older = {"city": StewardValue("Quenby", None, "persona:data_steward", T0 + timedelta(days=9))}
    newer = {"city": StewardValue("Quenby", None, "persona:data_steward", T0 + timedelta(days=11))}
    assert run(person_model, members, rules, older)[0]["city"] == "Tarnwick"
    assert run(person_model, members, rules, newer)[0]["city"] == "Quenby"


def test_an_unexpired_pin_wins_over_a_newer_source_value(person_model: EntityModel) -> None:
    """★ A pin until after now wins outright, whatever the strategies say."""
    rules = rules_with(person_model, city=("recency",))
    members = [member("hr:H1", {"city": "Tarnwick"}, days=40)]
    pinned = {"city": StewardValue("Quenby", NOW + timedelta(days=1), "persona:data_steward", T0)}
    values, provenance = run(person_model, members, rules, pinned)
    assert values["city"] == "Quenby"
    assert provenance["city"]["strategy"] == ["pin"]
    assert provenance["city"]["winner"] == {"source": "steward:city", "value": "Quenby"}
    assert provenance["city"]["runners_up"] == [{"source": "hr:H1", "value": "Tarnwick"}]


def test_an_expired_pin_no_longer_competes(person_model: EntityModel) -> None:
    members = [member("crm:C1", {"city": "Tarnwick"})]
    expired = {"city": StewardValue("Quenby", NOW - timedelta(seconds=1), "persona:data_steward", T0)}
    values, provenance = run(person_model, members, steward=expired)
    assert values["city"] == "Tarnwick"  # an unpinned steward value would have won on rank
    assert provenance["city"]["winner"]["source"] == "crm:C1"
    at_now = {"city": StewardValue("Quenby", NOW, "persona:data_steward", T0)}
    assert run(person_model, members, steward=at_now)[0]["city"] == "Tarnwick"


def test_naive_times_are_read_as_utc(person_model: EntityModel) -> None:
    naive_pin = {
        "city": StewardValue("Quenby", datetime(2026, 3, 2), "persona:data_steward", datetime(2026, 1, 1))
    }
    naive_member = Member(SourceKey("hr", "H1"), {"city": "Tarnwick"}, datetime(2026, 1, 9), 1)
    assert (
        survive(person_model, person_model.survivorship, [naive_member], naive_pin, NOW, 1)[0]["city"]
        == "Quenby"
    )


# ------------------------------------------------------------------------------------------------ groups


def test_keyed_union_resolves_each_key(person_model: EntityModel) -> None:
    home_hr = {
        "kind": "home",
        "line1": "1 Kestrel Row",
        "city": "Tarnwick",
        "postcode": None,
        "country": None,
    }
    home_crm = {"kind": "Home", "line1": "9 Other Lane", "city": "Quenby", "postcode": None, "country": None}
    work_crm = {"kind": "work", "line1": "2 Mill Yard", "city": "Tarnwick", "postcode": None, "country": None}
    members = [
        member("crm:C1", {"addresses": [home_crm, work_crm]}, days=9),
        member("hr:H1", {"addresses": [home_hr]}, days=1),
    ]
    values, provenance = run(person_model, members)
    assert values["addresses"] == [home_hr, work_crm]  # keys "home" and "work", folded and sorted
    assert provenance["addresses"]["group"] == "keyed_union"
    assert provenance["addresses"]["entries"] == [
        {"key": "home", "source": "hr:H1"},
        {"key": "work", "source": "crm:C1"},
    ]
    assert provenance["addresses"]["winner"]["source"] == "crm:C1"  # a tie on entries won: crm < hr


def test_whole_group_takes_the_winners_list(person_model: EntityModel) -> None:
    rules = replace(
        person_model.survivorship,
        attributes={
            **person_model.survivorship.attributes,
            "addresses": SurvivorshipSpec(("source_trust",), "whole_group"),
        },
    )
    hr_list = [{"kind": "home", "line1": "1 Kestrel Row"}]
    crm_list = [{"kind": "home", "line1": "9 Other Lane"}, {"kind": "work", "line1": "2 Mill Yard"}]
    members = [member("crm:C1", {"addresses": crm_list}), member("hr:H1", {"addresses": hr_list})]
    values, provenance = run(person_model, members, rules)
    assert values["addresses"] == hr_list
    assert "entries" not in provenance["addresses"]


def test_completeness_counts_filled_group_fields(person_model: EntityModel) -> None:
    rules = replace(
        person_model.survivorship,
        attributes={**person_model.survivorship.attributes, "addresses": SurvivorshipSpec(("completeness",))},
    )
    sparse = [{"kind": "home", "line1": "A very long first line indeed", "city": None}]
    full = [{"kind": "home", "line1": "1 Row", "city": "Tarnwick"}]
    members = [member("hr:H1", {"addresses": sparse}), member("crm:C1", {"addresses": full})]
    assert run(person_model, members, rules)[0]["addresses"] == full


# ------------------------------------------------------------------------------------------------ properties


def test_member_order_does_not_matter(person_model: EntityModel) -> None:
    rng = random.Random(8)
    members = [
        member(
            f"{rng.choice(['hr', 'crm', 'student_records'])}:K{i}",
            {
                "given_name": rng.choice(["Tam", "Tamsin", "Tamsyn"]),
                "city": rng.choice(["Tarnwick", "Quenby"]),
                "birth_date": date(1984, 2, rng.randint(1, 28)),
            },
            days=rng.randint(0, 3),
            seq=rng.randint(1, 5),
        )
        for i in range(12)
    ]
    expected = run(person_model, members)
    for seed in range(5):
        shuffled = list(members)
        random.Random(seed).shuffle(shuffled)
        assert run(person_model, shuffled) == expected


@pytest.mark.parametrize("strategies", [("source_trust",), ("recency",), ("completeness",), ("frequency",)])
def test_the_winner_is_always_a_candidate(person_model: EntityModel, strategies: tuple[str, ...]) -> None:
    rules = rules_with(person_model, city=strategies)
    members = [member(f"crm:C{i}", {"city": f"Town{i % 3}"}, days=i % 4, seq=i) for i in range(9)]
    values, provenance = run(person_model, members, rules)
    assert values["city"] in {f"Town{i}" for i in range(3)}
    assert provenance["city"]["winner"]["value"] == values["city"]


def test_a_keyed_union_whose_entries_carry_no_key_falls_back_to_the_plain_winner(
    person_model: EntityModel,
) -> None:
    """★ An address without its `kind` must not stop survivorship: the group has nothing to union."""
    addresses = [{"line1": "1 Quill Lane", "city": "Norvale"}]
    values, provenance = run(
        person_model, [member("hr:H1", {"family_name": "Arden", "addresses": addresses})]
    )
    assert values["addresses"] == addresses
    assert provenance["addresses"]["winner"]["source"] == "hr:H1"
    assert "group" not in provenance["addresses"]


# ------------------------------------------------------------------------------------------------ what decided (initiative 3)


def test_the_strategy_that_decided_is_recorded(person_model: EntityModel) -> None:
    """`decided_by`: trust decides; recency after a trust tie; a single holder; a pin; a keyed union; the tie-break."""
    members = [
        member("hr:H1", {"given_name": "Tamsin", "person_ref": "79927398713"}, days=1),
        member("crm:C1", {"given_name": "Tam", "email": "tam@example.org"}, days=9),
        member("crm:C2", {"given_name": "Tamsyn", "email": "tamsyn@example.org"}, days=9, seq=2),
    ]
    _, provenance = run(person_model, members)
    assert provenance["given_name"]["decided_by"] == "source_trust"  # hr ranks 1, before crm's 3
    assert provenance["person_ref"]["decided_by"] == "only"
    # email: recency first; crm:C1 and crm:C2 share the day, and the later landing wins it
    assert provenance["email"]["decided_by"] == "recency"
    trust_tie = rules_with(person_model, city=("source_trust", "recency"))
    same_rank = [
        member("crm:C1", {"city": "Tarnwick"}, days=1),
        member("crm:C2", {"city": "Ellsmoor"}, days=4),
    ]
    assert run(person_model, same_rank, trust_tie)[1]["city"]["decided_by"] == "recency"
    tied = [member("crm:C2", {"city": "Tarnwick"}, days=1), member("crm:C1", {"city": "Ellsmoor"}, days=1)]
    tie = run(person_model, tied, rules_with(person_model, city=("source_trust",)))[1]["city"]
    assert (tie["decided_by"], tie["winner"]["source"]) == ("tie_break", "crm:C1")
    pin = {"city": StewardValue("Quenby", NOW + timedelta(days=5), "persona:data_steward", T0)}
    assert run(person_model, same_rank, steward=pin)[1]["city"]["decided_by"] == "pin"
    groups = [
        member("hr:H1", {"addresses": [{"kind": "home", "line1": "1 Ash Row"}]}),
        member("crm:C1", {"addresses": [{"kind": "work", "line1": "2 Elm Row"}]}),
    ]
    assert run(person_model, groups)[1]["addresses"]["decided_by"] == "keyed_union"
