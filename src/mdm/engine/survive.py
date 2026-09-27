"""Survivorship: the golden record's values from its members' approved values (owner: ENGINE, B.9.7).

Per column attribute (references are relationships and never survived):
- a steward value pinned until after `now` wins outright (strategy `pin`);
- otherwise the candidates are the members with a non-empty value, plus an
  unpinned steward value as source `steward` with trust `rules.steward_rank`
  and recency `set_at`; an expired pin no longer competes;
- the attribute's strategies (else `rules.default`) filter in turn:
  `source_trust` keeps the lowest trust rank (`model.trust(system, attr)`),
  `recency` the latest `occurred_at` (then `landing_seq`), `completeness` the
  longest text / most filled group, `frequency` the value most members hold
  (compared folded); tie-break (system, key) ascending;
- repeating groups: `whole_group` takes the winner's list; `keyed_union`
  unions entries by `key`, each key resolved by the same strategies.
Provenance per attribute: `{"winner": {"source", "value"}, "runners_up": [...5],
"strategy": [...], "decided_by": code, "rule_version": n}`, where `decided_by` names what set the
winner apart when the value was committed: `pin`, `only` (one candidate held a value), `keyed_union`,
the first strategy whose key separates the winner from the runner-up, or `tie_break`. A held member contributes its approved
values; registry style: no values (provenance empty). Pure.

Successive filters followed by the tie-break choose the same winner as one sort
on the strategies' keys in order and then (system, key), so candidates are
ranked by that sort and the runners-up are the next five. A steward value is
source `steward:<attribute>`; on a recency tie it wins (a person chose it). An
attribute nobody holds is None in `values` and absent from the provenance. A
`keyed_union` group names the source that won the most entries as its winner
and lists every entry's source under `entries`; entries without a key are left
out.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from mdm.engine.standardise import fold
from mdm.models.canonical import canonical_json
from mdm.models.entity_model import Attribute, EntityModel, SurvivorshipRules
from mdm.models.errors import NotFound
from mdm.models.records import SourceKey, StewardValue

STEWARD = "steward"
RUNNERS_UP = 5
_UNKNOWN_TRUST = 1_000_000
_STEWARD_SEQ = 2**62  # a steward value wins a recency tie


@dataclass(frozen=True, slots=True)
class Member:
    source: SourceKey
    values: Mapping[
        str, Any
    ]  # the member's APPROVED values (SourceState.approved_values), never a held update's
    occurred_at: datetime
    landing_seq: int


@dataclass(frozen=True, slots=True)
class _Candidate:
    source: SourceKey
    value: Any
    trust: int
    when: datetime
    seq: int


def _empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _text_form(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (dict, list, tuple)):
        return canonical_json(value)
    return str(value)


def _filled(value: Any) -> int:
    """Completeness: filled fields of a group (entries' non-empty fields), else the length of the text form."""
    if isinstance(value, (list, tuple)):
        return sum(
            sum(1 for field in entry.values() if not _empty(field)) if isinstance(entry, Mapping) else 1
            for entry in value
        )
    if isinstance(value, Mapping):
        return sum(1 for field in value.values() if not _empty(field))
    return len(_text_form(value))


def _folded(value: Any) -> str:
    return fold(_text_form(value))


def _trust(model: EntityModel, system: str, attribute: str) -> int:
    try:
        return model.trust(system, attribute)
    except NotFound:
        return _UNKNOWN_TRUST


def _strategy_keys(
    candidates: Sequence[_Candidate], strategies: Sequence[str]
) -> list[tuple[str, Callable[[_Candidate], Any]]]:
    """(strategy, its sort key) for each strategy survivorship knows, in order."""
    frequency = Counter(_folded(c.value) for c in candidates) if "frequency" in strategies else Counter()
    keys: list[tuple[str, Callable[[_Candidate], Any]]] = []
    for strategy in strategies:
        if strategy == "source_trust":
            keys.append((strategy, lambda c: c.trust))
        elif strategy == "recency":
            keys.append((strategy, lambda c: (-c.when.timestamp(), -c.seq)))
        elif strategy == "completeness":
            keys.append((strategy, lambda c: -_filled(c.value)))
        elif strategy == "frequency":
            keys.append((strategy, lambda c: -frequency[_folded(c.value)]))
    return keys


def _ranked(candidates: Sequence[_Candidate], strategies: Sequence[str]) -> list[_Candidate]:
    """Candidates best first: the strategies' keys in order, then (system, key)."""
    keys = [key for _, key in _strategy_keys(candidates, strategies)]
    return sorted(candidates, key=lambda c: (*(key(c) for key in keys), c.source.system, c.source.key))


def _decided_by(ranked: Sequence[_Candidate], strategies: Sequence[str]) -> str:
    """The strategy that set the winner apart: `only` when one candidate holds a value, else the first
    strategy whose key separates the winner from the runner-up, else `tie_break` (the source key order)."""
    if len(ranked) < 2:
        return "only"
    first, second = ranked[0], ranked[1]
    for strategy, key in _strategy_keys(ranked, strategies):
        if key(first) != key(second):
            return strategy
    return "tie_break"


def _provenance_entry(source: SourceKey, value: Any) -> dict[str, Any]:
    return {"source": source.text(), "value": value}


def _candidates(
    model: EntityModel,
    rules: SurvivorshipRules,
    attribute: Attribute,
    members: Sequence[Member],
    steward: StewardValue | None,
    value_of: Callable[[Member], Any],
) -> list[_Candidate]:
    out = [
        _Candidate(
            source=member.source,
            value=value,
            trust=_trust(model, member.source.system, attribute.name),
            when=_aware(member.occurred_at),
            seq=member.landing_seq,
        )
        for member in members
        if not _empty(value := value_of(member))
    ]
    if steward is not None and steward.pinned_until is None and not _empty(steward.value):
        out.append(
            _Candidate(
                source=SourceKey(STEWARD, attribute.name),
                value=steward.value,
                trust=rules.steward_rank,
                when=_aware(steward.set_at),
                seq=_STEWARD_SEQ,
            )
        )
    return out


def _keyed_union(
    ranked_members: Sequence[_Candidate], strategies: Sequence[str], key_field: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], SourceKey]:
    """(entries, their sources, the source that won the most entries) of a keyed union."""
    by_key: dict[str, list[_Candidate]] = {}
    for candidate in ranked_members:
        for entry in candidate.value if isinstance(candidate.value, (list, tuple)) else ():
            if not isinstance(entry, Mapping) or _empty(entry.get(key_field)):
                continue
            by_key.setdefault(_folded(entry.get(key_field)), []).append(
                _Candidate(candidate.source, dict(entry), candidate.trust, candidate.when, candidate.seq)
            )
    entries: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    wins: Counter[SourceKey] = Counter()
    for key in sorted(by_key):
        winner = _ranked(by_key[key], strategies)[0]
        entries.append(winner.value)
        sources.append({"key": key, "source": winner.source.text()})
        wins[winner.source] += 1
    if not wins:
        # No entry carries its key: nothing to union, and the caller falls back to the plain winner.
        return [], [], ranked_members[0].source
    top = min(wins, key=lambda source: (-wins[source], source.system, source.key))
    return entries, sources, top


def survive(
    model: EntityModel,
    rules: SurvivorshipRules,
    members: Sequence[Member],
    steward: Mapping[str, StewardValue],
    now: datetime,
    rule_version: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """(values, provenance) of one golden record."""
    if model.style == "registry":
        return {}, {}
    now = _aware(now)
    values: dict[str, Any] = {}
    provenance: dict[str, Any] = {}
    for attribute in model.column_attributes():
        name = attribute.name
        strategies = rules.strategies(name)
        held = steward.get(name)
        pinned = held is not None and held.pinned_until is not None and _aware(held.pinned_until) > now
        candidates = _candidates(
            model, rules, attribute, members, None if pinned else held, lambda m, n=name: m.values.get(n)
        )
        ranked = _ranked(candidates, strategies)
        if pinned and held is not None:
            values[name] = held.value
            provenance[name] = {
                "winner": _provenance_entry(SourceKey(STEWARD, name), held.value),
                "runners_up": [_provenance_entry(c.source, c.value) for c in ranked[:RUNNERS_UP]],
                "strategy": ["pin"],
                "decided_by": "pin",
                "rule_version": rule_version,
            }
            continue
        if not ranked:
            values[name] = None
            continue
        spec = rules.attributes.get(name)
        if attribute.repeating and spec is not None and spec.group == "keyed_union" and attribute.key:
            entries, sources, top = _keyed_union(ranked, strategies, attribute.key)
            if entries:
                values[name] = entries
                provenance[name] = {
                    "winner": _provenance_entry(top, entries),
                    "runners_up": [_provenance_entry(c.source, c.value) for c in ranked if c.source != top][
                        :RUNNERS_UP
                    ],
                    "strategy": list(strategies),
                    "group": "keyed_union",
                    "entries": sources,
                    "decided_by": "keyed_union",
                    "rule_version": rule_version,
                }
                continue
        winner = ranked[0]
        values[name] = winner.value
        provenance[name] = {
            "winner": _provenance_entry(winner.source, winner.value),
            "runners_up": [_provenance_entry(c.source, c.value) for c in ranked[1 : 1 + RUNNERS_UP]],
            "strategy": list(strategies),
            "decided_by": _decided_by(ranked, strategies),
            "rule_version": rule_version,
        }
    return values, provenance
