"""Signature batches (story 3.3): alike reviews linked together once a forced sample of them agrees.

A signature group is the open reviews of one entity that share a comparison signature and a match rule
version; its key is `SIG-` and 16 hexadecimal characters. A group has at most one open batch. A batch fixes
its population at the draw, holds a forced sample decided one by one, splits off the reviews that share a
disagreeing record's value on the comparison the steward names, and, once the sample agrees, links every
review left to the golden record its case suggests, chunk by chunk, each chunk its own change set. A
compensation is a batch of kind `compensate` that reverses a committed batch's own links.

Every status list here is checked by the service, never by the DDL, so each list can grow story by story.
Every text is a code, an ID, a key, a source key or a signature: never a value. Frozen dataclasses with
slots, no input or output.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from mdm.models.records import SourceKey

#: a batch links alike reviews, or reverses a committed batch's own links
BATCH_KINDS = ("link", "compensate")
BATCH_STATUSES = (
    "sampling",  # the forced sample is being decided one by one
    "ready",  # the sample agreed: every change can be shown, then staged
    "awaiting_checker",  # above the second-steward threshold: waits outside the tray for a second steward
    "staged",  # one tray entry, in its undo window
    "committing",  # its chunks commit, one a flush pass
    "committed",  # every chunk committed (or the last reviews failed alone after the first chunk)
    "stopped",  # ended early after at least one chunk; committed chunks stand
    "discarded",  # dropped before the tray, or ended by a split
    "failed",  # ended with nothing committed
)
#: a batch in one of these holds its group's `open_batch` row
OPEN_BATCH_STATUSES = BATCH_STATUSES[:5]
#: a batch not yet in the tray: its maker, or a coordinating steward, may discard it
BEFORE_TRAY = ("sampling", "ready", "awaiting_checker")
#: what a review is to its batch: a forced-sample review decided one by one, a review linked in bulk, or a
#: review a disagreement split off
ITEM_ROLES = ("sample", "bulk", "split")
#: the statuses of a sample review
SAMPLE_OUTCOMES = ("open", "agreed", "disagreed", "void")
#: the statuses of a bulk review
BULK_STATUSES = (
    "candidate",  # drawn into the population, waiting for the sample
    "planned",  # prepared: its target, the target's row version and its event are frozen
    "excluded",  # left out with its reason: while the sample is decided (its task closed or moved), or at
    # preparation or staging
    "committed",  # linked in a chunk
    "failed",  # failed alone at its chunk's pre-check, with its reason; its task is back in the queue
    "released",  # the batch ended before its chunk; its task is back in the queue
    "compensated",  # its link was reversed by a compensation
    "kept",  # a compensation left it, since another commit touched it since
)
#: the status of a bulk review a split took; a split sample review keeps its outcome
SPLIT_STATUS = "split"
#: the reason code of a review a split took: `split:<comparison>`, or `split:all`
SPLIT_REASON_PREFIX = "split:"
#: why a review is left out, fails or ends: codes only (a split review's is `split:<comparison>`)
ITEM_REASONS = (
    "closed",
    "staged",
    "claimed",
    "snoozed",
    "escalated",
    "record_changed",
    "linked",
    "no_candidate",
    "blocked",
    "close_call",
    "signature_changed",
    "rules_changed",
    "split_earlier",
    "task_closed",
    "target_changed",
    "moved_since",
    "stopped",
    "bulk_withdrawn",
    "chunk_failed",
    "over_max",
)
#: how a batch ended, or where it stands: the batch's outcome and its tray entry's outcome carry the same codes
BATCH_OUTCOMES = (
    "committing",
    "committed",
    "stopped",
    "bulk_withdrawn",
    "chunk_failed",
    "split_all",
    "too_few_left",
    "discarded",
    "nothing_left",
    "persona_refused",
    "checker_required",
)
#: the outcome of a batch's tray entry while its chunks still commit (the entry itself is `committed`)
COMMITTING = "committing"
#: the `split_on` code of "Every alike review in this batch"
SPLIT_ALL = "all"
#: why a steward compensates a committed batch: codes only
COMPENSATE_REASONS = ("pattern_wrong", "source_defect", "sample_missed")
#: a tray entry's decision for a batch; not in `workbench.DECISIONS`, so `DecisionService.check` never
#: stages one
BATCH_DECISIONS = ("batch_link", "batch_compensate")
#: the codes of `BatchView.actions`, which the batch page turns into buttons; a split is named in the decide
#: pane, never on the batch page
PAGE_ACTIONS = ("decide_sample", "prepare", "stage", "confirm", "send_back", "discard", "undo", "stop")
#: the most reviews one batch takes (proposed); `capacity` re-exports it, because a setting's bound reads it
BATCH_MAX = 1_000
#: the forced sample: 5 + 1 per 150 reviews (adopted); a shared store may not make it smaller
FORCED_SAMPLE_BASE = 5
FORCED_SAMPLE_PER = 150
#: the bounds of MDM_FORCED_SAMPLE_BASE and MDM_FORCED_SAMPLE_PER
FORCED_SAMPLE_BASE_MAX = 100
FORCED_SAMPLE_PER_MAX = 10_000
#: above this many decisions a batch needs a second steward (adopted); a shared store may not raise it
BATCH_CHECKER_ABOVE = 250
#: days after its last chunk a batch can be compensated (adopted), and the most a setting allows
BATCH_UNDO_DAYS = 30
BATCH_UNDO_DAYS_MAX = 365
#: a batch ID: `BAT-` and 20 hexadecimal characters
BATCH_ID_RE = re.compile(r"^BAT-[0-9a-f]{20}\Z")
#: a signature group's key: `SIG-` and 16 hexadecimal characters
SIGNATURE_KEY_RE = re.compile(r"^SIG-[0-9a-f]{16}\Z")


@dataclass(frozen=True, slots=True)
class Batch:
    """One batch, as `mdm_work.batch` holds it. Actor names (`maker`, `checker`, `stop_requested_by`) are
    workbench actors, never values; every other text is a code, an ID, a key or the signature."""

    batch_id: str  # BAT-<20 hex>
    entity: str
    kind: str  # BATCH_KINDS
    status: str  # BATCH_STATUSES
    maker: str  # the steward who drew it (a compensation: who asked for it)
    maker_role: str
    created_at: datetime
    updated_at: datetime
    rule_version: int | None = None  # the match rule version of its group
    signature: str = ""  # its group's signature ('' for none)
    signature_key: str | None = None  # SIG-<16 hex>: its group, for a link batch
    bulk_band: str | None = None  # bulk:<16 hex>: its signature's bulk rights, for a link batch
    persona: bool = False
    checker: str | None = None  # the second steward who confirmed it
    checker_role: str | None = None
    checked_at: datetime | None = None
    population: int = 0  # the reviews drawn into it
    sample_size: int = 0  # the forced sample it needs
    decisions: int = 0  # the reviews it links (or reverses), once prepared
    chunks: int = 0  # the chunks planned
    chunks_committed: int = 0
    rows_committed: int = 0  # published rows its chunks wrote
    entry_id: str | None = None  # its tray entry, while staged and after
    compensates: str | None = None  # a compensation: the batch it reverses
    compensated_by: str | None = None  # the compensation open on it, or committed
    compensate_reason: str | None = None  # COMPENSATE_REASONS
    stop_requested_by: str | None = None
    stop_requested_at: datetime | None = None
    not_before: datetime | None = None  # the throttle: the next chunk commits no earlier
    attempts: int = 0  # failed flush passes in a row; a chunk that commits sets it back to 0
    outcome: str | None = None  # BATCH_OUTCOMES
    figures: Mapping[str, Any] = field(default_factory=dict)  # safe numbers and codes only
    planning_version: int = 0  # the last commit version its plan read
    staged_at: datetime | None = None
    finished_at: datetime | None = None
    stop_requested_role: str | None = None  # the role of the steward who asked it to stop


@dataclass(frozen=True, slots=True)
class BatchItem:
    """One review of a batch, as `mdm_work.batch_item` holds it: its role, its status, and, once prepared,
    the plan frozen for it."""

    batch_id: str
    task_id: str
    role: str  # ITEM_ROLES
    status: str  # SAMPLE_OUTCOMES for a sample review, BULK_STATUSES for a bulk one, SPLIT_STATUS when split
    source: SourceKey  # the review's record
    event_id: str | None  # the record's event the review was drawn (or planned) at
    target: str | None = None  # the golden record its case suggests (a master ID)
    target_version: int | None = None  # the target's row version when prepared
    score: float | None = None
    band: str | None = None
    stratum: str = ""  # the source-system pair of the record and its best candidate member: "crm/hr"
    draw: int = 0  # its keyed draw value
    position: int = 0  # its order in the batch, unique within it: due order, then planned order
    review: bool = False  # drawn for blind review when its link commits
    changes: tuple[str, ...] = ()  # the attribute names the target's golden values change
    chunk_no: int | None = None
    change_set_id: str | None = None
    entry_id: str | None = None  # the tray entry that decided a sample review
    split_on: str | None = None  # a disagreeing sample review: the comparison its decision named, or `all`
    split_applied: bool = False  # set once the split it names has applied
    reason: str | None = None  # ITEM_REASONS, or `split:<comparison>`
    updated_at: datetime | None = None  # stamped by the store when None


@dataclass(frozen=True, slots=True)
class BatchChunk:
    """One committed chunk of a batch: its own change set and commit version."""

    batch_id: str
    chunk_no: int
    change_set_id: str  # CS-<the batch's 20 hex>-<n>
    commit_version: int | None  # None when it published nothing
    items: int  # the reviews it committed
    rows: int  # the published rows it wrote
    committed_at: datetime
    compensated_by: str | None = None  # the compensation that undid it
    compensated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class BatchChunkWrite:
    """The `WorkWrites.batch` of one chunk, written in its commit's own transaction (`SqlStore.apply_work`).

    Before any other write the batch row is held (`staged` or `committing`, and no stop asked), and for a
    link its bulk rights too; after the published rows, the chunk row is inserted exactly once, its reviews
    move from `planned` to `committed`, the batch counts the chunk, and its lock subjects are released.
    `change_set_id`, `commit_version` and `rows` stay `None`/`None`/0 until `CommitService._map_work` stamps
    them."""

    batch_id: str
    chunk_no: int
    kind: str  # BATCH_KINDS
    entry_id: str  # the batch's tray entry
    first: bool  # the first chunk: the batch becomes `committing`
    last: bool  # the last chunk: the batch becomes `committed` and frees its group
    task_ids: tuple[str, ...]  # the reviews this chunk commits
    subjects: tuple[str, ...] = ()  # the tray-lock subjects it releases
    bulk_band: str | None = None  # held at commit, for a link batch only
    signature: str | None = None  # the bulk row's signature, for a link batch only
    seconds_per_row: float = 0.0  # 3,600 ÷ the throttle; 0 when it is off
    compensates: str | None = None  # a compensation: the original batch
    undoes_chunk: int | None = None  # a compensation: the original chunk it undoes
    change_set_id: str | None = None
    commit_version: int | None = None
    rows: int = 0


@dataclass(frozen=True, slots=True)
class BatchSampleWrite:
    """A forced-sample outcome (`agreed`, `disagreed` or `void`), with the comparison a disagreeing decision
    named, written in the sample decision's own transaction. It never raises: a sample review split or voided
    meanwhile stays as it is."""

    batch_id: str
    task_id: str
    status: str  # SAMPLE_OUTCOMES
    entry_id: str | None  # the tray entry that decided it
    split_on: str | None = None  # a comparison's name, or SPLIT_ALL


def new_batch_id() -> str:
    """A random batch ID: `BAT-` and 20 hexadecimal characters."""
    return "BAT-" + secrets.token_hex(10)


def signature_key(entity: str, rule_version: int | None, signature: str | None) -> str | None:
    """A signature group's key, `SIG-` and the first 16 hexadecimal characters of
    sha256(`entity|rule_version|signature`); None for no signature (`''` or None) or no rule version. The
    key is safe text, for addresses, component IDs, evidence and stores; the signature text stays in the
    columns the store writes through `safe_signature`."""
    if not signature or rule_version is None:
        return None
    return "SIG-" + hashlib.sha256(f"{entity}|{rule_version}|{signature}".encode()).hexdigest()[:16]


def chunk_change_set_id(batch_id: str, number: int) -> str:
    """The change set ID of a batch's chunk: `CS-` and the batch's 20 hexadecimal characters, then `-<n>`, so
    a batch's chunks share a stem and each has its own ID."""
    return "CS-" + batch_id[4:] + f"-{number}"
