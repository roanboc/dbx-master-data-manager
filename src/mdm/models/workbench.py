"""The workbench's view of the hub: inbox rows, a task's case, staged decisions, record lookups.

Services build these for one actor; every value a person could read is masked by the actor's role
unless revealed, so the workbench renders what it gets and formats nothing personal itself (decision
20). No field holds a label built from free text: labels are built at read time from decisions, IDs
and source keys. Frozen dataclasses with slots, no input or output.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

#: inbox views (the left rail); kinds come from tasks.TASK_KINDS
TASK_VIEWS = ("mine", "team", "breaching", "snoozed", "escalated")
#: the quality samples' own view (story 3.2): samples never appear in the task views above
SAMPLES_VIEW = "samples"
ALL_VIEWS = (*TASK_VIEWS, SAMPLES_VIEW)
#: decisions a steward stages in the undo tray in story 3.1 (detach joins in story 3.6, create in 3.7)
DECISIONS = (
    "link",  # link the task's source record to the chosen golden record
    "not_a_match",  # decline every candidate shown; the record goes back to arrival
    "keep_apart",  # two golden records are not the same (possible duplicate or golden-pair review)
    "approve_update",  # take a held update into the golden record (the link stays)
    "reject_update",  # leave the golden record as it is; release the hold
    "keep_orphan",  # keep a golden record with no source record
    # the matcher's checkpoint (story 3.2)
    "blind_link",  # a quality sample: the record belongs to the chosen golden record (a pair: they are the same)
    "blind_none",  # a quality sample: it belongs to none of those shown (a pair: they are not the same)
    "keep_decision",  # a blind review disagreed: keep the first decision
)
#: actions that are not staged (claiming a snoozed task wakes it)
TASK_ACTIONS = ("claim", "release", "snooze", "escalate")
TRAY_STATUSES = ("staged", "committed", "undone", "failed")
LABELS = ("match", "not_a_match", "keep_apart")
AGREEMENT = ("agree", "partial", "disagree", "missing", "")  # "" = no comparison on the attribute
ESCALATION_REASONS = ("second_opinion", "outside_my_data", "source_defect", "policy_question")
SNOOZE_HOURS = (1, 4, 24)
#: why a person reveals personal values: codes only, so no free text reaches the access log (decision 20)
REVEAL_REASONS = ("deciding_task", "source_defect", "subject_request", "audit_check")
#: the shapes of a task's case (DecisionService.case), which decide its columns and offered decisions
CASE_SHAPES = (
    "source",
    "golden_pair",
    "held_update",
    "held_new",
    "golden",
    "information",
    "blind",  # a quality sample of a record: decided without the first decision
    "blind_pair",  # a quality sample of two golden records kept apart
    "disputed",  # a blind review placed a record differently from the first decision
    "disputed_pair",  # a blind review found two golden records kept apart the same
)
#: the hours of a kind the service levels do not name
FALLBACK_SERVICE_HOURS = 24


@dataclass(frozen=True, slots=True)
class ServiceLevels:
    """Hours to decide a task, per kind; the commit path stamps each open task's due time."""

    hours: tuple[tuple[str, int], ...]

    def due(self, kind: str, created_at: datetime) -> datetime:
        return created_at + timedelta(hours=dict(self.hours).get(kind, FALLBACK_SERVICE_HOURS))


@dataclass(frozen=True, slots=True)
class TaskQuery:
    """What the store selects for an inbox page or a count."""

    now: datetime
    entity: str | None = None
    kind: str | None = None
    mine: str | None = None  # an actor name: unclaimed, lapsed, or claimed by this actor
    lapsed_before: datetime | None = None  # claims taken before this have lapsed
    snoozed: bool | None = False  # False: hide snoozed; True: only snoozed; None: both
    breaching: bool = False  # due time passed
    escalated: bool | None = None  # True: only escalated
    claimed_by: str | None = None  # an actor name: only the tasks whose claim by this actor still runs
    exclude_kind: str | None = None  # leave this kind out (the task views leave quality samples out)
    # an actor name: leave out the samples this actor decided first, or confirmed as a batch's second steward
    not_first_decider: str | None = None
    # signature batches (story 3.3)
    signature_key: str | None = None  # SIG-<16 hex>: only the reviews of this signature group
    batch_id: str | None = None  # BAT-<20 hex>: only the forced-sample reviews of this batch
    grouped: bool = False  # only the reviews that carry a signature group's key


@dataclass(frozen=True, slots=True)
class StagedRef:
    entry_id: str
    decision: str
    label: str  # "Link crm:C000123 to ORG-000123", built at read time from IDs
    deadline: datetime
    mine: bool
    batch_id: str | None = None  # the review waits as part of this batch (story 3.3)


@dataclass(frozen=True, slots=True)
class TaskRow:
    task_id: str
    entity: str
    kind: str
    kind_label: str  # "Review", "Possible duplicate", "Held update", …
    title: str  # the subject's display name, masked by role
    subject: str  # "crm:C000123", or "ORG-000123 · ORG-004410"
    score: float | None
    band: str | None  # auto | review | distinct
    suggestion: str  # "Link to ORG-000123", "Approve or reject", "Keep apart or merge"
    reason: str  # plain words: "hinges on registered ID", "critical: family name"
    due_at: datetime | None
    breaching: bool
    claimed_by: str | None  # "you", "Data steward (persona)", or the claimant's name
    claim_expires: datetime | None
    snoozed_until: datetime | None
    escalated: bool
    staged: StagedRef | None
    kept_apart: bool = False  # a cannot-link rule keeps the records apart, whatever the score says


@dataclass(frozen=True, slots=True)
class TaskPage:
    rows: tuple[TaskRow, ...]
    after: tuple[str, str] | None  # the cursor of the next page: (ISO due time, task ID); None = last page


@dataclass(frozen=True, slots=True)
class ViewCounts:
    """Capped counts: a value of capacity.COUNT_CAP means "999+"."""

    views: Mapping[str, int]  # ALL_VIEWS -> count
    kinds: Mapping[str, int]  # TASK_KINDS -> open count, within the entity filter
    claimed: int = 0  # of My queue, the tasks the actor holds a running claim on
    samples_breaching: int = 0  # of the Quality samples view, those past their service level
    # signature batches (story 3.3), each capped
    alike: int = 0  # open reviews that carry a signature group's key
    batches_to_confirm: int = 0  # batches waiting for the actor as their second steward
    # the actor's staged entries and committing batches, those they confirmed included: the header learns of a
    # batch confirmed while its maker's tray is not polling
    tray_live: int = 0


@dataclass(frozen=True, slots=True)
class BreakerView:
    """What the workbench says about an entity's demoted automatic band: the trigger, since when, and safe
    numbers only (agreement: `agreed`, `reviewed`, `threshold`; volume: `arrivals`, `mean`, `multiple`,
    `days`, `hour`)."""

    entity: str
    trigger: str  # agreement | volume
    since: datetime
    figures: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Health:
    open_tasks: int  # capped
    breaching: int  # capped
    staged: int  # capped; every steward's staged decisions
    last_commit_version: int
    last_commit_at: datetime | None
    last_arrival_at: datetime | None
    arrival_read: int | None  # rows read by the last arrival run
    arrival_tasks: int | None  # tasks it opened
    arrival_automatic: float | None  # share of its settled records the matcher committed, 0..1
    paused: tuple[BreakerView, ...] = ()  # the entities whose automatic linking the quality breaker paused


@dataclass(frozen=True, slots=True)
class CompareRow:
    attribute: str
    label: str  # "Registered ID"
    values: tuple[str | None, ...]  # masked display text per column, the subject first; None = missing
    agreement: tuple[str, ...]  # per candidate column, AGREEMENT
    critical: bool
    personal: bool
    changed: bool = False  # held updates: the value differs from the approved one


@dataclass(frozen=True, slots=True)
class WaterfallStep:
    label: str  # "Prior", "Name ≈", "Registered ID ∅"
    comparison: str | None  # None for the prior
    level_words: str  # "the same", "similar", "different", "missing"
    weight: float
    start: float  # running total before this step
    end: float  # running total after it


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _in_sentence(label: str) -> str:
    """An attribute label inside a sentence: "Phone" -> "phone", "Registered ID" -> "registered ID"."""
    if len(label) > 1 and label[1].islower():
        return label[0].lower() + label[1:]
    return label


def _joined(words: tuple[str, ...]) -> str:
    """ "name", "name and phone", "name, phone and email"."""
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


@dataclass(frozen=True, slots=True)
class Impact:
    xrefs_added: int = 0
    xrefs_removed: int = 0
    golden_changed: tuple[str, ...] = ()  # attribute labels whose golden value changes
    records_created: int = 0
    ids_retired: int = 0
    relationships_changed: int = 0
    held_released: int = 0

    def sentence(self) -> str:
        """'+1 cross-reference · golden phone changes · no ID retired' (built from counts and attribute
        labels only).

        Parts in a fixed order: records created, cross-references added and removed, golden values that
        change, relationships, holds released, IDs retired. A link into an existing record also says that
        no ID retires, since only a merge retires one; nothing at all reads "no change to published
        records"."""
        parts: list[str] = []
        if self.records_created:
            parts.append(_plural(self.records_created, "record created", "records created"))
        if self.xrefs_added:
            parts.append("+" + _plural(self.xrefs_added, "cross-reference", "cross-references"))
        if self.xrefs_removed:
            parts.append("−" + _plural(self.xrefs_removed, "cross-reference", "cross-references"))
        if self.golden_changed:
            words = tuple(_in_sentence(label) for label in self.golden_changed)
            parts.append(f"golden {_joined(words)} {'changes' if len(words) == 1 else 'change'}")
        if self.relationships_changed:
            parts.append(_plural(self.relationships_changed, "relationship changes", "relationships change"))
        if self.held_released:
            parts.append(_plural(self.held_released, "hold released", "holds released"))
        if self.ids_retired:
            parts.append(_plural(self.ids_retired, "ID retired", "IDs retired"))
        elif self.xrefs_added and not self.records_created:
            parts.append("no ID retired")
        return " · ".join(parts) if parts else "no change to published records"


@dataclass(frozen=True, slots=True)
class PreviewRow:
    label: str
    now: str | None  # masked
    after: str | None  # masked
    changed: bool


@dataclass(frozen=True, slots=True)
class Preview:
    master_id: str | None  # None: a record to be created
    rows: tuple[PreviewRow, ...]
    impact: Impact


@dataclass(frozen=True, slots=True)
class Candidate:
    index: int  # 1..CANDIDATES_SHOWN, the key that chooses it
    master_id: str
    title: str  # masked display name
    score: float
    band: str
    member: str  # the best-scoring member's source key
    steps: tuple[WaterfallStep, ...]  # prior first, then each comparison in rule order
    total: float  # the pair's weight (log2 odds)
    thresholds: tuple[float, float]  # (lower, upper) band edges as weights
    flip: tuple[str, ...]  # plain sentences: what would move the band, up or down
    hard_rule: str | None  # plain sentence when a hard rule decided
    blocked_by: str | None  # plain sentence when a cannot-link rule blocks it
    signature: str
    rule_version: int
    preview: Preview | None  # the golden values after linking to this candidate, and the impact line
    blocked_rule: str | None = None  # the code of the rule that blocks it: "cannot_link:registered_id"
    # (comparison, the weight the change `flip` names would add): the waterfall draws a missing comparison's
    # as a dashed bar of that length
    what_if: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True, slots=True)
class Choice:
    """One golden record a blind review offers: its masked title, and no score or band."""

    index: int  # 1..CANDIDATES_SHOWN, the key that chooses it
    master_id: str
    title: str


@dataclass(frozen=True, slots=True)
class Mark:
    """One comparison of a signature, in words: its label, its mark and what the mark means."""

    comparison: str  # the comparison's name: "birth_date"
    label: str  # the entity model's label: "Birth date"
    mark: str  # one of =, ≈, ≠, ∅
    words: str  # "the same", "similar", "different", "missing"


@dataclass(frozen=True, slots=True)
class SampleLine:
    """The forced-sample line of the decide pane (story 3.3): the review belongs to a batch's forced sample,
    so every decision shows at equal weight, and a decision that disagrees names the comparison that misled.
    `choices` are the pattern's comparisons in rule order, which the pane offers as "Which comparison
    misled?", beside "Every alike review in this batch"."""

    batch_id: str
    position: int  # this review's place among the sample's, 1-based
    size: int  # the sample's size
    decided: int  # the sample reviews decided so far
    choices: tuple[Mark, ...]


@dataclass(frozen=True, slots=True)
class Action:
    decision: str  # DECISIONS or TASK_ACTIONS or "undo"
    label: str  # "Link to ORG-000123", "Not a match", "Approve the update"
    key: str | None  # "L", "N", "A", "R", "S", "E", "C", "U"
    enabled: bool
    why_not: str | None  # plain sentence, shown as visible text when not enabled
    target: str | None = None  # the master ID a link names


@dataclass(frozen=True, slots=True)
class TaskCase:
    row: TaskRow
    reason_text: str  # the reason, in a full sentence
    shape: str  # CASE_SHAPES: source | golden_pair | held_update | held_new | golden | information
    columns: tuple[str, ...]  # column headers: "Arriving · crm:C000123", "1 · ORG-000123" (candidate 1)
    compare: tuple[CompareRow, ...]
    candidates: tuple[Candidate, ...]  # at most capacity.CANDIDATES_SHOWN, best first, each with its preview
    default_candidate: str | None  # the candidate shown first; None in a close call (a choice is required)
    close_call: bool
    preview: Preview | None  # held and golden shapes: the preview of the offered decision
    actions: tuple[Action, ...]
    notice: str | None  # "Merging needs a second steward to check it; that is not on screen yet."
    masked: bool  # at least one value is masked
    revealable: tuple[str, ...]  # attributes this actor may reveal (empty for a consumer)
    staged: StagedRef | None
    claimed_by: str | None
    event_id: str | None  # the record's event the case was built on (a code); None for golden subjects
    # the task's version the case was built on (ISO time of its last change): what a decision on a task
    # with no source record is checked against, as `event_id` is for one with a source record
    task_version: str | None = None
    # the matcher's checkpoint (story 3.2)
    choices: tuple[Choice, ...] = ()  # a blind case: the golden records offered, in master-ID order
    blind: bool = False  # the first decision, its score, band and suggestion stay hidden
    paused: BreakerView | None = None  # the quality breaker paused automatic linking for this entity
    sample: SampleLine | None = None  # an open forced-sample review of a batch (story 3.3)


@dataclass(frozen=True, slots=True)
class Revealed:
    """Values in clear for one reveal; rendered once, never stored in the browser or cached."""

    compare: tuple[CompareRow, ...]
    logged: int  # access-log rows written
    columns: tuple[str, ...] = ()  # the column headers, from the same read as the values


@dataclass(frozen=True, slots=True)
class Staging:
    """What DecisionService.check hands the tray: the decision is allowed, and what to keep."""

    task_id: str
    entity: str
    decision: str
    target: str | None
    subject: Mapping[str, Any]  # safe_detail only: source, candidates, score, band, rule_version, task kind
    signature: str | None
    event_id: str | None  # the event the steward saw (checked equal to the current one)
    locks: tuple[str, ...]  # "task:<id>", "source:<entity>:<system>:<key>", "golden:<master ID>"


@dataclass(frozen=True, slots=True)
class TrayEntry:
    entry_id: str  # "TR-" + token_hex(10)
    task_id: str
    entity: str
    decision: str
    target: str | None
    subject: Mapping[str, Any]
    signature: str | None
    actor: str
    actor_role: str
    persona: bool
    event_id: str | None
    planning_version: int
    staged_at: datetime
    deadline: datetime
    status: str  # TRAY_STATUSES
    attempts: int = 0
    settled_at: datetime | None = None
    change_set_id: str | None = None
    commit_version: int | None = None  # read-time join through mdm_audit.change_set; never stored here
    # a code: committed | undone | record_changed | task_closed | target_changed | not_settled | internal | …
    outcome: str | None = None


@dataclass(frozen=True, slots=True)
class TrayView:
    """One line of the tray popover, for its own steward."""

    entry_id: str
    task_id: str
    decision: str
    label: str  # built at read time from the decision, the source key and the master ID
    deadline: datetime
    status: str
    outcome: str | None
    commit_version: int | None
    settled_at: datetime | None = None
    # signature batches (story 3.3)
    batch_id: str | None = None  # a batch entry: its batch
    progress: tuple[int, int] | None = None  # a batch entry: (chunks committed, chunks planned)
    mine: bool = True  # False for a batch entry the actor confirmed as its second steward
    second_steward: str | None = None  # a batch entry that has one: the confirming steward's role


@dataclass(frozen=True, slots=True)
class TraySettlement:
    """Written in the commit's own transaction; the store raises Conflict when the entry is no longer staged.
    A blind answer settles with the outcome `agreed` or `disagreed`. A batch's first chunk settles its entry
    `committed` with outcome `committing` and `keep_locks`, so the locks of its later chunks stay held."""

    entry_id: str
    status: str
    change_set_id: str | None
    outcome: str
    keep_locks: bool = False  # leave the entry's locks: the batch releases them chunk by chunk


@dataclass(frozen=True, slots=True)
class MatchLabel:
    entity: str
    left_ref: str  # a source key "crm:C000123", or the lower master ID of a golden pair
    right_ref: str  # a master ID
    label: str  # LABELS
    rule_version: int | None
    score: float | None
    band: str | None
    signature: str | None
    task_id: str | None
    entry_id: str | None
    decided_by: str
    decided_role: str
    decided_at: datetime


@dataclass(frozen=True, slots=True)
class FlushReport:
    committed: int = 0
    failed: int = 0
    requeued: int = 0
    skipped_busy: bool = False
    outcomes: Mapping[str, int] = field(default_factory=dict)  # outcome code -> count
    # signature batches (story 3.3): the chunks this pass committed, and the batches it finished
    chunks: int = 0
    batches_finished: int = 0


# ---------------------------------------------------------------------------------------------- signature batches
# (story 3.3): the Alike reviews page, the batch page and its rows. Safe codes, counts and masked text only; a
# signature reaches a view as its marks, never as a value.


@dataclass(frozen=True, slots=True)
class LabelHistory:
    """The latest label on each pair that carries a signature, under any rule version; each count capped."""

    matched: int  # "Linked"
    not_matched: int  # "Not a match"


@dataclass(frozen=True, slots=True)
class AgreementView:
    """The lifetime blind-review agreement of samples with a signature, of every origin, and by origin
    (`automated`, `steward`, `batch`): (agreed, reviewed)."""

    agreed: int
    reviewed: int
    by_origin: Mapping[str, tuple[int, int]]


@dataclass(frozen=True, slots=True)
class BulkRightView:
    """A signature whose bulk rights the quality breaker withdrew: since when, and safe figures only
    (`agreed`, `reviewed`, `threshold`, `window`). No restore control goes with it: a data owner restores on
    the command line."""

    entity: str
    key: str  # bulk:<16 hex>
    since: datetime
    figures: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class GroupRow:
    """One signature group on the Alike reviews page."""

    group_key: str  # SIG-<16 hex>
    entity: str
    entity_label: str
    rule_version: int
    marks: tuple[Mark, ...]
    count: int  # capped: at capacity.COUNT_CAP it reads "999+"
    labels: LabelHistory
    agreement: AgreementView
    withdrawn: BulkRightView | None
    too_small: bool  # too few to link together
    batch_id: str | None = None  # the group's open batch
    batch_status: str | None = None
    batch_words: str | None = None  # the open batch's stage in words


@dataclass(frozen=True, slots=True)
class BatchLine:
    """One batch named in a list: the batches a second steward may confirm."""

    batch_id: str
    entity: str
    entity_label: str
    kind: str  # link | compensate
    decisions: int
    maker_label: str  # the maker's role, in words


@dataclass(frozen=True, slots=True)
class GroupList:
    """The Alike reviews page: the largest groups of the window, and the batches the actor may confirm."""

    groups: tuple[GroupRow, ...]
    window: int  # the open reviews due soonest that were grouped (capacity.GROUP_WINDOW)
    older_rules: int  # capped: open reviews scored under an earlier rule version, not grouped
    to_confirm: tuple[BatchLine, ...]


@dataclass(frozen=True, slots=True)
class SampleReview:
    """One forced-sample review on the batch page: an open one links to its task, a decided one to its source
    record."""

    task_id: str
    source: str  # "crm:C000123"
    title: str  # masked
    stratum_label: str  # "crm and hr"
    status: str  # open | agreed | disagreed | void
    words: str  # the status in words
    open: bool


@dataclass(frozen=True, slots=True)
class SplitLine:
    """One disagreeing sample review and the split it named: the comparison ("birth date", or "every alike
    review"), and the reviews its split took, or None while the split waits to apply."""

    task_id: str
    source: str
    decision_words: str
    on_label: str
    count: int | None


@dataclass(frozen=True, slots=True)
class BatchSummary:
    """The summary line of a prepared batch, and the reviews left out by reason."""

    xrefs: int
    golden: int
    chunks: int
    rows: int  # published rows, at most capacity.COMMIT_CHUNK_ROWS a chunk
    reviews: int  # drawn for blind review
    left_out: Mapping[str, int]  # ITEM_REASONS code -> count


@dataclass(frozen=True, slots=True)
class BatchProgress:
    """A committing batch: its chunks, and when the throttle lets the next one commit."""

    chunks: int
    chunks_committed: int
    rows_committed: int
    not_before: datetime | None
    stop_requested: bool


@dataclass(frozen=True, slots=True)
class BatchView:
    """The batch page: its stage, its forced sample, its splits, its summary and progress, and its actions
    (codes of `models.batch.PAGE_ACTIONS`)."""

    batch_id: str
    kind: str
    entity: str
    entity_label: str
    group_key: str | None
    marks: tuple[Mark, ...]
    status: str
    maker_label: str  # "you", or the role
    checker_label: str | None
    population: int
    sample_size: int
    sample: tuple[SampleReview, ...]
    decided: int
    agreed: int
    disagreed: int
    waiting: int
    splits: tuple[SplitLine, ...] = ()
    summary: BatchSummary | None = None
    progress: BatchProgress | None = None
    entry_id: str | None = None  # while staged, for Undo
    deadline: datetime | None = None
    withdrawn: BulkRightView | None = None
    actions: tuple[Action, ...] = ()
    notice: str | None = None
    compensates: str | None = None
    compensated_by: str | None = None
    outcome: str | None = None
    commits: tuple[int, int] | None = None  # the first and last commit versions
    finished_at: datetime | None = None
    # the reviews by item status: committed, failed, released, kept, excluded, compensated
    counts: Mapping[str, int] = field(default_factory=dict)
    undo_until: datetime | None = None  # the last moment its committed links can be compensated
    undone_by: tuple[str, ...] = ()  # the compensations that undid any of its chunks


@dataclass(frozen=True, slots=True)
class BatchRow:
    """One review of a prepared batch: the record, its target, the impact line and, on demand, the
    before-and-after table, masked by role."""

    task_id: str
    source: str
    title: str  # masked
    target: str | None
    target_title: str | None  # masked
    impact: Impact
    preview: tuple[PreviewRow, ...]
    joins: int  # other rows of this batch that join the same target
    status: str
    reason: str | None
    changed_since: bool  # the target's row version differs from the one planned
    chunk_no: int | None


@dataclass(frozen=True, slots=True)
class BatchRowPage:
    """One page of a batch's rows, keyed by position."""

    rows: tuple[BatchRow, ...]
    after: int | None  # the position to read after for the next page; None = last page


@dataclass(frozen=True, slots=True)
class Resolution:
    kind: str  # golden | source | unknown
    entity: str | None
    master_id: str | None
    source: str | None  # "crm:C000123"
    notice: str | None  # "ORG-003307 was merged into ORG-000123; showing the survivor."


@dataclass(frozen=True, slots=True)
class RecordHeader:
    entity: str
    master_id: str
    title: str  # masked
    status: str
    survivor_id: str | None
    retired_ids: tuple[str, ...]  # IDs merged into this record (chains collapsed)
    open_tasks: tuple[str, ...]  # task IDs of this record and its members
    commit_version: int
    member_count: int
    relationship_count: int


@dataclass(frozen=True, slots=True)
class ValueView:
    attribute: str
    label: str
    value: str | None  # masked unless revealed
    masked: bool
    personal: bool
    critical: bool
    source: str | None  # the winner: "finance:F000010", or "steward"
    # from provenance: source_trust | recency | completeness | frequency | pin | only | keyed_union |
    # tie_break; None for rows written before it was recorded
    decided_by: str | None
    chip: str | None  # "finance · trust rank · 2 d"; "Steward pin · until 30 Nov"
    age_days: int | None
    pinned_until: datetime | None


@dataclass(frozen=True, slots=True)
class RunnerUp:
    source: str
    value: str | None  # masked unless revealed
    age_days: int | None
    occurred_at: datetime | None = None  # the source record's latest event: shown when ages tie


@dataclass(frozen=True, slots=True)
class ValueWhy:
    attribute: str
    label: str
    sentence: str  # "Survivorship rules v1 for Phone use source trust, then recency. …"
    winner: RunnerUp | None
    runners_up: tuple[RunnerUp, ...]
    strategies: tuple[str, ...]
    decided_by: str | None
    rule_version: int | None


@dataclass(frozen=True, slots=True)
class MemberView:
    source: str  # "crm:C000123"
    system: str
    key: str
    trust: int | None  # the source's trust rank for the entity
    occurred_at: datetime | None  # the latest version's event time
    source_version: int | None
    held: bool
    rule_failures: tuple[str, ...]  # "email: bad_pattern"
    open_task: str | None


@dataclass(frozen=True, slots=True)
class TimelineEvent:
    commit_version: int
    change_seq: int
    at: datetime | None
    # created | updated | merged | retired | unmerged | reinstated | remapped; "decided" for a steward's
    # decision that published nothing (commit_version and change_seq are 0 then)
    kind: str
    headline: str  # "Values updated: phone, email"; "ORG-003307 merged into this record"
    parts: tuple[str, ...]
    actor: str  # "Automated matcher" or a role label; never a person
    automated: bool
    # "Applied automatically under rules v1 · from finance:F000123" or "Data steward"; "after a steward's
    # not-a-match" when the evidence names one. The policy's clauses stay in the audit.
    authority: str
    published: bool = True  # False: a decision audited with nothing published


@dataclass(frozen=True, slots=True)
class TimelinePage:
    events: tuple[TimelineEvent, ...]
    before: tuple[int, int] | None  # the cursor of the next (older) page


@dataclass(frozen=True, slots=True)
class RelationshipView:
    rel_type: str
    label: str  # "works at", "subsidiary of"; "employs" when inbound
    direction: str  # out | in
    other_entity: str
    other_master_id: str
    other_title: str  # masked
    valid_from: str | None
    valid_to: str | None
    status: str
    sources: tuple[str, ...]  # every asserting source record


@dataclass(frozen=True, slots=True)
class SourceView:
    entity: str
    source: str
    title: str  # masked
    status: str  # active | deleted
    linked_to: str | None
    held: bool
    values: tuple[ValueView, ...]  # current standardised values, masked; chip = None
    approved_differs: tuple[str, ...]  # attributes whose approved value differs (a held update)
    # (source version, event time, landing sequence), newest last
    versions: tuple[tuple[int | None, datetime, int], ...]
    open_tasks: tuple[str, ...]
    rule_failures: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HubBadges:
    engine: str  # "DuckDB", "Postgres", "Lakebase"
    assistant: str  # "stub" or "endpoint"
    local: bool
    entities: tuple[str, ...]  # published entities
