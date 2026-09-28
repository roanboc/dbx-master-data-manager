"""The inbox's health strip, one quiet line with "·" between its figures: "Last arrival 5 min ago · 291 read
· 96% settled automatically · Open 31 · Breaching 3 · In the tray 1 · Last commit 6, 4 min ago"; Breaching
is red and a link when above 0. Counts capped. Under it, only while the quality breaker has paused an
entity's automatic linking, one short line per entity in the warning colour (`components.breaker`); quality
samples count in none of these figures.

Times read relative to now, so they read the same whatever the steward's time zone; a count at the cap
reads "999+", since no count on screen reads more than a thousand rows.

Owner: INBOX (B.8.7).
"""

from __future__ import annotations

from datetime import datetime

import dash_mantine_components as dmc
from dash import html
from dash.development.base_component import Component

from mdm.models.workbench import Health
from mdm.ui.components import breaker, rail
from mdm.ui.components.common import count_text, relative_time
from mdm.ui.components.icons import icon


def arrival_text(health: Health, now: datetime) -> str:
    """ "Last arrival 5 min ago · 291 read · 96% settled automatically"; "No arrival run yet"."""
    if health.last_arrival_at is None:
        return "No arrival run yet"
    parts = [f"Last arrival {relative_time(health.last_arrival_at, now)}"]
    if health.arrival_read is not None:
        parts.append(f"{health.arrival_read:,} read")
    if health.arrival_automatic is not None:
        parts.append(f"{round(health.arrival_automatic * 100)}% settled automatically")
    return " · ".join(parts)


def commit_text(health: Health, now: datetime) -> str:
    """ "Last commit 6, 4 min ago"; "No commit yet"."""
    if not health.last_commit_version:
        return "No commit yet"
    when = f", {relative_time(health.last_commit_at, now)}" if health.last_commit_at else ""
    return f"Last commit {health.last_commit_version}{when}"


def _figure(label: str, value: str) -> Component:
    return html.Span(f"{label} {value}", className="mdm-health-item")


def _separated(items: list[Component]) -> list[Component]:
    """The figures with a muted "·" between each, hidden from screen readers (each figure is its own)."""
    out: list[Component] = []
    for index, item in enumerate(items):
        if index:
            out.append(html.Span(" · ", className="mdm-health-sep", **{"aria-hidden": "true"}))
        out.append(item)
    return out


def render(health: Health, now: datetime) -> Component:
    """The strip (HEALTH_STRIP's children): one quiet line of muted figures separated by "·" (styles.css
    `.mdm-health`); Breaching turns red, a link, only when above 0. A paused entity adds its line under the
    figures, with no live role (the strip is drawn again on every poll)."""
    breaching: Component
    if health.breaching:
        breaching = dmc.Anchor(
            [icon("alert"), f"Breaching {count_text(health.breaching)}"],
            href=rail.href("breaching"),
            className="mdm-health-item mdm-breaches",
        )
    else:
        breaching = _figure("Breaching", "0")
    items: list[Component] = [
        html.Span(arrival_text(health, now), className="mdm-health-item"),
        _figure("Open", count_text(health.open_tasks)),
        breaching,
        _figure("In the tray", count_text(health.staged)),
        html.Span(commit_text(health, now), className="mdm-health-item"),
    ]
    strip = html.Div(
        _separated(items),
        className="mdm-health",
        role="group",
        **{"aria-label": "Health of the hub"},
    )
    if not health.paused:
        return strip
    return html.Div(
        [strip, *(breaker.line(view, now) for view in health.paused)], className="mdm-health-block"
    )
