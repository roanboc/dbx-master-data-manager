"""The record reader: a golden record, its provenance, members, timeline and relationships; a source record.

Application service `ASVC10`, component Record reader. Every read needs `read` and returns values
masked by the actor's role; a reveal goes through `PrivacyService.reveal_values`, logged per attribute
with a reason code (`REVEAL_REASONS`). The Why of a value always stays masked. Nothing here writes but the
access log of a reveal.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any

from mdm import capacity
from mdm.backend.store import SqlStore
from mdm.config import Settings
from mdm.models.authority import ROLE_LABELS, Actor
from mdm.models.canonical import utcnow
from mdm.models.entity_model import EntityModel
from mdm.models.errors import Forbidden, NotFound
from mdm.models.records import GoldenRow, SourceKey, SourceState
from mdm.models.safety import MASTER_ID_RE, SOURCE_KEY_RE
from mdm.models.tasks import task_key
from mdm.models.wording import strategy_words
from mdm.models.workbench import (
    REVEAL_REASONS,
    MemberView,
    RecordHeader,
    RelationshipView,
    Resolution,
    RunnerUp,
    SourceView,
    TimelineEvent,
    TimelinePage,
    ValueView,
    ValueWhy,
)
from mdm.services import display
from mdm.services.authority import CLAUSES_MARK, require
from mdm.services.privacy import PrivacyService
from mdm.services.registry import ModelRegistry
from mdm.services.support import token

#: how a relationship reads from its other end
INBOUND_LABELS = {"works_at": "employs", "subsidiary_of": "parent of"}
_MATCH_VERSION = re.compile(r"match v([0-9]+)")


def _echo(ref: str) -> str | None:
    """The reference when it may be shown back: shaped like a master ID or a source key."""
    return ref if MASTER_ID_RE.match(ref) or SOURCE_KEY_RE.match(ref) else None


def _unknown_notice(ref: str) -> str:
    shown = _echo(ref)
    return f"No record has the ID {shown}." if shown else "No record has that ID."


def _day(value: date | datetime | None) -> str | None:
    """ "5 Jan 2026", the one date format the record view uses."""
    if value is None:
        return None
    day = value.date() if isinstance(value, datetime) else value
    return f"{day.day} {day.strftime('%b %Y')}"


def _long_day(value: datetime) -> str:
    """ "30 Nov 2026"."""
    return f"{value.day} {value.strftime('%b %Y')}"


def _joined(words: Sequence[str], last: str = "and") -> str:
    if not words:
        return ""
    if len(words) == 1:
        return words[0]
    return ", ".join(words[:-1]) + f" {last} " + words[-1]


class LookupService:
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

    # ------------------------------------------------------------------ helpers

    def _model(self, entity: str) -> EntityModel:
        return self.registry.published(entity)

    def _entities(self, entity: str | None) -> list[str]:
        return [entity] if entity is not None else list(self.registry.published_entities())

    def _golden_row(self, entity: str, master_id: str) -> GoldenRow:
        row = self.store.golden(entity, [master_id]).get(master_id)
        if row is None:
            raise NotFound("unknown_master_id", entity=entity, master_id=token(master_id))
        return row

    def _entity_of(self, master_id: str) -> str | None:
        """The published entity whose code starts the master ID."""
        code = master_id.split("-", 1)[0]
        for entity in self.registry.published_entities():
            try:
                if self._model(entity).code == code:
                    return entity
            except NotFound:
                continue
        return None

    def _age_days(self, when: datetime | None) -> int | None:
        if when is None:
            return None
        return max((self.clock() - when).days, 0)

    def _reveal(
        self,
        entity: str,
        model: EntityModel,
        subject: str,
        values: Mapping[str, Any],
        *,
        actor: Actor,
        reason: str,
        master_id: str | None,
    ) -> dict[str, Any]:
        if reason not in REVEAL_REASONS:
            raise Forbidden("reason_required", action="reveal")
        [clear] = self.privacy.reveal_values(
            entity,
            [(subject, values)],
            list(model.personal_attributes()),
            actor=actor,
            reason=reason,
            master_id=master_id,
        )
        return clear

    # ------------------------------------------------------------------ resolving a reference

    def resolve(self, ref: str, *, actor: Actor, entity: str | None = None) -> Resolution:
        """What a reference names: a master ID (a merged or retired one resolves to its survivor, with a
        notice), a source key `system:key` (the source record, with its golden record when linked), or
        nothing (`unknown`, echoing the reference only when it has the shape of an ID or source key)."""
        require(actor, "read")
        ref = (ref or "").strip()
        if ":" in ref:
            try:
                source = SourceKey.from_text(ref)
            except ValueError:
                return Resolution("unknown", None, None, None, _unknown_notice(ref))
            for name in self._entities(entity):
                try:
                    model = self._model(name)
                except NotFound:
                    continue
                if not model.has_source(source.system):
                    continue
                if self.store.source_states(name, [source]).get(source) is None:
                    continue
                master = self.store.xrefs_for_sources(name, [source]).get(source)
                return Resolution("source", name, master, source.text(), None)
            return Resolution("unknown", None, None, None, _unknown_notice(ref))
        found_entity = entity or self._entity_of(ref)
        if found_entity is None or not ref:
            return Resolution("unknown", None, None, None, _unknown_notice(ref))
        row = self.store.golden(found_entity, [ref]).get(ref)
        if row is None:
            return Resolution("unknown", None, None, None, _unknown_notice(ref))
        if row.status == "merged":
            survivor = self.store.resolve_retired([ref]).get(ref) or row.survivor_id
            if survivor and survivor != ref:
                return Resolution(
                    "golden",
                    found_entity,
                    survivor,
                    None,
                    f"{ref} was merged into {survivor}; showing the survivor.",
                )
        if row.status == "retired":
            return Resolution("golden", found_entity, ref, None, f"{ref} is retired.")
        return Resolution("golden", found_entity, ref, None, None)

    # ------------------------------------------------------------------ a golden record

    def header(self, entity: str, master_id: str, *, actor: Actor) -> RecordHeader:
        """The record's header: masked title, status, the IDs merged into it, open tasks and counts."""
        require(actor, "read")
        model = self._model(entity)
        row = self._golden_row(entity, master_id)
        masked = set(model.personal_attributes())
        members = self.store.members(entity, [master_id], capacity.MEMBERS_SHOWN).get(master_id, [])
        count = self.store.member_counts(entity, [master_id]).get(master_id, 0)
        retired = self.store.retired_through(master_id, capacity.MEMBERS_SHOWN)
        tasks = self.store.tasks_for_sources(entity, members) if members else []
        # the record's own orphan task, found by its key (a task naming golden records has no source)
        tasks += list(self.store.tasks_by_key([task_key("orphan", entity, None, (master_id,))]).values())
        relationships = self.store.relationships_of([master_id], capacity.RELATIONSHIPS_SHOWN)
        return RecordHeader(
            entity=entity,
            master_id=master_id,
            title=display.display_name(model, row.values, masked, master_id),
            status=row.status,
            survivor_id=row.survivor_id,
            retired_ids=tuple(r.retired_id for r in retired if r.retired_id != master_id),
            open_tasks=tuple(sorted({t.task_id for t in tasks})),
            commit_version=row.commit_version,
            member_count=count,
            relationship_count=len(relationships),
        )

    def golden(
        self, entity: str, master_id: str, *, actor: Actor, reveal: bool = False, reason: str = ""
    ) -> tuple[ValueView, ...]:
        """The golden values with their provenance chips, masked unless `reveal` with a reason code (logged
        per attribute)."""
        require(actor, "read")
        model = self._model(entity)
        row = self._golden_row(entity, master_id)
        provenance = self.store.provenance(entity, [master_id]).get(master_id, {})
        steward = self.store.steward_values(entity, [master_id]).get(master_id, {})
        clear: dict[str, Any] = {}
        if reveal:
            clear = self._reveal(
                entity, model, master_id, row.values, actor=actor, reason=reason, master_id=master_id
            )
        winners: dict[str, SourceKey] = {}
        for name, entry in provenance.items():
            source = self._winner(entry)
            if source is not None and source.system != "steward":
                winners[name] = source
        states = self.store.source_states(entity, sorted(set(winners.values()))) if winners else {}
        out: list[ValueView] = []
        for attribute in model.column_attributes():
            name = attribute.name
            value = row.values.get(name)
            personal = attribute.personal
            if personal and name in clear:
                text, masked = display.value_text(model, name, clear[name]), False
            elif personal:
                text, masked = self.privacy.masked_text(model, name, value), value is not None
            else:
                text, masked = display.value_text(model, name, value), False
            entry = provenance.get(name) if isinstance(provenance.get(name), Mapping) else None
            source = self._winner(entry) if entry else None
            decided_by = entry.get("decided_by") if entry else None
            decided_by = decided_by if isinstance(decided_by, str) else None
            pin = steward.get(name)
            pinned_until = pin.pinned_until if pin is not None else None
            state = states.get(source) if source is not None else None
            age = self._age_days(state.occurred_at) if state is not None else None
            if source is not None and source.system == "steward" and pin is not None:
                age = self._age_days(pin.set_at)
            out.append(
                ValueView(
                    attribute=name,
                    label=display.attribute_label(model, name),
                    value=text,
                    masked=masked,
                    personal=personal,
                    critical=attribute.criticality == "critical",
                    source="steward"
                    if source is not None and source.system == "steward"
                    else (source.text() if source is not None else None),
                    decided_by=decided_by,
                    chip=self._chip(source, decided_by, age, pinned_until) if value is not None else None,
                    age_days=age,
                    pinned_until=pinned_until,
                )
            )
        return tuple(out)

    @staticmethod
    def _winner(entry: Mapping[str, Any] | None) -> SourceKey | None:
        winner = entry.get("winner") if isinstance(entry, Mapping) else None
        text = winner.get("source") if isinstance(winner, Mapping) else None
        if not isinstance(text, str):
            return None
        try:
            return SourceKey.from_text(text)
        except ValueError:
            return None

    @staticmethod
    def _chip(
        source: SourceKey | None, decided_by: str | None, age: int | None, pinned_until: datetime | None
    ) -> str | None:
        """ "finance · source trust · 2 d"; "Steward pin · until 30 Nov 2026"; "rules" when the provenance
        was written before the deciding strategy was recorded."""
        if source is None:
            return None
        if source.system == "steward":
            if pinned_until is not None:
                return f"Steward pin · until {_long_day(pinned_until)}"
            return "Steward value" + (f" · {age} d" if age is not None else "")
        words = strategy_words(decided_by) if decided_by else "rules"
        parts = [source.system, words]
        if age is not None:
            parts.append(f"{age} d")
        return " · ".join(parts)

    def why(self, entity: str, master_id: str, attribute: str, *, actor: Actor) -> ValueWhy:
        """Why the golden value of `attribute` won: the sentence, the winner and the runners-up, masked
        always."""
        require(actor, "read")
        model = self._model(entity)
        spec = model.attribute(attribute)  # NotFound for an attribute the model does not name
        self._golden_row(entity, master_id)
        label = display.attribute_label(model, attribute)
        entry = self.store.provenance(entity, [master_id]).get(master_id, {}).get(attribute)
        if not isinstance(entry, Mapping):
            strategies = tuple(model.survivorship.strategies(attribute))
            return ValueWhy(
                attribute=attribute,
                label=label,
                sentence=f"No source holds a value for {label}.",
                winner=None,
                runners_up=(),
                strategies=strategies,
                decided_by=None,
                rule_version=model.survivorship.version,
            )
        strategies = tuple(s for s in entry.get("strategy") or () if isinstance(s, str))
        decided_by = entry.get("decided_by") if isinstance(entry.get("decided_by"), str) else None
        rule_version = entry.get("rule_version") if isinstance(entry.get("rule_version"), int) else None
        if spec.personal:
            # the vault's values, only to mask them: a Why never shows a personal value (decision 20)
            entry = self.privacy.vault.resolve(entry)
        winner_doc = entry.get("winner") if isinstance(entry.get("winner"), Mapping) else {}
        runners_doc = [r for r in entry.get("runners_up") or [] if isinstance(r, Mapping)]
        sources = [self._winner({"winner": d}) for d in [winner_doc, *runners_doc]]
        states = self.store.source_states(
            entity, sorted({s for s in sources if s is not None and s.system != "steward"})
        )

        def runner(doc: Mapping[str, Any], source: SourceKey | None) -> RunnerUp:
            state = states.get(source) if source is not None else None
            return RunnerUp(
                source=source.text() if source is not None else str(doc.get("source") or ""),
                value=self.privacy.masked_text(model, attribute, doc.get("value"))
                if spec.personal
                else display.value_text(model, attribute, doc.get("value")),
                age_days=self._age_days(state.occurred_at) if state is not None else None,
                occurred_at=state.occurred_at if state is not None else None,
            )

        winner = runner(winner_doc, sources[0]) if winner_doc else None
        runners = tuple(runner(d, s) for d, s in zip(runners_doc, sources[1:], strict=True))
        pin = self.store.steward_values(entity, [master_id]).get(master_id, {}).get(attribute)
        sentence = self._sentence(
            model, attribute, label, entry, strategies, decided_by, rule_version, sources, pin
        )
        return ValueWhy(
            attribute=attribute,
            label=label,
            sentence=sentence,
            winner=winner,
            runners_up=runners,
            strategies=strategies,
            decided_by=decided_by,
            rule_version=rule_version,
        )

    def _sentence(
        self,
        model: EntityModel,
        attribute: str,
        label: str,
        entry: Mapping[str, Any],
        strategies: Sequence[str],
        decided_by: str | None,
        rule_version: int | None,
        sources: Sequence[SourceKey | None],
        pin: Any,
    ) -> str:
        version = f"v{rule_version}" if rule_version is not None else ""
        uses = ", then ".join(strategy_words(s) for s in strategies) or "no strategy"
        rules = f"Survivorship rules {version} for {label} use {uses}.".replace("  ", " ")
        winner = sources[0] if sources else None
        runner = sources[1] if len(sources) > 1 else None
        if decided_by == "pin":
            until = getattr(pin, "pinned_until", None)
            when = f" until {_long_day(until)}" if isinstance(until, datetime) else ""
            return f"A steward pinned this value{when}, so it wins over every source."
        if decided_by == "only":
            holder = winner.text() if winner is not None else "one source"
            return f"Only {holder} holds a value for {label}."
        if decided_by == "keyed_union":
            key = model.attribute(attribute).key or "its key"
            return f"{rules} Entries are combined by {key}; each entry takes its value by {uses}."
        if decided_by == "source_trust" and winner is not None and runner is not None:
            first = self._trust(model, winner, attribute)
            second = self._trust(model, runner, attribute)
            return (
                f"{rules} The {winner.system} source ranks {first} and the {runner.system} source ranks "
                f"{second} for {label}, so the {winner.system} value wins."
            )
        if decided_by == "recency":
            earlier = strategies[: strategies.index("recency")] if "recency" in strategies else ()
            if "source_trust" in earlier:
                return f"{rules} They rank equally for {label}, so the most recent value wins."
            return f"{rules} The most recent value wins."
        if decided_by == "completeness":
            return f"{rules} The most complete value wins."
        if decided_by == "frequency":
            values = [entry.get("winner", {}).get("value")] + [
                r.get("value") for r in entry.get("runners_up") or [] if isinstance(r, Mapping)
            ]
            same = sum(1 for v in values if v == values[0])
            return f"{rules} The value most sources hold wins, {same} of {len(values)}."
        if decided_by == "tie_break" and winner is not None and runner is not None:
            return f"{rules} {winner.text()} and {runner.text()} tie on every rule, so the source key order decides."
        return f"{rules[:-1]}; the strategies are applied in that order."

    @staticmethod
    def _trust(model: EntityModel, source: SourceKey, attribute: str) -> int | str:
        if source.system == "steward":
            return model.survivorship.steward_rank
        try:
            return model.trust(source.system, attribute)
        except NotFound:
            return "unranked"

    def members(self, entity: str, master_id: str, *, actor: Actor) -> tuple[MemberView, ...]:
        """The source records linked to the record (at most `MEMBERS_SHOWN`), with trust, latest event,
        hold, rule failures and open task."""
        require(actor, "read")
        model = self._model(entity)
        self._golden_row(entity, master_id)
        sources = self.store.members(entity, [master_id], capacity.MEMBERS_SHOWN).get(master_id, [])
        if not sources:
            return ()
        states = self.store.source_states(entity, sources)
        failures = self.store.rule_failures(entity, sources)
        tasks: dict[SourceKey, str] = {}
        for task in self.store.tasks_for_sources(entity, sources):
            if task.source is not None:
                tasks.setdefault(task.source, task.task_id)
        attribute_of = {rule.rule_id: rule.attribute for rule in model.validation.rules}
        out: list[MemberView] = []
        for source in sources:
            state = states.get(source)
            trust = model.source(source.system).trust if model.has_source(source.system) else None
            out.append(
                MemberView(
                    source=source.text(),
                    system=source.system,
                    key=source.key,
                    trust=trust,
                    occurred_at=state.occurred_at if state is not None else None,
                    source_version=state.source_version if state is not None else None,
                    held=bool(state is not None and state.held),
                    rule_failures=tuple(
                        f"{attribute_of.get(str(f.get('rule_id')), str(f.get('rule_id')))}: {f.get('code')}"
                        for f in failures.get(source, [])
                    ),
                    open_task=tasks.get(source),
                )
            )
        return tuple(out)

    def timeline(
        self, entity: str, master_id: str, *, actor: Actor, before: tuple[int, int] | None = None
    ) -> TimelinePage:
        """The record's changes, newest first, one page of `TIMELINE_PAGE` before the cursor
        `(commit version, change sequence)`, with the steward decisions about it that published nothing
        (keep apart, keep an orphan, not a match, a held update rejected) merged in by time; the actor is
        the automated matcher or a role, never a person."""
        require(actor, "read")
        self._model(entity)
        rows = self.store.changes_of_record(entity, master_id, before, capacity.TIMELINE_PAGE + 1)
        more = len(rows) > capacity.TIMELINE_PAGE
        rows = rows[: capacity.TIMELINE_PAGE]
        versions = sorted({r.commit_version for r in rows} | ({int(before[0])} if before else set()))
        commits = {c.commit_version: c for c in self.store.commits_by_version(versions)} if versions else {}
        related = (
            [r.rel_id for r in self.store.relationships_of([master_id], capacity.RELATIONSHIPS_SHOWN)]
            if any("relationship" in r.parts for r in rows)
            else []
        )
        keys = [(r.commit_version, master_id) for r in rows]
        keys += [(r.commit_version, rel) for r in rows if "relationship" in r.parts for rel in related]
        logs = self.store.change_log_rows(keys) if keys else {}
        evidence = (
            self.store.change_set_evidence(sorted({c.change_set_id for c in commits.values()}))
            if commits
            else {}
        )
        merged_in: dict[int, list[str]] = {}
        for row in self.store.merged_into(master_id, capacity.MEMBERS_SHOWN):
            merged_in.setdefault(row.merge_version, []).append(row.retired_id)
        events: list[TimelineEvent] = []
        for row in rows:
            commit = commits.get(row.commit_version)
            own = logs.get((row.commit_version, master_id), [])
            names = self._changed_names(own)
            automated = commit is not None and commit.actor_kind == "automated"
            proof = (evidence.get(commit.change_set_id) or {}) if commit is not None else {}
            declined = bool(proof.get("declined_by"))
            created = row.change_kind == "created"
            rels = [
                self._relationship_words(entry, master_id)
                for rel in related
                for entry in logs.get((row.commit_version, rel), [])
                if entry.get("table_name") == "relationship"
            ]
            events.append(
                TimelineEvent(
                    commit_version=row.commit_version,
                    change_seq=row.change_seq,
                    at=commit.committed_at if commit is not None else None,
                    # the survivor's event in a merge is a merge too, not only an update of its values
                    kind="merged" if merged_in.get(row.commit_version) else row.change_kind,
                    headline=self._headline(
                        row.change_kind,
                        row.parts,
                        row.survivor_id,
                        names,
                        merged_in.get(row.commit_version, []),
                        [r for r in rels if r],
                        proof,
                    ),
                    parts=tuple(row.parts),
                    actor=self._actor_text(commit),
                    automated=automated,
                    authority=self._authority_text(
                        commit, declined, self._sources_of(own, names, created=created)
                    ),
                )
            )
        # a decision that published nothing, between this page's oldest change and the previous page's
        until = commits[int(before[0])].committed_at if before and int(before[0]) in commits else None
        since = events[-1].at if more and events else None
        decided = self._decisions(master_id, since=since, until=until)
        ordered = sorted(
            [*events, *decided], key=lambda e: e.at or datetime.min.replace(tzinfo=UTC), reverse=True
        )
        last = rows[-1] if rows else None
        return TimelinePage(
            events=tuple(ordered),
            before=(last.commit_version, last.change_seq) if more and last is not None else None,
        )

    def _decisions(
        self, master_id: str, *, since: datetime | None, until: datetime | None
    ) -> list[TimelineEvent]:
        """The steward decisions about the record that published nothing, as timeline events."""
        found = self.store.decisions_of_record(
            master_id, since=since, until=until, limit=capacity.TIMELINE_PAGE
        )
        if not found:
            return []
        sets = self.store.change_sets(sorted({cs for cs, _ in found}))
        out: list[TimelineEvent] = []
        for change_set_id, at in found:
            row = sets.get(change_set_id) or {}
            proof = row.get("evidence") if isinstance(row.get("evidence"), Mapping) else {}
            role = row.get("actor_role")
            who = ROLE_LABELS.get(role, "A person") if isinstance(role, str) else "A person"
            out.append(
                TimelineEvent(
                    commit_version=0,
                    change_seq=0,
                    at=at,
                    kind="decided",
                    headline=self._decision_headline(proof, master_id),
                    parts=(),
                    actor=who,
                    automated=False,
                    authority=who,
                    published=False,
                )
            )
        return out

    @staticmethod
    def _decision_headline(proof: Mapping[str, Any], master_id: str) -> str:
        """ "Kept apart from ORG-000204"; "Not a match: crm:C000469 kept out"; "Held update from crm:C000469
        rejected"; built from the decision's code, IDs and source keys."""
        decision = proof.get("decision")
        source = proof.get("source") if isinstance(proof.get("source"), str) else None
        named = [m for m in proof.get("master_ids") or [] if isinstance(m, str) and m != master_id]
        if decision == "keep_apart":
            return f"Kept apart from {named[0]}" if named else "Kept apart"
        if decision == "keep_orphan":
            return "Kept with no source record"
        if decision == "not_a_match":
            return f"Not a match: {source} kept out" if source else "Not a match"
        if decision == "reject_update":
            return f"Held update from {source} rejected" if source else "Held update rejected"
        if decision == "approve_update":
            return (
                f"Held update from {source} approved; no value changed" if source else "Held update approved"
            )
        if decision == "link":
            return f"{source} linked; it was already a member" if source else "Linked; nothing changed"
        if decision in ("blind_link", "blind_none", "keep_decision"):
            return LookupService._blind_headline(proof, decision, source, master_id)
        return "Decided; nothing published"

    @staticmethod
    def _blind_headline(proof: Mapping[str, Any], decision: str, source: str | None, master_id: str) -> str:
        """A blind review's answer, or the first decision kept after one, on a golden record's timeline."""
        answer = proof.get("answer") if isinstance(proof.get("answer"), str) else None
        pair = [m for m in proof.get("master_ids") or [] if isinstance(m, str)]
        if decision == "keep_decision":
            about = source or (" and ".join(pair[:2]) if pair else "this record")
            return f"First decision on {about} kept after a blind review"
        if source is None:  # a pair kept apart, reviewed blind
            both = " and ".join(pair[:2]) if len(pair) >= 2 else "the golden records"
            same = "the same" if decision == "blind_link" else "not the same"
            return f"Blind review: {both} are {same}"
        if decision == "blind_none":
            return f"Blind review placed {source} in none of those shown"
        if answer == master_id:
            return f"Blind review placed {source} here"
        return f"Blind review placed {source} in {answer}" if answer else f"Blind review placed {source}"

    @staticmethod
    def _changed_names(logs: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
        """The attribute names whose value a commit changed on the row, from its audit rows (names only)."""
        names: list[str] = []
        for log in logs:
            if log.get("table_name") in ("xref", "retired_id", "relationship", "provenance", "summary"):
                continue
            before = (log.get("before") or {}).get("values") or {}
            after = (log.get("after") or {}).get("values") or {}
            for name in sorted(set(before) | set(after)):
                if before.get(name) != after.get(name) and name not in names:
                    names.append(name)
        return tuple(names)

    @staticmethod
    def _sources_of(logs: Sequence[Mapping[str, Any]], names: Sequence[str], *, created: bool) -> list[str]:
        """The source records whose values won the attributes a commit changed (every attribute when it
        created the record), from its provenance audit row: source keys only."""
        found: list[str] = []
        for log in logs:
            if log.get("table_name") != "provenance":
                continue
            after = log.get("after") if isinstance(log.get("after"), Mapping) else {}
            for name, entry in sorted(after.items()):
                if not created and name not in names:
                    continue
                winner = entry.get("winner") if isinstance(entry, Mapping) else None
                source = winner.get("source") if isinstance(winner, Mapping) else None
                if isinstance(source, str) and SOURCE_KEY_RE.match(source) and source not in found:
                    found.append(source)
        return found

    @staticmethod
    def _relationship_words(entry: Mapping[str, Any], master_id: str) -> str | None:
        """ "employs PER-000261, added"; "works at ORG-000123, ended": one relationship a commit changed,
        read from this record's side."""
        after = entry.get("after") if isinstance(entry.get("after"), Mapping) else None
        before = entry.get("before") if isinstance(entry.get("before"), Mapping) else None
        if after is None:
            return None
        rel_type = str(after.get("rel_type") or "")
        words = rel_type.replace("_", " ")
        if after.get("from") == master_id:
            label, other = words, after.get("to")
        else:
            label, other = INBOUND_LABELS.get(rel_type, f"{words} (from the other side)"), after.get("from")
        if before is None:
            state = "added"
        elif after.get("status") == "ended" and before.get("status") != "ended":
            state = "ended"
        else:
            state = "changed"
        return f"{label} {other}, {state}" if isinstance(other, str) else None

    @staticmethod
    def _headline(
        kind: str,
        parts: Sequence[str],
        survivor: str | None,
        names: Sequence[str],
        merged_in: Sequence[str],
        relationships: Sequence[str] = (),
        proof: Mapping[str, Any] | None = None,
    ) -> str:
        words = [display.attribute_label(None, n).lower() for n in names]
        if merged_in:
            ids = _joined(list(merged_in))
            return f"{ids} merged into this record"
        if kind == "created":
            return "Created"
        if kind == "merged":
            return f"Merged into {survivor}" if survivor else "Merged"
        if kind == "retired":
            return "Retired"
        if kind == "unmerged":
            return "Unmerged"
        if kind == "reinstated":
            return "Reinstated"
        if kind == "remapped":
            return f"Now resolves to {survivor}" if survivor else "Now resolves to another record"
        if "values" in parts:
            return f"Values updated: {', '.join(words)}" if words else "Values updated"
        if "xref" in parts:
            source = (proof or {}).get("source")
            if (proof or {}).get("decision") == "link" and isinstance(source, str):
                return f"Members changed: {source} linked"
            return "Members changed"
        if "relationship" in parts:
            if not relationships:
                return "Relationships changed"
            shown = "; ".join(relationships[:3])
            rest = len(relationships) - 3
            return f"Relationships: {shown}" + (f"; and {rest} more" if rest > 0 else "")
        return "Updated"

    @staticmethod
    def _actor_text(commit: Any) -> str:
        if commit is None:
            return ""
        if commit.actor_kind == "automated":
            return "Automated matcher"
        return ROLE_LABELS.get(commit.actor_role, "A person")

    @staticmethod
    def _authority_text(commit: Any, declined: bool, sources: Sequence[str] = ()) -> str:
        """ "Applied automatically under rules v1 · from finance:F000123"; "Data steward"; the policy's
        clauses stay in the audit, out of this line."""
        if commit is None:
            return ""
        ref = commit.authority_ref or ""
        if commit.authority_kind == "rule_version":
            prefix, _, _clauses = ref.partition(CLAUSES_MARK)
            found = _MATCH_VERSION.search(prefix)
            text = (
                f"Applied automatically under rules v{found.group(1)}" if found else "Applied automatically"
            )
        elif commit.authority_kind == "role":
            role, _, checker = ref.partition("; checker ")
            text = ROLE_LABELS.get(role, role)
            if checker:
                text += f"; checker {ROLE_LABELS.get(checker, checker)}"
        else:
            text = "First publication"
        if sources:
            shown = ", ".join(sources[:2])
            rest = len(sources) - 2
            text += f" · from {shown}" + (f" and {rest} more" if rest > 0 else "")
        if declined:
            text += " · after a steward's not-a-match"
        return text

    def relationships(self, entity: str, master_id: str, *, actor: Actor) -> tuple[RelationshipView, ...]:
        """The record's relationships (at most `RELATIONSHIPS_SHOWN`), grouped by type, direction and other
        end, each naming every asserting source; other ends' titles masked."""
        require(actor, "read")
        self._model(entity)
        rows = self.store.relationships_of([master_id], capacity.RELATIONSHIPS_SHOWN)
        groups: dict[tuple[str, str, str, str], list[Any]] = {}
        for rel in rows:
            if rel.from_master_id == master_id:
                key = (rel.rel_type, "out", rel.to_entity, rel.to_master_id)
            else:
                key = (rel.rel_type, "in", rel.from_entity, rel.from_master_id)
            groups.setdefault(key, []).append(rel)
        others: dict[str, set[str]] = {}
        for _, _, other_entity, other_id in groups:
            others.setdefault(other_entity, set()).add(other_id)
        titles: dict[tuple[str, str], str] = {}
        for other_entity, ids in others.items():
            try:
                model = self._model(other_entity)
            except NotFound:
                continue
            masked = set(model.personal_attributes())
            for other_id, row in self.store.golden(other_entity, sorted(ids)).items():
                titles[(other_entity, other_id)] = display.display_name(model, row.values, masked, other_id)
        out: list[RelationshipView] = []
        for (rel_type, direction, other_entity, other_id), rels in sorted(groups.items()):
            active = [r for r in rels if r.status == "active"]
            shown = active[0] if active else rels[0]
            words = rel_type.replace("_", " ")
            out.append(
                RelationshipView(
                    rel_type=rel_type,
                    label=words
                    if direction == "out"
                    else INBOUND_LABELS.get(rel_type, f"{words} (from the other side)"),
                    direction=direction,
                    other_entity=other_entity,
                    other_master_id=other_id,
                    other_title=titles.get((other_entity, other_id), other_id),
                    valid_from=_day(shown.valid_from),
                    valid_to=_day(shown.valid_to),
                    status="active" if active else "ended",
                    sources=tuple(sorted({r.origin.text() for r in rels if r.origin is not None})),
                )
            )
        return tuple(out)

    # ------------------------------------------------------------------ a source record

    def source(
        self,
        source: SourceKey,
        *,
        actor: Actor,
        entity: str | None = None,
        reveal: bool = False,
        reason: str = "",
    ) -> SourceView:
        """A source record: its standardised values masked (unless revealed with a reason code), the
        approved values it differs from when held, its versions, open tasks and rule failures."""
        require(actor, "read")
        state: SourceState | None = None
        found_entity: str | None = None
        for name in self._entities(entity):
            try:
                model = self._model(name)
            except NotFound:
                continue
            if not model.has_source(source.system):
                continue
            state = self.store.source_states(name, [source]).get(source)
            if state is not None:
                found_entity = name
                break
        if state is None or found_entity is None:
            raise NotFound("unknown_source_record", source=token(source.text()))
        model = self._model(found_entity)
        linked = self.store.xrefs_for_sources(found_entity, [source]).get(source)
        clear: dict[str, Any] = {}
        if reveal:
            clear = self._reveal(
                found_entity, model, source.text(), state.values, actor=actor, reason=reason, master_id=linked
            )
        values: list[ValueView] = []
        for attribute in model.column_attributes():
            name = attribute.name
            value = state.values.get(name)
            if attribute.personal and name in clear:
                text, masked = display.value_text(model, name, clear[name]), False
            elif attribute.personal:
                text, masked = self.privacy.masked_text(model, name, value), value is not None
            else:
                text, masked = display.value_text(model, name, value), False
            values.append(
                ValueView(
                    attribute=name,
                    label=display.attribute_label(model, name),
                    value=text,
                    masked=masked,
                    personal=attribute.personal,
                    critical=attribute.criticality == "critical",
                    source=source.text(),
                    decided_by=None,
                    chip=None,
                    age_days=self._age_days(state.occurred_at),
                    pinned_until=None,
                )
            )
        differs: tuple[str, ...] = ()
        if state.approved_values is not None:
            differs = tuple(
                a.name
                for a in model.column_attributes()
                if display.value_text(model, a.name, state.approved_values.get(a.name))
                != display.value_text(model, a.name, state.values.get(a.name))
            )
        versions = self.store.source_versions(found_entity, source, 100)
        failures = self.store.rule_failures(found_entity, [source]).get(source, [])
        attribute_of = {rule.rule_id: rule.attribute for rule in model.validation.rules}
        tasks = self.store.tasks_for_sources(found_entity, [source])
        masked_names = set(model.personal_attributes())
        return SourceView(
            entity=found_entity,
            source=source.text(),
            title=display.display_name(model, state.values, masked_names, source.text()),
            status=state.status,
            linked_to=linked,
            held=state.held,
            values=tuple(values),
            approved_differs=differs,
            versions=tuple(
                (v.get("source_version"), v["occurred_at"], int(v["landing_seq"])) for v in versions
            ),
            open_tasks=tuple(t.task_id for t in tasks),
            rule_failures=tuple(
                f"{attribute_of.get(str(f.get('rule_id')), str(f.get('rule_id')))}: {f.get('code')}"
                for f in failures
            ),
        )
