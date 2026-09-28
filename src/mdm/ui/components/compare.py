"""The compare table: the attribute column, then one column per record, each cell marked by agreement
with a class and a symbol (=, ≈, ≠, ∅), so colour is never the only cue; masked values in italics,
with "(masked)" for screen readers (B.8.7). The cells stay neutral, and only a disagreement is tinted,
its ≠ red, so the eye goes to what separates them: the row when one record stands against the reference,
the ≠ cell alone when several do, so a candidate that agrees is never painted as disagreeing.

The table shows what the decision rests on: the attributes the matcher compared, the critical ones and,
for a held update, those that change. The rest sit under "Other values (N)", collapsed and marked "not
compared". An attribute none of the records holds is not given a row: one sentence names them instead.
Below the small breakpoint each attribute becomes a card, every value labelled with its column.

The values arrive masked by the service (decision 20); after a reveal the decide page renders the same
table once more from `Revealed.compare`, in clear, inside `DECIDE_COMPARE` and nowhere else.

Owner: INBOX (B.8.7).
"""

from __future__ import annotations

from collections.abc import Sequence

from dash import html
from dash.development.base_component import Component

from mdm.models.workbench import CompareRow
from mdm.ui.components import reveal

#: the symbol beside each agreement mark (AGREEMENT)
SYMBOLS = {"agree": "=", "partial": "≈", "disagree": "≠", "missing": "∅", "": ""}
#: what each mark means, for screen readers and the legend
AGREEMENT_WORDS = {
    "agree": "the same",
    "partial": "similar",
    "disagree": "different",
    "missing": "missing on one side",
}
#: the same, shorter, for the legend beside the heading (a cell's own words stay whole for screen readers)
LEGEND_WORDS = {"agree": "same", "partial": "similar", "disagree": "different", "missing": "missing"}
#: the text of a value no record holds
NO_VALUE = "—"
CAPTION = "The records side by side"
REVEALED_NOTE = "Shown in clear for this task. Your reason was logged, one entry per value."
#: what the rows the matcher did not compare are called
OTHER = "Other values"
NOT_COMPARED = "not compared"


def is_masked(row: CompareRow, value: str | None, *, revealed: bool) -> bool:
    """A personal value the service masked (every personal value is, until a reveal)."""
    return row.personal and value is not None and not revealed


#: where a long value may break between lines: after these, before any break inside a word
BREAKS_AFTER = "/.-@_"


def breakable(text: str) -> list:
    """`text` with a line-break opportunity (`<wbr>`) after each "/", ".", "-", "@" and "_" of a long word,
    so a web address in a narrow column breaks at its parts, not in the middle of one."""
    if len(text) <= 16 or not any(mark in text for mark in BREAKS_AFTER):
        return [text]
    parts: list = []
    start = 0
    for index, char in enumerate(text):
        if char in BREAKS_AFTER and index + 1 < len(text):
            parts.extend([text[start : index + 1], html.Wbr()])
            start = index + 1
    parts.append(text[start:])
    return parts


def _value(row: CompareRow, value: str | None, *, revealed: bool) -> list:
    if value is None:
        return [
            html.Span(NO_VALUE, **{"aria-hidden": "true"}),
            html.Span("not given", className="mdm-sr-only"),
        ]
    if is_masked(row, value, revealed=revealed):
        return [html.Span(value, className="mdm-masked"), html.Span(" (masked)", className="mdm-sr-only")]
    return breakable(value)


def cell(row: CompareRow, index: int, *, revealed: bool = False, column: str = "") -> Component:
    """The value of column `index` (0 = the reference): a later column carries its agreement with the
    reference as a class and a symbol, and the symbol's words for screen readers. `column` is the
    column's header, which the stacked layout shows beside the value."""
    value = row.values[index] if index < len(row.values) else None
    content = _value(row, value, revealed=revealed)
    agreement = row.agreement[index - 1] if 0 < index <= len(row.agreement) else ""
    classes = ["mdm-compare-cell"]
    if agreement:
        classes.append(f"mdm-{agreement}")
        content = [
            html.Span(SYMBOLS[agreement], className="mdm-symbol", **{"aria-hidden": "true"}),
            *content,
            html.Span(f" ({AGREEMENT_WORDS[agreement]})", className="mdm-sr-only"),
        ]
    if row.changed and index == 1:
        classes.append("mdm-changed")
    extra = {"data-column": column} if column else {}
    return html.Td(content, className=" ".join(classes), **extra)


def _label(row: CompareRow) -> Component:
    tags = []
    if row.critical:
        tags.append(html.Span(" · critical", className="mdm-compare-tag"))
    if row.changed:
        tags.append(html.Span(" · changes", className="mdm-compare-tag mdm-changed"))
    return html.Th([row.label, *tags], scope="row")


def empty_rows(rows: Sequence[CompareRow]) -> list[CompareRow]:
    """The rows no record holds a value for."""
    return [row for row in rows if all(value is None for value in row.values)]


def compared(row: CompareRow) -> bool:
    """Whether the matcher compared the attribute on any column."""
    return any(row.agreement)


def main_rows(rows: Sequence[CompareRow]) -> tuple[list[CompareRow], list[CompareRow]]:
    """(the rows the decision rests on, the others): compared, critical or changing rows first; when no row
    was compared at all (a golden record on its own, an update held), every row shows."""
    held = [row for row in rows if row not in empty_rows(rows)]
    if not any(compared(row) for row in held) and not any(row.changed for row in held):
        return held, []
    first = [row for row in held if compared(row) or row.critical or row.changed]
    return first, [row for row in held if row not in first]


def _phrase(label: str) -> str:
    """An attribute as the object of "has": "an email", "a left-on date", "any addresses"."""
    words = label[:1].lower() + label[1:] if len(label) > 1 and label[1:2].islower() else label
    if words.endswith(" on"):
        words = words[:-3] + "-on date"
    if words.endswith("s") and not words.endswith("ss"):
        return f"any {words}"
    return f"{'an' if words[:1] in 'aeiou' else 'a'} {words}"


def empty_sentence(rows: Sequence[CompareRow], columns: int) -> str:
    """ "None of these records has an email or a left-on date."; "This record has no phone." for one."""
    phrases = [_phrase(row.label) for row in rows]
    joined = phrases[0] if len(phrases) == 1 else ", ".join(phrases[:-1]) + " or " + phrases[-1]
    if columns <= 1:
        bare = [p.split(" ", 1)[1] if p.startswith(("a ", "an ", "any ")) else p for p in phrases]
        joined = bare[0] if len(bare) == 1 else ", ".join(bare[:-1]) + " or " + bare[-1]
        return f"This record has no {joined}."
    return f"None of these records has {joined}."


#: the reference column's noun as the legend says it
_REFERENCE_WORDS = {
    "Arriving": "the arriving record",
    "Record": "the record",
    "Approved": "the approved values",
}


def reference_words(header: str) -> str:
    """The reference column's header as the object of "Against": "the arriving record · crm:C000123" for
    "Arriving · crm:C000123", "the record · finance:F000160"; a master ID stays as it is."""
    noun, sep, ref = header.partition(" · ")
    words = _REFERENCE_WORDS.get(noun)
    if words is None:
        words = noun[:1].lower() + noun[1:] if noun[1:2].islower() else noun
    return f"{words}{sep}{ref}"


def legend(reference: str) -> Component:
    """What the marks mean, in one short line beside the heading: "Against the arriving record ·
    crm:C000123: = same, ≈ similar, ≠ different, ∅ missing"."""
    parts: list = [f"Against {reference_words(reference)}: "]
    for index, (code, words) in enumerate(LEGEND_WORDS.items()):
        if index:
            parts.append(", ")
        parts.extend([html.Span(SYMBOLS[code], className="mdm-symbol"), words])
    return html.P(parts, className="mdm-compare-legend")


def disagrees(row: CompareRow) -> bool:
    """Whether any record disagrees with the reference on this attribute."""
    return "disagree" in row.agreement


def _row_class(row: CompareRow, *, other: bool, single: bool) -> str | None:
    """The row's classes: the whole row is tinted for a disagreement only when one record stands against
    the reference; with several, the ≠ cell alone is (`mdm-compare-multi`)."""
    tinted = single and disagrees(row)
    names = [name for name, on in (("mdm-compare-other", other), ("mdm-row-disagree", tinted)) if on]
    return " ".join(names) or None


def _table(
    columns: Sequence[str], rows: Sequence[CompareRow], *, revealed: bool, caption: str, other: bool = False
) -> Component:
    head = html.Thead(
        html.Tr([html.Th("Attribute", scope="col")] + [html.Th(header, scope="col") for header in columns])
    )
    single = len(columns) <= 2
    body = html.Tbody(
        [
            html.Tr(
                [_label(row)]
                + [cell(row, i, revealed=revealed, column=columns[i]) for i in range(len(columns))],
                className=_row_class(row, other=other, single=single),
            )
            for row in rows
        ]
    )
    classes = ["mdm-table", "mdm-compare"]
    if len(columns) >= 3:
        # three or four records side by side (candidates, or a blind review's golden records): the labels
        # wrap and a long value breaks, so every column stays on screen
        classes.append("mdm-compare-wide")
    if not single:
        classes.append("mdm-compare-multi")
    return html.Div(
        html.Table(
            [html.Caption(caption, className="mdm-sr-only"), head, body],
            className=" ".join(classes),
        ),
        className="mdm-compare-scroll",
    )


def render(
    columns: Sequence[str],
    rows: Sequence[CompareRow],
    *,
    revealable: bool = False,
    revealed: bool = False,
) -> Component:
    """The table (DECIDE_COMPARE's children), with "Show values" when `revealable` and a value is
    masked; `revealed` marks a table rendered in clear after a reveal."""
    if not columns:
        return html.Div()
    empty = empty_rows(rows)
    first, rest = main_rows(rows)
    shown = first + rest
    masked = any(is_masked(row, value, revealed=revealed) for row in shown for value in row.values)
    bar: list = [html.H3("Values", className="mdm-decide-subheading")]
    if any(compared(row) for row in shown) and len(columns) > 1:
        bar.append(legend(columns[0]))
    offer = False
    if revealed:
        bar.append(html.P(REVEALED_NOTE, className="mdm-compare-note", role="status"))
    elif revealable and masked:
        bar.append(reveal.open_button("decide"))
        offer = True
    parts: list = [
        html.Div(bar, className="mdm-compare-bar"),
        _table(columns, first, revealed=revealed, caption=CAPTION),
    ]
    blank = html.P(empty_sentence(empty, len(columns)), className="mdm-compare-note") if empty else None
    if rest:
        inside: list = [
            html.Summary(
                [f"{OTHER} ({len(rest)})", html.Span(f" · {NOT_COMPARED}", className="mdm-compare-tag")],
                className="mdm-compare-summary",
            ),
            _table(columns, rest, revealed=revealed, caption=f"{OTHER}, {NOT_COMPARED}", other=True),
        ]
        if blank is not None:
            inside.append(blank)  # with the other values, out of the way until asked for
        parts.append(html.Details(inside, className="mdm-compare-rest"))
    elif blank is not None:
        parts.append(blank)
    if offer:
        # the modal travels with its button: both are on the page or neither is (a callback whose
        # input is here and whose output is gone would fail)
        parts.append(reveal.modal("decide"))
    return html.Div(parts, className="mdm-compare-block")
