"""Weight estimation: m by EM per blocking pass, u from random pairs and value frequencies (owner: ENGINE, B.9.5).

Comparisons inside a pass's own key agree for non-matches too, so each pass's
EM fixes the comparisons its keys read (neither used in the E step nor
updated); u for an exact level comes from value frequencies (Σ f_v²), since
random pairs cannot estimate a rare exact level; the global prior is kept apart
from each pass's in-block proportion (decision 10). Pairs are sorted by
(left, right) before EM, so iteration order is fixed; EM then runs over the
distinct level patterns with their counts, which gives the same fixed point in
a fraction of the time and the same numbers on every run. Pure.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import mdm.capacity as capacity
from mdm.engine.compare import compare, exact_form, level_count
from mdm.engine.score import default_m
from mdm.models.entity_model import MatchRules
from mdm.models.match import LEVEL_NULL
from mdm.models.records import SourceKey, SourceState

U_FLOOR = 1e-9
SMOOTHING = 1e-3
START_LAMBDA = 0.1
LAMBDA_MIN = 1e-9


@dataclass(frozen=True, slots=True)
class Levels:
    left: SourceKey
    right: SourceKey
    levels: tuple[int, ...]  # per comparison, in rule order
    strong_equal: bool  # both hold an equal valid strong ID


@dataclass(frozen=True, slots=True)
class EMResult:
    m: Mapping[str, tuple[float, ...]]  # comparison -> m per level
    lam: float  # the in-block proportion of matches
    iterations: int
    converged: bool
    max_delta: float
    pairs: int
    expected_matches: float
    #: comparison -> the expected matches among the pairs where it has a value (what `combine_passes` weighs by)
    informed: Mapping[str, float] = field(default_factory=dict)


def pair_levels(
    rules: MatchRules, left: SourceState, right: SourceState, strong_schemes: Collection[str] = ()
) -> Levels:
    """One candidate pair's levels for EM, in rule order; `strong_equal` when both hold an equal valid ID of
    one of `strong_schemes` (the schemes of the model's strong attributes)."""
    levels = tuple(compare(spec, left.match, right.match) for spec in rules.comparisons)
    schemes = frozenset(strong_schemes)
    left_ids = {(i.scheme, i.value) for i in left.ids if i.valid is True and i.scheme in schemes}
    right_ids = {(i.scheme, i.value) for i in right.ids if i.valid is True and i.scheme in schemes}
    first, second = (
        (left.source, right.source) if left.source <= right.source else (right.source, left.source)
    )
    return Levels(first, second, levels, bool(left_ids & right_ids))


def _active(record: SourceState) -> bool:
    return record.status == "active"


def _hash_order(record: SourceState) -> tuple[int, str, str]:
    return (record.sample_hash, record.source.system, record.source.key)


def u_pairs(records_by_hash: Sequence[SourceState], n: int) -> list[tuple[SourceState, SourceState]]:
    """Records sorted by (sample_hash, source_system, source_key), active only (2n of them): pair i with i + len//2."""
    chosen = sorted((r for r in records_by_hash if _active(r)), key=_hash_order)[: 2 * max(n, 0)]
    half = len(chosen) // 2
    return [(chosen[i], chosen[i + half]) for i in range(half)]


def _form_text(value: Any) -> str | None:
    if value is None or value == "" or isinstance(value, (list, tuple, dict)):
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def value_frequencies(records: Sequence[SourceState], rules: MatchRules) -> dict[str, dict[str, float]]:
    """Per comparison, the share of each value of its exact form among the records that have one."""
    out: dict[str, dict[str, float]] = {}
    active = [r for r in records if _active(r)]
    for spec in rules.comparisons:
        form = exact_form(spec)
        counts: Counter[str] = Counter()
        for record in active:
            text = _form_text(record.match.get(form))
            if text is not None:
                counts[text] += 1
        total = sum(counts.values())
        out[spec.name] = {value: counts[value] / total for value in sorted(counts)} if total else {}
    return out


def estimate_u(
    rules: MatchRules,
    pairs: Sequence[tuple[SourceState, SourceState]],
    frequencies: Mapping[str, Mapping[str, float]],
) -> dict[str, tuple[float, ...]]:
    """Level 0: u0 = Σ f_v², floored at 1e-9; other levels: (count_k + 1) / (N_c + K_c − 1) over the random
    pairs, rescaled to sum to 1 − u0; nulls ignored.

    With no frequencies for a comparison, level 0 is estimated from the random pairs like the others.
    """
    out: dict[str, tuple[float, ...]] = {}
    for spec in rules.comparisons:
        k_levels = level_count(spec)
        counts = [0] * k_levels
        for left, right in pairs:
            level = compare(spec, left.match, right.match)
            if level != LEVEL_NULL:
                counts[level] += 1
        n = sum(counts)
        shares = frequencies.get(spec.name) or {}
        if shares:
            u0 = sum(f * f for f in shares.values())
        else:
            u0 = (counts[0] + 1) / (n + k_levels)
        u0 = min(max(u0, U_FLOOR), 1.0 - U_FLOOR * k_levels)
        rest = [(counts[k] + 1) / (n + k_levels - 1) for k in range(1, k_levels)]
        scale = (1.0 - u0) / sum(rest)
        out[spec.name] = (u0, *(value * scale for value in rest))
    return out


def _start_m(rules: MatchRules) -> dict[str, list[float]]:
    return {spec.name: list(spec.m or default_m(spec)) for spec in rules.comparisons}


def em(
    levels: Sequence[Levels],
    rules: MatchRules,
    u: Mapping[str, tuple[float, ...]],
    *,
    fixed: Collection[str] = (),
    max_iter: int = capacity.EM_MAX_ITERATIONS,
    tol: float = 1e-5,
) -> EMResult:
    """Expectation-maximisation of m and λ with u fixed.

    Start: m from rules (or a declining default), λ = 0.1.
    E: a = log2 λ + Σ log2 m_c[l]; b = log2(1-λ) + Σ log2 u_c[l]; p_i = 1 / (1 + 2**clip(b - a, -60, 60))
       (nulls and `fixed` comparisons skipped).
    M: m_c[k] = (Σ p_i·[l=k] + 1e-3) / (Σ p_i·[l≠null] + 1e-3·K_c); λ = Σ p_i / N.
    Stop: max |Δm| < tol and |Δλ| < tol/10 -> converged; max_iter reached -> converged = False.

    `m` in the result holds every comparison; a fixed one keeps its starting value.
    """
    specs = rules.comparisons
    names = [spec.name for spec in specs]
    fixed_set = frozenset(fixed)
    active = [i for i, name in enumerate(names) if name not in fixed_set]
    m = _start_m(rules)
    lam = START_LAMBDA
    ordered = sorted(levels, key=lambda p: (p.left, p.right))
    n_pairs = len(ordered)
    if n_pairs == 0:
        return EMResult({k: tuple(v) for k, v in m.items()}, lam, 0, True, 0.0, 0, 0.0)

    # identical level patterns (on the comparisons EM reads) share one posterior: count them once
    patterns = Counter(tuple(p.levels[i] for i in active) for p in ordered)
    rows = sorted(patterns.items())
    log_u = [[math.log2(max(min(value, 1.0 - U_FLOOR), U_FLOOR)) for value in u[names[i]]] for i in active]
    k_sizes = [level_count(specs[i]) for i in active]

    iterations = 0
    converged = False
    max_delta = math.inf
    expected = 0.0
    while iterations < max_iter:
        iterations += 1
        log_m = [[math.log2(max(value, U_FLOOR)) for value in m[names[i]]] for i in active]
        log_lam = math.log2(max(lam, LAMBDA_MIN))
        log_not = math.log2(max(1.0 - lam, LAMBDA_MIN))
        agree = [[0.0] * k for k in k_sizes]
        seen = [0.0] * len(active)
        expected = 0.0
        for pattern, count in rows:
            a = log_lam
            b = log_not
            for j, level in enumerate(pattern):
                if level != LEVEL_NULL:
                    a += log_m[j][level]
                    b += log_u[j][level]
            p = 1.0 / (1.0 + 2.0 ** min(max(b - a, -60.0), 60.0))
            mass = p * count
            expected += mass
            for j, level in enumerate(pattern):
                if level != LEVEL_NULL:
                    agree[j][level] += mass
                    seen[j] += mass
        delta = 0.0
        for j, i in enumerate(active):
            if seen[j] <= 0.0:
                continue  # never observed in this pass: nothing to learn, the starting m stays
            k = k_sizes[j]
            new = [(agree[j][level] + SMOOTHING) / (seen[j] + SMOOTHING * k) for level in range(k)]
            old = m[names[i]]
            delta = max(delta, max(abs(x - y) for x, y in zip(new, old, strict=True)))
            m[names[i]] = new
        new_lam = min(max(expected / n_pairs, LAMBDA_MIN), 1.0 - LAMBDA_MIN)
        lam_delta = abs(new_lam - lam)
        lam = new_lam
        max_delta = max(delta, lam_delta)
        if delta < tol and lam_delta < tol / 10:
            converged = True
            break
    return EMResult(
        m={name: tuple(values) for name, values in m.items()},
        lam=lam,
        iterations=iterations,
        converged=converged,
        max_delta=max_delta,
        pairs=n_pairs,
        expected_matches=expected,
        informed={names[i]: seen[j] for j, i in enumerate(active)},
    )


def posteriors(
    levels: Sequence[Levels],
    rules: MatchRules,
    m: Mapping[str, tuple[float, ...]],
    u: Mapping[str, tuple[float, ...]],
    lam: float,
    *,
    fixed: Collection[str] = (),
) -> list[float]:
    """The E step alone: each pair's probability of being a match under m, u and λ, in the order given
    (nulls and `fixed` comparisons skipped, as in `em`)."""
    names = [spec.name for spec in rules.comparisons]
    active = [i for i, name in enumerate(names) if name not in frozenset(fixed)]
    log_m = {i: [math.log2(max(v, U_FLOOR)) for v in m[names[i]]] for i in active}
    log_u = {i: [math.log2(max(min(v, 1.0 - U_FLOOR), U_FLOOR)) for v in u[names[i]]] for i in active}
    log_lam = math.log2(min(max(lam, LAMBDA_MIN), 1.0 - LAMBDA_MIN))
    log_not = math.log2(1.0 - min(max(lam, LAMBDA_MIN), 1.0 - LAMBDA_MIN))
    out: list[float] = []
    for pair in levels:
        a, b = log_lam, log_not
        for i in active:
            level = pair.levels[i]
            if level != LEVEL_NULL:
                a += log_m[i][level]
                b += log_u[i][level]
        out.append(1.0 / (1.0 + 2.0 ** min(max(b - a, -60.0), 60.0)))
    return out


def union_expected_matches(
    passes: Mapping[str, Sequence[Levels]],
    results: Mapping[str, EMResult],
    rules: MatchRules,
    u: Mapping[str, tuple[float, ...]],
    fixed: Mapping[str, Collection[str]],
) -> float:
    """Expected matches over the deduplicated union of the passes' pairs.

    Each pass scores its own pairs as its EM did (its m and λ, its key comparisons skipped); a pair several
    passes found counts once, with the mean of their probabilities. Scale the result to the population with
    `scale_to_population` before `global_prior`.
    """
    found: dict[tuple[SourceKey, SourceKey], list[float]] = {}
    for pass_name in sorted(passes):
        result = results[pass_name]
        pairs = sorted(passes[pass_name], key=lambda p: (p.left, p.right))
        chances = posteriors(pairs, rules, result.m, u, result.lam, fixed=fixed.get(pass_name, ()))
        for pair, chance in zip(pairs, chances, strict=True):
            ends = (pair.left, pair.right) if pair.left <= pair.right else (pair.right, pair.left)
            found.setdefault(ends, []).append(chance)
    return sum(sum(values) / len(values) for _ends, values in sorted(found.items()))


def m_from_identifier(
    levels: Sequence[Levels], rules: MatchRules, strong: str
) -> dict[str, tuple[float, ...]]:
    """Pairs with equal valid strong IDs are matches: m_c[k] = (count_k + 1) / (N_c + K_c), c ≠ `strong`
    (the identifier comparison's name)."""
    matches = [p for p in levels if p.strong_equal]
    out: dict[str, tuple[float, ...]] = {}
    for index, spec in enumerate(rules.comparisons):
        if spec.name == strong:
            continue
        k_levels = level_count(spec)
        counts = [0] * k_levels
        for pair in matches:
            level = pair.levels[index]
            if level != LEVEL_NULL:
                counts[level] += 1
        n = sum(counts)
        out[spec.name] = tuple((count + 1) / (n + k_levels) for count in counts)
    return out


def combine_passes(
    results: Mapping[str, EMResult], fixed: Mapping[str, Collection[str]]
) -> dict[str, tuple[float, ...]]:
    """Each comparison's m averaged over the passes that estimated it, weighted by their expected matches.

    The weight is the pass's expected matches among its pairs where the comparison has a value
    (`EMResult.informed`), else all its expected matches; a pass that never saw a value does not count. A
    comparison fixed in, or unseen by, every pass is absent (the caller keeps its current m); passes with no
    expected match count equally when no pass has any.
    """
    totals: dict[str, list[float]] = {}
    mass: dict[str, float] = {}
    plain: dict[str, list[list[float]]] = {}
    for pass_name in sorted(results):
        result = results[pass_name]
        skip = frozenset(fixed.get(pass_name, ()))
        for comparison in sorted(result.m):
            if comparison in skip:
                continue
            values = result.m[comparison]
            weight = max(result.informed.get(comparison, result.expected_matches), 0.0)
            if comparison in result.informed and weight <= 0.0:
                continue  # the pass never saw a value for it
            plain.setdefault(comparison, []).append(list(values))
            if weight <= 0.0:
                continue
            acc = totals.setdefault(comparison, [0.0] * len(values))
            for level, value in enumerate(values):
                acc[level] += weight * value
            mass[comparison] = mass.get(comparison, 0.0) + weight
    out: dict[str, tuple[float, ...]] = {}
    for comparison in sorted(plain):
        if mass.get(comparison, 0.0) > 0.0:
            averaged = [value / mass[comparison] for value in totals[comparison]]
        else:
            vectors = plain[comparison]
            averaged = [sum(column) / len(vectors) for column in zip(*vectors, strict=True)]
        total = sum(averaged)
        out[comparison] = tuple(value / total for value in averaged)
    return out


def global_prior(expected_matches: float, records: int) -> float:
    """expected matches ÷ (records·(records−1)/2), clipped to [1e-9, 0.5]."""
    pairs = records * (records - 1) / 2.0
    if pairs <= 0:
        return 1e-9
    return min(max(expected_matches / pairs, 1e-9), 0.5)


def scale_to_population(expected_matches: float, seeds: int, records: int) -> float:
    """Expected matches among the seeds' candidate pairs scaled to the population: ÷ (2s − s²), s = seeds ÷ records.

    A true pair is found when either endpoint is a seed, which happens with probability 2s − s².
    """
    if seeds <= 0 or records <= 0:
        return 0.0
    s = min(seeds / records, 1.0)
    return expected_matches / (2.0 * s - s * s)


def estimated_rules(
    rules: MatchRules,
    m: Mapping[str, tuple[float, ...]],
    u: Mapping[str, tuple[float, ...]],
    prior: float,
    meta: Mapping[str, Any],
    version: int,
) -> MatchRules:
    """A new rule version with m, u and the prior replaced and `meta` recorded in `estimation`."""
    comparisons = tuple(
        replace(
            spec,
            m=tuple(float(v) for v in m[spec.name]) if spec.name in m else spec.m,
            u=tuple(float(v) for v in u[spec.name]) if spec.name in u else spec.u,
        )
        for spec in rules.comparisons
    )
    return replace(
        rules,
        version=version,
        prior=float(prior),
        comparisons=comparisons,
        estimation=dict(meta),
    )
