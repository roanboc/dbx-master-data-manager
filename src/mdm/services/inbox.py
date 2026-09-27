"""The inbox: pages of open tasks, capped counts, the health strip, claims, snoozes and escalations.

Application service `ASVC8` (with `DecisionService`). A page is keyed by `(due time, task ID)`, never
by an offset; every count reads at most `capacity.COUNT_CAP` rows and shows "999+" at the cap; titles
are masked by the actor's role. Reads need `view_tasks`, everything else `work_tasks`. A refusal never
names another person: `claimed_by_another` carries the claim's expiry only.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Any

from mdm import capacity
from mdm.backend.store import SqlStore
from mdm.config import Settings
from mdm.models.authority import Actor
from mdm.models.canonical import iso, utcnow
from mdm.models.entity_model import EntityModel
from mdm.models.errors import Conflict, Forbidden, NotFound
from mdm.models.records import SourceKey
from mdm.models.tasks import TASK_KINDS, Task
from mdm.models.workbench import (
    ESCALATION_REASONS,
    SNOOZE_HOURS,
    TASK_VIEWS,
    Health,
    ServiceLevels,
    StagedRef,
    TaskPage,
    TaskQuery,
    TaskRow,
    ViewCounts,
)
from mdm.services import display
from mdm.services.authority import require
from mdm.services.privacy import PrivacyService
from mdm.services.registry import ModelRegistry
from mdm.services.support import token


def task_lock(task_id: str) -> str:
    """The tray lock a staged decision on a task holds."""
    return f"task:{task_id}"


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


class TaskRows:
    """Builds inbox rows for one actor: masked titles from keyed reads of the subjects, staged markers from
    the tray's locks, and score, band, suggestion and reason from the stored evidence (never scored again)."""

    def __init__(self, settings: Settings, store: SqlStore, registry: ModelRegistry) -> None:
        self.settings = settings
        self.store = store
        self.registry = registry

    def lapsed_before(self, now: datetime) -> datetime:
        """Claims taken before this have lapsed."""
        return now - timedelta(minutes=self.settings.claim_minutes)

    def claim_holder(self, task: Task, now: datetime) -> str | None:
        """The actor whose claim still runs, or None."""
        if task.claimed_by and task.claimed_at is not None and task.claimed_at >= self.lapsed_before(now):
            return task.claimed_by
        return None

    def _model(self, entity: str, cache: dict[str, EntityModel | None]) -> EntityModel | None:
        if entity not in cache:
            try:
                cache[entity] = self.registry.published(entity)
            except NotFound:
                cache[entity] = None
        return cache[entity]

    def titles(self, tasks: Sequence[Task]) -> dict[str, str]:
        """Task ID -> its subject's display name, personal attributes masked (decision 20)."""
        models: dict[str, EntityModel | None] = {}
        sources: dict[str, set[SourceKey]] = {}
        masters: dict[str, set[str]] = {}
        for task in tasks:
            if task.source is not None:
                sources.setdefault(task.entity, set()).add(task.source)
            else:
                masters.setdefault(task.entity, set()).update(task.master_ids)
        states = {
            (entity, source): state
            for entity, found in sources.items()
            if self._model(entity, models) is not None
            for source, state in self.store.source_states(entity, sorted(found)).items()
        }
        golden = {
            (entity, master): row
            for entity, found in masters.items()
            if self._model(entity, models) is not None
            for master, row in self.store.golden(entity, sorted(found)).items()
        }
        out: dict[str, str] = {}
        for task in tasks:
            model = self._model(task.entity, models)
            fallback = display.subject_text(task)
            if model is None:
                out[task.task_id] = fallback
                continue
            masked = set(model.personal_attributes())
            if task.source is not None:
                state = states.get((task.entity, task.source))
                values = state.values if state is not None else {}
                out[task.task_id] = display.display_name(model, values, masked, fallback)
            else:
                names = [
                    display.display_name(model, golden[(task.entity, m)].values, masked, m)
                    if (task.entity, m) in golden
                    else m
                    for m in task.master_ids
                ]
                out[task.task_id] = " · ".join(names) or fallback
        return out

    def staged(self, tasks: Sequence[Task], actor: Actor) -> dict[str, StagedRef]:
        """Task ID -> the staged decision on it, labelled from IDs and source keys."""
        if not tasks:
            return {}
        held = self.store.staged_by_locks([task_lock(t.task_id) for t in tasks])
        out: dict[str, StagedRef] = {}
        for task in tasks:
            entry = held.get(task_lock(task.task_id))
            if entry is not None:
                out[task.task_id] = StagedRef(
                    entry_id=entry.entry_id,
                    decision=entry.decision,
                    label=display.tray_label(entry.decision, entry.subject, entry.target),
                    deadline=entry.deadline,
                    mine=entry.actor == actor.name,
                )
        return out

    def rows(self, tasks: Sequence[Task], actor: Actor, now: datetime) -> list[TaskRow]:
        titles = self.titles(tasks)
        staged = self.staged(tasks, actor)
        claim = timedelta(minutes=self.settings.claim_minutes)
        out: list[TaskRow] = []
        for task in tasks:
            evidence = task.evidence or {}
            holder = self.claim_holder(task, now)
            band = evidence.get("band")
            out.append(
                TaskRow(
                    task_id=task.task_id,
                    entity=task.entity,
                    kind=task.kind,
                    kind_label=display.kind_label(task.kind),
                    title=titles.get(task.task_id, display.subject_text(task)),
                    subject=display.subject_text(task),
                    score=_float(evidence.get("score")),
                    band=band if isinstance(band, str) else None,
                    suggestion=display.suggestion_text(task),
                    reason=display.reason_chip(task),
                    due_at=task.due_at,
                    breaching=task.due_at is not None and task.due_at < now,
                    claimed_by=display.claimant_text(holder, actor),
                    claim_expires=task.claimed_at + claim if holder and task.claimed_at else None,
                    snoozed_until=task.snoozed_until
                    if task.snoozed_until is not None and task.snoozed_until > now
                    else None,
                    escalated=task.escalated_at is not None,
                    staged=staged.get(task.task_id),
                    kept_apart=task.reason == "cannot_link_conflict",
                )
            )
        return out


class InboxService:
    def __init__(
        self,
        settings: Settings,
        store: SqlStore,
        registry: ModelRegistry,
        privacy: PrivacyService,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        """Wires the service; reads nothing from the store (`mdm init` wires the hub before the schema)."""
        self.settings = settings
        self.store = store
        self.registry = registry
        self.privacy = privacy
        self.clock = clock
        self.rows = TaskRows(settings, store, registry)

    # ------------------------------------------------------------------ reads

    def _query(
        self, view: str, actor: Actor, entity: str | None, kind: str | None, now: datetime
    ) -> TaskQuery:
        if kind is not None and kind not in TASK_KINDS:
            raise NotFound("unknown_kind", kind=token(kind))
        if view == "mine":
            return TaskQuery(
                now,
                entity,
                kind,
                mine=actor.name,
                lapsed_before=self.rows.lapsed_before(now),
                snoozed=False,
            )
        if view == "team":
            return TaskQuery(now, entity, kind, snoozed=False)
        if view == "breaching":
            return TaskQuery(now, entity, kind, snoozed=None, breaching=True)
        if view == "snoozed":
            return TaskQuery(now, entity, kind, snoozed=True)
        if view == "escalated":
            return TaskQuery(now, entity, kind, snoozed=None, escalated=True)
        raise NotFound("unknown_view", view=token(view))

    def page(
        self,
        view: str = "mine",
        *,
        actor: Actor,
        entity: str | None = None,
        kind: str | None = None,
        after: tuple[str, str] | None = None,
        limit: int = capacity.INBOX_PAGE,
    ) -> TaskPage:
        """One page of the view (`TASK_VIEWS`), ordered by `(due time, task ID)` after the cursor `after`
        (ISO due time, task ID); titles masked, staged markers from the tray's locks, score, band,
        suggestion and reason from the stored evidence (never scored again). `view_tasks`."""
        require(actor, "view_tasks")
        limit = capacity.require_limit(limit, capacity.COUNT_CAP)
        now = self.clock()
        query = self._query(view, actor, entity, kind, now)
        cursor: tuple[datetime, str] | None = None
        if after is not None:
            try:
                cursor = (datetime.fromisoformat(after[0].replace("Z", "+00:00")), str(after[1]))
            except (TypeError, ValueError, IndexError):
                raise Forbidden("bad_cursor") from None
        tasks = self.store.task_page(query, cursor, limit + 1)
        more = len(tasks) > limit
        tasks = tasks[:limit]
        rows = self.rows.rows(tasks, actor, now)
        last = tasks[-1] if tasks else None
        following = (iso(last.due_at), last.task_id) if more and last is not None and last.due_at else None
        return TaskPage(rows=tuple(rows), after=following)

    def counts(self, *, actor: Actor, entity: str | None = None) -> ViewCounts:
        """Capped counts per view and per kind within the entity filter; nothing summed over the task
        table. `view_tasks`."""
        require(actor, "view_tasks")
        now = self.clock()
        cap = capacity.COUNT_CAP
        views = {
            view: self.store.task_count(self._query(view, actor, entity, None, now), cap)
            for view in TASK_VIEWS
        }
        kinds = {
            kind: self.store.task_count(TaskQuery(now, entity, kind, snoozed=None), cap)
            for kind in TASK_KINDS
        }
        mine = TaskQuery(
            now, entity, claimed_by=actor.name, lapsed_before=self.rows.lapsed_before(now), snoozed=False
        )
        return ViewCounts(views=views, kinds=kinds, claimed=self.store.task_count(mine, cap))

    def health(self, *, actor: Actor) -> Health:
        """The health strip: open, breaching and staged (capped), the last commit and the last arrival
        run's progress. `view_tasks`."""
        require(actor, "view_tasks")
        now = self.clock()
        cap = capacity.COUNT_CAP
        version = self.store.last_commit_version()
        commit_at = None
        if version:
            found = self.store.commits_by_version([version])
            commit_at = found[0].committed_at if found else None
        runs = self.store.jobs(1, "arrival")
        run = runs[0] if runs else None
        progress = run.get("progress") if run else None
        progress = progress if isinstance(progress, dict) else {}
        read = progress.get("read")
        settled = progress.get("settled")
        opened = progress.get("tasks")
        automatic = None
        if isinstance(settled, int) and settled > 0 and isinstance(opened, int):
            automatic = max(0.0, min(1.0, (settled - opened) / settled))
        return Health(
            open_tasks=self.store.task_count(TaskQuery(now, snoozed=None), cap),
            breaching=self.store.task_count(TaskQuery(now, snoozed=None, breaching=True), cap),
            staged=self.store.staged_count(cap),
            last_commit_version=version,
            last_commit_at=commit_at,
            last_arrival_at=(run.get("finished_at") or run.get("heartbeat_at")) if run else None,
            arrival_read=read if isinstance(read, int) else None,
            arrival_tasks=opened if isinstance(opened, int) else None,
            arrival_automatic=automatic,
        )

    def task(self, task_id: str) -> Task:
        """The task, in any status; `NotFound(unknown_task)`."""
        found = self.store.tasks_by_id([task_id]).get(task_id)
        if found is None:
            raise NotFound("unknown_task", task_id=token(task_id))
        return found

    def row(self, task_id: str, *, actor: Actor) -> TaskRow:
        """One task as an inbox row, for a grid update after a decision or a settlement; `NotFound
        (unknown_task)`, and `Conflict(task_closed)` once it has been decided. `view_tasks`."""
        require(actor, "view_tasks")
        task = self.task(task_id)
        if task.status != "open":
            raise Conflict([token(task_id)], code="task_closed")
        return self.rows.rows([task], actor, self.clock())[0]

    # ------------------------------------------------------------------ claims, snoozes, escalations

    def _open(self, task_id: str) -> Task:
        task = self.task(task_id)
        if task.status != "open":
            raise Conflict([token(task_id)], code="task_closed")
        return task

    def _staged(self, task_id: str, actor: Actor) -> Conflict | None:
        """`Conflict(already_staged, mine=…)` while a decision on the task waits in the tray: the flush
        would close the task under a claim, a snooze or an escalation taken meanwhile."""
        held = self.store.staged_by_locks([task_lock(task_id)]).get(task_lock(task_id))
        if held is None:
            return None
        return Conflict([token(task_id)], code="already_staged", mine=held.actor == actor.name)

    def _refused(self, task_id: str, now: datetime, actor: Actor) -> Conflict:
        """Why a conditional write on an open task changed nothing: it closed, a decision on it waits in the
        tray, or another claim runs."""
        task = self.task(task_id)
        if task.status != "open":
            return Conflict([token(task_id)], code="task_closed")
        staged = self._staged(task_id, actor)
        if staged is not None:
            return staged
        until = (task.claimed_at or now) + timedelta(minutes=self.settings.claim_minutes)
        return Conflict([token(task_id)], code="claimed_by_another", until=iso(until))

    def claim(self, task_id: str, *, actor: Actor, staging: bool = False) -> datetime:
        """Claims the task for `claim_minutes` and returns the claim's expiry; wakes a snoozed task.
        `Conflict(claimed_by_another, until=…)` while another actor's claim runs, `Conflict(task_closed)`,
        and `Conflict(already_staged)` while a decision on it waits in the tray (unless `staging`: the tray
        claims just before it stages). `work_tasks`."""
        require(actor, "work_tasks")
        self._open(task_id)
        if not staging:
            staged = self._staged(task_id, actor)
            if staged is not None:
                raise staged
        now = self.clock()
        if not self.store.claim_task(task_id, actor.name, now, self.rows.lapsed_before(now), staging=staging):
            raise self._refused(task_id, now, actor)
        return now + timedelta(minutes=self.settings.claim_minutes)

    def release(self, task_id: str, *, actor: Actor) -> None:
        """Releases the actor's own claim (nothing when the actor holds none). `work_tasks`."""
        require(actor, "work_tasks")
        self.task(task_id)
        self.store.release_task(task_id, actor.name)

    def snooze(self, task_id: str, *, actor: Actor, hours: int) -> datetime:
        """Hides the task from My queue for `hours` (one of `SNOOZE_HOURS`, else `Forbidden(bad_snooze)`)
        and releases the claim; the due time stays. Returns the time it wakes. `Conflict(already_staged)` while a
        decision on it waits in the tray. `work_tasks`."""
        require(actor, "work_tasks")
        if isinstance(hours, bool) or hours not in SNOOZE_HOURS:
            raise Forbidden("bad_snooze")
        self._open(task_id)
        staged = self._staged(task_id, actor)
        if staged is not None:
            raise staged
        now = self.clock()
        until = now + timedelta(hours=hours)
        if not self.store.snooze_task(task_id, actor.name, until, self.rows.lapsed_before(now)):
            raise self._refused(task_id, now, actor)
        return until

    def escalate(self, task_id: str, *, actor: Actor, reason: str) -> None:
        """Marks the task escalated with a code of `ESCALATION_REASONS` (else `Forbidden(bad_escalation)`)
        and releases the claim. `Conflict(already_staged)` while a decision on it waits in the tray.
        `work_tasks`."""
        require(actor, "work_tasks")
        if reason not in ESCALATION_REASONS:
            raise Forbidden("bad_escalation")
        self._open(task_id)
        staged = self._staged(task_id, actor)
        if staged is not None:
            raise staged
        now = self.clock()
        if not self.store.escalate_task(task_id, actor.name, reason, now, self.rows.lapsed_before(now)):
            raise self._refused(task_id, now, actor)

    def backfill_due_times(self) -> int:
        """Gives every open task without a due time one, from the service level of its kind; idempotent.
        Returns how many it gave. Run by `mdm init` and once when the workbench starts."""
        levels = ServiceLevels(self.settings.sla_hours)
        given = 0
        after: str | None = None
        while True:
            page = self.store.open_tasks_without_due(after, capacity.READ_PAGE)
            if not page:
                return given
            given += self.store.set_due_times([(t.task_id, levels.due(t.kind, t.created_at)) for t in page])
            if len(page) < capacity.READ_PAGE:
                return given
            after = page[-1].task_id
