"""The undo tray in the header: the button with its countdown, the popover with this steward's entries,
and the callbacks that refresh it, count down and undo (S8, S9, S10).

The list is rewritten only when an entry appears, leaves or changes status, so a focused Undo button
keeps its focus; the poll and the one-second clock run only while one of this tab's decisions is staged
(decision 19). A settlement reaches the page as the `SETTLED` store (the inbox updates its grid from it)
and as one notification per committed or failed decision. Labels are built by the service from
decisions, IDs and source keys, never from a value.

Owner: SHELL (B.8.6).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

import dash_mantine_components as dmc
from dash import ALL, ClientsideFunction, Input, Output, State, ctx, html, no_update, set_props
from dash.development.base_component import Component

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


def outcome_text(view: TrayView) -> str:
    """What a settled entry says: "Committed as commit 42", "Undone", "Not committed. <why>"; a blind
    answer "Matched the first decision" or "Differed from the first decision: a review is open"."""
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


def _waiting(view: TrayView, now: datetime) -> Component:
    left = (view.deadline - now).total_seconds()
    return html.Li(
        [
            html.Span(view.label, className="mdm-tray-label"),
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
        ],
        className="mdm-tray-item mdm-tray-staged",
    )


def _done(view: TrayView) -> Component:
    when = _at(view.settled_at)
    return html.Li(
        [
            html.Span(view.label, className="mdm-tray-label"),
            html.Span(
                " · ".join(part for part in (outcome_text(view), when) if part), className="mdm-tray-outcome"
            ),
        ],
        className=f"mdm-tray-item mdm-tray-{view.status}",
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
    done = [view for view in entries if view.status != "staged"]
    parts: list[Component] = []
    if waiting:
        parts.extend(
            [
                html.H3(f"Waiting ({len(waiting)})", className="mdm-tray-heading"),
                html.Ul([_waiting(view, now) for view in waiting], className="mdm-tray-list"),
            ]
        )
    else:
        parts.append(html.P("Nothing is waiting.", className="mdm-tray-none"))
    if done:
        parts.extend(
            [
                html.H3(DONE_HEADING, className="mdm-tray-heading"),
                html.Ul([_done(view) for view in done], className="mdm-tray-list mdm-tray-done"),
            ]
        )
    return parts


def state_of(entries: Sequence[TrayView]) -> list[dict]:
    """The TRAY_STATE document: codes, IDs, labels, deadlines in milliseconds; never a value."""
    return [
        {
            "entry_id": view.entry_id,
            "task_id": view.task_id,
            "label": view.label,
            "deadline_ms": _deadline_ms(view.deadline),
            "status": view.status,
            "outcome": view.outcome,
            "version": view.commit_version,
        }
        for view in entries
    ]


def _shape(state: Sequence[dict] | None) -> list[tuple[str, str]]:
    return sorted((str(e.get("entry_id")), str(e.get("status"))) for e in state or ())


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


def settlement_notice(view: TrayView) -> dict | None:
    """The notification for a decision that committed or failed; none for one undone (the undo said so)."""
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
    before = {str(e.get("entry_id")): e.get("status") for e in previous or ()}
    settled_views = [v for v in entries if v.status != "staged" and before.get(v.entry_id) == "staged"]
    settled = [
        {"entry_id": v.entry_id, "task_id": v.task_id, "status": v.status, "outcome": v.outcome}
        for v in settled_views
    ]
    notices = [n for n in (settlement_notice(v) for v in settled_views) if n is not None]
    children = entry_items(entries, utcnow(), undo_seconds=ctx.settings.undo_seconds)
    return TrayRefresh(state=state, children=children, settled=settled, notices=notices)


def undo(ctx: context.UiContext, entry_id: str) -> dict:
    """Undoes the actor's staged entry `entry_id`; the notification that says how it went."""
    _, failure = context.guarded(ctx.hub.tray.undo, entry_id, actor=ctx.actor)
    if failure is not None:
        return failure
    return notify("Undone. The task is back in your queue.", "teal", title="Undone")[0]


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
            return (no_update,) * 5
        if change.notices:
            set_props(ids.NOTIFY, {"sendNotifications": change.notices})
        idle = change.staged == 0
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
