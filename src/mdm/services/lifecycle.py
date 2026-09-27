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
        return self.commit.apply(cs, work)

    @staticmethod
    def _require_reason(reason: str) -> None:
        if not reason or not reason.strip():
            raise Forbidden("reason_required")

    # ------------------------------------------------------------------ link and detach

    def link(
        self, entity: str, source: SourceKey, master_id: str, *, actor: Actor, reason: str
    ) -> CommitResult:
        require(actor, "link")
        self._require_reason(reason)
        model = self.registry.published(entity)
        state = self._state(entity, source)
        target = self._golden(entity, model, master_id)
        if target.status != "active":
            raise Conflict([token(master_id)], code="not_active")
        current = self.store.xrefs_for_sources(entity, [source]).get(source)
        if current == master_id:
            return CommitResult("", None, {}, {})
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
        work = WorkWrites(
            entity,
            approve=((source, state.event_id),),
            release=(source,) if state.held else (),
            tasks=tuple(tasks),
        )
        return self._commit(entity, "link", actor, items, reason, work=work)

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
