"""A person's actions on golden records: link, detach, merge, unmerge, retire, reinstate (owner: SERVICES, B.10).

Every action is a change set through `CommitService` with its expected states.
Link and detach carry `expected_master_id`, approve the record's current values
and recompute both golden records. Merge moves the retired record's active
cross-references to the survivor (recorded in `merge_member`), recomputes the
values, repoints relationships at both ends, tombstones the retired record,
writes or replaces its `retired_id` row and remaps the chains through it.
Unmerge moves exactly the members `merge_member` recorded back, recomputes both,
repoints the relationships whose origin is among them, deactivates the map row
and remaps the chains. Retire needs an orphan; reinstate takes a `retired`
record only. Merges across entities are refused. Merge, unmerge and retire
need a checker other than the maker.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any

from mdm.backend.store import SqlStore
from mdm.engine.survive import Member, survive
from mdm.models.authority import Actor
from mdm.models.canonical import utcnow
from mdm.models.changes import (
    ChangeItem,
    CommitResult,
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
    new_change_set,
)
from mdm.models.entity_model import EntityModel
from mdm.models.errors import Conflict, Forbidden, NotFound
from mdm.models.records import GoldenRow, SourceKey, SourceState
from mdm.models.safety import safe_detail
from mdm.models.tasks import Task, task_id, task_key
from mdm.services.authority import require, role_authority
from mdm.services.commit import MOVE_LIMIT, CommitService
from mdm.services.registry import ModelRegistry
from mdm.services.support import plain, relationship_id, source_token, token


class LifecycleService:
    def __init__(
        self,
        store: SqlStore,
        registry: ModelRegistry,
        commit: CommitService,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.store = store
        self.registry = registry
        self.commit = commit
        self.clock = clock

    # ------------------------------------------------------------------ helpers

    def _golden(self, entity: str, model: EntityModel, master_id: str) -> GoldenRow:
        if not master_id.startswith(f"{model.code}-"):
            raise Forbidden("across_entities", entity=entity, master_id=token(master_id))
        row = self.store.golden(entity, [master_id]).get(master_id)
        if row is None:
            raise NotFound("unknown_master_id", entity=entity, master_id=token(master_id))
        return row

    def _state(self, entity: str, source: SourceKey) -> SourceState:
        state = self.store.source_states(entity, [source]).get(source)
        if state is None or state.status != "active":
            raise NotFound("unknown_source_record", entity=entity, source=source_token(source))
        return state

    def _survive(
        self,
        model: EntityModel,
        master_id: str | None,
        sources: Sequence[SourceKey],
        current: Sequence[SourceKey] = (),
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Values and provenance from the members' approved values (current values for `current`)."""
        states = self.store.source_states(model.entity, sorted(set(sources)))
        steward = self.store.steward_values(model.entity, [master_id]).get(master_id, {}) if master_id else {}
        members: list[Member] = []
        for source in sorted(set(sources)):
            state = states.get(source)
            if state is None or state.status != "active":
                continue
            values = state.values if source in current else state.approved_values
            if values is None:
                continue
            members.append(Member(source, values, state.occurred_at, state.landing_seq))
        values, provenance = survive(
            model, model.survivorship, members, steward, self.clock(), model.survivorship.version
        )
        return plain(values), plain(provenance)

    def _members(self, entity: str, master_id: str) -> list[SourceKey]:
        return self.store.members(entity, [master_id], MOVE_LIMIT).get(master_id, [])

    def values_without(self, entity: str, master_id: str, source: SourceKey) -> dict[str, Any]:
        """The golden record's values as its other active members' approved values survive them, with no
        steward pin (a pin may have been taken from `source`): what a blind review shows of the golden record
        that holds the record under review, so no value reads as the same because the record won it. Written
        nothing; {} when no other member is left."""
        model = self.registry.published(entity)
        others = [m for m in self._members(entity, master_id) if m != source]
        if not others:
            return {}
        values, _ = self._survive(model, None, others)
        return values

    def _relationship_items(
        self, model: EntityModel, state: SourceState, master_id: str | None, clause: str = ""
    ) -> list[ChangeItem]:
        """The record's references as relationships from `master_id`; every one ended when it is None."""
        items: list[ChangeItem] = []
        references = model.reference_attributes()
        if not references:
            return items
        existing = self.store.relationships_by_origin([(state.source, a.name) for a in references])
        valid = self.clock().date()
        for attribute in references:
            spec = attribute.reference
            if spec is None:
                continue
            keep = None
            key = state.references.get(attribute.name) if master_id else None
            if isinstance(key, str):
                target = self.store.xrefs_for_sources(spec.entity, [SourceKey(spec.key_source, key)])
                to = next(iter(target.values()), None)
                if to is not None:
                    keep = relationship_id(
                        spec.relationship, state.source, attribute.name, spec.key_source, key
                    )
                    items.append(
                        UpsertRelationship(
                            keep,
                            spec.relationship,
                            master_id,
                            spec.entity,
                            to,
                            state.occurred_at.date(),
                            {},
                            state.source,
                            attribute.name,
                            clause,
                        )
                    )
            for rel in existing:
                if rel.origin_attribute == attribute.name and rel.status == "active" and rel.rel_id != keep:
                    items.append(EndRelationship(rel.rel_id, valid, clause))
        return items

    def _orphan_task(self, entity: str, master_id: str, reason: str) -> Task:
        now = self.clock()
        key = task_key("orphan", entity, None, (master_id,))
        return Task(
            task_id=task_id(key, f"orphan:{master_id}:{now.isoformat()}"),
            task_key=key,
            entity=entity,
            kind="orphan",
            status="open",
            source=None,
            master_ids=(master_id,),
            reason=reason,
            suggestion=safe_detail(action="retire_or_keep"),
            evidence=safe_detail(master_id=master_id),
            event_id=None,
            created_at=now,
            updated_at=now,
        )

    def _commit(
        self,
        entity: str,
        action: str,
        actor: Actor,
        items: Sequence[ChangeItem],
        reason: str,
        *,
        checker: Actor | None = None,
        work: WorkWrites | None = None,
        evidence: Mapping[str, Any] | None = None,
        audit_always: bool = False,
    ) -> CommitResult:
        cs = new_change_set(
            entity,
            action,
            actor,
            role_authority(actor, checker),
            items,
            planning_version=self.store.last_commit_version(),
            reason=reason,
            checker=checker,
            evidence=dict(evidence or {}),
        )
        return self.commit.apply(cs, work, audit_always=audit_always)

    @staticmethod
    def _require_reason(reason: str) -> None:
        if not reason or not reason.strip():
            raise Forbidden("reason_required")

    # ------------------------------------------------------------------ link and detach

    def plan_link(
        self, entity: str, source: SourceKey, master_id: str, *, event_id: str | None = None
    ) -> tuple[list[ChangeItem], WorkWrites]:
        """The items and work writes of linking `source` to `master_id`, written nothing: the link, the target
        recomputed with the record's current values, the record's former golden record recomputed (or an
        orphan task), its relationships; the work approves the record at `event_id` (default: its current
        event) and releases a hold. No item when the record is linked to the target already."""
        model = self.registry.published(entity)
        state = self._state(entity, source)
        target = self._golden(entity, model, master_id)
        if target.status != "active":
            raise Conflict([token(master_id)], code="not_active")
        current = self.store.xrefs_for_sources(entity, [source]).get(source)
        work = WorkWrites(
            entity,
            approve=((source, event_id or state.event_id),),
            release=(source,) if state.held else (),
        )
        if current == master_id:
            return [], work
        items: list[ChangeItem] = [LinkSource(source, master_id, current)]
        members = self._members(entity, master_id)
        values, provenance = self._survive(model, master_id, [*members, source], current=[source])
        items.append(UpdateGolden(master_id, target.row_version, values, provenance))
        tasks: list[Task] = []
        if current is not None:
            old = self._golden(entity, model, current)
            remaining = [m for m in self._members(entity, current) if m != source]
            if remaining:
                old_values, old_provenance = self._survive(model, current, remaining)
                items.append(UpdateGolden(current, old.row_version, old_values, old_provenance))
            else:
                tasks.append(self._orphan_task(entity, current, "last_member_moved"))
        items.extend(self._relationship_items(model, state, master_id))
        return items, replace(work, tasks=tuple(tasks))

    def link(
        self,
        entity: str,
        source: SourceKey,
        master_id: str,
        *,
        actor: Actor,
        reason: str,
        event_id: str | None = None,
        work: WorkWrites | None = None,
        evidence: Mapping[str, Any] | None = None,
    ) -> CommitResult:
        """Links the record to the golden record. With `work` (the workbench's), the steward's writes commit in
        the same transaction, always audited, and the method never returns without applying them: a record
        linked to the target already commits an audited change set with no items."""
        require(actor, "link")
        self._require_reason(reason)
        items, own = self.plan_link(entity, source, master_id, event_id=event_id)
        if not items:
            if work is None:
                return CommitResult("", None, {}, {})
            return self.decide_only(entity, "link", actor=actor, reason=reason, work=work, evidence=evidence)
        return self._commit(
            entity,
            "link",
            actor,
            items,
            reason,
            work=own.merged(work) if work is not None else own,
            evidence=evidence,
            audit_always=work is not None,
        )

    # ------------------------------------------------------------------ a signature batch's chunk (story 3.3)

    def plan_batch_links(
        self, entity: str, links: Sequence[tuple[SourceKey, str, str | None]]
    ) -> tuple[list[ChangeItem], WorkWrites]:
        """The items and work writes of one batch chunk's links, `(record, target, event)` each, written
        nothing: a link per record (each unlinked, else `Conflict(record_changed)`); one `UpdateGolden` per
        distinct target, surviving its members' approved values with every joiner's current values, so two
        rows into one golden record never overwrite each other; each record's relationships. The work approves
        each record at its event and releases a hold. A target no longer active is `Conflict(target_changed)`."""
        model = self.registry.published(entity)
        sources = [source for source, _, _ in links]
        states = self.store.source_states(entity, sources)
        linked = self.store.xrefs_for_sources(entity, sources)
        changed = sorted(
            source_token(s) for s in sources if s in linked or s not in states or states[s].status != "active"
        )
        if changed:
            raise Conflict(changed, code="record_changed")
        joiners: dict[str, list[SourceKey]] = {}
        for source, target, _ in links:
            joiners.setdefault(target, []).append(source)
        golden = self.store.golden(entity, list(joiners))
        items: list[ChangeItem] = [LinkSource(source, target, None) for source, target, _ in links]
        for target, joining in joiners.items():
            row = golden.get(target)
            if row is None or row.status != "active":
                raise Conflict([token(target)], code="target_changed")
            members = self._members(entity, target)
            values, provenance = self._survive(model, target, [*members, *joining], current=joining)
            items.append(UpdateGolden(target, row.row_version, values, provenance))
        for source, target, _ in links:
            items.extend(self._relationship_items(model, states[source], target))
        work = WorkWrites(
            entity,
            approve=tuple((source, event or states[source].event_id) for source, _, event in links),
            release=tuple(source for source in sources if states[source].held),
        )
        return items, work

    def plan_batch_detaches(
        self, entity: str, detaches: Sequence[tuple[SourceKey, str]]
    ) -> tuple[list[ChangeItem], WorkWrites]:
        """The items and work writes of one compensation chunk, `(record, golden record)` each, written nothing:
        a detach per record (still linked there, else `Conflict(record_changed)`); one `UpdateGolden` per
        golden record, over its members less every record this chunk detaches, or an orphan task
        (`last_member_detached`) when none is left; each record's relationships ended; each record approved at
        its current event."""
        model = self.registry.published(entity)
        sources = [source for source, _ in detaches]
        states = self.store.source_states(entity, sources)
        linked = self.store.xrefs_for_sources(entity, sources)
        changed = sorted(
            source_token(s)
            for s, target in detaches
            if linked.get(s) != target or s not in states or states[s].status != "active"
        )
        if changed:
            raise Conflict(changed, code="record_changed")
        leaving: dict[str, set[SourceKey]] = {}
        for source, target in detaches:
            leaving.setdefault(target, set()).add(source)
        golden = self.store.golden(entity, list(leaving))
        items: list[ChangeItem] = [DetachSource(source, target) for source, target in detaches]
        tasks: list[Task] = []
        for target, gone in leaving.items():
            row = golden.get(target)
            if row is None or row.status != "active":
                raise Conflict([token(target)], code="target_changed")
            remaining = [m for m in self._members(entity, target) if m not in gone]
            if remaining:
                values, provenance = self._survive(model, target, remaining)
                items.append(UpdateGolden(target, row.row_version, values, provenance))
            else:
                tasks.append(self._orphan_task(entity, target, "last_member_detached"))
        for source, _ in detaches:
            items.extend(self._relationship_items(model, states[source], None))
        work = WorkWrites(
            entity,
            approve=tuple((source, states[source].event_id) for source in sources),
            tasks=tuple(tasks),
        )
        return items, work

    def values_with(
        self,
        entity: str,
        master_id: str,
        joiners: Sequence[SourceKey] = (),
        *,
        without: Sequence[SourceKey] = (),
    ) -> dict[str, Any]:
        """The golden record's values as they would survive with `joiners` joining it (their current values)
        and `without` leaving it, written nothing: a batch's before-and-after, and a compensation's. {} when no
        member is left."""
        model = self.registry.published(entity)
        gone = set(without)
        members = [m for m in self._members(entity, master_id) if m not in gone]
        joining = [s for s in joiners if s not in gone]
        if not members and not joining:
            return {}
        values, _ = self._survive(model, master_id, [*members, *joining], current=joining)
        return values

    @staticmethod
    def relationship_rows(model: EntityModel) -> int:
        """The relationship rows one link may write, as an upper bound for packing a batch's chunks: one per
        reference attribute of the entity."""
        return len(model.reference_attributes())

    # ------------------------------------------------------------------ a steward's decisions on held updates

    def plan_approve_update(
        self, entity: str, source: SourceKey, *, event_id: str | None = None
    ) -> tuple[list[ChangeItem], WorkWrites]:
        """The items and work writes of taking a held update into the golden record, written nothing: the
        golden record recomputed with the record's current values, and its relationships; the work approves
        the record at `event_id` (default: its current event) and releases the hold. `Conflict(not_held)` when
        the record is not linked or not held."""
        model = self.registry.published(entity)
        state = self._state(entity, source)
        master_id = self.store.xrefs_for_sources(entity, [source]).get(source)
        if master_id is None or not state.held:
            raise Conflict([source_token(source)], code="not_held")
        golden = self._golden(entity, model, master_id)
        if golden.status != "active":
            raise Conflict([token(master_id)], code="not_active")
        members = self._members(entity, master_id)
        values, provenance = self._survive(model, master_id, [*members, source], current=[source])
        items: list[ChangeItem] = [UpdateGolden(master_id, golden.row_version, values, provenance)]
        items.extend(self._relationship_items(model, state, master_id))
        work = WorkWrites(entity, approve=((source, event_id or state.event_id),), release=(source,))
        return items, work

    def approve_update(
        self,
        entity: str,
        source: SourceKey,
        *,
        actor: Actor,
        reason: str,
        event_id: str | None = None,
        work: WorkWrites | None = None,
        evidence: Mapping[str, Any] | None = None,
    ) -> CommitResult:
        """A steward's decision alone (rule RULE2): the value the source asserted and its policy held reaches
        the golden record, and the hold is released. Always audited: when nothing publishes (another source
        outranks the new value), the change set and the work still commit."""
        require(actor, "approve_update")
        self._require_reason(reason)
        items, own = self.plan_approve_update(entity, source, event_id=event_id)
        return self._commit(
            entity,
            "approve_update",
            actor,
            items,
            reason,
            work=own.merged(work) if work is not None else own,
            evidence=evidence,
            audit_always=True,
        )

    def decide_only(
        self,
        entity: str,
        action: str,
        *,
        actor: Actor,
        reason: str,
        work: WorkWrites,
        evidence: Mapping[str, Any] | None = None,
    ) -> CommitResult:
        """A steward's decision that publishes nothing (not a match, keep apart, keep an orphan, reject a held
        update, or a link that holds already): a change set with no items, audited, with the work."""
        require(actor, action)
        self._require_reason(reason)
        self.registry.published(entity)  # NotFound for an entity with no published model
        return self._commit(
            entity, action, actor, [], reason, work=work, evidence=evidence, audit_always=True
        )

    def detach(self, entity: str, source: SourceKey, *, actor: Actor, reason: str) -> CommitResult:
        require(actor, "detach")
        self._require_reason(reason)
        model = self.registry.published(entity)
        state = self._state(entity, source)
        current = self.store.xrefs_for_sources(entity, [source]).get(source)
        if current is None:
            raise NotFound("not_linked", entity=entity, source=source_token(source))
        golden = self._golden(entity, model, current)
        items: list[ChangeItem] = [DetachSource(source, current)]
        tasks: list[Task] = []
        remaining = [m for m in self._members(entity, current) if m != source]
        if remaining:
            values, provenance = self._survive(model, current, remaining)
            items.append(UpdateGolden(current, golden.row_version, values, provenance))
        else:
            tasks.append(self._orphan_task(entity, current, "last_member_detached"))
        items.extend(self._relationship_items(model, state, None))
        work = WorkWrites(entity, approve=((source, state.event_id),), tasks=tuple(tasks))
        return self._commit(entity, "detach", actor, items, reason, work=work)

    # ------------------------------------------------------------------ merge and unmerge

    @staticmethod
    def _checked(action: str, maker: Actor, checker: Actor | None) -> None:
        require(maker, action)
        if checker is None:
            raise Forbidden("checker_required", action=action)
        if checker.name == maker.name:
            raise Forbidden("checker_is_maker", action=action)
        require(checker, action)

    def merge(
        self, entity: str, survivor_id: str, retired_id: str, *, maker: Actor, checker: Actor, reason: str
    ) -> CommitResult:
        self._checked("merge", maker, checker)
        self._require_reason(reason)
        model = self.registry.published(entity)
        if survivor_id == retired_id:
            raise Forbidden("merge_into_itself", master_id=token(survivor_id))
        survivor = self._golden(entity, model, survivor_id)
        retired = self._golden(entity, model, retired_id)
        if survivor.status != "active" or retired.status != "active":
            raise Conflict([token(survivor_id), token(retired_id)], code="not_active")
        members = [*self._members(entity, survivor_id), *self._members(entity, retired_id)]
        values, provenance = self._survive(model, survivor_id, members)
        item = MergeGolden(
            survivor_id, retired_id, (survivor.row_version, retired.row_version), values, provenance
        )
        return self._commit(entity, "merge", maker, [item], reason, checker=checker)

    def unmerge(
        self, entity: str, retired_id: str, *, maker: Actor, checker: Actor, reason: str
    ) -> CommitResult:
        self._checked("unmerge", maker, checker)
        self._require_reason(reason)
        model = self.registry.published(entity)
        retired = self._golden(entity, model, retired_id)
        if retired.status != "merged":
            raise Forbidden("unmerge_needs_merged", master_id=token(retired_id))
        row = self.store.retired_rows([retired_id]).get(retired_id)
        if row is None or not row.active:
            raise Conflict([token(retired_id)], code="not_merged")
        survivor_id = row.survivor_id
        survivor = self._golden(entity, model, survivor_id)
        moved = self.store.merge_members(retired_id, row.merge_version, MOVE_LIMIT)
        now_linked = self.store.xrefs_for_sources(entity, moved) if moved else {}
        back = tuple(s for s in moved if now_linked.get(s) == survivor_id)
        staying = [m for m in self._members(entity, survivor_id) if m not in set(back)]
        retired_values, retired_provenance = self._survive(model, retired_id, back)
        survivor_values, survivor_provenance = self._survive(model, survivor_id, staying)
        item = UnmergeGolden(
            retired_id=retired_id,
            survivor_id=survivor_id,
            merge_version=row.merge_version,
            expected_row_versions=(retired.row_version, survivor.row_version),
            members_back=back,
            retired_values=retired_values,
            survivor_values=survivor_values,
            provenance={retired_id: retired_provenance, survivor_id: survivor_provenance},
        )
        return self._commit(entity, "unmerge", maker, [item], reason, checker=checker)

    # ------------------------------------------------------------------ retire and reinstate

    def retire(
        self, entity: str, master_id: str, *, maker: Actor, checker: Actor, reason: str
    ) -> CommitResult:
        self._checked("retire", maker, checker)
        self._require_reason(reason)
        model = self.registry.published(entity)
        row = self._golden(entity, model, master_id)
        if row.status != "active":
            raise Conflict([token(master_id)], code="not_active")
        members = self.store.member_counts(entity, [master_id]).get(master_id, 0)
        if members:
            raise Forbidden("retire_needs_orphan", master_id=token(master_id), members=members)
        return self._commit(
            entity, "retire", maker, [RetireGolden(master_id, row.row_version)], reason, checker=checker
        )

    def reinstate(self, entity: str, master_id: str, *, actor: Actor, reason: str) -> CommitResult:
        require(actor, "reinstate")
        self._require_reason(reason)
        model = self.registry.published(entity)
        row = self._golden(entity, model, master_id)
        if row.status == "merged":
            raise Forbidden("reinstate_merged_needs_unmerge", master_id=token(master_id))
        if row.status != "retired":
            raise Conflict([token(master_id)], code="not_retired")
        return self._commit(entity, "reinstate", actor, [ReinstateGolden(master_id, row.row_version)], reason)
