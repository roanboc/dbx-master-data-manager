"""The batch page (story 3.3, plan 5 B.3): one batch of alike reviews from its forced sample to its result, in
one column: its header, its forced sample and splits, every row's change (50 a page, keyed by position), one
footer of actions, the tray's window, the chunks committing and the result. `/batch/<BAT-…>` opens it.

Its callbacks: B1 draws the page again when the batch's stamp changes (its status, its sample's outcomes, its
splits, its preparation, its chunks, its stop), on its own poll (every 2 s while the batch is staged or
committing, every 30 s while it is otherwise open, and not at all once it has ended), after an action and
when the tray settles something; B2 maps each action button to one service call and says how it went; B3
pages the rows. The page holds no form a redraw could reset: its only inputs are buttons. No single key acts
here (decision 18), and the page offers no reveal: a row's source key opens its source record, where a
reveal is asked and logged as ever.

The pure functions behind the callbacks (`body`, `refresh`, `act`, `move_rows`) run in tests without Dash.

Owner: WORKBENCH (plan 5, B.3).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from dash import ALL, Input, Output, State, dcc, html, no_update, set_props
from dash import ctx as dash_ctx
from dash.development.base_component import Component

from mdm import capacity
from mdm.models.batch import BATCH_ID_RE, PAGE_ACTIONS, Batch
from mdm.models.canonical import utcnow
from mdm.models.errors import Conflict
from mdm.models.workbench import BatchRowPage, BatchView
from mdm.services import display
from mdm.ui import context, ids
from mdm.ui.components import batch, groups
from mdm.ui.components.common import notice, notify, page_title
from mdm.ui.context import UiContext

#: the page's own copy of the shell's settlements (the inbox's bridge fills it)
SETTLED_HERE = ids.BATCH_SETTLED
#: the title of a page whose batch could not be read
UNKNOWN_TITLE = "Batch"
#: what the Undo of a batch says once it is back
UNDONE = "Undone. The batch is ready again, and nothing was linked."
UNDONE_COMPENSATION = "Undone. The batch is ready again, and nothing was undone."
#: B2's notification after each action, and its title; `stage` and `confirm` build theirs
NOTES: Mapping[str, tuple[str, str]] = {
    "send_back": ("Sent back to the steward who prepared it.", "Sent back"),
    "discard": ("Discarded. Its reviews stay in the inbox.", "Discarded"),
    "stop": ("Stop asked. It stops before the next chunk.", "Stopping"),
}
#: the actions after which the tray redraws (the confirming steward's included)
TRAY_ACTIONS = ("stage", "confirm", "undo")


@dataclass(frozen=True)
class Acted:
    """What one action did: its notification, and whether the tray changed."""

    note: dict
    tray: bool = False


# ---------------------------------------------------------------------------------------------- reading


def batch_of(data: Any) -> str | None:
    """The batch ID a BATCH_REF document names, when it has a batch ID's shape."""
    found = data.get("batch_id") if isinstance(data, Mapping) else None
    return found if isinstance(found, str) and BATCH_ID_RE.match(found) else None


def stack_of(cursor: Any) -> list[int]:
    """The positions a BATCH_ROWS_CURSOR document keeps, one per page before the one on screen."""
    stack = cursor.get("stack") if isinstance(cursor, Mapping) else None
    return [p for p in stack or () if isinstance(p, int) and not isinstance(p, bool)]


def cursor_doc(stack: list[int], page: BatchRowPage | None) -> dict:
    """BATCH_ROWS_CURSOR: the positions before the page on screen, and the one to read after for the next."""
    return {"stack": list(stack), "after": page.after if page is not None else None}


def rows_at(ctx: UiContext, view: BatchView, stack: list[int]) -> BatchRowPage | None:
    """The page of rows after the last position of `stack` (the first page for an empty one), once every
    change has been prepared; None before, or when the read failed."""
    if view.summary is None:
        return None
    found, _failure = context.guarded(
        ctx.hub.batches.rows,
        view.batch_id,
        actor=ctx.actor,
        after=stack[-1] if stack else None,
        limit=capacity.BATCH_PAGE,
    )
    return found


def body(ctx: UiContext, view: BatchView, page: BatchRowPage | None, depth: int = 0) -> list[Component]:
    """BATCH_VIEW's children: the page's regions for the actor, with the settings' window, second-steward
    threshold and chunk size."""
    return batch.render(
        view,
        page,
        depth,
        undo_seconds=ctx.settings.undo_seconds,
        checker_above=ctx.settings.batch_checker_above,
        chunk_rows=capacity.COMMIT_CHUNK_ROWS,
        now=utcnow(),
    )


def unknown_page(failure: dict | None) -> list[Component]:
    """A batch that could not be read: its sentence ("No batch has the ID BAT-…."), and the way back."""
    failure = failure or {}
    level = "error" if failure.get("color") == "red" else "warning"
    return [
        page_title(UNKNOWN_TITLE),
        notice(level, failure.get("message", "")),
        html.P(html.A("Back to Alike reviews", href="/groups"), className="mdm-batch-note"),
    ]


def _stores(batch_id: str, view: BatchView | None, page: BatchRowPage | None) -> list[Component]:
    disabled, interval = (
        batch.poll_of(view) if view is not None else (True, capacity.COUNTS_REFRESH_SECONDS * 1000)
    )
    return [
        dcc.Store(id=ids.BATCH_REF, storage_type="memory", data={"batch_id": batch_id}),
        dcc.Store(
            id=ids.BATCH_STAMP, storage_type="memory", data=batch.stamp_of(view) if view is not None else None
        ),
        dcc.Store(id=ids.BATCH_VERSION, storage_type="memory", data=0),
        dcc.Store(id=ids.BATCH_ROWS_CURSOR, storage_type="memory", data=cursor_doc([], page)),
        dcc.Store(id=SETTLED_HERE, storage_type="memory", data=None),
        dcc.Interval(id=ids.BATCH_POLL, interval=interval, disabled=disabled),
    ]


def _page(children: list[Component], stores: list[Component]) -> Component:
    return html.Div(
        [html.Div(children, id=ids.BATCH_VIEW, className="mdm-batch-view"), *stores],
        id=ids.BATCH,
        className="mdm-batch",
    )


def skeleton() -> Component:
    """The page's components with empty children and no service call, for the validation layout; the rows
    region is the view's own (`batch.rows_region`), which B1 draws inside BATCH_VIEW."""
    region = html.Div(
        [
            html.P(id=ids.BATCH_ROWS_LABEL),
            html.Div(id=ids.BATCH_ROWS),
            html.Button(id=ids.BATCH_ROWS_PREV),
            html.Button(id=ids.BATCH_ROWS_NEXT),
        ]
    )
    return _page([region], _stores("", None, None))


def layout(ctx: UiContext, batch_id: str) -> Component:
    """The page of batch `batch_id`: the consumer's line for a role that decides no tasks; the batch's
    regions otherwise, with its first page of rows once prepared; "No batch has the ID BAT-…." for an unknown
    one."""
    if not ctx.can("view_tasks"):
        return _page(groups.consumer(ctx.actor.role), [])
    view, failure = context.guarded(ctx.hub.batches.batch, batch_id, actor=ctx.actor)
    if view is None:
        return _page(unknown_page(failure), [])
    page = rows_at(ctx, view, [])
    return _page(body(ctx, view, page), _stores(view.batch_id, view, page))


# ---------------------------------------------------------------------------------------------- B1


def refresh(ctx: UiContext, ref: Any, stamp: Any, cursor: Any) -> tuple:
    """B1 without Dash: (BATCH_VIEW's children, BATCH_STAMP, BATCH_POLL's disabled and interval,
    BATCH_ROWS_CURSOR), each `no_update` when the batch's stamp is the one on screen, or it could not be read
    (the page keeps its last figure; the next poll tries again). A redraw keeps the page of rows on screen."""
    unchanged = (no_update,) * 5
    batch_id = batch_of(ref)
    if batch_id is None:
        return unchanged
    view, _failure = context.guarded(ctx.hub.batches.batch, batch_id, actor=ctx.actor)
    if view is None:
        return unchanged
    fresh = batch.stamp_of(view)
    if fresh == stamp:
        return unchanged
    stack = stack_of(cursor)
    page = rows_at(ctx, view, stack)
    if page is not None and not page.rows and stack:
        stack, page = [], rows_at(ctx, view, [])  # the page on screen is gone: back to the first
    disabled, interval = batch.poll_of(view)
    return body(ctx, view, page, len(stack)), fresh, disabled, interval, cursor_doc(stack, page)


# ---------------------------------------------------------------------------------------------- B2


def _label(found: Batch) -> str:
    decision = "batch_compensate" if found.kind == "compensate" else "batch_link"
    return display.batch_label(
        decision,
        {"batch_id": found.batch_id, "decisions": found.decisions, "compensates": found.compensates},
    )


def _prepared_words(view: BatchView) -> str:
    """ "Every change is shown: 566 links in 3 chunks. Check the rows, then link them."."""
    summary = view.summary
    links = summary.xrefs if summary is not None else 0
    chunks = summary.chunks if summary is not None else 0
    return (
        f"Every change is shown: {batch.plural(links, 'link')} in {batch.plural(chunks, 'chunk')}. "
        "Check the rows, then link them."
    )


def _staged_words(ctx: UiContext, found: Batch) -> Acted:
    if found.status != "staged":
        return Acted(
            notify("Waiting for a second steward to confirm.", "teal", title="Sent for confirmation")[0]
        )
    seconds = ctx.settings.undo_seconds
    text = f"In the tray: {_label(found)}. It commits in {batch.window_words(seconds)} unless you undo it."
    return Acted(notify(text, "teal", title="In the tray")[0], tray=True)


def _confirmed_words(ctx: UiContext, found: Batch) -> Acted:
    if found.staged_at is None:
        return Acted(notify("Confirmed. It waits in the tray.", "teal", title="Confirmed")[0], tray=True)
    until = found.staged_at + timedelta(seconds=ctx.settings.undo_seconds)
    return Acted(
        notify(f"Confirmed. It waits in the tray until {batch.clock(until)}.", "teal", title="Confirmed")[0],
        tray=True,
    )


def _undo(ctx: UiContext, batch_id: str) -> Acted:
    view, failure = context.guarded(ctx.hub.batches.batch, batch_id, actor=ctx.actor)
    if view is None:
        return Acted(failure or {})
    if view.entry_id is None:
        if view.status in ("committing", "committed", "stopped"):  # it has started to commit: too late
            refused = Conflict([batch_id], code="already_settled", status="committed", batch=batch_id)
            return Acted(context.notice(refused), tray=True)
        # never staged, sent back or undone meanwhile: the page on screen was out of date
        return Acted(context.notice(Conflict([batch_id], code="batch_changed")))
    entry, failure = context.guarded(ctx.hub.tray.undo, view.entry_id, actor=ctx.actor)
    if entry is None:
        return Acted(failure or {}, tray=True)
    words = UNDONE_COMPENSATION if view.kind == "compensate" else UNDONE
    return Acted(notify(words, "teal", title="Undone")[0], tray=True)


def act(ctx: UiContext, batch_id: Any, code: Any) -> Acted | None:
    """B2 without Dash: one action of `PAGE_ACTIONS` on the batch, through one service call, and the
    notification that says how it went (a refusal's sentence otherwise); None for anything else."""
    if code not in PAGE_ACTIONS or code == "decide_sample" or not isinstance(batch_id, str):
        return None
    if not BATCH_ID_RE.match(batch_id):
        return None
    service = ctx.hub.batches
    if code == "undo":
        return _undo(ctx, batch_id)
    calls: Mapping[str, Callable[..., Any]] = {
        "prepare": service.prepare,
        "stage": service.stage,
        "confirm": service.confirm,
        "send_back": service.send_back,
        "discard": service.discard,
        "stop": service.stop,
    }
    found, failure = context.guarded(calls[code], batch_id, actor=ctx.actor)
    if found is None:
        return Acted(failure or {}, tray=code in TRAY_ACTIONS)
    if code == "prepare":
        return Acted(notify(_prepared_words(found), "teal", title="Every change shown")[0])
    if code == "stage":
        return _staged_words(ctx, found)
    if code == "confirm":
        return _confirmed_words(ctx, found)
    text, title = NOTES[code]
    return Acted(notify(text, "teal", title=title)[0])


# ---------------------------------------------------------------------------------------------- B3


def move_rows(ctx: UiContext, ref: Any, cursor: Any, moved: str) -> tuple:
    """B3 without Dash: the next or the previous page of rows ("next" or "prev"): (BATCH_ROWS' children, its
    label, Previous disabled, Next disabled, BATCH_ROWS_CURSOR), or `no_update` everywhere when there is no
    page on that side or the batch could not be read."""
    unchanged = (no_update,) * 5
    batch_id = batch_of(ref)
    if batch_id is None:
        return unchanged
    stack = stack_of(cursor)
    after = cursor.get("after") if isinstance(cursor, Mapping) else None
    if moved == "next":
        if not isinstance(after, int) or isinstance(after, bool):
            return unchanged
        stack = [*stack, after]
    elif moved == "prev":
        if not stack:
            return unchanged
        stack = stack[:-1]
    else:
        return unchanged
    view, failure = context.guarded(ctx.hub.batches.batch, batch_id, actor=ctx.actor)
    if view is None:
        if failure:
            set_props(ids.NOTIFY, {"sendNotifications": [failure]})
        return unchanged
    page = rows_at(ctx, view, stack)
    if page is None:
        return unchanged
    total = view.summary.xrefs if view.summary is not None else 0
    return (
        batch.rows_table(view, page) if page.rows else None,
        batch.rows_label(len(stack), len(page.rows), total),
        len(stack) <= 0,
        page.after is None,
        cursor_doc(stack, page),
    )


# ---------------------------------------------------------------------------------------------- callbacks


def _request(persona: Any) -> UiContext | None:
    found, _failure = context.guarded(context.current, persona)
    return found


def _clicked() -> bool:
    return bool(dash_ctx.triggered) and bool(dash_ctx.triggered[0].get("value"))


def register(app) -> None:
    """Registers B1 (the page, on its stamp), B2 (an action) and B3 (the rows' paging) on `app`."""

    @app.callback(
        Output(ids.BATCH_VIEW, "children"),
        Output(ids.BATCH_STAMP, "data"),
        Output(ids.BATCH_POLL, "disabled"),
        Output(ids.BATCH_POLL, "interval"),
        Output(ids.BATCH_ROWS_CURSOR, "data", allow_duplicate=True),
        Input(ids.BATCH_POLL, "n_intervals"),
        Input(ids.BATCH_VERSION, "data"),
        Input(SETTLED_HERE, "data"),
        State(ids.BATCH_REF, "data"),
        State(ids.BATCH_STAMP, "data"),
        State(ids.BATCH_ROWS_CURSOR, "data"),
        State(ids.PERSONA, "data"),
        prevent_initial_call=True,
    )
    def refresh_batch(_polls, _version, _settled, ref, stamp, cursor, persona):
        request_ctx = _request(persona)
        if request_ctx is None:
            return (no_update,) * 5
        return refresh(request_ctx, ref, stamp, cursor)

    @app.callback(
        Output(ids.BATCH_VERSION, "data", allow_duplicate=True),
        Output(ids.TRAY_VERSION, "data", allow_duplicate=True),
        Input({"type": ids.BATCH_ACTION, "action": ALL}, "n_clicks"),
        State(ids.BATCH_REF, "data"),
        State(ids.PERSONA, "data"),
        State(ids.BATCH_VERSION, "data"),
        State(ids.TRAY_VERSION, "data"),
        prevent_initial_call=True,
    )
    def act_on_batch(_clicks, ref, persona, version, tray_version):
        trigger = dash_ctx.triggered_id
        if not isinstance(trigger, dict) or not _clicked():
            return no_update, no_update  # a re-rendered button with no click must not act
        request_ctx = _request(persona)
        if request_ctx is None:
            return no_update, no_update
        done = act(request_ctx, batch_of(ref), trigger.get("action"))
        if done is None:
            return no_update, no_update
        if done.note:
            set_props(ids.NOTIFY, {"sendNotifications": [done.note]})
        return (version or 0) + 1, ((tray_version or 0) + 1 if done.tray else no_update)

    @app.callback(
        Output(ids.BATCH_ROWS, "children"),
        Output(ids.BATCH_ROWS_LABEL, "children"),
        Output(ids.BATCH_ROWS_PREV, "disabled"),
        Output(ids.BATCH_ROWS_NEXT, "disabled"),
        Output(ids.BATCH_ROWS_CURSOR, "data"),
        Input(ids.BATCH_ROWS_PREV, "n_clicks"),
        Input(ids.BATCH_ROWS_NEXT, "n_clicks"),
        State(ids.BATCH_REF, "data"),
        State(ids.BATCH_ROWS_CURSOR, "data"),
        State(ids.PERSONA, "data"),
        prevent_initial_call=True,
    )
    def load_rows(_previous, _next, ref, cursor, persona):
        if not _clicked():
            return (no_update,) * 5  # a redrawn paging button with no click must not move
        request_ctx = _request(persona)
        if request_ctx is None:
            return (no_update,) * 5
        moved = "next" if dash_ctx.triggered_id == ids.BATCH_ROWS_NEXT else "prev"
        return move_rows(request_ctx, ref, cursor, moved)
