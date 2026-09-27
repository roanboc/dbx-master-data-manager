"""The arrival job: landing rows to source states, the queue, and automated commits (owner: SERVICES, B.10, B.6.3).

`run()`: requires `run_arrival`; takes the "arrival" lease (not held ->
`skipped_busy`, the CLI prints one line and exits 0); a job_run row with
heartbeats; `reader.reconcile()` when due, its rows through `intake`;
`settle()` drains what an earlier run left queued; then batches: `next_batch`
-> `intake` -> `settle` until empty; `reader.tick()` last, so a gap past its
timeout is probed by this run before it can be declared lost.

`intake(rows, batch)`: rejects with reasons and attribute names (a row over the
landing contract's size limits is `too_large`); standardise;
vault the personal values in their own short transaction; `put_source_versions`
(fault point "after_versions"); then one transaction: each record's state
moves only forward by its version key, a moved record gets its state, blocking
keys, rule failures and a queue row at its new event; the reader's position
(fault point "after_intake").

`settle()`: per entity, a page of the queue in landing order: deletes, candidates
(stop keys dropped, pairs deduplicated, capped, `fast_weight`, `explain` at or
above the lower band), golden candidates with `blocked_by`, decisions
(`decide_update`, `resolve_batch` + `decide_new`), references to relationships,
survivorship, one automated change set per entity and page with its
`WorkWrites`, `commit.apply_chunked` (fault point "after_chunk"). On `Conflict`
the page is re-planned once; a second `Conflict` turns the keys into
`exception` tasks and commits the rest.

Exactly once: a known event adds no version; a state moves only forward; a
queued record is settled only by the transaction that carries its effect (the
commit, or the task write when nothing publishes), so a crash anywhere leaves it
queued and the next run drains the queue first.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

import mdm.capacity as capacity
from mdm.backend.store import SqlStore
from mdm.config import Settings
from mdm.engine.cluster import ClusterInput, Resolution, resolve_batch
from mdm.engine.quality import check
from mdm.engine.score import explain, fast_weight
from mdm.engine.standardise import sample_hash, standardise_record
from mdm.engine.survive import Member, survive
from mdm.models.authority import AUTOMATED_MATCHER, Actor, Authority
from mdm.models.canonical import utcnow
from mdm.models.changes import (
    ChangeItem,
    ChangeSet,
    CreateGolden,
    DetachSource,
    EndRelationship,
    LinkSource,
    UpdateGolden,
    UpsertRelationship,
    WorkWrites,
)
from mdm.models.entity_model import ATTRIBUTE_TYPES, EntityModel, SourceSpec
from mdm.models.errors import Conflict, MdmError, NotFound
from mdm.models.match import Band, Explanation, GoldenCandidate, PairScore
from mdm.models.records import Reject, RuleResult, SourceChange, SourceKey, SourceState, StdRecord
from mdm.models.safety import safe_detail, safe_message
from mdm.models.tasks import Task, task_id, task_key
from mdm.services.authority import AuthorityService, require
from mdm.services.codelists import CodeListService, code_lists_of
from mdm.services.commit import CommitService, RateLimiter
from mdm.services.landing import Batch, LandingReader
from mdm.services.matching import MatchService, conflict
from mdm.services.policy import COMMITTING, Plan, decide_delete, decide_new, decide_update, rule1_clause
from mdm.services.privacy import Vault, source_subject, vault_ref
from mdm.services.registry import ModelRegistry
from mdm.services.support import (
    HINT_KEY,
    REF_PREFIX,
    fingerprint,
    is_ref,
    plain,
    relationship_id,
    source_token,
    token,
)

log = logging.getLogger("mdm.arrival")

FAULT_POINTS = ("after_versions", "after_intake", "in_commit", "after_chunk")
_SCALAR_TYPES = frozenset(t for t in ATTRIBUTE_TYPES if t not in ("json", "reference"))
_SUGGESTION = {
    "review": "decide_link_or_create",
    "possible_duplicate": "decide_merge_or_keep",
    "held": "approve_or_reject",
    "exception": "investigate",
    "orphan": "retire_or_keep",
    "unresolved_reference": "wait_or_investigate",
}


@dataclass
class ArrivalReport:
    read: int = 0
    rejected: int = 0
    versions: int = 0
    stale: int = 0
    queued: int = 0
    settled: int = 0
    created: int = 0
    linked: int = 0
    updated: int = 0
    detached: int = 0
    tasks: dict[str, int] = field(default_factory=dict)
    commits: int = 0
    first_version: int | None = None
    last_version: int | None = None
    high_water: int = 0
    low_water: int = 0
    gaps_open: int = 0
    gaps_lost: int = 0
    candidates_capped: int = 0
    skipped_busy: bool = False
    seconds: float = 0.0

    @property
    def records_per_second(self) -> float:
        return self.read / self.seconds if self.seconds > 0 else 0.0

    def add(self, other: ArrivalReport) -> None:
        """Accumulate another report's counts into this one (versions keep the first and the last)."""
        for name in (
            "read",
            "rejected",
            "versions",
            "stale",
            "queued",
            "settled",
            "created",
            "linked",
            "updated",
            "detached",
            "commits",
            "candidates_capped",
        ):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        for kind, count in other.tasks.items():
            self.tasks[kind] = self.tasks.get(kind, 0) + count
        if other.first_version is not None and (
            self.first_version is None or other.first_version < self.first_version
        ):
            self.first_version = other.first_version
        if other.last_version is not None and (
            self.last_version is None or other.last_version > self.last_version
        ):
            self.last_version = other.last_version
        self.skipped_busy = self.skipped_busy or other.skipped_busy

    def committed(self, versions: Sequence[int | None]) -> None:
        for version in versions:
            if version is None:
                continue
            self.commits += 1
            self.first_version = version if self.first_version is None else min(self.first_version, version)
            self.last_version = version if self.last_version is None else max(self.last_version, version)

    def progress(self) -> dict[str, Any]:
        """A safe detail for the job row's heartbeat."""
        return safe_detail(
            read=self.read,
            rejected=self.rejected,
            settled=self.settled,
            created=self.created,
            linked=self.linked,
            updated=self.updated,
            commits=self.commits,
        )


# --------------------------------------------------------------------------------------------- intake


@dataclass(frozen=True, slots=True)
class _Accepted:
    change: SourceChange
    model: EntityModel
    spec: SourceSpec
    std: StdRecord | None  # None for a delete
    results: tuple[RuleResult, ...]
    payload: Mapping[str, Any]  # the payload's JSON form
    values: Mapping[str, Any] | None = None  # the standardised values' JSON form (as the store returns them)
    match: Mapping[str, Any] | None = None  # the match forms' JSON form


def _longest_text(value: Any) -> int:
    """The length of the longest text anywhere inside `value`."""
    if isinstance(value, str):
        return len(value)
    if isinstance(value, Mapping):
        return max((_longest_text(v) for v in value.values()), default=0)
    if isinstance(value, (list, tuple)):
        return max((_longest_text(v) for v in value), default=0)
    return 0


def _size_problems(payload: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """(the payload is over MAX_PAYLOAD_BYTES, the attribute names over the text or group limits)."""
    names = sorted(
        token(str(name))[:60]
        for name, value in payload.items()
        if _longest_text(value) > capacity.MAX_TEXT_CHARS
        or (isinstance(value, list) and len(value) > capacity.MAX_GROUP_ITEMS)
    )
    whole = len(json.dumps(payload, separators=(",", ":"), default=str).encode()) > capacity.MAX_PAYLOAD_BYTES
    return whole, names


def _type_problems(model: EntityModel, payload: Mapping[str, Any]) -> list[str]:
    """Attribute names whose value has a JSON type the attribute can never hold."""
    problems: list[str] = []
    for attribute in model.attributes:
        value = payload.get(attribute.name)
        if value is None:
            continue
        if attribute.repeating:
            if not isinstance(value, list) or not all(isinstance(v, Mapping) for v in value):
                problems.append(attribute.name)
        elif attribute.type == "reference":
            if isinstance(value, (Mapping, list, bool)):
                problems.append(attribute.name)
        elif attribute.type in _SCALAR_TYPES:
            if isinstance(value, (Mapping, list)):
                problems.append(attribute.name)
            elif attribute.type in ("integer", "number") and isinstance(value, bool):
                problems.append(attribute.name)
    return problems


# --------------------------------------------------------------------------------------------- settling


@dataclass
class _PagePlan:
    """One page's change set, work writes and what the planner learnt, for the report and the conflict path."""

    items: list[ChangeItem] = field(default_factory=list)
    settle: list[tuple[SourceKey, str]] = field(default_factory=list)
    approve: list[tuple[SourceKey, str]] = field(default_factory=list)
    hold: list[SourceKey] = field(default_factory=list)
    release: list[SourceKey] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)
    pairs: dict[tuple[SourceKey, SourceKey], PairScore] = field(default_factory=dict)
    links: list[tuple[str, str]] = field(default_factory=list)
    touches: dict[SourceKey, set[str]] = field(default_factory=dict)
    pending: list[tuple] = field(default_factory=list)
    unpend: list[tuple] = field(default_factory=list)
    linked_now: list[SourceKey] = field(default_factory=list)  # sources linked or created by this page
    report: ArrivalReport = field(default_factory=ArrivalReport)
    initial_load: bool = False
    planning_version: int = 0


class ArrivalService:
    def __init__(
        self,
        settings: Settings,
        store: SqlStore,
        registry: ModelRegistry,
        codelists: CodeListService,
        authority: AuthorityService,
        vault: Vault,
        commit: CommitService,
        matching: MatchService,
        *,
        reader: LandingReader | None = None,
        clock: Callable[[], datetime] = utcnow,
        fault: Callable[[str], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """`fault(point)` is called at the FAULT_POINTS; tests raise there. `sleep` and `monotonic` drive the
        bulk throttle (a fake clock in tests)."""
        self.settings = settings
        self.store = store
        self.registry = registry
        self.codelists = codelists
        self.authority = authority
        self.vault = vault
        self.commit = commit
        self.matching = matching
        self.clock = clock
        self.reader = reader or LandingReader(store, gap_timeout=settings.gap_timeout_seconds, clock=clock)
        self.fault = fault
        self.sleep = sleep
        self.monotonic = monotonic
        self._throttle: RateLimiter | None = None

    def _fault(self, point: str) -> None:
        if self.fault is not None:
            self.fault(point)

    # ------------------------------------------------------------------ the run

    def run(
        self,
        *,
        started_by: Actor,
        max_batches: int | None = None,
        batch_size: int | None = None,
        bulk: bool = False,
    ) -> ArrivalReport:
        """Requires ACTIONS["run_arrival"]; the lease; a job_run row with heartbeat."""
        require(started_by, "run_arrival")
        began = time.perf_counter()
        report = ArrivalReport()
        size = batch_size or (capacity.BULK_BATCH if bulk else capacity.ARRIVAL_BATCH)
        with self.store.exclusive_lease("arrival") as held:
            if not held:
                report.skipped_busy = True
                return report
            run_id = self.store.start_job("arrival", started_by.name)
            self._throttle = self._make_throttle(bulk)
            try:
                if self.reader.due_for_reconcile():
                    report.add(self._reconcile_rows(bulk))
                report.add(self.settle(bulk=bulk))
                batches = 0
                while max_batches is None or batches < max_batches:
                    batch = self.reader.next_batch(size)
                    if not batch.rows:
                        break
                    report.add(self.intake(batch.rows, batch))
                    report.add(self.settle(bulk=bulk))
                    batches += 1
                    self.store.heartbeat(run_id, report.progress())
                self._position(report, self.reader.tick())
            except MdmError as exc:
                self._finish(run_id, "failed", exc.code)
                raise
            except BaseException:
                self._finish(run_id, "failed", "internal")
                raise
            finally:
                self._throttle = None
            report.seconds = time.perf_counter() - began
            self.store.heartbeat(run_id, report.progress())
            self._finish(run_id, "succeeded", None)
        return report

    def _finish(self, run_id: str, status: str, code: str | None) -> None:
        try:
            self.store.finish_job(run_id, status, code)
        except Exception:  # the job row is bookkeeping: never hide the run's own error behind it
            log.warning(safe_message("job_finish_failed", run_id=run_id))

    def _make_throttle(self, bulk: bool) -> RateLimiter | None:
        rows = self.settings.throttle_rows_per_hour
        if not bulk or rows <= 0:
            return None
        return RateLimiter(rows, sleep=self.sleep, clock=self.monotonic)

    @staticmethod
    def _position(report: ArrivalReport, tick: Any) -> None:
        report.high_water = tick.high_water
        report.low_water = tick.low_water
        report.gaps_open = tick.gaps_open
        report.gaps_lost = tick.gaps_lost

    def _reconcile_rows(self, bulk: bool) -> ArrivalReport:
        report = ArrivalReport()
        while True:
            batch = self.reader.reconcile(force=True)
            if batch.rows:
                report.add(self.intake(batch.rows, batch))
                report.add(self.settle(bulk=bulk))
            if len(batch.rows) < capacity.READ_PAGE:
                return report

    def reconcile(self, *, started_by: Actor) -> ArrivalReport:
        """Probe the lost gap ranges now (`mdm arrive --reconcile`), under the lease."""
        require(started_by, "run_arrival")
        with self.store.exclusive_lease("arrival") as held:
            if not held:
                return ArrivalReport(skipped_busy=True)
            report = self._reconcile_rows(bulk=False)
            self._position(report, self.reader.tick())
            return report

    def replay_rejects(self, *, started_by: Actor, limit: int = 1000) -> ArrivalReport:
        """Rejected landing rows read again after the model or the source was fixed, `limit` rejects a
        page until none is left; those read and accepted now are marked replayed, the others stay
        rejected (a reject whose landing row is gone is never read, so it is never marked)."""
        require(started_by, "run_arrival")
        limit = capacity.require_limit(limit, capacity.READ_PAGE)
        with self.store.exclusive_lease("arrival") as held:
            if not held:
                return ArrivalReport(skipped_busy=True)
            report = ArrivalReport()
            after: str | None = None
            while True:
                rejects = self.store.rejects(limit, after)
                if not rejects:
                    return report
                seqs = sorted({r.landing_seq for r in rejects})
                rows = self.store.landing_in_ranges([(s, s) for s in seqs], len(seqs))
                wanted = {r.event_id for r in rejects}
                rows = [r for r in rows if r.event_id in wanted]
                page, rejected_again = self._intake(rows, None)
                report.add(page)
                self.store.mark_rejects_replayed(sorted({r.event_id for r in rows} - rejected_again))
                report.add(self.settle())
                after = rejects[-1].event_id
                if len(rejects) < limit:
                    return report

    def process(self, changes: Sequence[SourceChange], *, bulk: bool = False) -> ArrivalReport:
        """`intake` then `settle` (tests, replay)."""
        report = self.intake(changes)
        report.add(self.settle(bulk=bulk))
        return report

    # ------------------------------------------------------------------ intake

    def intake(self, rows: Sequence[SourceChange], batch: Batch | None = None) -> ArrivalReport:
        report, _ = self._intake(rows, batch)
        return report

    def _model(self, entity: str, cache: dict[str, EntityModel | None]) -> EntityModel | None:
        if entity not in cache:
            try:
                cache[entity] = self.registry.published(entity)
            except NotFound:
                cache[entity] = None
        return cache[entity]

    def _check_row(
        self, change: SourceChange, models: dict[str, EntityModel | None]
    ) -> tuple[str | None, tuple[str, ...], EntityModel | None, SourceSpec | None]:
        """(reason, attribute names, model, source) of one landing row against the landing interface."""
        model = self._model(change.entity, models) if isinstance(change.entity, str) else None
        if model is None:
            return "unknown_entity", (), None, None
        if not model.has_source(change.source_system):
            return "unknown_source", (), model, None
        spec = model.source(change.source_system)
        if not isinstance(change.source_key, str) or not change.source_key.strip():
            return "missing_key", (), model, spec
        if change.op not in ("upsert", "delete"):
            return "bad_op", (), model, spec
        if spec.versioned and change.source_version is None:
            return "missing_version", (), model, spec
        payload = change.payload
        if not isinstance(payload, Mapping):
            return "bad_payload", (), model, spec
        whole, oversized = _size_problems(payload)
        if whole or oversized:
            return "too_large", tuple(oversized), model, spec
        known = {a.name for a in model.attributes} | {"master_id"}
        unknown = sorted(token(str(k))[:60] for k in payload if k not in known)
        if unknown:
            return "bad_payload", tuple(unknown), model, spec
        hint = payload.get("master_id")
        if hint is not None and not isinstance(hint, str):
            return "bad_type", ("master_id",), model, spec
        if change.op == "upsert":
            problems = _type_problems(model, payload)
            if problems:
                return "bad_type", tuple(problems), model, spec
        return None, (), model, spec

    def _intake(self, rows: Sequence[SourceChange], batch: Batch | None) -> tuple[ArrivalReport, set[str]]:
        report = ArrivalReport(read=len(rows))
        now = self.clock()
        today = now.date()
        models: dict[str, EntityModel | None] = {}
        rejects: list[Reject] = []
        accepted: list[_Accepted] = []
        snapshots: dict[str, tuple[Mapping[str, frozenset[str]], Mapping[str, int]]] = {}
        for change in rows:
            reason, attributes, model, spec = self._check_row(change, models)
            if reason is not None or model is None or spec is None:
                source = None
                if (
                    isinstance(change.source_system, str)
                    and isinstance(change.source_key, str)
                    and change.source_key
                ):
                    source = SourceKey(change.source_system, change.source_key)
                rejects.append(
                    Reject(
                        event_id=change.event_id,
                        landing_seq=change.landing_seq,
                        entity=change.entity if model is not None else token(str(change.entity)),
                        source=source,
                        reason=reason or "bad_payload",
                        attributes=attributes,
                    )
                )
                continue
            if change.op == "delete":
                accepted.append(_Accepted(change, model, spec, None, (), plain(dict(change.payload))))
                continue
            std = standardise_record(model, spec, change)
            if model.entity not in snapshots:
                snapshots[model.entity] = self.codelists.snapshot(code_lists_of(model))
            codes, _ = snapshots[model.entity]
            results = tuple(check(model.validation, model, std, codes, today))
            accepted.append(
                _Accepted(
                    change,
                    model,
                    spec,
                    std,
                    results,
                    plain(dict(change.payload)),
                    plain(dict(std.values)),
                    plain(dict(std.match)),
                )
            )
        if rejects:
            self.store.put_rejects(rejects)
            report.rejected = len(rejects)
            for reject in rejects:
                log.info(
                    safe_message(
                        "landing_row_rejected",
                        event=token(reject.event_id),
                        reason=reject.reason,
                        attributes=list(reject.attributes),
                    )
                )

        # personal values into the vault, in their own short transaction
        vault_rows: list[tuple[str, str, str, Any]] = []
        slots: list[tuple[int, str, str]] = []  # (accepted index, "raw"|"std", attribute)
        for index, item in enumerate(accepted):
            subject = source_subject(item.change.source)
            personal = item.model.personal_attributes()
            payload = item.payload
            values = item.values or {}
            for name in personal:
                raw = payload.get(name)
                if raw is not None:
                    vault_rows.append((item.model.entity, subject, name, raw))
                    slots.append((index, "raw", name))
                if values.get(name) is not None:
                    vault_rows.append((item.model.entity, subject, name, values[name]))
                    slots.append((index, "std", name))
        with self.store.transaction():
            ids = self.vault.put(vault_rows)
        raw_ids: dict[int, dict[str, str]] = {}
        std_ids: dict[int, dict[str, str]] = {}
        for (index, kind, name), value_id in zip(slots, ids, strict=True):
            (raw_ids if kind == "raw" else std_ids).setdefault(index, {})[name] = value_id
        versions: list[tuple[SourceChange, Mapping[str, Any]]] = []
        for index, item in enumerate(accepted):
            payload = dict(item.payload)
            for name, value_id in raw_ids.get(index, {}).items():
                payload[name] = vault_ref(value_id)
            versions.append((item.change, payload))
        report.versions = self.store.put_source_versions(versions)
        self._fault("after_versions")

        # one transaction: states move only forward; moved records are queued; the reader's position
        with self.store.transaction():
            by_entity: dict[str, list[int]] = {}
            for index, item in enumerate(accepted):
                by_entity.setdefault(item.model.entity, []).append(index)
            for entity, indexes in by_entity.items():
                self._move_states(
                    entity, [(i, accepted[i]) for i in indexes], std_ids, snapshots, now, report
                )
            if batch is not None:
                self.reader.record(batch)
        self._fault("after_intake")
        return report, {r.event_id for r in rejects}

    def _move_states(
        self,
        entity: str,
        items: Sequence[tuple[int, _Accepted]],
        std_ids: Mapping[int, Mapping[str, str]],
        snapshots: Mapping[str, tuple[Mapping[str, frozenset[str]], Mapping[str, int]]],
        now: datetime,
        report: ArrivalReport,
    ) -> None:
        sources = sorted({item.change.source for _, item in items})
        stored = self.store.source_states(entity, sources)
        best: dict[SourceKey, tuple[int, _Accepted]] = {}
        seen: Counter[SourceKey] = Counter()

        def key_of(item: _Accepted) -> tuple[int, datetime, int]:
            version = item.change.source_version if item.spec.versioned and item.change.source_version else 0
            return (version or 0, item.change.occurred_at, item.change.landing_seq)

        for index, item in items:
            source = item.change.source
            seen[source] += 1
            held = best.get(source)
            if held is None or key_of(item) > key_of(held[1]):
                best[source] = (index, item)
        states: list[SourceState] = []
        keys: dict[SourceKey, Mapping[str, Sequence[str]]] = {}
        failures: list[tuple[SourceKey, Sequence[RuleResult], str]] = []
        queue: list[tuple[str, SourceKey, str, int]] = []
        model = items[0][1].model
        _, list_versions = snapshots.get(entity, ({}, {}))
        for source in sources:
            index, item = best[source]
            current = stored.get(source)
            if current is not None and (
                current.event_id == item.change.event_id
                or current.version_key(item.spec.versioned) >= key_of(item)
            ):
                report.stale += seen[source]
                continue
            report.stale += seen[source] - 1
            state = self._state(item, current, std_ids.get(index, {}), now)
            states.append(state)
            keys[source] = dict(item.std.keys) if item.std is not None else {}
            if item.std is not None:
                failures.append((source, item.results, item.change.event_id))
            queue.append((entity, source, item.change.event_id, item.change.landing_seq))
        if not states:
            return
        self.store.put_source_states(states)
        self.store.replace_blocking_keys(entity, keys)
        if failures:
            self.store.replace_rule_failures(entity, failures, self._rule_list_versions(model, list_versions))
        self.store.queue_put(queue)
        report.queued += len(queue)

    @staticmethod
    def _rule_list_versions(model: EntityModel, versions: Mapping[str, int]) -> dict[str, int]:
        """Rule ID -> the version of the code list it read."""
        out: dict[str, int] = {}
        for rule in model.validation.rules:
            if rule.kind != "code_list":
                continue
            name = rule.params.get("code_list")
            if not isinstance(name, str):
                try:
                    name = model.attribute(rule.attribute).code_list
                except NotFound:
                    name = None
            if isinstance(name, str) and name in versions:
                out[rule.rule_id] = versions[name]
        return out

    @staticmethod
    def _state(
        item: _Accepted, current: SourceState | None, value_ids: Mapping[str, str], now: datetime
    ) -> SourceState:
        change = item.change
        source = change.source
        held = current.held if current is not None else False
        approved = current.approved_values if current is not None else None
        approved_event = current.approved_event_id if current is not None else None
        if item.std is None:  # a deletion marker keeps what was known for history
            return SourceState(
                entity=item.model.entity,
                source=source,
                status="deleted",
                values=current.values if current else {},
                match=current.match if current else {},
                ids=current.ids if current else (),
                references=current.references if current else {},
                value_ids=current.value_ids if current else {},
                sample_hash=sample_hash(item.model.entity, source),
                source_version=change.source_version,
                occurred_at=change.occurred_at,
                landing_seq=change.landing_seq,
                event_id=change.event_id,
                initial_load=change.initial_load,
                held=held,
                approved_values=approved,
                approved_event_id=approved_event,
                rules_checked=current.rules_checked if current else 0,
                rules_failed=current.rules_failed if current else 0,
                updated_at=now,
            )
        std = item.std
        references = dict(std.references)
        hint = change.payload.get("master_id")
        if isinstance(hint, str) and hint.strip():
            references[HINT_KEY] = hint.strip()
        return SourceState(
            entity=item.model.entity,
            source=source,
            status="active",
            values=item.values if item.values is not None else plain(std.values),
            match=item.match if item.match is not None else plain(std.match),
            ids=std.ids,
            references=references,
            value_ids=dict(value_ids),
            sample_hash=std.sample_hash,
            source_version=change.source_version,
            occurred_at=change.occurred_at,
            landing_seq=change.landing_seq,
            event_id=change.event_id,
            initial_load=change.initial_load,
            held=held,
            approved_values=approved,
            approved_event_id=approved_event,
            rules_checked=len(item.results),
            rules_failed=sum(1 for r in item.results if not r.passed),
            updated_at=now,
        )

    # ------------------------------------------------------------------ settle

    def _entity_order(self, entities: Sequence[str]) -> list[str]:
        """Referenced entities before the entities that reference them, then by name."""
        depends: dict[str, set[str]] = {}
        for entity in entities:
            try:
                model = self.registry.published(entity)
            except NotFound:
                depends[entity] = set()
                continue
            depends[entity] = {
                a.reference.entity
                for a in model.reference_attributes()
                if a.reference and a.reference.entity != entity
            } & set(entities)
        ordered: list[str] = []
        remaining = sorted(entities)
        while remaining:
            ready = [e for e in remaining if depends[e] <= set(ordered)] or remaining[:1]
            ordered.append(ready[0])
            remaining.remove(ready[0])
        return ordered

    def settle(self, *, bulk: bool = False, max_records: int | None = None) -> ArrivalReport:
        """Drains the queue, paged, in landing order."""
        report = ArrivalReport()
        page_size = capacity.BULK_BATCH if bulk else capacity.ARRIVAL_BATCH
        for entity in self._entity_order(self.store.queue_entities()):
            after: tuple[int, str, str] | None = None
            while True:
                limit = page_size
                if max_records is not None:
                    limit = min(limit, max_records - report.settled)
                    if limit <= 0:
                        return report
                page = self.store.queue_page(entity, after, limit)
                if not page:
                    break
                report.add(self._settle_page(entity, page, bulk))
                last_source, _, last_seq = page[-1]
                after = (last_seq, last_source.system, last_source.key)
                if len(page) < limit:
                    break
        return report

    def _settle_page(
        self, entity: str, page: Sequence[tuple[SourceKey, str, int]], bulk: bool
    ) -> ArrivalReport:
        conflicted: set[SourceKey] = set()
        failed: set[SourceKey] = set()
        for attempt in range(3):
            try:
                planned = self._plan_page(entity, page, conflicted, failed)
            except MdmError:
                raise
            except Exception:
                # one record the planner cannot handle must not stall arrival: find it, give it to a
                # steward as an exception task, and plan the rest of the page without it
                if failed:
                    raise
                failed = self._records_that_fail(entity, page)
                if not failed:
                    raise
                planned = self._plan_page(entity, page, conflicted, failed)
            model = self.registry.published(entity)
            cs = automated_change_set(
                entity,
                self.authority.automated_authority(model, [c for c in map(_clause, planned.items) if c]),
                planned.items,
                planning_version=planned.planning_version,
                initial_load=planned.initial_load,
                reason="arrival",
                evidence=safe_detail(records=len(page)),
            )
            work = WorkWrites(
                entity,
                settle=tuple(planned.settle),
                approve=tuple(planned.approve),
                hold=tuple(dict.fromkeys(planned.hold)),
                release=tuple(dict.fromkeys(planned.release)),
                tasks=tuple(planned.tasks),
                pairs=tuple(planned.pairs[k] for k in sorted(planned.pairs)),
            )
            if planned.pending:
                self.store.put_pending_references(planned.pending)
            try:
                results = self.commit.apply_chunked(
                    cs,
                    work,
                    max_rows=capacity.BULK_COMMIT_CHUNK_ROWS if bulk else capacity.COMMIT_CHUNK_ROWS,
                    throttle=self._throttle,
                    between_chunks=lambda _n: self._fault("after_chunk"),
                    links=planned.links,
                    fault=self._fault,
                )
            except Conflict as exc:
                log.info(safe_message("arrival_conflict", entity=entity, attempt=attempt, keys=len(exc.keys)))
                if attempt == 0:
                    continue
                if attempt == 1:
                    keys = set(exc.keys)
                    hit = {s for s, touched in planned.touches.items() if touched & keys}
                    if not hit:
                        break
                    conflicted |= hit
                    continue
                break
            report = planned.report
            report.settled += len({s for s, _ in planned.settle})
            report.committed([r.commit_version for r in results])
            if planned.unpend:
                self.store.drop_pending_references(planned.unpend)
            if planned.linked_now:
                report.add(
                    self._resolve_pending(self.store.pending_references_to(entity, planned.linked_now))
                )
            return report
        log.warning(safe_message("arrival_page_left_queued", entity=entity, records=len(page)))
        return ArrivalReport()

    def _records_that_fail(self, entity: str, page: Sequence[tuple[SourceKey, str, int]]) -> set[SourceKey]:
        """The records of a page whose plan raises when each is planned alone (planning writes nothing)."""
        failing: set[SourceKey] = set()
        for row in page:
            try:
                self._plan_page(entity, [row], set(), set())
            except MdmError:
                raise
            except Exception as exc:
                failing.add(row[0])
                log.warning(
                    safe_message(
                        "arrival_record_failed",
                        entity=entity,
                        source=source_token(row[0]),
                        error=type(exc).__name__,
                    )
                )
        return failing

    # ------------------------------------------------------------------ one page's plan

    def _plan_page(
        self,
        entity: str,
        page: Sequence[tuple[SourceKey, str, int]],
        conflicted: set[SourceKey],
        failed: Collection[SourceKey] = (),
    ) -> _PagePlan:
        store = self.store
        model = self.registry.published(entity)
        rules = self.registry.compiled(entity)
        now = self.clock()
        out = _PagePlan()
        out.planning_version = store.last_commit_version()
        order = [source for source, _, _ in page]
        states_read = store.source_states(entity, order)
        states = {s: states_read[s] for s in order if s in states_read}
        # a queue row whose state is gone has nothing to plan: settle it so it never blocks the queue
        out.settle.extend((s, event) for s, event, _ in page if s not in states)
        out.initial_load = bool(states) and all(st.initial_load for st in states.values())
        linked = store.xrefs_for_sources(entity, list(states))
        failures = store.rule_failures(entity, [s for s, st in states.items() if st.status == "active"])
        spec_of = {s: model.source(s.system) for s in states if model.has_source(s.system)}
        plans: dict[SourceKey, list[Plan]] = {}

        # records the conflict path gave up on, and records a quality rule holds
        for source, state in states.items():
            if source not in spec_of:
                plans[source] = [Plan("task", source, None, "exception", "unknown_source", "", {})]
            elif source in failed:
                plans[source] = [
                    Plan("task", source, linked.get(source), "exception", "planning_failed", "", {})
                ]
            elif source in conflicted:
                plans[source] = [
                    Plan(
                        "task", source, linked.get(source), "exception", "conflict", "", safe_detail(retry=2)
                    )
                ]
            elif state.status == "active" and any(
                f.get("severity") == "hold" for f in failures.get(source, [])
            ):
                codes = sorted(
                    {str(f.get("rule_id")) for f in failures.get(source, []) if f.get("severity") == "hold"}
                )
                plans[source] = [
                    Plan(
                        "task",
                        source,
                        linked.get(source),
                        "exception",
                        "quality_hold",
                        "",
                        safe_detail(rules=[token(c) for c in codes]),
                        hold=True,
                    )
                ]
        active = [s for s, st in states.items() if st.status == "active" and s not in plans]
        deleted = [s for s, st in states.items() if st.status == "deleted" and s not in plans]
        deleted_set = set(deleted)

        # candidates for every active record
        search = self.matching.search(model, rules, [states[s] for s in active])
        out.report.candidates_capped += search.capped
        known_states = {**search.states, **states}
        strong = {s: states[s].strong_ids(model) for s in active}
        golden = self.matching.golden_candidates(entity, search.pairs, strong, states=known_states)
        for source in active:
            for pair in search.pairs.get(source, []):
                key = (pair.left, pair.right) if pair.left < pair.right else (pair.right, pair.left)
                out.pairs.setdefault(key, pair)
                if pair.right in states and pair.explanation.band in (Band.AUTO, Band.REVIEW):
                    out.links.append(("s:" + pair.left.text(), "s:" + pair.right.text()))

        # deletions
        detaching: Counter[str] = Counter(linked[s] for s in deleted if s in linked)
        member_counts = store.member_counts(entity, sorted(detaching)) if detaching else {}
        for source in deleted:
            master = linked.get(source)
            remaining = member_counts.get(master, 0) - detaching[master] if master else 0
            plans[source] = decide_delete(model, spec_of[source], master, remaining, record=source)

        # updates from linked records
        updating = [s for s in active if s in linked]
        own = sorted({linked[s] for s in updating})
        members = store.members(entity, own, capacity.MAX_MEMBERS_CHECKED) if own else {}
        unread = sorted({m for ms in members.values() for m in ms if m not in known_states})
        if unread:
            known_states.update(store.source_states(entity, unread))
        for source in updating:
            state = states[source]
            master = linked[source]
            others = [
                known_states[m]
                for m in members.get(master, [])
                if m != source
                and m in known_states
                and known_states[m].status == "active"
                and m not in deleted_set
            ]
            rescore = self._best_score(rules, state, others)
            member_conflict = None
            for other in others:
                member_conflict = conflict(model, strong[source], other.strong_ids(model))
                if member_conflict:
                    break
            other_auto = sorted(
                g.master_id
                for g in golden.get(source, [])
                if g.master_id != master and g.best.explanation.band == Band.AUTO and not g.blocked_by
            )
            before = replace(state, values=state.approved_values or {})
            plans[source] = [
                decide_update(
                    model,
                    spec_of[source],
                    before,
                    state,
                    master,
                    rescore,
                    other_auto,
                    member_conflict,
                    record=source,
                )
            ]

        # new records: hints first, then clustering
        new = [s for s in active if s not in linked and s not in plans]
        hinted = {s: states[s].references.get(HINT_KEY) for s in new if states[s].references.get(HINT_KEY)}
        if hinted:
            retired = store.resolve_retired(sorted(set(hinted.values())))
            actives = {
                m
                for m, row in store.golden(entity, sorted(set(hinted.values()))).items()
                if row.status == "active"
            }
            # the hinted record's members' valid strong IDs: a hint never links past a cannot-link rule
            hinted_members = (
                store.members(entity, sorted(actives), capacity.MAX_MEMBERS_CHECKED) if actives else {}
            )
            unread = sorted({m for ms in hinted_members.values() for m in ms if m not in known_states})
            if unread:
                known_states.update(store.source_states(entity, unread))
            member_ids = {
                master: frozenset(
                    i
                    for m in ms
                    if m in known_states and known_states[m].status == "active"
                    for i in known_states[m].strong_ids(model)
                )
                for master, ms in hinted_members.items()
            }
            for source, hint in hinted.items():
                resolution = Resolution("new_cluster", (source,), (), None, None, None, reason="master_id")
                plans[source] = [
                    decide_new(
                        model,
                        spec_of[source],
                        resolution,
                        hint,
                        retired,
                        record=source,
                        hint_active=hint in actives,
                        hint_blocked=conflict(model, strong[source], member_ids.get(hint, frozenset()))
                        if hint in actives
                        else None,
                    )
                ]
        clustering = [s for s in new if s not in plans]
        in_batch = set(clustering)
        among = [
            p for s in clustering for p in search.pairs.get(s, []) if p.right in in_batch and p.left < p.right
        ]
        unlinked: dict[SourceKey, list[PairScore]] = {}
        linked_others = self._linked(entity, search, linked)
        for source in clustering:
            unlinked[source] = [
                p
                for p in search.pairs.get(source, [])
                if p.right not in in_batch and p.right not in linked_others and p.right != source
            ]
        resolutions = resolve_batch(
            [ClusterInput(s, strong[s]) for s in clustering],
            {s: golden.get(s, []) for s in clustering},
            among,
            unlinked,
        )
        clusters: dict[SourceKey, Resolution] = {}
        for resolution in resolutions:
            if resolution.is_cluster_pair:
                self._cluster_pair_task(entity, resolution, states, now, out)
                continue
            first = resolution.sources[0]
            plan = decide_new(model, spec_of[first], resolution, None, {}, record=first)
            if plan.kind == "create":
                clusters[first] = resolution
                for member in resolution.sources:
                    plans[member] = [plan if member == first else replace(plan, source=member)]
            else:
                for member in resolution.sources:
                    plans[member] = [plan if member == first else replace(plan, source=member)]

        self._build(entity, model, states, linked, plans, clusters, golden, now, out)
        return out

    def _linked(self, entity: str, search: Any, linked: Mapping[SourceKey, str]) -> set[SourceKey]:
        """The candidates (outside the page) that are linked to a golden record."""
        others = sorted({p.right for ps in search.pairs.values() for p in ps if p.right not in linked})
        found = self.store.xrefs_for_sources(entity, others) if others else {}
        return set(found) | set(linked)

    @staticmethod
    def _best_score(rules: Any, state: SourceState, others: Sequence[SourceState]) -> Explanation | None:
        best: Explanation | None = None
        best_key: tuple[float, SourceKey] | None = None
        for other in others:
            left, right = (state, other) if state.source < other.source else (other, state)
            weight, levels, rule = fast_weight(rules, left.match, right.match, left.ids, right.ids)
            found = explain(rules, levels, weight, rule)
            key = (-found.score, other.source)
            if best_key is None or key < best_key:
                best, best_key = found, key
        return best

    def _cluster_pair_task(
        self,
        entity: str,
        resolution: Resolution,
        states: Mapping[SourceKey, SourceState],
        now: datetime,
        out: _PagePlan,
    ) -> None:
        kind = "possible_duplicate" if resolution.reason == "possible_duplicate" else "review"
        refs = tuple(resolution.clusters)
        opened_by = states[resolution.sources[0]].event_id
        key = task_key(kind, entity, None, refs)
        best = resolution.best
        evidence = safe_detail(
            sources=[source_token(s) for s in resolution.sources],
            score=round(best.score, 6) if best else None,
            band=best.band.value if best else None,
        )
        out.tasks.append(
            Task(
                task_id=task_id(key, opened_by),
                task_key=key,
                entity=entity,
                kind=kind,
                status="open",
                source=None,
                master_ids=refs,
                reason=resolution.reason,
                suggestion=safe_detail(action=_SUGGESTION[kind]),
                evidence=evidence,
                event_id=opened_by,
                created_at=now,
                updated_at=now,
            )
        )
        out.report.tasks[kind] = out.report.tasks.get(kind, 0) + 1
        a, b = resolution.sources[0], resolution.sources[1]
        out.links.append(("s:" + a.text(), "s:" + b.text()))

    # ------------------------------------------------------------------ items, survivorship, relationships, work

    def _build(
        self,
        entity: str,
        model: EntityModel,
        states: Mapping[SourceKey, SourceState],
        linked: Mapping[SourceKey, str],
        plans: Mapping[SourceKey, list[Plan]],
        clusters: Mapping[SourceKey, Resolution],
        golden: Mapping[SourceKey, Sequence[GoldenCandidate]],
        now: datetime,
        out: _PagePlan,
    ) -> None:
        consolidate = model.match.mode == "consolidate"
        committing: set[SourceKey] = set()  # records whose current values count now
        target: dict[SourceKey, str] = {}  # record -> the master ID or ref it belongs to after this page
        detached: dict[str, set[SourceKey]] = {}
        joining: dict[str, set[SourceKey]] = {}
        recompute: dict[str, str] = {}  # master -> the clause of the first plan that asked
        creates: list[tuple[str, SourceKey, Resolution, Plan]] = []

        for source in (s for s in states if s in plans):
            state = states[source]
            for plan in plans[source]:
                out.touches.setdefault(source, set()).add(source_token(source))
                if plan.master_id:
                    out.touches[source].add(token(plan.master_id))
                if plan.kind in COMMITTING or plan.kind == "noop":
                    out.approve.append((source, state.event_id))
                    if state.held:
                        out.release.append(source)
                        self._close_task("held", entity, source, state, now, out)
                if plan.kind == "update":
                    committing.add(source)
                    target[source] = plan.master_id or linked[source]
                    recompute.setdefault(target[source], plan.clause)
                    out.report.updated += 1
                elif plan.kind == "noop" and source in linked:
                    target[source] = linked[source]
                elif plan.kind == "detach" and plan.master_id:
                    out.items.append(DetachSource(source, plan.master_id, plan.clause))
                    detached.setdefault(plan.master_id, set()).add(source)
                    recompute.setdefault(plan.master_id, plan.clause)
                    out.report.detached += 1
                elif plan.kind in ("link", "consolidate") and plan.master_id:
                    out.items.append(LinkSource(source, plan.master_id, None, plan.clause))
                    committing.add(source)
                    target[source] = plan.master_id
                    out.linked_now.append(source)
                    out.report.linked += 1
                    out.links.append(("s:" + source.text(), "m:" + plan.master_id))
                    if consolidate or plan.kind == "consolidate":
                        joining.setdefault(plan.master_id, set()).add(source)
                        recompute.setdefault(plan.master_id, plan.clause)
                elif plan.kind == "create" and source in clusters:
                    ref = REF_PREFIX + source.text()
                    creates.append((ref, source, clusters[source], plan))
                elif plan.kind == "create":
                    committing.add(source)  # a member of a cluster created by its first record
                elif plan.kind == "task" and plan.task_kind:
                    self._task(entity, plan, state, now, out)
                    if plan.hold:
                        out.hold.append(source)
                out.settle.append((source, state.event_id))
            if plan_is_held(plans[source]) and source in linked:
                out.links.append(("s:" + source.text(), "m:" + linked[source]))
        # new golden records, in the order of their first record
        for ref, first, resolution, plan in creates:
            members = list(resolution.sources)
            values, provenance = self._survive(model, [states[m] for m in members], {}, now, set(members))
            out.items.append(CreateGolden(ref, values, provenance, tuple(members), plan.clause))
            for member in members:
                clause = plan.clause if member == first else rule1_clause("auto_band")
                out.items.append(LinkSource(member, ref, None, clause))
                committing.add(member)
                target[member] = ref
                out.linked_now.append(member)
                out.touches.setdefault(member, set()).update({source_token(member), ref})
                out.links.append(("s:" + member.text(), "r:" + ref))
            out.report.created += 1
        # survivorship for every existing golden record a committed item changes
        if recompute:
            self._recompute(entity, model, states, recompute, detached, joining, committing, now, out)
        # relationships from the records' references
        self._relationships(entity, model, states, plans, target, now, out)

    def _recompute(
        self,
        entity: str,
        model: EntityModel,
        states: Mapping[SourceKey, SourceState],
        recompute: Mapping[str, str],
        detached: Mapping[str, set[SourceKey]],
        joining: Mapping[str, set[SourceKey]],
        committing: set[SourceKey],
        now: datetime,
        out: _PagePlan,
    ) -> None:
        store = self.store
        masters = sorted(recompute)
        rows = store.golden(entity, masters)
        members = store.members(entity, masters, capacity.MAX_MEMBERS_CHECKED * 20)
        needed = sorted({m for ms in members.values() for m in ms if m not in states})
        member_states = {**store.source_states(entity, needed), **states} if needed else dict(states)
        steward = store.steward_values(entity, masters)
        for master in masters:
            row = rows.get(master)
            if row is None or row.status != "active":
                continue
            sources = (set(members.get(master, [])) - detached.get(master, set())) | joining.get(
                master, set()
            )
            if not sources:
                continue  # an orphan keeps its values; the orphan task says so
            chosen = [member_states[s] for s in sorted(sources) if s in member_states]
            values, provenance = self._survive(model, chosen, steward.get(master, {}), now, committing)
            out.items.append(UpdateGolden(master, row.row_version, values, provenance, recompute[master]))
            out.links.extend(("s:" + s.text(), "m:" + master) for s in sorted(sources & committing))

    @staticmethod
    def _survive(
        model: EntityModel,
        members: Sequence[SourceState],
        steward: Mapping[str, Any],
        now: datetime,
        current: set[SourceKey],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Values and provenance from the members' approved values; the records committing now count with
        their current values."""
        chosen: list[Member] = []
        for state in members:
            if state.status != "active":
                continue
            values = state.values if state.source in current else state.approved_values
            if values is None:
                continue
            chosen.append(Member(state.source, values, state.occurred_at, state.landing_seq))
        values, provenance = survive(
            model, model.survivorship, chosen, steward, now, model.survivorship.version
        )
        return plain(values), provenance  # built from JSON-form member values: already plain

    def _relationships(
        self,
        entity: str,
        model: EntityModel,
        states: Mapping[SourceKey, SourceState],
        plans: Mapping[SourceKey, list[Plan]],
        target: Mapping[SourceKey, str],
        now: datetime,
        out: _PagePlan,
    ) -> None:
        references = model.reference_attributes()
        if not references:
            return
        store = self.store
        ending = [
            s
            for s, ps in plans.items()
            if any(p.kind == "detach" for p in ps) or states[s].status == "deleted"
        ]
        asserting = [s for s in target if s in states and states[s].status == "active"]
        asserting_set = set(asserting)
        subjects = sorted(set(ending) | asserting_set)
        if not subjects:
            return
        existing: dict[tuple[SourceKey, str], list[Any]] = {}
        for rel in store.relationships_by_origin([(s, a.name) for s in subjects for a in references]):
            if rel.origin is not None and rel.origin_attribute:
                existing.setdefault((rel.origin, rel.origin_attribute), []).append(rel)
        # targets: in-page records of this entity first, then the cross-references of the target entity
        wanted: dict[str, set[SourceKey]] = {}
        for source in asserting:
            for attribute in references:
                key = states[source].references.get(attribute.name)
                spec = attribute.reference
                if isinstance(key, str) and spec is not None:
                    wanted.setdefault(spec.entity, set()).add(SourceKey(spec.key_source, key))
        resolved: dict[tuple[str, SourceKey], str] = {}
        for target_entity, keys in wanted.items():
            for key in keys:
                if target_entity == entity and key in target:
                    resolved[(target_entity, key)] = target[key]
            rest = sorted(k for k in keys if (target_entity, k) not in resolved)
            if rest:
                for key, master in store.xrefs_for_sources(target_entity, rest).items():
                    resolved[(target_entity, key)] = master
        for source in subjects:
            state = states[source]
            valid = state.occurred_at.date()
            clause = next((p.clause for p in plans.get(source, []) if p.clause), "")
            if not self.authority.clause_held(model, clause):
                continue  # the source's policy holds this record's changes: its relationships wait with it
            for attribute in references:
                spec = attribute.reference
                if spec is None:
                    continue
                current = [r for r in existing.get((source, attribute.name), []) if r.status == "active"]
                key = state.references.get(attribute.name) if source in asserting_set else None
                keep: str | None = None
                if isinstance(key, str):
                    ref_source = SourceKey(spec.key_source, key)
                    rel_id = relationship_id(spec.relationship, source, attribute.name, spec.key_source, key)
                    to = resolved.get((spec.entity, ref_source))
                    if to is None:
                        out.pending.append((entity, source, attribute.name, spec.entity, ref_source))
                    else:
                        keep = rel_id
                        frm = target[source]
                        same = [r for r in current if r.rel_id == rel_id]
                        if not same or same[0].from_master_id != frm or same[0].to_master_id != to:
                            out.items.append(
                                UpsertRelationship(
                                    rel_id,
                                    spec.relationship,
                                    frm,
                                    spec.entity,
                                    to,
                                    valid,
                                    {},
                                    source,
                                    attribute.name,
                                    clause,
                                )
                            )
                            out.links.append(("l:" + rel_id, "s:" + source.text()))
                            if is_ref(to):
                                out.links.append(("s:" + source.text(), "r:" + to))
                        out.unpend.append((entity, source, attribute.name))
                else:
                    out.unpend.append((entity, source, attribute.name))
                for rel in current:
                    if rel.rel_id != keep:
                        out.items.append(EndRelationship(rel.rel_id, valid, clause))
                        out.links.append(("l:" + rel.rel_id, "s:" + source.text()))

    def _task(self, entity: str, plan: Plan, state: SourceState, now: datetime, out: _PagePlan) -> None:
        kind = plan.task_kind or "exception"
        if kind == "orphan":
            master_ids: tuple[str, ...] = (plan.master_id,) if plan.master_id else ()
            source = None
        else:
            master_ids = tuple(
                plan.evidence.get("master_ids", ()) or ((plan.master_id,) if plan.master_id else ())
            )
            source = plan.source
        key = task_key(kind, entity, source, master_ids)
        out.tasks.append(
            Task(
                task_id=task_id(key, state.event_id),
                task_key=key,
                entity=entity,
                kind=kind,
                status="open",
                source=source,
                master_ids=master_ids,
                reason=plan.reason,
                suggestion=safe_detail(
                    action=_SUGGESTION.get(kind, "investigate"), clause=token(plan.clause)
                ),
                evidence=safe_detail(**{k: v for k, v in plan.evidence.items()}),
                event_id=state.event_id,
                created_at=now,
                updated_at=now,
            )
        )
        out.report.tasks[kind] = out.report.tasks.get(kind, 0) + 1

    @staticmethod
    def _close_task(
        kind: str, entity: str, source: SourceKey, state: SourceState, now: datetime, out: _PagePlan
    ) -> None:
        key = task_key(kind, entity, source)
        out.tasks.append(
            Task(
                task_id=task_id(key, state.event_id),
                task_key=key,
                entity=entity,
                kind=kind,
                status="closed",
                source=source,
                master_ids=(),
                reason="released",
                suggestion={},
                evidence={},
                event_id=state.event_id,
                created_at=now,
                updated_at=now,
            )
        )

    # ------------------------------------------------------------------ references that arrive late

    def resolve_pending_references(self, entity: str) -> int:
        """Pending references whose target now has a master ID become relationships; returns how many."""
        resolved = 0
        after: tuple | None = None
        while True:
            rows = self.store.pending_references(entity, after, capacity.READ_PAGE)
            if not rows:
                return resolved
            report = self._resolve_pending(rows)
            resolved += report.linked
            last = rows[-1]
            after = (last[1].system, last[1].key, last[2])
            if len(rows) < capacity.READ_PAGE:
                return resolved

    def _resolve_pending(self, rows: Sequence[tuple]) -> ArrivalReport:
        """Relationships for pending references whose origin is linked and whose target now is.

        Each origin entity commits one automated change set; `linked` in the report counts relationships.
        """
        report = ArrivalReport()
        by_entity: dict[str, list[tuple]] = {}
        for row in rows:
            by_entity.setdefault(row[0], []).append(row)
        for origin_entity, group in sorted(by_entity.items()):
            try:
                model = self.registry.published(origin_entity)
            except NotFound:
                continue
            origins = sorted({r[1] for r in group})
            from_master = self.store.xrefs_for_sources(origin_entity, origins)
            states = self.store.source_states(origin_entity, origins)
            targets: dict[tuple[str, SourceKey], str] = {}
            for ref_entity in sorted({r[3] for r in group}):
                keys = sorted({r[4] for r in group if r[3] == ref_entity})
                for key, master in self.store.xrefs_for_sources(ref_entity, keys).items():
                    targets[(ref_entity, key)] = master
            existing: dict[tuple[SourceKey, str], list[Any]] = {}
            for rel in self.store.relationships_by_origin([(r[1], r[2]) for r in group]):
                if rel.origin is not None and rel.origin_attribute:
                    existing.setdefault((rel.origin, rel.origin_attribute), []).append(rel)
            items: list[ChangeItem] = []
            drop: list[tuple] = []
            links: list[tuple[str, str]] = []
            for _, origin, attribute, ref_entity, ref_source in group:
                state = states.get(origin)
                try:
                    spec = model.attribute(attribute).reference
                except NotFound:
                    spec = None
                if state is None or spec is None or state.references.get(attribute) != ref_source.key:
                    drop.append(
                        (origin_entity, origin, attribute)
                    )  # the reference moved on: not pending any more
                    continue
                to = targets.get((ref_entity, ref_source))
                frm = from_master.get(origin)
                source_spec = model.source(origin.system) if model.has_source(origin.system) else None
                if (
                    to is None
                    or frm is None
                    or state.held
                    or source_spec is None
                    or source_spec.policy.update != "auto"
                ):
                    continue
                clause = source_spec.policy.clause(origin.system, "update")
                rel_id = relationship_id(
                    spec.relationship, origin, attribute, ref_source.system, ref_source.key
                )
                valid = state.occurred_at.date()
                items.append(
                    UpsertRelationship(
                        rel_id, spec.relationship, frm, ref_entity, to, valid, {}, origin, attribute, clause
                    )
                )
                links.append(("l:" + rel_id, "s:" + origin.text()))
                for rel in existing.get((origin, attribute), []):
                    if rel.status == "active" and rel.rel_id != rel_id:
                        items.append(EndRelationship(rel.rel_id, valid, clause))
                        links.append(("l:" + rel.rel_id, "s:" + origin.text()))
                drop.append((origin_entity, origin, attribute))
                report.linked += 1
            if items:
                cs = automated_change_set(
                    origin_entity,
                    self.authority.automated_authority(model, [c for c in map(_clause, items) if c]),
                    items,
                    planning_version=self.store.last_commit_version(),
                    reason="arrival",
                    evidence=safe_detail(pending_references=len(items)),
                )
                results = self.commit.apply_chunked(
                    cs, WorkWrites(origin_entity), links=links, fault=self._fault
                )
                report.committed([r.commit_version for r in results])
            if drop:
                self.store.drop_pending_references(drop)
        return report


def automated_change_set(
    entity: str,
    authority: Authority,
    items: Sequence[ChangeItem],
    *,
    planning_version: int,
    initial_load: bool = False,
    reason: str = "arrival",
    evidence: Mapping[str, Any] | None = None,
) -> ChangeSet:
    """`new_change_set(entity, "arrival", AUTOMATED_MATCHER, …)`, with the fingerprint computed without deep
    copies (a bulk page holds tens of thousands of items)."""
    return ChangeSet(
        change_set_id="CS-" + secrets.token_hex(10),
        fingerprint=fingerprint(entity, "arrival", AUTOMATED_MATCHER, authority, items, planning_version),
        planning_version=planning_version,
        entity=entity,
        action="arrival",
        actor=AUTOMATED_MATCHER,
        authority=authority,
        items=tuple(items),
        initial_load=initial_load,
        reason=reason,
        evidence=dict(evidence or {}),
    )


def plan_is_held(plans: Sequence[Plan]) -> bool:
    return any(p.hold for p in plans)


def _clause(item: ChangeItem) -> str:
    return getattr(item, "clause", "")
