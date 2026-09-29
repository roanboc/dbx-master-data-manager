"""Signature batches: alike reviews linked together once a forced sample of them agrees (story 3.3).

Application service `ASVC8`, with the undo tray `ASVC9` (decision 23). A batch only links records that belong
to no golden record, or only reverses its own links; every chunk goes through `CommitService.apply`, so no
method here writes `mdm_core`. The readings of the plan, as built:

1. A signature group is the open `review` tasks of one entity that share a signature and a match rule version,
   keyed `SIG-` + 16 hex of sha256(`entity|rule_version|signature`). Arrival stores the signature only on a
   review a steward could link to a golden record the pattern names (`review_band`, or `breaker_demoted` with
   a golden candidate); every other task stores `''`. The Alike reviews page reads a window, the open reviews
   due soonest (`GROUP_WINDOW`), groups them in one capped aggregate and lists the largest groups
   (`GROUPS_SHOWN`, at least `GROUP_MIN`). Only the current match rule version is grouped. The stored
   signature is what arrival saw, so every batch step checks each review again, live. `backfill_signatures`
   gives one to the reviews written before this story.
2. The page's figures each open their rows: the capped count, the latest label per pair with the signature,
   the lifetime blind-review agreement of its samples by origin, bulk rights, and the open batch's stage.
3. A group has at most one open batch (`open_batch`). The population is fixed at the draw: the group's
   eligible reviews, the `BATCH_MAX` due soonest. A review is eligible when its task is open, its record is
   active at the task's event and linked to nothing, it is not staged, claimed by another, snoozed or
   escalated, no earlier batch split it off at its current event, and, checked live with the decide pane's own
   helpers, its best open candidate is not blocked, declined or a close call, with the group's signature and
   rule version. A group whose population does not exceed its forced sample is `group_too_small`.
4. The forced sample is 5 + ⌊n/150⌋ of the population, shared out over strata (the source-system pair of the
   record and its best candidate member) and drawn by the smallest keyed draw values, so both engines draw the
   same reviews and drawing again picks the same ones still open. It is decided one by one in the inbox; its
   outcome (agreed, disagreed, void) is written in the decision's own transaction. A void review is replaced.
5. The split (the product owner's answer of 2026-09-28): the steward who decides a disagreeing sample review
   names the comparison that misled, with the decision. Right after it commits, the flush splits off the
   reviews whose record holds the same match form on that comparison as the disagreeing record's (a missing
   form matches only a missing one), compared here only; task IDs and the comparison's name are stored, a
   count is shown, and one access row per split record is written on a personal comparison. "Every alike
   review" ends the batch (`split_all`). The sample is topped up with the next draws, the strata that lack a
   member first; when too few are left to link together the batch ends (`too_few_left`).
6. A batch becomes `ready` when every split has applied, every remaining sample review agreed, the sample
   holds 5 + ⌊n′/150⌋ of the reviews still in the batch, and the signature's bulk rights are held.
7. A bulk decision is a link only, to the golden record each case suggests.
8. Preparing shows every row's change: each candidate checked again, ordered by target, the joiners of one
   target walked in order against a cannot-link rule, each target's golden values computed once with all its
   joiners, and the plan frozen (targets, row versions, events, the attributes each target changes).
9. Chunks hold at most `COMMIT_CHUNK_ROWS` published rows and decisions, packed greedily; each is its own
   change set `CS-<the batch's hex>-<n>`, action `batch_link`, one chunk of a batch per flush pass, after the
   single decisions due. The throttle sets the next chunk's `not_before`; the flush never sleeps.
10. Decision 19 read per review and per chunk (decision 23): one tray entry (`batch_link`, its task ID the batch
    ID) locks every planned review's task and record. Before each chunk every review is checked again with keyed
    reads, and one that moved, or is now blocked, fails alone and returns to the queue. Each chunk commits
    exactly once, with its reviews' settlement, labels, blind-review samples and the release of its locks. The
    first chunk settles the entry `committed` with outcome `committing`, so Undo ends there; the last settles
    the batch `committed`. A conflict inside a chunk is planned again once; `FLUSH_ATTEMPTS` failed passes in
    a row stop the batch (`chunk_failed`).
11. Above `batch_checker_above` decisions a second steward, any steward but the maker, confirms the batch before
    it enters the tray; every chunk records the checker, and `AuthorityService.check` refuses a chunk without it.
12. Any steward may stop a committing batch: before the next chunk, whatever the throttle. Committed chunks
    stand; the rest are released.
13. ⌈2% × the batch's links⌉ (at least one) go to blind review, drawn at staging, written in their chunk, their
    occasion `<event>/<batch ID>`; a drawn review that fails its pre-check passes its draw to the planned review
    with the next smallest draw value, in the same transaction. Neither the maker nor the second steward
    answers them.
14. A signature's bulk rights live in `breaker_state` under a `bulk:` band. Only blind review of its batches
    withdraws them; while withdrawn, drawing and staging are refused and a committing batch stops at its next
    chunk. Only a data owner restores them, on the command line.
15. A compensation is itself a batch, of kind `compensate`, over the original's reviews still committed in chunks
    nobody undid, within `batch_undo_days`: prepared with every row's reversal, staged in the tray, confirmed
    above the threshold, committed chunk by chunk, one change set per original chunk.
16. The throttle applies to batches; an automatic-band demotion leaves them alone.

**One lock order.** Every transaction that touches a batch takes the batch row first, with a conditional update
(`hold_batch`, `set_batch`, `undo_batch`, `fail_items`, `finish_batch`, `end_batch`, `apply_split`,
`request_stop`, and a chunk's `_hold_batch` in `apply_work`); a stage sets the batch `staged` before
`stage_tray`. Only then does it touch `batch_item`, `tray_lock` or `tray_entry`, so on Postgres they wait for
one another and never deadlock.
"""

from __future__ import annotations

import logging
import secrets
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from mdm import capacity
from mdm.backend.store import SqlStore
from mdm.config import Settings
from mdm.engine.batch import (
    Member,
    PackItem,
    forced_sample_size,
    pack,
    pick,
    pick_reviews,
    review_count,
    split_members,
)
from mdm.engine.compare import exact_form
from mdm.engine.sample import draw_value
from mdm.models.authority import ROLE_LABELS, AccessRow, Actor, allowed
from mdm.models.batch import (
    BATCH_ID_RE,
    BEFORE_TRAY,
    COMMITTING,
    COMPENSATE_REASONS,
    ITEM_ROLES,
    OPEN_BATCH_STATUSES,
    SAMPLE_OUTCOMES,
    SIGNATURE_KEY_RE,
    SPLIT_ALL,
    SPLIT_REASON_PREFIX,
    Batch,
    BatchChunkWrite,
    BatchItem,
    chunk_change_set_id,
    new_batch_id,
)
from mdm.models.canonical import utcnow
from mdm.models.changes import WorkWrites, new_change_set
from mdm.models.entity_model import EntityModel
from mdm.models.errors import Conflict, Forbidden, MdmError, NotFound, PlatformRefused
from mdm.models.quality import bulk_band
from mdm.models.records import SourceKey, SourceState
from mdm.models.safety import safe_detail, safe_message
from mdm.models.tasks import Task
from mdm.models.workbench import (
    Action,
    AgreementView,
    BatchLine,
    BatchProgress,
    BatchRow,
    BatchRowPage,
    BatchSummary,
    BatchView,
    CompensationLine,
    GroupList,
    GroupRow,
    Impact,
    LabelHistory,
    MatchLabel,
    PreviewRow,
    SampleReview,
    SplitLine,
    TaskQuery,
    TrayEntry,
    TraySettlement,
)
from mdm.services import display
from mdm.services.arrival import ArrivalService
from mdm.services.authority import require, role_authority
from mdm.services.breaker import BreakerService
from mdm.services.commit import CommitService
from mdm.services.decisions import default_of, offered_candidates
from mdm.services.inbox import TaskRows, task_lock
from mdm.services.lifecycle import LifecycleService
from mdm.services.matching import MatchService, strong_ids_of
from mdm.services.privacy import PrivacyService
from mdm.services.quality import QualityService
from mdm.services.registry import ModelRegistry
from mdm.services.support import source_token, token

log = logging.getLogger("mdm.batches")

#: the review reasons a signature group holds: a steward could link the record to a golden record the pattern
#: names (a breaker wait only with a golden candidate)
GROUPED_REASONS = ("review_band", "breaker_demoted")
#: conflicts inside a chunk that are planned again once in the pass
_RETRY = frozenset({"stale_row", "stale_link", "record_changed", "task_closed", "target_changed"})
#: every status a batch's review may hold
_ALL_ITEM_STATUSES = (
    "open",
    "agreed",
    "disagreed",
    "void",
    "candidate",
    "planned",
    "excluded",
    "committed",
    "failed",
    "released",
    "compensated",
    "kept",
    "split",
)
#: the reviews a batch's rows list: planned, and what became of them
_ROW_STATUSES = ("planned", "committed", "failed", "released", "compensated", "kept")
#: a finished batch
_FINISHED = ("committed", "stopped", "discarded", "failed")
#: a batch's stage in words, for the Alike reviews page (its sampling and committing words carry figures)
_STAGE_WORDS = {
    "awaiting_checker": "waits for a second steward",
    "staged": "in the tray",
}
#: the words of a sample review's outcome
_SAMPLE_WORDS = {
    "open": "Waiting",
    "agreed": "Linked as suggested",
    "void": "Void: another review replaces it",
}
#: a sample review another review's split took out of the batch
_SPLIT_OFF_WORDS = "Left the batch with a split: decided one by one"
NOTICE_ROLE = "Your role, {role}, cannot decide alike reviews together."
#: a role that sees tasks without deciding them, as the decide pane says it
NOTICE_READS = "Your role, {role}, can see tasks but not decide them."
WHY_DECIDED = "Every sample review is decided."
WHY_PREPARE = "Only the steward who drew this batch prepares it."
WHY_STAGE = "Only the steward who drew this batch stages it."
WHY_CONFIRM_MAKER = "You prepared this batch, so another steward confirms it."
WHY_UNDO = "Only the steward who prepared it, or confirmed it, can undo it."
WHY_STOPPING = "Stop is already asked."


@dataclass(frozen=True, slots=True)
class ChunkPass:
    """What one flush step did to a batch: the chunks it committed, the batches it finished, and, for a batch
    entry's first step, the entry's outcome (`committed` when its first chunk committed, else the code it
    settled `failed` with); None when nothing happened."""

    chunks: int = 0
    finished: int = 0
    outcome: str | None = None


@dataclass(frozen=True, slots=True)
class _Checked:
    """A review checked live: planned (no reason) with its target, the target's row version, its score, band
    and stratum; or left out with a reason code."""

    task: Task
    state: SourceState | None
    reason: str | None
    target: str | None = None
    target_version: int | None = None
    score: float | None = None
    band: str | None = None
    stratum: str = ""


def _entity_label(entity: str) -> str:
    return entity.replace("_", " ").capitalize()


def _lock_subjects(entity: str, item: BatchItem) -> list[str]:
    """The tray locks a batch's review holds: its task and its record."""
    return [task_lock(item.task_id), f"source:{entity}:{item.source.system}:{item.source.key}"]


def _whole(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


class BatchService:
    """Draws, samples, splits, prepares, stages, commits, stops and compensates signature batches."""

    def __init__(
        self,
        settings: Settings,
        store: SqlStore,
        registry: ModelRegistry,
        matching: MatchService,
        lifecycle: LifecycleService,
        commit: CommitService,
        quality: QualityService,
        breaker: BreakerService,
        privacy: PrivacyService,
        arrival: ArrivalService,
        clock: Callable[[], datetime] = utcnow,
        *,
        fault: Callable[[str], None] | None = None,
    ) -> None:
        """Wires the service; reads nothing from the store (`mdm init` wires the hub before the schema).
        `fault(point)` is a test hook, called at `after_chunk` (a chunk committed), `before_finish` (a batch
        about to end) and `in_undo` (inside an undo's transaction, the batch row held)."""
        self.settings = settings
        self.store = store
        self.registry = registry
        self.matching = matching
        self.lifecycle = lifecycle
        self.commit = commit
        self.quality = quality
        self.breaker = breaker
        self.privacy = privacy
        self.arrival = arrival
        self.clock = clock
        self.fault = fault
        self.rows_helper = TaskRows(settings, store, registry)

    # ------------------------------------------------------------------ helpers

    def _fault(self, point: str) -> None:
        if self.fault is not None:
            self.fault(point)

    def _allow(self, actor: Actor, action: str) -> None:
        """The role allows the action, and a persona acts on a local store only."""
        require(actor, action)
        if actor.persona and not self.settings.local_mode:
            raise PlatformRefused("persona_refused", role=token(actor.role))

    def _get(self, batch_id: str) -> Batch:
        if not isinstance(batch_id, str) or not BATCH_ID_RE.match(batch_id):
            raise NotFound("unknown_batch", batch_id=token(str(batch_id)))
        found = self.store.batches([batch_id]).get(batch_id)
        if found is None:
            raise NotFound("unknown_batch", batch_id=batch_id)
        return found

    def _items(
        self, batch_id: str, roles: Sequence[str] = ITEM_ROLES, statuses: Sequence[str] | None = None
    ) -> list[BatchItem]:
        """A batch's reviews of `roles` (and `statuses`), by position: a batch holds at most `BATCH_MAX`."""
        out: list[BatchItem] = []
        after: int | None = None
        while True:
            page = self.store.batch_items(batch_id, roles, statuses, after, capacity.BATCH_MAX)
            out.extend(page)
            if len(page) < capacity.BATCH_MAX:
                return out
            after = page[-1].position

    def _model(self, entity: str) -> EntityModel:
        return self.registry.published(entity)

    def _withdrawn(self, batch_or_entity: Batch | str, signature: str | None = None) -> bool:
        if isinstance(batch_or_entity, Batch):
            if batch_or_entity.kind != "link":
                return False
            entity, signature = batch_or_entity.entity, batch_or_entity.signature
        else:
            entity = batch_or_entity
        found = self.breaker.bulk(entity, signature or "")
        return found is not None and found.demoted

    @staticmethod
    def _prepared(batch: Batch) -> bool:
        return batch.kind == "compensate" or bool(batch.figures.get("prepared"))

    def _end(self, batch: Batch, outcome: str, from_statuses: Sequence[str] = BEFORE_TRAY) -> bool:
        """Ends a batch before the tray (a discard, a split of every alike review, too few left) in one
        transaction, the batch row first: `discarded` with the outcome, its group's `open_batch` row deleted and,
        for a compensation, its original's mark cleared. Its reviews stay in the inbox."""
        return self.store.end_batch(
            batch.batch_id, outcome=outcome, at=self.clock(), from_statuses=from_statuses
        )

    # ------------------------------------------------------------------ reading: the Alike reviews page

    def groups(self, *, actor: Actor, entity: str | None = None) -> GroupList:
        """The largest signature groups of the open reviews due soonest, each with its capped count, marks,
        label history, agreement, bulk rights and open batch; the reviews scored under an earlier rule version
        (capped); and the batches the actor may confirm as second steward. `view_tasks`."""
        self._allow(actor, "view_tasks")
        entities = [entity] if entity else self.registry.published_entities()
        found: list[tuple[int, str, str, str, int]] = []  # (window count, key, entity, signature, version)
        older = 0
        for name in entities:
            model = self._model(name)
            version = model.match.version
            for key, signature, n in self.store.signature_groups(name, version, capacity.GROUP_WINDOW):
                if n >= capacity.GROUP_MIN:
                    found.append((n, key, name, signature, version))
            older += self.store.older_rules_count(name, version, capacity.COUNT_CAP)
        found.sort(key=lambda g: (-g[0], g[1]))
        shown = found[: capacity.GROUPS_SHOWN]
        batches = self.store.open_batches_of_groups([key for _, key, _, _, _ in shown]) if shown else {}
        rows = tuple(
            self._group_row(key, name, signature, version, batches.get(key))
            for _, key, name, signature, version in shown
        )
        return GroupList(
            groups=rows,
            window=capacity.GROUP_WINDOW,
            older_rules=min(older, capacity.COUNT_CAP),
            to_confirm=self._to_confirm(actor, entity),
        )

    def group(self, group_key: str, *, actor: Actor, entity: str) -> GroupRow | None:
        """One signature group, found by its key, as the Alike reviews page shows it; None when no open review
        of the current rule version carries it. `view_tasks`."""
        self._allow(actor, "view_tasks")
        if not isinstance(group_key, str) or not SIGNATURE_KEY_RE.match(group_key):
            raise Forbidden("bad_filter")
        model = self._model(entity)
        first = self.store.group_members(group_key, None, 1)
        if not first or first[0].entity != entity or first[0].rule_version != model.match.version:
            return None
        batch = self.store.open_batches_of_groups([group_key]).get(group_key)
        return self._group_row(group_key, entity, first[0].signature or "", model.match.version, batch)

    def _group_row(
        self, key: str, entity: str, signature: str, version: int, batch: Batch | None
    ) -> GroupRow:
        cap = capacity.COUNT_CAP
        now = self.clock()
        model = self._model(entity)
        count = self.store.task_count(TaskQuery(now, entity, "review", snoozed=None, signature_key=key), cap)
        labels = self.store.label_counts(entity, signature, cap)
        by_origin: dict[str, tuple[int, int]] = {}
        for row in self.store.agreement_of(entity, signature):
            agreed, reviewed = by_origin.get(row.origin, (0, 0))
            by_origin[row.origin] = (agreed + row.agreed, reviewed + row.reviewed)
        size = forced_sample_size(count, self.settings.forced_sample_base, self.settings.forced_sample_per)
        return GroupRow(
            group_key=key,
            entity=entity,
            entity_label=_entity_label(entity),
            rule_version=version,
            marks=display.signature_marks(model, signature),
            count=count,
            labels=LabelHistory(matched=labels.get("match", 0), not_matched=labels.get("not_a_match", 0)),
            agreement=AgreementView(
                agreed=sum(a for a, _ in by_origin.values()),
                reviewed=sum(r for _, r in by_origin.values()),
                by_origin=dict(sorted(by_origin.items())),
            ),
            withdrawn=self.breaker.bulk_withdrawn(entity, signature),
            too_small=count <= size,
            batch_id=batch.batch_id if batch is not None else None,
            batch_status=batch.status if batch is not None else None,
            batch_words=self._stage_words(batch) if batch is not None else None,
        )

    def _stage_words(self, batch: Batch) -> str:
        """An open batch's stage in words: "forced sample, 3 of 9 decided", "sample agreed", "every change
        shown", "waits for a second steward", "in the tray", "committing, chunk 2 of 3"."""
        if batch.status == "sampling":
            sample = self.store.batch_items(batch.batch_id, ("sample",), None, None, capacity.BATCH_MAX)
            decided = sum(1 for i in sample if i.status in ("agreed", "disagreed"))
            return f"forced sample, {decided} of {batch.sample_size} decided"
        if batch.status == "ready":
            return "every change shown" if self._prepared(batch) else "sample agreed"
        if batch.status == "committing":
            return f"committing, chunk {batch.chunks_committed + 1} of {max(batch.chunks, 1)}"
        return _STAGE_WORDS.get(batch.status, batch.status.replace("_", " "))

    def _to_confirm(self, actor: Actor, entity: str | None) -> tuple[BatchLine, ...]:
        """The batches waiting for a second steward that the actor did not make, at most `GROUPS_SHOWN`."""
        if not allowed(actor, "confirm_batch"):
            return ()
        out: list[BatchLine] = []
        after: str | None = None
        for _ in range(capacity.COUNT_CAP // capacity.GROUPS_SHOWN):
            page = self.store.batches_by_status(("awaiting_checker",), after, capacity.GROUPS_SHOWN)
            for batch in page:
                if batch.maker == actor.name or (entity is not None and batch.entity != entity):
                    continue
                out.append(
                    BatchLine(
                        batch_id=batch.batch_id,
                        entity=batch.entity,
                        entity_label=_entity_label(batch.entity),
                        kind=batch.kind,
                        decisions=batch.decisions,
                        maker_label=display.role_label(batch.maker_role),
                    )
                )
                if len(out) >= capacity.GROUPS_SHOWN:
                    return tuple(out)
            if len(page) < capacity.GROUPS_SHOWN:
                break
            after = page[-1].batch_id
        return tuple(out)

    def listing(
        self,
        *,
        actor: Actor,
        entity: str | None = None,
        statuses: Sequence[str] | None = None,
        limit: int = capacity.READ_PAGE,
    ) -> list[Batch]:
        """The batches in `statuses` (default: the open ones), of one entity when given, by batch ID, at most
        `limit`: what `mdm batch list` prints. `view_tasks`."""
        self._allow(actor, "view_tasks")
        limit = capacity.require_limit(limit, capacity.READ_PAGE)
        wanted = tuple(statuses) if statuses else OPEN_BATCH_STATUSES
        out: list[Batch] = []
        after: str | None = None
        while len(out) < limit:
            page = self.store.batches_by_status(wanted, after, limit)
            out.extend(b for b in page if entity is None or b.entity == entity)
            if len(page) < limit:
                break
            after = page[-1].batch_id
        return out[:limit]

    # ------------------------------------------------------------------ the live check (readings 3 and 8)

    def _check(
        self, entity: str, tasks: Sequence[Task], *, signature: str, rule_version: int | None, maker: str
    ) -> dict[str, _Checked]:
        """Each review checked live, in pages of `ARRIVAL_BATCH`: planned with its target (the decide pane's
        default candidate), or left out with its reason."""
        out: dict[str, _Checked] = {}
        model = self._model(entity)
        now = self.clock()
        for page in capacity.chunks(list(tasks), capacity.ARRIVAL_BATCH):
            out.update(self._check_page(entity, model, page, signature, rule_version, maker, now))
        return out

    def _check_page(
        self,
        entity: str,
        model: EntityModel,
        page: Sequence[Task],
        signature: str,
        rule_version: int | None,
        maker: str,
        now: datetime,
    ) -> dict[str, _Checked]:
        sources = sorted({t.source for t in page if t.source is not None})
        states = self.store.source_states(entity, sources) if sources else {}
        linked = self.store.xrefs_for_sources(entity, sources) if sources else {}
        locks = [task_lock(t.task_id) for t in page] + [
            f"source:{entity}:{s.system}:{s.key}" for s in sources
        ]
        held = self.store.staged_by_locks(locks) if locks else {}
        out: dict[str, _Checked] = {}
        live: list[tuple[Task, SourceState]] = []
        for task in page:
            state = states.get(task.source) if task.source is not None else None
            reason: str | None = None
            holder = self.rows_helper.claim_holder(task, now)
            if task.status != "open" or task.kind != "review" or task.source is None:
                reason = "closed"
            elif rule_version != model.match.version or task.rule_version != rule_version:
                reason = "rules_changed"
            elif (task.signature or "") != signature:
                reason = "signature_changed"
            elif (
                task_lock(task.task_id) in held
                or f"source:{entity}:{task.source.system}:{task.source.key}" in held
            ):
                reason = "staged"
            elif holder is not None and holder != maker:
                reason = "claimed"
            elif task.snoozed_until is not None and task.snoozed_until > now:
                reason = "snoozed"
            elif task.escalated_at is not None:
                reason = "escalated"
            elif state is None or state.status != "active" or state.event_id != task.event_id:
                reason = "record_changed"
            elif task.source in linked:
                reason = "linked"
            if reason is not None:
                out[task.task_id] = _Checked(task, state, reason)
            else:
                assert state is not None
                live.append((task, state))
        if not live:
            return out
        rules = self.registry.compiled(entity)
        search = self.matching.search(model, rules, [s for _, s in live], explain_all=True)
        labels = self.store.labels_for(entity, [s.source.text() for _, s in live])
        named: dict[str, set[str]] = {}
        for lab in labels:
            if lab.label == "not_a_match":
                named.setdefault(lab.left_ref, set()).add(lab.right_ref)
        every = sorted({m for ms in named.values() for m in ms})
        survivors = self.store.resolve_retired(every) if every else {}
        declined = {
            s.source: frozenset(
                named[s.source.text()] | {survivors[m] for m in named[s.source.text()] if m in survivors}
            )
            for _, s in live
            if s.source.text() in named
        }
        pairs = {
            s.source: [p for p in search.pairs.get(s.source, []) if p.right != s.source] for _, s in live
        }
        golden = self.matching.golden_candidates(
            entity,
            pairs,
            {s.source: strong_ids_of(model, s.ids) for _, s in live},
            states=search.states,
            declined=declined or None,
        )
        chosen: dict[str, tuple[Task, SourceState, Any]] = {}
        for task, state in live:
            named_ids = (task.evidence or {}).get("master_ids") or task.master_ids
            offered = offered_candidates(
                golden.get(state.source, []), {m for m in named_ids if isinstance(m, str)}
            )
            if not offered:
                out[task.task_id] = _Checked(task, state, "no_candidate")
                continue
            default, close = default_of(offered, self.settings.close_call_points)
            if close:
                out[task.task_id] = _Checked(task, state, "close_call")
                continue
            if default is None:
                out[task.task_id] = _Checked(task, state, "blocked")
                continue
            best = next(g for g in offered if g.master_id == default)
            explanation = best.best.explanation
            if explanation.signature != signature or explanation.rule_version != rule_version:
                out[task.task_id] = _Checked(task, state, "signature_changed")
                continue
            chosen[task.task_id] = (task, state, best)
        rows = (
            self.store.golden(entity, sorted({b.master_id for _, _, b in chosen.values()})) if chosen else {}
        )
        for task_id, (task, state, best) in chosen.items():
            row = rows.get(best.master_id)
            if row is None or row.status != "active":
                out[task_id] = _Checked(task, state, "no_candidate")
                continue
            explanation = best.best.explanation
            out[task_id] = _Checked(
                task,
                state,
                None,
                target=best.master_id,
                target_version=row.row_version,
                score=round(explanation.score, 6),
                band=explanation.band.value,
                stratum=f"{state.source.system}/{best.best.right.system}",
            )
        return out

    # ------------------------------------------------------------------ the draw (readings 3 and 4)

    def _members(self, group_key: str) -> list[Task]:
        """The group's open reviews in due order, at most `BATCH_MAX`, keyed by (due time, task ID)."""
        out: list[Task] = []
        after: tuple[datetime, str] | None = None
        while len(out) < capacity.BATCH_MAX:
            limit = min(capacity.READ_PAGE, capacity.BATCH_MAX - len(out))
            page = self.store.group_members(group_key, after, limit)
            out.extend(page)
            if len(page) < limit:
                break
            last = page[-1]
            if last.due_at is None:
                break
            after = (last.due_at, last.task_id)
        return out

    def _split_earlier(self, tasks: Sequence[Task]) -> set[str]:
        """The tasks an earlier batch split off at their current event: they stay out until a new event."""
        events = {t.task_id: t.event_id for t in tasks}
        found = self.store.items_by_task(list(events)) if events else {}
        return {
            task_id
            for task_id, items in found.items()
            if any(i.role == "split" and i.event_id == events.get(task_id) for i in items)
        }

    def draw(self, group_key: str, *, actor: Actor, entity: str) -> Batch:
        """Draws a forced sample from a signature group: the group's eligible reviews, the `BATCH_MAX` due soonest,
        become a batch (`sampling`), its sample the smallest keyed draws per stratum. `NotFound(unknown_group)`,
        `Conflict(bulk_withdrawn)` while the signature's bulk rights are withdrawn, `Conflict(group_too_small)`
        when the population does not exceed its sample, `Conflict(batch_open, batch=…)` while the group has an
        open batch. `batch_link`; on a shared store the checkpoint must be in force."""
        self._allow(actor, "batch_link")
        self.settings.validate_checkpoint_in_force()
        if not isinstance(group_key, str) or not SIGNATURE_KEY_RE.match(group_key):
            raise NotFound("unknown_group", group=token(str(group_key)))
        model = self._model(entity)
        first = self.store.group_members(group_key, None, 1)
        if not first or first[0].entity != entity or first[0].rule_version != model.match.version:
            raise NotFound("unknown_group", group=group_key)
        signature, rule_version = first[0].signature or "", first[0].rule_version
        if self._withdrawn(entity, signature):
            raise Conflict([group_key], code="bulk_withdrawn")
        members = self._members(group_key)
        earlier = self._split_earlier(members)
        checked = self._check(
            entity,
            [t for t in members if t.task_id not in earlier],
            signature=signature,
            rule_version=rule_version,
            maker=actor.name,
        )
        left_out: Counter[str] = Counter({"split_earlier": len(earlier)} if earlier else {})
        eligible: list[_Checked] = []
        for task in members:
            found = checked.get(task.task_id)
            if found is None:
                continue
            if found.reason is None:
                eligible.append(found)
            else:
                left_out[found.reason] += 1
        n = len(eligible)
        size = forced_sample_size(n, self.settings.forced_sample_base, self.settings.forced_sample_per)
        if n <= size:
            raise Conflict([group_key], code="group_too_small", reviews=n)
        key = self.settings.sample_key
        draws = {
            c.task.task_id: draw_value(entity, c.task.task_id, "forced_sample", group_key, key)
            for c in eligible
        }
        sample = set(
            pick([Member(c.task.task_id, c.stratum, draws[c.task.task_id]) for c in eligible], size, {})
        )
        now = self.clock()
        batch_id = new_batch_id()
        batch = Batch(
            batch_id=batch_id,
            entity=entity,
            kind="link",
            status="sampling",
            maker=actor.name,
            maker_role=actor.role,
            created_at=now,
            updated_at=now,
            rule_version=rule_version,
            signature=signature,
            signature_key=group_key,
            bulk_band=bulk_band(entity, signature),
            persona=actor.persona,
            population=n,
            sample_size=size,
            figures=safe_detail(drawn=n, left_out_at_draw=dict(sorted(left_out.items()))),
            planning_version=self.store.last_commit_version(),
        )
        items = [
            BatchItem(
                batch_id=batch_id,
                task_id=c.task.task_id,
                role="sample" if c.task.task_id in sample else "bulk",
                status="open" if c.task.task_id in sample else "candidate",
                source=c.state.source if c.state is not None else c.task.source,  # type: ignore[arg-type]
                event_id=c.task.event_id,
                target=c.target,
                target_version=c.target_version,
                score=c.score,
                band=c.band,
                stratum=c.stratum,
                draw=draws[c.task.task_id],
                position=position,
                updated_at=now,
            )
            for position, c in enumerate(eligible, start=1)
        ]
        self.store.insert_batch(batch, items)
        log.info(safe_message("batch_drawn", entity=entity, batch=batch_id, reviews=n, sample=size))
        return batch

    # ------------------------------------------------------------------ the sample, the split and the top-up

    def refresh(self, batch_id: str) -> Batch:
        """Moves a `sampling` batch on, writing only when something changed: a sample review whose task closed
        with no outcome, or whose record moved to another event, turns void, and a bulk candidate whose task
        closed or moved leaves the batch (`excluded`, `task_closed` or `record_changed`); each disagreeing
        review's named split applies, oldest decision first; with n′ the reviews still in the batch, the batch ends
        (`too_few_left`) when n′ does not exceed its sample, the sample is topped up when it holds too few, and
        the batch becomes `ready` once every split applied, every kept sample review agreed, the sample holds
        5 + ⌊n′/150⌋ and the signature's bulk rights are held. Any other batch is returned as it is."""
        batch = self._get(batch_id)
        if batch.status != "sampling":
            return batch
        items = self._items(batch_id, ("sample", "bulk", "split"))
        # 1. void sample reviews, and the bulk candidates decided or moved outside the batch leave it, so n′ counts
        # only reviews that can still be decided; a staged or claimed candidate stays, counted but never drawn,
        # until its decision commits or the claim ends
        open_samples = [i for i in items if i.role == "sample" and i.status == "open"]
        unsampled = [i for i in items if i.role == "bulk" and i.status == "candidate"]
        if open_samples or unsampled:
            tasks = self.store.tasks_by_id([i.task_id for i in (*open_samples, *unsampled)])
            voids: list[dict[str, Any]] = []
            gone: list[dict[str, Any]] = []
            for item in (*open_samples, *unsampled):
                task = tasks.get(item.task_id)
                into, status = (voids, "void") if item.role == "sample" else (gone, "excluded")
                if task is None or task.status != "open":
                    into.append({"task_id": item.task_id, "status": status, "reason": "task_closed"})
                elif task.event_id != item.event_id:
                    into.append({"task_id": item.task_id, "status": status, "reason": "record_changed"})
            if voids or gone:
                with self.store.transaction():
                    if self.store.hold_batch(batch_id, ("sampling",)) is None:
                        return self._get(batch_id)
                    if voids:
                        self.store.update_items(batch_id, voids, from_status="open")
                    if gone:
                        self.store.update_items(batch_id, gone, from_status="candidate")
                items = self._items(batch_id, ("sample", "bulk", "split"))
        # 2. the splits the disagreeing decisions named
        pending = sorted(
            (
                i
                for i in items
                if i.status == "disagreed" and i.role in ("sample", "split") and not i.split_applied
            ),
            key=lambda i: (i.updated_at or batch.created_at, i.task_id),
        )
        for item in pending:
            try:
                self._split(batch, item)
            except Exception as error:  # noqa: BLE001 - the next refresh tries it again
                log.warning("batch_split_failed type=%s", type(error).__name__)
                return self._get(batch_id)
            batch = self._get(batch_id)
            if batch.status != "sampling":
                return batch
        if pending:
            items = self._items(batch_id, ("sample", "bulk", "split"))
        # 3. too few left?
        candidates = [i for i in items if i.role == "bulk" and i.status == "candidate"]
        kept = [i for i in items if i.role == "sample" and i.status in ("open", "agreed")]
        left = len(candidates) + len(kept)
        required = forced_sample_size(left, self.settings.forced_sample_base, self.settings.forced_sample_per)
        if left <= required:
            self._end(batch, "too_few_left", ("sampling",))
            return self._get(batch_id)
        # 4. the top-up: the next draws, the strata that lack a member first
        if len(kept) < required:
            held = Counter(i.stratum for i in kept)
            members = self._top_up_members(batch, candidates)
            picked = pick([Member(i.task_id, i.stratum, i.draw) for i in members], required, held)
            if picked:
                with self.store.transaction():
                    if self.store.hold_batch(batch_id, ("sampling",)) is None:
                        return self._get(batch_id)
                    self.store.update_items(
                        batch_id,
                        [{"task_id": t, "role": "sample", "status": "open"} for t in picked],
                        from_status="candidate",
                    )
                    self.store.set_batch(batch_id, from_statuses=("sampling",), sample_size=required)
            return self._get(batch_id)
        # 5. ready?
        waiting = [i for i in items if i.role == "sample" and i.status in ("open", "disagreed")]
        unapplied = [i for i in items if i.status == "disagreed" and not i.split_applied]
        agreed = [i for i in kept if i.status == "agreed"]
        if waiting or unapplied or len(agreed) < required or self._withdrawn(batch):
            if batch.sample_size != required:
                self.store.set_batch(batch_id, from_statuses=("sampling",), sample_size=required)
            return self._get(batch_id)
        self.store.set_batch(batch_id, from_statuses=("sampling",), status="ready", sample_size=required)
        return self._get(batch_id)

    def _top_up_members(self, batch: Batch, candidates: Sequence[BatchItem]) -> list[BatchItem]:
        """The bulk candidates a top-up may draw, checked as the draw checks them (keyed reads): one whose task
        or record a staged decision holds, or that a steward other than the maker claimed, stays a candidate,
        counted in n′ but not drawn, and one whose task closed or moved to another event since the refresh read
        it is passed by too (the next refresh takes it out of the batch). So a decision staged outside the
        sample pane never becomes a sample outcome by a later top-up."""
        if not candidates:
            return []
        now = self.clock()
        tasks = self.store.tasks_by_id([i.task_id for i in candidates])
        subjects = {i.task_id: _lock_subjects(batch.entity, i) for i in candidates}
        held = self.store.staged_by_locks([s for ss in subjects.values() for s in ss])
        out: list[BatchItem] = []
        for item in candidates:
            task = tasks.get(item.task_id)
            if task is None or task.status != "open" or task.event_id != item.event_id:
                continue
            if any(s in held for s in subjects[item.task_id]):
                continue
            holder = self.rows_helper.claim_holder(task, now)
            if holder is not None and holder != batch.maker:
                continue
            out.append(item)
        return out

    def _split(self, batch: Batch, item: BatchItem) -> int:
        """The split one disagreeing sample review names (the product owner's answer): with a comparison, the
        reviews still in the batch whose record holds the same match form on it as the disagreeing record's
        move to role `split`, with one access row per split record on a personal comparison, under the name
        and role of the steward who named it; with "every alike review", every review moves and the batch ends
        (`split_all`). A disagreement that names no comparison splits nothing: it turns void (`no_comparison`)
        and the sample is topped up, as for a review decided outside the sample pane.

        One transaction, the batch row first; the review, the reviews still in the batch, their records and the
        batch's figures are all read after the hold, and `apply_split` applies the split only while it is not
        applied yet. So two refreshes that both saw the split pending apply it once: the second finds it
        applied, writes nothing, and never overwrites the split's count. Returns the reviews moved (0 when the
        split applied meanwhile, or the batch no longer samples)."""
        with self.store.transaction():
            held = self.store.hold_batch(batch.batch_id, ("sampling",))
            if held is None:
                return 0
            current = next(
                (
                    i
                    for i in self.store.items_by_task([item.task_id]).get(item.task_id, [])
                    if i.batch_id == batch.batch_id
                ),
                None,
            )
            if current is None or current.status != "disagreed" or current.split_applied:
                return 0  # applied meanwhile by another refresh, or nothing to apply
            if current.split_on is None:
                self.store.update_items(
                    batch.batch_id,
                    [{"task_id": current.task_id, "status": "void", "reason": "no_comparison"}],
                    from_status="disagreed",
                )
                return 0
            return self._split_held(held, current)

    def _split_held(self, batch: Batch, item: BatchItem) -> int:
        """`_split`'s work, inside its transaction, with the batch row held and `batch` and `item` read after
        the hold."""
        now = self.clock()
        batch_id = batch.batch_id
        model = self._model(batch.entity)
        comparison = item.split_on or SPLIT_ALL
        spec = next((c for c in model.match.comparisons if c.name == comparison), None)
        if comparison != SPLIT_ALL and (
            spec is None or comparison not in display.signature_comparisons(batch.signature)
        ):
            comparison = (
                SPLIT_ALL  # a comparison the pattern no longer names ends bulk decisions for the batch
            )
        # the reviews still in the batch: the bulk candidates, and the sample reviews open, agreed or disagreed; a
        # void one has left it already, so the split neither moves it nor reads its record
        in_batch = [
            i
            for i in self._items(batch_id, ("sample", "bulk"))
            if (i.role == "sample" and i.status != "void") or i.status == "candidate"
        ]
        splits = dict(batch.figures.get("splits") or {})
        if comparison == SPLIT_ALL or spec is None:
            ids = sorted({*(i.task_id for i in in_batch), item.task_id})
            moved = self.store.apply_split(
                batch_id, item.task_id, ids, f"{SPLIT_REASON_PREFIX}{SPLIT_ALL}", ()
            )
            if moved is None:
                return 0
            if self.store.end_batch(batch_id, outcome="split_all", at=now, from_statuses=("sampling",)):
                splits[item.task_id] = moved
                self.store.set_batch(
                    batch_id,
                    from_statuses=("discarded",),
                    figures=safe_detail(**{**batch.figures, "splits": splits}),
                )
            return moved
        sources = {i.task_id: i.source for i in in_batch}
        sources[item.task_id] = item.source
        states = self.store.source_states(batch.entity, sorted(set(sources.values())))
        form = exact_form(spec)
        forms = {
            task_id: (states[source].match.get(form) if source in states else None)
            for task_id, source in sources.items()
        }
        split = split_members(forms, item.task_id)
        accesses: list[AccessRow] = []
        try:
            personal = model.attribute(spec.attribute).personal
        except NotFound:
            personal = True  # an attribute the model no longer names: log each record split off
        if personal:
            entry = self.store.tray_entries([item.entry_id]).get(item.entry_id) if item.entry_id else None
            who, role = (
                (entry.actor, entry.actor_role) if entry is not None else (batch.maker, batch.maker_role)
            )
            accesses = [
                AccessRow(
                    actor=who,
                    actor_role=role,
                    action="batch_split",
                    entity=batch.entity,
                    master_id=None,
                    attribute=spec.attribute,
                    reason="batch_split",
                    detail=safe_detail(
                        batch_id=batch_id, source=source_token(sources[task_id]), task_id=item.task_id
                    ),
                )
                for task_id in sorted(split)
            ]
        moved = self.store.apply_split(
            batch_id, item.task_id, sorted(split), f"{SPLIT_REASON_PREFIX}{comparison}", accesses
        )
        if moved is None:
            return 0
        splits[item.task_id] = moved
        self.store.set_batch(
            batch_id,
            from_statuses=("sampling",),
            figures=safe_detail(**{**batch.figures, "splits": splits}),
        )
        log.info(safe_message("batch_split", batch=batch_id, comparison=comparison, reviews=moved))
        return moved

    # ------------------------------------------------------------------ every row's change (reading 8)

    def _pack(self, model: EntityModel, planned: Sequence[tuple[str, str]]) -> tuple[list[list[int]], int]:
        """The planned (task ID, target) packed into chunks, and the published rows they write at most."""
        own = 1 + self.lifecycle.relationship_rows(model)
        items = [PackItem(task_id, target, own) for task_id, target in planned]
        chunks = pack(items, capacity.COMMIT_CHUNK_ROWS, capacity.COMMIT_CHUNK_ROWS)
        rows = sum(len(c) * own + len({items[i].target for i in c}) for c in chunks)
        return chunks, rows

    def prepare(self, batch_id: str, *, actor: Actor) -> BatchView:
        """Shows every row's change of a `ready` batch (maker only, `Forbidden(not_the_maker)`;
        `Conflict(not_ready)` otherwise): each candidate checked live again, ordered by target, the joiners of
        one target walked in order against a cannot-link rule (`blocked`), each target's golden change computed
        once with all its joiners, packed into chunks, and the plan frozen. `batch_link`."""
        self._allow(actor, "batch_link")
        batch = self._get(batch_id)
        if batch.maker != actor.name:
            raise Forbidden("not_the_maker")
        if batch.kind != "link" or batch.status != "ready":
            raise Conflict([batch_id], code="not_ready")
        entity = batch.entity
        model = self._model(entity)
        bulk = self._items(batch_id, ("bulk",), ("candidate", "planned"))
        tasks = self.store.tasks_by_id([i.task_id for i in bulk])
        checked = self._check(
            entity,
            [tasks[i.task_id] for i in bulk if i.task_id in tasks],
            signature=batch.signature,
            rule_version=batch.rule_version,
            maker=batch.maker,
        )
        planned: list[tuple[BatchItem, _Checked]] = []
        excluded: list[tuple[BatchItem, str]] = []
        for item in bulk:
            found = checked.get(item.task_id)
            if found is None:
                excluded.append((item, "task_closed"))
            elif found.reason is not None:
                excluded.append((item, found.reason))
            else:
                planned.append((item, found))
        planned.sort(key=lambda p: (p[1].target or "", p[0].position))
        blocked = self.matching.joiners_blocked(
            entity, [(c.state, c.target) for _, c in planned if c.state is not None and c.target]
        )
        excluded += [(i, "blocked") for i, c in planned if c.state is not None and c.state.source in blocked]
        planned = [(i, c) for i, c in planned if c.state is None or c.state.source not in blocked]
        joiners: dict[str, list[SourceKey]] = {}
        for item, found in planned:
            joiners.setdefault(found.target or "", []).append(item.source)
        golden = self.store.golden(entity, list(joiners)) if joiners else {}
        changes = {
            target: display.changed_names(
                model,
                golden[target].values if target in golden else {},
                self.lifecycle.values_with(entity, target, sources),
            )
            for target, sources in joiners.items()
        }
        chunks, rows = self._pack(model, [(i.task_id, c.target or "") for i, c in planned])
        decisions = len(planned)
        left_out = Counter(reason for _, reason in excluded)
        figures = {
            **{k: v for k, v in batch.figures.items()},
            "prepared": 1,
            "xrefs": decisions,
            "golden": len(joiners),
            "chunks": len(chunks),
            "rows": rows,
            "reviews": review_count(decisions, self.settings.sample_share),
            "left_out": dict(sorted(left_out.items())),
        }
        chosen = {p.task_id for p, _ in planned}
        others = [i for i in self._items(batch_id) if i.task_id not in chosen]
        others.sort(key=lambda i: (i.position, i.task_id))
        with self.store.transaction():
            if self.store.hold_batch(batch_id, ("ready",)) is None:
                raise Conflict([batch_id], code="batch_changed")
            if planned:
                self.store.update_items(
                    batch_id,
                    [
                        {
                            "task_id": item.task_id,
                            "status": "planned",
                            "target": found.target,
                            "target_version": found.target_version,
                            "score": found.score,
                            "band": found.band,
                            "event_id": found.task.event_id,
                            "stratum": found.stratum or item.stratum,
                            "changes": list(changes.get(found.target or "", ())),
                            "position": position,
                            "reason": None,
                        }
                        for position, (item, found) in enumerate(planned, start=1)
                    ],
                    from_status=("candidate", "planned"),
                )
            if excluded:
                self.store.update_items(
                    batch_id,
                    [
                        {"task_id": item.task_id, "status": "excluded", "reason": reason}
                        for item, reason in excluded
                    ],
                    from_status=("candidate", "planned"),
                )
            if others:
                self.store.update_items(
                    batch_id,
                    [
                        {"task_id": item.task_id, "position": len(planned) + rank}
                        for rank, item in enumerate(others, start=1)
                    ],
                    from_status=_ALL_ITEM_STATUSES,
                )
            if not self.store.set_batch(
                batch_id,
                from_statuses=("ready",),
                decisions=decisions,
                chunks=len(chunks),
                figures=safe_detail(**figures),
                planning_version=self.store.last_commit_version(),
            ):
                raise Conflict([batch_id], code="batch_changed")
        return self.batch(batch_id, actor=actor)

    # ------------------------------------------------------------------ staging, confirming and undoing (10, 11)

    def stage(self, batch_id: str, *, actor: Actor) -> Batch:
        """Stages a prepared batch (maker only): at or below `batch_checker_above` decisions it enters the tray
        as one entry, locking every planned review's task and record; above, it waits for a second steward
        (`awaiting_checker`). `Conflict(not_prepared)`, `Conflict(bulk_withdrawn)`, `Conflict(batch_empty)`.
        `batch_link`, or `batch_compensate` for a compensation; on a shared store the checkpoint must hold."""
        batch = self._get(batch_id)
        self._allow(actor, "batch_compensate" if batch.kind == "compensate" else "batch_link")
        self.settings.validate_checkpoint_in_force()
        if batch.maker != actor.name:
            raise Forbidden("not_the_maker")
        if batch.status != "ready" or not self._prepared(batch):
            raise Conflict([batch_id], code="not_prepared")
        if self._withdrawn(batch):
            raise Conflict([batch_id], code="bulk_withdrawn")
        if batch.decisions <= 0:
            raise Conflict([batch_id], code="batch_empty")
        if batch.decisions > self.settings.batch_checker_above:
            if not self.store.set_batch(batch_id, from_statuses=("ready",), status="awaiting_checker"):
                raise Conflict([batch_id], code="batch_changed")
            return self._get(batch_id)
        return self._stage(batch, checker=None)

    def confirm(self, batch_id: str, *, actor: Actor) -> Batch:
        """The second steward confirms a batch waiting for one: any steward but the maker
        (`Forbidden(checker_is_maker)`). It enters the tray with its window, recording the checker.
        `confirm_batch`."""
        self._allow(actor, "confirm_batch")
        self.settings.validate_checkpoint_in_force()
        batch = self._get(batch_id)
        if batch.status != "awaiting_checker":
            raise Conflict([batch_id], code="batch_changed")
        if actor.name == batch.maker:
            raise Forbidden("checker_is_maker")
        if self._withdrawn(batch):
            raise Conflict([batch_id], code="bulk_withdrawn")
        return self._stage(batch, checker=actor)

    def send_back(self, batch_id: str, *, actor: Actor) -> Batch:
        """The second steward sends a batch back to `ready`, to its maker. `confirm_batch`, not the maker."""
        self._allow(actor, "confirm_batch")
        batch = self._get(batch_id)
        if actor.name == batch.maker:
            raise Forbidden("checker_is_maker")
        figures = {**batch.figures, "sent_back": _whole(batch.figures.get("sent_back")) + 1}
        if not self.store.set_batch(
            batch_id, from_statuses=("awaiting_checker",), status="ready", figures=safe_detail(**figures)
        ):
            raise Conflict([batch_id], code="batch_changed")
        return self._get(batch_id)

    def _stage(self, batch: Batch, checker: Actor | None) -> Batch:
        """One transaction, the batch row held first (`ready`, or `awaiting_checker` for a confirmation), so a
        preparation of the same batch either commits before it or waits and then finds the batch staged: the
        planned reviews read after the hold, their locks and claims checked again (keyed), any another entry
        holds or another steward claimed left out with its reason, then the batch `staged`, the reviews drawn
        for blind review, and the tray entry, with its second steward, locking every planned review's task and
        record. `Conflict(batch_empty)` when no planned review is left, the exclusions kept."""
        batch_id = batch.batch_id
        entity = batch.entity
        from_statuses = ("awaiting_checker",) if checker is not None else ("ready",)
        empty = False
        with self.store.transaction():
            held_batch = self.store.hold_batch(batch_id, from_statuses)
            if held_batch is None:
                raise Conflict([batch_id], code="batch_changed")
            batch = held_batch
            now = self.clock()
            planned = self._items(batch_id, ("bulk",), ("planned",))
            tasks = self.store.tasks_by_id([i.task_id for i in planned])
            subjects = {i.task_id: _lock_subjects(entity, i) for i in planned}
            held = self.store.staged_by_locks([s for ss in subjects.values() for s in ss])
            excluded: list[tuple[str, str]] = []
            for item in planned:
                task = tasks.get(item.task_id)
                holder = self.rows_helper.claim_holder(task, now) if task is not None else None
                if batch.kind == "link" and (task is None or task.status != "open"):
                    excluded.append((item.task_id, "task_closed"))
                elif any(s in held for s in subjects[item.task_id]):
                    excluded.append((item.task_id, "staged"))
                elif batch.kind == "link" and holder is not None and holder != batch.maker:
                    excluded.append((item.task_id, "claimed"))
            if excluded:
                gone = {t for t, _ in excluded}
                planned = [i for i in planned if i.task_id not in gone]
                left_out = Counter(dict(batch.figures.get("left_out") or {}))
                left_out.update(reason for _, reason in excluded)
                figures = {**batch.figures, "left_out": dict(sorted(left_out.items()))}
                if batch.kind == "link":
                    chunks, rows = self._pack(
                        self._model(entity), [(i.task_id, i.target or "") for i in planned]
                    )
                    figures.update(
                        xrefs=len(planned),
                        golden=len({i.target for i in planned}),
                        chunks=len(chunks),
                        rows=rows,
                        reviews=review_count(len(planned), self.settings.sample_share),
                    )
                self.store.update_items(
                    batch_id,
                    [{"task_id": t, "status": "excluded", "reason": r} for t, r in excluded],
                    from_status="planned",
                )
                self.store.set_batch(
                    batch_id,
                    from_statuses=from_statuses,
                    decisions=len(planned),
                    chunks=_whole(figures.get("chunks")) or batch.chunks,
                    figures=safe_detail(**figures),
                )
            if not planned:
                empty = True
            else:
                self._stage_held(batch, planned, checker, now, from_statuses)
        if empty:
            raise Conflict([batch_id], code="batch_empty")
        return self._get(batch_id)

    def _stage_held(
        self,
        batch: Batch,
        planned: Sequence[BatchItem],
        checker: Actor | None,
        now: datetime,
        from_statuses: Sequence[str],
    ) -> None:
        """`_stage`'s writes, inside its transaction with the batch row held: the batch `staged`, the reviews
        drawn for blind review, and the tray entry with its locks."""
        batch_id = batch.batch_id
        entity = batch.entity
        entry_id = "TR-" + secrets.token_hex(10)
        decision = "batch_compensate" if batch.kind == "compensate" else "batch_link"
        entry = TrayEntry(
            entry_id=entry_id,
            task_id=batch_id,
            entity=entity,
            decision=decision,
            target=None,
            subject=safe_detail(
                batch_id=batch_id,
                decisions=len(planned),
                entity=entity,
                kind=batch.kind,
                compensates=batch.compensates,
            ),
            signature=batch.signature or None,
            actor=batch.maker,
            actor_role=batch.maker_role,
            persona=batch.persona,
            event_id=None,
            planning_version=batch.planning_version,
            staged_at=now,
            deadline=now + timedelta(seconds=self.settings.undo_seconds),
            status="staged",
            checker=checker.name if checker is not None else None,
        )
        reviews: frozenset[str] = frozenset()
        if batch.kind == "link":
            key = self.settings.sample_key
            reviews = pick_reviews(
                [
                    (i.task_id, draw_value(entity, i.source.text(), "batch_link", batch_id, key))
                    for i in planned
                ],
                review_count(len(planned), self.settings.sample_share),
            )
        fields: dict[str, Any] = {"status": "staged", "entry_id": entry_id, "staged_at": now}
        if checker is not None:
            fields.update(checker=checker.name, checker_role=checker.role, checked_at=now)
        if not self.store.set_batch(batch_id, from_statuses=from_statuses, **fields):
            raise Conflict([batch_id], code="batch_changed")
        if reviews:
            self.store.update_items(
                batch_id, [{"task_id": t, "review": True} for t in sorted(reviews)], from_status="planned"
            )
        self.store.stage_tray(entry, [s for i in planned for s in _lock_subjects(entity, i)])

    def undo(self, entry: TrayEntry, *, actor: Actor) -> TrayEntry:
        """Takes a batch's entry back while it waits (`TrayService.undo`): the maker or the batch's second steward
        only (`Forbidden(not_yours)`). The batch is `ready` again with its entry, checker and review flags
        cleared, the entry settled `undone` and every lock gone; once a chunk committed,
        `Conflict(already_settled, batch=…)`."""
        self._allow(actor, "work_tasks")
        batch = self._get(entry.task_id)
        # the entry keeps its second steward, so a stale Undo of theirs reads "already undone", not "not yours"
        if actor.name not in (batch.maker, batch.checker, entry.checker):
            raise Forbidden("not_yours")
        now = self.clock()
        undone = self.store.undo_batch(
            batch.batch_id,
            entry.entry_id,
            now,
            after_hold=(lambda: self._fault("in_undo")) if self.fault is not None else None,
        )
        found = self.store.tray_entries([entry.entry_id]).get(entry.entry_id)
        if not undone:
            raise Conflict(
                [token(entry.entry_id)],
                code="already_settled",
                status=found.status if found is not None else None,
                version=found.commit_version if found is not None else None,
                batch=batch.batch_id,
            )
        assert found is not None
        return found

    def discard(self, batch_id: str, *, actor: Actor) -> Batch:
        """Discards a batch before the tray: its maker, or any coordinating steward. It becomes `discarded`, its
        group can draw again, and its reviews stay in the inbox; a compensation discarded clears its original's
        mark. `Conflict(still_in_tray)` while staged: undo it instead."""
        batch = self._get(batch_id)
        self._allow(actor, "batch_compensate" if batch.kind == "compensate" else "batch_link")
        if actor.name != batch.maker and actor.role != "coordinating_steward":
            raise Forbidden("not_the_maker")
        if batch.status == "staged":
            raise Conflict([batch_id], code="still_in_tray")
        if batch.status not in BEFORE_TRAY or not self._end(batch, "discarded"):
            raise Conflict([batch_id], code="batch_changed")
        return self._get(batch_id)

    def stop(self, batch_id: str, *, actor: Actor) -> Batch:
        """Asks a committing batch to stop before its next chunk, whatever the throttle; committed chunks stay.
        Any steward. `Conflict(still_in_tray)` while staged (undo it), `Conflict(not_committing)` otherwise.
        `stop_batch`."""
        self._allow(actor, "stop_batch")
        batch = self._get(batch_id)
        if batch.status == "staged":
            raise Conflict([batch_id], code="still_in_tray")
        if batch.status != "committing":
            raise Conflict([batch_id], code="not_committing")
        self.store.request_stop(batch_id, actor.name, self.clock(), actor.role)
        return self._get(batch_id)

    # ------------------------------------------------------------------ compensation (reading 15)

    def compensate(self, batch_id: str, *, actor: Actor, reason: str) -> Batch:
        """Prepares a compensation: a batch of kind `compensate`, `ready`, over the original's reviews still
        committed in chunks no compensation undid, each with its golden record's values without the batch's
        records; the original is marked in the same transaction. `Forbidden(bad_compensate_reason)`,
        `Conflict(not_compensable)`, `Conflict(undo_window_passed)`, `Conflict(batch_empty)`,
        `Conflict(already_compensated)`. `batch_compensate`. `stage` then stages it."""
        self._allow(actor, "batch_compensate")
        if reason not in COMPENSATE_REASONS:
            raise Forbidden("bad_compensate_reason")
        original = self._get(batch_id)
        if (
            original.kind != "link"
            or original.status not in ("committed", "stopped")
            or original.chunks_committed < 1
        ):
            raise Conflict([batch_id], code="not_compensable")
        now = self.clock()
        if original.finished_at is None or now > original.finished_at + timedelta(
            days=self.settings.batch_undo_days
        ):
            raise Conflict([batch_id], code="undo_window_passed")
        if original.compensated_by is not None:
            raise Conflict([batch_id], code="already_compensated", batch=original.compensated_by)
        open_chunks = {c.chunk_no for c in self.store.batch_chunks(batch_id) if c.compensated_by is None}
        reviews = [
            i
            for i in self._items(batch_id, ("bulk",), ("committed",))
            if i.chunk_no in open_chunks and i.target
        ]
        if not reviews:
            raise Conflict([batch_id], code="batch_empty")
        entity = original.entity
        model = self._model(entity)
        leaving: dict[str, list[SourceKey]] = {}
        for item in reviews:
            leaving.setdefault(item.target or "", []).append(item.source)
        golden = self.store.golden(entity, list(leaving))
        changes = {
            target: display.changed_names(
                model,
                golden[target].values if target in golden else {},
                self.lifecycle.values_with(entity, target, without=sources),
            )
            for target, sources in leaving.items()
        }
        chunks = sorted({i.chunk_no for i in reviews if i.chunk_no is not None})
        own = 1 + self.lifecycle.relationship_rows(model)
        rows = sum(
            len([i for i in reviews if i.chunk_no == n]) * own
            + len({i.target for i in reviews if i.chunk_no == n})
            for n in chunks
        )
        compensation_id = new_batch_id()
        batch = Batch(
            batch_id=compensation_id,
            entity=entity,
            kind="compensate",
            status="ready",
            maker=actor.name,
            maker_role=actor.role,
            created_at=now,
            updated_at=now,
            persona=actor.persona,
            population=len(reviews),
            decisions=len(reviews),
            chunks=len(chunks),
            compensates=batch_id,
            compensate_reason=reason,
            figures=safe_detail(
                prepared=1,
                xrefs=len(reviews),
                golden=len(leaving),
                chunks=len(chunks),
                rows=rows,
                reviews=0,
                left_out={},
            ),
            planning_version=self.store.last_commit_version(),
        )
        items = [
            replace(
                item,
                batch_id=compensation_id,
                role="bulk",
                status="planned",
                target_version=golden[item.target].row_version if item.target in golden else None,
                changes=tuple(changes.get(item.target or "", ())),
                review=False,
                change_set_id=None,
                entry_id=None,
                split_on=None,
                split_applied=False,
                reason=None,
                updated_at=now,
            )
            for item in reviews
        ]
        self.store.insert_batch(batch, items)
        log.info(safe_message("batch_compensation_prepared", batch=compensation_id, original=batch_id))
        return batch

    # ------------------------------------------------------------------ the flush (readings 9, 10 and 12)

    def commit_first(self, entry: TrayEntry, now: datetime) -> ChunkPass:
        """A batch entry past its deadline (`TrayService.flush`, under its lease): the entry's attempts bumped (0:
        undone meanwhile), then its first chunk. `ChunkPass.outcome` is `committed` when the chunk committed,
        else the code the entry settled `failed` with."""
        attempts = self.store.bump_tray_attempts(entry.entry_id)
        if attempts == 0:
            return ChunkPass()
        batch = self.store.batches([entry.task_id]).get(entry.task_id)
        if batch is None or batch.entry_id != entry.entry_id:
            # no batch holds this entry: it is settled, so it never blocks a lock
            if self.store.fail_tray(entry.entry_id, "batch_unknown", now, entry.task_id, entry.actor):
                return ChunkPass(outcome="batch_unknown")
            return ChunkPass()
        if batch.status != "staged":
            return ChunkPass()
        return self._commit_chunk(batch, entry, now)

    def commit_next(
        self,
        now: datetime,
        *,
        skip: frozenset[str] | set[str] = frozenset(),
        limit: int = capacity.FLUSH_BATCH,
    ) -> Counter[str]:
        """The next chunk of each committing batch that is due (`batches_due`), but those in `skip` (the batches
        that committed a chunk in this pass): a stop asked for, or bulk rights withdrawn, end the batch before
        any throttle check; a batch the throttle still holds is passed by. Returns counts of `chunks` and
        `finished`."""
        counts: Counter[str] = Counter()
        for batch in self.store.batches_due(now, limit):
            if batch.batch_id in skip:
                continue
            if batch.stop_requested_at is not None:
                result = self._finish(batch, "stopped", "stopped")
            elif self._withdrawn(batch):
                result = self._finish(batch, "stopped", "bulk_withdrawn")
            elif batch.not_before is not None and batch.not_before > now:
                continue
            else:
                entry = (
                    self.store.tray_entries([batch.entry_id]).get(batch.entry_id) if batch.entry_id else None
                )
                if entry is None:
                    continue
                result = self._commit_chunk(batch, entry, now)
            counts["chunks"] += result.chunks
            counts["finished"] += result.finished
        return counts

    def _finish(self, batch: Batch, status: str, outcome: str, entry_id: str | None = None) -> ChunkPass:
        """Ends a staged or committing batch (`finish_batch`): `committed`, else `stopped` after at least one
        chunk and `failed` when nothing committed. Its planned reviews are released and their locks go. With
        `entry_id`, the entry the pass was committing, only that staging ends: a batch undone meanwhile and
        staged again under another entry is left as it is."""
        self._fault("before_finish")
        current = self.store.batches([batch.batch_id]).get(batch.batch_id) or batch
        if current.entry_id is None or (entry_id is not None and current.entry_id != entry_id):
            return ChunkPass()
        final = status if status == "committed" else ("stopped" if current.chunks_committed > 0 else "failed")
        done = self.store.finish_batch(
            current.batch_id, status=final, outcome=outcome, at=self.clock(), entry_id=current.entry_id
        )
        if not done:
            return ChunkPass()
        log.info(safe_message("batch_finished", batch=current.batch_id, status=final, outcome=outcome))
        return ChunkPass(finished=1, outcome=outcome if current.chunks_committed == 0 else None)

    def _failed_pass(self, batch: Batch, entry_id: str) -> ChunkPass:
        """A chunk of the entry `entry_id` that could not commit in this pass: one more failed pass in a row, and
        at `FLUSH_ATTEMPTS` the batch stops (`chunk_failed`). A chunk that commits, or an undo, sets the count
        back to 0; a batch staged again under another entry meanwhile is left as it is."""
        current = self.store.batches([batch.batch_id]).get(batch.batch_id) or batch
        if current.entry_id != entry_id:
            return ChunkPass()
        attempts = current.attempts + 1
        if attempts >= capacity.FLUSH_ATTEMPTS:
            return self._finish(current, "stopped", "chunk_failed", entry_id)
        self.store.set_batch(current.batch_id, from_statuses=("staged", "committing"), attempts=attempts)
        return ChunkPass()

    def _commit_chunk(self, batch: Batch, entry: TrayEntry, now: datetime) -> ChunkPass:
        """The next chunk of a batch, with its failures handled (A.5 step 8): a conflict of a moved row is
        planned again once in the pass; an undo that won the batch row finishes nothing; a stop, a withdrawal
        or any other refusal ends the batch; an unexpected failure counts as a failed pass."""
        own = entry.entry_id
        if batch.persona and not self.settings.local_mode:
            return self._finish(batch, "stopped", "persona_refused", own)
        result: ChunkPass | None = None
        for attempt in (1, 2):
            try:
                result = self._chunk(batch, entry, now)
                break
            except Conflict as error:
                code = error.code
                if code in _RETRY and attempt == 1:
                    batch = self.store.batches([batch.batch_id]).get(batch.batch_id) or batch
                    continue
                if code in _RETRY:
                    return self._failed_pass(batch, own)
                if code in ("batch_changed", "tray_entry_settled"):
                    return ChunkPass()  # undone, or ended, meanwhile: nothing to finish
                if code == "batch_stopped":
                    return self._finish(batch, "stopped", "stopped", own)
                if code == "bulk_withdrawn":
                    return self._finish(batch, "stopped", "bulk_withdrawn", own)
                return self._finish(batch, "stopped", token(code), own)
            except MdmError as error:
                # a refusal ends only the staging this pass was committing, never one staged since
                return self._finish(batch, "stopped", token(error.code), own)
            except Exception as error:  # noqa: BLE001 - one batch must never stop the flush
                log.warning("batch_chunk_failed type=%s", type(error).__name__)
                return self._failed_pass(batch, own)
        if result is not None and result.chunks:
            self._fault("after_chunk")
        return result or ChunkPass()

    def _chunk(self, batch: Batch, entry: TrayEntry, now: datetime) -> ChunkPass:
        """One chunk: the next planned reviews checked again with keyed reads (those that moved fail alone), packed,
        planned and committed through `CommitService.apply` as the change set `CS-<hex>-<n>`, with the reviews'
        labels, blind-review samples, task closing and lock release in the same transaction."""
        entity = batch.entity
        link = batch.kind == "link"
        while True:
            page = self.store.batch_items(
                batch.batch_id, ("bulk",), ("planned",), None, capacity.COMMIT_CHUNK_ROWS + 1
            )
            if not page:
                if batch.chunks_committed == 0:
                    return self._finish(batch, "failed", "nothing_left", entry.entry_id)
                return self._finish(batch, "committed", "committed", entry.entry_id)
            survivors, failures = self._precheck(batch, page)
            if failures:
                subjects = [s for i in failures for s in _lock_subjects(entity, i[0])]
                with self.store.transaction():
                    moved = self.store.fail_items(
                        batch.batch_id,
                        [(i.task_id, "failed" if link else "kept", reason) for i, reason in failures],
                        subjects,
                    )
                    passed = (
                        self._pass_flags(batch, [i for i, _ in failures if i.review])
                        if moved == len(failures)
                        else frozenset()
                    )
                if moved < len(failures):
                    return ChunkPass()  # the batch changed meanwhile
                if passed:
                    survivors = [replace(i, review=True) if i.task_id in passed else i for i in survivors]
            if survivors:
                break
            batch = self.store.batches([batch.batch_id]).get(batch.batch_id) or batch
        model = self._model(entity)
        if link:
            own = 1 + self.lifecycle.relationship_rows(model)
            first = pack(
                [PackItem(i.task_id, i.target or "", own) for i in survivors],
                capacity.COMMIT_CHUNK_ROWS,
                capacity.COMMIT_CHUNK_ROWS,
            )[0]
            chunk = [survivors[k] for k in first]
            number = batch.chunks_committed + 1
            undoes: int | None = None
        else:
            number = survivors[0].chunk_no or batch.chunks_committed + 1
            chunk = [i for i in survivors if i.chunk_no == survivors[0].chunk_no]
            undoes = survivors[0].chunk_no
        last = len(page) <= capacity.COMMIT_CHUNK_ROWS and len(chunk) == len(survivors)
        first_chunk = batch.chunks_committed == 0
        rate = self.settings.throttle_rows_per_hour
        chunk_write = BatchChunkWrite(
            batch_id=batch.batch_id,
            chunk_no=number,
            kind=batch.kind,
            entry_id=entry.entry_id,
            first=first_chunk,
            last=last,
            task_ids=tuple(i.task_id for i in chunk),
            subjects=tuple(s for i in chunk for s in _lock_subjects(entity, i)),
            bulk_band=batch.bulk_band if link else None,
            signature=batch.signature if link else None,
            seconds_per_row=3600.0 / rate if rate > 0 else 0.0,
            compensates=batch.compensates,
            undoes_chunk=undoes,
        )
        settle = (
            (
                TraySettlement(
                    entry.entry_id,
                    "committed",
                    None,
                    "committed" if last else COMMITTING,
                    keep_locks=not last,
                ),
            )
            if first_chunk
            else ()
        )
        sources = [i.source for i in chunk]
        if link:
            items, own_work = self.lifecycle.plan_batch_links(
                entity, [(i.source, i.target or "", i.event_id) for i in chunk]
            )
            drawn = [self.quality.batch_sample(batch, i, now) for i in chunk if i.review]
            work = WorkWrites(
                entity,
                labels=tuple(self._label(batch, entry, i, now) for i in chunk),
                samples=tuple(s for s, _ in drawn),
                tasks=tuple(t for _, t in drawn),
                expect_events=tuple(sorted((i.source, i.event_id or "") for i in chunk)),
                close_task_ids=tuple(i.task_id for i in chunk),
                batch=chunk_write,
                tray=settle,
            )
            action = "batch_link"
        else:
            items, own_work = self.lifecycle.plan_batch_detaches(
                entity, [(i.source, i.target or "") for i in chunk]
            )
            states = self.store.source_states(entity, sources)
            original = self.store.batches([batch.compensates or ""]).get(batch.compensates or "")
            original_entry = original.entry_id if original is not None else None
            voids = tuple(
                (s.sample_id, s.task_id, s.source)
                for s in self.store.open_samples_for(entity, sources)
                if s.origin == "batch" and original_entry is not None and s.entry_id == original_entry
            )
            work = WorkWrites(
                entity,
                requeue=tuple((s, states[s].event_id, states[s].landing_seq) for s in sources if s in states),
                unlabel=tuple(
                    (i.source.text(), i.target or "", original_entry) for i in chunk if original_entry
                ),
                void_samples=voids,
                expect_events=tuple(sorted((s, states[s].event_id) for s in sources if s in states)),
                batch=chunk_write,
                tray=settle,
            )
            action = "batch_compensate"
        work = own_work.merged(work)
        maker = Actor(batch.maker, "person", batch.maker_role, persona=batch.persona)
        checker = (
            Actor(
                batch.checker,
                "person",
                batch.checker_role or "",
                persona=batch.checker.startswith("persona:"),
            )
            if batch.checker
            else None
        )
        targets = sorted({i.target for i in chunk if i.target})
        evidence = safe_detail(
            decision=action,
            batch_id=batch.batch_id,
            chunk=number,
            chunks=batch.chunks,
            entry_id=entry.entry_id,
            decisions=batch.decisions,
            items=len(chunk),
            bulk=batch.bulk_band,
            group=batch.signature_key,
            compensates=batch.compensates,
            undoes_chunk=undoes,
            master_ids=targets if not link else None,
        )
        cs = new_change_set(
            entity,
            action,
            maker,
            role_authority(maker, checker),
            items,
            planning_version=self.store.last_commit_version(),
            reason=f"workbench:{action}",
            evidence=evidence,
            checker=checker,
        )
        cs = replace(cs, change_set_id=chunk_change_set_id(batch.batch_id, number))
        self.commit.apply(cs, work, audit_always=True)
        if not link:
            try:
                self.arrival.settle_records(entity, sources)
            except Exception as error:  # noqa: BLE001 - the chunk has committed; arrival settles them later
                log.warning("batch_settle_records_failed type=%s", type(error).__name__)
        return ChunkPass(chunks=1, finished=1 if last else 0, outcome="committed")

    def _pass_flags(self, batch: Batch, failed: Sequence[BatchItem]) -> frozenset[str]:
        """Inside `fail_items`' transaction, the batch row held: each review drawn for blind review that failed
        its pre-check passes its draw to the planned review not drawn yet with the next smallest draw value
        (the draw of staging, `draw_value(entity, record, "batch_link", batch ID, key)`), so the batch still
        sends ⌈2% × its links⌉ to blind review, at least one. Returns the reviews drawn now; a draw is lost only
        when no planned review is left to take it."""
        if not failed:
            return frozenset()
        gone = {i.task_id for i in failed}
        key = self.settings.sample_key
        others = [
            (i.task_id, draw_value(batch.entity, i.source.text(), "batch_link", batch.batch_id, key))
            for i in self._items(batch.batch_id, ("bulk",), ("planned",))
            if not i.review and i.task_id not in gone
        ]
        chosen = pick_reviews(others, len(failed))
        if chosen:
            self.store.update_items(
                batch.batch_id,
                [{"task_id": t, "review": True} for t in sorted(chosen)],
                from_status="planned",
            )
        return chosen

    @staticmethod
    def _label(batch: Batch, entry: TrayEntry, item: BatchItem, now: datetime) -> MatchLabel:
        """The match label a batch's link writes: its planned score, band and rule version, the batch's
        signature, its task, the batch's entry, and the maker as decider (decision 22)."""
        return MatchLabel(
            entity=batch.entity,
            left_ref=item.source.text(),
            right_ref=item.target or "",
            label="match",
            rule_version=batch.rule_version,
            score=item.score,
            band=item.band,
            signature=batch.signature or None,
            task_id=item.task_id,
            entry_id=entry.entry_id,
            decided_by=batch.maker,
            decided_role=batch.maker_role,
            decided_at=now,
        )

    def _precheck(
        self, batch: Batch, page: Sequence[BatchItem]
    ) -> tuple[list[BatchItem], list[tuple[BatchItem, str]]]:
        """Every planned review of the page checked again with keyed reads (reading 10): (survivors, failures
        with their reasons)."""
        entity = batch.entity
        sources = [i.source for i in page]
        failures: list[tuple[BatchItem, str]] = []
        survivors: list[BatchItem] = []
        if batch.kind != "link":
            original = batch.compensates or ""
            versions = {c.chunk_no: c.commit_version for c in self.store.batch_chunks(original)}
            rows = self.store.xref_rows(entity, sources)
            # the record's own state too, as `plan_batch_detaches` checks it: a record whose delete was taken
            # in but not yet settled keeps its link a while, and must be kept alone, not fail the chunk
            records = self.store.source_states(entity, sources)
            for item in page:
                row = rows.get(item.source)
                state = records.get(item.source)
                if (
                    row is None
                    or row.status != "active"
                    or row.master_id != item.target
                    or row.commit_version != versions.get(item.chunk_no or 0)
                ):
                    failures.append((item, "moved_since"))
                elif state is None or state.status != "active":
                    failures.append((item, "record_changed"))
                else:
                    survivors.append(item)
            return survivors, failures
        tasks = self.store.tasks_by_id([i.task_id for i in page])
        states = self.store.source_states(entity, sources)
        linked = self.store.xrefs_for_sources(entity, sources)
        golden = self.store.golden(entity, sorted({i.target for i in page if i.target}))
        versions = {c.commit_version for c in self.store.batch_chunks(batch.batch_id) if c.commit_version}
        checked: list[BatchItem] = []
        for item in page:
            task = tasks.get(item.task_id)
            state = states.get(item.source)
            row = golden.get(item.target or "")
            if task is None or task.status != "open":
                failures.append((item, "task_closed"))
            elif state is None or state.status != "active" or state.event_id != item.event_id:
                failures.append((item, "record_changed"))
            elif item.source in linked:
                failures.append((item, "record_changed"))
            elif (
                row is None
                or row.status != "active"
                or (row.row_version != item.target_version and row.commit_version not in versions)
            ):
                failures.append((item, "target_changed"))
            else:
                checked.append(item)
        blocked = self.matching.joiners_blocked(entity, [(states[i.source], i.target or "") for i in checked])
        for item in checked:
            if item.source in blocked:
                failures.append((item, "blocked"))
            else:
                survivors.append(item)
        return survivors, failures

    # ------------------------------------------------------------------ the batch page (B.3)

    def batch(self, batch_id: str, *, actor: Actor) -> BatchView:
        """The batch page: its stage, its forced sample with each review's outcome, its splits, its summary and
        progress, its result, and its actions with why each is disabled. A `sampling` batch is refreshed first.
        Titles are masked by role. `view_tasks`."""
        self._allow(actor, "view_tasks")
        batch = self._get(batch_id)
        if batch.status == "sampling":
            batch = self.refresh(batch_id)
        return self._view(batch, actor)

    def _view(self, batch: Batch, actor: Actor) -> BatchView:
        try:
            model: EntityModel | None = self._model(batch.entity)
        except NotFound:
            model = None
        items = self._items(batch.batch_id)
        drawn = [
            i for i in items if i.role == "sample" or (i.role == "split" and i.status in SAMPLE_OUTCOMES)
        ]
        tasks = self.store.tasks_by_id([i.task_id for i in drawn]) if drawn else {}
        titles = self.rows_helper.titles([tasks[i.task_id] for i in drawn if i.task_id in tasks])
        entries = self.store.tray_entries([i.entry_id for i in drawn if i.entry_id]) if drawn else {}
        sample: list[SampleReview] = []
        splits: list[SplitLine] = []
        split_counts = dict(batch.figures.get("splits") or {})
        for item in drawn:
            entry = entries.get(item.entry_id or "")
            decided = "Not a match" if entry is not None and entry.decision == "not_a_match" else None
            if item.status == "disagreed":
                decision_words = decided or "Linked to another golden record"
                on = display.split_on_label(model, item.split_on)
                words = f"{decision_words}: disagrees, flagged on {on}"
                count = split_counts.get(item.task_id)
                splits.append(
                    SplitLine(
                        task_id=item.task_id,
                        source=item.source.text(),
                        decision_words=decision_words,
                        on_label=on,
                        count=_whole(count) if item.split_applied and count is not None else None,
                    )
                )
            elif item.role == "split":
                # another review's split took it out of the batch: it no longer counts toward the sample
                words = _SPLIT_OFF_WORDS
            else:
                words = _SAMPLE_WORDS.get(item.status, item.status)
            task = tasks.get(item.task_id)
            sample.append(
                SampleReview(
                    task_id=item.task_id,
                    source=item.source.text(),
                    title=titles.get(item.task_id, item.source.text()),
                    stratum_label=display.stratum_label(item.stratum),
                    status=item.status,
                    words=words,
                    open=item.role == "sample"
                    and item.status == "open"
                    and task is not None
                    and task.status == "open",
                )
            )
        agreed = sum(1 for i in drawn if i.status == "agreed" and i.role == "sample")
        disagreed = sum(1 for i in drawn if i.status == "disagreed")
        waiting = sum(1 for i in drawn if i.status == "open" and i.role == "sample")
        figures = batch.figures
        summary = (
            BatchSummary(
                xrefs=_whole(figures.get("xrefs")),
                golden=_whole(figures.get("golden")),
                chunks=_whole(figures.get("chunks")),
                rows=_whole(figures.get("rows")),
                reviews=_whole(figures.get("reviews")),
                left_out={str(k): _whole(v) for k, v in dict(figures.get("left_out") or {}).items()},
            )
            if self._prepared(batch)
            else None
        )
        in_tray = batch.status in ("staged", "committing") or batch.chunks_committed > 0
        progress = (
            BatchProgress(
                chunks=batch.chunks,
                chunks_committed=batch.chunks_committed,
                rows_committed=batch.rows_committed,
                not_before=batch.not_before,
                stop_requested=batch.stop_requested_at is not None,
            )
            if in_tray
            else None
        )
        deadline = None
        if batch.status == "staged" and batch.entry_id:
            found = self.store.tray_entries([batch.entry_id]).get(batch.entry_id)
            deadline = found.deadline if found is not None else None
        chunks = (
            self.store.batch_chunks(batch.batch_id)
            if batch.chunks_committed or batch.status in _FINISHED
            else []
        )
        versions = [c.commit_version for c in chunks if c.commit_version is not None]
        undone_by = tuple(sorted({c.compensated_by for c in chunks if c.compensated_by}))
        compensations = self._compensations(batch, undone_by)
        undo_until = (
            batch.finished_at + timedelta(days=self.settings.batch_undo_days)
            if batch.kind == "link"
            and batch.status in ("committed", "stopped")
            and batch.chunks_committed
            and batch.finished_at is not None
            else None
        )
        counts = Counter(i.status for i in items if i.role == "bulk")
        return BatchView(
            batch_id=batch.batch_id,
            kind=batch.kind,
            entity=batch.entity,
            entity_label=_entity_label(batch.entity),
            group_key=batch.signature_key,
            marks=display.signature_marks(model, batch.signature),
            status=batch.status,
            maker_label="you" if actor.name == batch.maker else display.role_label(batch.maker_role),
            checker_label=(
                ("you" if actor.name == batch.checker else display.role_label(batch.checker_role or ""))
                if batch.checker
                else None
            ),
            population=batch.population,
            sample_size=batch.sample_size,
            sample=tuple(sample),
            # a disagreeing review counts toward the sample until its split takes it out, as `agreed` counts
            decided=agreed + sum(1 for i in drawn if i.status == "disagreed" and i.role == "sample"),
            agreed=agreed,
            disagreed=disagreed,
            waiting=waiting,
            splits=tuple(splits),
            summary=summary,
            progress=progress,
            entry_id=batch.entry_id if batch.status == "staged" else None,
            deadline=deadline,
            withdrawn=self.breaker.bulk_withdrawn(batch.entity, batch.signature)
            if batch.kind == "link"
            else None,
            actions=self._actions(batch, actor, waiting),
            notice=None,
            compensates=batch.compensates,
            compensated_by=batch.compensated_by,
            outcome=batch.outcome,
            commits=(min(versions), max(versions)) if versions else None,
            finished_at=batch.finished_at,
            counts={
                k: counts.get(k, 0)
                for k in ("committed", "failed", "released", "kept", "excluded", "compensated")
            },
            undo_until=undo_until,
            undone_by=undone_by,
            stopped_by=(
                (
                    "you"
                    if actor.name == batch.stop_requested_by
                    else display.role_label(batch.stop_requested_role or "")
                )
                if batch.stop_requested_at is not None
                else None
            ),
            compensations=compensations,
            blind_reviews=sum(
                1 for i in items if i.role == "bulk" and i.review and i.status in ("committed", "compensated")
            ),
        )

    def _compensations(self, batch: Batch, undone_by: Sequence[str]) -> tuple[CompensationLine, ...]:
        """A link batch's compensations, oldest first: every one that undid a chunk and the one open on it, each
        with its status and the original's links its own committed chunks reversed (keyed reads, a few)."""
        ids = sorted({*undone_by, *([batch.compensated_by] if batch.compensated_by else [])})
        if batch.kind != "link" or not ids:
            return ()
        found = self.store.batches(ids)
        lines = [
            (
                other.created_at,
                CompensationLine(
                    batch_id=other.batch_id,
                    status=other.status,
                    outcome=other.outcome,
                    undone=sum(c.items for c in self.store.batch_chunks(other.batch_id)),
                ),
            )
            for other in found.values()
        ]
        return tuple(line for _, line in sorted(lines, key=lambda pair: (pair[0], pair[1].batch_id)))

    def _actions(self, batch: Batch, actor: Actor, waiting: int) -> tuple[Action, ...]:
        """The batch page's actions (`PAGE_ACTIONS`), each with `enabled` and why not, as B.3's table words them."""
        maker = actor.name == batch.maker
        right = "batch_compensate" if batch.kind == "compensate" else "batch_link"
        role = ROLE_LABELS.get(actor.role, actor.role).lower()
        can = allowed(actor, right)
        role_why = None if can else NOTICE_ROLE.format(role=role)
        status = batch.status
        out: list[Action] = []

        def action(code: str, label: str, enabled: bool, why: str | None) -> None:
            out.append(Action(code, label, "", enabled, None if enabled else why))

        if status == "sampling":
            # only a role that decides tasks is sent to decide them: a data owner or technical steward reads
            works = allowed(actor, "work_tasks")
            why = WHY_DECIDED if works else NOTICE_READS.format(role=role)
            action("decide_sample", "Decide the sample in the inbox", works and waiting > 0, why)
        elif status == "ready" and batch.kind == "link" and not self._prepared(batch):
            action("prepare", "Show every change", can and maker, role_why or WHY_PREPARE)
        elif status == "ready" and batch.kind == "link":
            label = (
                f"Link all {batch.decisions}"
                if batch.decisions <= self.settings.batch_checker_above
                else "Ask a second steward to confirm"
            )
            action("stage", label, can and maker, role_why or WHY_STAGE)
        elif status == "awaiting_checker":
            if maker:
                action("confirm", "Confirm the batch", False, WHY_CONFIRM_MAKER)
            else:
                may = allowed(actor, "confirm_batch")
                why = None if may else NOTICE_ROLE.format(role=role)
                action("confirm", "Confirm the batch", may, why)
                action("send_back", "Send it back", may, why)
        elif status == "staged":
            action("undo", "Undo", actor.name in (batch.maker, batch.checker), WHY_UNDO)
        elif status == "committing":
            asked = batch.stop_requested_at is not None
            may = allowed(actor, "stop_batch")
            action(
                "stop", "Stop", may and not asked, WHY_STOPPING if asked else NOTICE_ROLE.format(role=role)
            )
        # a compensation is started, staged and discarded on the command line until the audit screen (reading
        # 15): its page offers only what its second steward and Stop need
        if status in BEFORE_TRAY and batch.kind == "link" and (maker or actor.role == "coordinating_steward"):
            action("discard", "Discard this batch", can, role_why)
        return tuple(out)

    def rows(
        self, batch_id: str, *, actor: Actor, after: int | None = None, limit: int = capacity.BATCH_PAGE
    ) -> BatchRowPage:
        """One page of a prepared batch's rows (planned, and what became of them), by position after `after`:
        each record and its target, masked by role, the impact line, the before-and-after of the target with
        every row of the batch that joins it (or, for a compensation, without every record it takes back), how
        many other rows join the same target, and whether the target changed since it was planned.
        `view_tasks`."""
        self._allow(actor, "view_tasks")
        limit = capacity.require_limit(limit, capacity.COUNT_CAP)
        batch = self._get(batch_id)
        entity = batch.entity
        model = self._model(entity)
        every = self._items(batch_id, ("bulk",), _ROW_STATUSES)
        by_target: dict[str, list[SourceKey]] = {}
        for item in every:
            if item.target:
                by_target.setdefault(item.target, []).append(item.source)
        start = [i for i in every if after is None or i.position > after]
        page = start[:limit]
        more = len(start) > limit
        tasks = self.store.tasks_by_id([i.task_id for i in page]) if page else {}
        titles = self.rows_helper.titles([tasks[i.task_id] for i in page if i.task_id in tasks])
        targets = sorted({i.target for i in page if i.target})
        golden = self.store.golden(entity, targets) if targets else {}
        masked = set(model.personal_attributes())

        def text(name: str, value: Any) -> str | None:
            return self.privacy.masked_text(model, name, value)

        previews: dict[str, tuple[tuple[PreviewRow, ...], tuple[str, ...]]] = {}
        compensate = batch.kind == "compensate"
        for target in targets:
            now = golden[target].values if target in golden else {}
            pending = [i.source for i in every if i.target == target and i.status == "planned"]
            if compensate:
                later = self.lifecycle.values_with(entity, target, without=pending) if pending else dict(now)
            else:
                later = self.lifecycle.values_with(entity, target, pending) if pending else dict(now)
            previews[target] = display.preview_rows(model, now, later, text)
        rows: list[BatchRow] = []
        for item in page:
            target = item.target
            row = golden.get(target or "")
            preview, changed = previews.get(target or "", ((), ()))
            impact = (
                Impact(xrefs_removed=1, golden_changed=changed)
                if compensate
                else Impact(xrefs_added=1, golden_changed=changed)
            )
            rows.append(
                BatchRow(
                    task_id=item.task_id,
                    source=item.source.text(),
                    title=titles.get(item.task_id, item.source.text()),
                    target=target,
                    target_title=(
                        display.display_name(model, row.values, masked, target or "")
                        if row is not None
                        else target
                    ),
                    impact=impact,
                    preview=preview if item.status == "planned" else (),
                    joins=max(0, len(by_target.get(target or "", [])) - 1),
                    status=item.status,
                    reason=item.reason,
                    changed_since=(
                        item.status == "planned"
                        and row is not None
                        and item.target_version is not None
                        and row.row_version != item.target_version
                    ),
                    chunk_no=item.chunk_no,
                )
            )
        return BatchRowPage(rows=tuple(rows), after=page[-1].position if more and page else None)

    # ------------------------------------------------------------------ the signature backfill (reading 1)

    def backfill_signatures(self) -> int:
        """Gives every open review written before this story a signature: the best stored pair between its record
        and a member of its first named golden record, for a review a steward could link (`review_band`, or
        `breaker_demoted` with a golden record); `''` otherwise, or when no pair is found, so the scan never
        repeats. Idempotent. Run by `mdm init` and once when the workbench starts. Returns the reviews given
        one (`''` included)."""
        given = 0
        after: str | None = None
        while True:
            page = self.store.open_tasks_without_signature(after, capacity.ARRIVAL_BATCH)
            if not page:
                return given
            rows: list[tuple[str, str, int | None]] = []
            by_entity: dict[str, list[Task]] = {}
            for task in page:
                by_entity.setdefault(task.entity, []).append(task)
            for entity, tasks in by_entity.items():
                rows.extend(self._backfill_page(entity, tasks))
            given += self.store.set_task_signatures(rows)
            if len(page) < capacity.ARRIVAL_BATCH:
                return given
            after = page[-1].task_id

    def _backfill_page(self, entity: str, tasks: Sequence[Task]) -> list[tuple[str, str, int | None]]:
        groupable = [
            t
            for t in tasks
            if t.kind == "review" and t.reason in GROUPED_REASONS and t.master_ids and t.source is not None
        ]
        out: list[tuple[str, str, int | None]] = [(t.task_id, "", None) for t in tasks if t not in groupable]
        if not groupable:
            return out
        firsts = sorted({t.master_ids[0] for t in groupable})
        members = self.store.members(entity, firsts, capacity.MAX_MEMBERS_CHECKED)
        pairs = self.store.pairs_of(entity, sorted({t.source for t in groupable if t.source is not None}))
        for task in groupable:
            assert task.source is not None
            mine = (task.source.system, task.source.key)
            theirs = {(m.system, m.key) for m in members.get(task.master_ids[0], [])}
            best: dict[str, Any] | None = None
            for pair in pairs:
                left = (pair["left_system"], pair["left_key"])
                right = (pair["right_system"], pair["right_key"])
                other = right if left == mine else left if right == mine else None
                if other is None or other not in theirs or not pair.get("signature"):
                    continue
                if best is None or float(pair.get("score") or 0) > float(best.get("score") or 0):
                    best = pair
            if best is None:
                out.append((task.task_id, "", None))
            else:
                version = best.get("rule_version")
                out.append(
                    (task.task_id, str(best["signature"]), int(version) if version is not None else None)
                )
        return out


__all__ = ["BatchService", "ChunkPass", "GROUPED_REASONS"]
