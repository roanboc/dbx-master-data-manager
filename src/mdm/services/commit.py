"""The commit transaction: the only writer of the published tables (owner: SERVICES, B.7).

`apply(cs, work)`: authority checked before the transaction; items planned
into row writes with keyed reads; personal values vaulted before the lock;
then, under `store.commit_scope()`: authority again, optimistic checks
(`Conflict` rolls back), the next commit version, master IDs, golden rows,
cross-references, merges and relationships, provenance, the work writes
(settle, approve, hold, release, tasks, pairs), change rows, the commit-log row
(a role or automated actor, never a person), the audit change set (the person)
and the change log; COMMIT. A change set that publishes nothing uses no
version but still applies its work writes.

`apply_chunked` splits items so no transaction writes more than `max_rows`
published rows, never splitting a create from its links or a merge from its
repoints; each chunk is its own change set (`<id>-<n>`) and version, with its
part of the work writes; task-only work goes with the last chunk.

The plan is computed twice: once before the transaction from keyed reads (to
learn whether anything would be published, and to fail fast), and again inside
it from the same keyed reads under the lock, where the optimistic checks decide.
Every golden row that gets a change row other than a relationship-only one is
written with the commit's version, so a listener that reads the current row
never sees a version older than the change that led it there.
"""

from __future__ import annotations

import hashlib
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any

import mdm.capacity as capacity
from mdm.backend.store import SqlStore
from mdm.models.authority import Authority
from mdm.models.canonical import iso, utcnow
from mdm.models.changes import (
    CHANGE_PARTS,
    ChangeItem,
    ChangeRow,
    ChangeSet,
    CommitLogRow,
    CommitResult,
    CreateGolden,
    DetachSource,
    EndRelationship,
    LinkSource,
    MergeGolden,
    ReinstateGolden,
    RetireGolden,
    UnmergeGolden,
    UpdateGolden,
    UpsertRelationship,
    WorkWrites,
    item_clause,
    item_kind,
)
from mdm.models.entity_model import EntityModel
from mdm.models.errors import Conflict, Forbidden, NotFound
from mdm.models.records import GoldenRow, RelationshipRow, RetiredRow, SourceKey, XrefRow
from mdm.models.tasks import Task, task_id, task_key
from mdm.services.authority import CLAUSES_MARK, AuthorityService
from mdm.services.privacy import Vault, is_vault_ref, master_subject, source_subject, vault_ref
from mdm.services.registry import ModelRegistry
from mdm.services.support import column_types, fingerprint, is_ref, plain, same_value, source_token, token

#: the most members, chained retired IDs or relationships one merge or unmerge moves
MOVE_LIMIT = 100_000
_KIND_RANK = {
    "created": 0,
    "merged": 1,
    "unmerged": 2,
    "retired": 3,
    "reinstated": 4,
    "remapped": 5,
    "updated": 6,
}
_ROW_PARTS = frozenset({"values", "xref", "status", "survivor"})


class RateLimiter:
    """Sleeps between bulk chunks so later bulk changes reach the tables no faster than `rows_per_hour`."""

    def __init__(
        self,
        rows_per_hour: int,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.rows_per_hour = max(int(rows_per_hour), 0)
        self.sleep = sleep
        self.clock = clock
        self._start: float | None = None
        self._written = 0
        self.slept = 0.0

    def wait(self, rows: int) -> float:
        """Sleep as needed before writing `rows` more rows; returns the seconds slept."""
        if self.rows_per_hour <= 0:
            return 0.0
        now = self.clock()
        if self._start is None:
            self._start = now
        earliest = self._start + self._written * 3600.0 / self.rows_per_hour
        waited = 0.0
        if earliest > now:
            waited = earliest - now
            self.sleep(waited)
            self.slept += waited
        self._written += max(int(rows), 0)
        return waited


# --------------------------------------------------------------------------------------------- the plan


@dataclass
class _Change:
    kind: str = "updated"
    survivor_id: str | None = None
    parts: set[str] = field(default_factory=set)

    def mark(self, kind: str, *parts: str, survivor_id: str | None = None) -> None:
        if _KIND_RANK[kind] < _KIND_RANK[self.kind]:
            self.kind = kind
        if survivor_id is not None:
            self.survivor_id = survivor_id
        self.parts.update(parts)


@dataclass
class _Plan:
    """The row writes of one change set, computed from keyed reads."""

    golden_before: dict[str, GoldenRow] = field(default_factory=dict)
    golden_after: dict[str, GoldenRow] = field(default_factory=dict)
    created: dict[str, str] = field(default_factory=dict)  # ref -> master ID
    xrefs: dict[SourceKey, tuple[XrefRow | None, XrefRow]] = field(default_factory=dict)
    retired: dict[str, tuple[RetiredRow | None, RetiredRow]] = field(default_factory=dict)
    merge_members: list[tuple[str, list[SourceKey]]] = field(default_factory=list)
    relationships: dict[str, tuple[RelationshipRow | None, RelationshipRow]] = field(default_factory=dict)
    provenance: dict[str, tuple[dict | None, dict]] = field(default_factory=dict)
    changes: dict[tuple[str, str], _Change] = field(default_factory=dict)
    clause_of: dict[str, str] = field(default_factory=dict)  # row key -> clause
    stored_provenance: dict[str, dict] = field(default_factory=dict)  # as read before any write

    def change(self, entity: str, master_id: str) -> _Change:
        return self.changes.setdefault((entity, master_id), _Change())

    def publishes(self) -> bool:
        return bool(self.changes or self.xrefs or self.retired or self.relationships)


def _units(
    items: Sequence[ChangeItem], links: Sequence[tuple[str, str]]
) -> tuple[list[list[int]], dict[int, set[SourceKey]]]:
    """Groups of item indices that must commit together, in the order of their first item, and for each group
    (by its first item) every source anchored in it, directly or through `links`."""
    parent: dict[str, str] = {}

    def find(a: str) -> str:
        parent.setdefault(a, a)
        root = a
        while parent[root] != root:
            root = parent[root]
        while parent[a] != root:
            parent[a], a = root, parent[a]
        return root

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    def golden(target: str) -> str:
        return ("r:" if is_ref(target) else "m:") + target

    anchors: list[list[str]] = []
    for item in items:
        if isinstance(item, CreateGolden):
            found = [golden(item.ref), *("s:" + s.text() for s in item.members)]
        elif isinstance(item, UpdateGolden):
            found = [golden(item.master_id)]
        elif isinstance(item, LinkSource):
            found = ["s:" + item.source.text(), golden(item.target)]
            if item.expected_master_id:
                found.append(golden(item.expected_master_id))
        elif isinstance(item, DetachSource):
            found = ["s:" + item.source.text(), golden(item.master_id)]
        elif isinstance(item, MergeGolden):
            found = [golden(item.survivor_id), golden(item.retired_id)]
        elif isinstance(item, UnmergeGolden):
            found = [
                golden(item.retired_id),
                golden(item.survivor_id),
                *("s:" + s.text() for s in item.members_back),
            ]
        elif isinstance(item, (RetireGolden, ReinstateGolden)):
            found = [golden(item.master_id)]
        elif isinstance(item, UpsertRelationship):
            found = ["l:" + item.rel_id, golden(item.from_ref)]
            if is_ref(item.to_ref):
                found.append(golden(item.to_ref))
            if item.origin is not None:
                found.append("s:" + item.origin.text())
        elif isinstance(item, EndRelationship):
            found = ["l:" + item.rel_id]
        else:
            raise TypeError(f"not a change item: {type(item).__name__}")
        anchors.append(found)
        for other in found[1:]:
            union(found[0], other)
    for a, b in links:
        union(a, b)
    groups: dict[str, list[int]] = {}
    for index, found in enumerate(anchors):
        groups.setdefault(find(found[0]), []).append(index)
    sources_of: dict[str, set[SourceKey]] = {}
    for anchor in list(parent):
        if anchor.startswith("s:"):
            sources_of.setdefault(find(anchor), set()).add(SourceKey.from_text(anchor[2:]))
    ordered = sorted(groups.items(), key=lambda kv: kv[1][0])
    return [g for _, g in ordered], {g[0]: sources_of.get(root, set()) for root, g in ordered}


def _unit_sources(items: Sequence[ChangeItem], unit: Sequence[int]) -> set[SourceKey]:
    out: set[SourceKey] = set()
    for index in unit:
        item = items[index]
        if isinstance(item, (LinkSource, DetachSource)):
            out.add(item.source)
        elif isinstance(item, CreateGolden):
            out.update(item.members)
        elif isinstance(item, UnmergeGolden):
            out.update(item.members_back)
        elif isinstance(item, UpsertRelationship) and item.origin is not None:
            out.add(item.origin)
    return out


def _item_rows(item: ChangeItem) -> int:
    if isinstance(item, (MergeGolden, UnmergeGolden)):
        return 3 + (len(item.members_back) if isinstance(item, UnmergeGolden) else 0)
    return 1


class CommitService:
    def __init__(
        self,
        store: SqlStore,
        registry: ModelRegistry,
        authority: AuthorityService,
        vault: Vault,
        clock: Callable[[], datetime] = utcnow,
        *,
        fault: Callable[[str], None] | None = None,
    ) -> None:
        """`fault(point)` is called at "in_commit", inside the transaction before COMMIT (tests raise there)."""
        self.store = store
        self.registry = registry
        self.authority = authority
        self.vault = vault
        self.clock = clock
        self.fault = fault

    # ------------------------------------------------------------------ public

    def apply(
        self,
        cs: ChangeSet,
        work: WorkWrites | None = None,
        *,
        created: Mapping[str, str] | None = None,
        fault: Callable[[str], None] | None = None,
    ) -> CommitResult:
        """One change set in one transaction. `created` maps refs created by earlier chunks to master IDs."""
        work = work if work is not None else WorkWrites(cs.entity)
        known = dict(created or {})
        # step 0: authority, a dry plan from keyed reads, personal values into the vault before the lock
        self.authority.check(cs)
        model = self.registry.published(cs.entity)
        vaulted = self._vault_provenance(cs, model)
        dry = self._plan(cs, model, vaulted, 0, {**known, **{r: r for r in self._refs(cs)}})
        if not dry.publishes():
            if not work.empty():
                with self.store.transaction():
                    self.store.apply_work(self._map_work(work, known, cs))
            return CommitResult(cs.change_set_id, None, {}, {})
        fault = fault or self.fault
        with self.store.commit_scope():
            # step 2: authority again, under the lock
            self.authority.check(cs, in_transaction=True)
            model = self.registry.published(cs.entity)
            # step 4 and 5: the version, then master IDs in item order
            version = self.store.next_commit_version()
            refs = self._refs(cs)
            ids = self.store.allocate_ids(model.entity, model.code, len(refs))
            mapping = {**known, **dict(zip(refs, ids, strict=True))}
            # step 3 happens inside the plan: every expectation is checked against the rows read now
            plan = self._plan(cs, model, vaulted, version, mapping)
            counts, row_count = self._write(cs, model, plan, version)
            # step 9: the work, so the effect and its settlement commit together
            self.store.apply_work(self._map_work(work, mapping, cs))
            # step 10 and 11: change rows, the commit-log row
            changes = self._change_rows(plan, version)
            self.store.write_changes(changes)
            counts.update(Counter(c.change_kind for c in changes))
            self.store.write_commit_log(
                CommitLogRow(
                    commit_version=version,
                    committed_at=self.clock(),
                    change_set_id=cs.change_set_id,
                    actor_kind=cs.actor.kind,
                    actor_role=cs.actor.role if cs.actor.kind == "person" else cs.actor.name,
                    authority_kind=cs.authority.kind,
                    authority_ref=cs.authority.ref,
                    initial_load=cs.initial_load,
                    counts=dict(sorted(counts.items())),
                    row_count=row_count,
                    change_count=len(changes),
                )
            )
            # step 12 and 13: the audit
            self.store.append_change_set(self._audited(cs), version, len(cs.items))
            self.store.append_change_log(self._change_log(cs, model, plan, version, counts))
            if fault is not None:
                fault("in_commit")
        return CommitResult(cs.change_set_id, version, dict(plan.created), dict(sorted(counts.items())))

    def apply_chunked(
        self,
        cs: ChangeSet,
        work: WorkWrites | None = None,
        max_rows: int = capacity.COMMIT_CHUNK_ROWS,
        throttle: RateLimiter | None = None,
        between_chunks: Callable[[int], None] | None = None,
        *,
        links: Sequence[tuple[str, str]] = (),
        fault: Callable[[str], None] | None = None,
    ) -> list[CommitResult]:
        """`between_chunks(n)` is called after chunk n commits (arrival's fault hook `after_chunk`).

        `links` are extra pairs of anchors (`s:<source>`, `m:<master>`, `r:<ref>`, `l:<relationship>`) that
        must commit together, e.g. two records the planner decided in the light of each other.
        """
        work = work if work is not None else WorkWrites(cs.entity)
        units, unit_sources = _units(cs.items, links)
        chunks: list[list[int]] = []
        size = 0
        for unit in units:
            rows = sum(_item_rows(cs.items[i]) for i in unit)
            if chunks and size + rows <= max_rows:
                chunks[-1].extend(unit)
                size += rows
            else:
                chunks.append(list(unit))
                size = rows
        if len(chunks) <= 1:
            if throttle is not None:
                throttle.wait(len(cs.items))
            return [self.apply(cs, work, fault=fault)]
        first_of = {i: unit[0] for unit in units for i in unit}
        results: list[CommitResult] = []
        created: dict[str, str] = {}
        rest = work
        for number, chunk in enumerate(chunks, start=1):
            chunk = sorted(chunk)
            items = tuple(cs.items[i] for i in chunk)
            last = number == len(chunks)
            if last:
                part, rest = rest, WorkWrites(cs.entity)
            else:
                sources = _unit_sources(cs.items, chunk)
                for first in {first_of[i] for i in chunk}:
                    sources |= unit_sources[first]
                part, rest = rest.split(sources)
            sub = self._chunk_change_set(cs, items, number)
            if throttle is not None:
                throttle.wait(sum(_item_rows(i) for i in items))
            result = self.apply(sub, part, created=created, fault=fault)
            created.update(result.created)
            results.append(result)
            if between_chunks is not None and not last:
                between_chunks(number)
        return results

    # ------------------------------------------------------------------ chunking helpers

    def _chunk_change_set(self, cs: ChangeSet, items: tuple[ChangeItem, ...], number: int) -> ChangeSet:
        authority = cs.authority
        if cs.actor.kind == "automated" and authority.kind == "rule_version":
            prefix = authority.ref.split(CLAUSES_MARK, 1)[0]
            clauses = sorted({item_clause(i) for i in items if item_clause(i)})
            authority = Authority(
                authority.kind, prefix + (CLAUSES_MARK + ", ".join(clauses) if clauses else "")
            )
        return replace(
            cs,
            change_set_id=f"{cs.change_set_id}-{number}",
            fingerprint=fingerprint(cs.entity, cs.action, cs.actor, authority, items, cs.planning_version),
            authority=authority,
            items=items,
        )

    # ------------------------------------------------------------------ step 0: the vault

    @staticmethod
    def _refs(cs: ChangeSet) -> list[str]:
        return [item.ref for item in cs.items if isinstance(item, CreateGolden)]

    def _vault_provenance(self, cs: ChangeSet, model: EntityModel) -> dict[str, dict]:
        """Every provenance document of the items, personal values replaced by vault references.

        A value is filed under the subject of the source that holds it, so a golden value reuses its winning
        member's value ID (the vault returns an equal live value) and no second copy is kept.
        """
        docs: dict[str, Mapping[str, Any]] = {}
        for item in cs.items:
            if isinstance(item, CreateGolden):
                docs[item.ref] = item.provenance
            elif isinstance(item, UpdateGolden):
                docs[item.master_id] = item.provenance
            elif isinstance(item, MergeGolden):
                docs[item.survivor_id] = item.provenance
            elif isinstance(item, UnmergeGolden):
                for master_id, provenance in item.provenance.items():
                    docs[master_id] = provenance
        personal = set(model.personal_attributes())
        rows: list[tuple[str, str, str, Any]] = []
        slots: list[tuple[str, str, str, int]] = []  # (doc key, attribute, "winner"|"runners_up", index)
        out: dict[str, dict] = {}
        for key, doc in docs.items():
            converted = plain(doc)
            out[key] = converted
            for name, entry in converted.items():
                if name not in personal or not isinstance(entry, dict):
                    continue
                entries = [("winner", -1, entry.get("winner"))]
                entries += [("runners_up", i, r) for i, r in enumerate(entry.get("runners_up") or [])]
                for place, index, found in entries:
                    if (
                        not isinstance(found, dict)
                        or found.get("value") is None
                        or is_vault_ref(found["value"])
                    ):
                        continue
                    subject = self._subject(found.get("source"), key)
                    if subject is None:
                        found["value"] = vault_ref(None)
                        continue
                    rows.append((model.entity, subject, name, found["value"]))
                    slots.append((key, name, place, index))
        ids = self.vault.put(rows)
        for (key, name, place, index), value_id in zip(slots, ids, strict=True):
            entry = out[key][name]
            target = entry["winner"] if place == "winner" else entry["runners_up"][index]
            target["value"] = vault_ref(value_id)
        return out

    @staticmethod
    def _subject(source_text: Any, key: str) -> str | None:
        if not isinstance(source_text, str):
            return None
        try:
            source = SourceKey.from_text(source_text)
        except ValueError:
            return None
        if source.system == "steward":
            return None if is_ref(key) else master_subject(key)
        return source_subject(source)

    # ------------------------------------------------------------------ steps 3–8: the plan

    def _plan(
        self,
        cs: ChangeSet,
        model: EntityModel,
        vaulted: Mapping[str, dict],
        version: int,
        mapping: Mapping[str, str],
    ) -> _Plan:
        entity = cs.entity
        types = column_types(model)
        plan = _Plan()
        store = self.store
        now_initial = cs.initial_load

        def resolve(target: str) -> str:
            return mapping.get(target, target)

        # keyed reads
        masters: set[str] = set()
        sources: set[SourceKey] = set()
        rel_ids: set[str] = set()
        retired_ids: set[str] = set()
        for item in cs.items:
            if isinstance(item, UpdateGolden):
                masters.add(item.master_id)
            elif isinstance(item, LinkSource):
                sources.add(item.source)
                if not is_ref(item.target):
                    masters.add(item.target)
                if item.expected_master_id:
                    masters.add(item.expected_master_id)
            elif isinstance(item, DetachSource):
                sources.add(item.source)
                masters.add(item.master_id)
            elif isinstance(item, MergeGolden):
                masters.update((item.survivor_id, item.retired_id))
                retired_ids.add(item.retired_id)
            elif isinstance(item, UnmergeGolden):
                masters.update((item.survivor_id, item.retired_id))
                retired_ids.add(item.retired_id)
                sources.update(item.members_back)
            elif isinstance(item, (RetireGolden, ReinstateGolden)):
                masters.add(item.master_id)
            elif isinstance(item, (UpsertRelationship, EndRelationship)):
                rel_ids.add(item.rel_id)
        golden = store.golden(entity, sorted(masters)) if masters else {}
        plan.golden_before.update(golden)
        xrefs = store.xref_rows(entity, sorted(sources)) if sources else {}
        retired_rows = store.retired_rows(sorted(retired_ids)) if retired_ids else {}
        relationships = store.relationships(sorted(rel_ids)) if rel_ids else {}
        stored_provenance = store.provenance(entity, sorted(masters)) if masters else {}
        plan.stored_provenance = stored_provenance

        def active_master(source: SourceKey) -> str | None:
            if source in plan.xrefs:
                row = plan.xrefs[source][1]
            else:
                row = xrefs.get(source)
            return row.master_id if row is not None and row.status == "active" else None

        def current(master_id: str) -> GoldenRow:
            if master_id in plan.golden_after:
                return plan.golden_after[master_id]
            row = golden.get(master_id)
            if row is None:
                if master_id not in plan.golden_before:
                    extra = store.golden(entity, [master_id])
                    plan.golden_before.update(extra)
                row = plan.golden_before.get(master_id)
            if row is None:
                raise Conflict([token(master_id)], code="unknown_master_id")
            return row

        def set_after(row: GoldenRow) -> None:
            plan.golden_after[row.master_id] = row

        def expect(row: GoldenRow, expected: int) -> None:
            before = plan.golden_before.get(row.master_id)
            if before is None or before.row_version != expected:
                raise Conflict([token(row.master_id)], code="stale_row")

        def set_xref(source: SourceKey, master_id: str, status: str, clause: str) -> None:
            before = plan.xrefs[source][0] if source in plan.xrefs else xrefs.get(source)
            plan.xrefs[source] = (before, XrefRow(entity, source, master_id, status, version))
            plan.clause_of.setdefault(f"xref:{source.text()}", clause)

        def set_provenance(master_id: str, doc: Mapping[str, Any] | None) -> None:
            if doc is None:
                return
            before = stored_provenance.get(master_id)
            after = dict(doc)  # the vaulted provenance: already in its JSON form
            if before != after:
                plan.provenance[master_id] = (before, after)

        def touch_relationship(before: RelationshipRow | None, after: RelationshipRow, clause: str) -> None:
            first = plan.relationships[after.rel_id][0] if after.rel_id in plan.relationships else before
            plan.relationships[after.rel_id] = (first, after)
            plan.clause_of.setdefault(f"relationship:{after.rel_id}", clause)
            ends = {(after.from_entity, after.from_master_id), (after.to_entity, after.to_master_id)}
            if before is not None:
                ends |= {(before.from_entity, before.from_master_id), (before.to_entity, before.to_master_id)}
            for end_entity, end_id in ends:
                if not is_ref(end_id):
                    plan.change(end_entity, end_id).mark("updated", "relationship")

        ends = sorted(
            {
                end
                for item in cs.items
                if isinstance(item, UpsertRelationship)
                for end in (item.from_ref, item.to_ref)
                if not is_ref(end) and end not in mapping
            }
        )
        survivors = store.resolve_retired(ends) if ends else {}

        def resolve_end(target: str) -> str:
            mapped = resolve(target)
            if is_ref(mapped) or mapped != target:
                return mapped
            return survivors.get(mapped, mapped)

        def repoint(
            from_id: str, to_id: str, *, only: Callable[[RelationshipRow], bool] | None = None
        ) -> None:
            for rel in store.relationships_of([from_id], MOVE_LIMIT):
                if rel.rel_id in plan.relationships:
                    rel = plan.relationships[rel.rel_id][1]
                if rel.status != "active" or (only is not None and not only(rel)):
                    continue
                if rel.from_master_id != from_id and rel.to_master_id != from_id:
                    continue
                moved = replace(
                    rel,
                    from_master_id=to_id if rel.from_master_id == from_id else rel.from_master_id,
                    to_master_id=to_id if rel.to_master_id == from_id else rel.to_master_id,
                    commit_version=version,
                    row_version=rel.row_version + 1,
                )
                touch_relationship(rel, moved, "")

        for item in cs.items:
            clause = item_clause(item)
            if isinstance(item, CreateGolden):
                master_id = resolve(item.ref)
                plan.created[item.ref] = master_id
                given = plain(dict(item.values))
                values = {name: given.get(name) for name in types}
                row = GoldenRow(entity, master_id, "active", None, values, version, 1, now_initial)
                set_after(row)
                plan.change(entity, master_id).mark("created", "values")
                plan.clause_of.setdefault(f"golden:{master_id}", clause)
                doc = vaulted.get(item.ref)
                if doc:
                    plan.provenance[master_id] = (None, doc)
            elif isinstance(item, UpdateGolden):
                row = current(item.master_id)
                expect(row, item.expected_row_version)
                if row.status != "active":
                    raise Conflict([token(item.master_id)], code="not_active")
                given = plain(dict(item.values))
                values = {name: given.get(name) for name in types}
                changed = [n for n, t in types.items() if not same_value(row.values.get(n), values.get(n), t)]
                if changed:
                    set_after(replace(row, values=values))
                    plan.change(entity, item.master_id).mark("updated", "values")
                plan.clause_of.setdefault(f"golden:{item.master_id}", clause)
                set_provenance(item.master_id, vaulted.get(item.master_id))
            elif isinstance(item, LinkSource):
                now_master = active_master(item.source)
                if now_master != item.expected_master_id:
                    raise Conflict([source_token(item.source)], code="stale_link")
                target = resolve(item.target)
                if not is_ref(item.target):
                    target_row = current(target)
                    if target_row.status != "active":
                        raise Conflict([token(target)], code="not_active")
                if now_master == target:
                    continue
                set_xref(item.source, target, "active", clause)
                plan.change(entity, target).mark("updated", "xref")
                plan.clause_of.setdefault(f"golden:{target}", clause)
                if now_master is not None:
                    plan.change(entity, now_master).mark("updated", "xref")
            elif isinstance(item, DetachSource):
                now_master = active_master(item.source)
                if now_master != item.master_id:
                    raise Conflict([source_token(item.source)], code="stale_link")
                set_xref(item.source, item.master_id, "detached", clause)
                plan.change(entity, item.master_id).mark("updated", "xref")
                plan.clause_of.setdefault(f"golden:{item.master_id}", clause)
            elif isinstance(item, MergeGolden):
                self._merge(
                    item,
                    entity,
                    types,
                    version,
                    plan,
                    current,
                    expect,
                    set_after,
                    set_xref,
                    set_provenance,
                    retired_rows,
                    vaulted,
                    repoint,
                )
            elif isinstance(item, UnmergeGolden):
                self._unmerge(
                    item,
                    entity,
                    types,
                    version,
                    plan,
                    current,
                    expect,
                    set_after,
                    set_xref,
                    active_master,
                    set_provenance,
                    retired_rows,
                    vaulted,
                    repoint,
                )
            elif isinstance(item, RetireGolden):
                row = current(item.master_id)
                expect(row, item.expected_row_version)
                if row.status != "active":
                    raise Conflict([token(item.master_id)], code="not_active")
                remaining = store.member_counts(entity, [item.master_id]).get(item.master_id, 0)
                remaining -= sum(
                    1
                    for b, a in plan.xrefs.values()
                    if a.master_id == item.master_id and a.status != "active"
                )
                if remaining > 0:
                    raise Forbidden("retire_needs_orphan", master_id=token(item.master_id), members=remaining)
                set_after(replace(row, status="retired"))
                plan.change(entity, item.master_id).mark("retired", "status")
            elif isinstance(item, ReinstateGolden):
                row = current(item.master_id)
                expect(row, item.expected_row_version)
                if row.status == "merged":
                    raise Forbidden("reinstate_merged_needs_unmerge", master_id=token(item.master_id))
                if row.status != "retired":
                    raise Conflict([token(item.master_id)], code="not_retired")
                set_after(replace(row, status="active", survivor_id=None))
                plan.change(entity, item.master_id).mark("reinstated", "status")
            elif isinstance(item, UpsertRelationship):
                before = (
                    plan.relationships[item.rel_id][1]
                    if item.rel_id in plan.relationships
                    else relationships.get(item.rel_id)
                )
                from_id = resolve_end(item.from_ref)
                to_id = resolve_end(item.to_ref)
                attributes = plain(item.attributes)
                if (
                    before is not None
                    and before.status == "active"
                    and before.from_master_id == from_id
                    and before.to_master_id == to_id
                    and plain(before.attributes) == attributes
                ):
                    continue
                after = RelationshipRow(
                    rel_id=item.rel_id,
                    rel_type=item.rel_type,
                    from_entity=entity,
                    from_master_id=from_id,
                    to_entity=item.to_entity,
                    to_master_id=to_id,
                    valid_from=item.valid_from
                    if before is None or before.status != "active"
                    else before.valid_from,
                    valid_to=None,
                    status="active",
                    attributes=attributes,
                    origin=item.origin,
                    origin_attribute=item.origin_attribute,
                    commit_version=version,
                    row_version=(before.row_version + 1) if before is not None else 1,
                )
                touch_relationship(before, after, clause)
            elif isinstance(item, EndRelationship):
                before = (
                    plan.relationships[item.rel_id][1]
                    if item.rel_id in plan.relationships
                    else relationships.get(item.rel_id)
                )
                if before is None or before.status != "active":
                    continue
                after = replace(
                    before,
                    status="ended",
                    valid_to=item.valid_to,
                    commit_version=version,
                    row_version=before.row_version + 1,
                )
                touch_relationship(before, after, clause)
            else:  # pragma: no cover - item_kind raised already
                raise TypeError(item_kind(item))

        # every golden row of this entity with a change beyond relationships is written with this version
        for (change_entity, master_id), change in plan.changes.items():
            if change_entity != entity or not (change.parts & _ROW_PARTS):
                continue
            row = plan.golden_after.get(master_id)
            if row is None:
                row = current(master_id)
            before = plan.golden_before.get(master_id)
            plan.golden_after[master_id] = replace(
                row,
                commit_version=version,
                row_version=(before.row_version + 1) if before is not None else 1,
                initial_load=now_initial,
            )
        for master_id in [m for m in plan.golden_after if (entity, m) not in plan.changes]:
            del plan.golden_after[master_id]
        return plan

    def _merge(
        self,
        item,
        entity,
        types,
        version,
        plan,
        current,
        expect,
        set_after,
        set_xref,
        set_provenance,
        retired_rows,
        vaulted,
        repoint,
    ) -> None:
        survivor = current(item.survivor_id)
        retired = current(item.retired_id)
        if item.survivor_id == item.retired_id:
            raise Forbidden("merge_into_itself", master_id=token(item.survivor_id))
        expect(survivor, item.expected_row_versions[0])
        expect(retired, item.expected_row_versions[1])
        if survivor.status != "active" or retired.status != "active":
            raise Conflict([token(item.survivor_id), token(item.retired_id)], code="not_active")
        known = retired_rows.get(item.retired_id)
        if known is not None and known.active:
            raise Conflict([token(item.retired_id)], code="already_merged")
        members = self.store.members(entity, [item.retired_id], MOVE_LIMIT).get(item.retired_id, [])
        for source in members:
            set_xref(source, item.survivor_id, "active", "")
        plan.merge_members.append((item.retired_id, list(members)))
        values = {name: plain(item.survivor_values.get(name)) for name in types}
        changed = [n for n, t in types.items() if not same_value(survivor.values.get(n), values.get(n), t)]
        set_after(replace(survivor, values=values))
        plan.change(entity, item.survivor_id).mark("updated", *(("values",) if changed else ()), "xref")
        set_provenance(item.survivor_id, vaulted.get(item.survivor_id))
        set_after(replace(retired, status="merged", survivor_id=item.survivor_id))
        plan.change(entity, item.retired_id).mark(
            "merged", "status", "survivor", *(("xref",) if members else ()), survivor_id=item.survivor_id
        )
        plan.retired[item.retired_id] = (
            known,
            RetiredRow(item.retired_id, entity, item.survivor_id, version, item.survivor_id, True, version),
        )
        for chained in self.store.retired_through(item.retired_id, MOVE_LIMIT):
            if chained.retired_id == item.retired_id:
                continue
            plan.retired[chained.retired_id] = (
                chained,
                replace(chained, survivor_id=item.survivor_id, commit_version=version),
            )
            tomb = current(chained.retired_id)
            set_after(replace(tomb, survivor_id=item.survivor_id))
            plan.change(entity, chained.retired_id).mark("remapped", "survivor", survivor_id=item.survivor_id)
        repoint(item.retired_id, item.survivor_id)

    def _unmerge(
        self,
        item,
        entity,
        types,
        version,
        plan,
        current,
        expect,
        set_after,
        set_xref,
        active_master,
        set_provenance,
        retired_rows,
        vaulted,
        repoint,
    ) -> None:
        retired = current(item.retired_id)
        survivor = current(item.survivor_id)
        expect(retired, item.expected_row_versions[0])
        expect(survivor, item.expected_row_versions[1])
        if retired.status != "merged":
            raise Forbidden("unmerge_needs_merged", master_id=token(item.retired_id))
        if survivor.status != "active":
            raise Conflict([token(item.survivor_id)], code="not_active")
        known = retired_rows.get(item.retired_id)
        if known is None or not known.active or known.merge_version != item.merge_version:
            raise Conflict([token(item.retired_id)], code="stale_merge")
        if known.survivor_id != item.survivor_id:
            raise Conflict([token(item.retired_id)], code="stale_survivor")
        for source in item.members_back:
            if active_master(source) != item.survivor_id:
                raise Conflict([source_token(source)], code="stale_link")
            set_xref(source, item.retired_id, "active", "")
        retired_values = {name: plain(item.retired_values.get(name)) for name in types}
        survivor_values = {name: plain(item.survivor_values.get(name)) for name in types}
        set_after(replace(retired, status="active", survivor_id=None, values=retired_values))
        plan.change(entity, item.retired_id).mark(
            "unmerged", "status", "survivor", "values", *(("xref",) if item.members_back else ())
        )
        changed = [
            n for n, t in types.items() if not same_value(survivor.values.get(n), survivor_values.get(n), t)
        ]
        set_after(replace(survivor, values=survivor_values))
        plan.change(entity, item.survivor_id).mark(
            "updated", *(("values",) if changed else ()), *(("xref",) if item.members_back else ())
        )
        for master_id in (item.retired_id, item.survivor_id):
            set_provenance(master_id, vaulted.get(master_id))
        plan.retired[item.retired_id] = (known, replace(known, active=False, commit_version=version))
        # chains that passed through the unmerged ID now end there
        through = {r.retired_id: r for r in self.store.retired_through(item.survivor_id, MOVE_LIMIT)}
        through.pop(item.retired_id, None)

        def ends_at(start: str) -> str:
            seen: set[str] = set()
            node = start
            while node in through and node not in seen:
                seen.add(node)
                node = through[node].merged_into
                if node == item.retired_id:
                    return item.retired_id
            return node

        for retired_id, row in sorted(through.items()):
            if ends_at(retired_id) != item.retired_id:
                continue
            plan.retired[retired_id] = (
                row,
                replace(row, survivor_id=item.retired_id, commit_version=version),
            )
            tomb = current(retired_id)
            set_after(replace(tomb, survivor_id=item.retired_id))
            plan.change(entity, retired_id).mark("remapped", "survivor", survivor_id=item.retired_id)
        # relationships asserted by the members that come back, and those that point at them
        back = set(item.members_back)
        repoint(
            item.survivor_id,
            item.retired_id,
            only=lambda rel: rel.from_master_id == item.survivor_id and rel.origin in back,
        )
        pointing = [
            rel
            for rel in self.store.relationships_of([item.survivor_id], MOVE_LIMIT)
            if rel.status == "active" and rel.to_master_id == item.survivor_id and rel.origin is not None
        ]
        targets = self._reference_targets(pointing)
        repoint(
            item.survivor_id,
            item.retired_id,
            only=lambda rel: rel.to_master_id == item.survivor_id and targets.get(rel.rel_id) in back,
        )

    def _reference_targets(self, relationships: Sequence[RelationshipRow]) -> dict[str, SourceKey]:
        """rel_id -> the source record its origin's reference names now."""
        out: dict[str, SourceKey] = {}
        by_entity: dict[str, list[RelationshipRow]] = {}
        for rel in relationships:
            by_entity.setdefault(rel.from_entity, []).append(rel)
        for from_entity, rels in by_entity.items():
            try:
                model = self.registry.published(from_entity)
            except NotFound:
                continue
            states = self.store.source_states(from_entity, sorted({r.origin for r in rels if r.origin}))
            for rel in rels:
                state = states.get(rel.origin) if rel.origin else None
                if state is None or not rel.origin_attribute:
                    continue
                try:
                    spec = model.attribute(rel.origin_attribute).reference
                except NotFound:
                    continue
                key = state.references.get(rel.origin_attribute)
                if spec is not None and isinstance(key, str):
                    out[rel.rel_id] = SourceKey(spec.key_source, key)
        return out

    # ------------------------------------------------------------------ writing

    def _write(self, cs: ChangeSet, model: EntityModel, plan: _Plan, version: int) -> tuple[Counter, int]:
        store = self.store
        counts: Counter = Counter()
        golden_rows = [
            (plan.golden_before.get(m) if m not in plan.created.values() else None, row)
            for m, row in sorted(plan.golden_after.items())
        ]
        written = store.write_golden(cs.entity, golden_rows)
        counts["golden"] = len(golden_rows)
        xref_rows = [after for _, after in sorted(plan.xrefs.values(), key=lambda p: p[1].source)]
        store.write_xrefs(xref_rows)
        counts["xref"] = len(xref_rows)
        for retired_id, members in plan.merge_members:
            if members:
                store.write_merge_members(cs.entity, retired_id, version, members)
        retired = [after for _, after in sorted(plan.retired.values(), key=lambda p: p[1].retired_id)]
        store.write_retired(retired)
        counts["retired_id"] = len(retired)
        relationships = [after for _, after in sorted(plan.relationships.values(), key=lambda p: p[1].rel_id)]
        store.write_relationships(relationships)
        counts["relationship"] = len(relationships)
        if plan.provenance:
            store.write_provenance(
                cs.entity,
                {m: after for m, (_, after) in sorted(plan.provenance.items())},
                model.survivorship.version,
                version,
            )
        del written
        return counts, len(golden_rows) + len(xref_rows) + len(retired) + len(relationships)

    @staticmethod
    def _change_rows(plan: _Plan, version: int) -> list[ChangeRow]:
        rows: list[ChangeRow] = []
        for seq, ((entity, master_id), change) in enumerate(sorted(plan.changes.items()), start=1):
            rows.append(
                ChangeRow(
                    commit_version=version,
                    change_seq=seq,
                    entity=entity,
                    master_id=master_id,
                    change_kind=change.kind,
                    survivor_id=change.survivor_id if change.kind in ("merged", "remapped") else None,
                    parts=tuple(p for p in CHANGE_PARTS if p in change.parts),
                )
            )
        return rows

    def _map_work(self, work: WorkWrites, mapping: Mapping[str, str], cs: ChangeSet) -> WorkWrites:
        """Tasks naming CreateGolden refs name the master IDs instead; a task without a source is re-keyed."""
        if not work.tasks or not any(is_ref(m) for t in work.tasks for m in t.master_ids):
            return work
        tasks: list[Task] = []
        for task in work.tasks:
            if not any(is_ref(m) for m in task.master_ids):
                tasks.append(task)
                continue
            master_ids = tuple(mapping.get(m, m) for m in task.master_ids)
            if any(is_ref(m) for m in master_ids):
                continue  # its cluster was never created: nothing to name
            if task.source is None:
                key = task_key(task.kind, task.entity, None, master_ids)
                tasks.append(
                    replace(
                        task,
                        master_ids=master_ids,
                        task_key=key,
                        task_id=task_id(key, task.event_id or cs.fingerprint),
                    )
                )
            else:
                tasks.append(replace(task, master_ids=master_ids))
        return replace(work, tasks=tuple(tasks))

    # ------------------------------------------------------------------ the audit

    @staticmethod
    def _audited(cs: ChangeSet) -> ChangeSet:
        """The change set with the count of items per clause in its evidence."""
        clauses = Counter(item_clause(i) for i in cs.items if item_clause(i))
        return replace(cs, evidence={**dict(cs.evidence), "clauses": dict(sorted(clauses.items()))})

    def _golden_doc(
        self, model: EntityModel, row: GoldenRow | None, provenance: Mapping[str, Any] | None
    ) -> dict[str, Any] | None:
        """A golden row for the audit: personal values as vault references from its provenance."""
        if row is None:
            return None
        personal = set(model.personal_attributes())
        values: dict[str, Any] = {}
        for name, value in row.values.items():
            if name in personal and value is not None:
                entry = (provenance or {}).get(name)
                winner = entry.get("winner") if isinstance(entry, Mapping) else None
                ref = winner.get("value") if isinstance(winner, Mapping) else None
                values[name] = ref if is_vault_ref(ref) else vault_ref(None)
            else:
                values[name] = plain(value)
        return {
            "status": row.status,
            "survivor_id": row.survivor_id,
            "values": values,
            "_commit_version": row.commit_version,
            "_row_version": row.row_version,
        }

    def _change_log(
        self, cs: ChangeSet, model: EntityModel, plan: _Plan, version: int, counts: Mapping[str, int]
    ) -> list[tuple]:
        rows: list[tuple] = []

        def add(table: str, key: str, op: str, clause: str | None, before: Any, after: Any) -> None:
            rows.append(
                (
                    f"{cs.change_set_id}:{len(rows) + 1}",
                    version,
                    cs.change_set_id,
                    table,
                    key,
                    op,
                    clause or None,
                    before,
                    after,
                )
            )

        if cs.initial_load:
            clauses = Counter(item_clause(i) for i in cs.items if item_clause(i))
            add(
                "summary",
                f"commit:{version}",
                "summary",
                None,
                None,
                {"tables": dict(sorted(counts.items())), "clauses": dict(sorted(clauses.items()))},
            )
            return rows
        stored = plan.stored_provenance
        for master_id, after in sorted(plan.golden_after.items()):
            before = None if master_id in plan.created.values() else plan.golden_before.get(master_id)
            old_prov = stored.get(master_id)
            new_prov = plan.provenance.get(master_id, (None, old_prov))[1]
            add(
                cs.entity,
                master_id,
                "insert" if before is None else "update",
                plan.clause_of.get(f"golden:{master_id}"),
                self._golden_doc(model, before, old_prov),
                self._golden_doc(model, after, new_prov),
            )
        for source, (before, after) in sorted(plan.xrefs.items()):
            add(
                "xref",
                f"{cs.entity}:{source.text()}",
                "insert" if before is None else "update",
                plan.clause_of.get(f"xref:{source.text()}"),
                None if before is None else {"master_id": before.master_id, "status": before.status},
                {"master_id": after.master_id, "status": after.status},
            )
        for retired_id, (before, after) in sorted(plan.retired.items()):
            add(
                "retired_id",
                retired_id,
                "insert" if before is None else "update",
                None,
                None if before is None else _retired_doc(before),
                _retired_doc(after),
            )
        for rel_id, (before, after) in sorted(plan.relationships.items()):
            add(
                "relationship",
                rel_id,
                "insert" if before is None else "update",
                plan.clause_of.get(f"relationship:{rel_id}"),
                None if before is None else _relationship_doc(before),
                _relationship_doc(after),
            )
        for master_id, (before, after) in sorted(plan.provenance.items()):
            add(
                "provenance",
                master_id,
                "insert" if before is None else "update",
                plan.clause_of.get(f"golden:{master_id}"),
                before,
                after,
            )
        return rows


def _retired_doc(row: RetiredRow) -> dict[str, Any]:
    return {
        "merged_into": row.merged_into,
        "merge_version": row.merge_version,
        "survivor_id": row.survivor_id,
        "active": row.active,
    }


def _relationship_doc(row: RelationshipRow) -> dict[str, Any]:
    def day(value: date | None) -> str | None:
        return iso(value) if value is not None else None

    return {
        "rel_type": row.rel_type,
        "from": row.from_master_id,
        "to": row.to_master_id,
        "status": row.status,
        "valid_from": day(row.valid_from),
        "valid_to": day(row.valid_to),
        "origin": row.origin.text() if row.origin else None,
        "origin_attribute": row.origin_attribute,
    }


def fingerprint_of(items: Sequence[ChangeItem]) -> str:
    """A short digest of items, for logs."""
    return hashlib.sha256(repr([item_kind(i) for i in items]).encode()).hexdigest()[:12]
