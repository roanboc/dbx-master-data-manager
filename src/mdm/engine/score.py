"""Scoring, the explanation and the counterfactuals (owner: ENGINE, B.9.4).

- `w_k = log2(m_k / u_k)`, m and u clipped to [1e-9, 1 - 1e-9]; prior weight
  `w0 = log2(λ / (1 - λ))`; total `w = w0 + Σ w_c[level_c]` (null adds 0).
- Score `s = 100 / (1 + 2 ** (-w))`; for `w < -60`, `100 · 2^w`.
- Band: `s >= upper` auto; `s >= lower` review; else distinct. The fast path
  compares weights against `log2(b / (100 - b))`, and so does `explain`, so a
  pair's band never depends on which path scored it.
- Hard rules fire only when both sides hold a valid ID of the rule's scheme:
  cannot-link first (values differ -> distinct), then must-link (equal -> auto).
- Counterfactuals (none under a hard rule): the smallest weight change that
  raises the band (`up`) and the smallest that lowers it (`down`), with the
  hard rules re-applied for the changed level: the identifier comparison of a
  must-link attribute moved to level 0 gives auto, that of a cannot-link
  attribute moved to its last level gives distinct (the counterfactual
  supposes both sides valid, as those levels describe).
- Signature: `" · ".join(f"{comparison}{mark}")`, mark `=` level 0, `≈`
  intermediate, `≠` last, `∅` null.

A comparison with no `m` or `u` in its rule set uses the declining defaults of
`default_m` and `default_u`. Arrival calls `fast_weight` for every pair and
`explain` only for pairs at or above the lower band or decided by a hard rule;
`explain(fast_weight(…))` equals `score_pair(…)`. Pure.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from mdm.engine.compare import Comparer, comparer, level_count, level_labels
from mdm.models.entity_model import Bands, ComparisonSpec, EntityModel, MatchRules
from mdm.models.match import LEVEL_NULL, Band, Contribution, Counterfactual, Explanation
from mdm.models.records import RegisteredId, SourceState, StdRecord

P_MIN = 1e-9
P_MAX = 1.0 - 1e-9
#: starting m per level count when a rule set gives none: most matches agree exactly
DEFAULT_M: Mapping[int, tuple[float, ...]] = {
    2: (0.9, 0.1),
    3: (0.85, 0.10, 0.05),
    4: (0.80, 0.10, 0.06, 0.04),
}
#: u per level count when a rule set gives none: most non-matches disagree
DEFAULT_U: Mapping[int, tuple[float, ...]] = {
    2: (0.01, 0.99),
    3: (0.01, 0.04, 0.95),
    4: (0.01, 0.03, 0.06, 0.90),
}
_RANK = {Band.DISTINCT: 0, Band.REVIEW: 1, Band.AUTO: 2}


@dataclass(frozen=True, slots=True)
class CompiledRules:
    """One rule version, compiled once: weight tables, band edges in weight space, the hard rules."""

    rules: MatchRules
    prior_weight: float
    weight_tables: tuple[tuple[float, ...], ...]  # per comparison, per level
    upper_weight: float
    lower_weight: float
    hard: tuple[
        tuple[str, str, int], ...
    ]  # (kind, attribute, comparison index; -1 when no comparison reads it)
    schemes: Mapping[str, str] = field(
        default_factory=dict
    )  # hard-rule attribute -> its registered-ID scheme
    comparers: tuple[Comparer, ...] = ()  # per comparison: (left forms, right forms) -> level
    labels: tuple[tuple[str, ...], ...] = ()  # per comparison, per level

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.rules.comparisons)


def _clip(p: float) -> float:
    return min(max(float(p), P_MIN), P_MAX)


def default_m(spec: ComparisonSpec) -> tuple[float, ...]:
    return DEFAULT_M[level_count(spec)]


def default_u(spec: ComparisonSpec) -> tuple[float, ...]:
    return DEFAULT_U[level_count(spec)]


def weights(spec: ComparisonSpec) -> tuple[float, ...]:
    """log2(m_k / u_k) per level, m and u clipped to [1e-9, 1 - 1e-9]."""
    m = spec.m or default_m(spec)
    u = spec.u or default_u(spec)
    return tuple(math.log2(_clip(mk) / _clip(uk)) for mk, uk in zip(m, u, strict=True))


def edge_weight(percent: float) -> float:
    """The weight whose score is `percent`: log2(b / (100 − b))."""
    b = min(max(float(percent), 1e-12), 100.0 - 1e-12)
    return math.log2(b / (100.0 - b))


def compile_rules(rules: MatchRules, model: EntityModel) -> CompiledRules:
    """Once per rule version."""
    prior = _clip(rules.prior)
    hard: list[tuple[str, str, int]] = []
    schemes: dict[str, str] = {}
    for rule in rules.hard_rules:
        index = -1
        for position, spec in enumerate(rules.comparisons):
            if spec.attribute == rule.attribute and spec.comparator == "identifier":
                index = position
                break
        hard.append((rule.kind, rule.attribute, index))
        scheme = model.attribute(rule.attribute).scheme
        if scheme:
            schemes[rule.attribute] = scheme
    # cannot-link rules are evaluated first, whatever order the rule set lists them in
    hard.sort(key=lambda item: 0 if item[0] == "cannot_link" else 1)
    return CompiledRules(
        rules=rules,
        prior_weight=math.log2(prior / (1.0 - prior)),
        weight_tables=tuple(weights(spec) for spec in rules.comparisons),
        upper_weight=edge_weight(rules.bands.upper),
        lower_weight=edge_weight(rules.bands.lower),
        hard=tuple(hard),
        schemes=schemes,
        comparers=tuple(comparer(spec) for spec in rules.comparisons),
        labels=tuple(level_labels(spec) for spec in rules.comparisons),
    )


def _valid_values(ids: Sequence[RegisteredId], scheme: str) -> frozenset[str]:
    return frozenset(i.value for i in ids if i.scheme == scheme and i.valid is True)


def hard_rule(
    c: CompiledRules, left_ids: Sequence[RegisteredId], right_ids: Sequence[RegisteredId]
) -> str | None:
    """The hard rule that decides a pair, `"<kind>:<attribute>"`, or None.

    A rule fires only when both sides hold a valid ID of its scheme: cannot-link when the values differ,
    must-link when they are equal. An ID with no checksum (valid None) or a failing one never fires.
    """
    if not c.hard or not left_ids or not right_ids:
        return None
    for kind, attribute, _index in c.hard:
        scheme = c.schemes.get(attribute)
        if scheme is None:
            continue
        left = _valid_values(left_ids, scheme)
        right = _valid_values(right_ids, scheme)
        if not left or not right:
            continue
        if kind == "cannot_link" and left.isdisjoint(right):
            return f"cannot_link:{attribute}"
        if kind == "must_link" and not left.isdisjoint(right):
            return f"must_link:{attribute}"
    return None


def fast_weight(
    c: CompiledRules,
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    left_ids: Sequence[RegisteredId],
    right_ids: Sequence[RegisteredId],
) -> tuple[float, tuple[int, ...], str | None]:
    """(total weight, levels, hard rule) from the weight tables: no dataclass per pair."""
    levels = tuple(fn(left, right) for fn in c.comparers)
    weight = c.prior_weight
    for table, level in zip(c.weight_tables, levels, strict=True):
        if level >= 0:
            weight += table[level]
    return weight, levels, hard_rule(c, left_ids, right_ids)


def probability(weight: float) -> float:
    """The score of a weight as a percentage: 100 / (1 + 2 ** (-weight)), without overflow."""
    if weight < -60.0:
        return 100.0 * 2.0**weight
    return 100.0 / (1.0 + 2.0 ** (-weight))


def band_of(score: float, bands: Bands) -> Band:
    if score >= bands.upper:
        return Band.AUTO
    if score >= bands.lower:
        return Band.REVIEW
    return Band.DISTINCT


def band_of_weight(c: CompiledRules, weight: float) -> Band:
    """The band of a weight, compared against the band edges in weight space (the fast path's test)."""
    if weight >= c.upper_weight:
        return Band.AUTO
    if weight >= c.lower_weight:
        return Band.REVIEW
    return Band.DISTINCT


def _band_under_rule(hard: str | None, weight_band: Band) -> Band:
    if hard is None:
        return weight_band
    return Band.DISTINCT if hard.startswith("cannot_link:") else Band.AUTO


def _counterfactual_rule(c: CompiledRules, index: int, level: int) -> str | None:
    """The hard rule a comparison moved to `level` would fire, supposing both sides valid."""
    last = len(c.weight_tables[index]) - 1
    for kind, attribute, position in c.hard:
        if position != index:
            continue
        if kind == "cannot_link" and level == last:
            return f"cannot_link:{attribute}"
        if kind == "must_link" and level == 0:
            return f"must_link:{attribute}"
    return None


def counterfactuals(
    c: CompiledRules, levels: Sequence[int], weight: float, band: Band
) -> tuple[Counterfactual, ...]:
    """The smallest change of one comparison's level that raises the band, and the smallest that lowers it."""
    best: dict[str, tuple[float, Counterfactual]] = {}
    for index, (spec, table) in enumerate(zip(c.rules.comparisons, c.weight_tables, strict=True)):
        current = levels[index]
        now = table[current] if current >= 0 else 0.0
        for level in range(len(table)):
            if level == current:
                continue
            changed = weight - now + table[level]
            new_band = _band_under_rule(_counterfactual_rule(c, index, level), band_of_weight(c, changed))
            if _RANK[new_band] == _RANK[band]:
                continue
            direction = "up" if _RANK[new_band] > _RANK[band] else "down"
            distance = abs(changed - weight)
            held = best.get(direction)
            if held is not None and held[0] <= distance:
                continue
            best[direction] = (
                distance,
                Counterfactual(
                    comparison=spec.name,
                    from_level=current,
                    to_level=level,
                    to_label=c.labels[index][level],
                    score=probability(changed),
                    band=new_band,
                    direction=direction,
                ),
            )
    return tuple(best[d][1] for d in ("up", "down") if d in best)


def signature(c: CompiledRules, levels: Sequence[int]) -> str:
    """`given_name= · family_name≈ · phone∅ · …`: one mark per comparison, in rule order."""
    marks: list[str] = []
    for spec, table, level in zip(c.rules.comparisons, c.weight_tables, levels, strict=True):
        if level == LEVEL_NULL:
            mark = "∅"
        elif level == 0:
            mark = "="
        elif level == len(table) - 1:
            mark = "≠"
        else:
            mark = "≈"
        marks.append(f"{spec.name}{mark}")
    return " · ".join(marks)


def explain(c: CompiledRules, levels: tuple[int, ...], weight: float, hard_rule: str | None) -> Explanation:
    """The waterfall, band, counterfactuals and signature; `prior + Σ contributions == weight` within 1e-9."""
    contributions = tuple(
        Contribution(
            comparison=spec.name,
            level=level,
            label="null" if level == LEVEL_NULL else c.labels[index][level],
            weight=0.0 if level == LEVEL_NULL else c.weight_tables[index][level],
        )
        for index, (spec, level) in enumerate(zip(c.rules.comparisons, levels, strict=True))
    )
    band = _band_under_rule(hard_rule, band_of_weight(c, weight))
    return Explanation(
        prior=c.prior_weight,
        contributions=contributions,
        hard_rule=hard_rule,
        weight=weight,
        score=probability(weight),
        band=band,
        counterfactuals=() if hard_rule is not None else counterfactuals(c, levels, weight, band),
        signature=signature(c, levels),
        rule_version=c.rules.version,
    )


def score_pair(
    c: CompiledRules, left: StdRecord | SourceState, right: StdRecord | SourceState
) -> Explanation:
    """`fast_weight` then `explain`, for match tests and single pairs."""
    weight, levels, rule = fast_weight(c, left.match, right.match, left.ids, right.ids)
    return explain(c, levels, weight, rule)


def worth_explaining(c: CompiledRules, weight: float, rule: str | None) -> bool:
    """True for the pairs arrival explains and stores: at or above the lower band, or decided by a hard rule."""
    return rule is not None or weight >= c.lower_weight
