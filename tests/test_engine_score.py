"""Scoring, the waterfall, bands, hard rules, counterfactuals and the signature (B.9.4)."""

from __future__ import annotations

import itertools
import math
import random
from dataclasses import replace

import pytest

from mdm.engine.compare import level_count
from mdm.engine.score import (
    band_of,
    band_of_weight,
    compile_rules,
    edge_weight,
    explain,
    fast_weight,
    probability,
    score_pair,
    signature,
    weights,
    worth_explaining,
)
from mdm.models.entity_model import Bands, ComparisonSpec, EntityModel, HardRule
from mdm.models.match import LEVEL_NULL, Band, Explanation
from mdm.models.records import RegisteredId
from tests.test_engine_support import person_ref, std

REF_A = person_ref("11223344")
REF_B = person_ref("55667788")


def _random_levels(c, rng: random.Random) -> tuple[int, ...]:
    return tuple(rng.randint(LEVEL_NULL, len(table) - 1) for table in c.weight_tables)


def _weight_of(c, levels: tuple[int, ...]) -> float:
    weight = c.prior_weight
    for table, level in zip(c.weight_tables, levels, strict=True):
        if level >= 0:
            weight += table[level]
    return weight


# ------------------------------------------------------------------------------------------------ arithmetic


def test_weights_are_log_ratios_clipped() -> None:
    spec = ComparisonSpec("c", "a", "exact", m=(0.9, 0.1), u=(0.01, 0.99))
    assert weights(spec) == pytest.approx((math.log2(90), math.log2(0.1 / 0.99)))
    zero = ComparisonSpec("c", "a", "exact", m=(1.0, 0.0), u=(0.0, 1.0))
    assert weights(zero) == pytest.approx((math.log2((1 - 1e-9) / 1e-9), math.log2(1e-9 / (1 - 1e-9))))
    default = ComparisonSpec("c", "a", "phone")
    assert len(weights(default)) == level_count(default)
    assert list(weights(default)) == sorted(weights(default), reverse=True)


def test_probability() -> None:
    assert probability(0.0) == 50.0
    assert probability(1.0) == pytest.approx(100 * 2 / 3)
    assert probability(-61.0) == 100.0 * 2.0**-61
    assert probability(-5000.0) == 0.0
    assert probability(5000.0) == 100.0
    samples = [x / 4 for x in range(-400, 400)]
    scores = [probability(w) for w in samples]
    assert scores == sorted(scores)
    # continuous across the overflow switch
    assert probability(-60.0) == pytest.approx(probability(-60.0000001), rel=1e-6)


def test_band_of_edges() -> None:
    bands = Bands(upper=90, lower=60)
    assert band_of(90.0, bands) is Band.AUTO
    assert band_of(89.999999, bands) is Band.REVIEW
    assert band_of(60.0, bands) is Band.REVIEW
    assert band_of(59.999999, bands) is Band.DISTINCT
    assert band_of(100.0, bands) is Band.AUTO and band_of(0.0, bands) is Band.DISTINCT


def test_band_edges_in_weight_space(person_model: EntityModel) -> None:
    c = compile_rules(person_model.match, person_model)
    assert c.upper_weight == pytest.approx(math.log2(90 / 10))
    assert c.lower_weight == pytest.approx(math.log2(60 / 40))
    assert probability(c.upper_weight) == pytest.approx(90.0)
    assert band_of_weight(c, c.upper_weight) is Band.AUTO
    assert band_of_weight(c, math.nextafter(c.upper_weight, -math.inf)) is Band.REVIEW
    assert band_of_weight(c, c.lower_weight) is Band.REVIEW
    assert band_of_weight(c, math.nextafter(c.lower_weight, -math.inf)) is Band.DISTINCT
    assert edge_weight(50) == 0.0
    levels = tuple(LEVEL_NULL for _ in c.weight_tables)
    # explain bands the weight exactly as the fast path does, at the edge itself
    at_edge = explain(c, levels, c.upper_weight, None)
    assert at_edge.band is Band.AUTO
    assert worth_explaining(c, c.lower_weight, None)
    assert not worth_explaining(c, math.nextafter(c.lower_weight, -math.inf), None)
    assert worth_explaining(c, -100.0, "cannot_link:person_ref")


def test_waterfall_sums_to_the_weight(person_model: EntityModel, org_model: EntityModel) -> None:
    """Property: prior + Σ contributions == weight within 1e-9, for random level combinations."""
    rng = random.Random(23)
    for model in (person_model, org_model):
        c = compile_rules(model.match, model)
        for _ in range(500):
            levels = _random_levels(c, rng)
            weight = _weight_of(c, levels)
            x = explain(c, levels, weight, None)
            assert abs(x.prior + sum(part.weight for part in x.contributions) - x.weight) < 1e-9
            assert x.score == probability(weight)
            assert [part.level for part in x.contributions] == list(levels)
            assert all(part.weight == 0.0 for part in x.contributions if part.level == LEVEL_NULL)


def test_score_is_monotone_in_every_comparison(person_model: EntityModel, org_model: EntityModel) -> None:
    """Property: the starter tables fall with the level, so a stronger level never lowers the score."""
    rng = random.Random(29)
    for model in (person_model, org_model):
        c = compile_rules(model.match, model)
        for table in c.weight_tables:
            assert list(table) == sorted(table, reverse=True)
        for _ in range(300):
            levels = _random_levels(c, rng)
            base = probability(_weight_of(c, levels))
            for index, table in enumerate(c.weight_tables):
                current = levels[index]
                for stronger in range(0, current if current >= 0 else len(table)):
                    if current == LEVEL_NULL and table[stronger] < 0:
                        continue  # a disagreeing level weighs less than a missing value, by design
                    changed = levels[:index] + (stronger,) + levels[index + 1 :]
                    assert probability(_weight_of(c, changed)) >= base


def test_explain_of_fast_weight_equals_score_pair(person_model: EntityModel) -> None:
    left = std(
        person_model,
        "hr",
        "H1",
        {
            "given_name": "Tamsin",
            "family_name": "Quellby",
            "birth_date": "1984-02-29",
            "email": "tq@example.org",
            "phone": "01234 5678",
            "postcode": "AB1 2CD",
        },
    )
    right = std(
        person_model,
        "crm",
        "C1",
        {
            "given_name": "Tamsyn",
            "family_name": "Quelby",
            "birth_date": "1984-02-28",
            "email": "tq@example.org",
            "phone": "+999 1234 5679",
        },
    )
    c = compile_rules(person_model.match, person_model)
    weight, levels, rule = fast_weight(c, left.match, right.match, left.ids, right.ids)
    assert explain(c, levels, weight, rule) == score_pair(c, left, right)
    x = score_pair(c, left, right)
    assert Explanation.from_dict(x.to_dict()) == x
    assert x.rule_version == person_model.match.version
    assert score_pair(c, left, right) == x  # deterministic


# ------------------------------------------------------------------------------------------------ hard rules


def _pair(person_model: EntityModel, left_ref: str | None, right_ref: str | None, *, alike: bool):
    base = {
        "given_name": "Tamsin",
        "family_name": "Quellby",
        "birth_date": "1984-02-29",
        "email": "tq@example.org",
    }
    other = base if alike else {"given_name": "Orrin", "family_name": "Vesk", "birth_date": "1951-11-03"}
    left = std(person_model, "hr", "H1", {**base, "person_ref": left_ref})
    right = std(person_model, "crm", "C1", {**other, "person_ref": right_ref})
    return left, right


def test_must_link_needs_both_ids_valid_and_equal(person_model: EntityModel) -> None:
    c = compile_rules(person_model.match, person_model)
    left, right = _pair(person_model, REF_A, REF_A, alike=False)
    x = score_pair(c, left, right)
    assert x.hard_rule == "must_link:person_ref"
    assert x.band is Band.AUTO
    assert x.counterfactuals == ()
    # the waterfall still shows the arithmetic
    assert abs(x.prior + sum(p.weight for p in x.contributions) - x.weight) < 1e-9


def test_cannot_link_needs_both_ids_valid_and_different(person_model: EntityModel) -> None:
    c = compile_rules(person_model.match, person_model)
    left, right = _pair(person_model, REF_A, REF_B, alike=True)
    x = score_pair(c, left, right)
    assert x.hard_rule == "cannot_link:person_ref"
    assert x.band is Band.DISTINCT
    assert x.score > 90  # the arithmetic alone would be automatic
    assert x.counterfactuals == ()


def test_an_invalid_or_unchecked_id_never_fires(person_model: EntityModel) -> None:
    c = compile_rules(person_model.match, person_model)
    broken = REF_B[:-1] + str((int(REF_B[-1]) + 1) % 10)
    left, right = _pair(person_model, REF_A, broken, alike=True)
    assert right.ids[0].valid is False
    x = score_pair(c, left, right)
    assert x.hard_rule is None and x.band is Band.AUTO
    unchecked = (RegisteredId("PERSON_REF", REF_A, None),)
    assert fast_weight(c, left.match, right.match, unchecked, unchecked)[2] is None
    assert fast_weight(c, left.match, right.match, left.ids, ())[2] is None
    other_scheme = (RegisteredId("ORG_REG", REF_B, True),)
    assert fast_weight(c, left.match, right.match, left.ids, other_scheme)[2] is None


def test_cannot_link_is_checked_first(person_model: EntityModel) -> None:
    rules = replace(
        person_model.match,
        hard_rules=(HardRule("must_link", "person_ref"), HardRule("cannot_link", "person_ref")),
    )
    c = compile_rules(rules, person_model)
    assert [kind for kind, _attribute, _index in c.hard] == ["cannot_link", "must_link"]
    assert c.hard[0][2] == [s.name for s in rules.comparisons].index("person_ref")
    # a record holding two values of the scheme: one shared, one not -> not disjoint, so must-link
    both = (RegisteredId("PERSON_REF", REF_A, True), RegisteredId("PERSON_REF", REF_B, True))
    one = (RegisteredId("PERSON_REF", REF_A, True),)
    assert fast_weight(c, {}, {}, both, one)[2] == "must_link:person_ref"


# ------------------------------------------------------------------------------------------------ counterfactuals


def _levels_for(c, **named: int) -> tuple[int, ...]:
    names = [spec.name for spec in c.rules.comparisons]
    return tuple(named.get(name, LEVEL_NULL) for name in names)


def _brute_force(c, levels: tuple[int, ...], band: Band) -> dict[str, tuple[float, int, int]]:
    """The smallest |Δw| up and down by exhaustive search, ignoring hard rules."""
    rank = {Band.DISTINCT: 0, Band.REVIEW: 1, Band.AUTO: 2}
    weight = _weight_of(c, levels)
    best: dict[str, tuple[float, int, int]] = {}
    for index, table in enumerate(c.weight_tables):
        for level in range(len(table)):
            if level == levels[index]:
                continue
            changed = levels[:index] + (level,) + levels[index + 1 :]
            new = band_of_weight(c, _weight_of(c, changed))
            if new == band:
                continue
            direction = "up" if rank[new] > rank[band] else "down"
            candidate = (abs(_weight_of(c, changed) - weight), index, level)
            if direction not in best or candidate < best[direction]:
                best[direction] = candidate
    return best


def test_counterfactuals_match_an_exhaustive_search(org_model: EntityModel) -> None:
    """Property: without hard rules in play, each counterfactual is the smallest weight change in its direction."""
    rules = replace(org_model.match, hard_rules=())
    c = compile_rules(rules, org_model)
    rng = random.Random(31)
    names = [spec.name for spec in rules.comparisons]
    for _ in range(400):
        levels = _random_levels(c, rng)
        x = explain(c, levels, _weight_of(c, levels), None)
        expected = _brute_force(c, levels, x.band)
        got = {cf.direction: cf for cf in x.counterfactuals}
        assert set(got) == set(expected)
        for direction, (distance, index, level) in expected.items():
            cf = got[direction]
            assert (names.index(cf.comparison), cf.to_level) == (index, level)
            assert cf.from_level == levels[index]
            changed = levels[:index] + (level,) + levels[index + 1 :]
            assert cf.score == pytest.approx(probability(_weight_of(c, changed)), rel=1e-12)
            assert abs(abs(_weight_of(c, changed) - x.weight) - distance) < 1e-12
        if x.band is Band.AUTO:
            assert "up" not in got
        if x.band is Band.DISTINCT:
            assert "down" not in got


def test_a_review_pair_shows_both_directions(person_model: EntityModel) -> None:
    c = compile_rules(person_model.match, person_model)
    levels = _levels_for(c, given_name=0, family_name=0, birth_date=3)
    x = explain(c, levels, _weight_of(c, levels), None)
    assert x.band is Band.REVIEW
    directions = [cf.direction for cf in x.counterfactuals]
    assert directions == ["up", "down"]
    up, down = x.counterfactuals
    # a birth date one level closer is the smallest rise; a phone that differs the smallest fall
    # (log2(0.3 / 0.9989) is a hair smaller in size than the e-mail's log2(0.3 / 0.99899))
    assert (up.comparison, up.from_level, up.to_level, up.band) == ("birth_date", 3, 2, Band.AUTO)
    assert up.score >= 90
    assert (down.comparison, down.from_level, down.to_level, down.band) == (
        "phone",
        LEVEL_NULL,
        2,
        Band.DISTINCT,
    )
    assert down.score < 60


def test_a_counterfactual_whose_band_comes_from_must_link(person_model: EntityModel) -> None:
    c = compile_rules(person_model.match, person_model)
    worst = {
        spec.name: len(table) - 1 for spec, table in zip(c.rules.comparisons, c.weight_tables, strict=True)
    }
    levels = _levels_for(c, **worst)
    x = explain(c, levels, _weight_of(c, levels), None)
    assert x.band is Band.DISTINCT
    assert [cf.direction for cf in x.counterfactuals] == ["up"]
    (up,) = x.counterfactuals
    assert (up.comparison, up.to_level, up.band) == ("person_ref", 0, Band.AUTO)
    assert up.score < 60  # the arithmetic alone would stay distinct: the band is the rule's


def test_a_counterfactual_whose_band_comes_from_cannot_link(person_model: EntityModel) -> None:
    rules = replace(person_model.match, hard_rules=(HardRule("cannot_link", "person_ref"),))
    c = compile_rules(rules, person_model)
    levels = _levels_for(c, given_name=0, family_name=0, birth_date=0, email=0, phone=0, postcode=0)
    x = explain(c, levels, _weight_of(c, levels), None)
    assert x.band is Band.AUTO
    (down,) = x.counterfactuals
    assert (down.comparison, down.to_level, down.band, down.direction) == (
        "person_ref",
        2,
        Band.DISTINCT,
        "down",
    )
    assert down.score > 90


def test_counterfactual_ties_go_to_the_earlier_comparison(person_model: EntityModel) -> None:
    twin = ComparisonSpec("first", "given_name", "exact", m=(0.9, 0.1), u=(0.1, 0.9))
    rules = replace(
        person_model.match,
        prior=0.5,
        bands=Bands(upper=99.9, lower=50.0),
        comparisons=(twin, replace(twin, name="second", attribute="family_name")),
        hard_rules=(),
    )
    c = compile_rules(rules, person_model)
    x = explain(c, (1, 1), _weight_of(c, (1, 1)), None)
    assert x.band is Band.DISTINCT
    (up,) = x.counterfactuals
    assert up.comparison == "first"


# ------------------------------------------------------------------------------------------------ signature


def test_signature(person_model: EntityModel) -> None:
    c = compile_rules(person_model.match, person_model)
    levels = _levels_for(c, given_name=0, family_name=1, birth_date=3, email=LEVEL_NULL, phone=2, postcode=0)
    assert signature(c, levels) == (
        "given_name= · family_name≈ · birth_date≠ · email∅ · phone≠ · postcode= · person_ref∅"
    )
    x = explain(c, levels, _weight_of(c, levels), None)
    assert x.signature == signature(c, levels)
    labels = [part.label for part in x.contributions]
    assert labels == ["exact", "phonetic", "else", "null", "else", "exact", "null"]


def test_every_level_combination_of_a_small_rule_set(org_model: EntityModel) -> None:
    """Exhaustive over three comparisons: bands follow the weight, counterfactuals change the band."""
    rules = replace(org_model.match, comparisons=org_model.match.comparisons[:3], hard_rules=())
    c = compile_rules(rules, org_model)
    ranges = [range(LEVEL_NULL, len(table)) for table in c.weight_tables]
    for levels in itertools.product(*ranges):
        weight = _weight_of(c, levels)
        x = explain(c, levels, weight, None)
        assert x.band is band_of_weight(c, weight)
        for cf in x.counterfactuals:
            assert cf.band is not x.band
