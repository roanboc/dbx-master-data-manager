"""The automated matcher's checkpoint: quality samples, their blind review, agreement and the quality breaker.

A quality sample is a committed decision drawn for blind review: the automated matcher's links and creates,
and a steward's link, "not a match" and keep apart. A second steward answers where the record belongs without
seeing the first decision; the answer agrees with it or not. Agreement is kept per entity, origin, band and
signature. The quality breaker demotes an entity's automatic band when the latest automated reviews agree
too rarely, or when arrivals in an hour spike, and only a data owner restores it (decision 3). A signature's
bulk rights (story 3.3) live beside the automatic band, one row per entity and signature under a band
`bulk:<16 hex>`: only blind review of that signature's batch samples withdraws them, and only a data owner
restores them.

Every text here is a code, an ID, a source key or a signature: never a value. Frozen dataclasses with slots,
no input or output.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from mdm.models.records import SourceKey

#: who made the decision a sample measures: the automated matcher, a steward, or a steward's batch (story 3.3)
SAMPLE_ORIGINS = ("automated", "steward", "batch")
#: the decisions drawn: the automated matcher's, then a steward's
SAMPLE_DECISIONS = ("auto_link", "auto_create", "hint_link", "link", "not_a_match", "keep_apart")
AUTOMATED_DECISIONS = ("auto_link", "auto_create", "hint_link")
STEWARD_DECISIONS = ("link", "not_a_match", "keep_apart")
SAMPLE_STATUSES = ("open", "agreed", "disagreed", "void")
#: the blind answer "belongs to none of these" (for a pair: "they are not the same")
NONE_ANSWER = "none"
BREAKER_STATES = ("normal", "demoted")
BREAKER_TRIGGERS = ("agreement", "volume")
#: why a data owner restores a demoted band: codes only, so no free text reaches the audit
RESTORE_REASONS = ("cause_fixed", "false_alarm", "load_expected")
#: the band the breaker demotes
AUTO_BAND = "auto"
#: the most automated samples one entity may hold open at once (the bound of MDM_SAMPLE_OPEN_CAP)
SAMPLE_OPEN_CAP_MAX = 10_000
#: the most reviews the agreement trigger reads (the bound of MDM_BREAKER_WINDOW)
BREAKER_WINDOW_CAP = 1_000
#: the least agreement MDM_BREAKER_AGREEMENT may ask for on a shared store: below it the trigger is off in effect
SHARED_AGREEMENT_FLOOR = 0.5
#: the shortest MDM_SAMPLE_KEY a shared store accepts
SAMPLE_KEY_MIN = 16
#: the band prefix of a signature's bulk rights (story 3.3): `bulk:` and 16 hexadecimal characters
BULK_PREFIX = "bulk:"
#: a bulk-rights band, as a data owner names it to restore it
BULK_BAND_RE = re.compile(r"^bulk:[0-9a-f]{16}\Z")
#: why a data owner restores a signature's bulk rights: a load expected never withdraws them
BULK_RESTORE_REASONS = ("cause_fixed", "false_alarm")
#: the bulk trigger's defaults (proposed): the latest batch samples it reads, the reviews it needs, and the
#: agreement it asks for
BULK_WINDOW = 50
BULK_MIN_SAMPLES = 5
BULK_AGREEMENT = 0.95


@dataclass(frozen=True, slots=True)
class QualitySample:
    """One committed decision drawn for blind review, and its answer once given."""

    sample_id: str
    entity: str
    origin: str  # SAMPLE_ORIGINS
    decision: str  # SAMPLE_DECISIONS
    source: SourceKey | None  # the record; None for a keep-apart pair
    master_ids: tuple[str, ...]  # the keep-apart pair, else ()
    event_id: str | None  # the record's event the decision was made on
    target: str | None  # the golden record the decision placed the record in (a master ID, or a `new:` ref)
    declined: tuple[str, ...]  # the golden records a "not a match" declined
    band: str  # the band of the first decision's pair; "" when there is none
    signature: str  # the comparison signature of that pair; "" when there is none
    score: float | None
    rule_version: int | None
    decided_by: str  # an actor name: the automated matcher, or a steward
    decided_role: str
    decided_at: datetime
    entry_id: str | None  # the tray entry of a steward's decision
    task_id: str  # the task that asks for the blind answer
    drawn_at: datetime
    pair_sources: tuple[str, ...] = ()  # a keep-apart pair's two source keys, for its compare table
    status: str = "open"  # SAMPLE_STATUSES
    answer: str | None = None  # a master ID, or NONE_ANSWER
    reviewed_by: str | None = None
    reviewed_role: str | None = None
    reviewed_at: datetime | None = None
    review_entry_id: str | None = None
    dispute_task_id: str | None = None  # the review a disagreement opened
    checked_by: str | None = None  # a batch sample: the batch's second steward, who may not answer it either


@dataclass(frozen=True, slots=True)
class SampleReview:
    """A blind answer, written in the same transaction as the steward's audited decision."""

    sample_id: str
    entity: str
    origin: str
    band: str
    signature: str
    answer: str  # a master ID, or NONE_ANSWER
    agreed: bool
    reviewed_by: str
    reviewed_role: str
    reviewed_at: datetime
    review_entry_id: str | None
    dispute_task_id: str | None = None


@dataclass(frozen=True, slots=True)
class AgreementRow:
    """Running counts of blind reviews per entity, origin, band and signature."""

    entity: str
    origin: str
    band: str
    signature: str
    reviewed: int
    agreed: int


@dataclass(frozen=True, slots=True)
class BreakerState:
    """An entity's automatic band, or a signature's bulk rights (band `bulk:<16 hex>`): normal or demoted,
    with its last trip and restore."""

    entity: str
    band: str
    state: str  # BREAKER_STATES
    watch_since: datetime  # the volume trigger needs its days of history from here
    trigger: str | None  # BREAKER_TRIGGERS, of the last trip
    figures: Mapping[str, Any]  # safe numbers of the last trip
    tripped_at: datetime | None
    trip_change_set: str | None
    restored_at: datetime | None
    restored_by: str | None
    restored_role: str | None
    restore_reason: str | None  # RESTORE_REASONS
    restore_change_set: str | None
    updated_at: datetime
    signature: str | None = None  # a bulk-rights row's signature; None for the automatic band

    @property
    def demoted(self) -> bool:
        return self.state == "demoted"


@dataclass(frozen=True, slots=True)
class BulkStatus:
    """What `mdm breaker status` prints for one withdrawn signature: its key, the signature (comparison names
    and marks, put into words by the command line), since when, and safe figures only."""

    entity: str
    key: str  # bulk:<16 hex>
    signature: str
    since: datetime
    figures: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class BreakerStatus:
    """What `mdm breaker status` prints for one entity: counts and figures only."""

    entity: str
    state: str  # BREAKER_STATES ("normal" when no row was written yet)
    watch_since: datetime | None
    trigger: str | None
    tripped_at: datetime | None
    trip_change_set: str | None
    figures: Mapping[str, Any]  # of the last trip
    restored_at: datetime | None
    restore_reason: str | None
    restore_change_set: str | None
    agreed: int  # of the latest automated reviews of the automatic band (since the last restore)
    reviewed: int
    bound: float | None  # the one-sided upper bound on agreement; None before any review
    threshold: float
    window: int
    min_samples: int
    samples_open: int  # capped
    samples_overdue: int  # capped
    samples_voided: int  # capped
    arrivals: int  # this hour
    mean: float  # the same hour's mean over the previous `days` days
    multiple: float
    spike_min: int
    days: int
    hour: datetime
    history_ready: bool  # the volume trigger has its days of history
    cap_reached: bool = False  # the automated samples open have reached the cap
    withdrawn: tuple[BulkStatus, ...] = ()  # the signatures whose bulk rights are withdrawn (story 3.3)


def bulk_band(entity: str, signature: str) -> str:
    """The band of a signature's bulk rights: `bulk:` and the first 16 hexadecimal characters of
    sha256(`entity|signature`). It leaves out the rule version, so a withdrawal survives a republish that keeps
    the comparisons' names; it passes `SAFE_TEXT_RE`."""
    return BULK_PREFIX + hashlib.sha256(f"{entity}|{signature}".encode()).hexdigest()[:16]


def sample_id(entity: str, decision: str, subject: str, occasion: str) -> str:
    """`"QS-" + sha256(entity|decision|subject|occasion)[:16]`: deterministic, so a page planned again or a
    flush retried never draws the same decision twice."""
    return "QS-" + hashlib.sha256(f"{entity}|{decision}|{subject}|{occasion}".encode()).hexdigest()[:16]
