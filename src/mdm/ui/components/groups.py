"""The Alike reviews page's views (story 3.3, B.2): the open reviews grouped by signature, largest first, each
group with its pattern in words, its capped count (which opens its reviews in the inbox), its label history,
its blind-review agreement and its batch, and the batches waiting for the steward as their second steward.

A signature group is the open reviews of one entity whose best candidate shows the same pattern of
comparisons under one rule version. A pattern reaches the page as its marks, each a symbol beside its word
("Birth date ≈ similar"), so colour and symbol are never the only cue; the signature itself is never an ID,
an address or a `data-*` value: the page carries the group's safe key (`SIG-…`) only. Figures read "999+" at
the cap, and percentages round down, so a pattern confidently below a threshold never reads it.

Pure: every function takes the dataclasses of `models.workbench` and returns components, so the tests
render them from `tests/workbench_samples.py` without services. No single key acts here (decision 18);
every action is a button, and the page offers no restore of withdrawn bulk decisions anywhere.

Owner: WORKBENCH (plan 5, B.2).
"""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlencode

import dash_mantine_components as dmc
from dash import html
from dash.development.base_component import Component

from mdm import capacity
from mdm.models.authority import ROLE_LABELS
from mdm.models.workbench import AgreementView, BatchLine, GroupList, GroupRow, LabelHistory, Mark
from mdm.ui import ids
from mdm.ui.components import breaker
from mdm.ui.components.common import count_text, empty_state, page_title

TITLE = "Alike reviews"
INTRODUCTION = (
    "Open reviews whose best candidate shows the same pattern of comparisons. Decide a forced sample of "
    "them one by one; when every sample agrees, link the rest together."
)
#: what the page says to a role that decides no tasks
CONSUMER_TEXT = "Your role, {role}, decides no tasks, so no alike reviews wait here for you."
#: why a role that may read tasks cannot draw (a data owner): one line every disabled draw points to
CANNOT_DRAW = "Your role, {role}, cannot decide alike reviews together."
DRAW = "Draw a forced sample"
TOO_SMALL = "Too few to link together: a forced sample takes at least {base}."
NO_GROUPS = "No two open reviews share a pattern yet."
CONFIRM_HEADING = "Waiting for your confirmation"
CONFIRM_LINK = "Check and confirm"
GROUPS_HEADING = "Groups"
CAPTION = "Groups of alike reviews, largest first"
COLUMNS = ("Entity", "Pattern", "Reviews", "Labels", "Blind review", "Batch")
#: each mark's class: the agreement classes of the compare table, so ≠ alone takes a colour
MARK_CLASSES = {"=": "mdm-agree", "≈": "mdm-partial", "≠": "mdm-disagree", "∅": "mdm-missing"}
#: the origins of blind-review agreement, in the order the disclosure lists them, with their words
ORIGIN_WORDS = (("automated", "automatic links"), ("steward", "stewards' decisions"), ("batch", "batches"))


def role_words(role: str) -> str:
    """ "data owner" for `data_owner`."""
    label = ROLE_LABELS.get(role)
    return label.lower() if label else "this role"


def a_person(label: str | None) -> str:
    """How a page names a person, by role only: "you", or "a data steward" for "Data steward"."""
    if not label:
        return "a steward"
    if label == "you":
        return "you"
    words = label.lower()
    return f"{'an' if words[:1] in 'aeiou' else 'a'} {words}"


def number(value: int) -> str:
    """ "1,000"."""
    return f"{int(value):,}"


def marks(found: Sequence[Mark]) -> Component:
    """The pattern in rule order: "Given name = the same · Family name = the same · Birth date ≈ similar ·
    Email ∅ missing". Each mark is a symbol (hidden from screen readers) beside its words."""
    parts: list = []
    for index, mark in enumerate(found):
        if index:
            parts.append(html.Span(" · ", className="mdm-marks-sep"))
        parts.append(
            html.Span(
                [
                    f"{mark.label} ",
                    html.Span(mark.mark, className="mdm-symbol", **{"aria-hidden": "true"}),
                    f" {mark.words}",
                ],
                className=f"mdm-mark {MARK_CLASSES.get(mark.mark, 'mdm-agree')}",
            )
        )
    return html.Span(parts, className="mdm-marks")


def percent(part: int, whole: int) -> int:
    """`part` of `whole`, rounded down (39 of 40 is 97)."""
    return breaker.percent_down(int(part), int(whole))


def group_href(group_key: str) -> str:
    """The inbox listing every open review of a group: "/?group=SIG-…"."""
    return "/?" + urlencode({"group": group_key})


def batch_href(batch_id: str) -> str:
    """A batch's page: "/batch/BAT-…"."""
    return f"/batch/{batch_id}"


def count_link(row: GroupRow) -> Component:
    """The group's capped count as a link to its reviews in the inbox."""
    shown = count_text(row.count)
    return dmc.Anchor(
        shown,
        href=group_href(row.group_key),
        className="mdm-group-count",
        inherit=True,
        **{"aria-label": f"{shown} reviews with this pattern: open them in the inbox"},
    )


def labels_summary(labels: LabelHistory) -> str:
    """ "Linked in 136 of 140 labels (97%)"; "No labels yet"."""
    total = labels.matched + labels.not_matched
    if total <= 0:
        return "No labels yet"
    capped = max(labels.matched, labels.not_matched) >= capacity.COUNT_CAP
    share = "" if capped else f" ({percent(labels.matched, total)}%)"
    return f"Linked in {count_text(labels.matched)} of {count_text(total)} labels{share}"


def labels_cell(labels: LabelHistory) -> Component:
    """The label history: its summary, and behind it both counts and what they are."""
    summary = labels_summary(labels)
    if labels.matched + labels.not_matched <= 0:
        return html.Span(summary, className="mdm-group-none")
    body = (
        "The latest steward label on each pair with this pattern: "
        f"{count_text(labels.matched)} linked, {count_text(labels.not_matched)} Not a match."
    )
    return html.Details(
        [html.Summary(summary, className="mdm-group-summary"), html.P(body)], className="mdm-group-figure"
    )


def agreement_summary(agreement: AgreementView) -> str:
    """ "Blind review agreed 70 of 71 (98%)"; "No blind review yet"."""
    if agreement.reviewed <= 0:
        return "No blind review yet"
    return (
        f"Blind review agreed {number(agreement.agreed)} of {number(agreement.reviewed)} "
        f"({percent(agreement.agreed, agreement.reviewed)}%)"
    )


def by_origin(agreement: AgreementView) -> str:
    """ "Automatic links 60 of 60 · stewards' decisions 8 of 9 · batches 2 of 2": the origins with any
    reviewed sample, in a fixed order."""
    parts: list[str] = []
    for origin, words in ORIGIN_WORDS:
        agreed, reviewed = agreement.by_origin.get(origin, (0, 0))
        if reviewed > 0:
            parts.append(f"{words} {number(agreed)} of {number(reviewed)}")
    text = " · ".join(parts)
    return text[:1].upper() + text[1:]


def agreement_cell(agreement: AgreementView) -> Component:
    """The lifetime blind-review agreement of the pattern's samples, and behind it the same by origin."""
    summary = agreement_summary(agreement)
    if agreement.reviewed <= 0:
        return html.Span(summary, className="mdm-group-none")
    return html.Details(
        [html.Summary(summary, className="mdm-group-summary"), html.P(by_origin(agreement))],
        className="mdm-group-figure",
    )


def batch_link(row: GroupRow) -> Component:
    """The group's open batch with its stage: "Batch BAT-…: forced sample, 3 of 9 decided"."""
    assert row.batch_id is not None
    text = f"Batch {row.batch_id}" + (f": {row.batch_words}" if row.batch_words else "")
    return dmc.Anchor(text, href=batch_href(row.batch_id), className="mdm-group-batch", inherit=True)


def draw_button(row: GroupRow, *, can_draw: bool) -> Component:
    """ "Draw a forced sample", outlined (every row has one; the page has no single primary action), with
    no size: the draw sizes the sample from the reviews still eligible. Disabled for a role that may not
    draw, pointing to the line that says why."""
    extra = {} if can_draw else {"aria-describedby": ids.GROUPS_DRAW_WHY}
    return dmc.Button(
        DRAW,
        id=ids.group_draw(row.group_key, row.entity),
        variant="default",
        size="xs",
        disabled=not can_draw,
        className="mdm-group-draw",
        **extra,
    )


def batch_cell(row: GroupRow, *, can_draw: bool, sample_base: int = 5) -> list[Component]:
    """What the Batch column holds: the withdrawn notice (no draw then), the open batch with its stage,
    "too few" for a group too small, or the draw."""
    parts: list[Component] = []
    if row.withdrawn is not None:
        parts.append(breaker.bulk_notice(row.withdrawn))
    if row.batch_id is not None:
        parts.append(batch_link(row))
    elif row.withdrawn is not None:
        pass  # no draw while bulk decisions are withdrawn, and no restore here
    elif row.too_small:
        parts.append(html.Span(TOO_SMALL.format(base=sample_base), className="mdm-group-none"))
    else:
        parts.append(draw_button(row, can_draw=can_draw))
    return parts


def _cell(column: str, children: object, *, className: str | None = None) -> Component:
    return html.Td(children, className=className, **{"data-column": column})


def group_row(row: GroupRow, *, can_draw: bool, sample_base: int = 5) -> Component:
    """One group's row; it carries its key (`data-group`), never its signature."""
    return html.Tr(
        [
            _cell("Entity", row.entity_label),
            _cell("Pattern", marks(row.marks), className="mdm-group-pattern"),
            _cell("Reviews", count_link(row), className="mdm-num"),
            _cell("Labels", labels_cell(row.labels)),
            _cell("Blind review", agreement_cell(row.agreement)),
            _cell("Batch", batch_cell(row, can_draw=can_draw, sample_base=sample_base)),
        ],
        **{"data-group": row.group_key},
    )


def groups_table(rows: Sequence[GroupRow], *, can_draw: bool, sample_base: int = 5) -> Component:
    """The groups, one row each, with column headers and a caption for screen readers."""
    return html.Table(
        [
            html.Caption(CAPTION, className="mdm-sr-only"),
            html.Thead(html.Tr([html.Th(name, scope="col") for name in COLUMNS])),
            html.Tbody([group_row(row, can_draw=can_draw, sample_base=sample_base) for row in rows]),
        ],
        className="mdm-table mdm-group-table",
    )


def grouped_line(view: GroupList) -> str:
    """What was grouped: "Grouped from the open reviews due soonest, at most 1,000 per entity." (the window is
    the most the page reads of each entity, not a count of what it read) and, when any waits under an earlier
    rule version, "12 reviews scored under an earlier rule version are not grouped."."""
    text = f"Grouped from the open reviews due soonest, at most {number(view.window)} per entity."
    if view.older_rules > 0:
        older = count_text(view.older_rules)
        noun = "review" if view.older_rules == 1 else "reviews"
        text += f" {older} {noun} scored under an earlier rule version {'is' if view.older_rules == 1 else 'are'} not grouped."
    return text


def confirm_line(line: BatchLine) -> Component:
    """ "Batch BAT-… · Person · 566 links, prepared by a data steward", then "Check and confirm"."""
    links = f"{number(line.decisions)} link" + ("" if line.decisions == 1 else "s")
    what = f"undoes {links}" if line.kind == "compensate" else links
    return html.Li(
        [
            f"Batch {line.batch_id} · {line.entity_label} · {what}, prepared by {a_person(line.maker_label)} ",
            dmc.Anchor(
                CONFIRM_LINK, href=batch_href(line.batch_id), className="mdm-confirm-link", inherit=True
            ),
        ],
        className="mdm-confirm-item",
        **{"data-batch": line.batch_id},
    )


def confirm_section(lines: Sequence[BatchLine]) -> Component | None:
    """The batches waiting for the steward as their second steward; nothing when none waits."""
    if not lines:
        return None
    return html.Section(
        [
            html.H2(CONFIRM_HEADING, className="mdm-section-title"),
            html.Ul([confirm_line(line) for line in lines], className="mdm-confirm-list"),
        ],
        className="mdm-groups-section",
        **{"aria-label": CONFIRM_HEADING},
    )


def groups_section(view: GroupList, *, can_draw: bool, role: str, sample_base: int = 5) -> Component:
    """The groups' table and what was grouped; "No two open reviews share a pattern yet." when none."""
    parts: list[Component] = [html.H2(GROUPS_HEADING, className="mdm-section-title")]
    if not can_draw and view.groups:
        parts.append(
            html.P(
                CANNOT_DRAW.format(role=role_words(role)), id=ids.GROUPS_DRAW_WHY, className="mdm-group-why"
            )
        )
    if view.groups:
        parts.append(html.Div(groups_table(view.groups, can_draw=can_draw, sample_base=sample_base)))
    else:
        parts.append(empty_state(NO_GROUPS))
    parts.append(html.P(grouped_line(view), className="mdm-group-note"))
    return html.Section(parts, className="mdm-groups-section", **{"aria-label": GROUPS_HEADING})


def render(view: GroupList, *, can_draw: bool, role: str, sample_base: int = 5) -> list[Component]:
    """The page's regions (GROUPS_LIST's children): the title and introduction, the batches waiting for
    the steward's confirmation, and the groups."""
    parts: list[Component] = [page_title(TITLE), html.P(INTRODUCTION, className="mdm-page-intro")]
    waiting = confirm_section(view.to_confirm)
    if waiting is not None:
        parts.append(waiting)
    parts.append(groups_section(view, can_draw=can_draw, role=role, sample_base=sample_base))
    return parts


def consumer(role: str) -> list[Component]:
    """The page of a role that decides no tasks."""
    return [page_title(TITLE), empty_state(CONSUMER_TEXT.format(role=role_words(role)))]
