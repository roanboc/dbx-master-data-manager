"""Blind review: quality samples drawn from committed decisions, their blind case, and their agreement.

Part of actor `ACT6`'s checkpoint (story 3.2, decision 1). A share of the automated matcher's links and creates
(`auto_link`, `auto_create`, and the source-asserted `hint_link`), and of a steward's committed link, "not a
match" (when it declined a golden record) and keep apart, is drawn by a keyed hash of the record, its event and the
decision, so both engines draw the same. Each draw is a `QualitySample` and a `quality_sample` task, written
in the same transaction as the decision; automated samples are capped per entity, and a draw over the cap is
skipped and counted.

A second steward answers blind: the record and up to three golden records it might belong to, in master-ID
order and with no score, band, suggestion or first decision. The golden record that holds the record shows
only what its other members bring, so nothing reads as the same because the record won it. The answer is
compared with the first decision, not the record's current placement; a disagreement opens a review of the
first decision beside the record's own tasks. No answer writes a match label: labels bind the matcher
(decision 22), and the answer lives on the sample.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from mdm import capacity
from mdm.backend.store import SqlStore
from mdm.config import Settings
from mdm.engine.sample import draw_value, drawn
from mdm.models.authority import AUTOMATED_MATCHER, Actor
from mdm.models.canonical import utcnow
from mdm.models.match import GoldenCandidate
from mdm.models.quality import (
    NONE_ANSWER,
    STEWARD_DECISIONS,
    QualitySample,
    SampleReview,
    sample_id,
)
from mdm.models.records import SourceKey, SourceState
from mdm.models.safety import safe_detail
from mdm.models.tasks import Task, task_id, task_key
from mdm.models.workbench import TrayEntry
from mdm.services.matching import MatchService, strong_ids_of
from mdm.services.registry import ModelRegistry
from mdm.services.support import is_ref, token

if TYPE_CHECKING:
    from mdm.services.lifecycle import LifecycleService

#: a sample's task: what the inbox shows of it, and nothing of the first decision
SAMPLE_REASON = "blind_sample"
SAMPLE_SUGGESTION = "decide_blind"
DISPUTE_REASON = "blind_disagreement"
DISPUTE_SUGGESTION = "keep_or_correct"


@dataclass(frozen=True, slots=True)
class Settled:
    """An automated decision arrival settles, as the draw and the sample need it."""

    decision: str  # auto_link | auto_create | hint_link
    source: SourceKey
    event_id: str
    target: str  # a master ID, or a `new:` ref until the commit maps it
    band: str  # "auto", "distinct" for a create, "" for a hint link
    score: float | None
    signature: str  # "" when there is none
    rule_version: int | None


@dataclass(frozen=True, slots=True)
class Offered:
    """One golden record a blind case offers: its values as shown, and the comparison levels its markers use."""

    master_id: str
    values: Mapping[str, Any]
    levels: Mapping[str, str]  # comparison -> level label, from the record against its best other member
    holds_record: bool  # the golden record the record is in now: its values leave the record out


class QualityService:
    def __init__(
        self,
        settings: Settings,
        store: SqlStore,
        registry: ModelRegistry,
        matching: MatchService,
        lifecycle: LifecycleService | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        """Wires the service; reads nothing from the store. `lifecycle` builds a blind case's columns; arrival
        draws without it."""
        self.settings = settings
        self.store = store
        self.registry = registry
        self.matching = matching
        self.lifecycle = lifecycle
        self.clock = clock

    # ------------------------------------------------------------------ drawing

    @staticmethod
    def _task(
        entity: str,
        sid: str,
        source: SourceKey | None,
        master_ids: tuple[str, ...],
        event_id: str | None,
        occasion: str,
        now: datetime,
    ) -> Task:
        """The task that asks for the blind answer: one per sample, never merged with the record's own tasks,
        and its evidence names the sample only (nothing of the first decision, its score, band or target)."""
        key = task_key("quality_sample", entity, None, (sid,))
        return Task(
            task_id=task_id(key, occasion),
            task_key=key,
            entity=entity,
            kind="quality_sample",
            status="open",
            source=source,
            master_ids=master_ids,
            reason=SAMPLE_REASON,
            suggestion=safe_detail(action=SAMPLE_SUGGESTION),
            evidence=safe_detail(sample_id=sid),
            event_id=event_id,
            created_at=now,
            updated_at=now,
        )

    def automated(
        self, entity: str, settled: Sequence[Settled], now: datetime
    ) -> tuple[list[QualitySample], list[Task], int]:
        """(samples, their tasks, draws skipped at the cap) of the automated decisions one page settles, in
        landing order: a draw by `draw_value(entity, source key, decision, event, key)` under `sample_share`, while
        fewer than `sample_open_cap` automated samples of the entity are open."""
        share = self.settings.sample_share
        if share <= 0 or not settled:
            return [], [], 0
        cap = self.settings.sample_open_cap
        open_now = self.store.open_sample_count(entity, "automated", cap)
        samples: list[QualitySample] = []
        tasks: list[Task] = []
        skipped = 0
        seen: set[str] = set()
        for item in settled:
            subject = item.source.text()
            if not drawn(
                draw_value(entity, subject, item.decision, item.event_id, self.settings.sample_key), share
            ):
                continue
            sid = sample_id(entity, item.decision, subject, item.event_id)
            if sid in seen:
                continue
            if open_now >= cap:
                skipped += 1
                continue
            seen.add(sid)
            task = self._task(entity, sid, item.source, (), item.event_id, item.event_id, now)
            samples.append(
                QualitySample(
                    sample_id=sid,
                    entity=entity,
                    origin="automated",
                    decision=item.decision,
                    source=item.source,
                    master_ids=(),
                    event_id=item.event_id,
                    target=item.target,
                    declined=(),
                    band=item.band,
                    signature=item.signature,
                    score=round(item.score, 6) if item.score is not None else None,
                    rule_version=item.rule_version,
                    decided_by=AUTOMATED_MATCHER.name,
                    decided_role=AUTOMATED_MATCHER.role,
                    decided_at=now,
                    entry_id=None,
                    task_id=task.task_id,
                    drawn_at=now,
                )
            )
            tasks.append(task)
            open_now += 1
        return samples, tasks, skipped

    def steward(
        self,
        entry: TrayEntry,
        task: Task,
        state: SourceState | None,
        subject: Mapping[str, Any],
        now: datetime,
    ) -> tuple[QualitySample, Task] | None:
        """A steward's committed link, "not a match" that declined at least one golden record, or keep apart,
        drawn under `sample_share` (never capped: they come at the pace of people); None when not drawn.
        Approving or rejecting a held update and keeping an orphan are never drawn: a blind review cannot ask
        them again without showing the answer."""
        decision = entry.decision
        share = self.settings.sample_share
        if decision not in STEWARD_DECISIONS or share <= 0:
            return None
        entity = entry.entity
        source: SourceKey | None = None
        master_ids: tuple[str, ...] = ()
        declined: tuple[str, ...] = ()
        pair_sources: tuple[str, ...] = ()
        target: str | None = None
        event_id: str | None = None
        if decision in ("link", "not_a_match"):
            if task.source is None or state is None:
                return None
            source = task.source
            event_id = entry.event_id or state.event_id
            subject_text, occasion = source.text(), event_id
            if decision == "link":
                target = entry.target
            else:
                named = subject.get("candidates") or []
                declined = tuple(dict.fromkeys(m for m in named if isinstance(m, str)))
                if not declined:
                    return None  # it declined nothing, so it can only agree
        else:
            pair = tuple(sorted(task.master_ids[:2]))
            if len(pair) != 2:
                return None
            master_ids = pair
            subject_text, occasion = ",".join(pair), task.task_id
            named = (task.evidence or {}).get("sources") or []
            pair_sources = tuple(s for s in named if isinstance(s, str))[:2]
        if not drawn(draw_value(entity, subject_text, decision, occasion, self.settings.sample_key), share):
            return None
        sid = sample_id(entity, decision, subject_text, occasion)
        sample_task = self._task(entity, sid, source, master_ids, event_id, occasion, now)
        score = subject.get("score")
        band = subject.get("band")
        rule_version = subject.get("rule_version")
        sample = QualitySample(
            sample_id=sid,
            entity=entity,
            origin="steward",
            decision=decision,
            source=source,
            master_ids=master_ids,
            event_id=event_id,
            target=target,
            declined=declined,
            band=band if isinstance(band, str) else "",
            signature=entry.signature or "",
            score=float(score) if isinstance(score, (int, float)) and not isinstance(score, bool) else None,
            rule_version=rule_version
            if isinstance(rule_version, int) and not isinstance(rule_version, bool)
            else None,
            decided_by=entry.actor,
            decided_role=entry.actor_role,
            decided_at=now,
            entry_id=entry.entry_id,
            task_id=sample_task.task_id,
            drawn_at=now,
            pair_sources=pair_sources,
        )
        return sample, sample_task

    def void_for_deleted(
        self, entity: str, sources: Sequence[SourceKey]
    ) -> tuple[tuple[str, str, SourceKey | None], ...]:
        """(sample ID, task ID, record) of the open samples of records whose deletion settles: they are voided,
        count nothing, and their tasks close. The open reviews their blind disagreements opened close with
        them: a record that is gone has nothing left to keep or correct (their samples, answered already, stay
        as they are)."""
        if not sources:
            return ()
        wanted = sorted(set(sources))
        voids = [(s.sample_id, s.task_id, s.source) for s in self.store.open_samples_for(entity, wanted)]
        for task in self.store.tasks_for_sources(entity, wanted):
            named = (task.evidence or {}).get("sample_id")
            if task.reason == DISPUTE_REASON and isinstance(named, str):
                voids.append((named, task.task_id, task.source))
        return tuple(voids)

    # ------------------------------------------------------------------ the blind case

    def sample_of(self, task: Task) -> QualitySample | None:
        """The sample a quality-sample task, or the review its disagreement opened, names."""
        found = (task.evidence or {}).get("sample_id")
        if not isinstance(found, str):
            return None
        return self.store.samples_by_id([found]).get(found)

    def _survivors(self, ids: Sequence[str | None]) -> dict[str, str]:
        named = sorted({m for m in ids if isinstance(m, str) and m and not is_ref(m)})
        resolved = self.store.resolve_retired(named) if named else {}
        return {m: resolved.get(m, m) for m in named}

    def choices(self, sample: QualitySample, state: SourceState) -> list[Offered]:
        """The golden records a blind review of a record offers, at most `CANDIDATES_SHOWN`, in master-ID order:
        first those the first decision named (its target's survivor while that golden record holds another
        active member than the record; the survivors of the golden records a "not a match" declined), then the
        best others by score in any band, so a missed match can be caught. The golden record that holds the
        record shows the values its other active members survive to, with no pin, and its markers come from
        its best other member."""
        entity = sample.entity
        model = self.registry.published(entity)
        rules = self.registry.compiled(entity)
        source = state.source
        search = self.matching.search(model, rules, [state], explain_all=True)
        pairs = {source: [p for p in search.pairs.get(source, []) if p.right != source]}
        found = self.matching.golden_candidates(
            entity, pairs, {source: strong_ids_of(model, state.ids)}, states=search.states
        ).get(source, [])
        by_master: dict[str, GoldenCandidate] = {g.master_id: g for g in found}
        holder = self.store.xrefs_for_sources(entity, [source]).get(source)
        survivors = self._survivors([sample.target, *sample.declined])
        named: list[str] = []
        target = survivors.get(sample.target or "")
        if target is not None:
            members = self.store.members(entity, [target], capacity.MAX_MEMBERS_CHECKED).get(target, [])
            if any(m != source for m in members):
                named.append(target)
        named.extend(survivors[m] for m in sample.declined if m in survivors)
        wanted = list(dict.fromkeys([*named, *(g.master_id for g in found)]))
        rows = self.store.golden(entity, wanted) if wanted else {}
        active = [m for m in wanted if m in rows and rows[m].status == "active"]
        first = [m for m in dict.fromkeys(named) if m in active]
        chosen = first[: capacity.CANDIDATES_SHOWN]
        chosen += [m for m in active if m not in chosen][: capacity.CANDIDATES_SHOWN - len(chosen)]
        out: list[Offered] = []
        for master in sorted(chosen):
            holds = master == holder
            candidate = by_master.get(master) or self.matching.explain_against(
                entity, state, master, exclude=source, rules=rules
            )
            if holds:
                if self.lifecycle is None:
                    raise RuntimeError("a blind case needs the lifecycle service")
                values: Mapping[str, Any] = self.lifecycle.values_without(entity, master, source)
            else:
                values = rows[master].values
            levels = (
                {c.comparison: c.label for c in candidate.best.explanation.contributions}
                if candidate is not None
                else {}
            )
            out.append(Offered(master, values, levels, holds))
        return out

    # ------------------------------------------------------------------ the answer

    def target_survivor(self, sample: QualitySample) -> str | None:
        """The golden record the first decision placed the record in, through the retired map; None for a
        decision with no golden target."""
        return self._survivors([sample.target]).get(sample.target or "")

    def expected(self, sample: QualitySample, shown: Sequence[str]) -> set[str]:
        """The answers that agree with the first decision (never shown to the reviewer): a record's placement,
        the survivor of its target when that golden record was offered, else "none of these"; a "not a
        match", every answer but a golden record it declined (or its survivor); a keep apart, "not the same"."""
        if sample.decision == "keep_apart":
            return {NONE_ANSWER}
        survivors = self._survivors([sample.target, *sample.declined])
        if sample.decision == "not_a_match":
            declined = set(sample.declined) | {survivors.get(m, m) for m in sample.declined}
            return {m for m in (*shown, NONE_ANSWER) if m not in declined}
        target = survivors.get(sample.target or "")
        return {target} if target is not None and target in shown else {NONE_ANSWER}

    def review(
        self,
        sample: QualitySample,
        answer: str,
        shown: Sequence[str],
        reviewer: Actor,
        *,
        entry_id: str | None,
        event_id: str | None,
        now: datetime,
    ) -> tuple[SampleReview, Task | None]:
        """The blind answer compared with the first decision, and the review a disagreement opens: of a record,
        a `review` naming its golden record now and the answer; of a keep-apart pair answered "the same", a
        `possible_duplicate` naming the pair. Each is keyed by the sample, so it never merges with the
        record's or the pair's own task. Nothing is written here."""
        agreed = answer in self.expected(sample, shown)
        dispute = None if agreed else self._dispute(sample, answer, event_id, now)
        return (
            SampleReview(
                sample_id=sample.sample_id,
                entity=sample.entity,
                origin=sample.origin,
                band=sample.band,
                signature=sample.signature,
                answer=answer,
                agreed=agreed,
                reviewed_by=reviewer.name,
                reviewed_role=reviewer.role,
                reviewed_at=now,
                review_entry_id=entry_id,
                dispute_task_id=dispute.task_id if dispute is not None else None,
            ),
            dispute,
        )

    def _dispute(self, sample: QualitySample, answer: str, event_id: str | None, now: datetime) -> Task:
        entity = sample.entity
        golden_answer = answer if answer != NONE_ANSWER else None
        target = self.target_survivor(sample)
        evidence = safe_detail(
            sample_id=sample.sample_id,
            first=sample.decision,
            target=target,
            declined=list(sample.declined) or None,
            answer=token(answer),
            band=sample.band or None,
            score=sample.score,
        )
        if sample.source is None:
            key = task_key("possible_duplicate", entity, None, (sample.sample_id,))
            return Task(
                task_id=task_id(key, sample.sample_id),
                task_key=key,
                entity=entity,
                kind="possible_duplicate",
                status="open",
                source=None,
                master_ids=tuple(sample.master_ids),
                reason=DISPUTE_REASON,
                suggestion=safe_detail(action=DISPUTE_SUGGESTION),
                evidence={**evidence, **safe_detail(sources=list(sample.pair_sources))},
                event_id=None,
                created_at=now,
                updated_at=now,
            )
        current = self.store.xrefs_for_sources(entity, [sample.source]).get(sample.source)
        named = tuple(dict.fromkeys(m for m in (current, golden_answer) if m))
        key = task_key("review", entity, None, (sample.sample_id,))
        return Task(
            task_id=task_id(key, sample.sample_id),
            task_key=key,
            entity=entity,
            kind="review",
            status="open",
            source=sample.source,
            master_ids=named,
            reason=DISPUTE_REASON,
            suggestion=safe_detail(action=DISPUTE_SUGGESTION),
            evidence=evidence,
            event_id=event_id,
            created_at=now,
            updated_at=now,
        )


__all__ = ["DISPUTE_REASON", "Offered", "QualityService", "SAMPLE_REASON", "Settled"]
