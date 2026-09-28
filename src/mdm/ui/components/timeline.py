"""A record's timeline, newest first, one line per entry: a spark icon for the automated matcher or a
person icon for a role, the headline, a change of identity marked with a word (and a solid rule, so colour
is never the only cue) and when; the commit ("commit 42" or "nothing published") and who, under what
authority, open on demand, and open from the start for a change of identity (B.8.9).

The actor is "Automated matcher" or a role label and never a person; every text is a field the record
reader returned, built from codes, IDs and attribute labels.

Owner: RECORD (B.8.9).
"""

from __future__ import annotations

from datetime import datetime

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
    """One event as a list item: a disclosure whose summary is the icon, the headline, the identity word
    and the time, and whose body is the commit and who under what authority; open for a change of
    identity."""
    marked = identity_word(event)
    summary: list[Component] = [
        icon("spark", label="Automated matcher") if event.automated else icon("person", label="A person"),
        html.Span(event.headline, className="mdm-tl-headline"),
    ]
    if marked:
        summary.append(html.Span(marked, className="mdm-tag mdm-tl-mark"))
    summary.append(
        html.Time(relative_time(event.at, now), dateTime=event.at.isoformat(), className="mdm-tl-when")
        if event.at is not None
        else html.Span("time unknown", className="mdm-tl-when")
    )
    details: list[Component] = [
        html.Div(
            f"commit {event.commit_version}" if event.published else "nothing published",
            className="mdm-tl-commit",
        )
    ]
    who = who_text(event)
    if who:
        details.append(html.Div(who, className="mdm-tl-who"))
    return html.Li(
        html.Details(
            [
                html.Summary(summary, className="mdm-tl-summary"),
                html.Div(details, className="mdm-tl-details"),
            ],
            open=bool(marked),
            className="mdm-tl-entry",
        ),
        className="mdm-tl-item mdm-tl-identity" if marked else "mdm-tl-item",
    )


def render(page: TimelinePage, now: datetime | None = None) -> list[Component]:
    """One item per event of `page`, newest first (TIMELINE_LIST's children); one line when it is empty."""
    moment = now or utcnow()
    if not page.events:
        return [html.Li(EMPTY, className="mdm-empty")]
    return [item(event, moment) for event in page.events]
