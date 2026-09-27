"""A record's timeline, newest first: when, a spark icon for the automated matcher or a person icon for a
role, the headline, who and under what authority, "commit 42"; merges and other changes of identity
marked with a word as well as a solid line, so colour is never the only cue (B.8.9).

The actor is "Automated matcher" or a role label and never a person; every text is a field the record
reader returned, built from codes, IDs and attribute labels.

Owner: RECORD (B.8.9).
"""

from __future__ import annotations

from datetime import datetime

import dash_mantine_components as dmc
from dash import html
from dash.development.base_component import Component

from mdm.models.canonical import utcnow
from mdm.models.workbench import TimelineEvent, TimelinePage
from mdm.ui.components.common import relative_time
from mdm.ui.components.icons import icon

#: the changes of identity a timeline marks, and the word each is marked with
IDENTITY_WORDS = {
    "merged": "Merge",
    "unmerged": "Unmerge",
    "retired": "Retired",
    "reinstated": "Reinstated",
    "remapped": "Remapped",
}
#: what an empty timeline says
EMPTY = "No change is recorded for this record yet."
_MUTED = {"color": "var(--mdm-muted)", "fontSize": "0.85rem"}


def identity_word(event: TimelineEvent) -> str | None:
    """The word a change of identity is marked with ("Merge", on the survivor as on the record merged into
    it); None for an ordinary change."""
    return IDENTITY_WORDS.get(event.kind)


def who_text(event: TimelineEvent) -> str:
    """ "Automated matcher · Applied automatically under rules v1 · from finance:F000123"; the authority
    once when it repeats the actor."""
    parts = [event.actor] if event.actor else []
    if event.authority and event.authority != event.actor:
        parts.append(event.authority)
    return " · ".join(parts)


def item(event: TimelineEvent, now: datetime) -> Component:
    """One event as a list item."""
    marked = identity_word(event)
    when: list[Component] = [
        html.Time(relative_time(event.at, now), dateTime=event.at.isoformat())
        if event.at is not None
        else html.Span("time unknown"),
        html.Span(f" · commit {event.commit_version}" if event.published else " · nothing published"),
    ]
    what: list[Component] = [
        icon("spark", label="Automated matcher") if event.automated else icon("person", label="A person"),
        html.Strong(event.headline, className="mdm-tl-headline"),
    ]
    if marked:
        what.append(
            dmc.Badge(marked, variant="outline", color="gray", size="sm", tt="none", className="mdm-tl-mark")
        )
    parts: list[Component] = [
        html.Div(when, className="mdm-tl-when", style=_MUTED),
        html.Div(
            what, className="mdm-tl-what", style={"display": "flex", "gap": "6px", "alignItems": "center"}
        ),
    ]
    who = who_text(event)
    if who:
        parts.append(html.Div(who, className="mdm-tl-who", style=_MUTED))
    return html.Li(
        parts,
        className="mdm-tl-item mdm-tl-identity" if marked else "mdm-tl-item",
        style={
            "padding": "6px 0 6px 12px",
            "marginBottom": "6px",
            "borderLeft": f"3px {'solid' if marked else 'dotted'} var(--mdm-rule-strong)",
        },
    )


def render(page: TimelinePage, now: datetime | None = None) -> list[Component]:
    """One item per event of `page`, newest first (TIMELINE_LIST's children); one line when it is empty."""
    moment = now or utcnow()
    if not page.events:
        return [html.Li(EMPTY, className="mdm-empty")]
    return [item(event, moment) for event in page.events]
