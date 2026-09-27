"""Tasks: work for a person, one open task per record and kind.

A later event for the same record and kind updates that task's suggestion,
evidence, event and `updated_at` instead of opening another
(`mdm_work.open_task`). `reason` is a code (`critical_update_held`,
`review_band`, `cannot_link_conflict`, …); suggestion and evidence are safe
details only.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from mdm.models.records import SourceKey

TASK_KINDS = ("review", "possible_duplicate", "held", "exception", "orphan", "unresolved_reference")
TASK_STATUSES = ("open", "closed")


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


def task_key(kind: str, entity: str, source: SourceKey | None, master_ids: Sequence[str] = ()) -> str:
    """The key of one record and kind: `"TK-" + sha256(kind|entity|source or sorted master IDs)[:16]`."""
    subject = source.text() if source is not None else ",".join(sorted(master_ids))
    return "TK-" + hashlib.sha256(f"{kind}|{entity}|{subject}".encode()).hexdigest()[:16]


def task_id(key: str, opened_by: str) -> str:
    """`"TSK-" + sha256(key|opened_by)[:16]`; opened_by = the event ID (or change-set fingerprint) that opened it."""
    return "TSK-" + hashlib.sha256(f"{key}|{opened_by}".encode()).hexdigest()[:16]
