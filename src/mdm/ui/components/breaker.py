"""What the workbench says about a paused automatic band (story 3.2): one quiet line per entity in the
inbox's health strip, and a one-line notice in the decide pane of a task of that entity, with why and what
waits behind it, open at once on a record the breaker made wait for review.

The quality breaker pauses automatic linking for an entity when blind review confirms too few of its
automatic links, or when far more records arrive in an hour than usual. Only a data owner restores it,
on the command line; nothing here offers to. Pure: the sentences are built from `BreakerView`'s safe
numbers (counts, a threshold, an hour), never from a value. Percentages are rounded down, so a band
confidently below 95% never reads "95%".

Owner: CHECKPOINT-UI (plan 4, B).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from dash import html
from dash.development.base_component import Component

from mdm.models.workbench import BreakerView, BulkRightView
from mdm.services import display
from mdm.ui.components.common import notice
from mdm.ui.components.icons import icon

#: how the row of a record the breaker made wait for review names its reason
WAITS_REASON = display.REASONS["breaker_demoted"][0]
#: what the decide pane adds under the breaker's sentence
WAITING = (
    "Records that would have linked automatically wait here for review. Only a data owner restores "
    "automatic linking, on the command line; there is no button for it here."
)
#: what follows a pattern's withdrawn bulk decisions (story 3.3): no restore control anywhere on screen
BULK_WAITING = (
    "Its reviews are decided one by one. Only a data owner restores bulk decisions, on the command line; "
    "there is no button for it here."
)


def entity_label(entity: str) -> str:
    """ "Organisation" for `organisation`, "Student record" for `student_record`."""
    return entity.replace("_", " ").capitalize()


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return value


def _count(value: Any) -> str:
    """ "12,400"; "1,100"; "12.5" when the number has a tenth."""
    number = _number(value)
    if number is None:
        return "0"
    if float(number).is_integer():
        return f"{int(number):,}"
    return f"{number:,.1f}"


def percent_down(part: int, whole: int) -> int:
    """`part` of `whole` as a whole percentage, rounded down: 39 of 40 is 97, never 98."""
    return (part * 100) // whole if whole > 0 else 0


def threshold_percent(value: Any) -> int:
    """A threshold (0.95) as a whole percentage, rounded down (95); the tiny guard keeps 0.95 at 95."""
    number = _number(value)
    return math.floor(number * 100 + 1e-9) if number is not None else 0


def _moment(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def clock(moment: datetime, now: datetime | None = None) -> str:
    """ "14:02 UTC"; "26 Sep, 23:05 UTC" when it is not today (UTC) any more."""
    moment = moment.astimezone(UTC) if moment.tzinfo else moment.replace(tzinfo=UTC)
    time = f"{moment:%H:%M} UTC"
    if now is not None and moment.date() != now.astimezone(UTC).date():
        return f"{moment.day} {moment:%b}, {time}"
    return time


def why(view: BreakerView) -> str:
    """Why the breaker paused it, from its figures, as a clause: "blind review confirmed 30 of the last 40
    automatic links (75%), confidently below 95%"; "12,400 records arrived in the hour from 14:00 UTC, more
    than 5 times the mean for that hour over the last 7 days (1,100)"."""
    figures = view.figures or {}
    if view.trigger == "agreement":
        agreed = _number(figures.get("agreed"))
        reviewed = _number(figures.get("reviewed"))
        if agreed is None or reviewed is None or reviewed <= 0:
            return "blind review confirmed too few of its automatic links"
        share = percent_down(int(agreed), int(reviewed))
        below = threshold_percent(figures.get("threshold"))
        return (
            f"blind review confirmed {_count(agreed)} of the last {_count(reviewed)} automatic links "
            f"({share}%), confidently below {below}%"
        )
    if view.trigger == "volume":
        arrivals = _count(figures.get("arrivals"))
        hour = _moment(figures.get("hour"))
        when = f"in the hour from {hour:%H:%M} UTC" if hour is not None else "in one hour"
        days = _number(figures.get("days"))
        over = f"over the last {_count(days)} days" if days else "over the last days"
        mean = _number(figures.get("mean"))
        if not mean:
            return f"{arrivals} records arrived {when}; none arrived in that hour {over}"
        multiple = _count(figures.get("multiple"))
        return (
            f"{arrivals} records arrived {when}, more than {multiple} times the mean for that hour {over} "
            f"({_count(mean)})"
        )
    return "the quality breaker tripped"


def sentence(view: BreakerView) -> str:
    """Why automatic linking is paused, in one sentence built from the breaker's figures:

    "Automatic linking for Organisation is paused: blind review confirmed 30 of the last 40 automatic
    links (75%), confidently below 95%."; "Automatic linking for Person is paused: 12,400 records arrived
    in the hour from 14:00 UTC, more than 5 times the mean for that hour over the last 7 days (1,100)."
    """
    return f"{headline(view)[:-1]}: {why(view)}."


def headline(view: BreakerView) -> str:
    """ "Automatic linking for Organisation is paused."."""
    return f"Automatic linking for {entity_label(view.entity)} is paused."


def line_text(view: BreakerView, now: datetime | None = None) -> str:
    """ "Organisation: automatic linking paused since 14:02 UTC" (the decide pane of its tasks says by what
    and why)."""
    return f"{entity_label(view.entity)}: automatic linking paused since {clock(view.since, now)}"


def line(view: BreakerView, now: datetime | None = None) -> Component:
    """The inbox's line for one paused entity: an alert icon and the line, in the warning colour. No live
    role: the strip is drawn again every 30 seconds, and the decide pane says it in context."""
    return html.P(
        [icon("alert"), line_text(view, now)],
        className="mdm-breaker-line",
        **{"data-entity": view.entity},
    )


def pane_notice(view: BreakerView, *, open_: bool = False) -> Component:
    """The decide pane's notice for a task of a paused entity, with the warning rule: one line, "Automatic
    linking for Organisation is paused.", and behind it why (the breaker's figures) and what waits and who
    restores it. `open_` shows the figures at once, on a record the breaker made wait for review, whose
    reason and footer already say what waits and who restores it."""
    reason = why(view)
    because = f"{reason[:1].upper()}{reason[1:]}."
    found = notice(
        "warning",
        html.Details(
            [
                html.Summary(headline(view)),
                html.P(because if open_ else [because, " ", WAITING]),
            ],
            open=open_,
        ),
    )
    found.className = f"{found.className} mdm-breaker-notice"
    return found


# ---------------------------------------------------------------------------------------------- bulk rights


def bulk_sentence(view: BulkRightView) -> str:
    """Why a pattern's bulk decisions are withdrawn (story 3.3), from the breaker's safe figures: "Bulk
    decisions for this pattern are withdrawn: blind review agreed 3 of the last 5 batch links (60%),
    confidently below 95%."."""
    figures = view.figures or {}
    agreed = _number(figures.get("agreed"))
    reviewed = _number(figures.get("reviewed"))
    lead = "Bulk decisions for this pattern are withdrawn"
    if agreed is None or reviewed is None or reviewed <= 0:
        return f"{lead}: blind review agreed with too few of its batch links."
    share = percent_down(int(agreed), int(reviewed))
    below = threshold_percent(figures.get("threshold"))
    return (
        f"{lead}: blind review agreed {_count(agreed)} of the last {_count(reviewed)} batch links "
        f"({share}%), confidently below {below}%."
    )


def bulk_notice(view: BulkRightView) -> Component:
    """A warning notice with no live role (the pages that show it are drawn again on a poll): the sentence,
    then what follows and who restores it. It offers no control: only a data owner restores bulk
    decisions, on the command line."""
    return html.Div(
        [html.P(bulk_sentence(view)), html.P(BULK_WAITING)],
        className="mdm-notice mdm-notice-warning mdm-bulk-notice",
        **{"data-band": view.key},
    )
