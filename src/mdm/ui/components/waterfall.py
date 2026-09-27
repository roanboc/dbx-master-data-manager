"""The match-weight waterfall, to scale: one row per step from the prior, on one axis of weights with a zero
line and the band edges marked by the scores they stand for; a bar from the running total before the step
to after it, the signed weight; a total row with a marker, "Total +1.9 → score 79"; and a visually hidden
table repeating the numbers. Pure HTML with inline percentages (B.8.7).

Every number comes from the engine's explanation as the service handed it (`Candidate.steps`, `total`,
`thresholds`, `what_if`), so the picture is the arithmetic the matcher did: the prior, each comparison's
weight, and where the total falls between the band edges. A comparison one record leaves empty adds no
weight; when giving it would move the band, its row shows that weight as a dashed bar of its real length.

Owner: INBOX (B.8.7).
"""

from __future__ import annotations

import math

from dash import html
from dash.development.base_component import Component

from mdm.models import wording
from mdm.models.workbench import Candidate, WaterfallStep

#: the least the axis shows on each side of zero, in weight (log2 odds); its ends are whole numbers
AXIS_LOW = -5.0
AXIS_HIGH = 7.0
AXIS_MARGIN = 0.5
#: a weight this close to zero draws no bar
ZERO = 0.005
#: the marks a step's label ends with (the engine's level, as a symbol)
_LEVEL_SYMBOLS = ("=", "≈", "≠", "∅")
#: each row: label, track, weight
ROW_STYLE = {
    "display": "grid",
    "gridTemplateColumns": "minmax(7rem, 10.5rem) 1fr 4.2rem",
    "alignItems": "center",
    "gap": "8px",
    "minHeight": "17px",
}


def axis(candidate: Candidate) -> tuple[float, float]:
    """The weights at the axis's two ends, whole numbers: `floor(min(−5, lowest) − 0.5)` and
    `ceil(max(7, highest) + 0.5)`, over every running total, the total, both band edges and every dashed
    what-if bar."""
    weights = [0.0, candidate.total, *candidate.thresholds]
    what_if = dict(candidate.what_if)
    for step in candidate.steps:
        weights.extend((step.start, step.end))
        if step.comparison in what_if and abs(step.weight) < ZERO:
            weights.append(step.start + what_if[step.comparison])
    finite = [w for w in weights if math.isfinite(w)]
    return (
        float(math.floor(min(AXIS_LOW, *finite) - AXIS_MARGIN)),
        float(math.ceil(max(AXIS_HIGH, *finite) + AXIS_MARGIN)),
    )


def position(weight: float, low: float, high: float) -> float:
    """Where `weight` sits on the axis, in per cent from the left (0–100)."""
    if not math.isfinite(weight) or high <= low:
        return 0.0 if not math.isfinite(weight) or weight <= low else 100.0
    return round(min(max((weight - low) / (high - low) * 100.0, 0.0), 100.0), 3)


def score_of(weight: float) -> float:
    """A weight (log2 odds) as a score, 0–100."""
    if weight > 60:
        return 100.0
    if weight < -60:
        return 0.0
    odds = 2.0**weight
    return 100.0 * odds / (1.0 + odds)


def title(candidate: Candidate) -> str:
    """ "Why 79: match weights, to scale"."""
    return f"Why {wording.score_text(candidate.score)}: match weights, to scale"


def weight_words(weight: float) -> str:
    """ "plus 9.5", "minus 2.6", "no weight"."""
    if abs(weight) < ZERO:
        return "no weight"
    return f"{'plus' if weight > 0 else 'minus'} {abs(weight):.1f}"


def step_name(step: WaterfallStep) -> str:
    """The step's label without its level symbol: "Name" for "Name ="."""
    head, _, tail = step.label.rpartition(" ")
    return head if head and tail in _LEVEL_SYMBOLS else step.label


def missing(step: WaterfallStep) -> bool:
    """A comparison one of the records gives no value for: it adds no weight."""
    return step.comparison is not None and step.level_words == "missing"


def step_words(step: WaterfallStep, what_if: float | None = None) -> str:
    """What a screen reader hears for one row: "Name: the same, plus 9.5"; "Prior: minus 9.0";
    "Registered ID: missing, no weight; given and matching, it would add plus 5.3"."""
    if step.comparison is None:
        return f"{step_name(step)}: {weight_words(step.weight)}"
    level = step.level_words or "compared"
    words = f"{step_name(step)}: {level}, {weight_words(step.weight)}"
    if what_if is not None and missing(step):
        words += f"; given, it could {'add' if what_if > 0 else 'take away'} {abs(what_if):.1f}"
    return words


def band_words(band: str | None) -> str:
    """ "automatic", "review", "distinct"."""
    return {"auto": "automatic", "review": "review", "distinct": "distinct"}.get(band or "", band or "")


def _regions(low: float, high: float, thresholds: tuple[float, float]) -> list[Component]:
    lower, upper = sorted(thresholds)
    at_lower, at_upper = position(lower, low, high), position(upper, low, high)
    spans = (
        ("distinct", 0.0, at_lower),
        ("review", at_lower, at_upper),
        ("auto", at_upper, 100.0),
    )
    regions = [
        html.Div(
            className=f"mdm-wf-region mdm-wf-region-{band}",
            style={"left": f"{start}%", "width": f"{max(end - start, 0.0):.3f}%"},
        )
        for band, start, end in spans
    ]
    regions.append(html.Div(className="mdm-wf-zero-line", style={"left": f"{position(0.0, low, high)}%"}))
    return regions


def bar_geometry(step: WaterfallStep, low: float, high: float) -> tuple[float, float, str]:
    """(left %, width %, class) of one step's bar: up (green) or down (red) from the running total before
    it to after it; no bar (width 0) when the weight is about zero."""
    if abs(step.weight) < ZERO:
        return position(step.end, low, high), 0.0, "mdm-wf-none"
    start, end = position(step.start, low, high), position(step.end, low, high)
    left, right = min(start, end), max(start, end)
    return left, round(max(right - left, 0.4), 3), "mdm-wf-up" if step.weight > 0 else "mdm-wf-down"


def ghost_geometry(step: WaterfallStep, what_if: float, low: float, high: float) -> tuple[float, float]:
    """(left %, width %) of the dashed bar a missing comparison would add, from its running total."""
    start, end = position(step.start, low, high), position(step.start + what_if, low, high)
    return min(start, end), round(max(abs(end - start), 0.4), 3)


def _row(
    label: str, track: list[Component], weight: str, words: str, *, total: bool = False, ghost: bool = False
) -> Component:
    classes = "mdm-wf-row mdm-wf-total" if total else "mdm-wf-row"
    return html.Div(
        [
            html.Span(label, className="mdm-wf-label", **{"aria-hidden": "true"}),
            html.Div(track, className="mdm-wf-track", **{"aria-hidden": "true"}),
            html.Span(
                weight,
                className="mdm-wf-weight mdm-wf-weight-ghost" if ghost else "mdm-wf-weight",
                **{"aria-hidden": "true"},
            ),
        ],
        className=classes,
        style={**ROW_STYLE, "fontWeight": 650} if total else ROW_STYLE,
        role="img",
        **{"aria-label": words},
    )


def axis_row(candidate: Candidate, low: float, high: float) -> Component:
    """The axis under the rows: its ends and zero as weights, the band edges as the scores they stand
    for ("60", "90"). Hidden from screen readers: the rows and the table say the same."""
    lower, upper = sorted(candidate.thresholds)
    ticks = [
        (low, wording.signed(low).replace(".0", ""), "mdm-wf-tick mdm-wf-tick-end mdm-wf-tick-low"),
        (lower, wording.score_text(score_of(lower)), "mdm-wf-tick mdm-wf-tick-score"),
        (upper, wording.score_text(score_of(upper)), "mdm-wf-tick mdm-wf-tick-score"),
        (high, wording.signed(high).replace(".0", ""), "mdm-wf-tick mdm-wf-tick-end mdm-wf-tick-high"),
    ]
    marks = [
        html.Span(text, className=classes, style={"left": f"{position(at, low, high)}%"})
        for at, text, classes in ticks
    ]
    return html.Div(
        [
            html.Span("weight · score", className="mdm-wf-label mdm-wf-axis-label"),
            html.Div(marks, className="mdm-wf-axis"),
            html.Span(""),
        ],
        className="mdm-wf-row mdm-wf-axis-row",
        style=ROW_STYLE,
        **{"aria-hidden": "true"},
    )


def _table(candidate: Candidate) -> Component:
    rows = [
        html.Tr(
            [
                html.Th(step_name(step), scope="row"),
                html.Td(step.level_words or ""),
                html.Td(wording.signed(step.weight)),
                html.Td(wording.signed(step.end)),
            ]
        )
        for step in candidate.steps
    ]
    lower, upper = sorted(candidate.thresholds)
    rows.append(
        html.Tr(
            [
                html.Th("Total", scope="row"),
                html.Td(f"{wording.score_text(candidate.score)} {band_words(candidate.band)}"),
                html.Td(""),
                html.Td(wording.signed(candidate.total)),
            ]
        )
    )
    table = html.Table(
        [
            html.Caption(
                f"Match weights of {candidate.master_id}: review from {wording.signed(lower)} "
                f"(a score of {wording.score_text(score_of(lower))}), automatic from {wording.signed(upper)} "
                f"(a score of {wording.score_text(score_of(upper))})"
            ),
            html.Thead(
                html.Tr(
                    [
                        html.Th("Step", scope="col"),
                        html.Th("Level", scope="col"),
                        html.Th("Weight", scope="col"),
                        html.Th("Running total", scope="col"),
                    ]
                )
            ),
            html.Tbody(rows),
        ]
    )
    # hidden by its wrapper: a table grows to its content whatever width it is given, and would widen
    # the page on a narrow screen
    return html.Div(table, className="mdm-sr-only")


def render(candidate: Candidate) -> Component:
    """The waterfall of `candidate` to scale: a row per step (each labelled for screen readers, "Name:
    the same, plus 9.5"), a missing comparison that would move the band drawn as a dashed bar of the weight
    it would add, the total with a marker and its score, the axis, and a visually hidden table of the
    numbers. The decide pane puts `title(candidate)` above it."""
    low, high = axis(candidate)
    what_if = dict(candidate.what_if)
    rows = []
    # comparisons a record leaves empty and that could not move the band add nothing: one row names them
    quiet = [
        s for s in candidate.steps if missing(s) and abs(s.weight) < ZERO and s.comparison not in what_if
    ]
    for step in candidate.steps:
        if step in quiet:
            continue
        left, width, kind = bar_geometry(step, low, high)
        track = [*_regions(low, high, candidate.thresholds)]
        extra = what_if.get(step.comparison or "") if missing(step) else None
        if width:
            track.append(
                html.Div(className=f"mdm-wf-bar {kind}", style={"left": f"{left}%", "width": f"{width}%"})
            )
        elif extra is not None and abs(extra) >= ZERO:
            ghost_left, ghost_width = ghost_geometry(step, extra, low, high)
            track.append(
                html.Div(
                    className="mdm-wf-bar mdm-wf-ghost",
                    style={"left": f"{ghost_left}%", "width": f"{ghost_width}%"},
                )
            )
        elif missing(step):
            track.append(html.Span("∅", className="mdm-wf-empty", style={"left": f"{left}%"}))
        weight = wording.signed(step.weight)
        ghost = extra is not None and abs(extra) >= ZERO and not width
        if ghost:
            weight = f"({wording.signed(extra)})"
        rows.append(_row(step.label, track, weight, step_words(step, extra), ghost=ghost))
    if quiet:
        names = [step_name(s) for s in quiet]
        words = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        at = position(quiet[-1].end, low, high)
        track = [
            *_regions(low, high, candidate.thresholds),
            html.Span("∅", className="mdm-wf-empty", style={"left": f"{at}%"}),
        ]
        label = f"Not given ({len(names)}) ∅" if len(names) > 1 else f"{names[0]} ∅"
        rows.append(_row(label, track, wording.signed(0.0), f"{words}: missing on one side, no weight"))
    marker = position(candidate.total, low, high)
    total_track = [
        *_regions(low, high, candidate.thresholds),
        html.Div(className="mdm-wf-marker", style={"left": f"{marker}%"}),
    ]
    score = wording.score_text(candidate.score)
    rows.append(
        _row(
            f"Total {wording.signed(candidate.total)} → score {score}",
            total_track,
            band_words(candidate.band),
            f"Total: {weight_words(candidate.total)}, a score of {score}, {band_words(candidate.band)}",
            total=True,
        )
    )
    rows.append(axis_row(candidate, low, high))
    return html.Div([html.Div(rows, className="mdm-wf-rows"), _table(candidate)], className="mdm-wf")
