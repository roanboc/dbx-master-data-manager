"""The inbox and the decide pane without a browser (plan B.9.2; DuckDB only).

Two kinds of test: the views rendered from the invented samples of `tests/workbench_samples.py` (the
compare table, the waterfall to scale, the preview and impact line, the health strip, every case shape
of the decide pane, the grid's rows), and the pure functions behind the inbox's callbacks on a real hub
over the mini world with its crafted cases (the layout, a case's render, every action, a reveal, and the
rows a settlement changes). Nothing here starts a server or a browser.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from dash import Dash
from dash.development.base_component import Component

from mdm import capacity
from mdm.backend.factory import open_store
from mdm.models.canonical import utcnow
from mdm.models.records import SourceKey
from mdm.models.workbench import Action, CompareRow, Preview
from mdm.services.context import Hub
from mdm.ui import context, ids, messages
from mdm.ui.components import compare, decide, health, impact, reveal, waterfall
from mdm.ui.pages import inbox
from tests import helpers
from tests import workbench_samples as samples
from tests.conftest import base_settings, open_hub

NOW = samples.NOW


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
        parts = [text_of(getattr(tree, "children", None) or "")]
        for name in ("label", "rightSection"):
            value = getattr(tree, name, None) if name in tree._prop_names else None
            if isinstance(value, (str, Component)):
                parts.append(text_of(value))
        return "".join(parts)
    return ""


def by_id(tree: Any, wanted: Any) -> list[Component]:
    return [c for c in walk(tree) if getattr(c, "id", None) == wanted]


def one(tree: Any, wanted: Any) -> Component:
    found = by_id(tree, wanted)
    assert len(found) == 1, (wanted, len(found))
    return found[0]


def panels_of(tree: Any) -> list[Component]:
    """The candidate panels of a pane, in order."""
    return [
        c
        for c in walk(tree)
        if isinstance(getattr(c, "id", None), dict) and c.id.get("type") == ids.CANDIDATE_PANEL
    ]


def strings_outside(tree: Any, excluded: Any) -> list[str]:
    """Every string of a tree, leaving out the subtree whose ID is `excluded`."""
    found: list[str] = []

    def visit(node: Any) -> None:
        if isinstance(node, (list, tuple)):
            for item in node:
                visit(item)
        elif isinstance(node, Component):
            if getattr(node, "id", None) == excluded:
                return
            for name in node._prop_names:
                value = getattr(node, name, None)
                if isinstance(value, str):
                    found.append(value)
                else:
                    visit(value)
        elif isinstance(node, str):
            found.append(node)

    visit(tree)
    return found


def classes(component: Component) -> set[str]:
    return set((getattr(component, "className", None) or "").split())


def prop(component: Component, name: str) -> Any:
    return component.to_plotly_json()["props"].get(name)


def strings(tree: Any) -> Iterator[str]:
    if isinstance(tree, str):
        yield tree
    elif isinstance(tree, Component):
        for name in tree._prop_names:
            yield from strings(getattr(tree, name, None))
        for value in tree.to_plotly_json()["props"].values():
            if isinstance(value, str):
                yield value
    elif isinstance(tree, dict):
        for key, value in tree.items():
            yield str(key)
            yield from strings(value)
    elif isinstance(tree, (list, tuple)):
        for item in tree:
            yield from strings(item)


# ---------------------------------------------------------------------------------------------- the compare table


def test_the_compare_table_marks_agreement_with_a_symbol_as_well_as_a_class() -> None:
    table = compare.render(samples.CASE_CLOSE_CALL.columns, samples.COMPARE_CLOSE_CALL)
    cells = [c for c in walk(table) if type(c).__name__ == "Td"]
    marked = [c for c in cells if classes(c) & {"mdm-agree", "mdm-partial", "mdm-disagree", "mdm-missing"}]
    assert marked, "candidate cells carry their agreement"
    for cell in marked:
        (mark,) = classes(cell) & {"mdm-agree", "mdm-partial", "mdm-disagree", "mdm-missing"}
        symbol = [c for c in walk(cell) if "mdm-symbol" in classes(c)]
        assert len(symbol) == 1 and symbol[0].children == compare.SYMBOLS[mark.removeprefix("mdm-")]
        assert f"({compare.AGREEMENT_WORDS[mark.removeprefix('mdm-')]})" in text_of(cell)
    first = next(c for c in walk(table) if type(c).__name__ == "Thead")
    headers = [text_of(c) for c in walk(first) if type(c).__name__ == "Th"]
    assert headers == ["Attribute", *samples.CASE_CLOSE_CALL.columns]
    assert f"Against {samples.CASE_CLOSE_CALL.columns[0]}" in text_of(table)
    assert not by_id(table, ids.reveal(ids.REVEAL_OPEN, "decide"))  # nothing masked, nothing to show
    # every value names its column, which the stacked layout of a narrow screen shows beside it
    assert all(prop(c, "data-column") in samples.CASE_CLOSE_CALL.columns for c in cells)


def test_the_compare_table_leads_with_what_the_matcher_compared() -> None:
    rows = (
        *samples.COMPARE_CLOSE_CALL,
        CompareRow("email", "Email", ("a", "b", None, None), ("", "", ""), critical=False, personal=False),
    )
    table = compare.render(samples.CASE_CLOSE_CALL.columns, rows)
    first, rest = compare.main_rows(rows)
    assert all(compare.compared(r) or r.critical for r in first) and "email" in [r.attribute for r in rest]
    assert not [r for r in rest if compare.compared(r)]
    other = next(c for c in walk(table) if type(c).__name__ == "Details")
    assert f"Other values ({len(rest)})" in text_of(other) and "not compared" in text_of(other)
    assert (
        compare.empty_sentence(
            [
                CompareRow("email", "Email", (None,), (), critical=False, personal=False),
                CompareRow("left_on", "Left on", (None,), (), critical=False, personal=False),
            ],
            3,
        )
        == "None of these records has an email or a left-on date."
    )
    single = [CompareRow("phone", "Phone", (None,), (), critical=False, personal=False)]
    assert compare.empty_sentence(single, 1) == "This record has no phone."


def test_masked_values_read_masked_and_offer_a_reveal_with_its_modal() -> None:
    columns = samples.CASE_PERSON_STAGED.columns
    table = compare.render(columns, samples.COMPARE_PERSON, revealable=True)
    masked = [c for c in walk(table) if "mdm-masked" in classes(c)]
    assert {c.children for c in masked} >= {"K***", "B***", "hidden"}
    assert text_of(table).count("(masked)") == len(masked)
    one(table, ids.reveal(ids.REVEAL_OPEN, "decide"))
    one(table, ids.reveal(ids.REVEAL_MODAL, "decide"))  # the modal travels with its button
    one(table, ids.reveal(ids.REVEAL_CONFIRM, "decide"))
    shown = compare.render(columns, samples.REVEALED_PERSON.compare, revealed=True)
    assert not [c for c in walk(shown) if "mdm-masked" in classes(c)]
    assert not by_id(shown, ids.reveal(ids.REVEAL_OPEN, "decide"))
    assert not by_id(shown, ids.reveal(ids.REVEAL_MODAL, "decide"))
    assert compare.REVEALED_NOTE in text_of(shown)
    assert "Kaewyn" in text_of(shown)
    not_offered = compare.render(columns, samples.COMPARE_PERSON, revealable=False)
    assert not by_id(not_offered, ids.reveal(ids.REVEAL_OPEN, "decide"))


def test_an_attribute_no_record_holds_gets_one_line_not_a_row() -> None:
    rows = (
        *samples.COMPARE_CLOSE_CALL[:1],
        CompareRow("phone", "Phone", (None, None), ("",), critical=False, personal=False),
    )
    table = compare.render(samples.CASE_CLOSE_CALL.columns[:2], rows)
    labels = [text_of(c) for c in walk(table) if type(c).__name__ == "Th" and prop(c, "scope") == "row"]
    assert labels == ["Name · critical"]
    assert "None of these records has a phone." in text_of(table)
    assert compare.render((), ()).children is None


# ---------------------------------------------------------------------------------------------- the waterfall


def _bars(tree: Any) -> list[Component]:
    return [c for c in walk(tree) if "mdm-wf-bar" in classes(c)]


def _percent(value: str) -> float:
    return float(value.rstrip("%"))


def test_the_waterfall_is_drawn_to_scale_within_its_axis() -> None:
    candidate = samples.CANDIDATE_1
    low, high = waterfall.axis(candidate)
    assert (low, high) == (-10.0, 8.0)  # whole numbers, beyond the prior and the band edges
    assert waterfall.position(low, low, high) == 0.0 and waterfall.position(high, low, high) == 100.0
    assert waterfall.position(-100, low, high) == 0.0 and waterfall.position(100, low, high) == 100.0
    figure = waterfall.render(candidate)
    rows = [c for c in walk(figure) if "mdm-wf-row" in classes(c) and "mdm-wf-axis-row" not in classes(c)]
    assert len(rows) == len(candidate.steps) + 1  # a row per step, and the total
    previous_end = waterfall.position(0.0, low, high)
    for step, row in zip(candidate.steps, rows, strict=False):
        bars = _bars(row)
        start, end = waterfall.position(step.start, low, high), waterfall.position(step.end, low, high)
        if abs(step.weight) >= waterfall.ZERO:
            (bar,) = bars
            left, width = _percent(bar.style["left"]), _percent(bar.style["width"])
            assert left == pytest.approx(min(start, end)) and left + width == pytest.approx(max(start, end))
            assert ("mdm-wf-up" in classes(bar)) == (step.weight > 0)
            assert start == pytest.approx(previous_end)  # each bar starts where the last ended
        else:
            # the missing registered ID: a dashed bar of the weight a matching one would add, to scale
            (ghost,) = bars
            assert "mdm-wf-ghost" in classes(ghost)
            width = _percent(ghost.style["width"])
            assert width == pytest.approx(waterfall.position(step.start + 5.3, low, high) - start, abs=0.01)
            assert "(+5.3)" in text_of(row)
        previous_end = end
    zero = [c for c in walk(figure) if "mdm-wf-zero-line" in classes(c)]
    assert zero and all(_percent(z.style["left"]) == waterfall.position(0.0, low, high) for z in zero)
    regions = [c for c in walk(figure) if "mdm-wf-region" in classes(c)]
    assert sum(_percent(r.style["width"]) for r in regions[:3]) == pytest.approx(100.0, abs=0.01)
    marker = next(c for c in walk(figure) if "mdm-wf-marker" in classes(c))
    assert _percent(marker.style["left"]) == pytest.approx(waterfall.position(candidate.total, low, high))
    # the axis: its ends as weights, zero, and the band edges as the scores they stand for
    ticks = [text_of(c) for c in walk(figure) if "mdm-wf-tick" in classes(c)]
    assert ticks == ["−10", "60", "90", "+8"]
    assert "Total +1.9 → score 79" in text_of(figure) and "Shading" not in text_of(figure)
    distinct = waterfall.render(samples.CANDIDATE_3)
    assert not [c for c in walk(distinct) if "mdm-wf-ghost" in classes(c)]


def test_every_waterfall_row_is_labelled_and_a_hidden_table_repeats_the_numbers() -> None:
    figure = waterfall.render(samples.CANDIDATE_1)
    labels = [prop(c, "aria-label") for c in walk(figure) if prop(c, "role") == "img"]
    assert labels[:3] == ["Prior: minus 9.0", "Name: the same, plus 9.4", "City: the same, plus 4.1"]
    assert "Registered ID: missing, no weight; given, it could add 5.3" in labels
    assert labels[-1] == "Total: plus 1.9, a score of 79, review"
    hidden = next(c for c in walk(figure) if "mdm-sr-only" in classes(c) and type(c).__name__ == "Div")
    table = next(c for c in walk(hidden) if type(c).__name__ == "Table")
    body = next(c for c in walk(table) if type(c).__name__ == "Tbody")
    assert len(body.children) == len(samples.CANDIDATE_1.steps) + 1
    assert "(a score of 60)" in text_of(table) and "(a score of 90)" in text_of(table)
    assert waterfall.title(samples.CANDIDATE_1) == "Why 79: match weights, to scale"


# ---------------------------------------------------------------------------------------------- preview and health


def test_the_preview_marks_what_changes_and_says_the_impact() -> None:
    preview = samples.CANDIDATE_1.preview
    block = impact.changes(preview, verb="link")
    assert "If you link: what changes in ORG-000123 (1)" in text_of(block)
    assert type(block).__name__ == "Details" and not prop(block, "open")  # collapsed in the panel
    changed = [c for c in walk(block) if prop(c, "className") == "mdm-preview-changed"]
    assert len(changed) == 1 and "Phone" in text_of(changed[0]) and "changes" in text_of(changed[0])
    body = next(c for c in walk(block) if type(c).__name__ == "Tbody")
    assert len(body.children) == 1  # the values that stay are in the compare table already
    assert "Every other golden value stays as it is." in text_of(block)
    line = impact.line(preview, verb="link")
    assert text_of(line) == "If you link: +1 cross-reference · golden phone changes · no ID retired"
    same = replace(preview, rows=tuple(replace(r, changed=False) for r in preview.rows))
    assert text_of(impact.changes(same)) == "No golden value of ORG-000123 changes."
    quiet = impact.render(Preview("ORG-000211", (), samples.IMPACT_NONE), verb="keep_apart")
    assert "If you keep them apart: no change to published records" in text_of(quiet)
    assert not [c for c in walk(quiet) if type(c).__name__ == "Table"]


def test_the_health_strip_reads_capped_counts_and_relative_times() -> None:
    strip = text_of(health.render(samples.HEALTH, NOW))
    for part in (
        "Last arrival 5 min ago · 291 read · 96% settled automatically",
        "Open 31",
        "Breaching 3",
        "In the tray 1",
        "Last commit 6 · 4 min ago",
    ):
        assert part in strip
    tree = health.render(samples.HEALTH, NOW)
    links = [c for c in walk(tree) if type(c).__name__ == "Anchor"]
    assert [prop(c, "href") for c in links] == ["/?view=breaching"]
    empty = health.render(samples.HEALTH_EMPTY, NOW)
    assert "No arrival run yet" in text_of(empty) and "No commit yet" in text_of(empty)
    assert not [c for c in walk(empty) if type(c).__name__ == "Anchor"]
    capped = replace(samples.HEALTH, open_tasks=capacity.COUNT_CAP, breaching=capacity.COUNT_CAP)
    assert "Open 999+" in text_of(health.render(capped, NOW))


# ---------------------------------------------------------------------------------------------- the decide pane


def _buttons(pane: Any) -> dict[str, Component]:
    found = {}
    for component in walk(pane):
        identity = getattr(component, "id", None)
        if isinstance(identity, dict) and identity.get("type") == ids.ACTION:
            assert identity["decision"] not in found, "one button per decision"
            found[identity["decision"]] = component
    return found


@pytest.mark.parametrize("case", samples.CASES, ids=lambda case: f"{case.shape}-{case.row.task_id[-4:]}")
def test_the_decide_pane_renders_every_case_shape(case) -> None:
    pane = decide.render(case, now=NOW)
    one(pane, ids.DECIDE_COMPARE)
    one(pane, ids.ACTION_REASONS)
    one(pane, ids.PREV_TASK)
    one(pane, ids.NEXT_TASK)
    panels = panels_of(pane)
    assert [p.id["master_id"] for p in panels] == [c.master_id for c in case.candidates]
    if panels:
        assert sum("mdm-hidden" not in classes(p) for p in panels) == 1
        assert [prop(p, "data-candidate-index") for p in panels] == [str(c.index) for c in case.candidates]
    assert not [c for c in walk(pane) if type(c).__name__ == "Store"]
    if case.notice:
        assert case.notice in text_of(pane)
    assert case.reason_text in text_of(pane)
    reasons = text_of(one(pane, ids.ACTION_REASONS))
    for button in _buttons(pane).values():
        if prop(button, "disabled"):
            assert prop(button, "aria-describedby") == ids.ACTION_REASONS
    for action in case.actions:
        if not action.enabled and action.why_not:
            assert action.why_not in reasons
    assert reasons.count("Your role") <= 1  # each reason once


def test_a_close_call_asks_for_a_choice_before_the_link() -> None:
    case = samples.CASE_CLOSE_CALL
    pane = decide.render(case, now=NOW)
    choice = one(pane, ids.CANDIDATE_CHOICE)
    assert choice.value is None
    options = [c for c in walk(choice) if type(c).__name__ == "Radio"]
    assert [o.label for o in options] == [
        "1 · ORG-000123 · 79 review",
        "2 · ORG-004410 · 79 review",
        "3 · ORG-000871 · 0 · kept apart by a rule",
    ]
    assert [prop(o, "data-candidate-index") for o in options] == ["1", "2", "3"]
    assert "Close call (79 and 79): choose 1, 2 or 3 before you link." in text_of(pane)
    assert "Nothing in the score separates them." in text_of(pane)
    link = _buttons(pane)["link"]
    assert link.children == "Choose 1, 2 or 3 to link" and prop(link, "aria-keyshortcuts") == "L"
    assert link.variant == "light" and prop(link, "disabled") is False  # it moves to the choice
    assert prop(pane, "data-needs-choice") == "yes" and prop(pane, "data-task-id") == case.row.task_id
    two = replace(case, candidates=case.candidates[:2])
    assert _buttons(decide.render(two, now=NOW))["link"].children == "Choose 1 or 2 to link"
    visible = [
        c for c in walk(pane) if "mdm-candidate-panel" in classes(c) and "mdm-hidden" not in classes(c)
    ]
    assert visible[0].id["master_id"] == "ORG-000123"

    chosen = decide.render(case, chosen="ORG-004410", now=NOW)
    assert one(chosen, ids.CANDIDATE_CHOICE).value == "ORG-004410"
    assert _buttons(chosen)["link"].children == "Link to ORG-004410"
    assert _buttons(chosen)["link"].variant == "filled" and not prop(chosen, "data-needs-choice")
    assert "Close call (" not in text_of(chosen)
    visible = [
        c for c in walk(chosen) if "mdm-candidate-panel" in classes(c) and "mdm-hidden" not in classes(c)
    ]
    assert [v.id["master_id"] for v in visible] == ["ORG-004410"]
    unknown = decide.render(case, chosen="ORG-999999", now=NOW)  # a choice that is not a candidate
    assert one(unknown, ids.CANDIDATE_CHOICE).value is None


def test_unavailable_actions_are_disabled_and_say_why_in_visible_text() -> None:
    pane = decide.render(samples.CASE_OWNER_VIEW, now=NOW)
    buttons = _buttons(pane)
    assert set(buttons) == {"link", "not_a_match", "claim"}
    for button in buttons.values():
        assert prop(button, "disabled") is True
        assert prop(button, "aria-describedby") == ids.ACTION_REASONS
    targets = [c for c in walk(pane) if "mdm-menu-target" in classes(c)]
    assert len(targets) == 2 and all(prop(t, "disabled") is True for t in targets)
    reasons = one(pane, ids.ACTION_REASONS)
    assert text_of(reasons) == "Your role, data owner, can see tasks but not decide them."


def test_a_staged_case_says_when_it_commits_and_offers_undo() -> None:
    pane = decide.render(samples.CASE_PERSON_STAGED, now=NOW)
    assert (
        "In the tray: Link crm:C000812 to ORG-000123. It commits at 12:00:48 UTC unless you undo it (U)."
        in text_of(pane)
    )
    buttons = _buttons(pane)
    assert "undo" in buttons and prop(buttons["undo"], "disabled") is False
    assert prop(buttons["link"], "disabled") is True
    assert "In the tray" in text_of(decide.header(samples.CASE_PERSON_STAGED, NOW))
    another = replace(samples.CASE_PERSON_STAGED, staged=replace(samples.STAGED, mine=False))
    assert "Another steward's decision waits in the tray" in text_of(decide.render(another, now=NOW))


def test_held_golden_and_information_shapes_say_what_they_offer() -> None:
    held = decide.render(samples.CASE_HELD_UPDATE, now=NOW)
    assert "If you approve: what changes in ORG-000419" in text_of(held)
    assert {"approve_update", "reject_update", "claim"} <= set(_buttons(held))
    assert "Another steward is working on this task until 12:09." in text_of(one(held, ids.ACTION_REASONS))
    assert "Claimed by Coordinating steward (persona)" in text_of(held)
    pair = decide.render(samples.CASE_GOLDEN_PAIR, now=NOW)
    assert "Merging needs a second steward to check it" in text_of(pair)
    assert text_of(pair).count("If you keep them apart") == 1
    assert set(_buttons(pair)) == {"keep_apart", "claim"} and not by_id(pair, ids.CANDIDATE_CHOICE)
    assert "Escalated" in text_of(pair)
    orphan = decide.render(samples.CASE_ORPHAN, now=NOW)
    assert _buttons(orphan)["keep_orphan"].children == "Keep as it is"
    assert prop(orphan, "data-open-record") == "/record/ORG-000930"
    held_new = decide.render(samples.CASE_HELD_NEW, now=NOW)
    assert set(_buttons(held_new)) == {"claim"}
    assert "Creating a golden record from a held arrival is not on screen yet." in text_of(held_new)
    information = decide.render(samples.CASE_INFORMATION, now=NOW)
    assert set(_buttons(information)) == {"claim"}


def test_a_reveal_is_rendered_inside_the_compare_table_only() -> None:
    pane = decide.render(samples.CASE_PERSON_STAGED, revealed=samples.REVEALED_PERSON, now=NOW)
    inside = one(pane, ids.DECIDE_COMPARE)
    clear = ("Kaewyn", "Bromdale", "1990-04-17", "kaewyn.bromdale7@example.org")
    assert all(value in text_of(inside) for value in clear)
    outside = "\n".join(strings_outside(pane, ids.DECIDE_COMPARE))
    assert "Candidate 1: " in outside and "PER-000451" in outside
    assert not [value for value in clear if value in outside]


def test_the_subject_links_to_its_records() -> None:
    links = decide.subject_links(samples.ROW_GOLDEN_PAIR)
    assert [prop(a, "href") for a in links if type(a).__name__ == "Anchor"] == [
        "/record/ORG-000211",
        "/record/ORG-000388",
    ]
    (source,) = decide.subject_links(samples.ROW_CLOSE_CALL)
    assert prop(source, "href") == "/source/crm/C000812"


# ---------------------------------------------------------------------------------------------- the grid


def test_grid_rows_carry_codes_ids_and_masked_text_only() -> None:
    staged = inbox.grid_row(samples.ROW_PERSON_REVIEW, NOW)
    assert staged["title"] == "K*** B***" and staged["due"] == "Staged by you" and staged["staged"] is True
    assert staged["staged_ms"] == int(samples.ROW_PERSON_REVIEW.staged.deadline.timestamp() * 1000)
    assert staged["breaching"] is False and staged["claimed"] == "" and staged["claimed_other"] is False
    close = inbox.grid_row(samples.ROW_CLOSE_CALL, NOW)
    assert (close["band"], close["band_text"], close["due"]) == ("review", "79 review", "7 h 40 min")
    assert close["entity_label"] == "Organisation"
    assert inbox.grid_row(samples.ROW_CLOSE_CALL, NOW, entity_shown=False)["entity_label"] == ""
    held_row = inbox.grid_row(samples.ROW_HELD_UPDATE, NOW)
    assert (
        held_row["claimed_other"] is True and held_row["band_text"] == ""
    )  # approved or rejected, not scored
    kept = inbox.grid_row(replace(samples.ROW_CLOSE_CALL, score=95.2, band="auto", kept_apart=True), NOW)
    assert (kept["band_text"], kept["band"]) == ("95 · kept apart by a rule", "distinct")
    assert inbox.grid_row(samples.ROW_ORPHAN, NOW)["claimed"] == "snoozed"
    assert inbox.grid_row(samples.ROW_GOLDEN_PAIR, NOW)["claimed"] == "escalated"
    overdue = replace(samples.ROW_CLOSE_CALL, due_at=NOW - timedelta(hours=2))
    assert inbox.grid_row(overdue, NOW)["due"] == "Breached 2 h ago"
    for row in samples.ROWS:
        values = inbox.grid_row(row, NOW)
        assert values["task_id"] == row.task_id
        assert all(isinstance(v, (str, bool, int, type(None))) for v in values.values())
        assert not any("<" in v for v in values.values() if isinstance(v, str))


def test_the_grid_neither_sorts_nor_filters_and_never_takes_cell_focus() -> None:
    grid = inbox._grid([inbox.grid_row(samples.ROW_CLOSE_CALL, NOW)], samples.ROW_CLOSE_CALL.task_id)
    assert grid.defaultColDef["sortable"] is False and grid.defaultColDef["filter"] is False
    assert not [c for c in grid.columnDefs if c.get("sortable") or c.get("filter")]
    assert grid.getRowId == "params.data.task_id"
    assert grid.dashGridOptions["suppressCellFocus"] is True
    assert grid.dashGridOptions["suppressHeaderFocus"] is True  # Tab never loops over the headers
    assert grid.dashGridOptions["rowSelection"]["mode"] == "singleRow"
    assert grid.selectedRows == {"ids": [samples.ROW_CLOSE_CALL.task_id]}
    assert set(grid.rowClassRules) == {"mdm-staged", "mdm-breaching", "mdm-claimed-other"}
    renderers = {c.get("cellRenderer") for c in grid.columnDefs}
    assert renderers == {"MdmTask", "MdmDue"}


# ---------------------------------------------------------------------------------------------- the pure helpers


def test_the_query_the_page_label_and_the_selection() -> None:
    entities = ("organisation", "person")
    assert inbox.parse_query(
        {"view": "team", "kind": "review", "entity": "person", "task": "TSK-1a"}, entities
    ) == {
        "view": "team",
        "kind": "review",
        "entity": "person",
        "task": "TSK-1a",
    }
    assert inbox.parse_query({"view": "x", "kind": "y", "entity": "z", "task": "ORG-1"}, entities) == {
        "view": "mine",
        "kind": None,
        "entity": "",
        "task": None,
    }
    assert inbox.query_from_search("?view=team&kind=held&task=TSK-1a&tab=x&view=mine") == {
        "view": "team",
        "kind": "held",
        "task": "TSK-1a",
    }
    assert inbox.address_query({"search": "?view=breaching", "entity": "person", "path": "/"}, entities) == {
        "view": "breaching",
        "kind": None,
        "entity": "person",
    }
    kept = {"view": "mine", "kind": None, "entity": ""}
    assert inbox.new_query({"search": "", "entity": "", "path": "/"}, kept, entities) is None  # unchanged
    assert (
        inbox.new_query({"search": "?view=team", "entity": "", "path": "/record/X"}, kept, entities) is None
    )
    assert inbox.new_query({"search": "?view=team", "entity": "", "path": "/"}, kept, entities) == {
        "view": "team",
        "kind": None,
        "entity": "",
    }
    assert inbox.new_query(None, kept, entities) is None
    assert inbox.page_label({"view": "mine"}, 0, 31) == "My queue: 1–31"
    assert inbox.page_label({"view": "team", "kind": "review"}, 1, 50) == "Team · Review: 51–100"
    assert inbox.page_label({"view": "mine"}, 0, 0) == "My queue: nothing waiting"
    rows = samples.ROWS[:3]
    assert inbox.select_after_load(rows, None, rows[1].task_id) == rows[1].task_id
    assert inbox.select_after_load(rows, None, "TSK-gone") == rows[0].task_id
    assert inbox.select_after_load(rows, "next", rows[1].task_id) == rows[0].task_id
    assert inbox.select_after_load(rows, "prev", None) == rows[-1].task_id
    assert inbox.select_after_load((), None, None) is None
    assert inbox.cursor_of(["2026-09-27T12:00:00+00:00", "TSK-1"]) == ("2026-09-27T12:00:00+00:00", "TSK-1")
    assert inbox.cursor_of("nonsense") is None


def test_the_requested_action_and_the_decision_it_stages() -> None:
    for action in ("link", "not_a_match", "approve", "reject", "claim", "undo", "keep_apart", "keep_orphan"):
        assert inbox.requested_action({"action": action, "n": 1}) == action
    assert inbox.requested_action({"action": "snooze:4"}) == "snooze:4"
    assert inbox.requested_action({"action": "escalate:second_opinion"}) == "escalate:second_opinion"
    for bad in ({"action": "snooze:3"}, {"action": "escalate:because"}, {"action": "drop"}, None, "link"):
        assert inbox.requested_action(bad) is None
    assert inbox.decision_for("not_a_match", "source") == "not_a_match"
    assert inbox.decision_for("not_a_match", "golden_pair") == "keep_apart"
    assert inbox.decision_for("approve", "held_update") == "approve_update"
    assert inbox.decision_for("approve", "golden") == "keep_orphan"
    assert inbox.decision_for("reject", "held_update") == "reject_update"
    assert inbox.decision_for("reject", "source") is None
    assert inbox.decision_for("keep_apart", None) == "keep_apart"
    result = inbox.ActResult(
        True, samples.ROW_CLOSE_CALL, False, True, touched=samples.ROW_CLOSE_CALL.task_id
    )
    stored = inbox.result_store(result, samples.ROW_CLOSE_CALL.task_id, {"n": 4}, NOW)
    assert stored["n"] == 5 and stored["advance"] is True and stored["remove"] is False
    assert stored["row"]["task_id"] == samples.ROW_CLOSE_CALL.task_id


def test_the_callbacks_register_on_their_own_app() -> None:
    app = Dash(__name__)
    inbox.register(app)
    outputs = [str(callback["output"]) for callback in app._callback_list]
    assert any("decide-pane.children" in output for output in outputs)
    assert any("act-result.data" in output for output in outputs)
    for callback in app._callback_list:
        if "@" in callback["output"]:
            assert callback["prevent_initial_call"] is True, callback["output"]


def test_the_skeleton_holds_every_id_the_callbacks_name() -> None:
    skeleton = inbox.skeleton()
    for wanted in (
        ids.INBOX,
        ids.HEALTH_STRIP,
        ids.PAGE_LABEL,
        ids.INBOX_GRID,
        ids.PAGE_PREV,
        ids.PAGE_NEXT,
        ids.DECIDE_PANE,
        ids.DECIDE_COMPARE,
        ids.CANDIDATE_CHOICE,
        ids.INBOX_QUERY,
        ids.INBOX_CURSOR,
        ids.PAGE_AFTER,
        ids.SELECTED_TASK,
        ids.SELECTED_CANDIDATE,
        ids.NEXT_HINT,
        ids.CASE_VERSION,
        ids.CASE_STAMP,
        ids.ACT_RESULT,
        inbox.ADDRESS,
        inbox.SETTLED_HERE,
        inbox.HEALTH_POLL,
        inbox.ACT_REQUEST,
        ids.reveal(ids.REVEAL_CONFIRM, "decide"),
        ids.reveal(ids.REVEAL_MODAL, "decide"),
    ):
        one(skeleton, wanted)


# ---------------------------------------------------------------------------------------------- on a real hub


@pytest.fixture
def world() -> Iterator[Hub]:
    """The mini world on an in-memory DuckDB with the Organisation close call and the Person review."""
    settings = base_settings()
    store = open_store(settings)
    store.init_schema(create_landing=True)
    hub = open_hub(settings, store, "duckdb")
    try:
        helpers.workbench_world(hub)
        helpers.organisation_close_call(hub)
        helpers.person_review(hub)
        yield hub
    finally:
        hub.close()
        store.close()


def close_call(hub: Hub):
    return helpers.task_of(hub, kind="review", source=SourceKey("crm", "C0900003"))


def review(hub: Hub):
    return helpers.task_of(hub, kind="review", source=SourceKey("crm", "C1900004"))


def window_passed(hub: Hub) -> None:
    """Moves the tray's clock past every staged decision's deadline."""
    offset = timedelta(seconds=hub.settings.undo_seconds + 1)
    current = hub.tray.clock
    hub.tray.clock = lambda: current() + offset


def staged_entries(hub: Hub, ctx) -> list:
    return [e for e in hub.tray.entries(actor=ctx.actor) if e.status == "staged"]


def test_the_layout_shows_a_page_of_masked_rows_and_selects_the_first(world: Hub) -> None:
    ctx = context.for_test(world)
    page = inbox.layout(ctx, {"view": "team"})
    root = one(page, ids.INBOX)
    assert prop(root, "data-mdm-keys") == "on"
    grid = one(page, ids.INBOX_GRID)
    names = [row["task_id"] for row in grid.rowData]
    assert {close_call(world).task_id, review(world).task_id} <= set(names)
    person = next(row for row in grid.rowData if row["task_id"] == review(world).task_id)
    held = helpers.person_payload(4)
    assert held["given_name"] not in person["title"] and "***" in person["title"]
    assert one(page, ids.SELECTED_TASK).data == names[0]
    assert grid.selectedRows == {"ids": [names[0]]}
    assert one(page, ids.INBOX_QUERY).data == {"view": "team", "kind": None, "entity": ""}
    assert one(page, ids.PAGE_LABEL).children == f"Team: 1–{len(names)}"
    assert "Open" in text_of(one(page, ids.HEALTH_STRIP))
    linked = inbox.layout(ctx, {"view": "team", "task": review(world).task_id})
    assert one(linked, ids.SELECTED_TASK).data == review(world).task_id
    consumer = inbox.layout(context.for_test(world, role="consumer"), {})
    assert "Your role, consumer, decides no tasks" in text_of(consumer)
    assert not by_id(consumer, ids.INBOX_GRID)
    assert inbox.load_page(context.for_test(world, role="consumer"), {"view": "team"}, None).rows == ()


def stamp(ctx, task_id: str) -> dict:
    """CASE_STAMP of the task's case, as the pane on screen would carry it."""
    found = inbox.case_view(ctx, task_id)[1]
    assert found is not None
    return found


def test_the_case_renders_with_its_stamp_and_a_decided_task_says_so(world: Hub) -> None:
    ctx = context.for_test(world)
    task = close_call(world)
    pane, found = inbox.case_view(ctx, task.task_id)
    assert found["task_id"] == task.task_id and found["shape"] == "source" and found["event_id"]
    assert found["task_version"] and found["default"] is None  # a close call names no default
    assert set(found) == {"task_id", "event_id", "task_version", "shape", "default"}
    assert one(pane, ids.CANDIDATE_CHOICE).value is None
    assert len(panels_of(pane)) == 2
    empty, none = inbox.case_view(ctx, None)
    assert none is None and decide.NOTHING_SELECTED in text_of(empty)
    gone, gone_stamp = inbox.case_view(ctx, "TSK-0000000000000000")
    assert decide.DECIDED in text_of(gone) and gone_stamp is None
    # an empty view says so, and where the open tasks are
    nothing, _ = inbox.case_view(ctx, None, query={"view": "escalated", "kind": None, "entity": ""})
    assert "Nothing is escalated. Team has " in text_of(nothing)
    link = next(c for c in walk(nothing) if type(c).__name__ == "Anchor")
    assert prop(link, "href") == "/?view=team"


def test_an_action_waits_for_the_case_on_screen(world: Hub) -> None:
    ctx = context.for_test(world)
    task = close_call(world)
    person = review(world)
    for seen in (None, stamp(ctx, person.task_id), {"task_id": task.task_id}):
        result = inbox.act(ctx, "not_a_match", task_id=task.task_id, stamp=seen if seen else None)
        if seen is not None and seen.get("task_id") == task.task_id:
            continue  # a stamp of this task with nothing seen: the service refuses it (below)
        assert result.advance is False and result.bump_tray is False
        assert result.notices[0]["message"] == messages.sentence_for("case_loading")
        assert result.notices[0]["id"] == "n-case_loading"  # pressed again, it does not stack
    bare = inbox.act(ctx, "not_a_match", task_id=task.task_id, stamp={"task_id": task.task_id})
    assert bare.advance is False and bare.bump_case is True  # nothing seen: refused as out of date
    assert staged_entries(world, ctx) == []
    table, note = inbox.reveal_view(ctx, task.task_id, "deciding_task", stamp(ctx, person.task_id))
    assert table is None and note["message"] == messages.sentence_for("case_loading")


def test_a_close_call_links_only_after_a_choice_and_undo_takes_it_back(world: Hub) -> None:
    ctx = context.for_test(world)
    task = close_call(world)
    case = world.decisions.case(task.task_id, actor=ctx.actor)
    shown = stamp(ctx, task.task_id)
    refused = inbox.act(ctx, "link", task_id=task.task_id, stamp=shown)
    assert refused.advance is False and refused.bump_tray is False
    assert refused.notices[0]["message"] == messages.sentence_for("close_call")
    assert refused.notices[0]["title"] == "Choose a candidate first"
    assert staged_entries(world, ctx) == []

    second = case.candidates[1].master_id
    staged = inbox.act(ctx, "link", task_id=task.task_id, candidate=second, stamp=shown)
    assert staged.advance is True and staged.bump_tray is True and staged.remove is False
    assert staged.row is not None and staged.row.staged.label == f"Link crm:C0900003 to {second}"
    assert staged.notices[0]["color"] == "teal" and "press U to undo it" in staged.notices[0]["message"]
    assert [e.label for e in staged_entries(world, ctx)] == [f"Link crm:C0900003 to {second}"]
    assert world.inbox.row(task.task_id, actor=ctx.actor).claimed_by == "you"  # the first decision claims

    again = inbox.act(ctx, "link", task_id=task.task_id, candidate=second, stamp=shown)
    assert "already in the tray" in again.notices[0]["message"] and again.bump_case is True

    undone = inbox.act(ctx, "undo", task_id=task.task_id)
    assert undone.bump_tray and undone.bump_case and undone.touched == task.task_id
    assert undone.row is not None and undone.row.staged is None
    assert staged_entries(world, ctx) == []
    nothing = inbox.act(ctx, "undo", task_id=task.task_id)
    assert nothing.notices[0]["message"] == "Nothing of yours is waiting in the tray."

    inbox.act(ctx, "link", task_id=task.task_id, candidate=second, stamp=shown)
    last = inbox.act(ctx, "undo", task_id=review(world).task_id)  # another task selected: the last entry
    assert last.touched == task.task_id and last.bump_case is False and staged_entries(world, ctx) == []


def test_a_case_out_of_date_at_staging_says_so_and_stages_nothing(world: Hub) -> None:
    ctx = context.for_test(world)
    person = review(world)
    old = {**stamp(ctx, person.task_id), "event_id": "EV-0000000000000000"}
    stale = inbox.act(ctx, "link", task_id=person.task_id, stamp=old)
    assert stale.advance is False and stale.bump_case is True
    assert stale.notices[0]["message"] == messages.STAGE_RECORD_CHANGED
    assert staged_entries(world, ctx) == []


def test_keys_stage_the_decision_the_case_offers(world: Hub) -> None:
    ctx = context.for_test(world)
    person = review(world)
    result = inbox.act(ctx, "not_a_match", task_id=person.task_id, stamp=stamp(ctx, person.task_id))
    assert result.advance and [e.decision for e in staged_entries(world, ctx)] == ["not_a_match"]
    call = close_call(world)
    wrong = inbox.act(ctx, "reject", task_id=call.task_id, stamp=stamp(ctx, call.task_id))
    assert wrong.notices[0]["message"] == "This task does not offer that decision."

    helpers.golden_pair(world)
    pair = helpers.task_of(world, kind="possible_duplicate")
    assert inbox.act(ctx, "not_a_match", task_id=pair.task_id, stamp=stamp(ctx, pair.task_id)).advance
    source = helpers.standalone_organisation(world)
    helpers.held_name_change(world, source, "Quillmere Optical Ltd", at=utcnow())
    held = helpers.task_of(world, kind="held", source=source)
    assert inbox.act(ctx, "approve", task_id=held.task_id, stamp=stamp(ctx, held.task_id)).advance
    decisions = sorted(e.decision for e in staged_entries(world, ctx))
    assert decisions == ["approve_update", "keep_apart", "not_a_match"]
    inbox.act(ctx, "undo", task_id=held.task_id)
    assert inbox.act(ctx, "reject", task_id=held.task_id, stamp=stamp(ctx, held.task_id)).advance
    assert "reject_update" in {e.decision for e in staged_entries(world, ctx)}


def test_claim_snooze_and_escalate(world: Hub) -> None:
    ctx = context.for_test(world)
    task = close_call(world)
    shown = stamp(ctx, task.task_id)
    claimed = inbox.act(ctx, "claim", task_id=task.task_id, stamp=shown)
    assert claimed.bump_case and claimed.row.claimed_by == "you" and not claimed.advance
    assert claimed.notices[0]["message"].startswith("Claimed for 10 minutes.")
    other = context.for_test(world, role="coordinating_steward")
    taken = inbox.act(
        other,
        "link",
        task_id=task.task_id,
        candidate=world.decisions.case(task.task_id, actor=other.actor).candidates[0].master_id,
        stamp=stamp(other, task.task_id),
    )
    assert "Another steward is working on this task" in taken.notices[0]["message"] and taken.bump_case
    assert taken.notices[0]["title"] == "Another steward has it"

    escalated = inbox.act(ctx, "escalate:second_opinion", task_id=task.task_id, stamp=shown)
    assert escalated.row.escalated and escalated.bump_case and not escalated.remove
    assert "needs a second opinion" in escalated.notices[0]["message"]

    kept = inbox.act(ctx, "snooze:4", task_id=task.task_id, view="snoozed", stamp=shown)
    assert kept.remove is False and kept.row.snoozed_until is not None and kept.bump_case
    person = review(world)
    seen = stamp(ctx, person.task_id)
    left = inbox.act(ctx, "snooze:1", task_id=person.task_id, view="mine", stamp=seen)
    assert left.remove is True and left.advance is True and left.row is None
    assert inbox.act(ctx, "snooze:3", task_id=person.task_id, stamp=seen).notices[0]["message"] == (
        "Choose one of the offered times."
    )
    assert inbox.act(ctx, "escalate:because", task_id=person.task_id, stamp=seen).notices[0]["message"] == (
        "Choose one of the offered reasons."
    )
    assert inbox.act(ctx, "claim", task_id=None).notices[0]["message"] == "Choose a task in the list first."


def test_a_data_owner_sees_tasks_but_cannot_decide_them(world: Hub) -> None:
    owner = context.for_test(world, role="data_owner")
    task = close_call(world)
    pane, shown = inbox.case_view(owner, task.task_id)
    reasons = text_of(one(pane, ids.ACTION_REASONS))
    assert "Your role, data owner, can see tasks but not decide them." in reasons
    assert all(prop(b, "disabled") for b in _buttons(pane).values())
    refused = inbox.act(owner, "link", task_id=task.task_id, candidate="ORG-000001", stamp=shown)
    assert refused.notices[0]["message"].startswith("Your role, data owner, cannot")
    assert world.tray.entries(actor=owner.actor) == ()


def test_a_reveal_needs_a_reason_and_is_rendered_once(world: Hub) -> None:
    ctx = context.for_test(world)
    task = review(world)
    held = helpers.person_payload(4)
    shown = stamp(ctx, task.task_id)
    table, note = inbox.reveal_view(ctx, task.task_id, None, shown)
    assert table is None and note["message"] == reveal.REASON_REQUIRED
    table, note = inbox.reveal_view(ctx, task.task_id, "deciding_task", shown)
    assert table is not None and held["given_name"] in text_of(table)
    assert note["message"].startswith("Values shown; logged (")
    assert held["given_name"] not in str(world.decisions.case(task.task_id, actor=ctx.actor))
    pane, _ = inbox.case_view(ctx, task.task_id)
    assert held["given_name"] not in str(pane.to_plotly_json())
    consumer = context.for_test(world, role="consumer")
    table, note = inbox.reveal_view(consumer, task.task_id, "deciding_task", shown)
    assert table is None and note["color"] == "yellow"


def test_a_settlement_takes_committed_rows_out_and_refreshes_the_rest(world: Hub) -> None:
    ctx = context.for_test(world)
    task = close_call(world)
    person = review(world)
    target = world.decisions.case(task.task_id, actor=ctx.actor).candidates[1].master_id
    inbox.act(ctx, "link", task_id=task.task_id, candidate=target, stamp=stamp(ctx, task.task_id))
    inbox.act(ctx, "not_a_match", task_id=person.task_id, stamp=stamp(ctx, person.task_id))
    inbox.act(ctx, "undo", task_id=person.task_id)
    window_passed(world)
    assert world.tray.flush().committed == 1
    assert helpers.master_of(world, "organisation", "crm", "C0900003") == target
    since = world.store.last_commit_version() - 1
    assert target in {c.master_id for c in world.feed.read(since).changes}  # the feed shows the commit
    settled = [
        {"entry_id": "TR-a", "task_id": task.task_id, "status": "committed", "outcome": "committed"},
        {"entry_id": "TR-b", "task_id": person.task_id, "status": "undone", "outcome": "undone"},
        {"entry_id": "TR-c", "task_id": "TSK-elsewhere", "status": "committed", "outcome": "committed"},
    ]
    on_page = [task.task_id, person.task_id]
    transaction, touched = inbox.settled_updates(ctx, settled, on_page, task.task_id)
    assert touched is True and transaction["async"] is False
    assert transaction["remove"] == [{"task_id": task.task_id}]
    assert [row["task_id"] for row in transaction["update"]] == [person.task_id]
    assert transaction["update"][0]["staged"] is False
    quiet, touched = inbox.settled_updates(ctx, settled[2:], on_page, task.task_id)
    assert quiet is None and touched is False
    assert inbox.settled_updates(ctx, None, on_page, None) == (None, False)


def test_pages_move_forward_and_back_by_their_cursors(world: Hub, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = context.for_test(world)
    monkeypatch.setattr(capacity, "INBOX_PAGE", 1)
    monkeypatch.setattr(
        inbox,
        "load_page",
        lambda c, query, after: c.hub.inbox.page("team", actor=c.actor, after=after, limit=1),
    )
    query = {"view": "team", "kind": None, "entity": ""}
    rows, selected, label, prev_off, next_off, after, failure = inbox.load(ctx, query, None, None)
    assert failure is None and len(rows) == 1 and prev_off is True and next_off is False and after
    assert label == "Team: 1–1" and selected == {"ids": [rows[0]["task_id"]]}
    second = inbox.load(ctx, query, {"stack": [after], "moved": "next"}, rows[0]["task_id"])
    assert second[0][0]["task_id"] != rows[0]["task_id"] and second[2] == "Team: 2–2" and second[3] is False
    back = inbox.load(ctx, query, {"stack": [], "moved": "prev"}, second[0][0]["task_id"])
    assert back[1] == {"ids": [rows[0]["task_id"]]}


def test_an_action_the_role_may_take_but_the_case_disables_still_says_why() -> None:
    action = Action(
        "approve_update", "Approve the update", "A", False, "This record's update is no longer held."
    )
    button = decide.action_button(action)
    assert prop(button, "disabled") is True and prop(button, "aria-describedby") == ids.ACTION_REASONS
    assert button.children == "Approve the update"
