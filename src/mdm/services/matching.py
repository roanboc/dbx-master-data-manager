"""Candidates and golden candidates, and the match test (owner: SERVICES, B.10).

Candidate retrieval: a record's blocking keys; keys shared by more than
`STOP_KEY_RECORDS` records dropped (stop keys); the records holding the rest;
at most `MAX_CANDIDATES` per record, ranked by the passes they share; pairs
deduplicated before scoring; `fast_weight` for every pair and `explain` only
for pairs at or above the lower band or decided by a hard rule.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import mdm.capacity as capacity
from mdm.backend.store import SqlStore
from mdm.engine.blocking import keys_from_forms, rank_candidates
from mdm.engine.score import CompiledRules, explain, fast_weight, worth_explaining
from mdm.engine.standardise import standardise_record
from mdm.models.authority import Actor
from mdm.models.canonical import utcnow
from mdm.models.entity_model import EntityModel
from mdm.models.errors import NotFound
from mdm.models.match import GoldenCandidate, PairScore
from mdm.models.records import RegisteredId, SourceChange, SourceKey, SourceState, StdRecord
from mdm.models.safety import safe_detail
from mdm.services.authority import require
from mdm.services.registry import ModelRegistry
from mdm.services.support import token

#: the source key a match-test record takes; it is never stored
TEST_KEY = "match-test"


class Scorable(Protocol):
    source: SourceKey
    match: Mapping[str, Any]
    ids: tuple[RegisteredId, ...]


@dataclass
class Search:
    """The candidates of a set of records: explained pairs per record (left = the record), the candidate states
    read, and how many records had candidates capped."""

    pairs: dict[SourceKey, list[PairScore]] = field(default_factory=dict)
    states: dict[SourceKey, SourceState] = field(default_factory=dict)
    capped: int = 0


def strong_ids_of(model: EntityModel, ids: Sequence[RegisteredId]) -> frozenset[tuple[str, str]]:
    """(scheme, value) of the valid identifiers of the model's strong attributes."""
    schemes = {a.scheme for a in model.strong_attributes() if a.scheme}
    return frozenset((i.scheme, i.value) for i in ids if i.valid is True and i.scheme in schemes)


def cannot_link_schemes(model: EntityModel) -> dict[str, str]:
    """Scheme -> attribute, for the strong attributes a cannot-link hard rule names."""
    named = {rule.attribute for rule in model.match.hard_rules if rule.kind == "cannot_link"}
    return {a.scheme: a.name for a in model.strong_attributes() if a.name in named and a.scheme}


def conflict(
    model: EntityModel, left: frozenset[tuple[str, str]], right: frozenset[tuple[str, str]]
) -> str | None:
    """`cannot_link:<attribute>` when both hold a valid ID of a cannot-link scheme and the values differ."""
    schemes = cannot_link_schemes(model)
    for scheme, attribute in sorted(schemes.items()):
        mine = {v for s, v in left if s == scheme}
        theirs = {v for s, v in right if s == scheme}
        if mine and theirs and mine.isdisjoint(theirs):
            return f"cannot_link:{attribute}"
    return None


def _oriented(pair: PairScore, record: SourceKey) -> PairScore:
    if pair.left == record:
        return pair
    return PairScore(record, pair.left, pair.explanation)


class MatchService:
    def __init__(self, store: SqlStore, registry: ModelRegistry) -> None:
        self.store = store
        self.registry = registry

    # ------------------------------------------------------------------ candidates

    def search(
        self,
        model: EntityModel,
        rules: CompiledRules,
        records: Sequence[Scorable],
        *,
        explain_all: bool = False,
    ) -> Search:
        """Candidates of `records` from the stored blocking keys, scored and explained (see the module text).

        `explain_all` explains every pair (the match test shows distinct candidates too).
        """
        entity = model.entity
        out = Search()
        if not records:
            return out
        own = {r.source: r for r in records}
        keys_of = {r.source: keys_from_forms(rules.rules, r.match, r.ids) for r in records}
        wanted = sorted({(p, k) for keys in keys_of.values() for p, ks in keys.items() for k in ks})
        counts = self.store.key_counts(entity, wanted) if wanted else {}
        kept = [pk for pk in wanted if 0 < counts.get(pk, 0) <= capacity.STOP_KEY_RECORDS]
        holders: dict[tuple[str, str], list[SourceKey]] = {}
        if kept:
            limit = sum(counts[pk] for pk in kept) + 1
            for pass_name, key, source in self.store.records_with_keys(entity, kept, limit):
                holders.setdefault((pass_name, key), []).append(source)
        chosen: dict[SourceKey, list[SourceKey]] = {}
        for source, keys in keys_of.items():
            shared: Counter[SourceKey] = Counter()
            for pass_name, values in keys.items():
                for value in values:
                    for other in holders.get((pass_name, value), ()):
                        if other != source:
                            shared[other] += 1
            candidates, dropped = rank_candidates(shared, capacity.MAX_CANDIDATES)
            if dropped:
                out.capped += 1
            chosen[source] = candidates
        needed = sorted({c for cs in chosen.values() for c in cs if c not in own})
        if needed:
            loaded = self.store.source_states(entity, needed)
            out.states.update({s: st for s, st in loaded.items() if st.status == "active"})
        unique: set[tuple[SourceKey, SourceKey]] = set()
        for source, candidates in chosen.items():
            for other in candidates:
                if other in own or other in out.states:
                    unique.add((source, other) if source < other else (other, source))
        by_record: dict[SourceKey, list[PairScore]] = {s: [] for s in own}
        for left_key, right_key in sorted(unique):
            left = own.get(left_key) or out.states[left_key]
            right = own.get(right_key) or out.states[right_key]
            weight, levels, rule = fast_weight(rules, left.match, right.match, left.ids, right.ids)
            if not (explain_all or worth_explaining(rules, weight, rule)):
                continue
            pair = PairScore(left_key, right_key, explain(rules, levels, weight, rule))
            for end in (left_key, right_key):
                if end in by_record:
                    by_record[end].append(_oriented(pair, end))
        out.pairs = by_record
        return out

    def candidates(
        self, model: EntityModel, rules: CompiledRules, records: Sequence[StdRecord]
    ) -> dict[SourceKey, list[PairScore]]:
        """Stop keys dropped, `records_with_keys`, pairs deduplicated, capped by passes shared; pairs at or
        above the lower band (or decided by a hard rule) explained."""
        return self.search(model, rules, records).pairs

    def golden_candidates(
        self,
        entity: str,
        pairs: Mapping[SourceKey, Sequence[PairScore]],
        strong_ids: Mapping[SourceKey, frozenset[tuple[str, str]]],
        *,
        states: Mapping[SourceKey, SourceState] | None = None,
        linked: Mapping[SourceKey, str] | None = None,
        declined: Mapping[SourceKey, frozenset[str]] | None = None,
    ) -> dict[SourceKey, list[GoldenCandidate]]:
        """Best member per golden record; `blocked_by` when the record's valid strong IDs conflict with any
        active member's (at most MAX_MEMBERS_CHECKED per golden record).

        `states` are member states already read; `linked` the active cross-references already read;
        `declined` the golden records a steward said each record is not (decision 22), which are never its
        candidates.
        """
        model = self.registry.published(entity)
        others = sorted({p.right for ps in pairs.values() for p in ps})
        known = dict(linked or {})
        missing = [s for s in others if s not in known]
        if missing:
            known.update(self.store.xrefs_for_sources(entity, missing))
        grouped: dict[SourceKey, dict[str, list[PairScore]]] = {}
        refused = declined or {}
        for record, found in pairs.items():
            not_these = refused.get(record, frozenset())
            for pair in found:
                master = known.get(pair.right)
                if master is not None and master not in not_these:
                    grouped.setdefault(record, {}).setdefault(master, []).append(pair)
        masters = sorted({m for by_master in grouped.values() for m in by_master})
        member_ids: dict[str, frozenset[tuple[str, str]]] = {}
        if masters and cannot_link_schemes(model):
            members = self.store.members(entity, masters, capacity.MAX_MEMBERS_CHECKED)
            cache = dict(states or {})
            unread = sorted({s for ms in members.values() for s in ms if s not in cache})
            if unread:
                cache.update(self.store.source_states(entity, unread))
            for master, sources in members.items():
                member_ids[master] = frozenset(
                    i for s in sources if s in cache for i in cache[s].strong_ids(model)
                )
        out: dict[SourceKey, list[GoldenCandidate]] = {}
        for record, by_master in grouped.items():
            mine = strong_ids.get(record, frozenset())
            found: list[GoldenCandidate] = []
            for master, scored in sorted(by_master.items()):
                best = min(scored, key=lambda p: (-p.explanation.score, p.right))
                blocked = conflict(model, mine, member_ids.get(master, frozenset())) if mine else None
                found.append(GoldenCandidate(master, best, len(scored), blocked))
            found.sort(key=lambda g: (-g.best.explanation.score, g.master_id))
            out[record] = found
        return out

    def blocked_by(self, entity: str, record: SourceState, master_id: str) -> str | None:
        """`cannot_link:<attribute>` when a cannot-link rule holds between `record` and an active member of
        `master_id` (at most MAX_MEMBERS_CHECKED), as `golden_candidates` finds it; None otherwise."""
        model = self.registry.published(entity)
        if not cannot_link_schemes(model):
            return None
        mine = strong_ids_of(model, record.ids)
        if not mine:
            return None
        members = self.store.members(entity, [master_id], capacity.MAX_MEMBERS_CHECKED).get(master_id, [])
        others = [s for s in members if s != record.source]
        states = self.store.source_states(entity, others) if others else {}
        theirs = frozenset(i for s in states.values() for i in s.strong_ids(model))
        return conflict(model, mine, theirs)

    def explain_pair(
        self, entity: str, left: SourceState, right: SourceState, rules: CompiledRules | None = None
    ):
        """The explanation of one pair of source records, as arrival scores it (the waterfall of a task that
        names two golden records)."""
        compiled = rules if rules is not None else self.registry.compiled(entity)
        first, second = (left, right) if left.source < right.source else (right, left)
        weight, levels, rule = fast_weight(compiled, first.match, second.match, first.ids, second.ids)
        return explain(compiled, levels, weight, rule)

    def masters_conflict(self, entity: str, left: str, right: str) -> str | None:
        """`cannot_link:<attribute>` when a cannot-link rule holds between an active member of one golden
        record and one of the other (at most MAX_MEMBERS_CHECKED each); None otherwise."""
        model = self.registry.published(entity)
        if not cannot_link_schemes(model):
            return None
        members = self.store.members(entity, [left, right], capacity.MAX_MEMBERS_CHECKED)
        wanted = sorted({s for ms in members.values() for s in ms})
        states = self.store.source_states(entity, wanted) if wanted else {}

        def ids_of(master: str) -> frozenset[tuple[str, str]]:
            return frozenset(
                i for s in members.get(master, []) if s in states for i in states[s].strong_ids(model)
            )

        return conflict(model, ids_of(left), ids_of(right))

    # ------------------------------------------------------------------ the match test

    def match_test(
        self,
        entity: str,
        *,
        actor: Actor,
        payload: Mapping[str, Any] | None = None,
        source: SourceKey | None = None,
        rules_version: int | None = None,
        top: int = 5,
        clock: Any = utcnow,
    ) -> list[GoldenCandidate]:
        """Scores a record (a payload, or a stored source record) against the golden records.

        Requires `match_test`: each comparison's level could tell a masked value to someone who varies the
        record, so a consumer may not, and every call writes an access-log row (it changes nothing else).
        A payload is standardised as its `source_system` key names (else the model's first source); it may
        carry the keys of the landing payload. Returns the best `top` golden candidates, best first.
        """
        require(actor, "match_test")
        model = self.registry.published(entity)
        rules = self.registry.compiled(entity, rules_version)
        top = capacity.require_limit(top, 100)
        record: Scorable
        if source is not None:
            state = self.store.source_states(entity, [source]).get(source)
            if state is None:
                raise NotFound("unknown_source_record", entity=entity, source=token(source.text()))
            record = state
        elif payload is not None:
            system = payload.get("source_system") if isinstance(payload.get("source_system"), str) else None
            spec = model.source(system) if system and model.has_source(system) else model.sources[0]
            now: datetime = clock()
            change = SourceChange(
                event_id=TEST_KEY,
                source_system=spec.system,
                source_key=TEST_KEY,
                entity=entity,
                op="upsert",
                occurred_at=now,
                source_version=None,
                initial_load=False,
                payload={k: v for k, v in payload.items() if k != "source_system"},
                landed_at=now,
                landing_seq=0,
            )
            record = standardise_record(model, spec, change)
        else:
            raise NotFound("nothing_to_match", entity=entity)
        self.store.append_access(
            actor,
            "match_test",
            entity,
            None,
            None,
            "match_test",
            safe_detail(top=top, stored=source is not None, rules_version=rules.rules.version),
        )
        search = self.search(model, rules, [record], explain_all=True)
        pairs = {record.source: [p for p in search.pairs.get(record.source, []) if p.right != record.source]}
        golden = self.golden_candidates(
            entity,
            pairs,
            {record.source: strong_ids_of(model, record.ids)},
            states=search.states,
        )
        return golden.get(record.source, [])[:top]
