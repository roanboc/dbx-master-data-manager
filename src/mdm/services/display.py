"""How the workbench words what it shows: labels, display names, reasons, tray lines, due times.

Component `ACMP14`. Pure helpers over a model and values, with no store: every sentence is built from codes,
attribute labels, master IDs and source keys, and a value reaches a string only through `value_text`, which
the privacy service masks by role before anything is shown (decision 20).
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from mdm.models.authority import ROLE_LABELS, Actor
from mdm.models.entity_model import EntityModel
from mdm.models.errors import NotFound
from mdm.models.records import SourceKey
from mdm.models.tasks import KIND_LABELS, Task
from mdm.models.wording import attribute_label as _attribute_words
from mdm.models.wording import comparison_name

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
    crm:C000123", "Approve the update of crm:C000123", "Keep ORG-000123 and ORG-004410 apart"."""
    source = _source_of(subject)
    masters = _masters_of(subject)
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
    decision hinges on, else the reason in a few words ("critical: family name")."""
    evidence = task.evidence or {}
    hinge = evidence.get("hinge")
    if isinstance(hinge, str) and hinge and task.kind in ("review", "possible_duplicate"):
        return f"hinges on {comparison_name(hinge)}"
    found = REASONS.get(task.reason)
    if found is None:
        return task.reason.replace("_", " ")
    return _filled(found[0], evidence)


def reason_sentence(task: Task) -> str:
    """The reason in a full sentence, for the decide pane."""
    evidence = task.evidence or {}
    found = REASONS.get(task.reason)
    sentence = _filled(found[1], evidence) if found else f"Reason: {task.reason.replace('_', ' ')}."
    hinge = evidence.get("hinge")
    if isinstance(hinge, str) and hinge and task.kind in ("review", "possible_duplicate"):
        sentence += f" It hinges on the {comparison_name(hinge)}."
    return sentence


def kind_label(kind: str) -> str:
    """ "Review", "Possible duplicate", "Held", …"""
    return KIND_LABELS.get(kind, kind.replace("_", " ").capitalize())


def suggestion_text(task: Task) -> str:
    """What the task asks, from its stored suggestion and evidence: "Link to ORG-000123", "Approve or
    reject", "Keep apart or merge", "Keep or retire", "Investigate"."""
    evidence = task.evidence or {}
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
    """ "due in 3 h", "due in 25 min", "overdue by 2 d"; "" without a due time."""
    if due_at is None:
        return ""
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
