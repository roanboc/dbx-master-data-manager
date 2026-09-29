"""The undo tray in the header: the button with its countdown, the popover with this steward's entries,
and the callbacks that refresh it, count down and undo (S8, S9, S10).

The list is rewritten only when an entry appears, leaves or changes status, so a focused Undo button
keeps its focus; the poll and the one-second clock run only while one of this tab's decisions is staged,
or one of its batches commits (decision 19). A settlement reaches the page as the `SETTLED` store (the
inbox updates its grid from it) and as one notification per committed or failed decision. Labels are
built by the service from decisions, IDs and source keys, never from a value.

A batch of alike reviews (story 3.3, decision 23) is one entry. It waits with its countdown and its Undo
like any decision, then commits chunk by chunk: its entry stays in the tray while it commits ("Committing:
chunk 2 of 3"), and each chunk reaches the page as a `SETTLED` document naming the batch, so the inbox
reloads its page. The second steward who confirmed a batch sees it in their own tray too ("…, which you
confirmed"), with its countdown and its Undo; its maker hears of the confirmation once ("Confirmed by a
coordinating steward: …"). A tray that is resting learns of it from the counts: the header's refresh (S6)
reads how many of the steward's entries still move, and S6b wakes the poll when that differs from what the
tab shows; a poll that then finds nothing new and nothing moving rests again.

Owner: SHELL (B.8.6).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

import dash_mantine_components as dmc
from dash import ALL, ClientsideFunction, Input, Output, State, ctx, html, no_update, set_props
from dash.development.base_component import Component

from mdm.models.authority import ROLE_LABELS
from mdm.models.batch import BATCH_DECISIONS, COMMITTING
from mdm.models.canonical import utcnow
from mdm.models.workbench import TrayView
from mdm.ui import context, ids, messages
from mdm.ui.components.common import countdown, empty_state, notify
from mdm.ui.components.icons import icon

#: the popover's title
TITLE = "Staged decisions"
#: what the tray says to a role that decides no tasks
NOT_A_WORKER = "Your role does not decide tasks, so nothing of yours waits here."
#: what the tray says about itself, above its entries
INTRODUCTION = "Each decision waits here before it commits. Undo takes it back until its time runs out."
#: the heading of the entries that settled lately (`TrayService.entries` keeps ten minutes of them)
DONE_HEADING = "Done in the last 10 minutes"
#: the link of a batch's entry to its page
OPEN_BATCH = "Open the batch"
#: why a batch stopped early, after "Stopped after chunk 1 of 3", per outcome
STOPPED_WHY = {
    "bulk_withdrawn": "bulk decisions for this pattern were withdrawn",
    "chunk_failed": "a chunk could not commit",
}


def button() -> Component:
    """The header's tray button (TRAY_BUTTON): "Tray", quiet, while nothing waits; "Tray 1 · 0:48", dashed
    in amber, while any is staged (the `mdm-staged` class the countdown sets)."""
    return dmc.Button(
        "Tray",
        id=ids.TRAY_BUTTON,
        variant="subtle",
        color="gray",
        size="sm",
        leftSection=icon("tray"),
        className="mdm-tray-button",
        **{"aria-expanded": "false"},
    )


def popover() -> Component:
    """The popover (TRAY_POPOVER) around the button, holding TRAY_LIST: a disclosure. Opening it moves the
    focus into it (so a keyboard reaches Undo), Escape closes it and the focus returns to the button.

    Mantine's roles are off (`withRoles=False`): it would put `aria-expanded` on the wrapper Dash draws
    round the button, a plain `div`, where it is not allowed; the button carries it instead (S9b)."""
    return dmc.Popover(
        id=ids.TRAY_POPOVER,
        opened=False,
        position="bottom-end",
        width=440,
        shadow="md",
        withArrow=True,
        withRoles=False,
        trapFocus=True,
        returnFocus=True,
        children=[
            dmc.PopoverTarget(button()),
            dmc.PopoverDropdown(
                [
                    html.H2(TITLE, className="mdm-popover-title"),
                    html.P(INTRODUCTION, className="mdm-popover-text"),
                    html.Div(entry_items((), utcnow()), id=ids.TRAY_LIST),
                ],
                className="mdm-tray-dropdown",
            ),
        ],
    )


def _deadline_ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


#: what a committed blind answer says: it matched the first decision, or differed and opened a review
BLIND_OUTCOMES = {
    "agreed": "Matched the first decision",
    "disagreed": "Differed from the first decision: a review is open",
}


def committing(view: TrayView) -> bool:
    """Whether the entry is a batch whose chunks still commit (the entry itself is `committed`)."""
    return view.batch_id is not None and view.status == "committed" and view.outcome == COMMITTING


def _chunks(view: TrayView) -> str:
    """ "chunk 2 of 3"; "a chunk" when the batch's progress is not known."""
    if view.progress is None:
        return "a chunk"
    done, planned = view.progress
    return f"chunk {done} of {planned}"


def batch_outcome_text(view: TrayView) -> str:
    """What a batch's entry says once it left the window: "Committing: chunk 2 of 3", "Committed in 3
    chunks", "Stopped after chunk 2 of 3" (with why, when bulk decisions were withdrawn or a chunk could
    not commit), "Not committed. <why>", "Undone"."""
    if view.status == "undone":
        return "Undone"
    if view.status == "failed":
        return f"Not committed. {messages.outcome_sentence(view.outcome)}"
    if view.status != "committed":
        return ""
    if view.outcome == COMMITTING:
        return f"Committing: {_chunks(view)}"
    if view.outcome == "committed":
        if view.progress is None:
            return "Committed"
        done = view.progress[0]
        return f"Committed in {done} chunk" + ("" if done == 1 else "s")
    stopped = f"Stopped after {_chunks(view)}"
    why = STOPPED_WHY.get(view.outcome or "")
    return f"{stopped}: {why}" if why else stopped


def outcome_text(view: TrayView) -> str:
    """What a settled entry says: "Committed as commit 42", "Undone", "Not committed. <why>"; a blind
    answer "Matched the first decision" or "Differed from the first decision: a review is open"; a batch
    as `batch_outcome_text` says."""
    if view.batch_id is not None:
        return batch_outcome_text(view)
    if view.status == "committed":
        if view.outcome in BLIND_OUTCOMES:
            return BLIND_OUTCOMES[view.outcome]
        if view.commit_version is None:
            return "Committed; no published record changed"
        return f"Committed as commit {view.commit_version}"
    if view.status == "undone":
        return "Undone"
    if view.status == "failed":
        return f"Not committed. {messages.outcome_sentence(view.outcome)}"
    return ""


def _at(moment: datetime | None) -> str:
    """ "12:04 UTC"."""
    return f"{moment.strftime('%H:%M')} UTC" if moment is not None else ""


def label_of(view: TrayView) -> str:
    """The entry's line: its label; for a batch the actor confirmed as its second steward, "…, which you
    confirmed"."""
    if view.batch_id is not None and not view.mine:
        return f"{view.label}, which you confirmed"
    return view.label


def batch_href(batch_id: str) -> str:
    """The page of a batch: "/batch/BAT-…"."""
    return f"/batch/{batch_id}"


def _batch_link(view: TrayView) -> list[Component]:
    if view.batch_id is None:
        return []
    return [dmc.Anchor(OPEN_BATCH, href=batch_href(view.batch_id), size="xs", className="mdm-tray-batch")]


def _waiting(view: TrayView, now: datetime) -> Component:
    left = (view.deadline - now).total_seconds()
    return html.Li(
        [
            html.Span(label_of(view), className="mdm-tray-label"),
            html.Span(
                [
                    html.Span("Commits in ", className="mdm-sr-only"),
                    html.Span(countdown(left), id=ids.tray_countdown(view.entry_id)),
                ],
                className="mdm-countdown",
            ),
            dmc.Button(
                "Undo",
                id=ids.tray_undo(view.entry_id),
                size="xs",
                variant="light",
                leftSection=icon("undo"),
                className="mdm-tray-undo",
                **{"aria-label": f"Undo: {view.label}"},
            ),
            *_batch_link(view),
        ],
        className="mdm-tray-item mdm-tray-staged",
    )


def _done(view: TrayView) -> Component:
    when = "" if committing(view) else _at(view.settled_at)
    return html.Li(
        [
            html.Span(label_of(view), className="mdm-tray-label"),
            html.Span(
                " · ".join(part for part in (outcome_text(view), when) if part), className="mdm-tray-outcome"
            ),
            *_batch_link(view),
        ],
        className=f"mdm-tray-item mdm-tray-{'committing' if committing(view) else view.status}",
    )


def entry_items(entries: Sequence[TrayView], now: datetime, *, undo_seconds: int = 60) -> list[Component]:
    """The steward's entries in two lists: "Waiting", each with its countdown (TRAY_COUNTDOWN) and Undo
    (TRAY_UNDO); and "Done in the last 10 minutes", each with its outcome and when it settled. Each keeps
    a stable pattern ID per entry."""
    if not entries:
        return [
            empty_state(
                f"Nothing is waiting. A decision you make in the inbox waits here for {undo_seconds} s "
                "before it commits."
            )
        ]
    waiting = [view for view in entries if view.status == "staged"]
    moving = [view for view in entries if committing(view)]
    done = [view for view in entries if view.status != "staged" and not committing(view)]
    parts: list[Component] = []
    if waiting:
        parts.extend(
            [
                html.H3(f"Waiting ({len(waiting)})", className="mdm-tray-heading"),
                html.Ul([_waiting(view, now) for view in waiting], className="mdm-tray-list"),
            ]
        )
    elif not moving:
        parts.append(html.P("Nothing is waiting.", className="mdm-tray-none"))
    if moving:
        parts.extend(
            [
                html.H3(f"Committing ({len(moving)})", className="mdm-tray-heading"),
                html.Ul([_done(view) for view in moving], className="mdm-tray-list mdm-tray-moving"),
            ]
        )
    if done:
        parts.extend(
            [
                html.H3(DONE_HEADING, className="mdm-tray-heading"),
                html.Ul([_done(view) for view in done], className="mdm-tray-list mdm-tray-done"),
            ]
        )
    return parts


def state_of(entries: Sequence[TrayView]) -> list[dict]:
    """The TRAY_STATE document: codes, IDs, labels, deadlines in milliseconds; never a value. A batch's
    entry also carries its batch, its progress (`[committed, planned]` chunks) and whether it is the
    actor's own (`mine`: false for one they confirmed as its second steward)."""
    return [
        {
            "entry_id": view.entry_id,
            "task_id": view.task_id,
            "label": view.label,
            "deadline_ms": _deadline_ms(view.deadline),
            "status": view.status,
            "outcome": view.outcome,
            "version": view.commit_version,
            "batch_id": view.batch_id,
            "progress": list(view.progress) if view.progress is not None else None,
            "mine": view.mine,
        }
        for view in entries
    ]


def _progress_of(entry: dict) -> int | None:
    progress = entry.get("progress")
    if isinstance(progress, (list, tuple)) and progress and isinstance(progress[0], int):
        return progress[0]
    return None


def _shape(state: Sequence[dict] | None) -> list[tuple[str, str, str, str]]:
    """What a redraw follows: each entry's ID, status and outcome, and a batch's committed chunks."""
    return sorted(
        (
            str(e.get("entry_id")),
            str(e.get("status")),
            str(e.get("outcome")),
            str(_progress_of(e)),
        )
        for e in state or ()
    )


def live_in(state: Sequence[dict] | None) -> int:
    """How many entries of a TRAY_STATE document still move: staged, or a batch still committing."""
    return sum(
        1
        for e in state or ()
        if e.get("status") == "staged" or (e.get("batch_id") and e.get("outcome") == COMMITTING)
    )


@dataclass(frozen=True)
class TrayRefresh:
    """What one refresh changed: the new state and list, the entries that settled since the last one, and
    their notifications."""

    state: list[dict]
    children: list[Component]
    settled: list[dict] = field(default_factory=list)
    notices: list[dict] = field(default_factory=list)

    @property
    def staged(self) -> int:
        return sum(1 for entry in self.state if entry["status"] == "staged")

    @property
    def live(self) -> int:
        """The entries still moving: staged, or a batch still committing; the polls run while any does."""
        return live_in(self.state)


def batch_notice(view: TrayView, before: dict | None = None) -> dict | None:
    """The notification for a batch's entry that moved since `before` (its last TRAY_STATE document): its
    first chunk ("Committing: …, in 3 chunks."), its end ("Committed in 3 chunks: …."), a stop, or nothing
    committed; none for a later chunk, nor for an undo (the undo said so)."""
    label = view.label
    planned = view.progress[1] if view.progress is not None else None
    was = (before or {}).get("outcome")
    if view.status == "committed" and view.outcome == COMMITTING:
        if was == COMMITTING:
            return None  # a later chunk: the page and the tray show it, quietly
        chunks = f", in {planned} chunks" if planned and planned > 1 else ""
        return notify(f"Committing: {label}{chunks}.", "teal", title="Committing")[0]
    if view.status == "committed" and view.outcome == "committed":
        return notify(f"{batch_outcome_text(view)}: {label}.", "teal", title="Committed")[0]
    if view.status == "committed":
        why = STOPPED_WHY.get(view.outcome or "")
        rest = (
            f"{why[:1].upper()}{why[1:]}; the rest are back in the queue."
            if why
            else "The rest are back in the queue."
        )
        return notify(f"Stopped after {_chunks(view)}: {label}. {rest}", "yellow", title="Stopped")[0]
    if view.status == "failed":
        return notify(f"{label}. {messages.outcome_sentence(view.outcome)}", "yellow", title="Not committed")[
            0
        ]
    return None


def confirmed_notice(view: TrayView) -> dict:
    """What the maker of a batch hears once a second steward confirmed it: "Confirmed by a coordinating
    steward: Link 566 alike reviews (BAT-…). It commits at 12:04:31 UTC unless one of you undoes it."."""
    role = ROLE_LABELS.get(view.second_steward or "", "")
    who = f"a {role.lower()}" if role else "a second steward"
    at = view.deadline.strftime("%H:%M:%S")
    return notify(
        f"Confirmed by {who}: {view.label}. It commits at {at} UTC unless one of you undoes it.",
        "teal",
        title="Confirmed",
    )[0]


def settlement_notice(view: TrayView) -> dict | None:
    """The notification for a decision that committed or failed; none for one undone (the undo said so). A
    batch's entry reads as `batch_notice` says."""
    if view.batch_id is not None:
        return batch_notice(view)
    if view.status == "committed" and view.outcome in BLIND_OUTCOMES:
        return notify(f"{view.label}. {BLIND_OUTCOMES[view.outcome]}.", "teal", title="Answered")[0]
    if view.status == "committed":
        return notify(f"{outcome_text(view)}: {view.label}.", "teal", title="Committed")[0]
    if view.status == "failed":
        return notify(
            f"{view.label}. {messages.outcome_sentence(view.outcome)}", "yellow", title="Not committed"
        )[0]
    return None


def refresh(ctx: context.UiContext, previous: Sequence[dict] | None) -> TrayRefresh | None:
    """The tray of `ctx`'s actor, or None when nothing appeared, left or changed status since `previous`
    (None: the first refresh of the tab). A role that works no tasks has an empty tray, and says why."""
    if not ctx.can("work_tasks"):
        if previous is not None and not previous:
            return None
        return TrayRefresh(state=[], children=[empty_state(NOT_A_WORKER)])
    entries, failure = context.guarded(ctx.hub.tray.entries, actor=ctx.actor)
    if failure is not None:
        return None  # keep what the tab shows; the next poll tries again
    entries = tuple(entries or ())
    state = state_of(entries)
    if previous is not None and _shape(previous) == _shape(state):
        return None
    before = {str(e.get("entry_id")): e for e in previous or () if isinstance(e, dict)}
    settled: list[dict] = []
    notices: list[dict] = []
    for view in entries:
        was = before.get(view.entry_id)
        if view.batch_id is not None:
            moved = was is not None and (
                was.get("status"),
                was.get("outcome"),
                _progress_of(was),
            ) != (view.status, view.outcome, view.progress[0] if view.progress is not None else None)
            if moved and view.status != "staged":
                settled.append(
                    {
                        "entry_id": view.entry_id,
                        "task_id": view.task_id,
                        "status": view.status,
                        "outcome": view.outcome,
                        "batch_id": view.batch_id,
                    }
                )
                note = batch_notice(view, was)
                if note is not None:
                    notices.append(note)
            elif (
                was is None
                and previous is not None
                and view.status == "staged"
                and view.mine
                and view.second_steward
            ):
                notices.append(confirmed_notice(view))  # its maker learns of the confirmation, once
            continue
        if view.status != "staged" and was is not None and was.get("status") == "staged":
            settled.append(
                {
                    "entry_id": view.entry_id,
                    "task_id": view.task_id,
                    "status": view.status,
                    "outcome": view.outcome,
                }
            )
            note = settlement_notice(view)
            if note is not None:
                notices.append(note)
    children = entry_items(entries, utcnow(), undo_seconds=ctx.settings.undo_seconds)
    return TrayRefresh(state=state, children=children, settled=settled, notices=notices)


#: what the tray's Undo says once a batch is back (story 3.3)
UNDONE_BATCH = "Undone. The batch is ready again, and nothing was {what}."


def undo(ctx: context.UiContext, entry_id: str) -> dict:
    """Undoes the actor's staged entry `entry_id` (a batch's, as its maker or its second steward); the
    notification that says how it went."""
    entry, failure = context.guarded(ctx.hub.tray.undo, entry_id, actor=ctx.actor)
    if failure is not None:
        return failure
    decision = getattr(entry, "decision", None)
    if decision in BATCH_DECISIONS:
        what = "undone" if decision == "batch_compensate" else "linked"
        return notify(UNDONE_BATCH.format(what=what), "teal", title="Undone")[0]
    return notify("Undone. The task is back in your queue.", "teal", title="Undone")[0]


def quiet_polls(previous: Sequence[dict] | None) -> bool:
    """Whether a poll that found nothing new may stop: nothing the tab's tray shows still moves (a poll the
    counts woke, S6b, rests again once it finds nothing)."""
    return previous is not None and live_in(previous) == 0


def register(app) -> None:
    """Registers S8 (refresh), S9 (countdown, clientside), S9b (the button's `aria-expanded`, clientside)
    and S10 (undo from the tray) on `app`."""

    @app.callback(
        Output(ids.TRAY_STATE, "data"),
        Output(ids.TRAY_LIST, "children"),
        Output(ids.SETTLED, "data"),
        Output(ids.TRAY_POLL, "disabled"),
        Output(ids.CLOCK_TICK, "disabled"),
        Input(ids.TRAY_POLL, "n_intervals"),
        Input(ids.TRAY_VERSION, "data"),
        Input(ids.PERSONA, "data"),
        State(ids.TRAY_STATE, "data"),
    )
    def refresh_tray(_polls, _version, persona, previous):
        request_ctx, failure = context.guarded(context.current, persona)
        if request_ctx is None:
            return (no_update,) * 5
        if ctx.triggered_id == ids.PERSONA:
            previous = None  # another actor's tray: nothing of the last one settled
        change = refresh(request_ctx, previous)
        if change is None:
            if ctx.triggered_id == ids.TRAY_POLL and quiet_polls(previous):
                return no_update, no_update, no_update, True, True
            return (no_update,) * 5
        if change.notices:
            set_props(ids.NOTIFY, {"sendNotifications": change.notices})
        idle = change.live == 0  # a staged entry counts down; a batch that commits moves chunk by chunk
        return change.state, change.children, change.settled or no_update, idle, idle

    app.clientside_callback(
        ClientsideFunction(namespace="mdm_shell", function_name="trayCountdown"),
        Output(ids.TRAY_BUTTON, "children"),
        Output(ids.TRAY_BUTTON, "className"),
        Output({"type": ids.TRAY_COUNTDOWN, "entry": ALL}, "children"),
        Input(ids.CLOCK_TICK, "n_intervals"),
        Input(ids.TRAY_STATE, "data"),
    )

    app.clientside_callback(
        ClientsideFunction(namespace="mdm_shell", function_name="trayExpanded"),
        Output(ids.TRAY_BUTTON, "aria-expanded"),
        Input(ids.TRAY_POPOVER, "opened"),
    )

    @app.callback(
        Output(ids.TRAY_VERSION, "data", allow_duplicate=True),
        Input({"type": ids.TRAY_UNDO, "entry": ALL}, "n_clicks"),
        State(ids.PERSONA, "data"),
        State(ids.TRAY_VERSION, "data"),
        prevent_initial_call=True,
    )
    def undo_from_tray(_clicks, persona, version):
        trigger = ctx.triggered_id
        if not isinstance(trigger, dict) or not ctx.triggered or not ctx.triggered[0].get("value"):
            return no_update  # a re-rendered button with no click must not act
        request_ctx, failure = context.guarded(context.current, persona)
        note = failure if request_ctx is None else undo(request_ctx, str(trigger.get("entry")))
        set_props(ids.NOTIFY, {"sendNotifications": [note]})
        return (version or 0) + 1
