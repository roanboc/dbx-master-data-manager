"""Invented instances of every dataclass of `mdm.models.workbench`, for the workbench's view tests.

The views render these before the services exist, and afterwards without a store. Everything is
invented: organisation names from the demo vocabulary's style, cities that do not exist, and person
values only in their masked form (first letter and three stars), as a steward without a reveal sees
them. The Organisation close call carries the weights the probe pinned (79.19 against two namesakes);
the bands are the starter models' (lower 60, upper 90).
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from mdm import capacity
from mdm.models.workbench import (
    Action,
    BreakerView,
    Candidate,
    Choice,
    CompareRow,
    FlushReport,
    Health,
    HubBadges,
    Impact,
    MatchLabel,
    MemberView,
    Preview,
    PreviewRow,
    RecordHeader,
    RelationshipView,
    Resolution,
    Revealed,
    RunnerUp,
    ServiceLevels,
    SourceView,
    StagedRef,
    Staging,
    TaskCase,
    TaskPage,
    TaskQuery,
    TaskRow,
    TimelineEvent,
    TimelinePage,
    TrayEntry,
    TraySettlement,
    TrayView,
    ValueView,
    ValueWhy,
    ViewCounts,
    WaterfallStep,
)
from mdm.services.decisions import (
    BLIND_PAIR_SENTENCE,
    BLIND_SENTENCE,
    NOTICE_BLIND_NONE,
    NOTICE_BREAKER_WAIT,
    NOTICE_DISPUTE_DETACH,
    NOTICE_MERGE,
    NOTICE_OWN_DECISION,
)

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def weight(score: float) -> float:
    """A score (0–100) as a match weight: log2(s / (100 − s))."""
    return math.log2(score / (100.0 - score))


#: the starter models' bands as weights: (lower 60, upper 90)
THRESHOLDS = (weight(60.0), weight(90.0))
SERVICE_LEVELS = ServiceLevels((("review", 8), ("possible_duplicate", 24), ("held", 8), ("orphan", 72)))
TASK_QUERY = TaskQuery(now=NOW, entity="organisation", kind="review", mine="persona:data_steward")

# ---------------------------------------------------------------------------------------------- the inbox

STAGED = StagedRef(
    entry_id="TR-0a1b2c3d4e5f60718293",
    decision="link",
    label="Link crm:C000812 to ORG-000123",
    deadline=NOW + timedelta(seconds=48),
    mine=True,
)

ROW_CLOSE_CALL = TaskRow(
    task_id="TSK-1f2e3d4c5b6a7980",
    entity="organisation",
    kind="review",
    kind_label="Review",
    title="QUORANE WORKS ltd.",
    subject="crm:C000812",
    score=79.19,
    band="review",
    suggestion="Choose ORG-000123 or ORG-004410",
    reason="close call: two namesakes",
    due_at=NOW + timedelta(hours=7, minutes=40),
    breaching=False,
    claimed_by=None,
    claim_expires=None,
    snoozed_until=None,
    escalated=False,
    staged=None,
)
ROW_PERSON_REVIEW = TaskRow(
    task_id="TSK-2a3b4c5d6e7f8091",
    entity="person",
    kind="review",
    kind_label="Review",
    title="K*** B***",
    subject="crm:C001377",
    score=88.66,
    band="review",
    suggestion="Link to PER-000451",
    reason="hinges on birth date",
    due_at=NOW - timedelta(hours=2),
    breaching=True,
    claimed_by="you",
    claim_expires=NOW + timedelta(minutes=6),
    snoozed_until=None,
    escalated=False,
    staged=STAGED,
)
ROW_HELD_UPDATE = TaskRow(
    task_id="TSK-3b4c5d6e7f809102",
    entity="organisation",
    kind="held",
    kind_label="Held",
    title="Velix Supplies Ltd",
    subject="crm:C000419",
    score=None,
    band=None,
    suggestion="Approve or reject",
    reason="critical: name",
    due_at=NOW + timedelta(hours=3),
    breaching=False,
    claimed_by="Coordinating steward (persona)",
    claim_expires=NOW + timedelta(minutes=9),
    snoozed_until=None,
    escalated=False,
    staged=None,
)
ROW_GOLDEN_PAIR = TaskRow(
    task_id="TSK-4c5d6e7f80910213",
    entity="organisation",
    kind="possible_duplicate",
    kind_label="Possible duplicate",
    title="Tessova Labs",
    subject="ORG-000211 · ORG-000388",
    score=86.4,
    band="review",
    suggestion="Keep apart or merge",
    reason="two golden records score in the review band",
    due_at=NOW + timedelta(hours=20),
    breaching=False,
    claimed_by=None,
    claim_expires=None,
    snoozed_until=None,
    escalated=True,
    staged=None,
)
ROW_ORPHAN = TaskRow(
    task_id="TSK-5d6e7f8091021324",
    entity="organisation",
    kind="orphan",
    kind_label="Orphan",
    title="Drava Foundry",
    subject="ORG-000930",
    score=None,
    band=None,
    suggestion="Keep or retire",
    reason="no active member",
    due_at=NOW + timedelta(hours=70),
    breaching=False,
    claimed_by=None,
    claim_expires=None,
    snoozed_until=NOW + timedelta(hours=4),
    escalated=False,
    staged=None,
)
ROW_HELD_NEW = TaskRow(
    task_id="TSK-6e7f809102132435",
    entity="person",
    kind="held",
    kind_label="Held",
    title="C*** D***",
    subject="student_records:S0004410",
    score=None,
    band="distinct",
    suggestion="Create or link",
    reason="new record held",
    due_at=NOW + timedelta(hours=5),
    breaching=False,
    claimed_by=None,
    claim_expires=None,
    snoozed_until=None,
    escalated=False,
    staged=None,
)
ROW_INFORMATION = TaskRow(
    task_id="TSK-7f80910213243546",
    entity="person",
    kind="unresolved_reference",
    kind_label="Unresolved reference",
    title="E*** F***",
    subject="hr:H000217",
    score=None,
    band=None,
    suggestion="Wait for the organisation",
    reason="employer not known yet",
    due_at=NOW + timedelta(hours=60),
    breaching=False,
    claimed_by=None,
    claim_expires=None,
    snoozed_until=None,
    escalated=False,
    staged=None,
)
ROWS = (
    ROW_CLOSE_CALL,
    ROW_PERSON_REVIEW,
    ROW_HELD_UPDATE,
    ROW_GOLDEN_PAIR,
    ROW_ORPHAN,
    ROW_HELD_NEW,
    ROW_INFORMATION,
)
TASK_PAGE = TaskPage(rows=ROWS, after=((NOW + timedelta(hours=70)).isoformat(), ROW_ORPHAN.task_id))
LAST_PAGE = TaskPage(rows=ROWS[:2], after=None)

COUNTS_UNDER_CAP = ViewCounts(
    views={"mine": 31, "team": 44, "breaching": 3, "snoozed": 1, "escalated": 1},
    kinds={
        "review": 12,
        "possible_duplicate": 4,
        "held": 21,
        "exception": 0,
        "orphan": 6,
        "unresolved_reference": 1,
    },
)
COUNTS_AT_CAP = ViewCounts(
    views={
        "mine": capacity.COUNT_CAP,
        "team": capacity.COUNT_CAP,
        "breaching": capacity.COUNT_CAP - 1,
        "snoozed": 0,
        "escalated": 2,
    },
    kinds={
        "review": capacity.COUNT_CAP,
        "possible_duplicate": 17,
        "held": capacity.COUNT_CAP,
        "exception": 0,
        "orphan": 0,
        "unresolved_reference": 0,
    },
)
HEALTH = Health(
    open_tasks=31,
    breaching=3,
    staged=1,
    last_commit_version=6,
    last_commit_at=NOW - timedelta(minutes=4),
    last_arrival_at=NOW - timedelta(minutes=5),
    arrival_read=291,
    arrival_tasks=12,
    arrival_automatic=0.96,
)
HEALTH_EMPTY = Health(
    open_tasks=0,
    breaching=0,
    staged=0,
    last_commit_version=0,
    last_commit_at=None,
    last_arrival_at=None,
    arrival_read=None,
    arrival_tasks=None,
    arrival_automatic=None,
)

# ---------------------------------------------------------------------------------------------- the close call

#: the probe's weights: prior −8.96, name +9.45, city +4.09, postcode −2.65 = 1.93 (79.19)
STEPS_CLOSE_CALL = (
    WaterfallStep("Prior", None, "", -8.96, 0.0, -8.96),
    WaterfallStep("Name =", "org_name", "the same", 9.45, -8.96, 0.49),
    WaterfallStep("City =", "city", "the same", 4.09, 0.49, 4.58),
    WaterfallStep("Postcode ≠", "postcode", "different", -2.65, 4.58, 1.93),
    WaterfallStep("Registered ID ∅", "registered_id", "missing", 0.0, 1.93, 1.93),
)
STEPS_DISTINCT = (
    WaterfallStep("Prior", None, "", -8.96, 0.0, -8.96),
    WaterfallStep("Name ≈", "org_name", "similar", 5.1, -8.96, -3.86),
    WaterfallStep("City ≠", "city", "different", -1.2, -3.86, -5.06),
    WaterfallStep("Postcode ≠", "postcode", "different", -2.65, -5.06, -7.71),
)
IMPACT_LINK = Impact(xrefs_added=1, golden_changed=("Phone",))
IMPACT_NONE = Impact()
IMPACT_APPROVE = Impact(golden_changed=("Name",), held_released=1)


def _link_preview(master_id: str) -> Preview:
    return Preview(
        master_id=master_id,
        rows=(
            PreviewRow("Name", "Quorane Works Ltd", "Quorane Works Ltd", False),
            PreviewRow("Phone", None, "+999 41 555 0123", True),
            PreviewRow("City", "Varnmouth", "Varnmouth", False),
        ),
        impact=IMPACT_LINK,
    )


CANDIDATE_1 = Candidate(
    index=1,
    master_id="ORG-000123",
    title="Quorane Works Ltd",
    score=79.19,
    band="review",
    member="finance:F000123",
    steps=STEPS_CLOSE_CALL,
    total=1.93,
    thresholds=THRESHOLDS,
    flip=(
        "If the postcodes shared their first part (+3.1), the score would rise to 98 and link on its own.",
    ),
    hard_rule=None,
    blocked_by=None,
    signature="org_name=exact|city=exact|postcode=else|registered_id=null",
    rule_version=1,
    preview=_link_preview("ORG-000123"),
    what_if=(("registered_id", 5.3), ("postcode", 3.1)),
)
CANDIDATE_2 = Candidate(
    index=2,
    master_id="ORG-004410",
    title="Quorane Works Ltd",
    score=79.19,
    band="review",
    member="finance:F004410",
    steps=STEPS_CLOSE_CALL,
    total=1.93,
    thresholds=THRESHOLDS,
    flip=("The same postcode would make it an automatic link.",),
    hard_rule=None,
    blocked_by=None,
    signature="org_name=exact|city=exact|postcode=else|registered_id=null",
    rule_version=1,
    preview=_link_preview("ORG-004410"),
)
CANDIDATE_3 = Candidate(
    index=3,
    master_id="ORG-000871",
    title="Quorane Textiles",
    score=round(100 * 2**-7.71 / (1 + 2**-7.71), 2),
    band="distinct",
    member="crm:C000871",
    steps=STEPS_DISTINCT,
    total=-7.71,
    thresholds=THRESHOLDS,
    flip=(),
    hard_rule=None,
    blocked_by="A different registered ID keeps these apart.",
    signature="org_name=similar|city=else|postcode=else",
    rule_version=1,
    preview=None,
)
CANDIDATES = (CANDIDATE_1, CANDIDATE_2, CANDIDATE_3)

COMPARE_CLOSE_CALL = (
    CompareRow(
        attribute="name",
        label="Name",
        values=("QUORANE WORKS ltd.", "Quorane Works Ltd", "Quorane Works Ltd", "Quorane Textiles"),
        agreement=("agree", "agree", "partial"),
        critical=True,
        personal=False,
    ),
    CompareRow(
        attribute="registered_id",
        label="Registered ID",
        values=(None, "ORG_REG 1234567890", "ORG_REG 7654321002", "ORG_REG 5550001118"),
        agreement=("missing", "missing", "missing"),
        critical=True,
        personal=False,
    ),
    CompareRow(
        attribute="city",
        label="City",
        values=("Varnmouth", "Varnmouth", "Varnmouth", "Kelborough"),
        agreement=("agree", "agree", "disagree"),
        critical=False,
        personal=False,
    ),
    CompareRow(
        attribute="postcode",
        label="Postcode",
        values=("LF9 2QX", "LF4 5JB", "LF7 1DA", "KB2 8RT"),
        agreement=("disagree", "disagree", "disagree"),
        critical=False,
        personal=False,
    ),
    CompareRow(
        attribute="website",
        label="Website",
        values=(None, None, None, "quorane.example"),
        agreement=("", "", ""),
        critical=False,
        personal=False,
    ),
)


def _actions(*, enabled: bool = True, staged: bool = False, target: str | None = None) -> tuple[Action, ...]:
    why = None if enabled else "Your role, data owner, can see tasks but not decide them."
    link = f"Link to {target}" if target else "Link"
    return (
        Action("link", link, "L", enabled and not staged, why, target=target),
        Action("not_a_match", "Not a match", "N", enabled and not staged, why),
        Action("claim", "Claim", "C", enabled, why),
        Action("snooze", "Snooze", "S", enabled, why),
        Action("escalate", "Escalate", "E", enabled, why),
        *((Action("undo", "Undo", "U", True, None),) if staged else ()),
    )


CASE_CLOSE_CALL = TaskCase(
    row=ROW_CLOSE_CALL,
    reason_text="Two golden records score the same against this arriving record; choose one before you link.",
    shape="source",
    columns=(
        "Arriving · crm:C000812",
        "1 · ORG-000123",
        "2 · ORG-004410",
        "3 · ORG-000871",
    ),
    compare=COMPARE_CLOSE_CALL,
    candidates=CANDIDATES,
    default_candidate=None,
    close_call=True,
    preview=None,
    actions=_actions(),
    notice=None,
    masked=False,
    revealable=(),
    staged=None,
    claimed_by=None,
    event_id="crm-7-00004101",
)
#: the same case as a data owner reads it: every action disabled, with its reason
CASE_OWNER_VIEW = TaskCase(
    row=ROW_CLOSE_CALL,
    reason_text=CASE_CLOSE_CALL.reason_text,
    shape="source",
    columns=CASE_CLOSE_CALL.columns,
    compare=COMPARE_CLOSE_CALL,
    candidates=CANDIDATES,
    default_candidate=None,
    close_call=True,
    preview=None,
    actions=_actions(enabled=False),
    notice=None,
    masked=False,
    revealable=(),
    staged=None,
    claimed_by=None,
    event_id="crm-7-00004101",
)

# ---------------------------------------------------------------------------------------------- a person review, staged

COMPARE_PERSON = (
    CompareRow("given_name", "Given name", ("K***", "K***"), ("agree",), critical=True, personal=True),
    CompareRow("family_name", "Family name", ("B***", "B***"), ("agree",), critical=True, personal=True),
    CompareRow("birth_date", "Birth date", ("hidden", "hidden"), ("partial",), critical=True, personal=True),
    CompareRow("email", "Email", (None, "hidden"), ("missing",), critical=False, personal=True),
)
CANDIDATE_PERSON = Candidate(
    index=1,
    master_id="PER-000451",
    title="K*** B***",
    score=88.66,
    band="review",
    member="hr:H000451",
    steps=(
        WaterfallStep("Prior", None, "", -9.97, 0.0, -9.97),
        WaterfallStep("Given name =", "given_name", "the same", 4.20, -9.97, -5.77),
        WaterfallStep("Family name =", "family_name", "the same", 4.20, -5.77, -1.57),
        WaterfallStep("Birth date ≈", "birth_date", "one digit apart", 4.53, -1.57, 2.96),
    ),
    total=2.97,
    thresholds=THRESHOLDS,
    flip=("The same birth date would make it an automatic link.",),
    hard_rule=None,
    blocked_by=None,
    signature="given_name=exact|family_name=exact|birth_date=one_digit",
    rule_version=1,
    preview=Preview(
        master_id="PER-000451",
        rows=(
            PreviewRow("Given name", "K***", "K***", False),
            PreviewRow("Email", "hidden", "hidden", False),
        ),
        impact=Impact(xrefs_added=1),
    ),
)
CASE_PERSON_STAGED = TaskCase(
    row=ROW_PERSON_REVIEW,
    reason_text="The arriving record scores in the review band against one golden record.",
    shape="source",
    columns=("Arriving · crm:C001377", "1 · PER-000451"),
    compare=COMPARE_PERSON,
    candidates=(CANDIDATE_PERSON,),
    default_candidate="PER-000451",
    close_call=False,
    preview=None,
    actions=_actions(staged=True, target="PER-000451"),
    notice=None,
    masked=True,
    revealable=("given_name", "family_name", "birth_date", "email"),
    staged=STAGED,
    claimed_by="you",
    event_id="crm-7-00005120",
)
REVEALED_PERSON = Revealed(
    compare=(
        CompareRow(
            "given_name", "Given name", ("Kaewyn", "Kaewyn"), ("agree",), critical=True, personal=True
        ),
        CompareRow(
            "family_name", "Family name", ("Bromdale", "Bromdale"), ("agree",), critical=True, personal=True
        ),
        CompareRow(
            "birth_date",
            "Birth date",
            ("1990-04-17", "1990-04-11"),
            ("partial",),
            critical=True,
            personal=True,
        ),
        CompareRow(
            "email",
            "Email",
            (None, "kaewyn.bromdale7@example.org"),
            ("missing",),
            critical=False,
            personal=True,
        ),
    ),
    logged=8,
)

# ---------------------------------------------------------------------------------------------- other shapes

PREVIEW_APPROVE = Preview(
    master_id="ORG-000419",
    rows=(
        PreviewRow("Name", "Velix Supplies Ltd", "Velix Supplies Limited", True),
        PreviewRow("City", "Kelborough", "Kelborough", False),
    ),
    impact=IMPACT_APPROVE,
)
CASE_HELD_UPDATE = TaskCase(
    row=ROW_HELD_UPDATE,
    reason_text="crm changed a critical attribute, Name, and its policy holds such a change for a steward.",
    shape="held_update",
    columns=("Approved · crm:C000419", "New · crm:C000419", "Golden · ORG-000419"),
    compare=(
        CompareRow(
            "name",
            "Name",
            ("Velix Supplies Ltd", "Velix Supplies Limited", "Velix Supplies Ltd"),
            ("partial", "partial"),
            critical=True,
            personal=False,
            changed=True,
        ),
        CompareRow(
            "city",
            "City",
            ("Kelborough", "Kelborough", "Kelborough"),
            ("agree", "agree"),
            critical=False,
            personal=False,
        ),
    ),
    candidates=(),
    default_candidate=None,
    close_call=False,
    preview=PREVIEW_APPROVE,
    actions=(
        Action("approve_update", "Approve the update", "A", True, None),
        Action("reject_update", "Reject the update", "R", True, None),
        Action("claim", "Claim", "C", False, "Another steward is working on this task until 12:09."),
        Action("snooze", "Snooze", "S", True, None),
        Action("escalate", "Escalate", "E", True, None),
    ),
    notice=None,
    masked=False,
    revealable=(),
    staged=None,
    claimed_by="Coordinating steward (persona)",
    event_id="crm-7-00006023",
)
CASE_GOLDEN_PAIR = TaskCase(
    row=ROW_GOLDEN_PAIR,
    reason_text="Two golden records score in the review band against each other.",
    shape="golden_pair",
    columns=("ORG-000211", "ORG-000388"),
    compare=(
        CompareRow(
            "name", "Name", ("Tessova Labs", "Tessova Labs Ltd"), ("partial",), critical=True, personal=False
        ),
    ),
    candidates=(),
    default_candidate=None,
    close_call=False,
    preview=Preview(master_id="ORG-000211", rows=(), impact=IMPACT_NONE),
    actions=(
        Action("keep_apart", "Keep apart", "N", True, None),
        Action("claim", "Claim", "C", True, None),
        Action("snooze", "Snooze", "S", True, None),
        Action("escalate", "Escalate", "E", True, None),
    ),
    notice="Merging needs a second steward to check it; that is not on screen yet.",
    masked=False,
    revealable=(),
    staged=None,
    claimed_by=None,
    event_id=None,
)
CASE_ORPHAN = TaskCase(
    row=ROW_ORPHAN,
    reason_text="This golden record has no active source record.",
    shape="golden",
    columns=("ORG-000930",),
    compare=(CompareRow("name", "Name", ("Drava Foundry",), (), critical=True, personal=False),),
    candidates=(),
    default_candidate=None,
    close_call=False,
    preview=Preview(master_id="ORG-000930", rows=(), impact=IMPACT_NONE),
    actions=(
        Action("keep_orphan", "Keep as it is", "A", True, None),
        Action("claim", "Claim", "C", True, None),
        Action("snooze", "Snooze", "S", True, None),
        Action("escalate", "Escalate", "E", True, None),
    ),
    notice="Retiring a golden record needs a second steward to check it; that is not on screen yet.",
    masked=False,
    revealable=(),
    staged=None,
    claimed_by=None,
    event_id=None,
)
CASE_HELD_NEW = TaskCase(
    row=ROW_HELD_NEW,
    reason_text="student_records holds a new record for a steward.",
    shape="held_new",
    columns=("New · student_records:S0004410",),
    compare=(CompareRow("given_name", "Given name", ("C***",), (), critical=True, personal=True),),
    candidates=(),
    default_candidate=None,
    close_call=False,
    preview=None,
    actions=(
        Action("claim", "Claim", "C", True, None),
        Action("snooze", "Snooze", "S", True, None),
        Action("escalate", "Escalate", "E", True, None),
    ),
    notice="Creating a golden record from a held arrival is not on screen yet.",
    masked=True,
    revealable=("given_name",),
    staged=None,
    claimed_by=None,
    event_id="student_records-7-00000912",
)
CASE_INFORMATION = TaskCase(
    row=ROW_INFORMATION,
    reason_text="This record names an employer the hub does not know yet.",
    shape="information",
    columns=("hr:H000217",),
    compare=(),
    candidates=(),
    default_candidate=None,
    close_call=False,
    preview=None,
    actions=(Action("claim", "Claim", "C", True, None), Action("snooze", "Snooze", "S", True, None)),
    notice="The record settles by itself when the organisation arrives.",
    masked=True,
    revealable=(),
    staged=None,
    claimed_by=None,
    event_id="hr-7-00000217",
)
CASES = (
    CASE_CLOSE_CALL,
    CASE_OWNER_VIEW,
    CASE_PERSON_STAGED,
    CASE_HELD_UPDATE,
    CASE_GOLDEN_PAIR,
    CASE_ORPHAN,
    CASE_HELD_NEW,
    CASE_INFORMATION,
)

# ---------------------------------------------------------------------------------------------- the checkpoint

#: the quality breaker paused Organisation's automatic linking: 30 of the last 40 automatic links confirmed
BREAKER_AGREEMENT = BreakerView(
    entity="organisation",
    trigger="agreement",
    since=datetime(2026, 9, 27, 11, 2, tzinfo=UTC),
    figures={"agreed": 30, "reviewed": 40, "threshold": 0.95, "window": 100},
)
#: and Person's, on a spike of arrivals in the hour from 11:00
BREAKER_VOLUME = BreakerView(
    entity="person",
    trigger="volume",
    since=datetime(2026, 9, 26, 23, 5, tzinfo=UTC),
    figures={
        "arrivals": 12400,
        "mean": 1100.0,
        "multiple": 5,
        "days": 7,
        "hour": "2026-09-26T23:00:00+00:00",
    },
)
HEALTH_PAUSED = Health(
    open_tasks=31,
    breaching=3,
    staged=1,
    last_commit_version=6,
    last_commit_at=NOW - timedelta(minutes=4),
    last_arrival_at=NOW - timedelta(minutes=5),
    arrival_read=291,
    arrival_tasks=12,
    arrival_automatic=0.96,
    paused=(BREAKER_AGREEMENT, BREAKER_VOLUME),
)
COUNTS_SAMPLES = ViewCounts(
    views={"mine": 31, "team": 44, "breaching": 3, "snoozed": 1, "escalated": 1, "samples": 12},
    kinds={**COUNTS_UNDER_CAP.kinds, "quality_sample": 12},
    claimed=2,
    samples_breaching=1,
)

ROW_SAMPLE = TaskRow(
    task_id="TSK-8091021324354657",
    entity="organisation",
    kind="quality_sample",
    kind_label="Quality sample",
    title="Quorane Works",
    subject="crm:C001409",
    score=None,
    band=None,
    suggestion="Decide blind",
    reason="",
    due_at=NOW + timedelta(hours=48),
    breaching=False,
    claimed_by=None,
    claim_expires=None,
    snoozed_until=None,
    escalated=False,
    staged=None,
)
CHOICES = (
    Choice(index=1, master_id="ORG-000123", title="Quorane Works Ltd"),
    Choice(index=2, master_id="ORG-000871", title="Quorane Textiles"),
    Choice(index=3, master_id="ORG-004410", title="Quorane Works Ltd"),
)
COMPARE_BLIND = (
    CompareRow(
        attribute="name",
        label="Name",
        values=("Quorane Works", "Quorane Works Ltd", "Quorane Textiles", "Quorane Works Ltd"),
        agreement=("partial", "partial", "partial"),
        critical=True,
        personal=False,
    ),
    CompareRow(
        attribute="city",
        label="City",
        values=("Varnmouth", "Varnmouth", "Kelborough", "Varnmouth"),
        agreement=("agree", "disagree", "agree"),
        critical=False,
        personal=False,
    ),
    CompareRow(
        attribute="postcode",
        label="Postcode",
        values=("LF4 5JB", "LF4 5JB", "KB2 8RT", "LF7 1DA"),
        agreement=("agree", "disagree", "disagree"),
        critical=False,
        personal=False,
    ),
)


def _work(*, why: str | None = None) -> tuple[Action, ...]:
    return (
        Action("claim", "Claim", "C", why is None, why),
        Action("snooze", "Snooze", "S", why is None, why),
        Action("escalate", "Escalate", "E", why is None, why),
    )


def _blind_actions(choices: tuple[Choice, ...], *, why: str | None = None) -> tuple[Action, ...]:
    return (
        *(
            Action("blind_link", f"Belongs to {c.master_id}", "L", why is None, why, target=c.master_id)
            for c in choices
        ),
        Action("blind_none", "Belongs to none of these", "N", why is None, why),
        *_work(why=why),
    )


#: a quality sample of a record, decided blind: three golden records offered, no score, band or first decision
CASE_BLIND = TaskCase(
    row=ROW_SAMPLE,
    reason_text=BLIND_SENTENCE,
    shape="blind",
    columns=("Record · crm:C001409", "1 · ORG-000123", "2 · ORG-000871", "3 · ORG-004410"),
    compare=COMPARE_BLIND,
    candidates=(),
    default_candidate=None,
    close_call=False,
    preview=None,
    actions=_blind_actions(CHOICES),
    notice=None,
    masked=False,
    revealable=(),
    staged=None,
    claimed_by=None,
    event_id="crm-7-00007141",
    choices=CHOICES,
    blind=True,
)
#: the same sample as the steward who made the first decision reads it: nothing to do but wait for another
CASE_BLIND_OWN = TaskCase(
    row=ROW_SAMPLE,
    reason_text=BLIND_SENTENCE,
    shape="blind",
    columns=CASE_BLIND.columns,
    compare=COMPARE_BLIND,
    candidates=(),
    default_candidate=None,
    close_call=False,
    preview=None,
    actions=_blind_actions(CHOICES, why=NOTICE_OWN_DECISION),
    notice=None,
    masked=False,
    revealable=(),
    staged=None,
    claimed_by=None,
    event_id="crm-7-00007141",
    choices=CHOICES,
    blind=True,
)
#: a sample with no golden record near: only "Belongs to none of these"
CASE_BLIND_NONE = TaskCase(
    row=TaskRow(
        task_id="TSK-9102132435465768",
        entity="person",
        kind="quality_sample",
        kind_label="Quality sample",
        title="Y*** T***",
        subject="hr:H000998",
        score=None,
        band=None,
        suggestion="Decide blind",
        reason="",
        due_at=NOW + timedelta(hours=70),
        breaching=False,
        claimed_by=None,
        claim_expires=None,
        snoozed_until=None,
        escalated=False,
        staged=None,
    ),
    reason_text=BLIND_SENTENCE,
    shape="blind",
    columns=("Record · hr:H000998",),
    compare=(CompareRow("given_name", "Given name", ("Y***",), (), critical=True, personal=True),),
    candidates=(),
    default_candidate=None,
    close_call=False,
    preview=None,
    actions=_blind_actions(()),
    notice=NOTICE_BLIND_NONE,
    masked=True,
    revealable=("given_name",),
    staged=None,
    claimed_by=None,
    event_id="hr-7-00000998",
    blind=True,
)
#: a sample of two golden records a steward kept apart
CASE_BLIND_PAIR = TaskCase(
    row=TaskRow(
        task_id="TSK-a213243546576879",
        entity="organisation",
        kind="quality_sample",
        kind_label="Quality sample",
        title="Tessova Labs",
        subject="ORG-000211 · ORG-000388",
        score=None,
        band=None,
        suggestion="Decide blind",
        reason="",
        due_at=NOW + timedelta(hours=60),
        breaching=False,
        claimed_by=None,
        claim_expires=None,
        snoozed_until=None,
        escalated=False,
        staged=None,
    ),
    reason_text=BLIND_PAIR_SENTENCE,
    shape="blind_pair",
    columns=("ORG-000211 · golden record", "ORG-000388 · golden record"),
    compare=(
        CompareRow(
            "name", "Name", ("Tessova Labs", "Tessova Labs Ltd"), ("partial",), critical=True, personal=False
        ),
    ),
    candidates=(),
    default_candidate=None,
    close_call=False,
    preview=None,
    actions=(
        Action("blind_link", "They are the same", "L", True, None, target="ORG-000388"),
        Action("blind_none", "They are not the same", "N", True, None),
        *_work(),
    ),
    notice=None,
    masked=False,
    revealable=(),
    staged=None,
    claimed_by=None,
    event_id=None,
    task_version="2026-09-27T09:00:00+00:00",
    blind=True,
)
ROW_DISPUTED = TaskRow(
    task_id="TSK-b32435465768798a",
    entity="organisation",
    kind="review",
    kind_label="Review",
    title="Quorane Works",
    subject="crm:C000957",
    score=79.19,
    band="review",
    suggestion="Keep or correct the first decision",
    reason="a blind review disagreed",
    due_at=NOW + timedelta(hours=6),
    breaching=False,
    claimed_by=None,
    claim_expires=None,
    snoozed_until=None,
    escalated=False,
    staged=None,
)
DISPUTE_SENTENCE = "A blind review placed this record differently from the first decision."
#: a blind review placed an unlinked record in a golden record: Keep the first decision, or link it there
CASE_DISPUTED_LINK = TaskCase(
    row=ROW_DISPUTED,
    reason_text=DISPUTE_SENTENCE,
    shape="disputed",
    columns=("Record · crm:C000957", "Blind review · ORG-000123"),
    compare=tuple(replace(r, values=r.values[:2], agreement=r.agreement[:1]) for r in COMPARE_CLOSE_CALL),
    candidates=(CANDIDATE_1,),
    default_candidate="ORG-000123",
    close_call=False,
    preview=None,
    actions=(
        Action("keep_decision", "Keep the first decision", "A", True, None),
        Action("link", "Link to ORG-000123", "L", True, None, target="ORG-000123"),
        *_work(),
    ),
    notice=None,
    masked=False,
    revealable=(),
    staged=None,
    claimed_by=None,
    event_id="crm-7-00004457",
)
#: the record is linked and shares its golden record: only Keep, and why
CASE_DISPUTED = TaskCase(
    row=ROW_DISPUTED,
    reason_text=DISPUTE_SENTENCE,
    shape="disputed",
    columns=("Record · crm:C000957", "Now · ORG-000123", "Blind review · ORG-004410"),
    compare=tuple(replace(r, values=r.values[:3], agreement=r.agreement[:2]) for r in COMPARE_CLOSE_CALL),
    candidates=(CANDIDATE_1, replace(CANDIDATE_2, preview=None)),
    default_candidate=None,
    close_call=False,
    preview=None,
    actions=(Action("keep_decision", "Keep the first decision", "A", True, None), *_work()),
    notice=NOTICE_DISPUTE_DETACH,
    masked=False,
    revealable=(),
    staged=None,
    claimed_by=None,
    event_id="crm-7-00004457",
)
#: a blind review found two golden records kept apart the same
CASE_DISPUTED_PAIR = TaskCase(
    row=replace(
        ROW_GOLDEN_PAIR,
        task_id="TSK-c435465768798a9b",
        suggestion="Keep or correct the first decision",
        reason="a blind review disagreed",
        escalated=False,
    ),
    reason_text=DISPUTE_SENTENCE,
    shape="disputed_pair",
    columns=("ORG-000211", "ORG-000388"),
    compare=CASE_GOLDEN_PAIR.compare,
    candidates=(),
    default_candidate=None,
    close_call=False,
    preview=None,
    actions=(Action("keep_decision", "Keep the first decision", "A", True, None), *_work()),
    notice=NOTICE_MERGE,
    masked=False,
    revealable=(),
    staged=None,
    claimed_by=None,
    event_id=None,
    task_version="2026-09-27T10:00:00+00:00",
)
#: a review the breaker opened while automatic linking is paused: two new records, no golden record yet
CASE_PAUSED = TaskCase(
    row=replace(
        ROW_CLOSE_CALL,
        task_id="TSK-d5465768798a9bac",
        subject="crm:C001502",
        score=96.2,
        band="auto",
        suggestion="Wait for automatic linking",
        reason="paused by the breaker",
    ),
    reason_text=(
        "This record would have linked automatically, but the quality breaker has paused automatic linking "
        "for this entity."
    ),
    shape="source",
    columns=("Arriving · crm:C001502",),
    compare=(CompareRow("name", "Name", ("Quorane Works Ltd",), (), critical=True, personal=False),),
    candidates=(),
    default_candidate=None,
    close_call=False,
    preview=None,
    actions=(Action("not_a_match", "Not a match", "N", False, NOTICE_BREAKER_WAIT), *_work()),
    notice=NOTICE_BREAKER_WAIT,
    masked=False,
    revealable=(),
    staged=None,
    claimed_by=None,
    event_id="crm-7-00007302",
    paused=BREAKER_AGREEMENT,
)
CHECKPOINT_CASES = (
    CASE_BLIND,
    CASE_BLIND_OWN,
    CASE_BLIND_NONE,
    CASE_BLIND_PAIR,
    CASE_DISPUTED_LINK,
    CASE_DISPUTED,
    CASE_DISPUTED_PAIR,
    CASE_PAUSED,
)
#: a blind answer committed: it matched the first decision, or differed and opened a review
TRAY_VIEWS_BLIND = (
    TrayView(
        entry_id="TR-3d4e5f60718293041526",
        task_id=ROW_SAMPLE.task_id,
        decision="blind_link",
        label="Quality sample: crm:C001409 belongs to ORG-000123",
        deadline=NOW - timedelta(minutes=3),
        status="committed",
        outcome="agreed",
        commit_version=None,
    ),
    TrayView(
        entry_id="TR-4e5f6071829304152637",
        task_id=CASE_BLIND_PAIR.row.task_id,
        decision="blind_none",
        label="Quality sample: ORG-000211 and ORG-000388 are not the same",
        deadline=NOW - timedelta(minutes=2),
        status="committed",
        outcome="disagreed",
        commit_version=None,
    ),
)

# ---------------------------------------------------------------------------------------------- the tray

STAGING = Staging(
    task_id=ROW_CLOSE_CALL.task_id,
    entity="organisation",
    decision="link",
    target="ORG-000123",
    subject={
        "source": "crm:C000812",
        "candidates": ["ORG-000123", "ORG-004410"],
        "score": 79.19,
        "band": "review",
    },
    signature=CANDIDATE_1.signature,
    event_id="crm-7-00004101",
    locks=(f"task:{ROW_CLOSE_CALL.task_id}", "source:organisation:crm:C000812"),
)
TRAY_ENTRY_STAGED = TrayEntry(
    entry_id=STAGED.entry_id,
    task_id=ROW_PERSON_REVIEW.task_id,
    entity="person",
    decision="link",
    target="PER-000451",
    subject={"source": "crm:C001377", "candidates": ["PER-000451"], "score": 88.66, "band": "review"},
    signature=CANDIDATE_PERSON.signature,
    actor="persona:data_steward",
    actor_role="data_steward",
    persona=True,
    event_id="crm-7-00005120",
    planning_version=6,
    staged_at=NOW - timedelta(seconds=12),
    deadline=STAGED.deadline,
    status="staged",
)
TRAY_ENTRY_COMMITTED = TrayEntry(
    entry_id="TR-1b2c3d4e5f6071829304",
    task_id=ROW_CLOSE_CALL.task_id,
    entity="organisation",
    decision="link",
    target="ORG-000123",
    subject=STAGING.subject,
    signature=CANDIDATE_1.signature,
    actor="persona:data_steward",
    actor_role="data_steward",
    persona=True,
    event_id="crm-7-00004101",
    planning_version=5,
    staged_at=NOW - timedelta(minutes=3),
    deadline=NOW - timedelta(minutes=2),
    status="committed",
    attempts=1,
    settled_at=NOW - timedelta(minutes=2),
    change_set_id="CS-00112233445566778899",
    commit_version=6,
    outcome="committed",
)
TRAY_ENTRY_FAILED = TrayEntry(
    entry_id="TR-2c3d4e5f607182930415",
    task_id=ROW_HELD_UPDATE.task_id,
    entity="organisation",
    decision="approve_update",
    target=None,
    subject={"source": "crm:C000419", "kind": "held"},
    signature=None,
    actor="persona:data_steward",
    actor_role="data_steward",
    persona=True,
    event_id="crm-7-00006023",
    planning_version=5,
    staged_at=NOW - timedelta(minutes=5),
    deadline=NOW - timedelta(minutes=4),
    status="failed",
    attempts=1,
    settled_at=NOW - timedelta(minutes=4),
    outcome="record_changed",
)
TRAY_VIEWS = (
    TrayView(
        entry_id=TRAY_ENTRY_STAGED.entry_id,
        task_id=TRAY_ENTRY_STAGED.task_id,
        decision="link",
        label=STAGED.label,
        deadline=TRAY_ENTRY_STAGED.deadline,
        status="staged",
        outcome=None,
        commit_version=None,
    ),
    TrayView(
        entry_id=TRAY_ENTRY_COMMITTED.entry_id,
        task_id=TRAY_ENTRY_COMMITTED.task_id,
        decision="link",
        label="Link crm:C000812 to ORG-000123",
        deadline=TRAY_ENTRY_COMMITTED.deadline,
        status="committed",
        outcome="committed",
        commit_version=6,
    ),
    TrayView(
        entry_id=TRAY_ENTRY_FAILED.entry_id,
        task_id=TRAY_ENTRY_FAILED.task_id,
        decision="approve_update",
        label="Approve the update of crm:C000419",
        deadline=TRAY_ENTRY_FAILED.deadline,
        status="failed",
        outcome="record_changed",
        commit_version=None,
    ),
)
TRAY_SETTLEMENT = TraySettlement(
    entry_id=TRAY_ENTRY_STAGED.entry_id, status="committed", change_set_id=None, outcome="committed"
)
MATCH_LABEL = MatchLabel(
    entity="person",
    left_ref="crm:C001377",
    right_ref="PER-000451",
    label="match",
    rule_version=1,
    score=88.66,
    band="review",
    signature=CANDIDATE_PERSON.signature,
    task_id=ROW_PERSON_REVIEW.task_id,
    entry_id=TRAY_ENTRY_STAGED.entry_id,
    decided_by="persona:data_steward",
    decided_role="data_steward",
    decided_at=NOW,
)
FLUSH_REPORT = FlushReport(committed=3, failed=1, requeued=1, outcomes={"committed": 3, "record_changed": 1})
FLUSH_BUSY = FlushReport(skipped_busy=True)

# ---------------------------------------------------------------------------------------------- the record

RESOLUTION_GOLDEN = Resolution(
    kind="golden", entity="organisation", master_id="ORG-000123", source=None, notice=None
)
RESOLUTION_RETIRED = Resolution(
    kind="golden",
    entity="organisation",
    master_id="ORG-000123",
    source=None,
    notice="ORG-003307 was merged into ORG-000123; showing the survivor.",
)
RESOLUTION_SOURCE = Resolution(
    kind="source", entity="organisation", master_id="ORG-000123", source="crm:C000812", notice=None
)
RESOLUTION_UNKNOWN = Resolution(
    kind="unknown", entity=None, master_id=None, source=None, notice="No record has the ID ORG-999999."
)
RECORD_HEADER = RecordHeader(
    entity="organisation",
    master_id="ORG-000123",
    title="Quorane Works Ltd",
    status="active",
    survivor_id=None,
    retired_ids=("ORG-003307",),
    open_tasks=(ROW_CLOSE_CALL.task_id,),
    commit_version=6,
    member_count=3,
    relationship_count=7,
)
VALUES = (
    ValueView(
        attribute="name",
        label="Name",
        value="Quorane Works Ltd",
        masked=False,
        personal=False,
        critical=True,
        source="finance:F000123",
        decided_by="source_trust",
        chip="finance · source trust · 2 d",
        age_days=2,
        pinned_until=None,
    ),
    ValueView(
        attribute="phone",
        label="Phone",
        value="+999 41 555 0123",
        masked=False,
        personal=False,
        critical=False,
        source="steward",
        decided_by="pin",
        chip="Steward pin · until 30 Nov",
        age_days=9,
        pinned_until=datetime(2026, 11, 30, tzinfo=UTC),
    ),
    ValueView(
        attribute="website",
        label="Website",
        value="quorane.example",
        masked=False,
        personal=False,
        critical=False,
        source="crm:C000812",
        decided_by=None,
        chip="crm · rules · 14 d",
        age_days=14,
        pinned_until=None,
    ),
    ValueView(
        attribute="registered_id",
        label="Registered ID",
        value=None,
        masked=False,
        personal=False,
        critical=True,
        source=None,
        decided_by=None,
        chip=None,
        age_days=None,
        pinned_until=None,
    ),
)
#: a Person value as a steward without a reveal sees it
VALUE_MASKED = ValueView(
    attribute="family_name",
    label="Family name",
    value="B***",
    masked=True,
    personal=True,
    critical=True,
    source="hr:H000451",
    decided_by="recency",
    chip="hr · recency · 30 d",
    age_days=30,
    pinned_until=None,
)
WHY = ValueWhy(
    attribute="name",
    label="Name",
    sentence=(
        "Survivorship rules v1 for Name use source trust, then recency. The finance source ranks 1 and the "
        "crm source ranks 2 for Name, so the finance value wins."
    ),
    winner=RunnerUp(source="finance:F000123", value="Quorane Works Ltd", age_days=2),
    runners_up=(RunnerUp(source="crm:C000812", value="QUORANE WORKS ltd.", age_days=0),),
    strategies=("source_trust", "recency"),
    decided_by="source_trust",
    rule_version=1,
)
MEMBERS = (
    MemberView(
        source="finance:F000123",
        system="finance",
        key="F000123",
        trust=1,
        occurred_at=NOW - timedelta(days=2),
        source_version=3,
        held=False,
        rule_failures=(),
        open_task=None,
    ),
    MemberView(
        source="crm:C000812",
        system="crm",
        key="C000812",
        trust=2,
        occurred_at=NOW - timedelta(hours=1),
        source_version=None,
        held=True,
        rule_failures=("phone: bad_pattern",),
        open_task=ROW_CLOSE_CALL.task_id,
    ),
)
TIMELINE_PAGE = TimelinePage(
    events=(
        TimelineEvent(
            commit_version=6,
            change_seq=1,
            at=NOW - timedelta(minutes=4),
            kind="updated",
            headline="Values updated: phone",
            parts=("values", "xref"),
            actor="Data steward",
            automated=False,
            authority="Data steward",
        ),
        TimelineEvent(
            commit_version=4,
            change_seq=2,
            at=NOW - timedelta(days=1),
            kind="merged",
            headline="ORG-003307 merged into this record",
            parts=("survivor",),
            actor="Data steward",
            automated=False,
            authority="Data steward; checker Data owner",
        ),
        TimelineEvent(
            commit_version=1,
            change_seq=7,
            at=NOW - timedelta(days=20),
            kind="created",
            headline="Created from finance:F000123",
            parts=("values", "xref"),
            actor="Automated matcher",
            automated=True,
            authority="rules v1 · finance.new=auto",
        ),
    ),
    before=(1, 7),
)
TIMELINE_LAST = TimelinePage(events=TIMELINE_PAGE.events[-1:], before=None)
RELATIONSHIPS = (
    RelationshipView(
        rel_type="subsidiary_of",
        label="subsidiary of",
        direction="out",
        other_entity="organisation",
        other_master_id="ORG-000211",
        other_title="Tessova Labs",
        valid_from="2024-01-01",
        valid_to=None,
        status="active",
        sources=("finance:F000123",),
    ),
    RelationshipView(
        rel_type="works_at",
        label="employs",
        direction="in",
        other_entity="person",
        other_master_id="PER-000451",
        other_title="K*** B***",
        valid_from=None,
        valid_to=None,
        status="active",
        sources=("hr:H000451", "crm:C001377"),
    ),
)
SOURCE_VIEW = SourceView(
    entity="organisation",
    source="crm:C000419",
    title="Velix Supplies Limited",
    status="active",
    linked_to="ORG-000419",
    held=True,
    values=(
        ValueView(
            attribute="name",
            label="Name",
            value="Velix Supplies Limited",
            masked=False,
            personal=False,
            critical=True,
            source="crm:C000419",
            decided_by=None,
            chip=None,
            age_days=0,
            pinned_until=None,
        ),
    ),
    approved_differs=("name",),
    versions=((None, NOW - timedelta(days=40), 812), (None, NOW - timedelta(hours=1), 5120)),
    open_tasks=(ROW_HELD_UPDATE.task_id,),
    rule_failures=(),
)
HUB_BADGES = HubBadges(engine="DuckDB", assistant="stub", local=True, entities=("organisation", "person"))
HUB_BADGES_SHARED = HubBadges(engine="Lakebase", assistant="stub", local=False, entities=("organisation",))

#: every sample, so a test can check that each dataclass of the module has one
ALL = (
    SERVICE_LEVELS,
    TASK_QUERY,
    STAGED,
    *ROWS,
    TASK_PAGE,
    LAST_PAGE,
    COUNTS_UNDER_CAP,
    COUNTS_AT_CAP,
    HEALTH,
    HEALTH_EMPTY,
    *STEPS_CLOSE_CALL,
    IMPACT_LINK,
    IMPACT_NONE,
    IMPACT_APPROVE,
    PREVIEW_APPROVE,
    *PREVIEW_APPROVE.rows,
    *CANDIDATES,
    CANDIDATE_PERSON,
    *COMPARE_CLOSE_CALL,
    *CASES,
    *CASE_HELD_UPDATE.actions,
    BREAKER_AGREEMENT,
    BREAKER_VOLUME,
    HEALTH_PAUSED,
    COUNTS_SAMPLES,
    *CHOICES,
    *CHECKPOINT_CASES,
    *TRAY_VIEWS_BLIND,
    REVEALED_PERSON,
    STAGING,
    TRAY_ENTRY_STAGED,
    TRAY_ENTRY_COMMITTED,
    TRAY_ENTRY_FAILED,
    *TRAY_VIEWS,
    TRAY_SETTLEMENT,
    MATCH_LABEL,
    FLUSH_REPORT,
    FLUSH_BUSY,
    RESOLUTION_GOLDEN,
    RESOLUTION_RETIRED,
    RESOLUTION_SOURCE,
    RESOLUTION_UNKNOWN,
    RECORD_HEADER,
    *VALUES,
    VALUE_MASKED,
    WHY,
    *WHY.runners_up,
    *MEMBERS,
    TIMELINE_PAGE,
    *TIMELINE_PAGE.events,
    TIMELINE_LAST,
    *RELATIONSHIPS,
    SOURCE_VIEW,
    HUB_BADGES,
    HUB_BADGES_SHARED,
)
