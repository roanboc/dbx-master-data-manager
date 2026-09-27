"""The provenance chip right after each golden value ("finance · source trust · 2 d"; "Steward pin · until
30 Nov 2026") and the body of the Why it opens under the value: the sentence, the value used and the
runners-up with their sources and ages (the date and time when their ages round alike); masked always
(B.8.9).

A chip is a button whose visible text is the chip and whose name adds what it opens ("…, why this
phone?"), with `aria-expanded`, so a keyboard and a screen reader reach the Why as a pointer does. Everything shown is a
field the record reader returned, already masked by role, or an ID, a code or an attribute label.

Owner: RECORD (B.8.9).
"""

from __future__ import annotations

from urllib.parse import quote

import dash_mantine_components as dmc
from dash import html
from dash.development.base_component import Component

from mdm.models.workbench import RunnerUp, ValueView, ValueWhy
from mdm.ui import ids

#: what a value cell says when no source holds a value
NO_VALUE = "No value"
#: the source of a value a steward set, as `ValueView.source` names it
STEWARD = "steward"
#: what the Why says under the values of a personal attribute
MASKED_NOTE = "Personal values stay masked here, even after Show values."


def label_words(label: str) -> str:
    """An attribute's label inside a sentence: "phone", "birth date", "registered ID" (an acronym stays)."""
    first, sep, rest = label.partition(" ")
    if len(first) > 1 and first[1:].islower():
        first = first.lower()
    return first + sep + rest


def why_title(label: str) -> str:
    """The Why's title: "Why this phone?"."""
    return f"Why this {label_words(label)}?"


def age_text(days: int | None) -> str:
    """ "today", "1 day old", "14 days old"; "age unknown" when none."""
    if days is None:
        return "age unknown"
    if days <= 0:
        return "today"
    return "1 day old" if days == 1 else f"{days} days old"


def source_href(source: str) -> str | None:
    """The source record view of a source key ("crm:C000123" → "/source/crm/C000123"); None for a
    steward's value or anything that is not a source key."""
    system, sep, key = source.partition(":")
    if not sep or not system or not key or system == STEWARD:
        return None
    return f"/source/{quote(system, safe='')}/{quote(key, safe='')}"


def source_link(source: str | None) -> Component:
    """A source key as a link to its source record; a steward's value as plain words."""
    if not source:
        return html.Span("no source", className="mdm-missing")
    if source == STEWARD:
        return html.Span("Steward value")
    href = source_href(source)
    if href is None:
        return html.Span(source)
    return dmc.Anchor(source, href=href, className="mdm-source-link")


def is_pin(value: ValueView) -> bool:
    """Whether a steward's value decided (a pin): drawn in the pin's colour."""
    return value.decided_by == "pin" or value.pinned_until is not None or value.source == STEWARD


def chip(value: ValueView) -> Component:
    """The chip button (`ids.prov_chip(attribute)`) that opens the Why under its value; nothing when no chip."""
    if not value.chip:
        return html.Span()
    return html.Button(
        [value.chip, html.Span(f", {why_title(value.label).lower()}", className="mdm-sr-only")],
        id=ids.prov_chip(value.attribute),
        n_clicks=0,
        type="button",
        className="mdm-prov mdm-prov-pin" if is_pin(value) else "mdm-prov",
        **{"aria-expanded": "false"},
    )


def critical_mark() -> Component:
    """The word "critical" beside an attribute a change of which needs a second pair of eyes."""
    return dmc.Badge("critical", variant="outline", color="gray", size="xs", ml=6, tt="none")


def value_cell(text: str | None, *, masked: bool) -> Component:
    """A value as the steward reads it: "No value"; a masked form in italics, named as masked to a screen
    reader; or the text."""
    if text is None:
        return html.Span(NO_VALUE, className="mdm-missing")
    if masked:
        return html.Span(
            [html.Span(text, className="mdm-masked"), html.Span(" (masked)", className="mdm-sr-only")]
        )
    return html.Span(text)


def moment_text(entry: RunnerUp, *, seconds: bool = False) -> str:
    """ "4 Feb 2026, 10:32 UTC" ("…, 10:32:07 UTC" with `seconds`): when the source record last changed;
    its age when that is not known."""
    at = entry.occurred_at
    if at is None:
        return age_text(entry.age_days)
    return f"{at.day} {at.strftime('%b %Y, %H:%M:%S' if seconds else '%b %Y, %H:%M')} UTC"


def minutes_tie(entries: list[RunnerUp]) -> bool:
    """Whether two of the values changed within the same minute, so only the seconds tell which is the
    most recent."""
    shown = [moment_text(e) for e in entries if e.occurred_at is not None]
    return len(shown) != len(set(shown))


def _value_row(
    role: str, entry: RunnerUp, *, personal: bool, exact: bool = False, seconds: bool = False
) -> Component:
    return html.Tr(
        [
            html.Td(role),
            html.Td(source_link(entry.source)),
            html.Td(value_cell(entry.value, masked=personal)),
            html.Td(moment_text(entry, seconds=seconds) if exact else age_text(entry.age_days)),
        ]
    )


def ages_tie(entries: list[RunnerUp]) -> bool:
    """Whether two of the values read the same age in days, so only the date and time can tell which is
    the most recent."""
    ages = [e.age_days for e in entries if e.age_days is not None]
    return len(ages) != len(set(ages))


def version_text(why: ValueWhy) -> str:
    """That the rules version was not recorded; "" when the sentence names it already."""
    if why.rule_version is None:
        return "The survivorship rules version was not recorded for this value."
    return ""


def why_body(why: ValueWhy, *, personal: bool = False) -> Component:
    """The Why's body (inside WHY_PANEL): the sentence, a table of the value used and the
    runners-up with their sources and ages, the strategies in order and the rule version. The values stay
    masked here whatever was revealed on the record; `personal` says so under them."""
    parts: list[Component] = [html.P(why.sentence, className="mdm-why-sentence")]
    rows: list[Component] = []
    entries = ([why.winner] if why.winner is not None else []) + list(why.runners_up)
    exact = ages_tie(entries)
    seconds = exact and minutes_tie(entries)
    if why.winner is not None:
        rows.append(_value_row("Used", why.winner, personal=personal, exact=exact, seconds=seconds))
    rows.extend(
        _value_row("Runner-up", runner, personal=personal, exact=exact, seconds=seconds)
        for runner in why.runners_up
    )
    if rows:
        parts.append(
            html.Table(
                [
                    html.Caption(
                        f"Values the sources hold for {label_words(why.label)}", className="mdm-sr-only"
                    ),
                    html.Thead(
                        html.Tr(
                            [
                                html.Th("Role", scope="col"),
                                html.Th("Source", scope="col"),
                                html.Th("Value", scope="col"),
                                html.Th("Changed" if exact else "Age", scope="col"),
                            ]
                        )
                    ),
                    html.Tbody(rows),
                ],
                className="mdm-table mdm-why-table",
            )
        )
        if not why.runners_up:
            parts.append(html.P("No other source holds a value.", className="mdm-why-note"))
    notes = version_text(why)
    if notes:
        parts.append(html.P(notes, className="mdm-why-note"))
    if personal:
        parts.append(html.P(MASKED_NOTE, className="mdm-why-note"))
    return html.Div(parts, className="mdm-why")
