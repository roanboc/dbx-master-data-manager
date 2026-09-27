"""Records as they move through the hub: landed, standardised, stored, published.

Column names in `mdm_work.source_state` are `std_values`, `match_forms` and
`refs`; `SourceState.values`, `.match` and `.references` map to them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from mdm.models.entity_model import EntityModel


@dataclass(frozen=True, slots=True, order=True)
class SourceKey:
    """One record in one source system; orders by (system, key)."""

    system: str
    key: str

    def text(self) -> str:
        return f"{self.system}:{self.key}"

    @classmethod
    def from_text(cls, text: str) -> SourceKey:
        """The inverse of `text()`: `crm:C000123` -> SourceKey("crm", "C000123")."""
        system, sep, key = text.partition(":")
        if not sep or not system or not key:
            raise ValueError("not a source key")
        return cls(system, key)


@dataclass(frozen=True, slots=True)
class SourceChange:
    """One landing row, read."""

    event_id: str
    source_system: str
    source_key: str
    entity: str
    op: str  # upsert | delete
    occurred_at: datetime
    source_version: int | None
    initial_load: bool
    payload: Mapping[str, Any]
    landed_at: datetime
    landing_seq: int

    @property
    def source(self) -> SourceKey:
        return SourceKey(self.source_system, self.source_key)


@dataclass(frozen=True, slots=True)
class LandingRow:
    """One landing row, as the simulator writes it (the integration platform's side)."""

    event_id: str
    source_system: str
    source_key: str
    entity: str
    op: str
    occurred_at: datetime
    payload: Mapping[str, Any]
    source_version: int | None = None
    initial_load: bool = False


@dataclass(frozen=True, slots=True)
class RegisteredId:
    scheme: str
    value: str
    valid: bool | None  # None: no checksum configured


@dataclass(frozen=True, slots=True)
class StdRecord:
    """Engine output for one source record."""

    entity: str
    source: SourceKey
    values: Mapping[str, Any]  # clean display values (ISO dates as date)
    match: Mapping[str, Any]  # comparison forms (folded, accentless, phonetic codes)
    ids: tuple[RegisteredId, ...]
    keys: Mapping[str, tuple[str, ...]]  # blocking pass -> keys
    references: Mapping[str, str]  # reference attribute -> the referenced source key
    sample_hash: int
    placeholders: tuple[str, ...]  # attributes whose value was a placeholder
    invalid: tuple[str, ...] = ()  # attributes that failed standardisation


@dataclass(frozen=True, slots=True)
class SourceState:
    """A source record's current state, as stored in mdm_work.source_state."""

    entity: str
    source: SourceKey
    status: str  # active | deleted
    values: Mapping[str, Any]
    match: Mapping[str, Any]
    ids: tuple[RegisteredId, ...]
    references: Mapping[str, str]
    value_ids: Mapping[str, str]  # attribute -> vault ID of the standardised personal value
    sample_hash: int
    source_version: int | None
    occurred_at: datetime
    landing_seq: int
    event_id: str
    initial_load: bool
    held: bool
    approved_values: Mapping[str, Any] | None  # what survivorship uses; None = never approved
    approved_event_id: str | None
    rules_checked: int
    rules_failed: int
    updated_at: datetime

    def version_key(self, versioned: bool) -> tuple[int, datetime, int]:
        """(source_version if versioned else 0, occurred_at, landing_seq): a state moves only forward by it.

        A versioned source always carries a version (the landing interface rejects a row without one).
        """
        version = self.source_version if versioned and self.source_version is not None else 0
        return (version, self.occurred_at, self.landing_seq)

    def strong_ids(self, model: EntityModel) -> frozenset[tuple[str, str]]:
        """(scheme, value) of the valid identifiers of the model's strong attributes."""
        schemes = {a.scheme for a in model.strong_attributes() if a.scheme}
        return frozenset((i.scheme, i.value) for i in self.ids if i.valid is True and i.scheme in schemes)


@dataclass(frozen=True, slots=True)
class GoldenRow:
    entity: str
    master_id: str
    status: str  # active | retired | merged
    survivor_id: str | None
    values: Mapping[str, Any]
    commit_version: int
    row_version: int
    initial_load: bool


@dataclass(frozen=True, slots=True)
class XrefRow:
    entity: str
    source: SourceKey
    master_id: str
    status: str  # active | detached
    commit_version: int


@dataclass(frozen=True, slots=True)
class RetiredRow:
    retired_id: str
    entity: str
    merged_into: str  # the direct edge, fixed per merge
    merge_version: int
    survivor_id: str  # derived: chains collapsed
    active: bool
    commit_version: int


@dataclass(frozen=True, slots=True)
class RelationshipRow:
    rel_id: str
    rel_type: str
    from_entity: str
    from_master_id: str
    to_entity: str
    to_master_id: str
    valid_from: date | None
    valid_to: date | None
    status: str  # active | ended
    attributes: Mapping[str, Any]
    origin: SourceKey | None
    origin_attribute: str | None
    commit_version: int
    row_version: int


@dataclass(frozen=True, slots=True)
class Reject:
    event_id: str
    landing_seq: int
    entity: str | None
    source: SourceKey | None
    reason: str  # unknown_entity | unknown_source | missing_key | bad_op | bad_payload | bad_type | missing_version
    attributes: tuple[str, ...] = ()  # attribute names only, never values


@dataclass(frozen=True, slots=True)
class RuleResult:
    rule_id: str
    attribute: str
    dimension: str
    passed: bool
    code: str  # missing | placeholder | out_of_range | bad_pattern | not_in_code_list | bad_checksum | ok
    severity: str


RULE_RESULT_CODES = (
    "missing",
    "placeholder",
    "out_of_range",
    "bad_pattern",
    "not_in_code_list",
    "bad_checksum",
    "ok",
)


@dataclass(frozen=True, slots=True)
class StewardValue:
    value: Any
    pinned_until: datetime | None
    set_by: str
    set_at: datetime


@dataclass(frozen=True, slots=True)
class ReaderState:
    high_water: int
    low_water: int
    reconciled_at: datetime | None


@dataclass(frozen=True, slots=True)
class Gap:
    lo: int
    hi: int
    state: str  # open | lost
    first_seen_at: datetime
    lost_at: datetime | None
