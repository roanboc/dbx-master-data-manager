"""Tasks: work for a person, one open task per record and kind.

A later event for the same record and kind updates that task's suggestion,
evidence, event and `updated_at` instead of opening another
(`mdm_work.open_task`). `reason` is a code (`critical_update_held`,
`review_band`, `cannot_link_conflict`, …); suggestion and evidence are safe
details only. The workbench's columns (due time, claim, snooze, escalation)
default to none, so a task built before initiative 3 reads as before.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from mdm.models.records import SourceKey

TASK_KINDS = (
    "review",
    "possible_duplicate",
    "held",
    "exception",
    "orphan",
    "unresolved_reference",
    "quality_sample",  # a blind review of a committed decision (story 3.2): one task per sample
)
TASK_STATUSES = ("open", "closed")
#: the due time of a record the quality breaker holds with no golden record to decide on: nobody can settle it
#: until a data owner restores automatic linking, so it never breaches, and it sorts after every other task
WAITS_FOR_RESTORE = datetime(9999, 1, 1, tzinfo=UTC)


#: what such a task shows for its due time
WAITS_TEXT = "Waits for a data owner"


def waits_for_restore(due_at: datetime | None) -> bool:
    """Whether a due time is the breaker's wait (`WAITS_FOR_RESTORE`) rather than a service level's."""
    return due_at is not None and due_at >= WAITS_FOR_RESTORE


#: what a steward reads for each kind
KIND_LABELS: Mapping[str, str] = {
    "review": "Review",
    "possible_duplicate": "Possible duplicate",
    "held": "Held",
    "exception": "Exception",
    "orphan": "Orphan",
    "unresolved_reference": "Unresolved reference",
    "quality_sample": "Quality sample",
}


@dataclass(frozen=True, slots=True)
class Task:
    task_id: str
    task_key: str
    entity: str
    kind: str
    status: str
    source: SourceKey | None
    master_ids: tuple[str, ...]
    reason: str
    suggestion: Mapping[str, Any]
    evidence: Mapping[str, Any]
    event_id: str | None
    created_at: datetime
    updated_at: datetime
    # The workbench's columns (initiative 3). A task opened again under its old ID starts afresh: a new
    # due time, and no claim, snooze or escalation. Actor names here are workbench actors, never values.
    due_at: datetime | None = None  # stamped by the commit path from the service level of its kind
    claimed_by: str | None = None
    claimed_at: datetime | None = None  # a claim lapses `Settings.claim_minutes` after this
    snoozed_until: datetime | None = None
    snoozed_by: str | None = None
    escalated_at: datetime | None = None
    escalated_by: str | None = None
    escalation: str | None = None  # a code of models.workbench.ESCALATION_REASONS


def task_key(kind: str, entity: str, source: SourceKey | None, master_ids: Sequence[str] = ()) -> str:
    """The key of one record and kind: `"TK-" + sha256(kind|entity|source or sorted master IDs)[:16]`."""
    subject = source.text() if source is not None else ",".join(sorted(master_ids))
    return "TK-" + hashlib.sha256(f"{kind}|{entity}|{subject}".encode()).hexdigest()[:16]


def task_id(key: str, opened_by: str) -> str:
    """`"TSK-" + sha256(key|opened_by)[:16]`; opened_by = the event ID (or change-set fingerprint) that opened it."""
    return "TSK-" + hashlib.sha256(f"{key}|{opened_by}".encode()).hexdigest()[:16]
