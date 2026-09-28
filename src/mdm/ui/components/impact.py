"""The impact line and what changes: "If you link: +1 cross-reference · golden website changes · no ID
retired", and, collapsed under it in the candidate's panel, the golden values that change (label, now,
after) (B.8.7).

The service plans the decision without committing it and hands back the golden values before and after,
masked by role, and the impact counted from the planned change items. The impact line stays in the decide
pane's footer, beside the actions; the values that change sit in the candidate's panel (every value is
already in the compare table above it), collapsed, as "What changes in ORG-000123 (1)".

Owner: INBOX (B.8.7).
"""

from __future__ import annotations

from dash import html
from dash.development.base_component import Component

from mdm.models.workbench import Preview, PreviewRow

#: the text of a value the golden record does not hold
NO_VALUE = "—"
#: how each verb reads: "If you link", "If you approve", …
VERBS = {
    "link": "link",
    "approve": "approve",
    "keep_apart": "keep them apart",
    "keep": "keep it",
}
#: the line when the service could not plan the decision (the commit path would refuse it)
NOT_PLANNED = "If you link: the change could not be planned; the flush would check it again."


def lead(verb: str) -> str:
    """ "If you link", "If you keep them apart"."""
    return f"If you {VERBS.get(verb, verb)}"


def changed_rows(preview: Preview) -> list[PreviewRow]:
    """The rows whose golden value the decision changes (the rest stay as they are)."""
    return [row for row in preview.rows if row.changed]


def heading(preview: Preview, verb: str) -> str:
    """ "What changes in ORG-000123 (1)"; "The new golden record's values" for one to be created;
    "No golden value of ORG-000123 changes" when none does."""
    rows = changed_rows(preview)
    if preview.master_id is None:
        return "The new golden record's values"
    if not rows:
        return f"No golden value of {preview.master_id} changes"
    return f"What changes in {preview.master_id} ({len(rows)})"


def unchanged_line(preview: Preview) -> str | None:
    """What the table leaves out: "Every other golden value stays as it is."; None without values."""
    if not preview.rows or not changed_rows(preview):
        return None
    return "Every other golden value stays as it is."


def _value(value: str | None) -> list:
    if value is None:
        return [
            html.Span(NO_VALUE, **{"aria-hidden": "true"}),
            html.Span("not held", className="mdm-sr-only"),
        ]
    return [value]


def _row(row: PreviewRow) -> Component:
    label: list = [row.label]
    if row.changed:
        label.append(html.Span(" · changes", className="mdm-changed"))
    return html.Tr(
        [
            html.Th(label, scope="row"),
            html.Td(_value(row.now)),
            html.Td(_value(row.after), className="mdm-changed" if row.changed else None),
        ],
        className="mdm-preview-changed" if row.changed else None,
    )


def line(preview: Preview, *, verb: str = "link") -> Component:
    """The impact line: "If you link: +1 cross-reference · golden website changes · no ID retired"; plain
    text, its lead the one weighted phrase."""
    return html.P(
        [html.Span(f"{lead(verb)}: ", className="mdm-impact-lead"), preview.impact.sentence()],
        className="mdm-impact",
    )


def none_line() -> Component:
    """The impact line of a candidate the service could not plan a link to."""
    return html.P(NOT_PLANNED, className="mdm-impact")


def changes(preview: Preview, *, verb: str = "link", open_: bool = False) -> Component:
    """The golden values the decision changes (label, now, after), under a disclosure "What changes in
    ORG-000123 (1)"; one plain line when nothing changes."""
    rows = changed_rows(preview)
    title = heading(preview, verb)
    if not rows:
        return html.P(title + ".", className="mdm-preview-rest")
    table = html.Table(
        [
            html.Caption(title, className="mdm-sr-only"),
            html.Thead(
                html.Tr(
                    [
                        html.Th("Attribute", scope="col"),
                        html.Th("Now", scope="col"),
                        html.Th("After", scope="col"),
                    ]
                )
            ),
            html.Tbody([_row(row) for row in rows]),
        ],
        className="mdm-table mdm-preview",
    )
    parts: list = [
        html.Summary(f"{lead(verb)}: {title[:1].lower()}{title[1:]}", className="mdm-preview-summary"),
        table,
    ]
    rest = unchanged_line(preview)
    if rest:
        parts.append(html.P(rest, className="mdm-preview-rest"))
    return html.Details(parts, open=open_, className="mdm-preview-block")


def render(preview: Preview, *, verb: str = "link") -> Component:
    """What changes, open, and the impact line under it (a held update's or a kept record's pane)."""
    return html.Div([changes(preview, verb=verb, open_=True), line(preview, verb=verb)])
