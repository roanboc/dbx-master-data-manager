"""The batch page without a browser (story 3.3, plan 5 B.3 and B.8): every state's regions and actions, at
most one filled button on the whole page, the forced sample and its splits, every row's change paged by
position, the tray's window, the chunks committing, the result sentences and the compensation line, the
stamp that decides a redraw and the poll's pace.

The views are rendered from the invented samples of `tests/workbench_samples.py`. The page's callbacks (B1 the
redraw, B2 an action, B3 the rows' paging) run last, on a world of alike reviews on an in-memory DuckDB, as
pure functions and posted through Dash as the browser would; nothing here starts a server or a browser.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from dash import Dash, no_update
from dash.development.base_component import Component

from mdm import capacity
from mdm.backend.factory import open_store
from mdm.models.batch import PAGE_ACTIONS
from mdm.models.workbench import BatchView
from mdm.services.context import Hub
from mdm.ui import app as app_module
from mdm.ui import context, ids
from mdm.ui.components import batch
from mdm.ui.pages import batch as batch_page
from tests import helpers
from tests import workbench_samples as samples
from tests.conftest import base_settings, open_hub

NOW = samples.NOW
BATCH_ID = samples.BATCH_ID

# ---------------------------------------------------------------------------------------------- helpers


def walk(tree: Any) -> Iterator[Component]:
    """Every component in a tree, through `children` and every other prop that holds components."""
    if isinstance(tree, (list, tuple)):
        for item in tree:
            yield from walk(item)
        return
    if not isinstance(tree, Component):
        return
    yield tree
    for name in tree._prop_names:
        value = getattr(tree, name, None)
        if isinstance(value, (Component, list, tuple)):
            yield from walk(value)


def text_of(tree: Any) -> str:
    """The text of a tree: every string among its children, in order."""
    if isinstance(tree, str):
        return tree
    if isinstance(tree, (list, tuple)):
        return "".join(text_of(item) for item in tree)
    if isinstance(tree, Component):
        return text_of(getattr(tree, "children", None) or "")
    return ""


def of_type(tree: Any, name: str) -> list[Component]:
    return [c for c in walk(tree) if type(c).__name__ == name]


def prop(component: Component, name: str) -> Any:
    return component.to_plotly_json()["props"].get(name)


def classes(component: Component) -> set[str]:
    return set((getattr(component, "className", None) or "").split())


def page(view: BatchView, rows: Any = None, depth: int = 0, **kwargs: Any) -> list[Component]:
    return batch.render(view, rows, depth, now=NOW, **kwargs)


def section(tree: Any, name: str) -> Component | None:
    found = [c for c in of_type(tree, "Section") if prop(c, "aria-label") == name]
    assert len(found) <= 1
    return found[0] if found else None


def filled(tree: Any) -> list[Component]:
    """The filled buttons of a page: Mantine's filled variant, and a link drawn as a filled button."""
    buttons = [c for c in of_type(tree, "Button") if getattr(c, "variant", None) == "filled"]
    links = [c for c in of_type(tree, "A") if "mdm-link-button-filled" in classes(c)]
    return buttons + links


def action_ids(tree: Any) -> list[str]:
    return [
        c.id["action"]
        for c in walk(tree)
        if isinstance(getattr(c, "id", None), dict) and c.id.get("type") == ids.BATCH_ACTION
    ]


def paragraphs(tree: Any) -> list[str]:
    return [text_of(c) for c in of_type(tree, "P")]


# ---------------------------------------------------------------------------------------------- every state


@pytest.mark.parametrize("view", samples.BATCH_VIEWS, ids=lambda v: f"{v.kind}-{v.status}-{v.outcome}")
def test_every_state_has_one_title_one_filled_button_at_most_and_headed_sections(view: BatchView) -> None:
    tree = page(view, samples.ROW_PAGE if view.summary is not None else None)
    assert len(of_type(tree, "H1")) == 1
    assert len(filled(tree)) <= 1, [text_of(c) for c in filled(tree)]
    for found in of_type(tree, "Section"):
        assert type(found.children[0]).__name__ == "H2", prop(found, "aria-label")
    for button in of_type(tree, "Button"):
        if isinstance(button.id, dict):
            assert button.id["action"] in PAGE_ACTIONS
    assert not [
        c
        for c in walk(tree)
        if isinstance(getattr(c, "id", None), dict) and c.id.get("type") == ids.REVEAL_OPEN
    ]
    assert not [
        c for c in of_type(tree, "RadioGroup")
    ]  # the page holds no form: a split is named in the pane


def test_the_stage_reads_in_words_in_every_state() -> None:
    words = {
        "sampling": "Forced sample",
        "awaiting_checker": "Waits for a second steward",
        "staged": "In the tray",
        "committing": "Committing",
        "committed": "Committed",
        "stopped": "Stopped",
        "failed": "Nothing committed",
        "discarded": "Discarded",
    }
    for view in samples.BATCH_VIEWS:
        if view.status in words:
            assert batch.state_words(view) == words[view.status]
    assert batch.state_words(samples.BATCH_READY) == "Sample agreed"
    assert batch.state_words(samples.BATCH_PREPARED) == "Every change shown"
    shown = paragraphs(page(samples.BATCH_READY))
    assert "Sample agreed" in shown and "check" not in " ".join(shown).lower().split()


def test_the_header_names_people_by_role_and_the_pattern_by_its_marks() -> None:
    tree = page(samples.BATCH_STAGED)
    [title] = of_type(tree, "H1")
    assert title.children == "Alike Person reviews"
    facts = next(c for c in of_type(tree, "P") if "mdm-batch-facts" in classes(c))
    assert text_of(facts) == (
        f"{BATCH_ID} · prepared by you · 566 reviews · forced sample of 9 · confirmed by a coordinating steward"
    )
    other = next(c for c in of_type(page(samples.BATCH_READY_OTHER), "P") if "mdm-batch-facts" in classes(c))
    assert "prepared by a data steward" in text_of(other)
    marks = next(c for c in of_type(tree, "P") if "mdm-batch-marks" in classes(c))
    assert text_of(marks).startswith("Given name = the same · Family name = the same · Birth date ≈ similar")
    undo = page(samples.COMPENSATION)
    assert [c.children for c in of_type(undo, "H1")] == [f"Undo of batch {BATCH_ID}"]


def test_a_withdrawal_shows_the_breakers_notice_under_the_header_and_no_restore() -> None:
    tree = page(samples.BATCH_BULK_STOPPED)
    notices = [c for c in walk(tree) if "mdm-bulk-notice" in classes(c)]
    assert len(notices) == 1
    assert text_of(notices[0]).startswith(
        "Bulk decisions for this pattern are withdrawn: blind review agreed 3"
    )
    for component in walk(tree):
        if type(component).__name__ in ("Button", "Anchor", "A"):
            assert "restore" not in text_of(component).lower()


# ---------------------------------------------------------------------------------------------- the forced sample


def test_the_sample_shows_its_meter_its_reviews_and_how_it_works() -> None:
    tree = page(samples.BATCH_SAMPLING)
    sample = section(tree, "Forced sample")
    assert sample is not None and not of_type(sample, "Details")  # open while it is decided
    [meter] = of_type(sample, "Progress")
    assert prop(meter, "aria-label") == "Forced sample decided" and meter.value == pytest.approx(400 / 9)
    assert "4 of 9 decided · 4 agreed · none disagreed" in [
        text_of(c) for c in walk(sample) if "mdm-progress-text" in classes(c)
    ]
    items = [c for c in of_type(sample, "Li") if "mdm-sample-item" in classes(c)]
    assert len(items) == 9
    assert text_of(items[0]) == "crm and hr · crm:C001400 · T*** Q*** · Linked as suggested"
    assert batch.SAMPLE_NOTE in paragraphs(sample)
    ready = section(page(samples.BATCH_READY), "Forced sample")
    assert ready is not None and len(of_type(ready, "Details")) == 1  # kept, collapsed, once ready
    assert not getattr(ready.children[1], "open", False)
    assert section(page(samples.COMPENSATION), "Forced sample") is None


def test_an_open_sample_review_opens_its_task_and_a_decided_one_its_source_record() -> None:
    view = replace(
        samples.BATCH_SPLIT, sample=(samples.SAMPLE_OPEN, samples.SAMPLE_DISAGREED, samples.SAMPLE_VOID)
    )
    links = {c.children: c.href for c in of_type(section(page(view), "Forced sample"), "Anchor")}
    assert links["crm:C001377"] == f"/?batch={BATCH_ID}&task={samples.SAMPLE_OPEN.task_id}"
    assert links["crm:C001409"] == "/source/crm/C001409"
    assert links["crm:C001415"] == "/source/crm/C001415"


def test_the_splits_say_what_left_and_what_the_sample_needs_now() -> None:
    applied = section(page(samples.BATCH_SPLIT), "Forced sample")
    lines = [text_of(c) for c in walk(applied) if "mdm-split-line" in classes(c)]
    assert lines == [
        "crm:C001409 was decided Not a match, flagged on birth date: 38 reviews left the batch, to be decided "
        "one by one. The sample now needs 8 of 574."
    ]
    waiting = section(page(samples.BATCH_SPLIT_WAITING), "Forced sample")
    assert [text_of(c) for c in walk(waiting) if "mdm-split-line" in classes(c)] == [
        "crm:C001409 was decided Not a match, flagged on birth date: its split applies in a moment."
    ]
    every = batch.split_text(samples.BATCH_SPLIT_ALL, samples.SPLIT_EVERY, last=True)
    assert every.endswith(
        "every alike review left the batch after a disagreement. Decide them one by one in the inbox."
    )
    for tree in (applied, waiting):
        assert not of_type(tree, "Button") and not of_type(tree, "RadioGroup")  # no split form, no button
    assert batch.still_in(samples.BATCH_SPLIT) == 574
    voided = replace(samples.BATCH_SPLIT, sample=(*samples.BATCH_SPLIT.sample, samples.SAMPLE_VOID))
    assert batch.still_in(voided) == 573  # a void review left too; the top-up replaced it
    one = batch.split_text(samples.BATCH_SPLIT, replace(samples.SPLIT_APPLIED, count=1), last=False)
    assert "1 review left the batch" in one and "The sample now needs" not in one


# ---------------------------------------------------------------------------------------------- every change


def test_every_change_waits_for_preparation_then_lists_the_rows() -> None:
    before = section(page(samples.BATCH_READY), "Every change")
    assert before is not None and batch.BEFORE_PREPARED in paragraphs(before)
    region = next(c for c in walk(before) if "mdm-batch-rows-region" in classes(c))
    assert "mdm-hidden" in classes(region)  # there for the paging callback, hidden until prepared
    assert section(page(samples.BATCH_SAMPLING), "Every change") is None
    after = section(page(samples.BATCH_PREPARED, samples.ROW_PAGE), "Every change")
    shown = paragraphs(after)
    assert (
        "566 cross-references · 566 golden records updated · 3 chunks of at most 500 published rows · 12 to blind "
        "review" in shown
    )
    assert "Left out: 3 claimed by another steward, 1 now a close call. They stay in the inbox." in shown
    assert "Rows 1–2 of 566" in shown


def test_the_rows_are_a_table_of_masked_records_their_targets_and_what_changes() -> None:
    tree = page(samples.BATCH_PREPARED, samples.ROW_PAGE)
    [table] = [c for c in of_type(tree, "Table") if "mdm-batch-rows" in classes(c)]
    caption = table.children[0]
    assert type(caption).__name__ == "Caption" and caption.children == "Every row's change"
    assert [h.children for h in of_type(table, "Th")][:3] == ["Record", "Golden record", "What changes"]
    rows = [c for c in of_type(table, "Tr") if prop(c, "data-task-id")]
    assert len(rows) == 2
    links = {c.children: c.href for c in of_type(rows[0], "Anchor")}
    assert links == {"crm:C001377": "/source/crm/C001377", "PER-000451": "/record/PER-000451"}
    assert "+1 cross-reference · golden phone changes · no ID retired" in paragraphs(rows[0])
    second = paragraphs(rows[1])
    assert (
        "+1 cross-reference · golden phone changes · no ID retired · with 2 other rows of this batch"
        in second
    )
    assert batch.CHANGED_SINCE in second
    [details] = of_type(rows[0], "Details")
    assert text_of(details.children[0]).startswith("If you link: what changes in PER-000451")
    assert "hidden" in text_of(details) and "K***" not in text_of(details)  # only the changed row, masked
    for row in rows:
        assert not [c for c in of_type(row, "P") if "mdm-row-state" in classes(c)]  # no state before commit


def test_rows_page_by_position_and_hide_a_button_with_no_page() -> None:
    first = page(samples.BATCH_PREPARED, samples.ROW_PAGE)
    buttons = {c.id: c for c in of_type(first, "Button") if isinstance(c.id, str)}
    assert buttons[ids.BATCH_ROWS_PREV].disabled and not buttons[ids.BATCH_ROWS_NEXT].disabled
    assert (
        buttons[ids.BATCH_ROWS_PREV].children == "Previous 50"
        and buttons[ids.BATCH_ROWS_NEXT].children == "Next 50"
    )
    last = page(samples.BATCH_COMMITTED, samples.ROW_PAGE_LAST, depth=1)
    buttons = {c.id: c for c in of_type(last, "Button") if isinstance(c.id, str)}
    assert not buttons[ids.BATCH_ROWS_PREV].disabled and buttons[ids.BATCH_ROWS_NEXT].disabled
    assert batch.rows_label(1, 50, 566) == "Rows 51–100 of 566"
    assert batch.rows_label(0, 0, 566) == "No rows"
    assert capacity.BATCH_PAGE == 50


def test_once_committing_or_done_each_row_says_its_state() -> None:
    tree = page(samples.BATCH_COMMITTED, samples.ROW_PAGE_LAST)
    states = [text_of(c) for c in walk(tree) if "mdm-row-state" in classes(c)]
    assert states == [
        "committed in chunk 2",
        "not linked: the record changed; back in the queue",
        "back in the queue",
    ]
    undo = page(samples.COMPENSATION, samples.ROW_PAGE_UNDO)
    assert "−1 cross-reference · golden phone changes" in [
        text_of(c) for c in walk(undo) if "mdm-batch-impact" in classes(c)
    ]
    assert "566 cross-references ended · 566 golden records recomputed · 3 chunks" in paragraphs(undo)
    ready = page(samples.COMPENSATION_READY, samples.ROW_PAGE_UNDO)
    assert (
        f"Once every row is checked, stage it on the command line: mdm batch stage {samples.COMPENSATION_ID}"
        in paragraphs(ready)
    )
    assert action_ids(ready) == ["discard"]


# ---------------------------------------------------------------------------------------------- the actions


def test_decide_the_sample_is_a_link_in_the_footer_only() -> None:
    tree = page(samples.BATCH_SAMPLING)
    links = [c for c in of_type(tree, "A") if text_of(c) == "Decide the sample in the inbox"]
    assert len(links) == 1 and links[0].href == f"/?batch={BATCH_ID}"
    assert "mdm-link-button-filled" in classes(links[0])
    footer = section(tree, "Actions")
    assert footer is not None and links[0] in list(walk(footer))
    assert action_ids(tree) == ["discard"]
    [discard] = [c for c in of_type(tree, "Button") if text_of(c) == "Discard this batch"]
    assert discard.variant == "default"
    done = page(samples.BATCH_SAMPLE_DONE)
    [quiet] = [c for c in of_type(done, "A") if text_of(c) == "Decide the sample in the inbox"]
    assert "mdm-link-button-filled" not in classes(quiet)
    assert prop(quiet, "aria-describedby") == ids.BATCH_ACTION_REASONS
    [reasons] = [c for c in walk(done) if getattr(c, "id", None) == ids.BATCH_ACTION_REASONS]
    assert text_of(reasons) == "Every sample review is decided."


@pytest.mark.parametrize(
    ("view", "wanted"),
    [
        (samples.BATCH_READY, ["prepare", "discard"]),
        (samples.BATCH_READY_OTHER, ["prepare"]),
        (samples.BATCH_PREPARED, ["stage", "discard"]),
        (samples.BATCH_AWAITING, ["confirm", "discard"]),
        (samples.BATCH_AWAITING_CHECKER, ["confirm", "send_back", "discard"]),
        (samples.BATCH_STAGED, ["undo"]),
        (samples.BATCH_COMMITTING, ["stop"]),
        (samples.BATCH_COMMITTED, []),
    ],
)
def test_each_state_offers_its_actions(view: BatchView, wanted: list[str]) -> None:
    assert action_ids(page(view, samples.ROW_PAGE)) == wanted


def test_only_the_primary_action_is_filled_and_stop_never_is() -> None:
    def looks(view: BatchView) -> dict[str, str]:
        return {c.id["action"]: c.variant for c in of_type(page(view), "Button") if isinstance(c.id, dict)}

    assert looks(samples.BATCH_READY) == {"prepare": "filled", "discard": "default"}
    assert looks(samples.BATCH_READY_OTHER) == {"prepare": "default"}  # disabled: never filled
    assert looks(samples.BATCH_PREPARED) == {"stage": "filled", "discard": "default"}
    assert looks(samples.BATCH_AWAITING) == {"confirm": "default", "discard": "default"}
    assert looks(samples.BATCH_AWAITING_CHECKER) == {
        "confirm": "filled",
        "send_back": "default",
        "discard": "default",
    }
    assert looks(samples.BATCH_STAGED) == {"undo": "default"}
    assert looks(samples.BATCH_COMMITTING) == {"stop": "default"}
    assert looks(samples.BATCH_STOPPING) == {"stop": "default"}


def test_a_disabled_action_carries_its_reason() -> None:
    for view, reason in (
        (samples.BATCH_READY_OTHER, "Only the steward who drew this batch prepares it."),
        (samples.BATCH_AWAITING, "You prepared this batch, so another steward confirms it."),
        (samples.BATCH_STOPPING, "Stop is already asked."),
    ):
        tree = page(view)
        disabled = [c for c in of_type(tree, "Button") if isinstance(c.id, dict) and c.disabled]
        assert disabled and all(prop(c, "aria-describedby") == ids.BATCH_ACTION_REASONS for c in disabled)
        [reasons] = [c for c in walk(tree) if getattr(c, "id", None) == ids.BATCH_ACTION_REASONS]
        assert text_of(reasons) == reason


def test_the_footer_says_what_happens_next() -> None:
    above = paragraphs(section(page(samples.BATCH_PREPARED), "Actions"))
    assert (
        "Above 250 links, a second steward checks every row and confirms before it enters the tray." in above
    )
    below = paragraphs(section(page(samples.BATCH_PREPARED_SMALL), "Actions"))
    assert "It waits in the tray for 60 s, then commits in chunks of at most 500 published rows." in below
    [link_all] = [
        c
        for c in of_type(page(samples.BATCH_PREPARED_SMALL), "Button")
        if isinstance(c.id, dict) and c.id["action"] == "stage"
    ]
    assert link_all.children == "Link all 5" and link_all.variant == "filled"
    checker = paragraphs(section(page(samples.BATCH_AWAITING_CHECKER), "Actions"))
    assert (
        "Prepared by a data steward. Check every row; once you confirm, it waits in the tray for 60 s, where "
        "either of you can still undo it." in checker
    )
    assert batch.STOP_LINE in paragraphs(section(page(samples.BATCH_COMMITTING), "Actions"))
    assert batch.STOPPING in paragraphs(section(page(samples.BATCH_STOPPING), "Actions"))
    custom = paragraphs(section(page(samples.BATCH_PREPARED, undo_seconds=30, checker_above=600), "Actions"))
    assert "It waits in the tray for 30 s, then commits in chunks of at most 500 published rows." in custom
    longer = paragraphs(section(page(samples.BATCH_PREPARED, undo_seconds=600, checker_above=600), "Actions"))
    assert "It waits in the tray for 10 min, then commits in chunks of at most 500 published rows." in longer


# ---------------------------------------------------------------------------------------------- the tray, the chunks


def test_a_staged_batch_says_until_when_it_waits_with_no_ticking_countdown() -> None:
    tray = section(page(samples.BATCH_STAGED), "In the tray")
    assert tray is not None
    assert "In the tray until 12:04:31 UTC. It then commits in 3 chunks, one at a time." in paragraphs(tray)
    assert not [c for c in walk(tray) if isinstance(getattr(c, "id", None), dict)]


def test_a_committing_batch_shows_its_chunks_and_the_throttle() -> None:
    tree = page(samples.BATCH_COMMITTING)
    committing = section(tree, "Committing")
    [bar] = of_type(committing, "Progress")
    assert prop(bar, "aria-label") == "Chunks committed" and bar.value == pytest.approx(200 / 3)
    assert "Chunk 2 of 3 committed · 1,000 of 1,132 published rows" in [
        text_of(c) for c in walk(committing) if "mdm-progress-text" in classes(c)
    ]
    assert "The next chunk commits at 12:06 UTC, at the agreed rate." in paragraphs(committing)
    stopping = section(page(samples.BATCH_STOPPING), "Committing")
    assert not [p for p in paragraphs(stopping) if "agreed rate" in p]
    later = batch.committing_section(samples.BATCH_COMMITTING, NOW + timedelta(minutes=7))
    assert not [p for p in paragraphs(later) if "agreed rate" in p]


# ---------------------------------------------------------------------------------------------- the result


@pytest.mark.parametrize(
    ("view", "wanted"),
    [
        (
            samples.BATCH_COMMITTED,
            "Committed in 3 chunks: 566 links, commits 41 to 43. 12 went to blind review.",
        ),
        (
            samples.BATCH_STOPPED,
            "Stopped after chunk 2 of 3: 500 links committed; 66 reviews are back in the queue.",
        ),
        (
            samples.BATCH_BULK_STOPPED,
            "Stopped after chunk 1 of 3: the quality breaker withdrew bulk decisions for this pattern. 250 links "
            "committed; 316 reviews are back in the queue.",
        ),
        (
            samples.BATCH_CHUNK_FAILED,
            "Stopped after chunk 1 of 3: the next chunk failed three times. 250 links committed; 316 reviews are "
            "back in the queue.",
        ),
        (
            samples.BATCH_NOTHING,
            "Nothing was linked: every review moved before the batch could commit, so nothing was linked. Every "
            "review is back in the queue.",
        ),
        (samples.BATCH_DISCARDED, "Discarded. Its reviews stay in the inbox."),
        (
            samples.BATCH_SPLIT_ALL,
            "Every alike review left the batch after a disagreement. Decide them one by one in the inbox.",
        ),
        (
            samples.BATCH_TOO_FEW,
            "Too few alike reviews are left to link together. Decide them one by one in the inbox.",
        ),
        (samples.COMPENSATION, "Undid 563 of 566 links. 3 were changed after the batch, so they are kept."),
    ],
)
def test_the_result_says_how_the_batch_ended(view: BatchView, wanted: str) -> None:
    result = section(page(view), "Result")
    assert result is not None and wanted in paragraphs(result)


def test_reviews_that_failed_alone_are_counted_and_back_in_the_queue() -> None:
    result = paragraphs(section(page(samples.BATCH_FAILED_ALONE), "Result"))
    assert (
        "4 reviews were not linked because their record, task or golden record moved, or a cannot-link rule now "
        "keeps them apart; they are back in the queue." in result
    )
    assert batch.failed_alone_line(replace(samples.BATCH_FAILED_ALONE, counts={"failed": 1})).startswith(
        "1 review was not linked because its record"
    )


def test_the_compensation_line_gives_the_full_command_and_no_button() -> None:
    tree = page(samples.BATCH_COMMITTED)
    result = section(tree, "Result")
    assert (
        f"Its committed links can be undone until 28 October 2026, on the command line: mdm batch compensate "
        f"{BATCH_ID} --reason pattern_wrong|source_defect|sample_missed" in paragraphs(result)
    )
    [code] = of_type(result, "Code")
    assert (
        code.children == f"mdm batch compensate {BATCH_ID} --reason pattern_wrong|source_defect|sample_missed"
    )
    assert not of_type(tree, "Button") or all(
        "compensate" not in text_of(b).lower() and "undo" not in text_of(b).lower()
        for b in of_type(tree, "Button")
    )
    late = batch.compensation_lines(
        samples.BATCH_COMMITTED, samples.BATCH_COMMITTED.undo_until + timedelta(days=1)
    )
    assert late == []  # past its 30 days: nothing to offer on the command line either


def test_a_compensation_open_done_or_stopped_is_named_and_linked() -> None:
    being = section(page(samples.BATCH_BEING_UNDONE), "Result")
    assert f"Being undone by batch {samples.COMPENSATION_ID}." in paragraphs(being)
    assert [c.href for c in of_type(being, "Anchor")] == [f"/batch/{samples.COMPENSATION_ID}"]
    undone = section(page(samples.BATCH_UNDONE), "Result")
    assert f"Undone by batch {samples.COMPENSATION_ID}." in paragraphs(undone)
    assert not of_type(undone, "Code")
    partly = paragraphs(section(page(samples.BATCH_PARTLY_UNDONE), "Result"))
    assert (
        f"250 of its 566 links were undone by batch {samples.COMPENSATION_STOPPED_ID}, which stopped."
        in partly
    )
    assert (
        f"The other 316 can be undone until 28 October 2026: mdm batch compensate {BATCH_ID} --reason "
        "pattern_wrong|source_defect|sample_missed" in partly
    )


# ---------------------------------------------------------------------------------------------- the stamp and the poll


def test_the_stamp_follows_what_a_redraw_needs_and_holds_codes_only() -> None:
    stamp = batch.stamp_of(samples.BATCH_SAMPLING)
    assert stamp == batch.stamp_of(replace(samples.BATCH_SAMPLING))
    assert json.loads(json.dumps(stamp)) == stamp
    assert batch.stamp_of(samples.BATCH_SPLIT) != stamp
    assert batch.stamp_of(samples.BATCH_SPLIT) != batch.stamp_of(samples.BATCH_SPLIT_WAITING)
    assert batch.stamp_of(samples.BATCH_COMMITTING) != batch.stamp_of(samples.BATCH_STOPPING)
    assert batch.stamp_of(samples.BATCH_READY) != batch.stamp_of(samples.BATCH_PREPARED)
    later = replace(samples.BATCH_COMMITTING, progress=replace(samples.PROGRESS, chunks_committed=3))
    assert batch.stamp_of(later) != batch.stamp_of(samples.BATCH_COMMITTING)
    shown = json.dumps(batch.stamp_of(samples.BATCH_STOPPED), ensure_ascii=False)
    assert "*" not in shown and "·" not in shown


def test_the_poll_runs_fast_while_the_batch_moves_slowly_while_open_and_not_once_done() -> None:
    assert batch.poll_of(samples.BATCH_STAGED) == (False, capacity.BATCH_POLL_SECONDS * 1000)
    assert batch.poll_of(samples.BATCH_COMMITTING) == (False, capacity.BATCH_POLL_SECONDS * 1000)
    assert batch.poll_of(samples.BATCH_SAMPLING) == (False, capacity.COUNTS_REFRESH_SECONDS * 1000)
    assert batch.poll_of(samples.BATCH_AWAITING) == (False, capacity.COUNTS_REFRESH_SECONDS * 1000)
    for view in (
        samples.BATCH_COMMITTED,
        samples.BATCH_STOPPED,
        samples.BATCH_DISCARDED,
        samples.BATCH_NOTHING,
    ):
        assert batch.poll_of(view)[0] is True


# ---------------------------------------------------------------------------------------------- on a real hub
#
# The page's callbacks (B1 the redraw, B2 an action, B3 the rows' paging) over a world of 12 alike Person
# reviews on an in-memory DuckDB: a forced sample of 5, and 7 reviews to link together.


@pytest.fixture
def alike() -> Iterator[Hub]:
    """12 alike Person reviews on an in-memory DuckDB (a forced sample of 5, 7 to link)."""
    settings = base_settings()
    store = open_store(settings)
    store.init_schema(create_landing=True)
    hub = open_hub(settings, store, "duckdb")
    try:
        helpers.workbench_world(hub, persons=32)
        helpers.alike_reviews(hub, 12, first=20)
        yield hub
    finally:
        hub.close()
        store.close()


def drawn(hub: Hub) -> str:
    """The batch a data steward draws from the largest Person group."""
    ctx = context.for_test(hub)
    group = hub.batches.groups(actor=ctx.actor, entity="person").groups[0]
    return hub.batches.draw(group.group_key, actor=ctx.actor, entity="person").batch_id


def sample_agreed(hub: Hub, batch_id: str) -> None:
    """Every forced-sample review linked to its default, each flushed after its window."""
    helpers.decide_sample(hub, batch_id, actor=helpers.STEWARD)
    assert hub.batches.refresh(batch_id).status == "ready"


def ref(batch_id: str) -> dict:
    return {"batch_id": batch_id}


def store_of(tree: Any, wanted: Any) -> Any:
    [found] = [c for c in walk(tree) if getattr(c, "id", None) == wanted]
    return found


def test_the_page_of_a_drawn_batch_holds_its_sample_its_stores_and_its_poll(alike: Hub) -> None:
    batch_id = drawn(alike)
    ctx = context.for_test(alike)
    shown = batch_page.layout(ctx, batch_id)
    assert [text_of(c) for c in of_type(shown, "H1")] == ["Alike Person reviews"]
    assert store_of(shown, ids.BATCH_REF).data == ref(batch_id)
    view = alike.batches.batch(batch_id, actor=ctx.actor)
    assert store_of(shown, ids.BATCH_STAMP).data == batch.stamp_of(view)
    poll = store_of(shown, ids.BATCH_POLL)
    assert poll.disabled is False and poll.interval == capacity.COUNTS_REFRESH_SECONDS * 1000
    assert store_of(shown, ids.BATCH_ROWS_CURSOR).data == {"stack": [], "after": None}
    [decide_link] = [c for c in of_type(shown, "A") if text_of(c) == "Decide the sample in the inbox"]
    assert decide_link.href == f"/?batch={batch_id}"
    assert "mdm-link-button-filled" in classes(decide_link)  # the page's one filled action
    sample_links = [c.href for c in of_type(shown, "Anchor") if "mdm-sample-source" in classes(c)]
    assert len(sample_links) == view.sample_size
    assert all(link.startswith(f"/?batch={batch_id}&task=TSK-") for link in sample_links)
    assert prop(store_of(shown, ids.BATCH), "data-mdm-keys") is None  # no single key acts here


def test_an_unknown_batch_says_so_and_a_consumer_is_told_why(alike: Hub) -> None:
    ctx = context.for_test(alike)
    gone = batch_page.layout(ctx, "BAT-0000000000000000abcd")
    assert "No batch has the ID BAT-0000000000000000abcd." in text_of(gone)
    assert [text_of(c) for c in of_type(gone, "H1")] == ["Batch"]
    assert not [c for c in walk(gone) if getattr(c, "id", None) == ids.BATCH_POLL]
    batch_id = drawn(alike)
    consumer = batch_page.layout(context.for_test(alike, role="consumer"), batch_id)
    assert "Your role, consumer, decides no tasks" in text_of(consumer)
    assert not action_ids(consumer)
    kind, args = app_module.parse_route(f"/batch/{batch_id}")
    page_, note, on_inbox = app_module.render_route(ctx, f"/batch/{batch_id}", "", "person")
    assert (kind, args, note, on_inbox) == ("batch", (batch_id,), None, False)
    assert "Alike Person reviews" in text_of(page_)


def test_b1_draws_the_page_again_only_when_its_stamp_changes(alike: Hub) -> None:
    batch_id = drawn(alike)
    ctx = context.for_test(alike)
    stamp = batch.stamp_of(alike.batches.batch(batch_id, actor=ctx.actor))
    assert batch_page.refresh(ctx, ref(batch_id), stamp, None) == (no_update,) * 5
    assert batch_page.refresh(ctx, {"batch_id": "Tamsin"}, stamp, None) == (no_update,) * 5
    sample_agreed(alike, batch_id)
    children, fresh, disabled, interval, cursor = batch_page.refresh(ctx, ref(batch_id), stamp, None)
    assert fresh != stamp and fresh["status"] == "ready"
    assert "Sample agreed" in paragraphs(children)
    assert (disabled, interval) == (False, capacity.COUNTS_REFRESH_SECONDS * 1000)
    assert cursor == {"stack": [], "after": None}
    assert batch_page.refresh(ctx, ref(batch_id), fresh, cursor) == (no_update,) * 5


def test_b2_takes_a_batch_from_its_sample_to_the_tray_and_back_saying_how_each_went(alike: Hub) -> None:
    batch_id = drawn(alike)
    ctx = context.for_test(alike)
    refused = batch_page.act(ctx, batch_id, "prepare")
    assert refused is not None and refused.note["color"] == "yellow" and not refused.tray
    assert batch_page.act(ctx, batch_id, "decide_sample") is None  # a link, never a button
    assert batch_page.act(ctx, batch_id, "restore") is None
    assert batch_page.act(ctx, "Tamsin", "prepare") is None
    sample_agreed(alike, batch_id)
    prepared = batch_page.act(ctx, batch_id, "prepare")
    assert prepared is not None and prepared.note["title"] == "Every change shown"
    assert (
        prepared.note["message"]
        == "Every change is shown: 7 links in 1 chunk. Check the rows, then link them."
    )
    staged = batch_page.act(ctx, batch_id, "stage")
    assert staged is not None and staged.tray and staged.note["title"] == "In the tray"
    assert staged.note["message"] == (
        f"In the tray: Link 7 alike reviews ({batch_id}). It commits in "
        f"{alike.settings.undo_seconds} s unless you undo it."
    )
    other = batch_page.act(context.for_test(alike, role="coordinating_steward"), batch_id, "undo")
    assert other is not None and other.note["color"] == "yellow"  # neither its maker nor its second steward
    undone = batch_page.act(ctx, batch_id, "undo")
    assert undone is not None and undone.tray
    assert undone.note["message"] == "Undone. The batch is ready again, and nothing was linked."
    assert alike.batches.batch(batch_id, actor=ctx.actor).status == "ready"
    again = batch_page.act(ctx, batch_id, "undo")
    assert again is not None and again.note["title"] == "The batch changed"
    discarded = batch_page.act(ctx, batch_id, "discard")
    assert discarded is not None and discarded.note["message"] == "Discarded. Its reviews stay in the inbox."
    assert alike.batches.batch(batch_id, actor=ctx.actor).status == "discarded"


def test_b2_above_the_threshold_asks_a_second_steward_who_confirms_or_sends_it_back(alike: Hub) -> None:
    checked = Hub.open(alike.settings.with_(batch_checker_above=3), store=alike.store, as_role="data_owner")
    batch_id = drawn(checked)
    maker = context.for_test(checked)
    checker = context.for_test(checked, role="coordinating_steward")
    sample_agreed(checked, batch_id)
    batch_page.act(maker, batch_id, "prepare")
    asked = batch_page.act(maker, batch_id, "stage")
    assert asked is not None and not asked.tray
    assert (asked.note["title"], asked.note["message"]) == (
        "Sent for confirmation",
        "Waiting for a second steward to confirm.",
    )
    own = batch_page.act(maker, batch_id, "confirm")
    assert own is not None and own.note["title"] == "A second steward confirms"
    back = batch_page.act(checker, batch_id, "send_back")
    assert back is not None and back.note["message"] == "Sent back to the steward who prepared it."
    batch_page.act(maker, batch_id, "stage")
    confirmed = batch_page.act(checker, batch_id, "confirm")
    assert confirmed is not None and confirmed.tray and confirmed.note["title"] == "Confirmed"
    assert re.fullmatch(
        r"Confirmed\. It waits in the tray until \d\d:\d\d:\d\d UTC\.", confirmed.note["message"]
    )
    view = checked.batches.batch(batch_id, actor=checker.actor)
    assert view.status == "staged" and view.checker_label == "you"


def test_b2_stops_a_committing_batch_before_its_next_chunk(
    alike: Hub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        capacity, "COMMIT_CHUNK_ROWS", 6
    )  # a few links a chunk, so the batch commits in parts
    batch_id = drawn(alike)
    ctx = context.for_test(alike)
    sample_agreed(alike, batch_id)
    batch_page.act(ctx, batch_id, "prepare")
    nothing = batch_page.act(ctx, batch_id, "stop")
    assert nothing is not None and nothing.note["title"] == "Nothing to stop"
    batch_page.act(ctx, batch_id, "stage")
    waiting = batch_page.act(ctx, batch_id, "stop")
    assert waiting is not None and waiting.note["title"] == "Still in the tray"
    helpers.flush_past_window(alike)
    view = alike.batches.batch(batch_id, actor=ctx.actor)
    assert view.status == "committing" and view.progress is not None and view.progress.chunks > 1
    late = batch_page.act(ctx, batch_id, "undo")
    assert late is not None and late.note["title"] == "Too late to undo"
    assert late.note["message"].startswith("Too late to undo: this batch has started to commit.")
    stopping = batch_page.act(ctx, batch_id, "stop")
    assert stopping is not None and not stopping.tray
    assert stopping.note["message"] == "Stop asked. It stops before the next chunk."
    shown = batch_page.body(ctx, alike.batches.batch(batch_id, actor=ctx.actor), None)
    assert batch.STOPPING in paragraphs(shown)
    alike.tray.flush()
    done = alike.batches.batch(batch_id, actor=ctx.actor)
    assert done.status == "stopped"
    assert any(
        p.startswith("Stopped after chunk 1 of ") for p in paragraphs(batch_page.body(ctx, done, None))
    )


def test_b3_pages_the_rows_by_position_and_keeps_a_stack_of_cursors(
    alike: Hub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(capacity, "BATCH_PAGE", 3)
    batch_id = drawn(alike)
    ctx = context.for_test(alike)
    sample_agreed(alike, batch_id)
    batch_page.act(ctx, batch_id, "prepare")
    shown = batch_page.layout(ctx, batch_id)
    first = store_of(shown, ids.BATCH_ROWS_CURSOR).data
    assert first["stack"] == [] and isinstance(first["after"], int)
    assert text_of(store_of(shown, ids.BATCH_ROWS_LABEL)) == "Rows 1–3 of 7"
    rows, label, prev_off, next_off, second = batch_page.move_rows(ctx, ref(batch_id), first, "next")
    assert label == "Rows 4–6 of 7" and (prev_off, next_off) == (False, False)
    assert second["stack"] == [first["after"]]
    assert len(of_type(rows, "Tr")) == 1 + 3  # the header and three rows
    *_, third = batch_page.move_rows(ctx, ref(batch_id), second, "next")
    assert len(third["stack"]) == 2 and third["after"] is None
    assert batch_page.move_rows(ctx, ref(batch_id), third, "next") == (no_update,) * 5
    _, label, prev_off, _, back = batch_page.move_rows(ctx, ref(batch_id), third, "prev")
    assert label == "Rows 4–6 of 7" and back == second and prev_off is False
    *_, start = batch_page.move_rows(ctx, ref(batch_id), second, "prev")
    assert start == first
    assert batch_page.move_rows(ctx, ref(batch_id), first, "prev") == (no_update,) * 5
    # a redraw keeps the page of rows on screen
    alike.batches.stage(batch_id, actor=ctx.actor)
    stamp = store_of(shown, ids.BATCH_STAMP).data
    children, *_, kept = batch_page.refresh(ctx, ref(batch_id), stamp, second)
    assert kept == second and "Rows 4–6 of 7" in paragraphs(children)


# ---------------------------------------------------------------------------------------------- through Dash


PORT = 8050


@pytest.fixture
def app(alike: Hub) -> Iterator[Dash]:
    """The whole workbench over the alike world."""
    built = app_module.create_app(alike.settings, hub=alike, worker=False, listen=("127.0.0.1", PORT))
    state = built.server.extensions[context.EXTENSION]
    state.owns_hub = False
    try:
        yield built
    finally:
        state.close()


def callback_of(app: Dash, fragment: str) -> dict:
    found = [c for c in app._callback_list if fragment in c["output"]]
    assert len(found) == 1, (fragment, len(found))
    return found[0]


def outputs_of(output: str) -> list[dict]:
    parts = output[2:-2].split("...") if output.startswith("..") else [output]
    found = []
    for part in parts:
        raw, _, prop_ = part.rpartition(".")
        found.append({"id": json.loads(raw) if raw.startswith("{") else raw, "property": prop_.split("@")[0]})
    return found


def fire(app: Dash, fragment: str, inputs: list, state: list, changed: list[str]) -> dict:
    """Posts one callback as the browser would; its JSON answer."""
    callback = callback_of(app, fragment)
    outputs = outputs_of(callback["output"])
    body = {
        "output": callback["output"],
        "outputs": outputs if len(outputs) > 1 else outputs[0],
        "inputs": inputs,
        "state": state,
        "changedPropIds": changed,
    }
    answer = app.server.test_client().post(
        "/_dash-update-component",
        data=json.dumps(body),
        headers={"Content-Type": "application/json", "Host": f"127.0.0.1:{PORT}"},
    )
    assert answer.status_code in (200, 204), answer.get_data(as_text=True)[:300]
    return json.loads(answer.get_data(as_text=True) or "{}")


def test_the_batch_page_registers_its_three_callbacks(app: Dash) -> None:
    redraw = callback_of(app, f"{ids.BATCH_VIEW}.children")
    act_ = callback_of(app, f"{ids.BATCH_VERSION}.data@")
    rows = callback_of(app, f"{ids.BATCH_ROWS}.children")
    assert redraw["prevent_initial_call"] and act_["prevent_initial_call"] and rows["prevent_initial_call"]
    assert f"{ids.TRAY_VERSION}.data@" in act_["output"]


def test_an_action_posted_through_dash_moves_the_batch_and_notifies(app: Dash, alike: Hub) -> None:
    batch_id = drawn(alike)
    sample_agreed(alike, batch_id)
    action = ids.batch_action("prepare")
    key = json.dumps(action, sort_keys=True, separators=(",", ":"))
    state = [
        {"id": ids.BATCH_REF, "property": "data", "value": ref(batch_id)},
        {"id": ids.PERSONA, "property": "data", "value": "data_steward"},
        {"id": ids.BATCH_VERSION, "property": "data", "value": 4},
        {"id": ids.TRAY_VERSION, "property": "data", "value": 9},
    ]
    unclicked = [[{"id": action, "property": "n_clicks", "value": None}]]
    assert (
        fire(app, f"{ids.BATCH_VERSION}.data@", unclicked, state, [f"{key}.n_clicks"]).get("response", {})
        == {}
    )
    clicked = [[{"id": action, "property": "n_clicks", "value": 1}]]
    answer = fire(app, f"{ids.BATCH_VERSION}.data@", clicked, state, [f"{key}.n_clicks"])
    assert answer["response"][ids.BATCH_VERSION]["data"] == 5
    assert ids.TRAY_VERSION not in answer["response"]  # preparing leaves the tray alone
    notes = answer["sideUpdate"][ids.NOTIFY]["sendNotifications"]
    assert notes[0]["title"] == "Every change shown"
    assert alike.batches.batch(batch_id, actor=helpers.STEWARD).summary is not None
    redraw = fire(
        app,
        f"{ids.BATCH_VIEW}.children",
        [
            {"id": ids.BATCH_POLL, "property": "n_intervals", "value": 1},
            {"id": ids.BATCH_VERSION, "property": "data", "value": 5},
            {"id": ids.BATCH_SETTLED, "property": "data", "value": None},
        ],
        [
            {"id": ids.BATCH_REF, "property": "data", "value": ref(batch_id)},
            {"id": ids.BATCH_STAMP, "property": "data", "value": None},
            {"id": ids.BATCH_ROWS_CURSOR, "property": "data", "value": None},
            {"id": ids.PERSONA, "property": "data", "value": "data_steward"},
        ],
        [f"{ids.BATCH_VERSION}.data"],
    )["response"]
    assert "Every change shown" in json.dumps(redraw[ids.BATCH_VIEW]["children"])
    assert redraw[ids.BATCH_STAMP]["data"]["prepared"] is True
    assert redraw[ids.BATCH_POLL]["disabled"] is False


def test_a_page_of_rows_posted_through_dash_moves_by_position(
    app: Dash, alike: Hub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(capacity, "BATCH_PAGE", 5)
    batch_id = drawn(alike)
    sample_agreed(alike, batch_id)
    alike.batches.prepare(batch_id, actor=helpers.STEWARD)
    first = alike.batches.rows(batch_id, actor=helpers.STEWARD, limit=5)
    answer = fire(
        app,
        f"{ids.BATCH_ROWS}.children",
        [
            {"id": ids.BATCH_ROWS_PREV, "property": "n_clicks", "value": None},
            {"id": ids.BATCH_ROWS_NEXT, "property": "n_clicks", "value": 1},
        ],
        [
            {"id": ids.BATCH_REF, "property": "data", "value": ref(batch_id)},
            {"id": ids.BATCH_ROWS_CURSOR, "property": "data", "value": {"stack": [], "after": first.after}},
            {"id": ids.PERSONA, "property": "data", "value": "data_steward"},
        ],
        [f"{ids.BATCH_ROWS_NEXT}.n_clicks"],
    )["response"]
    assert answer[ids.BATCH_ROWS_LABEL]["children"] == "Rows 6–7 of 7"
    assert answer[ids.BATCH_ROWS_CURSOR]["data"] == {"stack": [first.after], "after": None}
    assert (
        answer[ids.BATCH_ROWS_NEXT]["disabled"] is True and answer[ids.BATCH_ROWS_PREV]["disabled"] is False
    )
