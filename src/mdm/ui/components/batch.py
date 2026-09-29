"""The batch page's views (story 3.3, B.3): a batch of alike reviews from its forced sample to its result,
in one column: the header with the pattern and the stage in words, the forced sample and its splits, every
row's change, one footer of actions, the tray's window, the chunks committing, and the result.

A batch links each review left after an agreeing forced sample to the golden record its case suggests, and
nothing else: "Not a match", keep apart and merge are decided one by one. Every row's change is listed, 50 a
page, masked by role, before the batch can be staged, and a second steward sees the same rows (principle
`P5`). The page shows at most one filled button, in its actions footer; Stop, Discard, Send it back and
Undo are outlined. A disabled action carries its reason through `aria-describedby`, as the decide pane's
do, and something not on screen yet (compensation, on the command line) is one plain line, never a dead
button. The page offers no reveal: a row's source key opens its source record, where a reveal is asked and
logged as ever; only an open sample review's key opens its task, in the inbox filtered to the batch.

Pure: every function takes the dataclasses of `models.workbench` and returns components, so the tests
render them from `tests/workbench_samples.py` without services. Titles are masked by the service; the page
adds codes, IDs, source keys, counts and times only.

Owner: WORKBENCH (plan 5, B.3).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from urllib.parse import quote, urlencode

import dash_mantine_components as dmc
from dash import html
from dash.development.base_component import Component

from mdm import capacity
from mdm.models.batch import COMPENSATE_REASONS
from mdm.models.canonical import utcnow
from mdm.models.workbench import (
    Action,
    BatchRow,
    BatchRowPage,
    BatchSummary,
    BatchView,
    CompensationLine,
    Preview,
    SampleReview,
    SplitLine,
)
from mdm.services import display
from mdm.ui import ids, messages
from mdm.ui.components import breaker, impact
from mdm.ui.components.common import duration, notice, page_title
from mdm.ui.components.groups import a_person, marks, number
from mdm.ui.components.provenance import source_href

#: the stage of a batch in words, per status (a ready batch reads by whether every change is shown yet)
STATE_WORDS: Mapping[str, str] = {
    "sampling": "Forced sample",
    "awaiting_checker": "Waits for a second steward",
    "staged": "In the tray",
    "committing": "Committing",
    "committed": "Committed",
    "stopped": "Stopped",
    "discarded": "Discarded",
    "failed": "Nothing committed",
}
#: the statuses of a batch that has ended
FINISHED = ("committed", "stopped", "discarded", "failed")
#: the statuses of a batch that moves on its own, whose page refreshes every BATCH_POLL_SECONDS
MOVING = ("staged", "committing")
#: the actions that move a batch on: at most one of them, enabled, is filled
PRIMARY = ("decide_sample", "prepare", "stage", "confirm")
#: the `on_label` of a split that took every alike review ("Every alike review in this batch")
EVERY = "every alike review"
#: what the forced sample section says about how a sample works
SAMPLE_NOTE = (
    "Each review is decided on its own, with its full case. If one disagrees, the steward who decides it "
    "names the comparison that misled, and the reviews that share the record's value on it leave the batch. "
    "Values stay hidden: the hub compares them and, when they are personal, logs each record that leaves."
)
BEFORE_PREPARED = "Nothing is linked until you have seen every row's change."
BEFORE_PREPARED_UNDO = "Nothing is undone until you have seen every row's change."
CHANGED_SINCE = "The golden record changed since; the commit checks it again."
STOP_LINE = (
    "Stop takes effect before the next chunk. A chunk already committing finishes, committed chunks stay, "
    "and the rest go back to the queue."
)
STOPPING = "Stopping before the next chunk."
ROWS_CAPTION = "Every row's change"
#: why a review failed alone at its chunk, after "not linked:" (any other code reads as the display service
#: words it, `display.item_reason_words`)
FAILED_WORDS: Mapping[str, str] = {
    "record_changed": "the record changed",
    "task_closed": "its task closed",
    "closed": "its task closed",
    "target_changed": "the golden record changed",
    "blocked": "a cannot-link rule now keeps it apart",
    "linked": "the record is linked already",
}
#: why a batch stopped early, after "Stopped after chunk 1 of 3", per outcome
STOPPED_WHY: Mapping[str, str] = {
    "bulk_withdrawn": "the quality breaker withdrew bulk decisions for this pattern",
    "chunk_failed": "the next chunk failed three times",
}


# ---------------------------------------------------------------------------------------------- small words


def plural(count: int, one: str, many: str | None = None) -> str:
    """ "1 chunk", "3 chunks", "1,000 reviews"."""
    return f"{number(count)} {one if count == 1 else (many or one + 's')}"


def reason_words(code: str | None) -> str:
    """An item's reason code in words, as the display service words it after a count ("claimed by another
    steward", "now a close call", "split off on birth date")."""
    return display.item_reason_words(code) or "for another reason"


def _sentence_clause(text: str) -> str:
    """A sentence as a clause after a colon: its first letter lowered, its full stop dropped."""
    text = text.rstrip(".")
    return text[:1].lower() + text[1:] if text[1:2].islower() else text


def clock(moment: datetime) -> str:
    """ "12:04:31 UTC"."""
    return f"{moment:%H:%M:%S} UTC"


def day(moment: datetime) -> str:
    """ "28 October 2026"."""
    return f"{moment.day} {moment:%B %Y}"


def window_words(seconds: int) -> str:
    """The undo window as the inbox says it: "60 s", or "10 min" from two minutes on."""
    return f"{seconds} s" if seconds < 120 else duration(timedelta(seconds=seconds))


def compensate_command(batch_id: str) -> str:
    """The command that undoes a batch's committed links, with every reason it takes."""
    return f"mdm batch compensate {batch_id} --reason {'|'.join(COMPENSATE_REASONS)}"


def inbox_href(batch_id: str, task_id: str | None = None) -> str:
    """The inbox filtered to a batch's forced sample, with one of its tasks selected."""
    query = {"batch": batch_id}
    if task_id:
        query["task"] = task_id
    return "/?" + urlencode(query)


def batch_href(batch_id: str) -> str:
    return f"/batch/{batch_id}"


def record_href(master_id: str) -> str:
    return f"/record/{quote(master_id, safe='')}"


def links_ever(view: BatchView) -> int:
    """The links a batch committed, those a compensation undid since included."""
    return view.counts.get("committed", 0) + view.counts.get("compensated", 0)


def prepared(view: BatchView) -> bool:
    """Whether every row's change has been shown (the batch's summary is known)."""
    return view.summary is not None


def state_words(view: BatchView) -> str:
    """The stage in words: "Forced sample", "Sample agreed", "Every change shown", "Waits for a second
    steward", "In the tray", "Committing", "Committed", "Stopped", "Nothing committed", "Discarded"."""
    if view.status == "ready":
        return "Every change shown" if prepared(view) else "Sample agreed"
    return STATE_WORDS.get(view.status, view.status.replace("_", " ").capitalize())


# ---------------------------------------------------------------------------------------------- the header


def title(view: BatchView) -> str:
    """ "Alike Person reviews"; "Undo of batch BAT-…" for a compensation."""
    if view.compensates:
        return f"Undo of batch {view.compensates}"
    return f"Alike {view.entity_label} reviews"


def facts(view: BatchView) -> list:
    """ "BAT-… · prepared by you · 612 reviews · forced sample of 9", then "confirmed by a coordinating
    steward" once confirmed. People are named by role only, or "you"."""
    parts: list = [
        html.Span(view.batch_id, className="mdm-record-id"),
        f" · prepared by {a_person(view.maker_label)}",
    ]
    if view.compensates:
        parts.extend(
            [" · undoes ", dmc.Anchor(view.compensates, href=batch_href(view.compensates), inherit=True)]
        )
        parts.append(f" · {plural(view.population, 'link')}")
    else:
        parts.append(f" · {plural(view.population, 'review')} · forced sample of {number(view.sample_size)}")
    if view.checker_label:
        parts.append(f" · confirmed by {a_person(view.checker_label)}")
    return parts


def header(view: BatchView) -> Component:
    """The title, the facts line, the pattern's marks, the stage in words and, while the pattern's bulk
    decisions are withdrawn, the breaker's notice (with no restore control)."""
    parts: list[Component] = [
        page_title(title(view)),
        html.P(facts(view), className="mdm-record-facts mdm-batch-facts"),
    ]
    if view.marks:
        parts.append(html.P(marks(view.marks), className="mdm-batch-marks"))
    parts.append(html.P(state_words(view), className="mdm-batch-state"))
    if view.withdrawn is not None:
        parts.append(breaker.bulk_notice(view.withdrawn))
    if view.notice:
        parts.append(notice("info", view.notice))
    return html.Div(parts, className="mdm-batch-header")


# ---------------------------------------------------------------------------------------------- the forced sample


def sample_figures(view: BatchView) -> str:
    """ "4 of 9 decided · 4 agreed · none disagreed"."""
    disagreed = "none disagreed" if view.disagreed == 0 else f"{number(view.disagreed)} disagreed"
    return (
        f"{number(view.decided)} of {number(view.sample_size)} decided · {number(view.agreed)} agreed · "
        f"{disagreed}"
    )


def meter(value: float, name: str, text: str) -> Component:
    """A bar with an accessible name, and beside it the same figure as text (no live region: the page
    polls, and outcomes are announced by notifications)."""
    return html.Div(
        [
            dmc.Progress(
                value=max(0.0, min(100.0, value)),
                size="md",
                className="mdm-meter-bar",
                **{"aria-label": name},
            ),
            html.Span(text, className="mdm-progress-text"),
        ],
        className="mdm-meter",
    )


def sample_href(view: BatchView, review: SampleReview) -> str:
    """Where a sample review's source key leads: an open review's task, in the inbox filtered to the batch;
    a decided or void one's source record, since its task is closed."""
    if review.open:
        return inbox_href(view.batch_id, review.task_id)
    return source_href(review.source) or "/"


def sample_item(view: BatchView, review: SampleReview) -> Component:
    """ "crm and hr · crm:C001377 · K*** B*** · Waiting"."""
    return html.Li(
        [
            html.Span(review.stratum_label, className="mdm-sample-stratum"),
            " · ",
            dmc.Anchor(
                review.source, href=sample_href(view, review), className="mdm-sample-source", inherit=True
            ),
            " · ",
            html.Span(review.title, className="mdm-sample-title"),
            " · ",
            html.Span(review.words, className=f"mdm-sample-outcome mdm-sample-{review.status}"),
        ],
        className="mdm-sample-item",
        **{"data-task-id": review.task_id},
    )


def still_in(view: BatchView) -> int:
    """n′, the reviews still in the batch while its sample is decided: the population drawn, less the reviews
    its applied splits took, the void sample reviews, which the top-up replaced, and the reviews that left
    because their task closed or their record moved outside the batch."""
    split = sum(line.count for line in view.splits if line.count is not None)
    void = sum(1 for review in view.sample if review.status == "void")
    return max(view.population - split - void - view.counts.get("excluded", 0), 0)


def split_text(view: BatchView, line: SplitLine, *, last: bool) -> str:
    """One disagreeing review and its split: "crm:C001409 was decided Not a match, flagged on birth date:
    38 reviews left the batch, to be decided one by one. The sample now needs 8 of 574."; while it waits,
    "…: its split applies in a moment."; for every alike review, the batch's end."""
    lead = f"{line.source} was decided {line.decision_words}"
    if line.on_label == EVERY:
        return (
            f"{lead}, flagged on every alike review: every alike review left the batch after a "
            "disagreement. Decide them one by one in the inbox."
        )
    lead += f", flagged on {line.on_label}"
    if line.count is None:
        return f"{lead}: its split applies in a moment."
    moved = "1 review left the batch" if line.count == 1 else f"{number(line.count)} reviews left the batch"
    text = f"{lead}: {moved}, to be decided one by one."
    if last and view.status == "sampling":
        text += f" The sample now needs {number(view.sample_size)} of {number(still_in(view))}."
    return text


def splits(view: BatchView) -> Component | None:
    """The splits, one line per disagreeing review; no form and no button: the steward who decided it named
    the comparison in the decide pane."""
    if not view.splits:
        return None
    applied = [index for index, line in enumerate(view.splits) if line.count is not None]
    last = applied[-1] if applied else None
    return html.Ul(
        [
            html.Li(split_text(view, line, last=index == last), className="mdm-split-line")
            for index, line in enumerate(view.splits)
        ],
        className="mdm-split-list",
        **{"aria-label": "Splits"},
    )


def sample_section(view: BatchView) -> Component | None:
    """The forced sample: its meter, its reviews and their outcomes, its splits and how it works; open while
    it is decided, collapsed once the batch is ready. None for a compensation, which has no sample."""
    if view.kind != "link" or (not view.sample and view.status != "sampling"):
        return None
    figures = sample_figures(view)
    share = 100.0 * view.decided / view.sample_size if view.sample_size else 0.0
    body: list[Component] = [
        meter(share, "Forced sample decided", figures),
        html.Ul([sample_item(view, review) for review in view.sample], className="mdm-sample-list"),
    ]
    found = splits(view)
    if found is not None:
        body.append(found)
    body.append(html.P(SAMPLE_NOTE, className="mdm-batch-note"))
    heading = html.H2("Forced sample", className="mdm-section-title")
    if view.status == "sampling":
        content: list[Component] = [heading, *body]
    else:
        content = [heading, html.Details([html.Summary(figures, className="mdm-sample-summary"), *body])]
    return html.Section(content, className="mdm-batch-section", **{"aria-label": "Forced sample"})


# ---------------------------------------------------------------------------------------------- every change


def summary_line(view: BatchView, summary: BatchSummary, *, chunk_rows: int | None = None) -> str:
    """ "566 cross-references · 566 golden records updated · 3 chunks of at most 500 published rows · 12 to
    blind review"; a compensation's "566 cross-references ended · 566 golden records recomputed · 3
    chunks"."""
    rows = chunk_rows if chunk_rows is not None else capacity.COMMIT_CHUNK_ROWS
    if view.kind == "compensate":
        return " · ".join(
            [
                f"{plural(summary.xrefs, 'cross-reference')} ended",
                f"{plural(summary.golden, 'golden record')} recomputed",
                plural(summary.chunks, "chunk"),
            ]
        )
    parts = [
        plural(summary.xrefs, "cross-reference"),
        f"{plural(summary.golden, 'golden record')} updated",
        f"{plural(summary.chunks, 'chunk')} of at most {number(rows)} published rows",
    ]
    if summary.reviews > 0:
        parts.append(f"{number(summary.reviews)} to blind review")
    return " · ".join(parts)


def left_out_line(summary: BatchSummary) -> str | None:
    """ "Left out: 3 claimed by another steward, 1 now a close call. They stay in the inbox."; None when
    nothing was left out."""
    found = [(count, code) for code, count in summary.left_out.items() if count > 0]
    if not found:
        return None
    found.sort(key=lambda item: (-item[0], item[1]))
    words = ", ".join(f"{number(count)} {reason_words(code)}" for count, code in found)
    total = sum(count for count, _ in found)
    return f"Left out: {words}. {'It stays' if total == 1 else 'They stay'} in the inbox."


def row_state(view: BatchView, row: BatchRow) -> str | None:
    """A row's state once the batch commits or has ended: "committed in chunk 2", "not linked: the record
    changed", "back in the queue"; None before."""
    undoing = view.kind == "compensate"
    if row.status == "committed":
        verb = "undone" if undoing else "committed"
        return f"{verb} in chunk {row.chunk_no}" if row.chunk_no is not None else verb
    if row.status == "failed":
        why = FAILED_WORDS.get(row.reason or "") or reason_words(row.reason)
        return f"not linked: {why}; back in the queue"
    if row.status == "released":
        return "back in the queue"
    if row.status == "kept":
        return "kept: it changed after the batch"
    if row.status == "compensated":
        return "undone by a later batch"
    if row.status == "excluded":
        return f"left out: {reason_words(row.reason)}"
    return None


def impact_text(row: BatchRow) -> str:
    """ "+1 cross-reference · golden phone changes · no ID retired", and "with 2 other rows of this batch"
    when rows share the target."""
    text = row.impact.sentence()
    if row.joins > 0:
        others = "1 other row" if row.joins == 1 else f"{number(row.joins)} other rows"
        text += f" · with {others} of this batch"
    return text


def row_view(view: BatchView, row: BatchRow) -> Component:
    """One row: the record (its source record's link and its masked title), the golden record (its link and
    masked title), and what changes, with the masked before-and-after table behind a disclosure."""
    record = [
        dmc.Anchor(
            row.source, href=source_href(row.source) or "/", className="mdm-batch-source", inherit=True
        ),
        html.Span(row.title, className="mdm-batch-title"),
    ]
    golden: list = ["—"]
    if row.target:
        golden = [
            dmc.Anchor(row.target, href=record_href(row.target), className="mdm-batch-target", inherit=True)
        ]
        if row.target_title:
            golden.append(html.Span(row.target_title, className="mdm-batch-title"))
    changes: list[Component] = [html.P(impact_text(row), className="mdm-batch-impact")]
    preview = Preview(master_id=row.target, rows=row.preview, impact=row.impact)
    if row.preview:
        changes.append(impact.changes(preview, verb="undo" if view.kind == "compensate" else "link"))
    if row.changed_since:
        changes.append(html.P(CHANGED_SINCE, className="mdm-row-changed"))
    state = (
        row_state(view, row)
        if (view.status in ("committing", *FINISHED) or row.status == "excluded")
        else None
    )
    if state:
        changes.append(html.P(state, className=f"mdm-row-state mdm-row-{row.status}"))
    return html.Tr(
        [
            html.Td(record, **{"data-column": "Record"}),
            html.Td(golden, **{"data-column": "Golden record"}),
            html.Td(changes, **{"data-column": "What changes"}),
        ],
        **{"data-task-id": row.task_id},
    )


def rows_table(view: BatchView, page: BatchRowPage) -> Component:
    """One page of rows, with column headers and a caption for screen readers."""
    return html.Table(
        [
            html.Caption(ROWS_CAPTION, className="mdm-sr-only"),
            html.Thead(
                html.Tr([html.Th(name, scope="col") for name in ("Record", "Golden record", "What changes")])
            ),
            html.Tbody([row_view(view, row) for row in page.rows]),
        ],
        className="mdm-table mdm-batch-rows",
    )


def rows_label(depth: int, count: int, total: int) -> str:
    """ "Rows 51–100 of 566" (depth: the pages before this one); "No rows" for an empty page."""
    if count <= 0:
        return "No rows"
    start = depth * capacity.BATCH_PAGE + 1
    return f"Rows {number(start)}–{number(start + count - 1)} of {number(total)}"


def rows_region(view: BatchView, page: BatchRowPage | None, depth: int = 0) -> Component:
    """The rows and their paging (BATCH_ROWS, BATCH_ROWS_LABEL, Previous 50 and Next 50, keyed by position),
    always present so the paging callback finds them; hidden until every change has been prepared. A
    paging button with no page on its side is hidden, as the inbox's is."""
    rows = page.rows if page is not None else ()
    total = view.summary.xrefs if view.summary is not None else 0
    shown = prepared(view)
    return html.Div(
        [
            html.P(rows_label(depth, len(rows), total), id=ids.BATCH_ROWS_LABEL, className="mdm-rows-label"),
            html.Div(rows_table(view, page) if rows and page is not None else None, id=ids.BATCH_ROWS),
            dmc.Group(
                [
                    dmc.Button(
                        "Previous 50",
                        id=ids.BATCH_ROWS_PREV,
                        variant="default",
                        size="xs",
                        disabled=depth <= 0,
                    ),
                    dmc.Button(
                        "Next 50",
                        id=ids.BATCH_ROWS_NEXT,
                        variant="default",
                        size="xs",
                        disabled=page is None or page.after is None,
                    ),
                ],
                gap="xs",
                mt="xs",
                className="mdm-paging",
            ),
        ],
        className="mdm-batch-rows-region" if shown else "mdm-batch-rows-region mdm-hidden",
    )


def changes_section(
    view: BatchView, page: BatchRowPage | None = None, depth: int = 0, *, chunk_rows: int | None = None
) -> Component | None:
    """Every change: before preparation, that nothing is linked until every row is seen; after it, the
    summary line, what was left out, and the rows. None while the forced sample is decided."""
    if view.status == "sampling":
        return None
    parts: list[Component] = [html.H2("Every change", className="mdm-section-title")]
    if view.summary is None:
        text = BEFORE_PREPARED_UNDO if view.kind == "compensate" else BEFORE_PREPARED
        parts.append(html.P(text, className="mdm-batch-note"))
    else:
        parts.append(
            html.P(summary_line(view, view.summary, chunk_rows=chunk_rows), className="mdm-batch-summary")
        )
        left = left_out_line(view.summary)
        if left:
            parts.append(html.P(left, className="mdm-batch-note"))
        if view.kind == "compensate" and view.status == "ready":
            # undoing a batch is on the command line until the audit screen: one line, never a dead button
            parts.append(
                html.P(
                    [
                        "Once every row is checked, stage it on the command line: ",
                        html.Code(f"mdm batch stage {view.batch_id}"),
                    ],
                    className="mdm-batch-note",
                )
            )
    parts.append(rows_region(view, page, depth))
    return html.Section(parts, className="mdm-batch-section", **{"aria-label": "Every change"})


# ---------------------------------------------------------------------------------------------- the actions


def _link_button(action: Action, view: BatchView, *, filled: bool) -> Component:
    """ "Decide the sample in the inbox": a link to the filtered inbox, filled while a review is open,
    outlined with its reason beside it while none is."""
    extra = {} if action.enabled else {"aria-describedby": ids.BATCH_ACTION_REASONS}
    return html.A(
        action.label,
        href=inbox_href(view.batch_id),
        className="mdm-link-button mdm-link-button-filled" if filled else "mdm-link-button",
        **extra,
    )


def action_button(action: Action, *, filled: bool) -> Component:
    """One action's button (BATCH_ACTION pattern ID), filled only for the page's one primary action,
    disabled with its reason when not enabled."""
    extra = {} if action.enabled else {"aria-describedby": ids.BATCH_ACTION_REASONS}
    return dmc.Button(
        action.label,
        id=ids.batch_action(action.decision),
        variant="filled" if filled else "default",
        disabled=not action.enabled,
        size="sm",
        className=f"mdm-batch-button mdm-batch-{action.decision}",
        **extra,
    )


def action_lines(
    view: BatchView, *, undo_seconds: int = 60, checker_above: int = 250, chunk_rows: int | None = None
) -> list[str]:
    """What the footer says under its buttons: how staging goes on, what the second steward does, and
    what Stop does."""
    codes = {a.decision: a for a in view.actions}
    rows = chunk_rows if chunk_rows is not None else capacity.COMMIT_CHUNK_ROWS
    lines: list[str] = []
    stage = codes.get("stage")
    if stage is not None and view.summary is not None:
        if view.summary.xrefs > checker_above:
            lines.append(
                f"Above {number(checker_above)} links, a second steward checks every row and confirms before "
                "it enters the tray."
            )
        else:
            lines.append(
                f"It waits in the tray for {window_words(undo_seconds)}, then commits in chunks of at most "
                f"{number(rows)} published rows."
            )
    confirm = codes.get("confirm")
    if confirm is not None and confirm.enabled:
        lines.append(
            f"Prepared by {a_person(view.maker_label)}. Check every row; once you confirm, it waits in the "
            f"tray for {window_words(undo_seconds)}, where either of you can still undo it."
        )
    if "stop" in codes:
        stopping = view.progress is not None and view.progress.stop_requested
        lines.append(STOPPING if stopping else STOP_LINE)
    return lines


def reasons(actions: Sequence[Action]) -> list[str]:
    """Each distinct reason an action is unavailable, in the order the actions come."""
    return list(dict.fromkeys(a.why_not for a in actions if not a.enabled and a.why_not))


def actions_footer(
    view: BatchView, *, undo_seconds: int = 60, checker_above: int = 250, chunk_rows: int | None = None
) -> Component | None:
    """The page's one footer of actions (BatchView.actions, codes of PAGE_ACTIONS): the first enabled
    primary action filled, every other outlined (Stop, Discard, Send it back and Undo never filled), and
    under them the lines that say what happens next and why any is unavailable. None once finished."""
    if not view.actions:
        return None
    buttons: list[Component] = []
    filled_used = False
    for action in view.actions:
        filled = action.decision in PRIMARY and action.enabled and not filled_used
        filled_used = filled_used or filled
        if action.decision == "decide_sample":
            buttons.append(_link_button(action, view, filled=filled))
        else:
            buttons.append(action_button(action, filled=filled))
    parts: list[Component] = [
        html.H2("Actions", className="mdm-sr-only"),
        html.Div(buttons, className="mdm-batch-buttons"),
    ]
    parts.extend(
        html.P(line, className="mdm-batch-note")
        for line in action_lines(
            view, undo_seconds=undo_seconds, checker_above=checker_above, chunk_rows=chunk_rows
        )
    )
    parts.append(
        html.Div(
            [html.P(text, className="mdm-action-reason") for text in reasons(view.actions)],
            id=ids.BATCH_ACTION_REASONS,
            className="mdm-action-reasons",
        )
    )
    return html.Section(parts, className="mdm-batch-actions", **{"aria-label": "Actions"})


# ---------------------------------------------------------------------------------------------- the tray, the chunks


def tray_section(view: BatchView) -> Component | None:
    """While staged: "In the tray until 12:04:31 UTC. It then commits in 3 chunks, one at a time." No
    ticking countdown here: the tray's button counts down, in the maker's tray and the second steward's."""
    if view.status != "staged":
        return None
    chunks = view.summary.chunks if view.summary is not None else 0
    until = f"In the tray until {clock(view.deadline)}." if view.deadline is not None else "In the tray."
    then = f" It then commits in {plural(chunks, 'chunk')}, one at a time." if chunks else ""
    return html.Section(
        [html.H2("In the tray", className="mdm-section-title"), html.P(until + then)],
        className="mdm-batch-section",
        **{"aria-label": "In the tray"},
    )


def committing_section(view: BatchView, now: datetime | None = None) -> Component | None:
    """While committing: a bar of the chunks committed, "Chunk 2 of 3 committed · 1,000 of 1,132 published
    rows", and with the throttle, when the next chunk may commit."""
    if view.status != "committing" or view.progress is None:
        return None
    now = now if now is not None else utcnow()
    progress = view.progress
    text = f"Chunk {number(progress.chunks_committed)} of {number(progress.chunks)} committed"
    if view.summary is not None:
        text += f" · {number(progress.rows_committed)} of {number(view.summary.rows)} published rows"
    share = 100.0 * progress.chunks_committed / progress.chunks if progress.chunks else 0.0
    parts: list[Component] = [
        html.H2("Committing", className="mdm-section-title"),
        meter(share, "Chunks committed", text),
    ]
    if progress.not_before is not None and progress.not_before > now and not progress.stop_requested:
        parts.append(
            html.P(
                f"The next chunk commits at {progress.not_before:%H:%M} UTC, at the agreed rate.",
                className="mdm-batch-note",
            )
        )
    return html.Section(parts, className="mdm-batch-section", **{"aria-label": "Committing"})


# ---------------------------------------------------------------------------------------------- the result


def _chunks_of(view: BatchView) -> tuple[int, int]:
    if view.progress is not None:
        return view.progress.chunks_committed, view.progress.chunks
    chunks = view.summary.chunks if view.summary is not None else 0
    return chunks, chunks


def _commits(view: BatchView) -> str:
    if view.commits is None:
        return ""
    first, last = view.commits
    return f", commit {first}" if first == last else f", commits {first} to {last}"


def _stopped_after(view: BatchView, done: int, planned: int) -> str:
    """ "Stopped after chunk 2 of 3", or "Stopped by a data steward after chunk 2 of 3" when a person asked
    (`stopped_by`: "you", or a role)."""
    by = f" by {a_person(view.stopped_by)}" if view.stopped_by else ""
    return f"Stopped{by} after chunk {done} of {planned}"


def _blind(view: BatchView) -> str:
    """ " 12 went to blind review.": the samples its chunks wrote, not those planned; nothing when none."""
    count = view.blind_reviews
    if count <= 0:
        return ""
    return f" {number(count)} went to blind review."


def outcome_lines(view: BatchView) -> list[str]:
    """How the batch ended, from its outcome and its counts: who stopped it, when a person did, and how many
    of its links went to blind review."""
    counts = view.counts
    done, planned = _chunks_of(view)
    back = counts.get("released", 0)
    if view.kind == "compensate" and view.status in ("committed", "stopped"):
        undone = counts.get("committed", 0)
        kept = counts.get("kept", 0)
        total = undone + kept + counts.get("failed", 0) + back
        lead = f"{_stopped_after(view, done, planned)}. " if view.status == "stopped" else ""
        text = f"{lead}Undid {number(undone)} of {plural(total, 'link')}."
        if kept:
            text += f" {number(kept)} {'was' if kept == 1 else 'were'} changed after the batch, so {'it is' if kept == 1 else 'they are'} kept."
        return [text]
    if view.status == "committed":
        text = f"Committed in {plural(done, 'chunk')}: {plural(links_ever(view), 'link')}{_commits(view)}."
        return [text + _blind(view)]
    if view.status == "stopped":
        why = STOPPED_WHY.get(view.outcome or "")
        if why is None and view.outcome and view.outcome != "stopped":
            why = _sentence_clause(messages.sentence_for(view.outcome))
        lead = _stopped_after(view, done, planned) + (f": {why}." if why else ":")
        queue = f"{plural(back, 'review')} {'is' if back == 1 else 'are'} back in the queue."
        return [f"{lead} {plural(links_ever(view), 'link')} committed; {queue}{_blind(view)}"]
    if view.status == "failed":
        sentence = messages.sentence_for(view.outcome or "internal")
        return [f"Nothing was linked: {_sentence_clause(sentence)}. Every review is back in the queue."]
    if view.status == "discarded":
        if view.outcome == "split_all":
            return [
                "Every alike review left the batch after a disagreement. Decide them one by one in the inbox."
            ]
        if view.outcome == "too_few_left":
            return [messages.sentence_for("too_few_left")]
        return ["Discarded. Its reviews stay in the inbox."]
    return []


def failed_alone_line(view: BatchView) -> str | None:
    """ "4 reviews were not linked because their record, task or golden record moved, or a cannot-link rule
    now keeps them apart; they are back in the queue."."""
    failed = view.counts.get("failed", 0)
    if failed <= 0 or view.kind != "link":
        return None
    if failed == 1:
        return (
            "1 review was not linked because its record, task or golden record moved, or a cannot-link rule "
            "now keeps it apart; it is back in the queue."
        )
    return (
        f"{number(failed)} reviews were not linked because their record, task or golden record moved, or a "
        "cannot-link rule now keeps them apart; they are back in the queue."
    )


#: a compensation's statuses once it has ended without undoing everything it planned
ENDED_EARLY = ("stopped", "failed")


def compensation_line(view: BatchView, line: CompensationLine) -> Component:
    """One compensation of the batch, credited with the links it undid itself (`line.undone`), in words by its
    own status: "Undone by batch BAT-…: 563 of its 566 links."; "Being undone by batch BAT-…." (with "250 of
    its 566 links so far" once a chunk is undone); "250 of its 566 links were undone by batch BAT-…, which
    stopped."."""
    links = plural(links_ever(view), "link")
    anchor = dmc.Anchor(line.batch_id, href=batch_href(line.batch_id), inherit=True)
    if line.status == "committed":
        text: list = ["Undone by batch ", anchor, f": {number(line.undone)} of its {links}."]
    elif line.status in ENDED_EARLY:
        count = "None" if line.undone <= 0 else number(line.undone)
        verb = "was" if line.undone == 1 else "were"
        text = [f"{count} of its {links} {verb} undone by batch ", anchor, ", which stopped."]
    elif line.undone > 0:
        text = ["Being undone by batch ", anchor, f": {number(line.undone)} of its {links} so far."]
    else:
        text = ["Being undone by batch ", anchor, "."]
    return html.P(text, className="mdm-batch-note")


def compensation_lines(view: BatchView, now: datetime) -> list[Component]:
    """What undoing the batch's links looks like: each compensation, oldest first, with the links it undid
    itself (being undone, undone, or undone in part by one that stopped); or, while none is open or done,
    that its links can still be undone until a date, with the full command (no button: undoing a batch is on
    the command line until the audit screen)."""
    if view.kind != "link" or view.status not in ("committed", "stopped"):
        return []
    lines: list[Component] = [compensation_line(view, line) for line in view.compensations]
    if view.compensated_by or any(line.status not in ENDED_EARLY for line in view.compensations):
        return lines  # one is open, or has undone the batch: no other may start
    left = view.counts.get("committed", 0)
    if left > 0 and view.undo_until is not None and now <= view.undo_until:
        lead = (
            f"The other {number(left)} can be undone until {day(view.undo_until)}: "
            if view.compensations
            else f"Its committed links can be undone until {day(view.undo_until)}, on the command line: "
        )
        lines.append(html.P([lead, html.Code(compensate_command(view.batch_id))], className="mdm-batch-note"))
    return lines


def result_section(view: BatchView, now: datetime | None = None) -> Component | None:
    """How the batch ended: what committed, what went back, what failed alone, and how its links can still
    be undone. None before it ends."""
    if view.status not in FINISHED:
        return None
    now = now if now is not None else utcnow()
    parts: list[Component] = [html.H2("Result", className="mdm-section-title")]
    parts.extend(html.P(line, className="mdm-batch-result") for line in outcome_lines(view))
    failed = failed_alone_line(view)
    if failed:
        parts.append(html.P(failed, className="mdm-batch-note"))
    parts.extend(compensation_lines(view, now))
    return html.Section(parts, className="mdm-batch-section", **{"aria-label": "Result"})


# ---------------------------------------------------------------------------------------------- the page


def render(
    view: BatchView,
    page: BatchRowPage | None = None,
    depth: int = 0,
    *,
    undo_seconds: int = 60,
    checker_above: int = 250,
    chunk_rows: int | None = None,
    now: datetime | None = None,
) -> list[Component]:
    """The batch page's regions (BATCH_VIEW's children), top to bottom: the header, the forced sample,
    every change with one page of rows, the actions, the tray's window, the chunks committing and the
    result; each section opens with its own `h2`."""
    now = now if now is not None else utcnow()
    sections = [
        header(view),
        sample_section(view),
        changes_section(view, page, depth, chunk_rows=chunk_rows),
        actions_footer(view, undo_seconds=undo_seconds, checker_above=checker_above, chunk_rows=chunk_rows),
        tray_section(view),
        committing_section(view, now),
        result_section(view, now),
    ]
    return [section for section in sections if section is not None]


def stamp_of(view: BatchView) -> dict:
    """BATCH_STAMP: what a redraw follows, codes and counts only (its status, its sample's outcomes, its
    splits, its preparation, its chunks and its stop); the page is drawn again only when it changes."""
    return {
        "batch_id": view.batch_id,
        "status": view.status,
        "outcome": view.outcome,
        "sample": [[review.task_id, review.status] for review in view.sample],
        "splits": [[line.task_id, line.count] for line in view.splits],
        "prepared": prepared(view),
        "chunks": view.progress.chunks_committed if view.progress is not None else None,
        "stop": bool(view.progress is not None and view.progress.stop_requested),
        "checker": view.checker_label,
        "withdrawn": view.withdrawn.key if view.withdrawn is not None else None,
        "compensated_by": view.compensated_by,
        "compensations": [[line.batch_id, line.status, line.undone] for line in view.compensations],
    }


def poll_of(view: BatchView) -> tuple[bool, int]:
    """BATCH_POLL's (disabled, interval in ms): every BATCH_POLL_SECONDS while the batch moves on its own
    (staged or committing), every COUNTS_REFRESH_SECONDS while it is open otherwise, and off once it has
    ended."""
    if view.status in FINISHED:
        return True, capacity.COUNTS_REFRESH_SECONDS * 1000
    if view.status in MOVING:
        return False, capacity.BATCH_POLL_SECONDS * 1000
    return False, capacity.COUNTS_REFRESH_SECONDS * 1000
