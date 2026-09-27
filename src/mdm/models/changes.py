"""Change sets: the only way anything reaches the published tables (B.7).

A change set carries its actor, its authority and items, each automated item
with the clause that allowed it (decision 16). Its ID is random and unique; its
fingerprint is deterministic, so the parity tests can compare two engines.
`WorkWrites` settles the arrival queue and writes tasks in the same
transaction as the commit, so an effect and its settlement commit together.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from mdm.models.authority import Actor, Authority
from mdm.models.canonical import canonical_json
from mdm.models.match import PairScore
from mdm.models.records import SourceKey
from mdm.models.tasks import Task


@dataclass(frozen=True, slots=True)
class CreateGolden:
    ref: str  # a local reference other items name as a target until the master ID is allocated
    values: Mapping[str, Any]
    provenance: Mapping[str, Any]
    members: tuple[SourceKey, ...]
    clause: str = ""


@dataclass(frozen=True, slots=True)
class UpdateGolden:
    master_id: str
    expected_row_version: int
    values: Mapping[str, Any]
    provenance: Mapping[str, Any]
    clause: str = ""


@dataclass(frozen=True, slots=True)
class LinkSource:
    source: SourceKey
    target: str  # a master ID or a CreateGolden ref
    expected_master_id: str | None  # the active link now (None = unlinked)
    clause: str = ""


@dataclass(frozen=True, slots=True)
class DetachSource:
    source: SourceKey
    master_id: str  # the expected active link
    clause: str = ""


@dataclass(frozen=True, slots=True)
class MergeGolden:
    survivor_id: str
    retired_id: str
    expected_row_versions: tuple[int, int]  # (survivor, retired)
    survivor_values: Mapping[str, Any]
    provenance: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class UnmergeGolden:
    retired_id: str
    survivor_id: str  # the current collapsed survivor, where members_back are linked now (checked at commit)
    merge_version: int
    expected_row_versions: tuple[int, int]  # (retired, survivor)
    members_back: tuple[SourceKey, ...]
    retired_values: Mapping[str, Any]
    survivor_values: Mapping[str, Any]
    provenance: Mapping[str, Mapping[str, Any]]  # master ID -> provenance


@dataclass(frozen=True, slots=True)
class RetireGolden:
    master_id: str
    expected_row_version: int


@dataclass(frozen=True, slots=True)
class ReinstateGolden:
    master_id: str  # status 'retired' only; a merged record needs unmerge
    expected_row_version: int


@dataclass(frozen=True, slots=True)
class UpsertRelationship:
    rel_id: str
    rel_type: str
    from_ref: str  # a master ID or a CreateGolden ref
    to_entity: str
    to_ref: str
    valid_from: date | None
    attributes: Mapping[str, Any]
    origin: SourceKey | None
    origin_attribute: str | None
    clause: str = ""


@dataclass(frozen=True, slots=True)
class EndRelationship:
    rel_id: str
    valid_to: date
    clause: str = ""


ChangeItem = (
    CreateGolden
    | UpdateGolden
    | LinkSource
    | DetachSource
    | MergeGolden
    | UnmergeGolden
    | RetireGolden
    | ReinstateGolden
    | UpsertRelationship
    | EndRelationship
)

_ITEM_KINDS: Mapping[type, str] = {
    CreateGolden: "create",
    UpdateGolden: "update",
    LinkSource: "link",
    DetachSource: "detach",
    MergeGolden: "merge",
    UnmergeGolden: "unmerge",
    RetireGolden: "retire",
    ReinstateGolden: "reinstate",
    UpsertRelationship: "relationship",
    EndRelationship: "relationship",
}
ITEM_KINDS = ("create", "update", "link", "detach", "merge", "unmerge", "retire", "reinstate", "relationship")


def item_kind(item: ChangeItem) -> str:
    """create | update | link | detach | merge | unmerge | retire | reinstate | relationship"""
    try:
        return _ITEM_KINDS[type(item)]
    except KeyError:
        raise TypeError(f"not a change item: {type(item).__name__}") from None


def item_clause(item: ChangeItem) -> str:
    """The clause an item carries; "" for the kinds that never carry one (merge, unmerge, retire, reinstate)."""
    return getattr(item, "clause", "")


@dataclass(frozen=True, slots=True)
class ChangeSet:
    change_set_id: str  # "CS-" + secrets.token_hex(10): unique, never derived
    fingerprint: str  # deterministic; the parity tests compare it
    planning_version: int  # the last commit version the planner read
    entity: str
    action: str
    actor: Actor
    authority: Authority
    items: tuple[ChangeItem, ...]
    initial_load: bool = False
    reason: str = ""
    evidence: Mapping[str, Any] = field(default_factory=dict)
    checker: Actor | None = None


_OPTIONAL = frozenset({"initial_load", "reason", "evidence", "checker"})


def change_set_fingerprint(
    entity: str,
    action: str,
    actor: Actor,
    authority: Authority,
    items: Sequence[ChangeItem],
    planning_version: int,
) -> str:
    """sha256(canonical_json([entity, action, actor.kind, actor.name, authority, [[kind, item], …], planning_version]))[:32].

    Each item is written with its kind, since two kinds can share the same fields.
    """
    document = [
        entity,
        action,
        actor.kind,
        actor.name,
        authority,
        [[item_kind(item), item] for item in items],
        planning_version,
    ]
    return hashlib.sha256(canonical_json(document).encode()).hexdigest()[:32]


def new_change_set(
    entity: str,
    action: str,
    actor: Actor,
    authority: Authority,
    items: Sequence[ChangeItem],
    *,
    planning_version: int,
    **optional: Any,
) -> ChangeSet:
    """A change set with a random ID and a deterministic fingerprint.

    `optional` takes `initial_load`, `reason`, `evidence` and `checker`; any other key is a TypeError.
    """
    unknown = set(optional) - _OPTIONAL
    if unknown:
        raise TypeError(f"unknown change-set fields: {sorted(unknown)}")
    return ChangeSet(
        change_set_id="CS-" + secrets.token_hex(10),
        fingerprint=change_set_fingerprint(entity, action, actor, authority, items, planning_version),
        planning_version=planning_version,
        entity=entity,
        action=action,
        actor=actor,
        authority=authority,
        items=tuple(items),
        **optional,
    )


@dataclass(frozen=True, slots=True)
class WorkWrites:
    """Written in the same transaction as the commit, or alone when nothing publishes."""

    entity: str
    settle: tuple[
        tuple[SourceKey, str], ...
    ] = ()  # (source, event_id): delete the queue row if it still names that event
    approve: tuple[
        tuple[SourceKey, str], ...
    ] = ()  # approved_values := std_values, if event_id still matches
    hold: tuple[SourceKey, ...] = ()  # held := true
    release: tuple[SourceKey, ...] = ()  # held := false
    tasks: tuple[Task, ...] = ()  # upserted through open_task
    pairs: tuple[PairScore, ...] = ()  # candidate pairs at or above the lower band

    def empty(self) -> bool:
        return not (self.settle or self.approve or self.hold or self.release or self.tasks or self.pairs)

    def split(self, sources: Collection[SourceKey]) -> tuple[WorkWrites, WorkWrites]:
        """(the part for `sources`, the rest), for `CommitService.apply_chunked`.

        A task goes with its source; a task without a source (one naming master IDs) stays in the rest,
        which goes with the last chunk. A pair goes with the part when either end is in `sources`.
        """
        chosen = frozenset(sources)
        part = WorkWrites(
            entity=self.entity,
            settle=tuple(s for s in self.settle if s[0] in chosen),
            approve=tuple(a for a in self.approve if a[0] in chosen),
            hold=tuple(s for s in self.hold if s in chosen),
            release=tuple(s for s in self.release if s in chosen),
            tasks=tuple(t for t in self.tasks if t.source is not None and t.source in chosen),
            pairs=tuple(p for p in self.pairs if p.left in chosen or p.right in chosen),
        )
        rest = WorkWrites(
            entity=self.entity,
            settle=tuple(s for s in self.settle if s[0] not in chosen),
            approve=tuple(a for a in self.approve if a[0] not in chosen),
            hold=tuple(s for s in self.hold if s not in chosen),
            release=tuple(s for s in self.release if s not in chosen),
            tasks=tuple(t for t in self.tasks if t.source is None or t.source not in chosen),
            pairs=tuple(p for p in self.pairs if p.left not in chosen and p.right not in chosen),
        )
        return part, rest


@dataclass(frozen=True, slots=True)
class CommitResult:
    change_set_id: str
    commit_version: int | None  # None: nothing published, no version used
    created: Mapping[str, str]  # CreateGolden ref -> master ID
    counts: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class CommitLogRow:
    commit_version: int
    committed_at: datetime
    change_set_id: str
    actor_kind: str
    actor_role: str  # a role name, or the automated actor's name; never a person
    authority_kind: str
    authority_ref: str
    initial_load: bool
    counts: Mapping[str, int]
    row_count: int
    change_count: int  # change rows under this version: a reader knows when it has them all


CHANGE_KINDS = ("created", "updated", "merged", "retired", "unmerged", "reinstated", "remapped")
CHANGE_PARTS = ("values", "xref", "relationship", "status", "survivor")


@dataclass(frozen=True, slots=True)
class ChangeRow:
    commit_version: int
    change_seq: int
    entity: str
    master_id: str
    change_kind: str
    survivor_id: str | None
    parts: tuple[str, ...]  # parts ⊆ values, xref, relationship, status, survivor


@dataclass(frozen=True, slots=True)
class FeedPage:
    commits: tuple[CommitLogRow, ...]
    changes: tuple[ChangeRow, ...]
    rows: Mapping[str, Mapping[str, Mapping[str, Any]]]  # entity -> master_id -> current published row
    next_watermark: int  # the last commit read to its end
    cursor: tuple[int, int] | None  # (commit_version, change_seq) inside a commit read in part
