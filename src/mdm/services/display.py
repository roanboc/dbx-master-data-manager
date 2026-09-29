"""How the workbench words what it shows: labels, display names, reasons, tray lines, due times.

Component `ACMP14`. Pure helpers over a model and values, with no store: every sentence is built from codes,
attribute labels, master IDs and source keys, and a value reaches a string only through `value_text`, which
the privacy service masks by role before anything is shown (decision 20).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Mapping, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from mdm.models.authority import ROLE_LABELS, Actor
from mdm.models.batch import SPLIT_ALL, SPLIT_REASON_PREFIX
from mdm.models.changes import (
    ChangeItem,
    EndRelationship,
    LinkSource,
    UpdateGolden,
    UpsertRelationship,
    WorkWrites,
)
from mdm.models.entity_model import EntityModel
from mdm.models.errors import NotFound
from mdm.models.records import GoldenRow, SourceKey
from mdm.models.tasks import KIND_LABELS, WAITS_TEXT, Task, waits_for_restore
from mdm.models.wording import attribute_label as _attribute_words
from mdm.models.wording import comparison_name
from mdm.models.workbench import Impact, Mark, Preview, PreviewRow

#: what a masked personal value that is not text reads (a date, a group)
HIDDEN = "hidden"
MASK = "***"
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

#: task reason code -> (the inbox's chip, the decide pane's sentence); "{…}" is filled from the evidence
REASONS: Mapping[str, tuple[str, str]] = {
    # review
    "review_band": (
        "review band",
        "In the review band: alike, but not enough to link on its own.",
    ),
    "cannot_link_conflict": (
        "kept apart by a rule",
        "The records score as a match, but a cannot-link rule keeps them apart: an identifier differs.",
    ),
    "unlinked_candidate": (
        "like an unlinked record",
        "The record looks like another source record that no golden record holds yet.",
    ),
    "batch_candidate": (
        "like a record beside it",
        "The record looks like another record that arrived in the same batch.",
    ),
    "master_id_held": (
        "master ID named",
        "The source names a golden record for this record, and its policy holds such links for a steward.",
    ),
    # possible duplicate
    "ambiguous_auto": (
        "matches more than one",
        "The record matches more than one golden record automatically, and the matcher never chooses "
        "between them.",
    ),
    "possible_duplicate": (
        "possible duplicate",
        "Two golden records look alike: they may be the same.",
    ),
    # held
    "update_held": (
        "update held",
        "The source's policy holds every update from this record for a steward.",
    ),
    "critical_update_held": (
        "critical: {critical}",
        "The update changes a critical attribute ({critical}), and the source's policy holds such updates "
        "for a steward.",
    ),
    "end_date_held": (
        "end date: {end_dates}",
        "The update sets an end date ({end_dates}), and the source's policy holds such updates for a steward.",
    ),
    "no_longer_auto": (
        "no longer matches",
        "After this update the record no longer matches the other members of its golden record "
        "automatically.",
    ),
    "auto_elsewhere": (
        "matches {other}",
        "After this update the record matches another golden record ({other}) automatically.",
    ),
    "member_conflict": (
        "identifier conflict",
        "After this update a strong identifier of the record differs from another member's.",
    ),
    "new_held": (
        "new record held",
        "The source's policy holds new records for a steward.",
    ),
    "authored_style": (
        "authored by stewards",
        "Stewards author the golden records of this entity, so an arriving record waits for one.",
    ),
    # exception
    "unknown_master_id": (
        "unknown master ID",
        "The source names a golden record the hub does not know.",
    ),
    "master_id_conflict": (
        "master ID conflicts",
        "The source names a golden record, but a strong identifier of the record differs from its members'.",
    ),
    "unknown_source": (
        "unknown source",
        "The record comes from a source the published model does not name.",
    ),
    "planning_failed": (
        "could not be planned",
        "The matcher could not plan this record, so nothing was published for it.",
    ),
    "conflict": (
        "changed while planned",
        "The record's golden record changed while the matcher planned it, twice, so nothing was published "
        "for it.",
    ),
    "quality_hold": (
        "quality rule holds it",
        "A quality rule that holds records failed on this one ({rules}).",
    ),
    # orphan
    "no_active_member": (
        "no member left",
        "The last source record of this golden record was deleted, so it has no member.",
    ),
    "last_member_detached": (
        "last member detached",
        "A steward detached the last source record of this golden record, so it has no member.",
    ),
    "last_member_moved": (
        "last member moved",
        "A steward linked the last source record of this golden record elsewhere, so it has no member.",
    ),
    # the matcher's checkpoint (story 3.2)
    "breaker_demoted": (
        "paused by the breaker",
        "This record would have linked automatically, but the quality breaker has paused automatic linking "
        "for this entity.",
    ),
    "blind_sample": (
        "blind review",
        "Blind review: decide where this record belongs, as if it had just arrived. The first decision and "
        "its score stay hidden.",
    ),
    "blind_disagreement": (
        "a blind review disagreed",
        "A blind review placed this record differently from the first decision.",
    ),
    # unresolved references (opened from initiative 4)
    "reference_unresolved": (
        "reference unresolved",
        "The record names a record of another entity that no golden record holds yet.",
    ),
}


def attribute_label(model: EntityModel | None, name: str) -> str:
    """An attribute's label: "Registered ID", "Family name"."""
    del model  # every label reads from the name until a model carries its own labels
    return _attribute_words(name)


def _is_personal(model: EntityModel, name: str) -> bool:
    try:
        return model.attribute(name).personal
    except NotFound:
        return False


def value_text(model: EntityModel | None, name: str, value: Any) -> str | None:
    """A value's display form, in clear: dates ISO, numbers plain, a group as "registered: 121 Umber Lane,
    Varnmouth; …". None stays None. The privacy service masks it before anything is shown."""
    del model
    if value is None:
        return None
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, float):
        return format(Decimal(repr(value)).normalize(), "f")
    if isinstance(value, Mapping):
        return _entry_text(value)
    if isinstance(value, (list, tuple)):
        entries = [_entry_text(v) if isinstance(v, Mapping) else str(v) for v in value]
        return "; ".join(e for e in entries if e) or None
    text = str(value)
    return text if text.strip() else None


def _entry_text(entry: Mapping[str, Any]) -> str:
    """One group entry: its key field first ("registered: …"), then the other fields in order."""
    kind = entry.get("kind")
    rest = [str(v) for k, v in entry.items() if k != "kind" and v not in (None, "")]
    body = ", ".join(rest)
    if kind:
        return f"{kind}: {body}" if body else str(kind)
    return body


def masked_form(model: EntityModel, name: str, value: Any) -> str | None:
    """The mdm_read rule for display: a personal text value -> its first character and "***"; any other
    personal value -> "hidden"; a value that is not personal -> its display form."""
    text = value_text(model, name, value)
    if text is None:
        return None
    if not _is_personal(model, name):
        return text
    try:
        logical_text = model.attribute(name).type == "text" and not model.attribute(name).repeating
    except NotFound:
        logical_text = False
    if logical_text and isinstance(value, str):
        return value[:1] + MASK
    return HIDDEN


def display_name(
    model: EntityModel, values: Mapping[str, Any], masked: Collection[str], fallback: str = ""
) -> str:
    """The model's display name ("{given_name} {family_name}") filled from `values`; an attribute in `masked`
    shows its masked form. Empty -> `fallback` (the master ID or source key the caller passes)."""

    def fill(found: re.Match[str]) -> str:
        name = found.group(1)
        value = values.get(name)
        text = masked_form(model, name, value) if name in masked else value_text(model, name, value)
        return text or ""

    text = " ".join(_PLACEHOLDER.sub(fill, model.display_name or "").split())
    return text or fallback


def role_label(role: str, persona: bool = False) -> str:
    """ "Data steward", or "Data steward (persona)" for a persona."""
    label = ROLE_LABELS.get(role, role.replace("_", " ").capitalize())
    return f"{label} (persona)" if persona else label


def claimant_text(claimed_by: str | None, actor: Actor) -> str | None:
    """Who holds a claim, from the actor's side: "you"; "Data steward (persona)" for a persona; else the
    claimant's name. None when nobody does."""
    if not claimed_by:
        return None
    if claimed_by == actor.name:
        return "you"
    if claimed_by.startswith("persona:"):
        return role_label(claimed_by.partition(":")[2], persona=True)
    return claimed_by


def _source_of(subject: Mapping[str, Any]) -> str:
    source = subject.get("source")
    return source if isinstance(source, str) and source else "the record"


def _masters_of(subject: Mapping[str, Any]) -> list[str]:
    found = subject.get("master_ids") or subject.get("candidates") or []
    return [m for m in found if isinstance(m, str)] if isinstance(found, (list, tuple)) else []


def tray_label(decision: str, subject: Mapping[str, Any], target: str | None) -> str:
    """One tray line, from IDs and source keys only: "Link crm:C000123 to ORG-000123", "Not a match:
    crm:C000123", "Approve the update of crm:C000123", "Keep ORG-000123 and ORG-004410 apart", "Quality
    sample: crm:C000123 belongs to ORG-000123"."""
    if decision in ("batch_link", "batch_compensate"):
        return batch_label(decision, subject)
    source = _source_of(subject)
    masters = _masters_of(subject)
    pair = f"{masters[0]} and {masters[1]}" if len(masters) >= 2 else "the golden records"
    if decision == "blind_link":
        if subject.get("source") is None and len(masters) >= 2:
            return f"Quality sample: {pair} are the same"
        return f"Quality sample: {source} belongs to {target or 'a golden record'}"
    if decision == "blind_none":
        if subject.get("source") is None and len(masters) >= 2:
            return f"Quality sample: {pair} are not the same"
        return f"Quality sample: {source} belongs to none shown"
    if decision == "keep_decision":
        if subject.get("source") is None and len(masters) >= 2:
            return f"Keep the first decision on {pair}"
        return f"Keep the first decision on {source}"
    if decision == "link":
        return f"Link {source} to {target or 'a golden record'}"
    if decision == "not_a_match":
        return f"Not a match: {source}"
    if decision == "approve_update":
        return f"Approve the update of {source}"
    if decision == "reject_update":
        return f"Reject the update of {source}"
    if decision == "keep_apart":
        if len(masters) >= 2:
            return f"Keep {masters[0]} and {masters[1]} apart"
        return "Keep the golden records apart"
    if decision == "keep_orphan":
        return f"Keep {masters[0] if masters else target or 'the golden record'}"
    return decision.replace("_", " ").capitalize()


def _words(values: Any) -> str:
    """Attribute names, IDs or codes from evidence, joined for a sentence: "family name and birth date"."""
    items = [v for v in (values if isinstance(values, (list, tuple)) else [values]) if isinstance(v, str)]
    words = [comparison_name(v) if "-" not in v and ":" not in v else v for v in items]
    if not words:
        return "not named"
    if len(words) == 1:
        return words[0]
    return ", ".join(words[:-1]) + " and " + words[-1]


def _filled(template: str, evidence: Mapping[str, Any]) -> str:
    return _PLACEHOLDER.sub(lambda m: _words(evidence.get(m.group(1))), template)


def reason_chip(task: Task) -> str:
    """The inbox's reason chip: "hinges on registered ID" when the stored evidence names the comparison a
    decision hinges on, else the reason in a few words ("critical: family name"); none on a quality sample,
    whose suggestion ("Decide blind") says it all."""
    if task.kind == "quality_sample":
        return ""
    evidence = task.evidence or {}
    hinge = evidence.get("hinge")
    if _hinges(task) and isinstance(hinge, str) and hinge:
        return f"hinges on {comparison_name(hinge)}"
    found = REASONS.get(task.reason)
    if found is None:
        return task.reason.replace("_", " ")
    return _filled(found[0], evidence)


#: what the first decision a blind review disputes did, by its code
_FIRST_DECISIONS = {
    "auto_link": ("The matcher linked it to {target}", "The matcher linked it automatically"),
    "hint_link": (
        "The matcher linked it to {target}, as its source named",
        "The matcher linked it as its source named",
    ),
    "auto_create": ("The matcher created {target} for it", "The matcher created a golden record for it"),
    "link": ("A steward linked it to {target}", "A steward linked it"),
    "not_a_match": ("A steward said it is not {declined}", "A steward said it is not a match"),
}


def dispute_sentence(task: Task) -> str:
    """What the first decision did and what the blind review answered, from the dispute's codes and IDs:
    "The matcher created ORG-000164 for it; a blind review placed it in none of the golden records shown.";
    for a pair kept apart, "A steward kept ORG-000211 and ORG-000388 apart; a blind review found them the
    same."."""
    evidence = task.evidence or {}
    answer = evidence.get("answer")
    if task.source is None:
        pair = " and ".join(m for m in task.master_ids[:2] if isinstance(m, str)) or "them"
        return f"A steward kept {pair} apart; a blind review found them the same."
    target = evidence.get("target")
    declined = [m for m in evidence.get("declined") or () if isinstance(m, str)]
    first = _FIRST_DECISIONS.get(str(evidence.get("first") or ""))
    if first is None:
        lead = "The first decision placed it differently"
    elif "{declined}" in first[0]:
        lead = first[0].format(declined=" or ".join(declined)) if declined else first[1]
    else:
        lead = first[0].format(target=target) if isinstance(target, str) and target else first[1]
    if isinstance(answer, str) and answer and answer != "none":
        placed = f"a blind review placed it in {answer}"
    else:
        placed = "a blind review placed it in none of the golden records shown"
    return f"{lead}; {placed}."


def breaker_wait_sentence(task: Task) -> str:
    """A record the quality breaker holds with no golden record to decide on, naming the records it would
    have formed one with: "This record would have formed a golden record with crm:C000251 (and 1 other)
    automatically, but the quality breaker has paused automatic linking for this entity."."""
    partners = [s for s in (task.evidence or {}).get("review_with") or () if isinstance(s, str)]
    if not partners:
        return REASONS["breaker_demoted"][1]
    more = len(partners) - 1
    others = f" (and {more} other{'s' if more > 1 else ''})" if more else ""
    return (
        f"This record would have formed a golden record with {partners[0]}{others} automatically, but the "
        "quality breaker has paused automatic linking for this entity."
    )


def reason_sentence(task: Task) -> str:
    """The reason in a full sentence, for the decide pane."""
    evidence = task.evidence or {}
    if task.reason == "blind_disagreement":
        return dispute_sentence(task)
    if task.reason == "breaker_demoted" and not task.master_ids:
        return breaker_wait_sentence(task)
    found = REASONS.get(task.reason)
    sentence = _filled(found[1], evidence) if found else f"Reason: {task.reason.replace('_', ' ')}."
    hinge = evidence.get("hinge")
    if _hinges(task) and isinstance(hinge, str) and hinge:
        sentence += f" It hinges on the {comparison_name(hinge)}."
    return sentence


def _hinges(task: Task) -> bool:
    """Whether the comparison a decision hinges on is the task's story: a review or a possible duplicate,
    unless the quality breaker or a blind review opened it (their reason says more)."""
    return task.kind in ("review", "possible_duplicate") and task.reason not in (
        "breaker_demoted",
        "blind_disagreement",
    )


def kind_label(kind: str) -> str:
    """ "Review", "Possible duplicate", "Held", …"""
    return KIND_LABELS.get(kind, kind.replace("_", " ").capitalize())


def suggestion_text(task: Task) -> str:
    """What the task asks, from its stored suggestion and evidence: "Link to ORG-000123", "Approve or
    reject", "Keep apart or merge", "Keep or retire", "Investigate"."""
    evidence = task.evidence or {}
    if task.kind == "quality_sample":
        return "Decide blind"
    if task.reason == "blind_disagreement":
        return "Keep or correct the first decision"
    if task.kind in ("review", "possible_duplicate") and task.source is not None:
        masters = [m for m in (evidence.get("master_ids") or task.master_ids or ()) if isinstance(m, str)]
        if len(masters) == 1:
            return f"Link to {masters[0]}"
        if masters:
            return f"Choose among {len(masters)} records"
        return "Link or not a match"
    if task.kind in ("review", "possible_duplicate"):
        return "Keep apart or merge"
    if task.kind == "held":
        if task.reason in ("new_held", "authored_style"):
            return "Create or wait"
        return "Approve or reject"
    if task.kind == "orphan":
        return "Keep or retire"
    return "Investigate"


def due_text(due_at: datetime | None, now: datetime) -> str:
    """ "due in 3 h", "due in 25 min", "overdue by 2 d"; "waits for a data owner" for a record the quality
    breaker holds with nothing to decide; "" without a due time."""
    if due_at is None:
        return ""
    if waits_for_restore(due_at):
        return WAITS_TEXT.lower()
    delta = due_at - now
    late = delta < timedelta(0)
    span = _span(-delta if late else delta)
    return f"overdue by {span}" if late else f"due in {span}"


def _span(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{max(minutes, 1)} min"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} h"
    return f"{hours // 24} d"


def subject_text(task: Task) -> str:
    """The task's subject: "crm:C000123", or "ORG-000123 · ORG-004410"."""
    if task.source is not None:
        return task.source.text()
    return " · ".join(task.master_ids)


def source_text(source: SourceKey | None) -> str | None:
    return source.text() if source is not None else None


def ordered_attributes(model: EntityModel, names: Sequence[str]) -> list[str]:
    """`names` in model order, names the model does not know last."""
    order = {a.name: i for i, a in enumerate(model.attributes)}
    return sorted(names, key=lambda n: (order.get(n, len(order)), n))


# ---------------------------------------------------------------------------------------------- signature batches
# (story 3.3): a pattern of comparisons in words, a batch's tray line, and the before-and-after of a golden
# record, which the decide pane and a batch's rows share. Built from codes, counts, attribute labels, IDs and
# source keys only; a value reaches a preview only through the masking callable the caller passes.

#: a comparison's mark in a signature, and what it means
MARK_WORDS: Mapping[str, str] = {"=": "the same", "≈": "similar", "≠": "different", "∅": "missing"}

#: why a batch's review was left out, failed or ended, after its count: "3 claimed by another steward"
ITEM_REASON_WORDS: Mapping[str, str] = {
    "closed": "whose task closed",
    "staged": "held by another decision in the tray",
    "claimed": "claimed by another steward",
    "snoozed": "snoozed",
    "escalated": "escalated",
    "record_changed": "whose record changed",
    "linked": "already linked to a golden record",
    "no_candidate": "with no golden record to link to now",
    "blocked": "kept apart by a cannot-link rule",
    "close_call": "now a close call",
    "signature_changed": "whose pattern changed",
    "rules_changed": "scored under an earlier rule version",
    "split_earlier": "split off an earlier batch",
    "task_closed": "whose task closed",
    "target_changed": "whose golden record changed",
    "moved_since": "changed after the batch",
    "stopped": "back in the queue after a stop",
    "bulk_withdrawn": "back in the queue after bulk decisions were withdrawn",
    "chunk_failed": "back in the queue after a chunk failed",
    "over_max": "beyond the largest batch",
    "nothing_left": "back in the queue",
    "committed": "committed",
}


def parse_signature(signature: str | None) -> list[tuple[str, str]]:
    """(comparison, mark) of each part of a signature, in rule order: "given_name= · birth_date≈" gives
    [("given_name", "="), ("birth_date", "≈")]. A part without a known mark is left out."""
    out: list[tuple[str, str]] = []
    for part in (signature or "").split(" · "):
        part = part.strip()
        if len(part) >= 2 and part[-1] in MARK_WORDS:
            out.append((part[:-1], part[-1]))
    return out


def _comparison_attribute(model: EntityModel | None, comparison: str) -> str:
    if model is not None:
        for spec in model.match.comparisons:
            if spec.name == comparison:
                return spec.attribute
    return comparison


def comparison_label(model: EntityModel | None, comparison: str) -> str:
    """A comparison's label, from the attribute it compares: "Birth date"."""
    return attribute_label(model, _comparison_attribute(model, comparison))


def signature_marks(model: EntityModel | None, signature: str | None) -> tuple[Mark, ...]:
    """A signature as marks in rule order, each with its label and words: Mark("birth_date", "Birth date",
    "≈", "similar")."""
    return tuple(
        Mark(comparison, comparison_label(model, comparison), mark, MARK_WORDS[mark])
        for comparison, mark in parse_signature(signature)
    )


def signature_words(model: EntityModel | None, signature: str | None) -> str:
    """A signature in words, for the command line: "given name the same · family name the same · birth date
    similar · …"."""
    return (
        " · ".join(f"{m.label.lower()} {m.words}" for m in signature_marks(model, signature)) or "no pattern"
    )


def signature_comparisons(signature: str | None) -> tuple[str, ...]:
    """The comparisons a signature names, in rule order: the choices of "Which comparison misled?"."""
    return tuple(comparison for comparison, _ in parse_signature(signature))


def stratum_label(stratum: str) -> str:
    """A forced-sample stratum in words: "crm/hr" -> "crm and hr"."""
    left, _, right = stratum.partition("/")
    return f"{left} and {right}" if right else left


def item_reason_words(code: str | None) -> str:
    """A batch review's reason in words: "claimed by another steward"; a split's "split off on birth date"."""
    if not code:
        return ""
    if code.startswith(SPLIT_REASON_PREFIX):
        named = code[len(SPLIT_REASON_PREFIX) :]
        if named == SPLIT_ALL:
            return "split off with every alike review"
        return f"split off on {comparison_name(named)}"
    return ITEM_REASON_WORDS.get(code, code.replace("_", " "))


def split_on_label(model: EntityModel | None, split_on: str | None) -> str:
    """The comparison a disagreeing sample decision named, in words: "birth date", or "every alike review"."""
    if not split_on or split_on == SPLIT_ALL:
        return "every alike review"
    return comparison_label(model, split_on).lower()


def batch_label(decision: str, subject: Mapping[str, Any]) -> str:
    """A batch's tray line, from its subject's codes and counts alone: "Link 566 alike reviews (BAT-…)", or
    "Undo batch BAT-…: 566 links"."""
    batch_id = subject.get("batch_id") if isinstance(subject.get("batch_id"), str) else "a batch"
    count = subject.get("decisions")
    count = count if isinstance(count, int) and not isinstance(count, bool) else 0
    if decision == "batch_compensate":
        original = subject.get("compensates") if isinstance(subject.get("compensates"), str) else "a batch"
        return f"Undo batch {original}: {count} link{'s' if count != 1 else ''}"
    return f"Link {count} alike review{'s' if count != 1 else ''} ({batch_id})"


def preview_rows(
    model: EntityModel,
    now: Mapping[str, Any],
    after: Mapping[str, Any],
    masked_text: Callable[[str, Any], str | None],
) -> tuple[tuple[PreviewRow, ...], tuple[str, ...]]:
    """The golden values before and after, each masked through `masked_text(name, value)`, and the labels of
    the attributes that change."""
    rows: list[PreviewRow] = []
    changed: list[str] = []
    for attribute in model.column_attributes():
        name = attribute.name
        before = value_text(model, name, now.get(name))
        later = value_text(model, name, after.get(name))
        differs = before != later
        label = attribute_label(model, name)
        if differs:
            changed.append(label)
        rows.append(
            PreviewRow(
                label=label,
                now=masked_text(name, now.get(name)),
                after=masked_text(name, after.get(name)),
                changed=differs,
            )
        )
    return tuple(rows), tuple(changed)


def changed_names(model: EntityModel, now: Mapping[str, Any], after: Mapping[str, Any]) -> tuple[str, ...]:
    """The names of the attributes whose display form differs between two sets of golden values."""
    return tuple(
        a.name
        for a in model.column_attributes()
        if value_text(model, a.name, now.get(a.name)) != value_text(model, a.name, after.get(a.name))
    )


def impact_of(
    model: EntityModel,
    items: Sequence[ChangeItem],
    work: WorkWrites,
    master_id: str,
    golden: GoldenRow | None,
    masked_text: Callable[[str, Any], str | None],
) -> Preview:
    """What a plan does to one golden record: its values before and after, masked, and the impact line."""
    now = golden.values if golden is not None else {}
    after = dict(now)
    for item in items:
        if isinstance(item, UpdateGolden) and item.master_id == master_id:
            after = dict(item.values)
    rows, changed = preview_rows(model, now, after, masked_text)
    links = [i for i in items if isinstance(i, LinkSource)]
    impact = Impact(
        xrefs_added=sum(1 for i in links if i.target == master_id),
        xrefs_removed=sum(1 for i in links if i.expected_master_id not in (None, master_id)),
        golden_changed=changed,
        relationships_changed=sum(1 for i in items if isinstance(i, (UpsertRelationship, EndRelationship))),
        held_released=len(work.release),
    )
    return Preview(master_id=master_id, rows=rows, impact=impact)
