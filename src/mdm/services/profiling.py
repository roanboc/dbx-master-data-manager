"""Profiling source records (owner: SERVICES, B.10): counts, patterns, masked top values."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

import mdm.capacity as capacity
from mdm.backend.store import SqlStore
from mdm.models.authority import Actor
from mdm.models.canonical import canonical_json
from mdm.services.authority import require
from mdm.services.privacy import MASK
from mdm.services.registry import ModelRegistry

TOP_VALUES = 10
PATTERN_LENGTH = 40
_LOWER = re.compile(r"[^\W\d_]", re.UNICODE)


@dataclass(frozen=True, slots=True)
class AttributeProfile:
    name: str
    filled: int
    empty: int
    distinct: int
    distinct_capped: bool
    top: tuple[tuple[str, int], ...]  # personal values masked
    patterns: tuple[tuple[str, int], ...]  # letters -> A/a, digits -> 9
    min_length: int | None
    max_length: int | None


@dataclass(frozen=True, slots=True)
class Profile:
    entity: str
    source_system: str | None
    records: int
    attributes: tuple[AttributeProfile, ...]


def value_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return canonical_json(value)


def pattern_of(text: str) -> str:
    """Letters -> A (upper) or a (lower), digits -> 9, anything else kept; at most PATTERN_LENGTH characters."""
    out = []
    for char in text[:PATTERN_LENGTH]:
        if char.isdigit():
            out.append("9")
        elif _LOWER.match(char):
            out.append("A" if char.isupper() else "a")
        else:
            out.append(char)
    return "".join(out) + ("…" if len(text) > PATTERN_LENGTH else "")


class _Tally:
    """Counts for one attribute. A personal value is counted by its digest, so the distinct count is true while
    the tally holds no personal value; its top values are the masked forms."""

    def __init__(self) -> None:
        self.filled = 0
        self.empty = 0
        self.values: Counter[str] = Counter()
        self.shown: Counter[str] = Counter()
        self.capped = False
        self.patterns: Counter[str] = Counter()
        self.min_length: int | None = None
        self.max_length: int | None = None

    def add(self, value: Any, personal: bool) -> None:
        if value is None or value == "" or value == [] or value == {}:
            self.empty += 1
            return
        self.filled += 1
        text = value_text(value)
        counted = hashlib.sha256(text.encode("utf-8")).hexdigest() if personal else text
        if counted in self.values or len(self.values) < capacity.PROFILE_DISTINCT_CAP:
            self.values[counted] += 1
        else:
            self.capped = True
        if personal:
            self.shown[text[:1] + MASK if isinstance(value, str) else MASK] += 1
        if len(self.patterns) < capacity.PROFILE_DISTINCT_CAP or pattern_of(text) in self.patterns:
            self.patterns[pattern_of(text)] += 1
        length = len(text)
        self.min_length = length if self.min_length is None else min(self.min_length, length)
        self.max_length = length if self.max_length is None else max(self.max_length, length)


def _top(counter: Counter[str]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_VALUES])


def _top_values(tally: _Tally, personal: bool) -> tuple[tuple[str, int], ...]:
    return _top(tally.shown if personal else tally.values)


class ProfileService:
    def __init__(self, store: SqlStore, registry: ModelRegistry) -> None:
        self.store = store
        self.registry = registry

    def profile(self, entity: str, source_system: str | None = None, *, actor: Actor) -> Profile:
        """Pages source_state; requires `profile`. Active records only; personal top values masked (the
        distinct count of a personal attribute counts its values, never shown)."""
        require(actor, "profile")
        model = self.registry.published(entity)
        if source_system is not None:
            model.source(source_system)  # NotFound for a source the model does not name
        attributes = model.column_attributes()
        tallies = {a.name: _Tally() for a in attributes}
        records = 0
        for page in capacity.pages(
            lambda after, limit: self.store.states_page(entity, source_system, after, limit),
            key=lambda state: state.source,
        ):
            for state in page:
                if state.status != "active":
                    continue
                records += 1
                for attribute in attributes:
                    tallies[attribute.name].add(state.values.get(attribute.name), attribute.personal)
        return Profile(
            entity=entity,
            source_system=source_system,
            records=records,
            attributes=tuple(
                AttributeProfile(
                    name=a.name,
                    filled=tallies[a.name].filled,
                    empty=tallies[a.name].empty,
                    distinct=len(tallies[a.name].values),
                    distinct_capped=tallies[a.name].capped,
                    top=_top_values(tallies[a.name], a.personal),
                    patterns=_top(tallies[a.name].patterns),
                    min_length=tallies[a.name].min_length,
                    max_length=tallies[a.name].max_length,
                )
                for a in attributes
            ),
        )
