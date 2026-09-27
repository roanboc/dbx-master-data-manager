"""Weight estimation: u from random pairs and value frequencies, m by EM per pass, the prior (B.9.5)."""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import replace

import pytest

from mdm.engine.blocking import fixed_comparisons
from mdm.engine.estimate import (
    EMResult,
    Levels,
    combine_passes,
    em,
    estimate_u,
    estimated_rules,
    global_prior,
    m_from_identifier,
    scale_to_population,
    u_pairs,
    value_frequencies,
)
from mdm.engine.score import DEFAULT_M, compile_rules, weights
from mdm.models.entity_model import EntityModel, MatchRules
from mdm.models.match import LEVEL_NULL
from mdm.models.records import SourceKey
from tests.test_engine_support import rare_name_rules, state, std

TOLERANCE = 0.03


def _bare(rules: MatchRules) -> MatchRules:
    """The rules without m, so EM starts from the declining default rather than the answer."""
    return replace(rules, comparisons=tuple(replace(spec, m=()) for spec in rules.comparisons))


def _draw(rng: random.Random, probabilities: Sequence[float]) -> int:
    x = rng.random()
    total = 0.0
    for level, p in enumerate(probabilities):
        total += p
        if x < total:
            return level
    return len(probabilities) - 1


def _synthetic(
    rules: MatchRules,
    m: Mapping[str, Sequence[float]],
    u: Mapping[str, Sequence[float]],
    lam: float,
    n: int,
    seed: int,
    *,
    constant: Mapping[str, int] | None = None,
    null_rate: float = 0.0,
) -> list[Levels]:
    """Pairs drawn from a known model: a match with probability lam, each level from m or u independently."""
    rng = random.Random(seed)
    constant = constant or {}
    out = []
    for i in range(n):
        match = rng.random() < lam
        levels = []
        for spec in rules.comparisons:
            if spec.name in constant:
                levels.append(constant[spec.name])
            elif null_rate and rng.random() < null_rate:
                levels.append(LEVEL_NULL)
            else:
                levels.append(_draw(rng, (m if match else u)[spec.name]))
        out.append(Levels(SourceKey("a", f"{i:07d}"), SourceKey("b", f"{i:07d}"), tuple(levels), match))
    return out


def _truth(rules: MatchRules) -> tuple[dict[str, tuple[float, ...]], dict[str, tuple[float, ...]]]:
    return {s.name: s.m for s in rules.comparisons}, {s.name: s.u for s in rules.comparisons}


# ------------------------------------------------------------------------------------------------ u


def _states(person_model: EntityModel, count: int, *, status: str = "active"):
    families = ["Quellby", "Varrow", "Brisk", "Tenniel", "Ossory"]
    out = []
    for i in range(count):
        record = std(
            person_model,
            "crm",
            f"C{i:05d}",
            {
                "given_name": f"Gi{i % 7}",
                "family_name": families[i % len(families)],
                "email": f"p{i}@example.org",
            },
        )
        out.append(state(record, status=status))
    return out


def test_u_pairs_by_hash_active_only(person_model: EntityModel) -> None:
    active = _states(person_model, 30)
    deleted = [replace(s, status="deleted") for s in _states(person_model, 5)]
    shuffled = active + deleted
    random.Random(1).shuffle(shuffled)
    pairs = u_pairs(shuffled, 10)
    assert len(pairs) == 10
    chosen = sorted(active, key=lambda s: (s.sample_hash, s.source.system, s.source.key))[:20]
    assert pairs == [(chosen[i], chosen[i + 10]) for i in range(10)]
    assert all(left.status == "active" and right.status == "active" for left, right in pairs)
    assert u_pairs(list(reversed(shuffled)), 10) == pairs
    assert len(u_pairs(active, 1000)) == 15  # fewer records than asked: half of what there is


def test_value_frequencies(person_model: EntityModel) -> None:
    records = _states(person_model, 20)
    records.append(replace(records[0], status="deleted", source=SourceKey("crm", "gone")))
    freq = value_frequencies(records, person_model.match)
    assert sum(freq["family_name"].values()) == pytest.approx(1.0)
    assert freq["family_name"] == {name: 0.2 for name in ("brisk", "ossory", "quellby", "tenniel", "varrow")}
    assert freq["email"] == {f"p{i}@example.org": 0.05 for i in range(20)}
    assert freq["phone"] == {}  # nobody has one
    assert freq["person_ref"] == {}


def test_estimate_u_levels_sum_to_one_and_level_zero_from_frequencies(person_model: EntityModel) -> None:
    records = _states(person_model, 40)
    pairs = u_pairs(records, 20)
    freq = value_frequencies(records, person_model.match)
    u = estimate_u(person_model.match, pairs, freq)
    for spec in person_model.match.comparisons:
        assert sum(u[spec.name]) == pytest.approx(1.0)
        assert all(value > 0 for value in u[spec.name])
    assert u["family_name"][0] == pytest.approx(5 * 0.2**2)
    assert u["email"][0] == pytest.approx(40 * (1 / 40) ** 2)
    # no frequencies and no pairs with a value: Laplace over the levels
    assert u["phone"] == pytest.approx((1 / 3, 1 / 3, 1 / 3))


def test_frequency_based_u_beats_the_laplace_floor(person_model: EntityModel) -> None:
    """★ A value shared by 1 in 10⁶: random pairs could only say ≤ 1/(N+K−1); frequencies say 1e-6."""
    rules = person_model.match
    shares = {"person_ref": {f"{i:07d}": 1e-3 for i in range(1000)}}  # Σ f² = 1000 · 1e-6 = 1e-3
    tiny = {"person_ref": {"0000001": 1e-3}}  # Σ f² = 1e-6
    records = _states(person_model, 400)
    pairs = u_pairs(records, 200)
    laplace_floor = 1 / (200 + 3 - 1)
    u_tiny = estimate_u(rules, pairs, tiny)["person_ref"]
    assert u_tiny[0] == pytest.approx(1e-6)
    assert u_tiny[0] < laplace_floor / 1000
    assert estimate_u(rules, pairs, shares)["person_ref"][0] == pytest.approx(1e-3)
    no_frequencies = estimate_u(rules, pairs, {})["person_ref"]
    assert no_frequencies[0] >= 1 / (200 + 3)  # the floor random pairs alone give
    # the exact level's weight is then worth log2(m0 / 1e-6) instead of log2(m0 / 0.005)
    spec = replace(rules.comparison("person_ref"), u=u_tiny)
    assert weights(spec)[0] > math.log2(0.9 / 1e-6) - 1e-6


def test_u_zero_is_floored(person_model: EntityModel) -> None:
    u = estimate_u(person_model.match, [], {"email": {"x": 1e-6}})
    assert u["email"][0] == pytest.approx(1e-9)
    assert sum(u["email"]) == pytest.approx(1.0)


# ------------------------------------------------------------------------------------------------ EM


def test_em_recovers_known_m_and_lambda(person_model: EntityModel) -> None:
    rules = person_model.match
    m, u = _truth(rules)
    pairs = _synthetic(rules, m, u, lam=0.3, n=20_000, seed=3)
    result = em(pairs, _bare(rules), u)
    assert result.converged
    assert result.pairs == 20_000
    assert result.lam == pytest.approx(0.3, abs=TOLERANCE)
    assert result.expected_matches == pytest.approx(result.lam * result.pairs, rel=1e-3)
    for spec in rules.comparisons:
        estimated = result.m[spec.name]
        assert sum(estimated) == pytest.approx(1.0)
        for got, want in zip(estimated, m[spec.name], strict=True):
            assert got == pytest.approx(want, abs=TOLERANCE), spec.name


def test_em_with_missing_values(org_model: EntityModel) -> None:
    rules = org_model.match
    m, u = _truth(rules)
    pairs = _synthetic(rules, m, u, lam=0.4, n=20_000, seed=5, null_rate=0.3)
    result = em(pairs, _bare(rules), u)
    assert result.converged
    for spec in rules.comparisons:
        for got, want in zip(result.m[spec.name], m[spec.name], strict=True):
            assert got == pytest.approx(want, abs=TOLERANCE), spec.name


def test_em_is_deterministic(person_model: EntityModel) -> None:
    rules = person_model.match
    m, u = _truth(rules)
    pairs = _synthetic(rules, m, u, lam=0.2, n=5_000, seed=9)
    first = em(pairs, _bare(rules), u)
    shuffled = list(pairs)
    random.Random(4).shuffle(shuffled)
    assert em(shuffled, _bare(rules), u) == first
    assert em(pairs, _bare(rules), u) == first


def test_em_per_pass_does_not_inflate_the_key_comparison(person_model: EntityModel) -> None:
    """★ In a family-name block every pair agrees on the family name, matches or not.

    Read as evidence, that agreement makes every pair look like a match: λ runs to 1 and the other m collapse
    towards u. Fixed, as the pass's own key, it is neither read nor updated, and the rest is recovered. The
    inflation shows where family names rarely agree by chance, so the test fixes those weights.
    """
    rules = rare_name_rules(person_model.match)
    m, u = _truth(rules)
    pairs = _synthetic(rules, m, u, lam=0.05, n=30_000, seed=3, constant={"family_name": 0})
    fixed = fixed_comparisons(rules)["family_birth_year"] - {"birth_date"}
    assert fixed == {"family_name"}
    naive = em(pairs, _bare(rules), u)
    per_pass = em(pairs, _bare(rules), u, fixed=fixed)
    assert naive.lam > 0.5  # inflated: the block's shared key read as evidence of a match
    assert naive.m["family_name"][0] > 0.99
    assert per_pass.converged
    assert per_pass.lam == pytest.approx(0.05, abs=0.01)
    assert per_pass.m["family_name"] == DEFAULT_M[4]  # the starting value, untouched
    for spec in rules.comparisons:
        if spec.name == "family_name":
            continue
        for got, want in zip(per_pass.m[spec.name], m[spec.name], strict=True):
            assert got == pytest.approx(want, abs=TOLERANCE), spec.name


def test_em_for_the_identifier_reads_the_known_m_and_converges(person_model: EntityModel) -> None:
    """★ The identifier method: the other comparisons' m come from pairs sharing a strong ID.

    EM over the identifier comparison alone has no single answer, since λ and its m trade off: on the demo world
    it drifted without converging, and here it lands 0.036 off the true m. Read with the known m, and updating
    only the identifier's, it converges on the truth.
    """
    rules = person_model.match
    m, u = _truth(rules)
    pairs = _synthetic(rules, m, u, lam=0.3, n=20_000, seed=5)
    known = {spec.name: m[spec.name] for spec in rules.comparisons if spec.name != "person_ref"}
    result = em(pairs, _bare(rules), u, known=known)
    assert result.converged
    assert result.lam == pytest.approx(0.3, abs=0.02)
    for got, want in zip(result.m["person_ref"], m["person_ref"], strict=True):
        assert got == pytest.approx(want, abs=TOLERANCE)
    for name, values in known.items():
        assert result.m[name] == tuple(values)  # read, never updated


def test_em_that_does_not_converge_says_so(person_model: EntityModel) -> None:
    rules = person_model.match
    m, u = _truth(rules)
    pairs = _synthetic(rules, m, u, lam=0.3, n=2_000, seed=13)
    result = em(pairs, _bare(rules), u, max_iter=1)
    assert not result.converged
    assert result.iterations == 1
    assert result.max_delta > 1e-5


def test_em_without_pairs(person_model: EntityModel) -> None:
    result = em([], person_model.match, _truth(person_model.match)[1])
    assert result == EMResult(
        m={s.name: s.m for s in person_model.match.comparisons},
        lam=0.1,
        iterations=0,
        converged=True,
        max_delta=0.0,
        pairs=0,
        expected_matches=0.0,
    )


# ------------------------------------------------------------------------------------------------ identifier m


def test_m_from_identifier(person_model: EntityModel) -> None:
    rules = person_model.match
    names = [s.name for s in rules.comparisons]
    equal = (0, 1, 0, LEVEL_NULL, 2, 0, 0)
    also = (0, 0, 3, 1, LEVEL_NULL, 1, 0)
    ignored = (3, 3, 3, 2, 2, 2, 2)  # not strong-equal: not counted
    levels = [
        Levels(SourceKey("a", "1"), SourceKey("b", "1"), equal, True),
        Levels(SourceKey("a", "2"), SourceKey("b", "2"), also, True),
        Levels(SourceKey("a", "3"), SourceKey("b", "3"), ignored, False),
    ]
    m = m_from_identifier(levels, rules, "person_ref")
    assert set(m) == set(names) - {"person_ref"}
    assert m["given_name"] == pytest.approx((3 / 6, 1 / 6, 1 / 6, 1 / 6))
    assert m["family_name"] == pytest.approx((2 / 6, 2 / 6, 1 / 6, 1 / 6))
    assert m["email"] == pytest.approx((1 / 4, 2 / 4, 1 / 4))  # one null ignored
    for values in m.values():
        assert sum(values) == pytest.approx(1.0)


# ------------------------------------------------------------------------------------------------ combining


def _result(m: dict[str, tuple[float, ...]], expected: float) -> EMResult:
    return EMResult(
        m=m, lam=0.1, iterations=3, converged=True, max_delta=0.0, pairs=100, expected_matches=expected
    )


def test_combine_passes_weights_by_expected_matches_and_skips_fixed() -> None:
    results = {
        "p1": _result({"x": (0.8, 0.2), "y": (0.5, 0.5), "k1": (0.9, 0.1), "k2": (0.7, 0.3)}, 30.0),
        "p2": _result({"x": (0.6, 0.4), "y": (0.9, 0.1), "k1": (0.6, 0.4), "k2": (0.99, 0.01)}, 10.0),
    }
    fixed = {"p1": {"k1"}, "p2": {"k2", "y"}}
    combined = combine_passes(results, fixed)
    assert combined["x"] == pytest.approx((0.75, 0.25))  # (30·0.8 + 10·0.6) / 40
    assert combined["y"] == pytest.approx((0.5, 0.5))  # fixed in p2: p1's alone
    assert combined["k1"] == pytest.approx((0.6, 0.4))  # fixed in p1: p2's alone
    assert combined["k2"] == pytest.approx((0.7, 0.3))  # fixed in p2: p1's alone
    assert combine_passes(results, {"p1": {"x"}, "p2": {"x"}}).keys() == {"y", "k1", "k2"}
    assert combine_passes({"p1": _result({"z": (0.5, 0.5)}, 5.0)}, {"p1": {"z"}}) == {}
    # the order passes are given in does not change the numbers
    assert combine_passes(dict(reversed(list(results.items()))), fixed) == combined


def test_combine_passes_without_expected_matches_averages_plainly() -> None:
    results = {"p1": _result({"x": (0.8, 0.2)}, 0.0), "p2": _result({"x": (0.6, 0.4)}, 0.0)}
    assert combine_passes(results, {}) == {"x": pytest.approx((0.7, 0.3))}


def test_global_prior_and_scaling() -> None:
    assert global_prior(500.0, 1001) == pytest.approx(500 / (1001 * 1000 / 2))
    assert global_prior(0.0, 1000) == 1e-9
    assert global_prior(10**9, 10) == 0.5
    assert global_prior(3.0, 1) == 1e-9
    # every record a seed: nothing to scale
    assert scale_to_population(120.0, 1000, 1000) == pytest.approx(120.0)
    # a fifth of the records are seeds: a true pair is seen with probability 2s − s² = 0.36
    assert scale_to_population(36.0, 200, 1000) == pytest.approx(100.0)
    assert scale_to_population(5.0, 0, 1000) == 0.0


def test_estimated_rules_is_a_valid_new_version(person_model: EntityModel) -> None:
    rules = person_model.match
    m, u = _truth(rules)
    pairs = _synthetic(rules, m, u, lam=0.3, n=5_000, seed=21)
    result = em(pairs, _bare(rules), u)
    new_u = estimate_u(rules, [], {"email": {"a": 0.5, "b": 0.5}})
    meta = {"method": "em", "passes": {"all": {"lam": result.lam, "iterations": result.iterations}}}
    new = estimated_rules(rules, {"email": result.m["email"]}, {"email": new_u["email"]}, 0.0004, meta, 2)
    assert new.version == 2
    assert new.prior == 0.0004
    assert new.estimation == meta
    assert new.comparison("email").m == result.m["email"]
    assert new.comparison("email").u == new_u["email"]
    assert new.comparison("phone") == rules.comparison("phone")  # not estimated: kept
    # the new rule set passes the model's own validation and compiles
    doc = replace(person_model, match=new).to_dict()
    again = EntityModel.from_dict(doc)
    assert again.match.comparison("email").m == pytest.approx(result.m["email"])
    compile_rules(again.match, again)


# ------------------------------------------------------------------------------------------------ the whole procedure

_GIVEN = ["Tamsin", "Orrin", "Elspeth", "Corvin", "Maelis", "Brannoch", "Ysolde", "Dace", "Wren", "Ilse", "Tobiah",
          "Sabeth", "Quill", "Rhosyn", "Anwell", "Merrit"]  # fmt: skip
_FAMILY = ["Quellby", "Varrow", "Brisk", "Tenniel", "Ossory", "Pellam", "Drace", "Kestwick", "Morrow", "Unsel",
           "Fairlock", "Glenny", "Hask", "Ivery", "Jarrold", "Lusk", "Nettle", "Purvis", "Rook", "Stave"]  # fmt: skip


def _typo(rng: random.Random, text: str) -> str:
    i = rng.randrange(1, len(text) - 1)
    return text[:i] + text[i + 1] + text[i] + text[i + 2 :]


def _world(person_model: EntityModel, persons: int, seed: int):
    """Invented people, each in hr and sometimes in crm and student records, with typos and gaps."""
    from mdm.engine.identifiers import luhn_digit

    rng = random.Random(seed)
    records, truth = [], {}
    for n in range(persons):
        given = rng.choice(_GIVEN)
        family = _FAMILY[min(int(rng.paretovariate(1.1)) - 1, len(_FAMILY) - 1)]
        born = f"{rng.randint(1950, 2005)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
        base = f"{n:08d}"
        ref = base + luhn_digit(base)
        mail = f"{given}.{family}{n}@example.org".lower()
        phone = f"0{rng.randint(10**7, 10**8 - 1)}"
        post = f"AB{rng.randint(1, 9)} {rng.randint(1, 9)}CD"
        hr = {"given_name": given, "family_name": family, "birth_date": born, "email": mail, "phone": phone,
              "postcode": post, "person_ref": ref}  # fmt: skip
        variants = [("hr", f"H{n:06d}", hr)]
        if rng.random() < 0.7:
            crm = dict(hr, person_ref=ref if rng.random() < 0.4 else None)
            if rng.random() < 0.2:
                crm["given_name"] = _typo(rng, given)
            if rng.random() < 0.3:
                crm["email"] = None
            if rng.random() < 0.4:
                crm["phone"] = None
            variants.append(("crm", f"C{n:06d}", crm))
        if rng.random() < 0.5:
            year, month, day = born.split("-")
            student = dict(hr, birth_date=f"{day}/{month}/{year}", phone=None, person_ref=None)
            if rng.random() < 0.15:
                student["family_name"] = _typo(rng, family)
            variants.append(("student_records", f"S{n:06d}", student))
        for system, source_key, payload in variants:
            record = state(std(person_model, system, source_key, payload))
            records.append(record)
            truth[record.source] = n
    return records, truth


def test_the_whole_procedure_on_standardised_records(person_model: EntityModel) -> None:
    """Blocking, EM per pass with its keys fixed, u from pairs and frequencies, the prior: the estimated weights
    separate an invented world's true pairs from the rest."""
    from mdm.engine.estimate import pair_levels, union_expected_matches
    from mdm.engine.score import band_of_weight, fast_weight
    from mdm.models.match import Band

    rules = _bare(person_model.match)
    records, truth = _world(person_model, 700, seed=7)
    by_source = {r.source: r for r in records}
    strong = {"PERSON_REF"}
    fixed = fixed_comparisons(rules)
    blocks: dict[tuple[str, str], list[SourceKey]] = {}
    for record in records:
        for pass_name, keys in std_keys(person_model, record).items():
            for value in keys:
                blocks.setdefault((pass_name, value), []).append(record.source)
    per_pass: dict[str, set[tuple[SourceKey, SourceKey]]] = {p.name: set() for p in rules.blocking}
    for (pass_name, _value), members in blocks.items():
        for i, left in enumerate(members):
            for right in members[i + 1 :]:
                per_pass[pass_name].add((min(left, right), max(left, right)))
    u = estimate_u(rules, u_pairs(records, len(records) // 2), value_frequencies(records, rules))
    results, pass_levels = {}, {}
    for pass_name, pairs in per_pass.items():
        levels = [pair_levels(rules, by_source[a], by_source[b], strong) for a, b in sorted(pairs)]
        pass_levels[pass_name] = levels
        results[pass_name] = em(levels, rules, u, fixed=fixed[pass_name])
        assert results[pass_name].converged, pass_name
    m = combine_passes(results, fixed)
    union = set().union(*per_pass.values())
    true_pairs = {(a, b) for a, b in union if truth[a] == truth[b]}
    expected = union_expected_matches(pass_levels, results, rules, u, fixed)
    assert expected == pytest.approx(len(true_pairs), rel=0.15)
    # every record was a seed here, so nothing to scale
    prior = global_prior(scale_to_population(expected, len(records), len(records)), len(records))
    estimated = estimated_rules(rules, m, u, prior, {"method": "em"}, 2)
    c = compile_rules(estimated, person_model)
    auto = set()
    for a, b in sorted(union):
        left, right = by_source[a], by_source[b]
        weight, _levels, rule = fast_weight(c, left.match, right.match, left.ids, right.ids)
        band = Band.AUTO if rule and rule.startswith("must") else band_of_weight(c, weight)
        if rule and rule.startswith("cannot"):
            band = Band.DISTINCT
        if band is Band.AUTO:
            auto.add((a, b))
    precision = len(auto & true_pairs) / len(auto)
    recall = len(auto & true_pairs) / len(true_pairs)
    assert precision >= 0.95, precision
    assert recall >= 0.85, recall
    # the estimated m of the exact level is high where the world agrees exactly
    assert m["birth_date"][0] > 0.6 and m["email"][0] > 0.6


def std_keys(model: EntityModel, record) -> dict[str, tuple[str, ...]]:
    from mdm.engine.blocking import keys_from_forms

    return keys_from_forms(model.match, record.match, record.ids)


def test_pair_levels_orders_the_ends_and_flags_equal_strong_ids(person_model: EntityModel) -> None:
    from mdm.engine.estimate import pair_levels
    from tests.test_engine_support import person_ref

    ref = person_ref("24681357")
    left = state(std(person_model, "hr", "H1", {"family_name": "Quellby", "person_ref": ref}))
    right = state(std(person_model, "crm", "C1", {"family_name": "Quelby", "person_ref": ref}))
    levels = pair_levels(person_model.match, left, right, {"PERSON_REF"})
    assert (levels.left, levels.right) == (right.source, left.source)  # crm < hr
    assert levels.strong_equal
    names = [s.name for s in person_model.match.comparisons]
    assert levels.levels[names.index("person_ref")] == 0
    assert levels.levels[names.index("email")] == LEVEL_NULL
    assert not pair_levels(person_model.match, left, right, ()).strong_equal
    assert pair_levels(person_model.match, right, left, {"PERSON_REF"}) == levels


def test_posteriors_are_the_e_step(person_model: EntityModel) -> None:
    from mdm.engine.estimate import posteriors

    rules = person_model.match
    m, u = _truth(rules)
    pairs = _synthetic(rules, m, u, lam=0.3, n=3_000, seed=19)
    result = em(pairs, _bare(rules), u)
    chances = posteriors(pairs, rules, result.m, u, result.lam)
    assert len(chances) == len(pairs)
    assert all(0.0 <= p <= 1.0 for p in chances)
    assert sum(chances) == pytest.approx(result.expected_matches, rel=1e-3)
    agree = [p for p, pair in zip(chances, pairs, strict=True) if pair.levels == (0, 0, 0, 0, 0, 0, 0)]
    assert agree and min(agree) > 0.999
    # a fixed comparison is left out: agreeing on it alone is no evidence
    only_family = [
        Levels(SourceKey("a", "1"), SourceKey("b", "1"), (LEVEL_NULL, 0, *(LEVEL_NULL,) * 5), False)
    ]
    assert posteriors(only_family, rules, result.m, u, 0.1, fixed={"family_name"}) == [pytest.approx(0.1)]


def test_a_comparison_a_pass_never_sees_keeps_its_start_and_does_not_count(person_model: EntityModel) -> None:
    rules = person_model.match
    m, u = _truth(rules)
    pairs = _synthetic(rules, m, u, lam=0.3, n=4_000, seed=27)
    names = [s.name for s in rules.comparisons]
    phone = names.index("phone")
    no_phone = [replace(p, levels=p.levels[:phone] + (LEVEL_NULL,) + p.levels[phone + 1 :]) for p in pairs]
    blind = em(no_phone, _bare(rules), u)
    assert blind.m["phone"] == DEFAULT_M[3]  # nothing observed: the starting value
    assert blind.informed["phone"] == 0.0
    assert blind.informed["email"] == pytest.approx(blind.expected_matches)
    seeing = em(pairs, _bare(rules), u)
    combined = combine_passes({"blind": blind, "seeing": seeing}, {})
    assert combined["phone"] == pytest.approx(seeing.m["phone"])  # the blind pass does not pull it
    assert combine_passes({"blind": blind}, {}).get("phone") is None


def test_combine_passes_weighs_by_informed_mass() -> None:
    a = replace(_result({"x": (0.8, 0.2)}, 100.0), informed={"x": 10.0})
    b = replace(_result({"x": (0.4, 0.6)}, 10.0), informed={"x": 10.0})
    assert combine_passes({"a": a, "b": b}, {})["x"] == pytest.approx((0.6, 0.4))
