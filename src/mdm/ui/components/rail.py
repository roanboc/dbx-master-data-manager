"""The view rail under Inbox: My queue, Team, Breaching, Snoozed, Escalated, then the open tasks by kind,
each a plain link with its capped count, the chosen one marked `aria-current="page"` (S6).

A view is `/?view=<view>`; a kind is `/?view=team&kind=<kind>`, since the counts by kind are over every
open task within the entity filter. The links carry codes only.

Owner: SHELL (B.8.6).
"""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlencode

import dash_mantine_components as dmc
from dash import html
from dash.development.base_component import Component

from mdm.models.tasks import KIND_LABELS, TASK_KINDS
from mdm.models.workbench import TASK_VIEWS, ViewCounts
from mdm.ui.components.common import count_text

#: what each view (TASK_VIEWS) is called
VIEW_LABELS = {
    "mine": "My queue",
    "team": "Team",
    "breaching": "Breaching",
    "snoozed": "Snoozed",
    "escalated": "Escalated",
}
#: the view a kind's link opens: every open task of that kind
KIND_VIEW = "team"


def href(view: str, kind: str | None = None) -> str:
    """The inbox address of a view, and of a kind within it."""
    query = {"view": view} if kind is None else {"view": view, "kind": kind}
    return "/?" + urlencode(query)


def chosen(query: Mapping[str, str | None]) -> tuple[str | None, str | None]:
    """The (view, kind) the rail marks for `query`: a known view (default My queue) and a known kind, or
    (None, None) off the inbox (`query` is empty then)."""
    if not query:
        return None, None
    view = query.get("view")
    view = view if view in TASK_VIEWS else "mine"
    kind = query.get("kind")
    return view, kind if kind in TASK_KINDS else None


def _link(
    label: str, count: int, target: str, *, active: bool, alert: bool = False, description: str | None = None
) -> Component:
    shown = count_text(count)
    # the label and the count as one name ("Team, 44"), which the badge alone would run together
    extra = {"aria-label": f"{label}, {shown}" + (f", {description}" if description else "")}
    if description:
        extra["description"] = description
    if active:
        extra["aria-current"] = "page"
    return dmc.NavLink(
        label=label,
        href=target,
        active=active,
        variant="light",
        className="mdm-rail-link",
        rightSection=dmc.Badge(
            shown,
            variant="light",
            color="red" if alert and count else "gray",
            size="sm",
            className="mdm-count",
        ),
        **extra,
    )


def claimed_words(claimed: int) -> str:
    """Under My queue, which holds every task open to the steward: "3 claimed by you"; "none claimed by
    you"."""
    return f"{count_text(claimed)} claimed by you" if claimed else "none claimed by you"


def render(counts: ViewCounts, query: Mapping[str, str | None]) -> Component:
    """The rail for the inbox query on screen (`view`, `kind`); an empty query (off the inbox) marks none."""
    view, kind = chosen(query)
    views = [
        _link(
            VIEW_LABELS[name],
            counts.views.get(name, 0),
            href(name),
            active=name == view and kind is None,
            alert=name == "breaching",
            description=claimed_words(counts.claimed) if name == "mine" else None,
        )
        for name in TASK_VIEWS
    ]
    kinds = [
        _link(KIND_LABELS[name], counts.kinds.get(name, 0), href(KIND_VIEW, name), active=name == kind)
        for name in TASK_KINDS
    ]
    return html.Div(
        [
            html.Div(views, className="mdm-rail-group"),
            html.H3("Open tasks by kind", className="mdm-nav-subheading"),
            html.Div(kinds, className="mdm-rail-group"),
        ],
        className="mdm-rail",
    )
