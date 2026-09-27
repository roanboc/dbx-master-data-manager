"""The undo tray: a steward's decision waits here, server-side, until its window passes.

Application service `ASVC9` (decision 19). `stage` checks the decision, claims the task and keeps the
entry with its locks for `Settings.undo_seconds`; `undo` takes it back while it waits; `flush` commits
every entry whose window has passed, each in its own transaction through `DecisionService.execute`, and
settles it `committed` or `failed` with an outcome code. `TrayWorker` runs `flush` in the workbench's
process on a local store; on the platform a job runs `mdm tray flush`.
"""

from __future__ import annotations

import logging
import secrets
import threading
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta

from mdm import capacity
from mdm.backend.store import SqlStore
from mdm.config import Settings
from mdm.models.authority import Actor
from mdm.models.canonical import utcnow
from mdm.models.errors import Conflict, Forbidden, MdmError, NotFound
from mdm.models.records import SourceKey
from mdm.models.workbench import FlushReport, TrayEntry, TrayView
from mdm.services import display
from mdm.services.arrival import ArrivalService
from mdm.services.authority import require
from mdm.services.decisions import DecisionService
from mdm.services.inbox import InboxService, task_lock
from mdm.services.support import token

logger = logging.getLogger(__name__)


class TrayService:
    def __init__(
        self,
        settings: Settings,
        store: SqlStore,
        decisions: DecisionService,
        inbox: InboxService,
        arrival: ArrivalService,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        """Wires the service; reads nothing from the store (`mdm init` wires the hub before the schema)."""
        self.settings = settings
        self.store = store
        self.decisions = decisions
        self.inbox = inbox
        self.arrival = arrival
        self.clock = clock

    # ------------------------------------------------------------------ staging and undoing

    def stage(
        self,
        task_id: str,
        decision: str,
        *,
        actor: Actor,
        target: str | None = None,
        seen_event: str | None = None,
        seen_task: str | None = None,
    ) -> TrayEntry:
        """Checks the decision against the case the steward saw (`DecisionService.check`: `seen_event` for a
        task with a source record, `seen_task` for one without), claims the task and keeps the entry, staged,
        until its deadline; `Conflict(already_staged, mine=…)` when another entry holds one of its locks,
        and then a claim this call took is released again. `work_tasks`."""
        require(actor, "work_tasks")
        staging = self.decisions.check(
            task_id, decision, actor=actor, target=target, seen_event=seen_event, seen_task=seen_task
        )
        before = self.inbox.task(task_id)
        held_before = self.inbox.rows.claim_holder(before, self.clock()) == actor.name
        self.inbox.claim(task_id, actor=actor, staging=True)
        now = self.clock()
        entry = TrayEntry(
            entry_id="TR-" + secrets.token_hex(10),
            task_id=task_id,
            entity=staging.entity,
            decision=staging.decision,
            target=staging.target,
            subject=dict(staging.subject),
            signature=staging.signature,
            actor=actor.name,
            actor_role=actor.role,
            persona=actor.persona,
            event_id=staging.event_id,
            planning_version=self.store.last_commit_version(),
            staged_at=now,
            deadline=now + timedelta(seconds=self.settings.undo_seconds),
            status="staged",
        )
        try:
            self.store.stage_tray(entry, staging.locks)
        except Exception:
            if not held_before:
                self.store.release_task(task_id, actor.name)  # the claim came with the stage that failed
            raise
        return entry

    def _entry(self, entry_id: str) -> TrayEntry:
        found = self.store.tray_entries([entry_id]).get(entry_id)
        if found is None:
            raise NotFound("unknown_entry", entry_id=token(entry_id))
        return found

    def undo(self, entry_id: str, *, actor: Actor) -> TrayEntry:
        """Takes back the actor's own staged entry (`Forbidden(not_yours)` for another's);
        `Conflict(already_settled, status=…, version=…)` once it committed, failed or was undone. The claim
        stays with the steward."""
        require(actor, "work_tasks")
        entry = self._entry(entry_id)
        if entry.actor != actor.name:
            raise Forbidden("not_yours")
        now = self.clock()
        if not self.store.settle_tray(entry_id, "undone", change_set_id=None, outcome="undone", at=now):
            settled = self._entry(entry_id)
            raise Conflict(
                [token(entry_id)],
                code="already_settled",
                status=settled.status,
                version=settled.commit_version,
            )
        return self._entry(entry_id)

    def undo_for_task(self, task_id: str, *, actor: Actor) -> TrayEntry | None:
        """Undoes the actor's staged entry on this task; None when there is none."""
        require(actor, "work_tasks")
        held = self.store.staged_by_locks([task_lock(task_id)]).get(task_lock(task_id))
        if held is None or held.actor != actor.name:
            return None
        return self.undo(held.entry_id, actor=actor)

    def undo_last(self, *, actor: Actor) -> TrayEntry | None:
        """Undoes the actor's most recently staged entry that still waits; None when there is none."""
        require(actor, "work_tasks")
        now = self.clock()
        waiting = [
            e for e in self.store.tray_of_actor(actor.name, now, capacity.TRAY_SHOWN) if e.status == "staged"
        ]
        if not waiting:
            return None
        return self.undo(waiting[0].entry_id, actor=actor)

    def entries(self, *, actor: Actor, recent_minutes: int = 10) -> tuple[TrayView, ...]:
        """The actor's own entries still staged or settled in the last `recent_minutes`, newest first, at
        most `TRAY_SHOWN`; labels built from IDs and source keys only."""
        since = self.clock() - timedelta(minutes=max(recent_minutes, 0))
        return tuple(
            TrayView(
                entry_id=e.entry_id,
                task_id=e.task_id,
                decision=e.decision,
                label=display.tray_label(e.decision, e.subject, e.target),
                deadline=e.deadline,
                status=e.status,
                outcome=e.outcome,
                commit_version=e.commit_version,
                settled_at=e.settled_at,
            )
            for e in self.store.tray_of_actor(actor.name, since, capacity.TRAY_SHOWN)
        )

    # ------------------------------------------------------------------ the flush

    def flush(self, *, started_by: Actor | None = None, limit: int = capacity.FLUSH_BATCH) -> FlushReport:
        """Commits the entries whose window has passed, in deadline order, each on its own; settles each
        `committed` or `failed` with its outcome, and asks arrival to settle the records a "not a match"
        queued again. `FlushReport(skipped_busy=True)` when another flush holds the tray. `started_by`
        (the command line) needs `flush_tray`; the worker passes none."""
        if started_by is not None:
            require(started_by, "flush_tray")
        limit = capacity.require_limit(limit, capacity.READ_PAGE)
        with self.store.exclusive_lease("tray") as held:
            if not held:
                return FlushReport(skipped_busy=True)
            now = self.clock()
            outcomes: Counter[str] = Counter()
            requeued = 0
            for entry in self.store.due_tray(now, limit):
                outcome, queued = self._flush_one(entry, now)
                if outcome is not None:
                    outcomes[outcome] += 1
                if outcome == "committed" and queued:
                    requeued += len(queued)
                    self._settle_records(entry.entity, queued)
            committed = outcomes.get("committed", 0)
            return FlushReport(
                committed=committed,
                failed=sum(outcomes.values()) - committed,
                requeued=requeued,
                outcomes=dict(sorted(outcomes.items())),
            )

    def _flush_one(self, entry: TrayEntry, now: datetime) -> tuple[str | None, tuple[SourceKey, ...]]:
        """One entry: (its outcome, the records queued again). None when it was undone meanwhile, or when an
        unexpected failure leaves it staged for the next pass."""
        attempts = self.store.bump_tray_attempts(entry.entry_id)
        if attempts == 0:
            return None, ()  # undone (or settled) meanwhile
        commit = self.decisions.execute  # the commit path, under the steward's authority
        try:
            try:
                _, queued = commit(entry, now=now)
            except Conflict as error:
                if error.code not in ("stale_row", "stale_link"):
                    raise
                _, queued = commit(entry, now=now)  # once more, from a fresh plan
        except Conflict as error:
            code = "target_changed" if error.code == "not_active" and entry.decision == "link" else error.code
            return self._failed(entry, code, now), ()
        except MdmError as error:
            return self._failed(entry, error.code, now), ()
        except Exception as error:  # noqa: BLE001 - one entry must never stop the flush
            logger.warning("tray_entry_failed type=%s attempts=%d", type(error).__name__, attempts)
            if attempts >= capacity.FLUSH_ATTEMPTS:
                return self._failed(entry, "internal", now), ()
            return None, ()
        settled = self.store.tray_entries([entry.entry_id]).get(entry.entry_id)
        if settled is not None and settled.status == "staged":
            # a decision that committed without its settlement must never be flushed twice
            return self._failed(entry, "not_settled", now), ()
        return "committed", queued

    def _failed(self, entry: TrayEntry, code: str, now: datetime) -> str | None:
        outcome = token(code)
        if self.store.fail_tray(entry.entry_id, outcome, now, entry.task_id, entry.actor):
            return outcome
        return None  # undone meanwhile: nothing to do

    def _settle_records(self, entity: str, sources: tuple[SourceKey, ...]) -> None:
        """Arrival settles the records a "not a match" queued again; any failure leaves them queued for the
        next arrival run, and never touches the committed entry or its claim."""
        try:
            self.arrival.settle_records(entity, sources)
        except Exception as error:  # noqa: BLE001 - the decision has committed; arrival retries later
            logger.warning("tray_settle_records_failed type=%s", type(error).__name__)


class TrayWorker:
    """A daemon thread that calls tray.flush() every `interval` seconds until stop().

    Written whole by the skeleton: it catches everything a pass raises, logs the type only (a message could
    quote a value), and never stops the workbench."""

    def __init__(self, tray: TrayService, interval: float = 2.0) -> None:
        self.tray = tray
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Starts the thread (once; a second call does nothing while it runs)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="mdm-tray-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Asks the thread to stop and waits up to `timeout` seconds for the pass in hand to end."""
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.tray.flush()
            except Exception as error:  # noqa: BLE001 - the worker outlives any one pass
                logger.warning("tray_flush_failed type=%s", type(error).__name__)
