"""A source record, read-only: its masked title and key, "Linked to ORG-000123" or "Not linked", held,
its standardised values (a held update marked), versions, open tasks, rule failures and "Show values"
(B.8.9). Re-running, editing and ignoring are not on screen yet (story 3.7).

`/source/<system>/<key>` opens it (`?entity=` narrows it to one entity), and so does `/record/<system:key>`
when the record is not linked. Its callbacks: SR1 opens and closes the reveal modal (clientside), SR2
shows the values in clear once a reason is chosen, logged per attribute, and keeps them out of every
store. The pure functions behind them (`values_table`, `reveal_values`) run in tests without Dash.

Owner: RECORD (B.8.9).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from urllib.parse import quote

import dash_mantine_components as dmc
from dash import Input, Output, State, dcc, html, no_update
from dash.development.base_component import Component

from mdm.models.canonical import utcnow
from mdm.models.errors import NotFound
from mdm.models.records import SourceKey
from mdm.models.workbench import SourceView, ValueView
from mdm.ui import context, ids, messages
from mdm.ui.components import common, provenance, reveal
from mdm.ui.context import UiContext

#: the reveal modal this page owns
PAGE = "source"
#: what the page says about the actions that come later
LATER = "Re-running, editing and ignoring are not on screen yet."
#: what a held record says above its values
HELD = (
    "Its latest update is held for a steward. The values marked held update are not in its golden record yet."
)
#: what the reveal button reads once the values are shown
SHOWN = "Values shown"
_MUTED = {"color": "var(--mdm-muted)"}
_MONO = {"fontFamily": "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"}


def entity_label(entity: str) -> str:
    """ "Organisation" for `organisation`."""
    return entity.replace("_", " ").capitalize()


def record_href(master_id: str) -> str:
    """The golden record view of a master ID."""
    return f"/record/{quote(master_id, safe='')}"


def task_href(task_id: str) -> str:
    """The inbox with the task selected (every open task is in the team's view)."""
    return f"/?view=team&task={quote(task_id, safe='')}"


#: at most this many open tasks are named by what they ask; the rest are counted
TASKS_NAMED = 5


def task_words(ctx: UiContext, task_id: str) -> str:
    """What a task asks, as the inbox names it: "Review · choose among 2 records"; "Open task" when it
    cannot be read."""
    row, _failure = context.guarded(ctx.hub.inbox.row, task_id, actor=ctx.actor)
    if row is None:
        return "Open task"
    suggestion = row.suggestion[:1].lower() + row.suggestion[1:] if row.suggestion else ""
    return f"{row.kind_label} · {suggestion}" if suggestion else row.kind_label


def task_links(ctx: UiContext, task_ids: Sequence[str]) -> Component:
    """ "Open tasks: Review · choose among 2 records" as links to the inbox with the task selected, or as a
    count for a role without an inbox."""
    if not task_ids:
        return dmc.Text("No open task.", size="sm")
    count = len(task_ids)
    words = "1 open task" if count == 1 else f"{count} open tasks"
    if not ctx.can("view_tasks"):
        return dmc.Text(f"{words}.", size="sm")
    links: list[Component | str] = [f"{words}: "]
    for index, task_id in enumerate(task_ids[:TASKS_NAMED]):
        if index:
            links.append("; ")
        links.append(dmc.Anchor(task_words(ctx, task_id), href=task_href(task_id)))
    if count > TASKS_NAMED:
        links.append(f"; and {count - TASKS_NAMED} more")
    return dmc.Text(links, size="sm")


def status_badge(status: str) -> Component:
    """The record's status in words, coloured as well."""
    color = "teal" if status == "active" else "gray"
    return dmc.Badge(status, variant="light", color=color, size="md", tt="none")


def unknown_page(ctx: UiContext, sentence: str) -> Component:
    """A reference no record answers: the sentence, and the way back to the inbox for a role that has one."""
    parts: list[Component] = [common.page_title("No record found"), common.notice("warning", sentence)]
    if ctx.can("view_tasks"):
        parts.append(dmc.Anchor("Back to the inbox", href="/"))
    return html.Div(parts, className="mdm-record-missing")


def revealable(ctx: UiContext, values: Sequence[ValueView]) -> bool:
    """Whether "Show values" is offered: the role may reveal, and something is masked."""
    return ctx.can("reveal") and any(v.masked for v in values)


def values_table(view: SourceView) -> Component:
    """The standardised values (SOURCE_VALUES's children): attribute, value (masked unless revealed), and
    "held update" where the published value differs."""
    held = set(view.approved_differs)
    rows: list[Component] = []
    for value in view.values:
        label: list[Component | str] = [value.label]
        if value.critical:
            label.append(provenance.critical_mark())
        cell: list[Component] = [provenance.value_cell(value.value, masked=value.masked)]
        if value.attribute in held:
            cell.append(dmc.Badge("held update", variant="light", color="yellow", size="sm", ml=8, tt="none"))
        rows.append(html.Tr([html.Th(label, scope="row"), html.Td(cell)]))
    return html.Table(
        [
            html.Caption(f"Values of {view.source}", className="mdm-sr-only"),
            html.Thead(
                html.Tr(
                    [html.Th("Attribute", scope="col", style={"width": "26%"}), html.Th("Value", scope="col")]
                )
            ),
            html.Tbody(rows),
        ],
        className="mdm-table mdm-source-values",
    )


def versions_table(view: SourceView, now: datetime | None = None) -> Component:
    """The versions seen, newest first (SOURCE_VERSIONS's children)."""
    moment = now or utcnow()
    if not view.versions:
        return common.empty_state("No version has been seen yet.")
    rows = [
        html.Tr(
            [
                html.Td(f"v{version}" if version is not None else "not given"),
                html.Td(html.Time(common.relative_time(at, moment), dateTime=at.isoformat())),
                html.Td(str(sequence)),
            ]
        )
        for version, at, sequence in reversed(view.versions)
    ]
    return html.Table(
        [
            html.Caption(f"Versions of {view.source}, newest first", className="mdm-sr-only"),
            html.Thead(
                html.Tr(
                    [
                        html.Th("Source version", scope="col"),
                        html.Th("Event time", scope="col"),
                        html.Th("Landing sequence", scope="col"),
                    ]
                )
            ),
            html.Tbody(rows),
        ],
        className="mdm-table mdm-source-versions",
    )


def failures_list(view: SourceView) -> Component:
    """The rule failures ("email: bad_pattern"), or that none fails."""
    if not view.rule_failures:
        return common.empty_state("No validation rule fails.")
    return html.Ul([html.Li(failure) for failure in view.rule_failures], className="mdm-rule-failures")


def header(ctx: UiContext, view: SourceView) -> Component:
    """The title (masked), the key, the entity, the status, held, the golden record it is linked to, the
    open tasks, "Show values" and the line on what comes later."""
    badges: list[Component] = [
        html.Span(view.source, style=_MONO),
        dmc.Text(entity_label(view.entity), size="sm", span=True),
        status_badge(view.status),
    ]
    if view.held:
        badges.append(dmc.Badge("held", variant="light", color="yellow", size="md", tt="none"))
    linked = (
        dmc.Text(
            ["Linked to ", dmc.Anchor(view.linked_to, href=record_href(view.linked_to), style=_MONO), "."],
            size="sm",
        )
        if view.linked_to
        else dmc.Text("Not linked to a golden record.", size="sm")
    )
    actions: list[Component] = []
    if revealable(ctx, view.values):
        actions.append(reveal.open_button(PAGE))
    actions.append(dmc.Text(LATER, size="sm", style=_MUTED))
    return html.Div(
        [
            common.page_title(view.title),
            dmc.Group(badges, gap="sm", mb=6),
            linked,
            task_links(ctx, view.open_tasks),
            dmc.Group(actions, gap="md", mt=8),
        ],
        className="mdm-source-header",
    )


def page(ctx: UiContext, view: SourceView, *, notice: Component | None = None) -> Component:
    """The source record page for `view`."""
    parts: list[Component] = []
    if notice is not None:
        parts.append(notice)
    parts.append(header(ctx, view))
    if view.held:
        parts.append(common.notice("warning", HELD))
    parts += [
        dcc.Store(
            id=ids.SOURCE_REF, storage_type="memory", data={"entity": view.entity, "source": view.source}
        ),
        html.H2("Values", className="mdm-section-title", style={"fontSize": "1.1rem", "marginTop": "16px"}),
        html.Div(values_table(view), id=ids.SOURCE_VALUES),
        html.H2("Versions", className="mdm-section-title", style={"fontSize": "1.1rem", "marginTop": "16px"}),
        html.Div(versions_table(view), id=ids.SOURCE_VERSIONS),
        html.H2(
            "Rule failures", className="mdm-section-title", style={"fontSize": "1.1rem", "marginTop": "16px"}
        ),
        failures_list(view),
    ]
    if revealable(ctx, view.values):
        parts.append(reveal.modal(PAGE))
    return html.Div(parts, id=ids.SOURCE, className="mdm-source")


def skeleton() -> Component:
    """The page's components with empty children and no service call, for the validation layout."""
    return html.Div(
        [
            dcc.Store(id=ids.SOURCE_REF, storage_type="memory"),
            reveal.open_button(PAGE),
            html.Div(id=ids.SOURCE_VALUES),
            html.Div(id=ids.SOURCE_VERSIONS),
            reveal.modal(PAGE),
        ],
        id=ids.SOURCE,
    )


def entity_of(ctx: UiContext, query: Mapping[str, str]) -> str | None:
    """The `?entity=` of the address when it names a published entity."""
    entity = query.get("entity")
    return entity if isinstance(entity, str) and entity in ctx.badges.entities else None


def layout(
    ctx: UiContext, system: str, key: str, query: Mapping[str, str], *, notice: Component | None = None
) -> Component:
    """The source record `system:key` (`?entity=` narrows it to one entity); `notice` above it when another
    address led here. A record no source holds gives a notice with the way back to the inbox."""
    try:
        view = ctx.hub.lookup.source(SourceKey(system, key), actor=ctx.actor, entity=entity_of(ctx, query))
    except NotFound as error:
        return unknown_page(ctx, messages.sentence(error))
    return page(ctx, view, notice=notice)


# ---------------------------------------------------------------------------------------------- callbacks


def source_ref(data: Any) -> tuple[str, SourceKey] | None:
    """(entity, source key) from SOURCE_REF's data, or None when it holds anything else."""
    if not isinstance(data, Mapping):
        return None
    entity, text = data.get("entity"), data.get("source")
    if not isinstance(entity, str) or not isinstance(text, str):
        return None
    try:
        return entity, SourceKey.from_text(text)
    except ValueError:
        return None


def reveal_values(ctx: UiContext, data: Any, reason: Any) -> tuple[Component | None, dict | None, str | None]:
    """SR2 without Dash: (the values table in clear, a notification, the reason field's error). No reason
    chosen: nothing is revealed and the field says so; a refusal: its sentence, nothing revealed."""
    code = reveal.checked_reason(reason)
    if code is None:
        return None, None, reveal.REASON_REQUIRED
    found = source_ref(data)
    if found is None:
        return None, common.notify(messages.sentence_for("unknown_source_record"), "yellow")[0], None
    entity, source = found
    view, failure = context.guarded(
        ctx.hub.lookup.source, source, actor=ctx.actor, entity=entity, reveal=True, reason=code
    )
    if view is None:
        return None, failure, None
    return values_table(view), shown_notice(view.values), None


def shown_notice(values: Sequence[ValueView]) -> dict:
    """ "Personal values shown. Each is logged with your reason." (a count, never a value)."""
    count = sum(1 for v in values if v.personal and v.value is not None)
    words = "1 personal value" if count == 1 else f"{count} personal values"
    return common.notify(f"{words} shown. Each is logged with your reason.", "teal", title="Values shown")[0]


def register(app) -> None:
    """Registers SR1 (reveal open and cancel) and SR2 (reveal confirmed) on `app`."""
    reveal.register_toggle(app, PAGE)

    @app.callback(
        Output(ids.SOURCE_VALUES, "children"),
        Output(ids.reveal(ids.REVEAL_MODAL, PAGE), "opened", allow_duplicate=True),
        Output(ids.reveal(ids.REVEAL_REASON, PAGE), "error"),
        Output(ids.reveal(ids.REVEAL_OPEN, PAGE), "children"),
        Output(ids.reveal(ids.REVEAL_OPEN, PAGE), "disabled"),
        Output(ids.NOTIFY, "sendNotifications", allow_duplicate=True),
        Input(ids.reveal(ids.REVEAL_CONFIRM, PAGE), "n_clicks"),
        State(ids.reveal(ids.REVEAL_REASON, PAGE), "value"),
        State(ids.SOURCE_REF, "data"),
        State(ids.PERSONA, "data"),
        prevent_initial_call=True,
    )
    def confirm_reveal(n_clicks, reason, data, persona):
        if not n_clicks:
            return (no_update,) * 6
        request_ctx, failure = context.guarded(context.current, persona)
        if request_ctx is None:
            return no_update, no_update, None, no_update, no_update, [failure]
        table, shown, error = reveal_values(request_ctx, data, reason)
        if table is None:
            return no_update, no_update, error, no_update, no_update, [shown] if shown else no_update
        return table, False, None, SHOWN, True, [shown]
