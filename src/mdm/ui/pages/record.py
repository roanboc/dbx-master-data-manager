"""A golden record, read-only: the header (masked title, master ID, status, the IDs merged into it, open
tasks, "Show values"), and the tabs Golden (values with provenance chips, each opening its Why under the value), Sources
and cross-references, Timeline and Relationships (B.8.9). Editing, pinning, detaching, merging and
retiring are not on screen yet (story 3.6).

`/record/<ref>` opens it: a master ID; a merged ID, which shows its survivor with a notice; or a source
key, which shows its golden record, or the source record itself when it is not linked. An unknown
reference says so. `?tab=` chooses the first tab shown.

The Golden tab is filled when the page is built (it tells whether anything is masked); each other tab is
filled when it is first shown (R1), so a revealed value stays while the steward moves between tabs.
RECORD_REF keeps the record's entity and ID, the tabs filled and the personal attributes' names: codes
and IDs only; WHY_REF keeps the same for the Why rows, as a pattern the Why's callback finds only while
the record is on screen. The callbacks call the pure functions below (`tab_content`, `fill_tab`, `why`,
`reveal_golden`, `older_events`), so tests run them without Dash.

Owner: RECORD (B.8.9).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

import dash_mantine_components as dmc
from dash import ALL, Input, Output, Patch, State, dcc, html, no_update
from dash import ctx as dash_ctx
from dash.development.base_component import Component

from mdm import capacity
from mdm.models.canonical import utcnow
from mdm.models.workbench import MemberView, RecordHeader, RelationshipView, ValueView
from mdm.ui import context, ids, messages
from mdm.ui.components import common, provenance, reveal, timeline
from mdm.ui.context import UiContext
from mdm.ui.pages import source as source_page
from mdm.ui.pages.source import entity_label, record_href, status_badge, task_href, task_links, unknown_page

#: the tabs, in order; `?tab=` chooses the first one shown
TABS = ("golden", "sources", "timeline", "relationships")
#: the reveal modal this page owns
PAGE = "record"
#: what the page says about the actions that come later (story 3.6 and 3.7)
LATER = "Editing, pinning, detaching, merging and retiring are not on screen yet."
#: what the reveal button reads once the values are shown
SHOWN = source_page.SHOWN
#: what a tab holds before it is first shown
LOADING = "Loading…"


def first_tab(query: Mapping[str, str]) -> str:
    """The tab `?tab=` names, else Golden."""
    tab = query.get("tab")
    return tab if tab in TABS else "golden"


def tab_labels(header: RecordHeader) -> dict[str, str]:
    """The tabs' names; the Sources tab counts the source records linked."""
    return {
        "golden": "Golden",
        "sources": f"Sources and cross-references ({header.member_count})",
        "timeline": "Timeline",
        "relationships": "Relationships",
    }


def failed(notice: Mapping[str, Any] | None) -> Component:
    """A notification payload from `context.guarded`, as a notice in the page."""
    notice = notice or {}
    return common.notice(
        "error" if notice.get("color") == "red" else "warning", str(notice.get("message", ""))
    )


# ---------------------------------------------------------------------------------------------- the header


def open_tasks_link(ctx: UiContext, task_ids: Sequence[str]) -> Component | str:
    """ "2 open tasks" as a link to the first in the inbox (its health, in one phrase); "no open task"."""
    if not task_ids:
        return "no open task"
    words = "1 open task" if len(task_ids) == 1 else f"{len(task_ids)} open tasks"
    if not ctx.can("view_tasks"):
        return words
    return dmc.Anchor(words, href=task_href(task_ids[0]), className="mdm-record-tasks")


def header_view(ctx: UiContext, header: RecordHeader, *, offer_reveal: bool) -> Component:
    """RECORD_HEADER: the title (masked); one line with the master ID, entity, status, the IDs merged into
    it, its source records and last commit (and "no open task", or the count for a role that sees no
    task); the record it was merged into; the open tasks by name, once; "Show values" when offered, and the
    line on what comes later."""
    facts: list[Component | str] = [
        html.Span(header.master_id, className="mdm-record-id"),
        " · ",
        entity_label(header.entity),
        " · ",
        status_badge(header.status),
    ]
    if header.retired_ids:
        facts.append(" · retired IDs: ")
        for index, retired in enumerate(header.retired_ids):
            if index:
                facts.append(", ")
            facts.append(dmc.Anchor(retired, href=record_href(retired)))
    members = "1 source record" if header.member_count == 1 else f"{header.member_count} source records"
    named = bool(header.open_tasks) and ctx.can("view_tasks")  # the tasks line below names them, once
    if not named:
        facts.extend([" · ", open_tasks_link(ctx, header.open_tasks)])
    facts.extend([" · ", members])
    facts.append(f" · last changed in commit {header.commit_version}")
    lines: list[Component] = [
        common.page_title(header.title),
        html.P(facts, className="mdm-record-facts"),
    ]
    if header.survivor_id and header.survivor_id != header.master_id:
        lines.append(
            dmc.Text(
                [
                    "Merged into ",
                    dmc.Anchor(header.survivor_id, href=record_href(header.survivor_id)),
                    ".",
                ],
                size="sm",
            )
        )
    if header.open_tasks and ctx.can("view_tasks"):
        lines.append(task_links(ctx, header.open_tasks))
    actions: list[Component] = []
    if offer_reveal:
        actions.append(reveal.open_button(PAGE))
    actions.append(dmc.Text(LATER, size="xs", className="mdm-later"))
    lines.append(dmc.Group(actions, gap="md", mt=8))
    return html.Div(lines, id=ids.RECORD_HEADER, className="mdm-record-header")


# ---------------------------------------------------------------------------------------------- the tabs


def golden_table(master_id: str, values: Sequence[ValueView]) -> Component:
    """The golden values (GOLDEN_TABLE's children): attribute (critical marked), then the value (masked
    unless revealed) with its provenance chip right after it; the chip opens the value's Why in a row
    under it (WHY_ROW), so the value it explains stays in view."""
    rows: list[Component] = []
    for value in values:
        label: list[Component | str] = [value.label]
        if value.critical:
            label.append(provenance.critical_mark())
        chip = provenance.chip(value)
        rows.append(
            html.Tr(
                [
                    html.Th(label, scope="row"),
                    html.Td(
                        [provenance.value_cell(value.value, masked=value.masked), chip],
                        className="mdm-golden-value",
                    ),
                ]
            )
        )
        if value.chip:
            rows.append(
                html.Tr(
                    html.Td(
                        html.Div(id=ids.why_panel(value.attribute), className="mdm-why-inline"),
                        colSpan=2,
                    ),
                    id=ids.why_row(value.attribute),
                    className="mdm-why-row mdm-hidden",
                )
            )
    return html.Table(
        [
            html.Caption(f"Golden values of {master_id}", className="mdm-sr-only"),
            html.Thead(
                html.Tr(
                    [
                        html.Th("Attribute", scope="col", style={"width": "24%"}),
                        html.Th("Value and where it comes from", scope="col"),
                    ]
                )
            ),
            html.Tbody(rows),
        ],
        className="mdm-table mdm-golden",
    )


def _latest(member: MemberView, now: datetime) -> Component | str:
    if member.occurred_at is None:
        return "not seen"
    when = html.Time(common.relative_time(member.occurred_at, now), dateTime=member.occurred_at.isoformat())
    if member.source_version is None:
        return when
    return html.Span([f"v{member.source_version} · ", when])


def members_table(
    ctx: UiContext, master_id: str, members: Sequence[MemberView], total: int, now: datetime | None = None
) -> Component:
    """The source records linked to the record (MEMBERS_TABLE's children): each links to its source record
    view, with its trust rank, latest version, hold, rule failures and open task. No Detach here (story
    3.6)."""
    moment = now or utcnow()
    if not members:
        return common.empty_state("No source record is linked to this record.")
    rows: list[Component] = []
    for member in members:
        task: Component | str = "none"
        if member.open_task:
            task = source_page.task_anchor(ctx, member.open_task) if ctx.can("view_tasks") else "1 open task"
        rows.append(
            html.Tr(
                [
                    html.Th(provenance.source_link(member.source), scope="row"),
                    html.Td(str(member.trust) if member.trust is not None else "unranked"),
                    html.Td(_latest(member, moment)),
                    html.Td("held" if member.held else "no"),
                    html.Td(", ".join(member.rule_failures) if member.rule_failures else "none"),
                    html.Td(task),
                ]
            )
        )
    parts: list[Component] = [
        html.Table(
            [
                html.Caption(f"Source records linked to {master_id}", className="mdm-sr-only"),
                html.Thead(
                    html.Tr(
                        [
                            html.Th("Source record", scope="col"),
                            html.Th("Trust rank", scope="col"),
                            html.Th("Latest version", scope="col"),
                            html.Th("Update held", scope="col"),
                            html.Th("Rule failures", scope="col"),
                            html.Th("Open task", scope="col"),
                        ]
                    )
                ),
                html.Tbody(rows),
            ],
            className="mdm-table mdm-members",
        )
    ]
    if total > len(members):
        parts.append(dmc.Text(f"Showing {len(members)} of {total} source records.", size="sm", mt=6))
    return html.Div(parts)


def relationships_table(master_id: str, relationships: Sequence[RelationshipView]) -> Component:
    """The record's relationships (RELATIONSHIPS_TABLE's children), one row per type and other end, each
    naming every source that asserts it."""
    if not relationships:
        return common.empty_state("No relationship is recorded for this record.")
    rows: list[Component] = []
    for rel in relationships:
        asserted: list[Component | str] = []
        for index, origin in enumerate(rel.sources):
            if index:
                asserted.append(", ")
            asserted.append(provenance.source_link(origin))
        rows.append(
            html.Tr(
                [
                    html.Th(rel.label, scope="row"),
                    html.Td(
                        [
                            html.Span(rel.other_title),
                            " · ",
                            dmc.Anchor(rel.other_master_id, href=record_href(rel.other_master_id)),
                        ]
                    ),
                    html.Td(rel.valid_from or "not given"),
                    html.Td(rel.valid_to or "not given"),
                    html.Td(rel.status),
                    html.Td(asserted or "none"),
                ]
            )
        )
    shown = len(relationships)
    parts: list[Component] = [
        html.Table(
            [
                html.Caption(f"Relationships of {master_id}", className="mdm-sr-only"),
                html.Thead(
                    html.Tr(
                        [
                            html.Th("Relationship", scope="col"),
                            html.Th("Other record", scope="col"),
                            html.Th("From", scope="col"),
                            html.Th("To", scope="col"),
                            html.Th("Status", scope="col"),
                            html.Th("Asserted by", scope="col"),
                        ]
                    )
                ),
                html.Tbody(rows),
            ],
            className="mdm-table mdm-relationships",
        )
    ]
    if shown >= capacity.RELATIONSHIPS_SHOWN:
        parts.append(dmc.Text(f"Showing the first {shown} relationships.", size="sm", mt=6))
    return html.Div(parts)


def more_class(cursor: Any) -> str:
    """ "Show older" is shown only while an older page remains."""
    return "" if cursor else "mdm-hidden"


def timeline_panel(items: Sequence[Component], cursor: list[int] | None) -> Component:
    """The Timeline tab: the list (TIMELINE_LIST), its cursor (TIMELINE_CURSOR) and "Show older"
    (TIMELINE_MORE)."""
    return html.Div(
        [
            html.Ol(
                list(items),
                id=ids.TIMELINE_LIST,
                className="mdm-timeline",
                style={"listStyle": "none", "padding": 0, "margin": 0},
                **{"aria-label": "Changes, newest first"},
            ),
            dcc.Store(id=ids.TIMELINE_CURSOR, storage_type="memory", data=cursor),
            dmc.Button(
                "Show older",
                id=ids.TIMELINE_MORE,
                n_clicks=0,
                variant="default",
                size="xs",
                mt=8,
                className=more_class(cursor),
            ),
        ]
    )


def timeline_part(
    ctx: UiContext, entity: str, master_id: str, before: tuple[int, int] | None = None
) -> tuple[list[Component], list[int] | None]:
    """One page of the timeline: its items and the cursor of the next (older) page."""
    page = ctx.hub.lookup.timeline(entity, master_id, actor=ctx.actor, before=before)
    return timeline.render(page), list(page.before) if page.before is not None else None


def tab_content(ctx: UiContext, entity: str, master_id: str, tab: str) -> Component:
    """One tab's panel, filled when first shown (R1)."""
    lookup = ctx.hub.lookup
    if tab == "golden":
        return golden_table(master_id, lookup.golden(entity, master_id, actor=ctx.actor))
    if tab == "sources":
        members = lookup.members(entity, master_id, actor=ctx.actor)
        total = len(members)
        if total >= capacity.MEMBERS_SHOWN:  # more may be linked than are listed: count them
            total = lookup.header(entity, master_id, actor=ctx.actor).member_count
        return members_table(ctx, master_id, members, total)
    if tab == "timeline":
        items, cursor = timeline_part(ctx, entity, master_id)
        return timeline_panel(items, cursor)
    if tab == "relationships":
        return relationships_table(master_id, lookup.relationships(entity, master_id, actor=ctx.actor))
    raise ValueError("not a tab of the record")


# ---------------------------------------------------------------------------------------------- the page


def record_ref(entity: str, master_id: str, filled: Sequence[str], personal: Sequence[str]) -> dict:
    """RECORD_REF's data: codes and IDs only."""
    return {
        "entity": entity,
        "master_id": master_id,
        "filled": sorted(set(filled)),
        "personal": sorted(set(personal)),
    }


def golden_page(
    ctx: UiContext, entity: str, master_id: str, tab: str, notices: Sequence[Component] = ()
) -> Component:
    """The golden record page: header, tabs (Golden and the tab asked for filled), the Why rows and,
    when a reveal is offered, the reveal modal."""
    header = ctx.hub.lookup.header(entity, master_id, actor=ctx.actor)
    values = ctx.hub.lookup.golden(entity, master_id, actor=ctx.actor)
    offer = source_page.revealable(ctx, values)
    personal = [v.attribute for v in values if v.personal]
    filled = {"golden", tab}
    contents: dict[str, Component | list] = {name: common.empty_state(LOADING) for name in TABS}
    contents["golden"] = golden_table(master_id, values)
    items: list[Component] = []
    cursor: list[int] | None = None
    if tab == "timeline":
        items, cursor = timeline_part(ctx, entity, master_id)
    elif tab != "golden":
        contents[tab] = tab_content(ctx, entity, master_id, tab)
    labels = tab_labels(header)
    tabs = dmc.Tabs(
        [
            dmc.TabsList([dmc.TabsTab(labels[name], value=name) for name in TABS], mb="md"),
            dmc.TabsPanel(html.Div(contents["golden"], id=ids.GOLDEN_TABLE), value="golden"),
            dmc.TabsPanel(html.Div(contents["sources"], id=ids.MEMBERS_TABLE), value="sources"),
            dmc.TabsPanel(
                timeline_panel(items or [html.Li(LOADING, className="mdm-empty")], cursor), value="timeline"
            ),
            dmc.TabsPanel(
                html.Div(contents["relationships"], id=ids.RELATIONSHIPS_TABLE), value="relationships"
            ),
        ],
        id=ids.RECORD_TABS,
        value=tab,
        mt="md",
    )
    parts: list[Component] = [
        *notices,
        header_view(ctx, header, offer_reveal=offer),
        dcc.Store(
            id=ids.RECORD_REF,
            storage_type="memory",
            data=record_ref(entity, master_id, sorted(filled), personal),
        ),
        dcc.Store(id=ids.WHY_REF, storage_type="memory", data=record_ref(entity, master_id, [], personal)),
        tabs,
    ]
    if offer:
        parts.append(reveal.modal(PAGE))
    return html.Div(parts, id=ids.RECORD, className="mdm-record")


def skeleton() -> Component:
    """The page's components with empty children and no service call, for the validation layout."""
    return html.Div(
        [
            html.Div(id=ids.RECORD_HEADER),
            reveal.open_button(PAGE),
            dcc.Store(id=ids.RECORD_REF, storage_type="memory"),
            dcc.Store(id=ids.WHY_REF, storage_type="memory"),
            dmc.Tabs(
                [
                    dmc.TabsPanel(html.Div(id=ids.GOLDEN_TABLE), value="golden"),
                    dmc.TabsPanel(html.Div(id=ids.MEMBERS_TABLE), value="sources"),
                    dmc.TabsPanel(timeline_panel([], None), value="timeline"),
                    dmc.TabsPanel(html.Div(id=ids.RELATIONSHIPS_TABLE), value="relationships"),
                ],
                id=ids.RECORD_TABS,
                value="golden",
            ),
            reveal.modal(PAGE),
        ],
        id=ids.RECORD,
    )


def layout(ctx: UiContext, ref: str, query: Mapping[str, str]) -> Component:
    """The record `ref` names: a master ID, a retired ID (its survivor, with a notice) or `system:key`
    (its golden record, or the source page when unlinked); an unknown reference gives a notice with a link
    to the inbox."""
    found = ctx.hub.lookup.resolve(ref, actor=ctx.actor, entity=source_page.entity_of(ctx, query))
    if found.kind == "unknown" or found.entity is None:
        return unknown_page(ctx, found.notice or messages.sentence_for("unknown_ref"))
    if found.kind == "source" and found.source is not None:
        system, _, key = found.source.partition(":")
        if found.master_id is None:
            note = common.notice(
                "info", f"{found.source} is not linked to a golden record, so its source record is shown."
            )
            return source_page.layout(ctx, system, key, {"entity": found.entity}, notice=note)
        note = common.notice(
            "info",
            [
                f"{found.source} is linked to {found.master_id}; this is its golden record. ",
                dmc.Anchor("Open the source record", href=provenance.source_href(found.source) or "/"),
            ],
        )
        return golden_page(ctx, found.entity, found.master_id, first_tab(query), [note])
    if found.master_id is None:
        return unknown_page(ctx, messages.sentence_for("unknown_ref"))
    notices = [common.notice("info", found.notice)] if found.notice else []
    return golden_page(ctx, found.entity, found.master_id, first_tab(query), notices)


# ---------------------------------------------------------------------------------------------- callbacks


def ref_of(data: Any) -> tuple[str, str, list[str], list[str]] | None:
    """(entity, master ID, tabs filled, personal attributes) from RECORD_REF's data, or None."""
    if not isinstance(data, Mapping):
        return None
    entity, master_id = data.get("entity"), data.get("master_id")
    if not isinstance(entity, str) or not isinstance(master_id, str):
        return None
    filled = [t for t in data.get("filled") or [] if t in TABS]
    personal = [a for a in data.get("personal") or [] if isinstance(a, str)]
    return entity, master_id, filled, personal


def fill_tab(ctx: UiContext, data: Any, tab: Any) -> tuple:
    """R1 without Dash: (members, timeline items, timeline cursor, "Show older" class, relationships,
    RECORD_REF) for the tab `tab` shown for the first time; `no_update` everywhere for a tab already filled.
    A failure fills the tab with its sentence."""
    unchanged = (no_update,) * 6
    found = ref_of(data)
    if found is None or tab not in TABS:
        return unchanged
    entity, master_id, filled, personal = found
    if tab in filled:
        return unchanged
    out = list(unchanged)
    out[5] = record_ref(entity, master_id, [*filled, tab], personal)
    if tab == "timeline":
        part, failure = context.guarded(timeline_part, ctx, entity, master_id)
        if part is None:
            out[1], out[2], out[3] = [html.Li(failed(failure))], None, more_class(None)
        else:
            out[1], out[2], out[3] = part[0], part[1], more_class(part[1])
        return tuple(out)
    content, failure = context.guarded(tab_content, ctx, entity, master_id, tab)
    shown = content if content is not None else failed(failure)
    if tab == "sources":
        out[0] = shown
    elif tab == "relationships":
        out[4] = shown
    else:  # the Golden tab is filled when the page is built
        return unchanged
    return tuple(out)


def clicked_attribute(triggered_id: Any, inputs: Sequence[Mapping[str, Any]]) -> str | None:
    """The attribute of the provenance chip clicked: the triggering chip, when its clicks are truthy (a chip
    drawn again after a reveal, with no click, opens nothing)."""
    if not isinstance(triggered_id, Mapping) or triggered_id.get("type") != ids.PROV_CHIP:
        return None
    for entry in inputs:
        if entry.get("id") == dict(triggered_id) and entry.get("value"):
            attribute = triggered_id.get("attr")
            return attribute if isinstance(attribute, str) else None
    return None


def why(ctx: UiContext, data: Any, attribute: str) -> tuple[str, Component]:
    """R2 without Dash: the Why's title and body for `attribute` of the record in RECORD_REF, masked
    always. Raises the record reader's refusal."""
    found = ref_of(data)
    if found is None:
        raise ValueError("no record on screen")
    entity, master_id, _filled, personal = found
    explained = ctx.hub.lookup.why(entity, master_id, attribute, actor=ctx.actor)
    return provenance.why_title(explained.label), provenance.why_body(
        explained, personal=attribute in personal
    )


def why_rows(
    ctx: UiContext,
    data: Any,
    attribute: str,
    rows: Sequence[Mapping[str, Any]],
    classes: Sequence[Any],
) -> tuple[list, list, list, dict | None]:
    """R2 without Dash: for the chip of `attribute` pressed, (each Why row's class, each row's panel, each
    chip's `aria-expanded`, a failure's notification): its row opens with the explanation, or closes when
    it was open; the other rows stay as they are. `rows` are the rows' pattern IDs in order."""
    names = [str(row.get("attr")) for row in rows]
    shown = [str(c or "") for c in classes]
    out_class: list = [no_update] * len(names)
    out_panel: list = [no_update] * len(names)
    out_open: list = [no_update] * len(names)
    if attribute not in names:
        return out_class, out_panel, out_open, None
    at = names.index(attribute)
    if "mdm-hidden" not in shown[at]:
        out_class[at], out_open[at] = "mdm-why-row mdm-hidden", "false"
        return out_class, out_panel, out_open, None
    found, failure = context.guarded(why, ctx, data, attribute)
    if found is None:
        return out_class, out_panel, out_open, failure
    title, body = found
    out_class[at], out_open[at] = "mdm-why-row", "true"
    out_panel[at] = html.Div([html.H3(title, className="mdm-why-title"), body], className="mdm-why-box")
    return out_class, out_panel, out_open, None


def reveal_golden(ctx: UiContext, data: Any, reason: Any) -> tuple[Component | None, dict | None, str | None]:
    """R4 without Dash: (the golden table in clear, a notification, the reason field's error). No reason
    chosen: nothing is revealed and the field says so; a refusal: its sentence, nothing revealed. The
    values are shown once, in the table, and kept in no store."""
    code = reveal.checked_reason(reason)
    if code is None:
        return None, None, reveal.REASON_REQUIRED
    found = ref_of(data)
    if found is None:
        return None, common.notify(messages.sentence_for("unknown_ref"), "yellow")[0], None
    entity, master_id, _filled, _personal = found
    values, failure = context.guarded(
        ctx.hub.lookup.golden, entity, master_id, actor=ctx.actor, reveal=True, reason=code
    )
    if values is None:
        return None, failure, None
    return golden_table(master_id, values), source_page.shown_notice(values), None


def older_events(ctx: UiContext, data: Any, cursor: Any) -> tuple[list[Component], list[int] | None]:
    """R5 without Dash: the next (older) page of the timeline after `cursor`, and its own cursor."""
    found = ref_of(data)
    if found is None or not isinstance(cursor, (list, tuple)) or len(cursor) != 2:
        return [], None
    if not all(isinstance(part, int) and not isinstance(part, bool) for part in cursor):
        return [], None
    entity, master_id, _filled, _personal = found
    page = ctx.hub.lookup.timeline(entity, master_id, actor=ctx.actor, before=(cursor[0], cursor[1]))
    items = timeline.render(page) if page.events else []
    return items, list(page.before) if page.before is not None else None


def register(app) -> None:
    """Registers R1–R5 on `app`."""

    @app.callback(
        Output(ids.MEMBERS_TABLE, "children"),
        Output(ids.TIMELINE_LIST, "children"),
        Output(ids.TIMELINE_CURSOR, "data"),
        Output(ids.TIMELINE_MORE, "className"),
        Output(ids.RELATIONSHIPS_TABLE, "children"),
        Output(ids.RECORD_REF, "data"),
        Input(ids.RECORD_TABS, "value"),
        State(ids.RECORD_REF, "data"),
        State(ids.PERSONA, "data"),
        prevent_initial_call=True,
    )
    def show_tab(tab, data, persona):
        found = ref_of(data)
        if found is None or tab in found[2]:
            return (no_update,) * 6
        request_ctx, failure = context.guarded(context.current, persona)
        if request_ctx is None:
            return (no_update,) * 6
        return fill_tab(request_ctx, data, tab)

    @app.callback(
        Output({"type": ids.WHY_ROW, "attr": ALL}, "className"),
        Output({"type": ids.WHY_PANEL, "attr": ALL}, "children"),
        Output({"type": ids.PROV_CHIP, "attr": ALL}, "aria-expanded"),
        Output(ids.NOTIFY, "sendNotifications", allow_duplicate=True),
        Input({"type": ids.PROV_CHIP, "attr": ALL}, "n_clicks"),
        State({"type": ids.WHY_ROW, "attr": ALL}, "className"),
        State({**ids.WHY_REF, "page": ALL}, "data"),
        State(ids.PERSONA, "data"),
        prevent_initial_call=True,
    )
    def open_why(_clicks, classes, refs, persona):
        data = refs[0] if refs else None  # none once the record has left the screen
        rows = [slot.get("id") or {} for slot in (dash_ctx.outputs_list[0] or [])]
        chips = len(dash_ctx.outputs_list[2] or [])
        unchanged = ([no_update] * len(rows), [no_update] * len(rows), [no_update] * chips, no_update)
        attribute = clicked_attribute(
            dash_ctx.triggered_id, dash_ctx.inputs_list[0] if dash_ctx.inputs_list else []
        )
        if attribute is None:
            return unchanged
        request_ctx, failure = context.guarded(context.current, persona)
        if request_ctx is None:
            return (*unchanged[:3], [failure])
        out_class, out_panel, out_open, failure = why_rows(request_ctx, data, attribute, rows, classes or [])
        chip_ids = [str((slot.get("id") or {}).get("attr")) for slot in (dash_ctx.outputs_list[2] or [])]
        expanded = [no_update] * len(chip_ids)
        for name, state in zip((str(r.get("attr")) for r in rows), out_open, strict=False):
            if name in chip_ids:
                expanded[chip_ids.index(name)] = state
        return out_class, out_panel, expanded, [failure] if failure else no_update

    reveal.register_toggle(app, PAGE)

    @app.callback(
        Output(ids.GOLDEN_TABLE, "children", allow_duplicate=True),
        Output(ids.reveal(ids.REVEAL_MODAL, PAGE), "opened", allow_duplicate=True),
        Output(ids.reveal(ids.REVEAL_REASON, PAGE), "error"),
        Output(ids.reveal(ids.REVEAL_OPEN, PAGE), "children"),
        Output(ids.reveal(ids.REVEAL_OPEN, PAGE), "disabled"),
        Output(ids.NOTIFY, "sendNotifications", allow_duplicate=True),
        Input(ids.reveal(ids.REVEAL_CONFIRM, PAGE), "n_clicks"),
        State(ids.reveal(ids.REVEAL_REASON, PAGE), "value"),
        State(ids.RECORD_REF, "data"),
        State(ids.PERSONA, "data"),
        prevent_initial_call=True,
    )
    def confirm_reveal(n_clicks, reason, data, persona):
        if not n_clicks:
            return (no_update,) * 6
        request_ctx, failure = context.guarded(context.current, persona)
        if request_ctx is None:
            return no_update, no_update, None, no_update, no_update, [failure]
        table, shown, error = reveal_golden(request_ctx, data, reason)
        if table is None:
            return no_update, no_update, error, no_update, no_update, [shown] if shown else no_update
        return table, False, None, SHOWN, True, [shown]

    @app.callback(
        Output(ids.TIMELINE_LIST, "children", allow_duplicate=True),
        Output(ids.TIMELINE_CURSOR, "data", allow_duplicate=True),
        Output(ids.TIMELINE_MORE, "className", allow_duplicate=True),
        Output(ids.NOTIFY, "sendNotifications", allow_duplicate=True),
        Input(ids.TIMELINE_MORE, "n_clicks"),
        State(ids.TIMELINE_CURSOR, "data"),
        State(ids.RECORD_REF, "data"),
        State(ids.PERSONA, "data"),
        prevent_initial_call=True,
    )
    def show_older(n_clicks, cursor, data, persona):
        if not n_clicks or not cursor:
            return (no_update,) * 4
        request_ctx, failure = context.guarded(context.current, persona)
        if request_ctx is None:
            return no_update, no_update, no_update, [failure]
        older, failure = context.guarded(older_events, request_ctx, data, cursor)
        if older is None:
            return no_update, no_update, no_update, [failure]
        items, after = older
        appended = Patch()
        appended.extend(items)
        return appended, after, more_class(after), no_update
