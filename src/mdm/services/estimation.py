"""Weight estimation as a service: samples from the store, the B.9.5 procedure, a draft rule set
(owner: SERVICES, B.10).

1. Seeds: the first `sample_records` active states by (sample_hash, source) —
   identical on both engines.
2. Pairs per pass: the seeds' candidates from the full blocking-key table
   (stop keys dropped), deduplicated, at most `EM_MAX_PAIRS` per pass: a pass
   with more keeps those lowest by a hash of the pair, not by key order, and
   each kept pair counts for the pairs it stands for in step 5.
3. u: random pairs by hash for the non-exact levels; value frequencies for the
   exact levels.
4. m: from equal valid strong IDs when there are at least `MIN_ID_PAIRS` such
   pairs (EM per pass for the ID comparison only); otherwise EM per pass with
   that pass's key comparisons fixed, combined.
5. The prior: the expected matches of the union of the passes' pairs, scaled
   from the seeds to the population. Each pass's in-block proportion is
   recorded beside it, never used as the prior.
6. A pass that did not converge raises `EstimationError` and nothing is saved.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from typing import Any

import mdm.capacity as capacity
from mdm.backend.store import SqlStore
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
    pair_levels,
    scale_to_population,
    u_pairs,
    union_expected_matches,
    value_frequencies,
)
from mdm.models.authority import Actor
from mdm.models.entity_model import EntityModel, MatchRules
from mdm.models.errors import EstimationError, MdmError
from mdm.models.records import SourceKey, SourceState
from mdm.services.authority import require
from mdm.services.registry import ModelRegistry
from mdm.services.support import token

METHODS = ("auto", "em", "identifier")
_HASH_SPACE = float(2**63)


def _finite(value: float) -> float | None:
    return value if math.isfinite(value) else None


class EstimationService:
    def __init__(self, store: SqlStore, registry: ModelRegistry) -> None:
        self.store = store
        self.registry = registry

    def estimate(
        self,
        entity: str,
        *,
        actor: Actor,
        method: str = "auto",
        sample_records: int = capacity.EM_SAMPLE_RECORDS,
        u_pairs: int = capacity.U_SAMPLE_PAIRS,
    ) -> tuple[int, MatchRules]:
        """(draft version, the estimated rules). Requires `estimate`.

        method: auto (identifier when enough strong-ID pairs, else em) | em | identifier.
        `EstimationError` when a pass did not converge; nothing is saved then.
        """
        require(actor, "estimate")
        if method not in METHODS:
            raise MdmError("unknown_method", method=token(method))
        sample_records = capacity.require_limit(sample_records, capacity.EM_SAMPLE_RECORDS * 10)
        u_count = capacity.require_limit(u_pairs, capacity.U_SAMPLE_PAIRS * 10)
        model = self.registry.published(entity)
        rules = model.match
        ordered, population = self._by_hash(
            entity, max(sample_records, 2 * u_count, capacity.FREQUENCY_SAMPLE_RECORDS)
        )
        seeds = ordered[:sample_records]
        passes, id_pairs, weights = self._pairs(model, seeds)
        u = estimate_u(
            rules,
            u_pairs_of(ordered[: 2 * u_count], u_count),
            value_frequencies(ordered[: capacity.FREQUENCY_SAMPLE_RECORDS], rules),
        )
        fixed = {name: set(found) for name, found in fixed_comparisons(rules).items()}
        id_comparison = self._id_comparison(model)
        if method == "identifier" and id_comparison is None:
            raise EstimationError("no_strong_identifier", entity=entity)
        use_id = id_comparison is not None and (
            method == "identifier" or (method == "auto" and id_pairs >= capacity.MIN_ID_PAIRS)
        )
        union = self._union(passes)
        results: dict[str, EMResult] = {}
        if use_id and id_comparison is not None:
            m = dict(m_from_identifier(union, rules, id_comparison))
            # the other comparisons' m are known from the identifier: EM reads them and updates the ID's m only
            used = {name: fixed.get(name, set()) for name in passes}
            for name, levels in sorted(passes.items()):
                results[name] = em(levels, rules, u, fixed=used[name], known=m)
            combined = combine_passes(results, used)
            if id_comparison in combined:
                m[id_comparison] = combined[id_comparison]
        else:
            used = {name: fixed.get(name, set()) for name in passes}
            for name, levels in sorted(passes.items()):
                results[name] = em(levels, rules, u, fixed=used[name])
            m = dict(combine_passes(results, used))
        for name, result in sorted(results.items()):
            if not result.converged:
                raise EstimationError(
                    "em_not_converged",
                    entity=entity,
                    blocking_pass=token(name),
                    iterations=result.iterations,
                    max_delta=_finite(result.max_delta),
                )
        expected = union_expected_matches(passes, results, rules, u, used, weights)
        scaled = scale_to_population(expected, len(seeds), population)
        prior = global_prior(scaled, population)
        meta: dict[str, Any] = {
            "method": "identifier" if use_id else "em",
            "seeds": len(seeds),
            "records": population,
            "u_pairs": min(u_count, len(ordered) // 2),
            "id_pairs": id_pairs,
            "expected_matches": _finite(scaled),
            "passes": {
                name: {
                    "pairs": result.pairs,
                    "in_block": _finite(result.lam),
                    "iterations": result.iterations,
                    "converged": result.converged,
                    "max_delta": _finite(result.max_delta),
                    "expected_matches": _finite(result.expected_matches),
                }
                for name, result in sorted(results.items())
            },
            "from_version": rules.version,
        }
        drafted = estimated_rules(rules, m, u, prior, meta, rules.version)
        version = self.registry.save_match_draft(entity, drafted, actor)
        return version, self.registry.match_rules(entity, version)

    # ------------------------------------------------------------------ samples

    def _by_hash(self, entity: str, wanted: int) -> tuple[list[SourceState], int]:
        """Up to `wanted` active states by (sample_hash, source), and the population: exact when every state was
        read, else estimated from the share of the hash space the states read cover."""
        out: list[SourceState] = []
        after: tuple[int, str, str] | None = None
        exhausted = False
        while len(out) < wanted:
            limit = min(capacity.READ_PAGE, wanted - len(out))
            page = self.store.states_by_sample_hash(entity, after, limit)
            out.extend(page)
            if len(page) < limit:
                exhausted = True
                break
            last = page[-1]
            after = (last.sample_hash, last.source.system, last.source.key)
        if exhausted or not out:
            return out, len(out)
        covered = (out[-1].sample_hash + 1) / _HASH_SPACE
        return out, max(len(out), int(round(len(out) / covered)))

    @staticmethod
    def _id_comparison(model: EntityModel) -> str | None:
        strong = {a.name for a in model.strong_attributes()}
        for spec in model.match.comparisons:
            if spec.comparator == "identifier" and spec.attribute in strong:
                return spec.name
        return None

    def _pairs(
        self, model: EntityModel, seeds: Sequence[SourceState]
    ) -> tuple[dict[str, list[Levels]], int, dict[tuple[SourceKey, SourceKey], float]]:
        """Pass -> the level patterns of its candidate pairs (seed and any record sharing a key), the number
        of distinct pairs with an equal valid strong ID, and the weight of each kept pair a capped pass
        stood for (absent when 1).

        A pass over `EM_MAX_PAIRS` keeps the pairs lowest by `pair_hash`, so the kept ones are a uniform
        sample whatever the source keys; the hash of its last kept pair is the share it kept. A pair is in
        the union when any pass that found it kept it, so its weight is 1 over the largest share among
        those passes (Horvitz–Thompson)."""
        entity = model.entity
        rules = model.match
        schemes = [a.scheme for a in model.strong_attributes() if a.scheme]
        keys_of = self.store.blocking_keys_of(entity, [s.source for s in seeds]) if seeds else {}
        states: dict[SourceKey, SourceState] = {s.source: s for s in seeds}
        found: dict[str, list[tuple[SourceKey, SourceKey]]] = {}
        every: dict[str, set[tuple[SourceKey, SourceKey]]] = {}
        share: dict[str, float] = {}
        for blocking in rules.blocking:
            wanted = sorted(
                {(blocking.name, k) for s in seeds for k in keys_of.get(s.source, {}).get(blocking.name, [])}
            )
            counts = self.store.key_counts(entity, wanted) if wanted else {}
            kept = [pk for pk in wanted if 0 < counts.get(pk, 0) <= capacity.STOP_KEY_RECORDS]
            holders: dict[tuple[str, str], list[SourceKey]] = {}
            if kept:
                limit = sum(counts[pk] for pk in kept) + 1
                for pass_name, key, source in self.store.records_with_keys(entity, kept, limit):
                    holders.setdefault((pass_name, key), []).append(source)
            pairs: set[tuple[SourceKey, SourceKey]] = set()
            for seed in seeds:
                for key in keys_of.get(seed.source, {}).get(blocking.name, []):
                    for other in holders.get((blocking.name, key), []):
                        if other != seed.source:
                            pairs.add((seed.source, other) if seed.source < other else (other, seed.source))
            if len(pairs) > capacity.EM_MAX_PAIRS:
                kept = sorted(pairs, key=pair_hash)[: capacity.EM_MAX_PAIRS]
                share[blocking.name] = pair_hash(kept[-1])
                every[blocking.name] = pairs
                found[blocking.name] = sorted(kept)
            else:
                found[blocking.name] = sorted(pairs)
        missing = sorted({s for pairs in found.values() for pair in pairs for s in pair if s not in states})
        if missing:
            states.update(self.store.source_states(entity, missing))
        passes: dict[str, list[Levels]] = {}
        strong_equal: set[tuple[SourceKey, SourceKey]] = set()
        for name, pairs in found.items():
            levels: list[Levels] = []
            for left, right in pairs:
                a, b = states.get(left), states.get(right)
                if a is None or b is None or a.status != "active" or b.status != "active":
                    continue
                pattern = pair_levels(rules, a, b, schemes)
                levels.append(pattern)
                if pattern.strong_equal:
                    strong_equal.add((pattern.left, pattern.right))
            passes[name] = levels
        weights: dict[tuple[SourceKey, SourceKey], float] = {}
        if share:
            members = {name: every.get(name) or set(pairs) for name, pairs in found.items()}
            for pairs in found.values():
                for pair in pairs:
                    rate = max(share.get(name, 1.0) for name in found if pair in members[name])
                    if rate < 1.0:
                        weights[pair] = 1.0 / max(rate, 1e-12)
        return passes, len(strong_equal), weights

    @staticmethod
    def _union(passes: Mapping[str, Sequence[Levels]]) -> list[Levels]:
        seen: dict[tuple[SourceKey, SourceKey], Levels] = {}
        for name in sorted(passes):
            for pattern in passes[name]:
                seen.setdefault((pattern.left, pattern.right), pattern)
        return [seen[k] for k in sorted(seen)]


def pair_hash(pair: tuple[SourceKey, SourceKey]) -> float:
    """A pair's place in [0, 1), the same on every run and engine: a uniform order that ignores the keys'
    own order."""
    left, right = pair
    text = f"{left.system}\x1f{left.key}\x1e{right.system}\x1f{right.key}"
    return int.from_bytes(hashlib.blake2b(text.encode(), digest_size=8).digest(), "big") / 2.0**64


def u_pairs_of(records: Sequence[SourceState], n: int) -> list[tuple[SourceState, SourceState]]:
    """`engine.estimate.u_pairs` (the name is taken by the parameter of `estimate`)."""
    return u_pairs(records, n)
