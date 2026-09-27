"""A task's case, its reveal, the check at staging, and the decision's commit at flush.

Application service `ASVC8` (with `InboxService`). `case` builds what the decide pane shows: the
record and its candidates as columns, each candidate's match-weight waterfall, what would flip it, and
the golden values after linking; masked by role, and cached per task version, record event, commit
version, published rule versions and role. `reveal` shows values in clear once, logged per attribute
with a reason code, and never touches the cache. `check` fails fast when a decision cannot be staged;
`execute` commits a staged decision through the commit path, always audited, with the checks that
matter inside the commit's own transaction (decision 19).
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from mdm import capacity
from mdm.backend.store import SqlStore
from mdm.config import Settings
from mdm.models.authority import ROLE_LABELS, Actor, allowed
from mdm.models.canonical import iso, utcnow
from mdm.models.changes import (
    ChangeItem,
    CommitResult,
    EndRelationship,
    LinkSource,
    UpdateGolden,
    UpsertRelationship,
    WorkWrites,
)
from mdm.models.entity_model import EntityModel
from mdm.models.errors import Conflict, Forbidden, MdmError, NotFound, PlatformRefused
from mdm.models.match import Band, GoldenCandidate
from mdm.models.records import GoldenRow, SourceKey, SourceState
from mdm.models.safety import safe_detail
from mdm.models.tasks import Task
from mdm.models.wording import agreement_of as _agreement_of
from mdm.models.wording import (
    comparison_name,
    flip_sentence,
    hard_rule_sentence,
    level_words,
    plural_name,
)
from mdm.models.workbench import (
    CASE_SHAPES,
    DECISIONS,
    REVEAL_REASONS,
    Action,
    Candidate,
    CompareRow,
    Impact,
    MatchLabel,
    Preview,
    PreviewRow,
    Revealed,
    Staging,
    TaskCase,
    TaskRow,
    TrayEntry,
    TraySettlement,
    WaterfallStep,
)
from mdm.services import display
from mdm.services.authority import require
from mdm.services.inbox import TaskRows, task_lock
from mdm.services.lifecycle import LifecycleService
from mdm.services.matching import MatchService, strong_ids_of
from mdm.services.privacy import PrivacyService
from mdm.services.registry import ModelRegistry
from mdm.services.support import source_token, token

logger = logging.getLogger(__name__)

#: held tasks whose update the steward approves or rejects
HELD_UPDATE_REASONS = frozenset(
    {
        "update_held",
        "critical_update_held",
        "end_date_held",
        "no_longer_auto",
        "auto_elsewhere",
        "member_conflict",
    }
)
#: held tasks for a new record: creating its golden record on screen comes with story 3.7
HELD_NEW_REASONS = frozenset({"new_held", "authored_style"})
#: held updates whose record may belong elsewhere: detaching it comes with story 3.6
_MAY_BELONG_ELSEWHERE = frozenset({"no_longer_auto", "auto_elsewhere", "member_conflict"})
#: the decisions each shape offers
OFFERED: Mapping[str, tuple[str, ...]] = {
    "source": ("link", "not_a_match"),
    "golden_pair": ("keep_apart",),
    "held_update": ("approve_update", "reject_update"),
    "held_new": (),
    "golden": ("keep_orphan",),
    "information": (),
}
_KEYS = {
    "link": "L",
    "not_a_match": "N",
    "keep_apart": "N",
    "approve_update": "A",
    "reject_update": "R",
    "keep_orphan": "A",
    "claim": "C",
    "snooze": "S",
    "escalate": "E",
    "undo": "U",
}
_LEVEL_SHORT = {"exact": "the same", "else": "different", "null": "missing"}
_MARKS = {"exact": "=", "else": "≠", "null": "∅"}

NOTICE_MERGE = "Merging needs a second steward to check it; that is not on screen yet."
NOTICE_RETIRE = "Retiring a golden record needs a second steward to check it; that is not on screen yet."
NOTICE_DETACH = "If this record belongs to another golden record, detaching it is not on screen yet."
NOTICE_CREATE = "Creating a golden record from a held arrival is not on screen yet."
NOTICE_INFORMATION = (
    "Nothing here is decided on screen yet: fixing it at its source, or on screen in a later release."
)
NOTICE_GONE = (
    "The source record is gone, so there is nothing to decide here; the task closes when arrival settles it."
)
NOTICE_NO_CANDIDATE = "No golden record scores against this record now. Not a match hands it back to arrival."


def blocked_sentence(blocked_by: str) -> str:
    """ "A cannot-link rule keeps them apart: the registered IDs differ." for `cannot_link:registered_id`."""
    kind, _, attribute = blocked_by.partition(":")
    if kind == "cannot_link" and attribute:
        return f"A cannot-link rule keeps them apart: the {plural_name(comparison_name(attribute))} differ."
    return "A rule keeps them apart."


def why_blocked(blocked_by: str | None) -> str | None:
    """Why the link to a blocked candidate is unavailable: "A cannot-link rule blocks this link: the
    registered IDs differ."; None when nothing blocks it."""
    if not blocked_by:
        return None
    kind, _, attribute = blocked_by.partition(":")
    if kind == "cannot_link" and attribute:
        return f"A cannot-link rule blocks this link: the {plural_name(comparison_name(attribute))} differ."
    return "A rule blocks this link."


def shape_of(task: Task) -> str:
    """The case's shape from the task's kind, reason and subject (`CASE_SHAPES`)."""
    if task.kind in ("review", "possible_duplicate"):
        if task.source is not None:
            return "source"
        if len(task.master_ids) == 2:
            return "golden_pair"
        return "information"
    if task.kind == "held" and task.source is not None:
        if task.reason in HELD_NEW_REASONS:
            return "held_new"
        if task.reason in HELD_UPDATE_REASONS:
            return "held_update"
        return "information"
    if task.kind == "orphan" and task.master_ids:
        return "golden"
    return "information"


@dataclass(frozen=True, slots=True)
class _Column:
    """One column of the compare table: its header, the record it shows, and that record's values."""

    header: str
    ref: str  # a source key or a master ID: what a reveal logs
    values: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class _Prepared:
    """The costly, cached part of a case: everything but the row, the claim, the staged marker and whether
    each action is enabled now."""

    shape: str
    reason_text: str
    columns: tuple[str, ...]
    compare: tuple[CompareRow, ...]
    candidates: tuple[Candidate, ...]
    default_candidate: str | None
    close_call: bool
    preview: Preview | None
    offered: tuple[Action, ...]  # the decisions, enabled; the per-call part decides
    notice: str | None
    masked: bool
    revealable: tuple[str, ...]
    event_id: str | None
    held: bool  # held_update: the record is still linked and held
    master_ids: tuple[str, ...]  # the golden records a golden shape names


class DecisionService:
    def __init__(
        self,
        settings: Settings,
        store: SqlStore,
        registry: ModelRegistry,
        matching: MatchService,
        lifecycle: LifecycleService,
        privacy: PrivacyService,
        clock: Callable[[], datetime] = utcnow,
        cache_size: int = capacity.CASE_CACHE,
    ) -> None:
        """Wires the service; reads nothing from the store (`mdm init` wires the hub before the schema)."""
        self.settings = settings
        self.store = store
        self.registry = registry
        self.matching = matching
        self.lifecycle = lifecycle
        self.privacy = privacy
        self.clock = clock
        self.cache_size = cache_size
        self.rows = TaskRows(settings, store, registry)
        self._cache: OrderedDict[tuple, _Prepared] = OrderedDict()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ the case

    def case(self, task_id: str, *, actor: Actor) -> TaskCase:
        """The task's case for the decide pane, masked by the actor's role: its shape, columns, compare
        rows, candidates (at most `CANDIDATES_SHOWN`, best first, each with its waterfall, flip sentences
        and preview), whether it is a close call, and the actions with `enabled` and `why_not`.
        `NotFound(unknown_task)`, `Conflict(task_closed)`. `view_tasks`."""
        require(actor, "view_tasks")
        task = self._task(task_id)
        now = self.clock()
        prepared = self._prepared(task, actor)
        row = self.rows.rows([task], actor, now)[0]
        return self._assemble(task, prepared, row, actor, now)

    def prefetch(self, task_id: str, *, actor: Actor) -> None:
        """Prepares the case of the next row while the steward reads; never raises (logs the type)."""
        try:
            self.case(task_id, actor=actor)
        except Exception as error:  # noqa: BLE001 - a prefetch never disturbs the steward
            logger.info("case_prefetch_failed type=%s", type(error).__name__)

    def _task(self, task_id: str) -> Task:
        task = self.store.tasks_by_id([task_id]).get(task_id)
        if task is None:
            raise NotFound("unknown_task", task_id=token(task_id))
        if task.status != "open":
            raise Conflict([token(task_id)], code="task_closed")
        return task

    def _model(self, entity: str) -> EntityModel:
        return self.registry.published(entity)

    def _state(self, task: Task) -> SourceState | None:
        if task.source is None:
            return None
        return self.store.source_states(task.entity, [task.source]).get(task.source)

    def _cache_key(self, task: Task, actor: Actor, model: EntityModel, state: SourceState | None) -> tuple:
        return (
            task.task_id,
            task.updated_at,
            state.event_id if state is not None else None,
            state.held if state is not None else None,
            self.store.last_commit_version(),
            (model.version, model.match.version, model.survivorship.version, model.validation.version),
            actor.role,
        )

    def _prepared(self, task: Task, actor: Actor) -> _Prepared:
        model = self._model(task.entity)
        state = self._state(task)
        key = self._cache_key(task, actor, model, state)
        with self._lock:
            found = self._cache.get(key)
            if found is not None:
                self._cache.move_to_end(key)
                return found
        prepared = self._prepare(task, actor, model, state)
        with self._lock:
            self._cache[key] = prepared
            self._cache.move_to_end(key)
            while len(self._cache) > max(self.cache_size, 1):
                self._cache.popitem(last=False)
        return prepared

    def cache_size_now(self) -> int:
        """How many prepared cases the cache holds (tests)."""
        with self._lock:
            return len(self._cache)

    # ------------------------------------------------------------------ preparing a case

    def _prepare(self, task: Task, actor: Actor, model: EntityModel, state: SourceState | None) -> _Prepared:
        shape = shape_of(task)
        if task.source is not None and (state is None or state.status != "active"):
            shape = "information"
        columns, candidates_found = self._columns(task, model, state, shape)
        candidates = self._candidates(task, model, state, candidates_found, shape)
        pair_levels: Mapping[str, str] | None = None
        if shape == "golden_pair":
            paired = self._pair(task, model)
            if paired is not None:
                candidates, pair_levels = [paired[0]], paired[1]
        compare = self._compare(
            model,
            columns,
            candidates_found,
            shape,
            state,
            self._masked_text(model),
            pair_levels=pair_levels,
            kept_apart=candidates[0].blocked_rule if shape == "golden_pair" and candidates else None,
        )
        # a candidate a cannot-link rule blocks is shown, never linked: it is neither the default nor half
        # of a close call
        open_ones = [c for c in candidates if c.blocked_by is None]
        close = (
            shape == "source"
            and len(open_ones) >= 2
            and open_ones[0].score - open_ones[1].score <= self.settings.close_call_points
        )
        default = open_ones[0].master_id if open_ones and not close and shape == "source" else None
        preview = self._shape_preview(task, model, state, shape, columns)
        notice = self._notice(task, shape, state, candidates)
        personal = set(model.personal_attributes())
        masked = any(column.values.get(name) is not None for column in columns for name in personal)
        revealable = (
            tuple(
                a.name
                for a in model.column_attributes()
                if a.personal and any(column.values.get(a.name) is not None for column in columns)
            )
            if allowed(actor, "reveal")
            else ()
        )
        return _Prepared(
            shape=shape,
            reason_text=display.reason_sentence(task),
            columns=tuple(c.header for c in columns),
            compare=compare,
            candidates=tuple(candidates),
            default_candidate=default,
            close_call=close,
            preview=preview,
            offered=self._offered(task, shape, candidates),
            notice=notice,
            masked=masked,
            revealable=revealable,
            event_id=state.event_id if state is not None and task.source is not None else None,
            held=bool(state is not None and state.held),
            master_ids=tuple(task.master_ids),
        )

    def _masked_text(self, model: EntityModel) -> Callable[[str, Any], str | None]:
        return lambda name, value: self.privacy.masked_text(model, name, value)

    def _golden_candidates(self, task: Task, model: EntityModel, state: SourceState) -> list[GoldenCandidate]:
        """The record's golden candidates, best first, at most `CANDIDATES_SHOWN`: the task's named golden
        records and the best others, less those the record has a "not a match" label for."""
        entity = task.entity
        rules = self.registry.compiled(entity)
        search = self.matching.search(model, rules, [state], explain_all=True)
        pairs = {state.source: [p for p in search.pairs.get(state.source, []) if p.right != state.source]}
        labels = self.store.labels_for(entity, [state.source.text()])
        named = sorted({lab.right_ref for lab in labels if lab.label == "not_a_match"})
        # a golden record declined and since merged: its survivor is declined too, as arrival reads it
        survivors = self.store.resolve_retired(named) if named else {}
        declined = frozenset(named) | frozenset(survivors.values())
        golden = self.matching.golden_candidates(
            entity,
            pairs,
            {state.source: strong_ids_of(model, state.ids)},
            states=search.states,
            declined={state.source: declined} if declined else None,
        ).get(state.source, [])
        named_ids = (task.evidence or {}).get("master_ids") or task.master_ids
        named = {m for m in named_ids if isinstance(m, str)}
        shown = capacity.CANDIDATES_SHOWN
        chosen = [g for g in golden if g.master_id in named][:shown]
        # the best others, when they score at least in the review band: a distinct record is no candidate
        others = [g for g in golden if g.master_id not in named and g.best.explanation.band != Band.DISTINCT]
        chosen += others[: shown - len(chosen)]
        chosen.sort(key=lambda g: (-g.best.explanation.score, g.master_id))
        return chosen

    def _columns(
        self, task: Task, model: EntityModel, state: SourceState | None, shape: str
    ) -> tuple[list[_Column], list[GoldenCandidate]]:
        """The compare table's columns (the subject first) and, for a source subject, its golden candidates."""
        entity = task.entity
        if shape in ("source", "held_new") and state is not None:
            found = self._golden_candidates(task, model, state)
            golden = self.store.golden(entity, [g.master_id for g in found])
            columns = [_Column(f"Arriving · {state.source.text()}", state.source.text(), state.values)]
            for index, g in enumerate(found, start=1):
                row = golden.get(g.master_id)
                columns.append(
                    _Column(
                        f"{index} · {g.master_id}",
                        g.master_id,
                        row.values if row is not None else {},
                    )
                )
            return columns, found
        if shape == "held_update" and state is not None:
            master = self.store.xrefs_for_sources(entity, [state.source]).get(state.source)
            golden = self.store.golden(entity, [master]).get(master) if master else None
            source = state.source.text()
            columns = [
                _Column(f"Approved · {source}", source, state.approved_values or {}),
                _Column(f"New · {source}", source, state.values),
            ]
            if master is not None:
                columns.append(
                    _Column(f"{master} · golden record now", master, golden.values if golden else {})
                )
            return columns, []
        if shape in ("golden_pair", "golden") or (task.source is None and task.master_ids):
            golden = self.store.golden(entity, list(task.master_ids))
            return [
                _Column(f"{m} · golden record", m, golden[m].values if m in golden else {})
                for m in task.master_ids
            ], []
        if state is not None:
            return [_Column(f"Record · {state.source.text()}", state.source.text(), state.values)], []
        return [], []

    def _candidate(
        self,
        model: EntityModel,
        rules: Any,
        index: int,
        master_id: str,
        row: GoldenRow | None,
        explanation: Any,
        member: str,
        *,
        blocked_rule: str | None,
        preview: Preview | None,
    ) -> Candidate:
        """One candidate from the engine's explanation: its waterfall steps, what would flip it (with the
        weight each change adds), a hard or blocking rule in words, and the preview."""
        masked = set(model.personal_attributes())
        edges = (model.match.bands.upper, model.match.bands.lower)
        steps = [WaterfallStep("Prior", None, "", explanation.prior, 0.0, explanation.prior)]
        running = explanation.prior
        for contribution in explanation.contributions:
            start, running = running, running + contribution.weight
            mark = _MARKS.get(contribution.label, "≈")
            steps.append(
                WaterfallStep(
                    label=f"{display.attribute_label(model, contribution.comparison)} {mark}",
                    comparison=contribution.comparison,
                    level_words=_LEVEL_SHORT.get(contribution.label) or level_words(contribution.label),
                    weight=contribution.weight,
                    start=start,
                    end=running,
                )
            )
        return Candidate(
            index=index,
            master_id=master_id,
            title=display.display_name(model, row.values, masked, master_id) if row else master_id,
            score=explanation.score,
            band=explanation.band.value,
            member=member,
            steps=tuple(steps),
            total=explanation.weight,
            thresholds=(rules.lower_weight, rules.upper_weight),
            flip=tuple(
                flip_sentence(
                    c,
                    edges,
                    weight=self._flip_weight(rules, c),
                    from_label=self._level_label(rules, c.comparison, c.from_level),
                )
                for c in explanation.counterfactuals
            ),
            hard_rule=hard_rule_sentence(explanation.hard_rule) if explanation.hard_rule else None,
            blocked_by=blocked_sentence(blocked_rule) if blocked_rule else None,
            signature=explanation.signature,
            rule_version=explanation.rule_version,
            preview=preview,
            blocked_rule=blocked_rule,
            what_if=tuple(
                (c.comparison, round(self._flip_weight(rules, c), 6)) for c in explanation.counterfactuals
            ),
        )

    def _candidates(
        self,
        task: Task,
        model: EntityModel,
        state: SourceState | None,
        found: Sequence[GoldenCandidate],
        shape: str,
    ) -> list[Candidate]:
        if not found or state is None:
            return []
        rules = self.registry.compiled(task.entity)
        golden = self.store.golden(task.entity, [g.master_id for g in found])
        out: list[Candidate] = []
        for index, g in enumerate(found, start=1):
            row = golden.get(g.master_id)
            preview = self._link_preview(task, model, state, g.master_id, row) if shape == "source" else None
            out.append(
                self._candidate(
                    model,
                    rules,
                    index,
                    g.master_id,
                    row,
                    g.best.explanation,
                    g.best.right.text(),
                    blocked_rule=g.blocked_by,
                    preview=preview,
                )
            )
        return out

    def _pair(self, task: Task, model: EntityModel) -> tuple[Candidate, Mapping[str, str]] | None:
        """For two golden records: the second as the candidate of the first, explained by the pair of
        source records that raised the task (its evidence), with the rule that keeps them apart; and the
        comparison levels of that pair, for the compare table. None when the evidence names no pair."""
        if len(task.master_ids) != 2:
            return None
        named = [s for s in ((task.evidence or {}).get("sources") or []) if isinstance(s, str)][:2]
        try:
            keys = [SourceKey.from_text(s) for s in named]
        except ValueError:
            return None
        states = self.store.source_states(task.entity, keys) if len(keys) == 2 else {}
        if len(states) != 2:
            return None
        rules = self.registry.compiled(task.entity)
        left, right = (states[k] for k in keys)
        explanation = self.matching.explain_pair(task.entity, left, right, rules)
        first, second = task.master_ids
        owners = self.store.xrefs_for_sources(task.entity, keys)
        member = next((k.text() for k in keys if owners.get(k) == second), keys[1].text())
        row = self.store.golden(task.entity, [second]).get(second)
        blocked = self.matching.masters_conflict(task.entity, first, second)
        candidate = self._candidate(
            model, rules, 1, second, row, explanation, member, blocked_rule=blocked, preview=None
        )
        return candidate, {c.comparison: c.label for c in explanation.contributions}

    @staticmethod
    def _flip_weight(rules: Any, counterfactual: Any) -> float:
        """The weight a counterfactual's change adds (negative: takes away), from the compiled rules."""
        try:
            index = rules.names.index(counterfactual.comparison)
            table = rules.weight_tables[index]
            before = table[counterfactual.from_level] if counterfactual.from_level >= 0 else 0.0
            return float(table[counterfactual.to_level] - before)
        except (ValueError, IndexError, AttributeError):
            return 0.0

    @staticmethod
    def _level_label(rules: Any, comparison: str, level: int) -> str:
        """A comparison level's label ("exact", "prefix", …), "null" for a missing value."""
        if level < 0:
            return "null"
        try:
            return str(rules.labels[rules.names.index(comparison)][level])
        except (ValueError, IndexError, AttributeError):
            return ""

    def _compare(
        self,
        model: EntityModel,
        columns: Sequence[_Column],
        found: Sequence[GoldenCandidate],
        shape: str,
        state: SourceState | None,
        text: Callable[[str, Any], str | None],
        *,
        pair_levels: Mapping[str, str] | None = None,
        kept_apart: str | None = None,
    ) -> tuple[CompareRow, ...]:
        """Every column attribute in model order: each column's value through `text`, and each candidate's
        agreement with the subject from its best member's level on the comparison naming the attribute. Two
        golden records compare by the levels of the pair that raised the task (`pair_levels`), the first
        the reference; the attribute a cannot-link rule keeps them apart on (`kept_apart`,
        "cannot_link:<attribute>") reads different when their values differ."""
        if not columns:
            return ()
        by_attribute: dict[str, str] = {}
        for spec in model.match.comparisons:
            by_attribute.setdefault(spec.attribute, spec.name)
        levels = [{c.comparison: c.label for c in g.best.explanation.contributions} for g in found]
        approved = columns[0].values if shape == "held_update" else None
        out: list[CompareRow] = []
        for attribute in model.column_attributes():
            name = attribute.name
            values = tuple(text(name, column.values.get(name)) for column in columns)
            comparison = by_attribute.get(name)
            if shape in ("source", "held_new") and found:
                agreement = tuple(
                    _agreement_of(lv[comparison]) if comparison and comparison in lv else "" for lv in levels
                )
            elif shape == "golden_pair" and pair_levels is not None and len(columns) == 2:
                mark = _agreement_of(pair_levels[comparison]) if comparison in pair_levels else ""
                if kept_apart == f"cannot_link:{name}":
                    first, second = (display.value_text(model, name, c.values.get(name)) for c in columns)
                    if first is not None and second is not None and first != second:
                        mark = "disagree"
                agreement = (mark,)
            else:
                agreement = tuple("" for _ in columns[1:])
            changed = False
            if approved is not None and state is not None:
                changed = display.value_text(model, name, approved.get(name)) != display.value_text(
                    model, name, state.values.get(name)
                )
            out.append(
                CompareRow(
                    attribute=name,
                    label=display.attribute_label(model, name),
                    values=values,
                    agreement=agreement,
                    critical=attribute.criticality == "critical",
                    personal=attribute.personal,
                    changed=changed,
                )
            )
        return tuple(out)

    # ------------------------------------------------------------------ previews and the impact line

    def _preview_rows(
        self, model: EntityModel, now: Mapping[str, Any], after: Mapping[str, Any]
    ) -> tuple[tuple[PreviewRow, ...], tuple[str, ...]]:
        rows: list[PreviewRow] = []
        changed: list[str] = []
        for attribute in model.column_attributes():
            name = attribute.name
            before = display.value_text(model, name, now.get(name))
            later = display.value_text(model, name, after.get(name))
            differs = before != later
            label = display.attribute_label(model, name)
            if differs:
                changed.append(label)
            rows.append(
                PreviewRow(
                    label=label,
                    now=self.privacy.masked_text(model, name, now.get(name)),
                    after=self.privacy.masked_text(model, name, after.get(name)),
                    changed=differs,
                )
            )
        return tuple(rows), tuple(changed)

    def _impact(
        self,
        model: EntityModel,
        items: Sequence[ChangeItem],
        work: WorkWrites,
        master_id: str,
        golden: GoldenRow | None,
    ) -> Preview:
        now = golden.values if golden is not None else {}
        after = dict(now)
        for item in items:
            if isinstance(item, UpdateGolden) and item.master_id == master_id:
                after = dict(item.values)
        rows, changed = self._preview_rows(model, now, after)
        links = [i for i in items if isinstance(i, LinkSource)]
        impact = Impact(
            xrefs_added=sum(1 for i in links if i.target == master_id),
            xrefs_removed=sum(1 for i in links if i.expected_master_id not in (None, master_id)),
            golden_changed=changed,
            relationships_changed=sum(
                1 for i in items if isinstance(i, (UpsertRelationship, EndRelationship))
            ),
            held_released=len(work.release),
        )
        return Preview(master_id=master_id, rows=rows, impact=impact)

    def _link_preview(
        self, task: Task, model: EntityModel, state: SourceState, master_id: str, golden: GoldenRow | None
    ) -> Preview | None:
        try:
            items, work = self.lifecycle.plan_link(task.entity, state.source, master_id)
        except MdmError:
            return None
        return self._impact(model, items, work, master_id, golden)

    def _shape_preview(
        self,
        task: Task,
        model: EntityModel,
        state: SourceState | None,
        shape: str,
        columns: Sequence[_Column],
    ) -> Preview | None:
        if shape == "held_update" and state is not None:
            try:
                items, work = self.lifecycle.plan_approve_update(task.entity, state.source)
            except MdmError:
                return None
            master = next((i.master_id for i in items if isinstance(i, UpdateGolden)), None)
            if master is None:
                return None
            golden = self.store.golden(task.entity, [master]).get(master)
            return self._impact(model, items, work, master, golden)
        if shape in ("golden_pair", "golden"):
            return Preview(
                master_id=task.master_ids[0] if task.master_ids else None, rows=(), impact=Impact()
            )
        return None

    def _notice(
        self, task: Task, shape: str, state: SourceState | None, candidates: Sequence[Candidate]
    ) -> str | None:
        if task.source is not None and (state is None or state.status != "active"):
            return NOTICE_GONE
        if shape == "source" and not candidates:
            return NOTICE_NO_CANDIDATE
        if shape == "golden_pair":
            blocked = candidates[0].blocked_by if candidates else None
            return f"{blocked} {NOTICE_MERGE}" if blocked else NOTICE_MERGE
        if shape == "golden":
            return NOTICE_RETIRE
        if shape == "held_update" and task.reason in _MAY_BELONG_ELSEWHERE:
            return NOTICE_DETACH
        if shape == "held_new":
            return NOTICE_CREATE
        if shape == "information":
            return NOTICE_INFORMATION
        return None

    def _offered(self, task: Task, shape: str, candidates: Sequence[Candidate]) -> tuple[Action, ...]:
        """The decisions the shape offers, before the per-call part decides whether each is enabled."""
        out: list[Action] = []
        for decision in OFFERED.get(shape, ()):
            if decision == "link":
                out.extend(
                    Action(
                        "link",
                        f"Link to {c.master_id}",
                        _KEYS["link"],
                        c.blocked_by is None,
                        why_blocked(c.blocked_rule),
                        target=c.master_id,
                    )
                    for c in candidates
                )
            elif decision == "not_a_match":
                out.append(Action("not_a_match", "Not a match", _KEYS["not_a_match"], True, None))
            elif decision == "keep_apart":
                pair = " and ".join(task.master_ids[:2])
                out.append(Action("keep_apart", f"Keep {pair} apart", _KEYS["keep_apart"], True, None))
            elif decision == "approve_update":
                out.append(
                    Action("approve_update", "Approve the update", _KEYS["approve_update"], True, None)
                )
            elif decision == "reject_update":
                out.append(Action("reject_update", "Reject the update", _KEYS["reject_update"], True, None))
            elif decision == "keep_orphan":
                target = task.master_ids[0] if task.master_ids else None
                out.append(
                    Action(
                        "keep_orphan",
                        f"Keep {target or 'the golden record'}",
                        _KEYS["keep_orphan"],
                        True,
                        None,
                    )
                )
        return tuple(out)

    # ------------------------------------------------------------------ the part refreshed on every call

    def _assemble(
        self, task: Task, prepared: _Prepared, row: TaskRow, actor: Actor, now: datetime
    ) -> TaskCase:
        holder = self.rows.claim_holder(task, now)
        staged = row.staged
        can_decide = allowed(actor, "work_tasks")
        role = ROLE_LABELS.get(actor.role, actor.role).lower()
        blocked: str | None = None
        if not can_decide:
            blocked = f"Your role, {role}, can see tasks but not decide them."
        elif staged is not None:
            blocked = (
                "Your decision on this task is in the tray. Undo it there to change it."
                if staged.mine
                else "Another steward's decision on this task is waiting in the tray."
            )
        elif holder is not None and holder != actor.name:
            blocked = "Another steward is working on this task. Pick another task."
        actions: list[Action] = []
        for action in prepared.offered:
            why = blocked
            if why is None and not action.enabled:
                why = action.why_not  # a candidate a cannot-link rule blocks
            if why is None and not allowed(actor, action.decision):
                why = f"Your role, {role}, cannot take this decision."
            if why is None and prepared.shape == "held_update" and not prepared.held:
                why = "This record's update is no longer held."
            actions.append(replace(action, enabled=why is None, why_not=why))
        work_blocked = None if can_decide else f"Your role, {role}, can see tasks but not work on them."
        # while a decision on the task waits in the tray, nobody claims, snoozes or escalates it: the flush
        # would close it under them
        claim_blocked = work_blocked or (blocked if staged is not None else None)
        if claim_blocked is None and holder is not None and holder != actor.name:
            claim_blocked = "Another steward is working on this task. Pick another task."
        actions.append(
            Action(
                "claim",
                "Claimed by you" if holder == actor.name else "Claim",
                _KEYS["claim"],
                claim_blocked is None,
                claim_blocked,
            )
        )
        actions.append(Action("snooze", "Snooze", _KEYS["snooze"], claim_blocked is None, claim_blocked))
        actions.append(
            Action("escalate", "Escalate", _KEYS["escalate"], claim_blocked is None, claim_blocked)
        )
        if staged is not None and staged.mine:
            actions.append(Action("undo", "Undo", _KEYS["undo"], True, None))
        return TaskCase(
            row=row,
            reason_text=prepared.reason_text,
            shape=prepared.shape,
            columns=prepared.columns,
            compare=prepared.compare,
            candidates=prepared.candidates,
            default_candidate=prepared.default_candidate,
            close_call=prepared.close_call,
            preview=prepared.preview,
            actions=tuple(actions),
            notice=prepared.notice,
            masked=prepared.masked,
            revealable=prepared.revealable,
            staged=staged,
            claimed_by=display.claimant_text(holder, actor),
            event_id=prepared.event_id,
            task_version=iso(task.updated_at),
        )

    # ------------------------------------------------------------------ reveal

    def reveal(self, task_id: str, *, actor: Actor, reason: str) -> Revealed:
        """The case's compare rows in clear, once; one access-log row per attribute and record with the
        reason code (`REVEAL_REASONS`, else `Forbidden(reason_required)`). `reveal`. Never reads or fills the
        case cache."""
        require(actor, "view_tasks")
        require(actor, "reveal")
        if reason not in REVEAL_REASONS:
            raise Forbidden("reason_required", action="reveal")
        task = self._task(task_id)
        model = self._model(task.entity)
        state = self._state(task)
        shape = shape_of(task)
        if task.source is not None and (state is None or state.status != "active"):
            shape = "information"
        columns, found = self._columns(task, model, state, shape)
        master = None
        if state is not None:
            master = self.store.xrefs_for_sources(task.entity, [state.source]).get(state.source)
        clear = self.privacy.reveal_values(
            task.entity,
            [(c.ref, c.values) for c in columns],
            [a.name for a in model.column_attributes()],
            actor=actor,
            reason=reason,
            master_id=master,
        )
        shown = [
            _Column(c.header, c.ref, {**{k: v for k, v in c.values.items()}, **clear[i]})
            for i, c in enumerate(columns)
        ]
        personal = set(model.personal_attributes())

        def text(name: str, value: Any) -> str | None:
            if name in personal:
                return self.privacy.clear_text(model, name, value)
            return display.value_text(model, name, value)

        paired = self._pair(task, model) if shape == "golden_pair" else None
        compare = self._compare(
            model,
            shown,
            found,
            shape,
            state,
            text,
            pair_levels=paired[1] if paired is not None else None,
            kept_apart=paired[0].blocked_rule if paired is not None else None,
        )
        logged = len({(c.ref, name) for i, c in enumerate(columns) for name in clear[i]})
        return Revealed(compare=compare, logged=logged, columns=tuple(c.header for c in columns))

    # ------------------------------------------------------------------ the check at staging

    def check(
        self,
        task_id: str,
        decision: str,
        *,
        actor: Actor,
        target: str | None = None,
        seen_event: str | None = None,
        seen_task: str | None = None,
    ) -> Staging:
        """Whether the decision can be staged, writing nothing: the role allows it, the task is open, of a
        shape that offers it, not staged, not claimed by another; the case the steward decided on is the
        current one — for a task with a source record `seen_event` equals the record's current event, for
        one without `seen_task` equals the task's version (`TaskCase.task_version`), and a missing one is
        refused like a stale one (`Conflict(record_changed)`, `Conflict(task_changed)`); a link names an
        offered candidate, explicitly in a close call (`Forbidden(close_call)`), and never one a cannot-link
        rule blocks (`Forbidden(cannot_link)`). Returns what the tray keeps, with its locks and the row
        versions of the golden records it names, which the flush checks again."""
        require(actor, "work_tasks")
        if decision not in DECISIONS:
            raise Forbidden("decision_not_offered", decision=token(decision))
        require(actor, decision)
        task = self._task(task_id)
        now = self.clock()
        prepared = self._prepared(task, actor)
        if decision not in OFFERED.get(prepared.shape, ()):
            raise Forbidden("decision_not_offered", decision=decision)
        held = self.store.staged_by_locks([task_lock(task_id)]).get(task_lock(task_id))
        if held is not None:
            raise Conflict([token(task_id)], code="already_staged", mine=held.actor == actor.name)
        holder = self.rows.claim_holder(task, now)
        if holder is not None and holder != actor.name:
            until = (task.claimed_at or now) + timedelta(minutes=self.settings.claim_minutes)
            raise Conflict([token(task_id)], code="claimed_by_another", until=iso(until))
        state = self._state(task)
        if task.source is not None:
            if state is None or state.status != "active":
                raise Conflict([source_token(task.source)], code="record_changed")
            if seen_event is None or seen_event != state.event_id:
                raise Conflict([source_token(task.source)], code="record_changed")
        elif seen_task is None or seen_task != iso(task.updated_at):
            raise Conflict([token(task_id)], code="task_changed")
        chosen = None
        score = band = signature = None
        rule_version = None
        candidates = [c.master_id for c in prepared.candidates]
        if decision == "link":
            if target is None:
                if prepared.close_call:
                    raise Forbidden("close_call")
                if prepared.default_candidate is None and prepared.candidates:
                    raise Forbidden("cannot_link")  # every candidate is blocked
                target = prepared.default_candidate
            chosen = next((c for c in prepared.candidates if c.master_id == target), None)
            if chosen is None:
                raise Forbidden("candidate_not_offered")
            if chosen.blocked_by is not None:
                raise Forbidden("cannot_link")
        elif decision in ("not_a_match",) and prepared.candidates:
            chosen = prepared.candidates[0]
        if chosen is not None:
            score, band, signature, rule_version = (
                round(chosen.score, 6),
                chosen.band,
                chosen.signature,
                chosen.rule_version,
            )
        elif task.evidence:
            found_score = task.evidence.get("score")
            score = (
                found_score
                if isinstance(found_score, (int, float)) and not isinstance(found_score, bool)
                else None
            )
            found_band = task.evidence.get("band")
            band = found_band if isinstance(found_band, str) else None
        linked: str | None = None
        if decision in ("approve_update", "reject_update"):
            if state is None or not state.held:
                raise Conflict([source_token(task.source)] if task.source else [], code="not_held")
            linked = self.store.xrefs_for_sources(task.entity, [state.source]).get(state.source)
            if not linked:
                raise Conflict([source_token(state.source)], code="not_held")
        locks = [task_lock(task_id)]
        if task.source is not None:
            locks.append(f"source:{task.entity}:{task.source.system}:{task.source.key}")
        else:
            locks.extend(f"golden:{m}" for m in task.master_ids)
        if decision != "link":
            target = task.master_ids[0] if decision == "keep_orphan" and task.master_ids else None
        # the golden records the decision acts on, at the version the steward staged it against
        named = [target] if decision == "link" and target else []
        if decision == "approve_update" and linked:
            named = [linked]
        if task.source is None:
            named = list(task.master_ids)
        rows = self.store.golden(task.entity, named) if named else {}
        for master in named:
            row = rows.get(master)
            if row is None or row.status != "active":
                raise Conflict([token(master)], code="target_changed")
        subject = safe_detail(
            source=source_token(task.source) if task.source else None,
            candidates=candidates if decision == "not_a_match" or decision == "link" else [],
            master_ids=list(task.master_ids) if task.source is None else [],
            score=score,
            band=band,
            rule_version=rule_version,
            kind=task.kind,
            reason=token(task.reason),
            row_versions={master: rows[master].row_version for master in named},
        )
        return Staging(
            task_id=task_id,
            entity=task.entity,
            decision=decision,
            target=target,
            subject=subject,
            signature=signature,
            event_id=state.event_id if state is not None and task.source is not None else None,
            locks=tuple(dict.fromkeys(locks)),
        )

    # ------------------------------------------------------------------ the commit at flush

    def execute(self, entry: TrayEntry, *, now: datetime) -> tuple[CommitResult, tuple[SourceKey, ...]]:
        """Commits a staged decision once its window has passed, always audited, closing the task and
        settling the entry in the same transaction; returns the commit result and the records queued again
        for arrival. `Conflict` (record_changed, task_closed, target_changed) or `PlatformRefused
        (persona_refused)` when it must not commit."""
        actor = Actor(entry.actor, "person", entry.actor_role, persona=entry.persona)
        if actor.persona and not self.settings.local_mode:
            raise PlatformRefused("persona_refused", role=token(actor.role))
        if entry.decision not in DECISIONS:
            raise Forbidden("decision_not_offered", decision=token(entry.decision))
        task = self.store.tasks_by_id([entry.task_id]).get(entry.task_id)
        if task is None or task.status != "open":
            raise Conflict([token(entry.task_id)], code="task_closed")
        entity = entry.entity
        source = task.source
        state = self.store.source_states(entity, [source]).get(source) if source is not None else None
        if source is not None:
            if state is None or state.status != "active" or state.event_id != entry.event_id:
                raise Conflict([source_token(source)], code="record_changed")
        subject = entry.subject or {}
        holds = (
            entry.decision == "link"
            and source is not None
            and self.store.xrefs_for_sources(entity, [source]).get(source) == entry.target
        )
        # a link that holds already has nothing to check against: its target moved by taking this very record
        self._check_targets(entry, subject, moved_ok=(entry.target,) if holds and entry.target else ())
        if entry.decision == "link" and state is not None and entry.target:
            # a member that joined the target since staging may hold a registered ID a cannot-link rule
            # keeps apart from this record's
            if self.matching.blocked_by(entity, state, entry.target) is not None:
                raise Conflict([token(entry.target)], code="target_changed")
        score = subject.get("score")
        band = subject.get("band")
        rule_version = subject.get("rule_version")
        labels: list[MatchLabel] = []

        def label(left: str, right: str, kind: str) -> MatchLabel:
            return MatchLabel(
                entity=entity,
                left_ref=left,
                right_ref=right,
                label=kind,
                rule_version=rule_version if isinstance(rule_version, int) else None,
                score=float(score)
                if isinstance(score, (int, float)) and not isinstance(score, bool)
                else None,
                band=band if isinstance(band, str) else None,
                signature=entry.signature,
                task_id=entry.task_id,
                entry_id=entry.entry_id,
                decided_by=entry.actor,
                decided_role=entry.actor_role,
                decided_at=now,
            )

        requeue: list[tuple[SourceKey, str, int]] = []
        release: list[SourceKey] = []
        if entry.decision == "link" and source is not None and entry.target:
            labels.append(label(source.text(), entry.target, "match"))
        elif entry.decision == "not_a_match" and source is not None and state is not None:
            declined = [m for m in subject.get("candidates") or [] if isinstance(m, str)]
            labels.extend(label(source.text(), m, "not_a_match") for m in dict.fromkeys(declined))
            requeue.append((source, state.event_id, state.landing_seq))
        elif entry.decision == "keep_apart" and len(task.master_ids) >= 2:
            lower, higher = sorted(task.master_ids[:2])
            labels.append(label(lower, higher, "keep_apart"))
        elif entry.decision == "reject_update" and source is not None:
            release.append(source)
        work = WorkWrites(
            entity,
            release=tuple(release),
            labels=tuple(labels),
            requeue=tuple(requeue),
            tray=(TraySettlement(entry.entry_id, "committed", None, "committed"),),
            expect_events=((source, entry.event_id),) if source is not None and entry.event_id else (),
            close_task_ids=(entry.task_id,),
        )
        reason = f"workbench:{entry.decision}"
        evidence = safe_detail(
            task_id=entry.task_id,
            entry_id=entry.entry_id,
            decision=entry.decision,
            target=entry.target,
            score=score if isinstance(score, (int, float)) and not isinstance(score, bool) else None,
            band=band if isinstance(band, str) else None,
            kind=task.kind,
            source=source_token(source) if source is not None else None,
            # the golden records the decision is about: a decision that publishes nothing still shows on
            # their timelines (the audit's change log keys it by each)
            master_ids=self._decided_about(entry, task, subject),
        )
        if entry.decision == "link":
            if source is None or not entry.target:
                raise Forbidden("decision_not_offered", decision="link")
            result = self.lifecycle.link(
                entity,
                source,
                entry.target,
                actor=actor,
                reason=reason,
                event_id=entry.event_id,
                work=work,
                evidence=evidence,
            )
        elif entry.decision == "approve_update":
            if source is None:
                raise Forbidden("decision_not_offered", decision="approve_update")
            result = self.lifecycle.approve_update(
                entity,
                source,
                actor=actor,
                reason=reason,
                event_id=entry.event_id,
                work=work,
                evidence=evidence,
            )
        else:
            result = self.lifecycle.decide_only(
                entity, entry.decision, actor=actor, reason=reason, work=work, evidence=evidence
            )
        return result, tuple(s for s, _, _ in requeue)

    @staticmethod
    def _decided_about(entry: TrayEntry, task: Task, subject: Mapping[str, Any]) -> list[str]:
        """The golden records a decision is about: the link's or kept orphan's target, the candidates a
        "not a match" declined, the pair kept apart, the record a held update belongs to."""
        if entry.decision in ("link", "keep_orphan"):
            return [entry.target] if entry.target else []
        if entry.decision == "not_a_match":
            return [m for m in dict.fromkeys(subject.get("candidates") or []) if isinstance(m, str)]
        if entry.decision == "keep_apart":
            return list(task.master_ids[:2])
        versions = subject.get("row_versions")
        return [m for m in versions if isinstance(m, str)] if isinstance(versions, Mapping) else []

    def _check_targets(
        self, entry: TrayEntry, subject: Mapping[str, Any], *, moved_ok: Sequence[str] = ()
    ) -> None:
        """`Conflict(target_changed)` when a golden record the decision acts on is no longer active, or
        changed since the decision was staged (its row version then; not for those in `moved_ok`); a link
        always names its target."""
        versions = subject.get("row_versions")
        versions = dict(versions) if isinstance(versions, Mapping) else {}
        named = [m for m in versions if isinstance(m, str)]
        if entry.decision == "link" and entry.target and entry.target not in named:
            named.append(entry.target)
        if entry.decision == "link" and not entry.target:
            raise Conflict([token("none")], code="target_changed")
        if not named:
            return
        rows = self.store.golden(entry.entity, named)
        for master in named:
            row = rows.get(master)
            if row is None or row.status != "active":
                raise Conflict([token(master)], code="target_changed")
            staged_at = versions.get(master)
            if master in moved_ok:
                continue
            if (
                isinstance(staged_at, int)
                and not isinstance(staged_at, bool)
                and staged_at != row.row_version
            ):
                raise Conflict([token(master)], code="target_changed")


__all__ = ["CASE_SHAPES", "DecisionService", "OFFERED", "shape_of"]
