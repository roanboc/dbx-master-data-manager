"""The inbox: the health strip, the task grid (one keyed page of 50), the decide pane beside it, the
page controls, and the stores that keep the steward's working state (B.8.7). `SELECTED_TASK` is the one
source of truth; the pane re-renders only when it or the selected task's own `CASE_VERSION` changes; the
grid changes by row transactions; J, K and 1–3 run in the browser (`assets/inbox.js`), and decisions
reach `act` one at a time. Moving renders the next case (one read, prepared ahead by the prefetch) and
never claims a task: the first decision does (the tray service claims it when it stages), or Claim.

The callbacks I0–I11 call the pure functions below, so tests run them without Dash. A callback whose
output is on this page never takes an input from the shell directly: off the inbox its output would be
missing while its input is there, which Dash reports as an error. Clientside bridges copy the shell's
address, entity, settlements and decision keys into page-owned stores instead, through wildcard outputs
that match nothing on another page. The reveal modal sits inside the compare table with its button.

Everything a callback sends to the browser is a code, an ID, a masked title or a sentence built from
them; a revealed value is rendered once into the compare table and kept nowhere.

Owner: INBOX (B.8.7).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl

import dash_ag_grid as dag
import dash_mantine_components as dmc
from dash import ALL, ClientsideFunction, Input, Output, State, dcc, html, no_update, set_props
from dash import ctx as dash_ctx
from dash.development.base_component import Component

from mdm import capacity
from mdm.models.authority import ROLE_LABELS
from mdm.models.canonical import utcnow
from mdm.models.errors import Conflict, Forbidden, MdmError, NotFound
from mdm.models.safety import SAFE_TEXT_RE
from mdm.models.tasks import KIND_LABELS, TASK_KINDS, WAITS_TEXT, waits_for_restore
from mdm.models.workbench import (
    ALL_VIEWS,
    ESCALATION_REASONS,
    SAMPLES_VIEW,
    SNOOZE_HOURS,
    TaskPage,
    TaskRow,
    TrayEntry,
)
from mdm.services import display
from mdm.ui import context, ids, messages
from mdm.ui.components import compare, decide, health, reveal
from mdm.ui.components.band import band_words, score_text
from mdm.ui.components.common import count_text, duration, notify, page_title
from mdm.ui.components.icons import icon
from mdm.ui.components.rail import VIEW_LABELS
from mdm.ui.components.rail import href as rail_href
from mdm.ui.context import UiContext

#: the actions `act` takes: a decision key's action, or a menu item's "snooze:<hours>" / "escalate:<code>"
ACTS = ("link", "not_a_match", "approve", "reject", "claim", "undo")
#: the decisions a button names directly (its `Action.decision`)
DIRECT = (
    "link",
    "not_a_match",
    "keep_apart",
    "approve_update",
    "reject_update",
    "keep_orphan",
    "blind_link",
    "blind_none",
    "keep_decision",
)
#: a key's action, per case shape, as the decision it stages: on a quality sample L places the record in
#: the golden record chosen (a pair: the same) and N in none of them; on a dispute A keeps the first decision
BY_SHAPE: Mapping[str, Mapping[str, str]] = {
    "link": {"blind": "blind_link", "blind_pair": "blind_link", "disputed": "link"},
    "not_a_match": {
        "source": "not_a_match",
        "golden_pair": "keep_apart",
        "blind": "blind_none",
        "blind_pair": "blind_none",
    },
    "approve": {
        "held_update": "approve_update",
        "golden": "keep_orphan",
        "disputed": "keep_decision",
        "disputed_pair": "keep_decision",
    },
    "reject": {"held_update": "reject_update"},
}
#: the decisions that name the golden record chosen on screen (a candidate, or a blind review's choice)
NAMES_CHOICE = ("link", "blind_link")
#: the views that still hold a snoozed task (My queue and Team hide it until it wakes)
SHOWS_SNOOZED = ("breaching", "snoozed", "escalated")
#: refusals after which the case on screen is out of date and is rendered again
STALE_CODES = frozenset(
    {"record_changed", "already_staged", "claimed_by_another", "task_closed", "not_held", "unknown_task"}
)
#: the grid's columns: the task (line 1: its kind, in a mixed list only, and its title, a button that opens
#: it; line 2, muted, at most two lines with its full text in its title: the band, the reason, the
#: suggestion and the entity), and when it is due with who holds it under it. The cells are
#: React elements built from the row's own fields (`assets/inbox.js`), never HTML. No column sorts or
#: filters: a page is one slice of the queue, ordered by due time.
COLUMNS: tuple[dict[str, Any], ...] = (
    {"field": "title", "headerName": "Task", "cellRenderer": "MdmTask", "flex": 1, "minWidth": 200},
    {
        "field": "due",
        "headerName": "Due",
        "cellRenderer": "MdmDue",
        "width": 104,
        "cellClass": "mdm-due-cell",
    },
)
DEFAULT_COLUMN = {"sortable": False, "filter": False, "suppressMovable": True, "resizable": False}
GRID_OPTIONS = {
    "rowSelection": {"mode": "singleRow", "checkboxes": False, "enableClickSelection": True},
    # neither cells nor headers take focus: the row's button does, so Tab walks the rows' buttons and
    # leaves the grid, and the grid's own keys never fight the listener (J, K and the arrows move)
    "suppressCellFocus": True,
    "suppressHeaderFocus": True,
    "animateRows": False,
    "rowHeight": 56,
    "headerHeight": 32,
    "suppressDragLeaveHidesColumns": True,
    # an empty view says so once, in the heading and the pane; the grid adds nothing
    "localeText": {"noRowsToShow": " "},
}
ROW_CLASSES = {
    "mdm-staged": "params.data.staged",
    "mdm-breaching": "params.data.breaching",
    "mdm-claimed-other": "params.data.claimed_other",
}
#: the list and the pane: side by side from 1200 px (five twelfths, at least 480 px, and seven), stacked
#: below; with F the list is hidden and the pane grows into its place
QUEUE_PART = {"flex": "5 1 0", "minWidth": "min(480px, 100%)"}
DECIDE_PART = {"flex": "7 1 0", "minWidth": 0, "position": "relative"}
GRID_STYLE = {"height": "calc(100vh - 250px)", "minHeight": "320px", "width": "100%"}
HEADING_STYLE = {"fontSize": "0.95rem", "fontWeight": 600, "margin": "0 0 8px"}
#: the page's own copies of what the shell announces (see `ids`)
ADDRESS = ids.INBOX_ADDRESS
SETTLED_HERE = ids.INBOX_SETTLED
HEALTH_POLL = ids.HEALTH_POLL
ACT_REQUEST = ids.INBOX_ACT_REQUEST
CONSUMER_TEXT = "Your role, {role}, decides no tasks, so nothing waits here for you."
#: what an empty view says in the pane, per view (ALL_VIEWS)
EMPTY_VIEWS = {
    "mine": "Nothing waits in My queue.",
    "team": "No task is open.",
    "breaching": "Nothing is breaching.",
    "snoozed": "Nothing is snoozed.",
    "escalated": "Nothing is escalated.",
    SAMPLES_VIEW: "No quality sample waits for you.",
}
#: the actions that act on the task on screen, so they need its case drawn first (undo acts on the tray)
ON_THE_CASE = ("link", "not_a_match", "approve", "reject", "claim", *DIRECT)


@dataclass(frozen=True)
class ActResult:
    """What one act did, for the callback to hand the grid, the pane, the tray and the notifications."""

    advance: bool  # a decision was staged: select the next row that is not staged
    row: TaskRow | None  # the task's row again, for the grid's rowTransaction (None: leave it, or remove it)
    bump_case: bool  # the selected task's own state changed: render its case again
    bump_tray: bool  # the tray changed: refresh it
    notices: tuple[dict, ...] = ()  # notification payloads (a refusal's sentence, a confirmation)
    remove: bool = False  # the task left this view: take its row out of the grid
    touched: str | None = None  # the task the row belongs to (an undo may touch another than the selected)


# ---------------------------------------------------------------------------------------------- the query


def parse_query(query: Mapping[str, str | None], entities: Sequence[str] = ()) -> dict[str, str | None]:
    """The inbox's query as codes: a known view (default My queue), a known kind (none in Quality samples,
    which holds one kind), a published entity ("" for all) and a task ID of a safe shape."""
    view = query.get("view")
    view = view if view in ALL_VIEWS else "mine"
    kind = query.get("kind")
    entity = query.get("entity")
    task = query.get("task")
    return {
        "view": view,
        "kind": kind if kind in TASK_KINDS and view != SAMPLES_VIEW else None,
        "entity": entity if isinstance(entity, str) and entity in entities else "",
        "task": task
        if isinstance(task, str) and SAFE_TEXT_RE.match(task) and task.startswith("TSK-")
        else None,
    }


def query_from_search(search: str | None) -> dict[str, str]:
    """The address's query as codes and IDs: the inbox's keys only, each value of a safe shape."""
    found: dict[str, str] = {}
    for key, value in parse_qsl((search or "").lstrip("?"), keep_blank_values=False):
        if key in ("view", "kind", "task") and key not in found and SAFE_TEXT_RE.match(value):
            found[key] = value
    return found


def address_query(address: Mapping[str, Any], entities: Sequence[str]) -> dict[str, str | None]:
    """INBOX_QUERY for an address and the header's entity (the ADDRESS bridge's document)."""
    search = address.get("search")
    found: dict[str, str | None] = dict(query_from_search(search if isinstance(search, str) else None))
    entity = address.get("entity")
    found["entity"] = entity if isinstance(entity, str) else ""
    return query_store(parse_query(found, entities))


def new_query(address: Any, kept: Any, entities: Sequence[str]) -> dict[str, str | None] | None:
    """I0 without Dash: the query an address and the header's entity ask for, or None when it is the one
    on screen already, or the address is not the inbox's."""
    if not isinstance(address, Mapping) or (address.get("path") or "/") != "/":
        return None
    wanted = address_query(address, entities)
    return None if wanted == kept else wanted


def query_store(query: Mapping[str, str | None]) -> dict[str, str | None]:
    """INBOX_QUERY: the view, the kind and the entity, codes only."""
    return {
        "view": query.get("view") or "mine",
        "kind": query.get("kind"),
        "entity": query.get("entity") or "",
    }


def mixed_kinds(query: Mapping[str, Any] | None) -> bool:
    """Whether the list mixes kinds, so each row names its own: not under a kind, nor in Quality samples."""
    query = query or {}
    return not query.get("kind") and query.get("view") != SAMPLES_VIEW


def list_name(query: Mapping[str, str | None]) -> str:
    """ "My queue", "Team · Review"."""
    name = VIEW_LABELS.get(str(query.get("view") or "mine"), "My queue")
    kind = query.get("kind")
    return f"{name} · {KIND_LABELS[kind]}" if kind in KIND_LABELS else name


def page_label(query: Mapping[str, str | None], depth: int, count: int) -> str:
    """ "My queue: 51–100" (no total: a total would read the whole task table); "My queue: nothing
    waiting"."""
    if count <= 0:
        return f"{list_name(query)}: nothing waiting" if depth == 0 else f"{list_name(query)}: no more tasks"
    start = depth * capacity.INBOX_PAGE + 1
    return f"{list_name(query)}: {start}–{start + count - 1}"


def cursor_of(value: Any) -> tuple[str, str] | None:
    """A cursor kept in the browser, back as a (due time, task ID) pair; anything else is the first page."""
    if isinstance(value, (list, tuple)) and len(value) == 2 and all(isinstance(v, str) for v in value):
        return value[0], value[1]
    return None


def load_page(ctx: UiContext, query: Mapping[str, str], after: tuple[str, str] | None) -> TaskPage:
    """One page of the inbox for `query` after the cursor (I1); an empty page for a role that sees no
    tasks (it has no inbox)."""
    if not ctx.can("view_tasks"):
        return TaskPage(rows=(), after=None)
    return ctx.hub.inbox.page(
        str(query.get("view") or "mine"),
        actor=ctx.actor,
        entity=query.get("entity") or None,
        kind=query.get("kind") or None,
        after=after,
    )


# ---------------------------------------------------------------------------------------------- grid rows


def due_text(row: TaskRow, now: datetime) -> str:
    """ "7 h 40 min", "Breached 2 h ago"; for a staged row "Staged by you" (its countdown ticks beside it
    in the browser) or "Staged by another"."""
    if row.staged is not None:
        return "Staged by you" if row.staged.mine else "Staged by another"
    if row.due_at is None:
        return ""
    if waits_for_restore(row.due_at):
        return WAITS_TEXT
    text = duration(row.due_at - now)
    return text[:1].upper() + text[1:]


def claimed_text(row: TaskRow) -> str:
    """ "Claimed by you", "Claimed by Data steward (persona)", "" — with "escalated" and "snoozed"
    marked."""
    parts = [f"Claimed by {row.claimed_by}"] if row.claimed_by and row.staged is None else []
    if row.escalated:
        parts.append("escalated")
    if row.snoozed_until is not None:
        parts.append("snoozed")
    return " · ".join(parts)


def band_cell(row: TaskRow) -> tuple[str, str]:
    """(the band chip's text, its band class): "79 review"; "95 · kept apart by a rule" when a cannot-link
    rule holds; nothing for a held update, which is approved or rejected, not scored."""
    if row.kind == "held" or (row.score is None and row.band is None):
        return "", ""
    if row.kept_apart:
        score = score_text(row.score)
        return (f"{score} · kept apart by a rule" if score else "kept apart by a rule"), "distinct"
    band = row.band if row.band in ("auto", "review", "distinct") else "distinct"
    return " ".join(part for part in (score_text(row.score), band_words(row.band)) if part), band


def second_line(band_text: str, entity_label: str, row: TaskRow) -> str:
    """The second line's full text, for its `title` (the line itself is cut after two lines): "79 review
    · hinges on postcode · Link to PER-000239 · Person"."""
    return " · ".join(part for part in (band_text, row.reason, row.suggestion, entity_label) if part)


def grid_row(
    row: TaskRow, now: datetime | None = None, *, entity_shown: bool = True, kind_shown: bool = True
) -> dict:
    """A task row as the grid's row dict: codes, IDs and masked text only, never HTML. `entity_shown`
    false (the header's entity filter is set) leaves the entity out of the second line; `kind_shown` false
    (the list holds one kind) leaves the kind out of the first."""
    now = now if now is not None else utcnow()
    band_text, band = band_cell(row)
    staged = row.staged
    entity_label = row.entity.replace("_", " ").capitalize() if entity_shown else ""
    return {
        "task_id": row.task_id,
        "kind": row.kind,
        "kind_label": row.kind_label if kind_shown else "",
        "entity": row.entity,
        "entity_label": entity_label,
        "title": row.title,
        "subject": row.subject,
        "band": band,
        "band_text": band_text,
        "suggestion": row.suggestion,
        "reason": row.reason,
        "second_title": second_line(band_text, entity_label, row),
        "due": due_text(row, now),
        "breaching": bool(row.breaching) and staged is None,
        "claimed": claimed_text(row),
        "claimed_other": row.claimed_by not in (None, "you"),
        "staged": staged is not None,
        "staged_ms": int(staged.deadline.timestamp() * 1000) if staged is not None and staged.mine else None,
    }


def select_after_load(rows: Sequence[TaskRow], moved: str | None, selected: str | None) -> str | None:
    """The row I1 selects: the selected task when it is on the page; else the first row after "next" or a
    new list, the last after "prev"."""
    names = [row.task_id for row in rows]
    if not names:
        return None
    if moved == "prev":
        return names[-1]
    if moved == "next":
        return names[0]
    return selected if selected in names else names[0]


def load(
    ctx: UiContext, query: Mapping[str, Any], cursor: Mapping[str, Any] | None, selected: str | None
) -> tuple[list[dict], Any, str, bool, bool, Any, dict | None]:
    """I1 without Dash: (rowData, selectedRows, page label, previous disabled, next disabled, the next
    cursor, a failure's notification or None)."""
    stack = list((cursor or {}).get("stack") or [])
    moved = (cursor or {}).get("moved")
    after = cursor_of(stack[-1]) if stack else None
    page, failure = context.guarded(load_page, ctx, query, after)
    if page is None:
        return [], [], page_label(query, len(stack), 0), not stack, True, None, failure
    now = utcnow()
    chosen = select_after_load(page.rows, moved, selected)
    shown = not query.get("entity")
    rows = [grid_row(row, now, entity_shown=shown, kind_shown=mixed_kinds(query)) for row in page.rows]
    return (
        rows,
        {"ids": [chosen]} if chosen else [],
        page_label(query, len(stack), len(rows)),
        not stack,
        page.after is None,
        list(page.after) if page.after else None,
        None,
    )


# ---------------------------------------------------------------------------------------------- acting


def _attempt(fn, *args, **kwargs) -> tuple[Any, MdmError | Exception | None, dict | None]:
    """`context.guarded` that also hands back the error, so `act` can tell a stale case from a refusal."""
    try:
        return fn(*args, **kwargs), None, None
    except Exception as error:  # noqa: BLE001 - every failure becomes one safe sentence
        context.log_failure(error)
        return None, error, context.notice(error)


def _stale(error: BaseException | None) -> bool:
    return isinstance(error, MdmError) and error.code in STALE_CODES


def _row_again(ctx: UiContext, task_id: str) -> tuple[TaskRow | None, bool]:
    """The task's row now, and whether it is gone from the list (decided or unknown)."""
    row, error, _ = _attempt(ctx.hub.inbox.row, task_id, actor=ctx.actor)
    if row is None:
        return None, isinstance(error, (Conflict, NotFound))
    return row, False


def stamp_of(case: Any) -> dict:
    """CASE_STAMP for a case on screen: the task, the record's event or the task's version it was built on,
    its shape and the candidate it names by default; codes and IDs only."""
    return {
        "task_id": case.row.task_id,
        "event_id": case.event_id,
        "task_version": case.task_version,
        "shape": case.shape,
        "default": case.default_candidate,
    }


def stamped(stamp: Any, task_id: str | None) -> Mapping[str, Any] | None:
    """The stamp when it is the case of `task_id` (the pane shows that task), else None."""
    if isinstance(stamp, Mapping) and task_id and stamp.get("task_id") == task_id:
        return stamp
    return None


def _code(stamp: Mapping[str, Any], name: str) -> str | None:
    value = stamp.get(name)
    return value if isinstance(value, str) else None


def loading_result(task_id: str | None) -> ActResult:
    """What an action on a task whose case is not drawn yet does: nothing, and says so."""
    note = notify(
        messages.sentence_for("case_loading"),
        "gray",
        title=messages.title_for("case_loading"),
        key="case_loading",
    )
    return ActResult(False, None, False, False, (note[0],), touched=task_id)


def decision_for(action: str, shape: str | None) -> str | None:
    """The decision an action stages on a case of `shape`: a button's decision as it is; a key's by shape
    (N is not a match, or keep apart for two golden records; A approves an update or keeps an orphan)."""
    mapped = BY_SHAPE.get(action, {}).get(shape or "")
    if mapped is not None:
        return mapped
    return action if action in DIRECT else None


def _stage(
    ctx: UiContext,
    task_id: str,
    decision: str,
    *,
    candidate: str | None,
    seen_event: str | None,
    seen_task: str | None,
) -> ActResult:
    target = candidate if decision in NAMES_CHOICE else None
    entry, error, failure = _attempt(
        ctx.hub.tray.stage,
        task_id,
        decision,
        actor=ctx.actor,
        target=target,
        seen_event=seen_event,
        seen_task=seen_task,
    )
    if entry is None:
        if failure is not None and isinstance(error, MdmError) and error.code == "record_changed":
            failure = {**failure, "message": messages.STAGE_RECORD_CHANGED}  # nothing waited yet
        return ActResult(False, None, _stale(error), False, (failure,) if failure else (), touched=task_id)
    row, gone = _row_again(ctx, task_id)
    label = row.staged.label if row is not None and row.staged is not None else "Your decision"
    seconds = ctx.settings.undo_seconds
    window = f"{seconds} s" if seconds < 120 else duration(timedelta(seconds=seconds))
    note = notify(f"{label}. It commits in {window}; press U to undo it.", "teal", title="In the tray")
    return ActResult(True, row, False, True, (note[0],), remove=gone, touched=task_id)


def _undo(ctx: UiContext, task_id: str | None) -> ActResult:
    entry: TrayEntry | None = None
    if task_id:
        entry, failure = context.guarded(ctx.hub.tray.undo_for_task, task_id, actor=ctx.actor)
        if failure is not None:
            return ActResult(False, None, False, True, (failure,), touched=task_id)
    if entry is None:
        entry, failure = context.guarded(ctx.hub.tray.undo_last, actor=ctx.actor)
        if failure is not None:
            return ActResult(False, None, False, True, (failure,), touched=task_id)
    if entry is None:
        note = notify("Nothing of yours is waiting in the tray.", "gray", title="Nothing to undo")
        return ActResult(False, None, False, False, (note[0],), touched=task_id)
    label = display.tray_label(entry.decision, entry.subject, entry.target)
    row, gone = _row_again(ctx, entry.task_id)
    note = notify(f"Undone: {label}. The task is back in your queue.", "teal", title="Undone")
    return ActResult(
        False, row, entry.task_id == task_id, True, (note[0],), remove=gone, touched=entry.task_id
    )


def _claim(ctx: UiContext, task_id: str) -> ActResult:
    expiry, error, failure = _attempt(ctx.hub.inbox.claim, task_id, actor=ctx.actor)
    if expiry is None:
        return ActResult(False, None, _stale(error), False, (failure,) if failure else (), touched=task_id)
    row, gone = _row_again(ctx, task_id)
    minutes = ctx.settings.claim_minutes
    note = notify(
        f"Claimed for {minutes} minutes. Other stewards see it is yours until then.", "teal", title="Claimed"
    )
    return ActResult(False, row, True, False, (note[0],), remove=gone, touched=task_id)


def _snooze(ctx: UiContext, task_id: str, hours: int, view: str) -> ActResult:
    until, error, failure = _attempt(ctx.hub.inbox.snooze, task_id, actor=ctx.actor, hours=hours)
    if until is None:
        return ActResult(False, None, _stale(error), False, (failure,) if failure else (), touched=task_id)
    row, gone = _row_again(ctx, task_id)
    leaves = gone or view not in SHOWS_SNOOZED
    span = "until tomorrow" if hours == 24 else f"for {hours} hour{'s' if hours != 1 else ''}"
    note = notify(f"Snoozed {span}. It leaves My queue until then.", "teal", title="Snoozed")
    return ActResult(
        leaves, None if leaves else row, not leaves, False, (note[0],), remove=leaves, touched=task_id
    )


def _escalate(ctx: UiContext, task_id: str, reason: str) -> ActResult:
    _done, error, failure = _attempt(ctx.hub.inbox.escalate, task_id, actor=ctx.actor, reason=reason)
    if failure is not None:
        return ActResult(False, None, _stale(error), False, (failure,), touched=task_id)
    row, gone = _row_again(ctx, task_id)
    words = decide.ESCALATION_LABELS.get(reason, reason).lower()
    note = notify(
        f"Escalated: {words}. It shows under Escalated until someone decides it.", "teal", title="Escalated"
    )
    return ActResult(False, row, True, False, (note[0],), remove=gone, touched=task_id)


def act(
    ctx: UiContext,
    action: str,
    *,
    task_id: str | None,
    candidate: str | None = None,
    stamp: Mapping[str, Any] | None = None,
    view: str = "mine",
) -> ActResult:
    """One action on the selected task (I7): `link` stages a link to `candidate` (else the candidate the
    case on screen names by default); `not_a_match` stages not a match or keep apart by the case's shape;
    `approve` approve_update or keep_orphan; `reject` reject_update; `snooze:<h>`, `escalate:<code>`,
    `claim`; `undo` the selected task's entry, else the last. Every service call through
    `context.guarded`. `stamp` is CASE_STAMP, the case on screen: an action on a task whose case has not
    been drawn yet does nothing and says so, and a decision is checked against the event or version the
    case was built on. `view` is the list on screen, which decides whether a snoozed task leaves it."""
    if action == "undo":
        return _undo(ctx, task_id)
    if not task_id:
        note = notify("Choose a task in the list first.", "gray", title="No task selected")
        return ActResult(False, None, False, False, (note[0],))
    seen = stamped(stamp, task_id)
    if seen is None:
        return loading_result(task_id)
    if action == "claim":
        return _claim(ctx, task_id)
    kind, _, argument = action.partition(":")
    if kind == "snooze":
        hours = int(argument) if argument.isdigit() else -1
        if hours not in SNOOZE_HOURS:
            return ActResult(
                False, None, False, False, (context.notice(Forbidden("bad_snooze")),), touched=task_id
            )
        return _snooze(ctx, task_id, hours, view)
    if kind == "escalate":
        if argument not in ESCALATION_REASONS:
            return ActResult(
                False, None, False, False, (context.notice(Forbidden("bad_escalation")),), touched=task_id
            )
        return _escalate(ctx, task_id, argument)
    decision = decision_for(action, _code(seen, "shape"))
    if decision is None:
        refused = context.notice(Forbidden("decision_not_offered"))
        return ActResult(False, None, False, False, (refused,), touched=task_id)
    target = candidate or (_code(seen, "default") if decision == "link" else None)
    return _stage(
        ctx,
        task_id,
        decision,
        candidate=target,
        seen_event=_code(seen, "event_id"),
        seen_task=_code(seen, "task_version"),
    )


def requested_action(request: Any) -> str | None:
    """The action an ACT_REQUEST document asks for, when it is one `act` takes: a decision key's
    (ACTS), a button's decision, or a menu item's "snooze:<hours>" / "escalate:<code>"; else None."""
    action = request.get("action") if isinstance(request, Mapping) else None
    if not isinstance(action, str):
        return None
    if action in ACTS or action in DIRECT:
        return action
    kind, _, argument = action.partition(":")
    if kind == "snooze" and argument in {str(hours) for hours in SNOOZE_HOURS}:
        return action
    if kind == "escalate" and argument in ESCALATION_REASONS:
        return action
    return None


def result_store(
    result: ActResult,
    task_id: str | None,
    previous: Any,
    now: datetime | None = None,
    *,
    entity_shown: bool = True,
    kind_shown: bool = True,
) -> dict:
    """ACT_RESULT: what the advance callback (I8) needs — the task acted on, whether to move on, a counter
    so every act is a change, and the grid row to update or remove (codes, IDs and masked text only)."""
    n = (previous.get("n") if isinstance(previous, dict) and isinstance(previous.get("n"), int) else 0) + 1
    return {
        "task_id": task_id,
        "advance": result.advance,
        "n": n,
        "touched": result.touched,
        "row": grid_row(result.row, now, entity_shown=entity_shown, kind_shown=kind_shown)
        if result.row is not None
        else None,
        "remove": result.remove,
    }


# ---------------------------------------------------------------------------------------------- the case


def empty_view(ctx: UiContext, query: Mapping[str, Any] | None) -> Component:
    """The pane of a view with nothing in it: "Nothing is breaching. Team has 67 open tasks." with a link
    to Team; the plain line when the view is not empty (no task selected yet)."""
    query = query or {}
    view = str(query.get("view") or "mine")
    entity = query.get("entity") or None
    counts, _failure = context.guarded(ctx.hub.inbox.counts, actor=ctx.actor, entity=entity)
    if counts is None or counts.views.get(view, 0) > 0 or query.get("kind"):
        return decide.empty_pane()
    words = EMPTY_VIEWS.get(view, "Nothing waits in this view.")
    team = counts.views.get("team", 0)
    if view in ("team", SAMPLES_VIEW) or team <= 0:
        return decide.empty_pane(words)
    tasks = "1 open task" if team == 1 else f"{count_text(team)} open tasks"
    return decide.empty_pane(
        [f"{words} Team has {tasks}. ", dmc.Anchor("Open Team", href=rail_href("team"), inherit=True)]
    )


def case_view(
    ctx: UiContext,
    task_id: str | None,
    *,
    chosen: str | None = None,
    next_hint: str | None = None,
    query: Mapping[str, Any] | None = None,
) -> tuple[Component, dict | None]:
    """I5 without Dash: the pane for the selected task and CASE_STAMP (`stamp_of`, codes only); prepares
    the next row's case while the steward reads. With no task, what the view on screen (`query`) holds."""
    if not task_id:
        return empty_view(ctx, query), None
    case, error, failure = _attempt(ctx.hub.decisions.case, task_id, actor=ctx.actor)
    if case is None:
        if isinstance(error, (Conflict, NotFound)) and getattr(error, "code", "") in (
            "task_closed",
            "unknown_task",
        ):
            return decide.empty_pane(decide.DECIDED), None
        text = (failure or {}).get("message") or decide.DECIDED
        return decide.empty_pane(text), None
    context.prefetch(ctx, next_hint if next_hint != task_id else None)
    return decide.render(case, chosen=chosen), stamp_of(case)


def reveal_view(
    ctx: UiContext, task_id: str | None, reason: Any, stamp: Any = None
) -> tuple[Component | None, dict]:
    """I11 without Dash: the compare table in clear, once, and the notification; nothing when no reason
    is chosen (its sentence instead), and nothing while the pane shows another task than `task_id`
    (`stamp`, CASE_STAMP), so a reveal logs and shows the case on screen only. The headers come from the
    same read as the values. The values are rendered, never kept."""
    code = reveal.checked_reason(reason)
    if code is None:
        return None, notify(reveal.REASON_REQUIRED, "yellow", title="Choose a reason", key="reason_required")[
            0
        ]
    if not task_id:
        return None, notify("Choose a task in the list first.", "gray", title="No task selected")[0]
    if stamped(stamp, task_id) is None:
        return None, loading_result(task_id).notices[0]
    shown, failure = context.guarded(ctx.hub.decisions.reveal, task_id, actor=ctx.actor, reason=code)
    if shown is None:
        return None, failure or {}
    table = compare.render(shown.columns, shown.compare, revealed=True)
    return table, notify(f"Values shown; logged ({shown.logged} entries).", "teal", title="Shown")[0]


def settled_updates(
    ctx: UiContext,
    settled: Any,
    on_page: Sequence[str],
    selected: str | None,
    *,
    entity_shown: bool = True,
    kind_shown: bool = True,
) -> tuple[dict | None, bool]:
    """I9 without Dash: the grid transaction for the settled entries whose rows are on the page (a
    committed one leaves; an undone or failed one is read again, its staged marker cleared), and whether
    the selected task is among them."""
    if not isinstance(settled, list):
        return None, False
    present = set(on_page)
    remove: list[dict] = []
    update: list[dict] = []
    touched = False
    now = utcnow()
    for entry in settled:
        if not isinstance(entry, dict):
            continue
        task_id = entry.get("task_id")
        if not isinstance(task_id, str):
            continue
        touched = touched or task_id == selected
        if task_id not in present:
            continue
        if entry.get("status") == "committed":
            remove.append({"task_id": task_id})
            continue
        row, gone = _row_again(ctx, task_id)
        if row is not None:
            update.append(grid_row(row, now, entity_shown=entity_shown, kind_shown=kind_shown))
        elif gone:
            remove.append({"task_id": task_id})
    if not remove and not update:
        return None, touched
    transaction: dict[str, Any] = {"async": False}
    if remove:
        transaction["remove"] = remove
    if update:
        transaction["update"] = update
    return transaction, touched


# ---------------------------------------------------------------------------------------------- layout


def _grid(rows: list[dict], selected: str | None) -> Component:
    return dag.AgGrid(
        id=ids.INBOX_GRID,
        rowData=rows,
        columnDefs=[dict(column) for column in COLUMNS],
        defaultColDef=dict(DEFAULT_COLUMN),
        getRowId="params.data.task_id",
        rowClassRules=dict(ROW_CLASSES),
        dashGridOptions=dict(GRID_OPTIONS),
        selectedRows={"ids": [selected]} if selected else [],
        className="mdm-grid",
        style=GRID_STYLE,
    )


def _stores(
    query: Mapping[str, Any],
    after: Any,
    selected: str | None,
    next_hint: str | None,
) -> list[Component]:
    return [
        dcc.Store(id=ids.INBOX_QUERY, storage_type="memory", data=query_store(query)),
        dcc.Store(id=ids.INBOX_CURSOR, storage_type="memory", data={"stack": [], "moved": None}),
        dcc.Store(id=ids.PAGE_AFTER, storage_type="memory", data=after),
        dcc.Store(id=ids.SELECTED_TASK, storage_type="memory", data=selected),
        dcc.Store(id=ids.SELECTED_CANDIDATE, storage_type="memory", data=None),
        dcc.Store(id=ids.NEXT_HINT, storage_type="memory", data=next_hint),
        dcc.Store(id=ids.CASE_VERSION, storage_type="memory", data=0),
        dcc.Store(id=ids.CASE_STAMP, storage_type="memory", data=None),
        dcc.Store(id=ids.ACT_RESULT, storage_type="memory", data=None),
        dcc.Store(id=ADDRESS, storage_type="memory", data=None),
        dcc.Store(id=SETTLED_HERE, storage_type="memory", data=None),
        dcc.Store(id=ids.INBOX_TRAY, storage_type="memory", data=None),
        dcc.Store(id=ACT_REQUEST, storage_type="memory", data=None),
        dcc.Interval(id=HEALTH_POLL, interval=capacity.COUNTS_REFRESH_SECONDS * 1000),
    ]


def full_width_button() -> Component:
    """The pane's full-width toggle (F): outside the pane, so a new case keeps its state; its name says
    what it does next, "Full width (F)" or "Show the list (F)"."""
    name = f"{decide.FULL_WIDTH} (F)"
    return dmc.Tooltip(
        dmc.ActionIcon(
            icon("expand"),
            id=ids.PANE_FULL,
            variant="default",
            size="lg",
            className="mdm-pane-full",
            **{"aria-label": name, "aria-keyshortcuts": "F"},
        ),
        id=ids.PANE_FULL_TIP,
        label=name,
        withArrow=True,
        position="left",
    )


def _page(
    health_strip: Component | str,
    label: str,
    grid: Component,
    prev_disabled: bool,
    next_disabled: bool,
    stores: list[Component],
    pane: Component | None = None,
) -> Component:
    queue = html.Div(
        [
            html.Div(health_strip, id=ids.HEALTH_STRIP),
            html.H2(label, id=ids.PAGE_LABEL, className="mdm-queue-heading", style=HEADING_STYLE),
            html.Div(grid, role="region", className="mdm-queue", **{"aria-label": "Tasks"}),
            dmc.Group(
                [
                    dmc.Button(
                        "Previous page",
                        id=ids.PAGE_PREV,
                        variant="default",
                        size="xs",
                        disabled=prev_disabled,
                    ),
                    dmc.Button(
                        "Next page", id=ids.PAGE_NEXT, variant="default", size="xs", disabled=next_disabled
                    ),
                ],
                gap="xs",
                mt="xs",
                className="mdm-paging",  # a button with no page on its side is hidden, not greyed
            ),
        ]
    )
    return html.Div(
        [
            page_title("Inbox", hidden=True),
            dmc.Flex(
                [
                    html.Div(queue, className="mdm-inbox-queue", style=QUEUE_PART),
                    html.Div(
                        [
                            full_width_button(),
                            html.Div(
                                pane if pane is not None else decide.empty_pane(),
                                id=ids.DECIDE_PANE,
                                className="mdm-decide-scroll",
                            ),
                        ],
                        className="mdm-inbox-decide",
                        style=DECIDE_PART,
                    ),
                ],
                direction={"base": "column", "lg": "row"},
                gap="md",
            ),
            *stores,
        ],
        id=ids.INBOX,
        className="mdm-inbox",
        **{"data-mdm-keys": "on"},
    )


def skeleton() -> Component:
    """The page's components with empty children and no service call, for the validation layout."""
    pane = html.Div(
        [
            html.Div([reveal.open_button("decide"), reveal.modal("decide")], id=ids.DECIDE_COMPARE),
            dmc.RadioGroup([], id=ids.CANDIDATE_CHOICE),
            html.Div(id=ids.WHY_SECTION),
            html.Div(id=ids.ACTION_REASONS),
            dmc.Button("Previous task", id=ids.PREV_TASK),
            dmc.Button("Next task", id=ids.NEXT_TASK),
        ]
    )
    return _page("", "", _grid([], None), True, True, _stores(query_store({}), None, None, None), pane)


def consumer_page(ctx: UiContext) -> Component:
    """The short page of a role that sees no tasks."""
    role = ROLE_LABELS.get(ctx.actor.role, ctx.actor.role).lower()
    return html.Div(
        [page_title("Inbox"), html.P(CONSUMER_TEXT.format(role=role), className="mdm-empty")],
        className="mdm-inbox-none",
    )


def layout(ctx: UiContext, query: Mapping[str, str]) -> Component:
    """The inbox for `query` (`view`, `kind`, `task`, codes and IDs only), or for a consumer one short
    page: "Your role, consumer, has no inbox. …". The first page, its selection and the health strip are
    built here; the decide pane is rendered by I5 on arrival."""
    if not ctx.can("view_tasks"):
        return consumer_page(ctx)
    parsed = parse_query(query, ctx.badges.entities)
    rows, _selected_rows, label, prev_disabled, next_disabled, after, failure = load(
        ctx, parsed, None, parsed["task"]
    )
    names = [row["task_id"] for row in rows]
    selected = parsed["task"] or (names[0] if names else None)
    next_hint = None
    if selected in names:
        index = names.index(selected)
        next_hint = names[index + 1] if index + 1 < len(names) else None
    strip, _failure = context.guarded(ctx.hub.inbox.health, actor=ctx.actor)
    strip_view: Component | str = health.render(strip, utcnow()) if strip is not None else ""
    grid = _grid(rows, selected if selected in names else None)
    if failure is not None:
        label = failure.get("message", label)
    return _page(
        strip_view, label, grid, prev_disabled, next_disabled, _stores(parsed, after, selected, next_hint)
    )


# ---------------------------------------------------------------------------------------------- callbacks


def _request(persona: Any) -> UiContext | None:
    found, _failure = context.guarded(context.current, persona)
    return found


def register(app) -> None:
    """Registers I0–I11 on `app` (the clientside ones from `assets/inbox.js`)."""

    # the bridges: the shell's address, entity and settlements, copied onto the page while it is on screen
    app.clientside_callback(
        ClientsideFunction(namespace="mdm_inbox", function_name="bridgeAddress"),
        Output({"type": ids.INBOX_QUERY, "part": ALL}, "data"),
        Input(ids.URL, "search"),
        Input(ids.ENTITY, "data"),
        State(ids.URL, "pathname"),
        prevent_initial_call=True,
    )
    app.clientside_callback(
        ClientsideFunction(namespace="mdm_inbox", function_name="bridgeSettled"),
        Output({"type": ids.SETTLED, "page": ALL}, "data"),
        Input(ids.SETTLED, "data"),
        prevent_initial_call=True,
    )
    app.clientside_callback(
        ClientsideFunction(namespace="mdm_inbox", function_name="bridgeSettled"),
        Output({"type": ids.TRAY_STATE, "page": ALL}, "data"),
        Input(ids.TRAY_STATE, "data"),
        prevent_initial_call=True,
    )

    # I0 and I1 as one: a new address or entity gives a new query and its first page; a page button a
    # page of the same query (one round trip either way)
    @app.callback(
        Output(ids.INBOX_GRID, "rowData"),
        Output(ids.INBOX_GRID, "selectedRows"),
        Output(ids.PAGE_LABEL, "children"),
        Output(ids.PAGE_PREV, "disabled"),
        Output(ids.PAGE_NEXT, "disabled"),
        Output(ids.PAGE_AFTER, "data"),
        Output(ids.INBOX_QUERY, "data"),
        Output(ids.INBOX_CURSOR, "data"),
        Output(ids.SELECTED_TASK, "data", allow_duplicate=True),
        Input(ADDRESS, "data"),
        Input(ids.INBOX_CURSOR, "data"),
        State(ids.INBOX_QUERY, "data"),
        State(ids.PERSONA, "data"),
        State(ids.SELECTED_TASK, "data"),
        prevent_initial_call=True,
    )
    def load_list(address, cursor, kept, persona, selected):
        request_ctx = _request(persona)
        if request_ctx is None:
            return (no_update,) * 9
        query_out: Any = no_update
        cursor_out: Any = no_update
        if dash_ctx.triggered_id == ADDRESS:
            query = new_query(address, kept, request_ctx.badges.entities)
            if query is None:
                return (no_update,) * 9
            cursor = {"stack": [], "moved": None}
            query_out, cursor_out = query, cursor
        else:
            query = kept or {}
        rows, selected_rows, label, prev_off, next_off, after, failure = load(
            request_ctx, query, cursor, selected
        )
        if failure is not None:
            set_props(ids.NOTIFY, {"sendNotifications": [failure]})
        # a list with nothing in it: no task is selected, and the pane says what the view holds
        emptied = None if not rows and failure is None else no_update
        return rows, selected_rows, label, prev_off, next_off, after, query_out, cursor_out, emptied

    # I2: the page buttons
    app.clientside_callback(
        ClientsideFunction(namespace="mdm_inbox", function_name="pageMove"),
        Output(ids.INBOX_CURSOR, "data", allow_duplicate=True),
        Input(ids.PAGE_PREV, "n_clicks"),
        Input(ids.PAGE_NEXT, "n_clicks"),
        State(ids.INBOX_CURSOR, "data"),
        State(ids.PAGE_AFTER, "data"),
        prevent_initial_call=True,
    )

    # I3: the health strip, on its poll and whenever the tray or a decision changed something, so its
    # figures keep step with the header's tray
    @app.callback(
        Output(ids.HEALTH_STRIP, "children"),
        Input(HEALTH_POLL, "n_intervals"),
        Input(SETTLED_HERE, "data"),
        Input(ids.INBOX_TRAY, "data"),
        Input(ids.ACT_RESULT, "data"),
        State(ids.PERSONA, "data"),
        prevent_initial_call=True,
    )
    def refresh_health(_polls, _settled, _tray, _acted, persona):
        request_ctx = _request(persona)
        if request_ctx is None or not request_ctx.can("view_tasks"):
            return no_update
        found, _failure = context.guarded(request_ctx.hub.inbox.health, actor=request_ctx.actor)
        return health.render(found, utcnow()) if found is not None else no_update

    # I4: the selection, debounced, in the browser
    app.clientside_callback(
        ClientsideFunction(namespace="mdm_inbox", function_name="select"),
        Output(ids.SELECTED_TASK, "data"),
        Output(ids.SELECTED_CANDIDATE, "data"),
        Output(ids.NEXT_HINT, "data"),
        Input(ids.INBOX_GRID, "selectedRows"),
        State(ids.SELECTED_TASK, "data"),
    )

    # I5: the case of the selected task
    @app.callback(
        Output(ids.DECIDE_PANE, "children"),
        Output(ids.CASE_STAMP, "data"),
        Input(ids.SELECTED_TASK, "data"),
        Input(ids.CASE_VERSION, "data"),
        State(ids.PERSONA, "data"),
        State(ids.NEXT_HINT, "data"),
        State(ids.SELECTED_CANDIDATE, "data"),
        State(ids.CASE_STAMP, "data"),
        State(ids.INBOX_QUERY, "data"),
    )
    def render_case(task_id, _version, persona, next_hint, chosen, stamp, query):
        request_ctx = _request(persona)
        if request_ctx is None:
            return no_update, no_update
        same = isinstance(stamp, dict) and stamp.get("task_id") == task_id
        return case_view(
            request_ctx,
            task_id,
            chosen=chosen if same else None,
            next_hint=next_hint,
            query=query if isinstance(query, dict) else None,
        )

    # I6: a candidate chosen (1–3 or a click), in the browser: its panel and impact line show, and the
    # link button names it (disabled, when a rule blocks it)
    app.clientside_callback(
        ClientsideFunction(namespace="mdm_inbox", function_name="chooseCandidate"),
        Output(ids.SELECTED_CANDIDATE, "data", allow_duplicate=True),
        Output({"type": ids.CANDIDATE_PANEL, "master_id": ALL}, "className"),
        Output({"type": ids.ACTION, "decision": ALL}, "children"),
        Output({"type": ids.CANDIDATE_IMPACT, "master_id": ALL}, "className"),
        Output({"type": ids.ACTION, "decision": ALL}, "variant"),
        Output({"type": ids.ACTION, "decision": ALL}, "disabled"),
        Input(ids.CANDIDATE_CHOICE, "value"),
        prevent_initial_call=True,
    )

    # F: the pane full width, and back
    app.clientside_callback(
        ClientsideFunction(namespace="mdm_inbox", function_name="fullWidth"),
        Output(ids.INBOX, "className"),
        Output(ids.PANE_FULL, "aria-label"),
        Output(ids.PANE_FULL_TIP, "label"),
        Input(ids.PANE_FULL, "n_clicks"),
        State(ids.INBOX, "className"),
        prevent_initial_call=True,
    )

    # I7a: a decision key, an action button or a menu item, as one request on the page (in the browser;
    # a button drawn again with no click asks for nothing, and off the inbox nothing listens)
    app.clientside_callback(
        ClientsideFunction(namespace="mdm_inbox", function_name="actRequest"),
        Output({"type": ids.KEY_EVENT, "page": ALL}, "data"),
        Input(ids.KEY_EVENT, "data"),
        Input({"type": ids.ACTION, "decision": ALL}, "n_clicks"),
        Input({"type": ids.SNOOZE_OPTION, "hours": ALL}, "n_clicks"),
        Input({"type": ids.ESCALATE_OPTION, "reason": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )

    # I7: act
    @app.callback(
        Output(ids.ACT_RESULT, "data"),
        Output(ids.CASE_VERSION, "data", allow_duplicate=True),
        Output(ids.TRAY_VERSION, "data", allow_duplicate=True),
        Output(ids.NOTIFY, "sendNotifications", allow_duplicate=True),
        Input(ACT_REQUEST, "data"),
        State(ids.SELECTED_TASK, "data"),
        State(ids.SELECTED_CANDIDATE, "data"),
        State(ids.CASE_STAMP, "data"),
        State(ids.PERSONA, "data"),
        State(ids.INBOX_QUERY, "data"),
        State(ids.ACT_RESULT, "data"),
        State(ids.CASE_VERSION, "data"),
        State(ids.TRAY_VERSION, "data"),
        prevent_initial_call=True,
    )
    def act_on_task(
        request,
        task_id,
        candidate,
        stamp,
        persona,
        query,
        previous,
        case_version,
        tray_version,
    ):
        action = requested_action(request)
        if action is None:
            return (no_update,) * 4
        request_ctx = _request(persona)
        if request_ctx is None:
            note = notify("Your session could not be read. Reload the page.", "red")
            return (
                result_store(ActResult(False, None, False, False), task_id, previous),
                no_update,
                no_update,
                note,
            )
        task_id = task_id if isinstance(task_id, str) else None
        pane = request.get("task") if isinstance(request, dict) else None
        if action in ON_THE_CASE or action.startswith(("snooze:", "escalate:")):
            # the pane, the selection and the stamp must name one task: a key pressed while the next case
            # loads acts on nothing, never on a case the steward has not seen
            if task_id and (pane != task_id or stamped(stamp, task_id) is None):
                result = loading_result(task_id)
                return (
                    result_store(result, task_id, previous),
                    no_update,
                    no_update,
                    list(result.notices),
                )
        result = act(
            request_ctx,
            action,
            task_id=task_id,
            candidate=candidate if isinstance(candidate, str) else None,
            stamp=stamp if isinstance(stamp, dict) else None,
            view=str((query or {}).get("view") or "mine"),
        )
        return (
            result_store(
                result,
                task_id,
                previous,
                entity_shown=not (query or {}).get("entity"),
                kind_shown=mixed_kinds(query),
            ),
            (case_version or 0) + 1 if result.bump_case else no_update,
            (tray_version or 0) + 1 if result.bump_tray else no_update,
            list(result.notices) if result.notices else no_update,
        )

    # I8: the grid takes the row back and the selection advances to the next row not staged, in the
    # browser; with none left, the pane shows the staged line (CASE_VERSION). Frees the key listener.
    app.clientside_callback(
        ClientsideFunction(namespace="mdm_inbox", function_name="advance"),
        Output(ids.CASE_VERSION, "data", allow_duplicate=True),
        Input(ids.ACT_RESULT, "data"),
        State(ids.SELECTED_TASK, "data"),
        State(ids.CASE_VERSION, "data"),
        prevent_initial_call=True,
    )

    # I9: decisions the tray settled
    @app.callback(
        Output(ids.INBOX_GRID, "rowTransaction", allow_duplicate=True),
        Output(ids.CASE_VERSION, "data", allow_duplicate=True),
        Input(SETTLED_HERE, "data"),
        State(ids.SELECTED_TASK, "data"),
        State(ids.PERSONA, "data"),
        State(ids.INBOX_GRID, "virtualRowData"),
        State(ids.CASE_VERSION, "data"),
        State(ids.INBOX_QUERY, "data"),
        prevent_initial_call=True,
    )
    def settled_rows(settled, selected, persona, shown, case_version, query):
        request_ctx = _request(persona)
        if request_ctx is None or not settled:
            return no_update, no_update
        on_page = [row.get("task_id") for row in shown or () if isinstance(row, dict)]
        transaction, touched = settled_updates(
            request_ctx,
            settled,
            on_page,
            selected,
            entity_shown=not (query or {}).get("entity"),
            kind_shown=mixed_kinds(query),
        )
        return (
            transaction if transaction is not None else no_update,
            (case_version or 0) + 1 if touched else no_update,
        )

    # I10: the reveal modal opens and cancels (clientside, the shell's builder). The modal sits inside
    # DECIDE_COMPARE with its "Show values" button, so both are there or neither is.
    reveal.register_toggle(app, "decide")

    # I11: a reveal confirmed: the compare table in clear replaces the button and the modal
    @app.callback(
        Output(ids.DECIDE_COMPARE, "children"),
        Output(ids.reveal(ids.REVEAL_MODAL, "decide"), "opened", allow_duplicate=True),
        Output(ids.NOTIFY, "sendNotifications", allow_duplicate=True),
        Input(ids.reveal(ids.REVEAL_CONFIRM, "decide"), "n_clicks"),
        State(ids.reveal(ids.REVEAL_REASON, "decide"), "value"),
        State(ids.SELECTED_TASK, "data"),
        State(ids.CASE_STAMP, "data"),
        State(ids.PERSONA, "data"),
        prevent_initial_call=True,
    )
    def reveal_values(clicks, reason, task_id, stamp, persona):
        if not clicks:
            return (no_update,) * 3
        request_ctx = _request(persona)
        if request_ctx is None:
            return (no_update,) * 3
        table, note = reveal_view(request_ctx, task_id if isinstance(task_id, str) else None, reason, stamp)
        if table is None:
            closes = reveal.checked_reason(reason) is not None
            return no_update, (False if closes else no_update), [note] if note else no_update
        return table, no_update, [note]

    # Previous task and Next task move in the browser (assets/inbox.js listens for their clicks).
