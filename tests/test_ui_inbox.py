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
from pathlib import Path
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
from mdm.ui.components import breaker, compare, decide, health, impact, reveal, waterfall
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
    assert f"Against {compare.reference_words(samples.CASE_CLOSE_CALL.columns[0])}" in text_of(table)
    assert compare.reference_words("Record · finance:F000160") == "the record · finance:F000160"
    assert compare.reference_words("ORG-000211 · golden record") == "ORG-000211 · golden record"
    assert not by_id(table, ids.reveal(ids.REVEAL_OPEN, "decide"))  # nothing masked, nothing to show
    # every value names its column, which the stacked layout of a narrow screen shows beside it
    assert all(prop(c, "data-column") in samples.CASE_CLOSE_CALL.columns for c in cells)


def test_only_a_disagreement_is_tinted() -> None:
    # two candidates: the ≠ cells carry the tint (`mdm-compare-multi`), never a whole row, so a candidate
    # that agrees is not painted as disagreeing
    table = compare.render(samples.CASE_CLOSE_CALL.columns, samples.COMPARE_CLOSE_CALL)
    rows = [c for c in walk(table) if type(c).__name__ == "Tr" and "mdm-compare-other" not in classes(c)]
    assert not [r for r in rows if "mdm-row-disagree" in classes(r)]
    multi = [c for c in walk(table) if "mdm-compare-multi" in classes(c)]
    assert multi and all({"mdm-compare", "mdm-compare-wide"} <= classes(c) for c in multi)
    assert [c for c in walk(table) if type(c).__name__ == "Td" and "mdm-disagree" in classes(c)]
    # one record against the reference: its row is tinted where they disagree, and only there
    single = [
        replace(row, values=row.values[:2], agreement=row.agreement[:1]) for row in samples.COMPARE_CLOSE_CALL
    ]
    one_table = compare.render(samples.CASE_CLOSE_CALL.columns[:2], single)
    body = [c for c in walk(one_table) if type(c).__name__ == "Tr" and "mdm-compare-other" not in classes(c)][
        1:
    ]
    tinted = {text_of(r.children[0]) for r in body if "mdm-row-disagree" in classes(r)}
    wanted = {
        text_of(compare._label(row)) for row in compare.main_rows(single)[0] if "disagree" in row.agreement
    }
    assert tinted == wanted and tinted
    assert not [c for c in walk(one_table) if "mdm-compare-multi" in classes(c)]
    # the cells stay neutral: the class and the symbol carry the agreement, no cell is filled by markup
    assert not [c for c in walk(table) if (getattr(c, "style", None) or {}).get("background")]
    # a long value breaks at its parts, not inside one
    address = compare.breakable("https://www.mondune-trading.example/")
    assert "".join(p for p in address if isinstance(p, str)) == "https://www.mondune-trading.example/"
    assert sum(1 for p in address if type(p).__name__ == "Wbr") >= 4


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
    # the axis: only the band edges, as the scores they stand for (each row keeps its signed weight)
    ticks = [text_of(c) for c in walk(figure) if "mdm-wf-tick" in classes(c)]
    assert ticks == ["60", "90"]
    # a wide scale draws the edges close together: one centred tick, never "6090"
    close = waterfall.axis_row(candidate, low - 200.0, high + 200.0)
    assert [text_of(c) for c in walk(close) if "mdm-wf-tick" in classes(c)] == ["60–90"]
    assert waterfall.AXIS_LABEL in text_of(figure) and "weight · score" not in text_of(figure)
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
    assert strip == (
        "Last arrival 5 min ago · 291 read · 96% settled automatically · Open 31 · Breaching 3 · "
        "In the tray 1 · Last commit 6, 4 min ago"
    )
    tree = health.render(samples.HEALTH, NOW)
    links = [c for c in walk(tree) if type(c).__name__ == "Anchor"]
    assert [prop(c, "href") for c in links] == ["/?view=breaching"]
    empty = health.render(samples.HEALTH_EMPTY, NOW)
    assert text_of(empty) == "No arrival run yet · Open 0 · Breaching 0 · In the tray 0 · No commit yet"
    assert not [c for c in walk(empty) if type(c).__name__ == "Anchor"]
    capped = replace(samples.HEALTH, open_tasks=capacity.COUNT_CAP, breaching=capacity.COUNT_CAP)
    assert "Open 999+ · Breaching 999+" in text_of(health.render(capped, NOW))


# ---------------------------------------------------------------------------------------------- the decide pane


def _buttons(pane: Any) -> dict[str, Component]:
    found = {}
    for component in walk(pane):
        identity = getattr(component, "id", None)
        if isinstance(identity, dict) and identity.get("type") == ids.ACTION:
            assert identity["decision"] not in found, "one button per decision"
            found[identity["decision"]] = component
    return found


@pytest.mark.parametrize(
    "case",
    samples.CASES + samples.CHECKPOINT_CASES,
    ids=lambda case: f"{case.shape}-{case.row.task_id[-4:]}",
)
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


def test_the_pane_fills_one_button_and_keeps_the_work_actions_quiet() -> None:
    pane = decide.render(samples.CASE_CLOSE_CALL, chosen="ORG-004410", now=NOW)
    buttons = _buttons(pane)
    assert buttons["link"].variant == "filled" and buttons["not_a_match"].variant == "default"
    assert (buttons["claim"].variant, buttons["claim"].color) == ("subtle", "gray")
    targets = [c for c in walk(pane) if "mdm-menu-target" in classes(c)]
    assert len(targets) == 2 and all((t.variant, t.color) == ("subtle", "gray") for t in targets)
    moves = [one(pane, ids.PREV_TASK), one(pane, ids.NEXT_TASK)]
    assert all(m.variant == "subtle" for m in moves)
    filled = [c for c in walk(pane) if getattr(c, "variant", None) == "filled"]
    assert filled == [buttons["link"]], "the decision that changes records is the one filled button"
    held = _buttons(decide.render(samples.CASE_HELD_UPDATE, now=NOW))
    assert (held["approve_update"].variant, held["reject_update"].variant) == ("filled", "default")
    # quiet: a measurement or a dispute nudges no answer, so no decision is filled
    quiet = decide.actions(samples.CASE_CLOSE_CALL, "ORG-004410", quiet=True)
    assert not [c for c in walk(quiet) if getattr(c, "variant", None) in ("filled", "light")]
    assert _buttons(quiet)["claim"].variant == "subtle"
    for shape in decide.QUIET_SHAPES:  # the pane says so, and the browser keeps the link outlined
        shaped = decide.render(replace(samples.CASE_CLOSE_CALL, shape=shape), chosen="ORG-004410", now=NOW)
        assert prop(shaped, "data-quiet") == "yes"
        assert not [c for c in walk(shaped) if getattr(c, "variant", None) == "filled"]
    assert prop(pane, "data-quiet") is None


def test_the_header_is_the_title_with_its_band_and_one_muted_line() -> None:
    head = decide.header(samples.CASE_CLOSE_CALL, NOW)
    assert not [c for c in walk(head) if type(c).__name__ == "Badge"]  # plain words, no pills
    headline = next(c for c in walk(head) if "mdm-decide-headline" in classes(c))
    assert [type(c).__name__ for c in headline.children] == ["H2", "Span"]
    assert classes(headline.children[1]) == {"mdm-band", "mdm-band-review"}
    meta = next(c for c in walk(head) if "mdm-decide-meta" in classes(c))
    row = samples.CASE_CLOSE_CALL.row
    assert text_of(meta) == f"{row.kind_label} · crm:C000812 · Due in 7 h 40 min · Not claimed"


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


def test_a_row_names_its_kind_only_in_a_mixed_list_and_titles_its_second_line() -> None:
    row = samples.ROW_CLOSE_CALL
    mixed = inbox.grid_row(row, NOW)
    assert mixed["kind_label"] == row.kind_label
    assert inbox.grid_row(row, NOW, kind_shown=False)["kind_label"] == ""
    assert mixed["second_title"] == f"79 review · {row.reason} · {row.suggestion} · Organisation"
    alone = inbox.grid_row(row, NOW, entity_shown=False)
    assert alone["second_title"] == f"79 review · {row.reason} · {row.suggestion}"
    held = inbox.grid_row(samples.ROW_HELD_UPDATE, NOW)
    assert not held["second_title"].startswith(" · ")


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


# ---------------------------------------------------------------------------------------------- the checkpoint


def _anchors(tree: Any) -> list[str]:
    """Every link's address in a tree."""
    return [str(prop(c, "href")) for c in walk(tree) if type(c).__name__ in ("Anchor", "A", "NavLink")]


def _decision_buttons(pane: Any) -> dict[str, Component]:
    return {name: button for name, button in _buttons(pane).items() if name in inbox.DIRECT}


BLIND_CASES = (samples.CASE_BLIND, samples.CASE_BLIND_NONE, samples.CASE_BLIND_PAIR)


@pytest.mark.parametrize("case", BLIND_CASES, ids=lambda case: f"{case.shape}-{case.row.task_id[-4:]}")
def test_the_blind_pane_shows_nothing_of_the_first_decision(case) -> None:
    # even a case that came with a score, a candidate, a preview and a paused band shows none of them blind
    leaky = replace(
        case,
        row=replace(case.row, score=97.4, band="auto"),
        candidates=samples.CANDIDATES,
        preview=samples.PREVIEW_APPROVE,
        paused=samples.BREAKER_AGREEMENT,
    )
    for shown in (case, leaky):
        pane = decide.render(shown, now=NOW)
        found = [
            c for c in walk(pane) if classes(c) & {"mdm-band", "mdm-why", "mdm-flip", "mdm-candidate-impact"}
        ]
        assert found == []
        assert panels_of(pane) == [] and by_id(pane, ids.WHY_SECTION) == []
        assert not [c for c in walk(pane) if "mdm-shape-preview" in classes(c) or "mdm-impact" in classes(c)]
        text = text_of(pane)
        for hidden in ("97", "automatic", "review band", "What would flip it", "Candidate ", "paused"):
            assert hidden not in text, hidden
        assert _anchors(pane) == []  # no record or source view: either would show the placement
        assert prop(pane, "data-open-record") is None  # Enter opens nothing
        assert prop(pane, "data-blind") == "yes" and prop(pane, "data-quiet") == "yes"
        meta = next(c for c in walk(pane) if "mdm-decide-meta" in classes(c))
        assert text_of(meta).startswith(f"Quality sample · {decide.subject_text(case.row)} · Due in ")
        assert case.reason_text in text
        # no tooltip, title or data attribute says more than the IDs on screen
        for c in walk(pane):
            assert getattr(c, "title", None) is None or "97" not in str(c.title)


def test_a_blind_review_asks_for_a_choice_and_never_chooses_by_default() -> None:
    case = samples.CASE_BLIND
    pane = decide.render(case, now=NOW)
    choice = one(pane, ids.CANDIDATE_CHOICE)
    assert choice.value is None and "mdm-blind-choice" in classes(choice)
    options = [c for c in walk(choice) if type(c).__name__ == "Radio"]
    assert [o.label for o in options] == ["1 · ORG-000123", "2 · ORG-000871", "3 · ORG-004410"]
    assert [prop(o, "data-candidate-index") for o in options] == ["1", "2", "3"]
    assert [prop(o, "data-master-id") for o in options] == ["ORG-000123", "ORG-000871", "ORG-004410"]
    assert prop(pane, "data-needs-choice") == "yes"
    buttons = _decision_buttons(pane)
    assert set(buttons) == {"blind_link", "blind_none"}  # one button follows the choice
    assert buttons["blind_link"].children == "Choose 1, 2 or 3 first"
    assert prop(buttons["blind_link"], "aria-keyshortcuts") == "L"
    assert prop(buttons["blind_link"], "disabled") is False  # L moves to the choice
    assert buttons["blind_none"].children == "Belongs to none of these"
    assert prop(buttons["blind_none"], "aria-keyshortcuts") == "N"
    one_choice = replace(case, choices=case.choices[:1], actions=case.actions[:1] + case.actions[3:])
    assert _decision_buttons(decide.render(one_choice, now=NOW))["blind_link"].children == "Choose 1 first"
    assert prop(decide.render(one_choice, now=NOW), "data-needs-choice") == "yes"  # even one is chosen

    chosen = decide.render(case, chosen="ORG-000871", now=NOW)
    assert one(chosen, ids.CANDIDATE_CHOICE).value == "ORG-000871"
    assert _decision_buttons(chosen)["blind_link"].children == "Belongs to ORG-000871"
    assert prop(chosen, "data-needs-choice") is None
    assert decide.link_target(case, "ORG-000871") == "ORG-000871"
    assert decide.link_target(case, "ORG-999999") is None and decide.needs_choice(case, "ORG-999999")
    assert one(decide.render(case, chosen="ORG-999999", now=NOW), ids.CANDIDATE_CHOICE).value is None


def test_no_blind_or_disputed_decision_is_filled() -> None:
    for case in samples.CHECKPOINT_CASES:
        if case.shape not in decide.QUIET_SHAPES:
            continue
        for chosen in (None, *(c.master_id for c in case.choices), *(c.master_id for c in case.candidates)):
            pane = decide.render(case, chosen=chosen, now=NOW)
            looks = {name: b.variant for name, b in _decision_buttons(pane).items()}
            assert looks and set(looks.values()) == {"default"}, (case.shape, looks)
            actions = [c for c in walk(pane) if "mdm-action" in classes(c)]
            assert not [c for c in actions if getattr(c, "variant", None) in ("filled", "light")]


def test_a_blind_pair_needs_no_choice() -> None:
    pane = decide.render(samples.CASE_BLIND_PAIR, now=NOW)
    assert by_id(pane, ids.CANDIDATE_CHOICE) == [] and prop(pane, "data-needs-choice") is None
    buttons = _decision_buttons(pane)
    assert [(b.children, prop(b, "aria-keyshortcuts")) for b in buttons.values()] == [
        ("They are the same", "L"),
        ("They are not the same", "N"),
    ]
    assert prop(pane, "data-link") is None
    assert "ORG-000211 · golden record" in text_of(one(pane, ids.DECIDE_COMPARE))


def test_a_blind_review_with_nothing_near_offers_none_of_these_only() -> None:
    pane = decide.render(samples.CASE_BLIND_NONE, now=NOW)
    assert set(_decision_buttons(pane)) == {"blind_none"} and by_id(pane, ids.CANDIDATE_CHOICE) == []
    assert prop(pane, "data-link") == "none"  # L does nothing; the notice says to press N
    assert "Press N if it belongs to none." in text_of(pane)


def test_the_first_decider_sees_the_sample_but_cannot_answer_it() -> None:
    pane = decide.render(samples.CASE_BLIND_OWN, now=NOW)
    for button in _buttons(pane).values():
        assert prop(button, "disabled") is True
    reasons = text_of(one(pane, ids.ACTION_REASONS))
    assert reasons == "You made the first decision on this record, so another steward reviews it."
    assert prop(pane, "data-locked") == "yes"  # choosing in the browser enables nothing
    assert prop(decide.render(samples.CASE_BLIND, now=NOW), "data-locked") is None
    assert prop(decide.render(samples.CASE_OWNER_VIEW, now=NOW), "data-locked") == "yes"


def test_the_disputed_pane_offers_keep_the_first_decision() -> None:
    open_link = decide.render(samples.CASE_DISPUTED_LINK, now=NOW)
    buttons = _decision_buttons(open_link)
    assert [(b.children, prop(b, "aria-keyshortcuts"), b.variant) for b in buttons.values()] == [
        ("Keep the first decision", "A", "default"),
        ("Link to ORG-000123", "L", "default"),
    ]
    assert len(panels_of(open_link)) == 1  # decided in the open: the waterfall is there
    assert "/record/ORG-000123" in _anchors(open_link)
    kept_only = decide.render(samples.CASE_DISPUTED, now=NOW)
    assert list(_decision_buttons(kept_only)) == ["keep_decision"]
    assert samples.CASE_DISPUTED.notice in text_of(kept_only)
    assert [c.master_id for c in samples.CASE_DISPUTED.candidates] == [
        p.id["master_id"] for p in panels_of(kept_only)
    ]
    pair = decide.render(samples.CASE_DISPUTED_PAIR, now=NOW)
    assert list(_decision_buttons(pair)) == ["keep_decision"]
    assert "Merging needs a second steward" in text_of(pair)


def test_the_breaker_sentences_round_down_and_name_no_value() -> None:
    assert breaker.sentence(samples.BREAKER_AGREEMENT) == (
        "Automatic linking for Organisation is paused: blind review confirmed 30 of the last 40 automatic "
        "links (75%), confidently below 95%."
    )
    near = replace(samples.BREAKER_AGREEMENT, figures={"agreed": 39, "reviewed": 41, "threshold": 0.95})
    assert "(95%)" in breaker.sentence(near)  # 95.1% reads 95%, never rounded up
    assert "(97%)" in breaker.sentence(replace(near, figures={**near.figures, "agreed": 40, "reviewed": 41}))
    assert breaker.sentence(samples.BREAKER_VOLUME) == (
        "Automatic linking for Person is paused: 12,400 records arrived in the hour from 23:00 UTC, more than "
        "5 times the mean for that hour over the last 7 days (1,100)."
    )
    quiet = replace(samples.BREAKER_VOLUME, figures={**samples.BREAKER_VOLUME.figures, "mean": 0.0})
    assert breaker.sentence(quiet) == (
        "Automatic linking for Person is paused: 12,400 records arrived in the hour from 23:00 UTC; none "
        "arrived in that hour over the last 7 days."
    )
    tenth = replace(samples.BREAKER_VOLUME, figures={**samples.BREAKER_VOLUME.figures, "mean": 12.5})
    assert "(12.5)" in breaker.sentence(tenth)
    assert breaker.line_text(samples.BREAKER_AGREEMENT, NOW) == (
        "Organisation: automatic linking paused since 11:02 UTC"
    )
    assert "since 26 Sep, 23:05 UTC" in breaker.line_text(samples.BREAKER_VOLUME, NOW)  # another day


def test_the_breaker_line_has_no_live_role_and_the_pane_explains_it() -> None:
    strip = health.render(samples.HEALTH_PAUSED, NOW)
    lines = [c for c in walk(strip) if "mdm-breaker-line" in classes(c)]
    assert [text_of(line).strip() for line in lines] == [
        breaker.line_text(samples.BREAKER_AGREEMENT, NOW),
        breaker.line_text(samples.BREAKER_VOLUME, NOW),
    ]
    for line in lines:
        assert prop(line, "role") is None and prop(line, "aria-live") is None  # drawn again every poll
        icons = [c for c in walk(line) if "mdm-icon" in classes(c)]
        assert icons and all(prop(i, "aria-hidden") == "true" for i in icons)
    assert not [c for c in walk(health.render(samples.HEALTH, NOW)) if "mdm-breaker-line" in classes(c)]
    pane = decide.render(samples.CASE_PAUSED, now=NOW)
    notices = [c for c in walk(pane) if "mdm-breaker-notice" in classes(c)]
    assert len(notices) == 1 and {"mdm-notice", "mdm-notice-warning"} <= classes(notices[0])
    (details,) = [c for c in walk(notices[0]) if type(c).__name__ == "Details"]
    assert details.open is True  # a record the breaker made wait: why, at once
    assert text_of(notices[0]) == (
        "Automatic linking for Organisation is paused.Blind review confirmed 30 of the last 40 automatic "
        "links (75%), confidently below 95%."
    )  # what waits and who restores it: the reason above and the footer say it, not a third time
    other = decide.render(replace(samples.CASE_CLOSE_CALL, paused=samples.BREAKER_AGREEMENT), now=NOW)
    (notice,) = [c for c in walk(other) if "mdm-breaker-notice" in classes(c)]
    (quiet,) = [c for c in walk(notice) if type(c).__name__ == "Details"]
    assert quiet.open is False  # any other task of the entity: one line, the rest behind it
    assert "there is no button for it here" in text_of(notice)
    for shape in ("golden", "golden_pair", "disputed", "information"):  # a record it could have linked only
        away = decide.render(replace(samples.CASE_CLOSE_CALL, paused=samples.BREAKER_AGREEMENT, shape=shape))
        assert not [c for c in walk(away) if "mdm-breaker-notice" in classes(c)]
    assert not [c for c in walk(pane) if "restore" in str(getattr(c, "id", "")).lower()]
    blocked = _decision_buttons(pane)["not_a_match"]
    assert prop(blocked, "disabled") is True
    assert samples.CASE_PAUSED.actions[0].why_not in text_of(one(pane, ids.ACTION_REASONS))
    # the case's notice is also why Not a match is unavailable: said once, beside the action
    assert text_of(pane).count(samples.CASE_PAUSED.notice) == 1


def test_a_quality_sample_row_is_its_title_and_one_muted_line() -> None:
    row = inbox.grid_row(samples.ROW_SAMPLE, NOW, kind_shown=False)
    assert (row["title"], row["kind_label"], row["band_text"], row["band"]) == ("Quorane Works", "", "", "")
    assert row["second_title"] == "Decide blind · Organisation"
    assert row["due"] == "2 d"


def test_the_samples_view_its_query_and_the_keys_it_maps() -> None:
    entities = ("organisation", "person")
    assert inbox.parse_query({"view": "samples", "kind": "review"}, entities)["view"] == "samples"
    assert inbox.parse_query({"view": "samples", "kind": "review"}, entities)["kind"] is None
    assert inbox.address_query({"search": "?view=samples", "entity": "", "path": "/"}, entities) == {
        "view": "samples",
        "kind": None,
        "entity": "",
    }
    assert inbox.list_name({"view": "samples"}) == "Quality samples"
    assert inbox.page_label({"view": "samples"}, 0, 12) == "Quality samples: 1–12"
    assert inbox.mixed_kinds({"view": "team"}) and not inbox.mixed_kinds({"view": "samples"})
    assert not inbox.mixed_kinds({"view": "team", "kind": "review"})
    assert inbox.EMPTY_VIEWS["samples"] == "No quality sample waits for you."
    assert inbox.decision_for("link", "blind") == "blind_link"
    assert inbox.decision_for("link", "blind_pair") == "blind_link"
    assert inbox.decision_for("not_a_match", "blind") == "blind_none"
    assert inbox.decision_for("not_a_match", "blind_pair") == "blind_none"
    assert inbox.decision_for("link", "disputed") == "link"
    assert inbox.decision_for("approve", "disputed") == "keep_decision"
    assert inbox.decision_for("approve", "disputed_pair") == "keep_decision"
    assert inbox.decision_for("link", "source") == "link"
    for action in ("blind_link", "blind_none", "keep_decision"):
        assert inbox.requested_action({"action": action}) == action
        assert inbox.decision_for(action, "blind") == action


# ---------------------------------------------------------------------------------------------- on a sampled hub


@pytest.fixture
def sampled() -> Iterator[Hub]:
    """The mini world with every automated decision drawn for blind review (a share of 1)."""
    settings = base_settings().with_(sample_share=1.0)
    store = open_store(settings)
    store.init_schema(create_landing=True)
    hub = open_hub(settings, store, "duckdb")
    try:
        helpers.workbench_world(hub)
        yield hub
    finally:
        hub.close()
        store.close()


def a_sample(hub: Hub, ctx, *, choices: int = 1):
    """An open quality sample of a record with at least `choices` golden records offered, and its case."""
    for row in hub.inbox.page("samples", actor=ctx.actor).rows:
        if ":" not in row.subject:
            continue
        case = hub.decisions.case(row.task_id, actor=ctx.actor)
        if len(case.choices) >= choices:
            return row, case
    raise AssertionError("no sample with enough golden records offered")


def test_the_quality_samples_view_lists_blind_rows_only(sampled: Hub) -> None:
    ctx = context.for_test(sampled)
    page = inbox.layout(ctx, {"view": "samples"})
    grid = one(page, ids.INBOX_GRID)
    assert grid.rowData and {row["kind"] for row in grid.rowData} == {"quality_sample"}
    for row in grid.rowData:
        assert row["kind_label"] == "" and row["band_text"] == ""  # one kind; no score to show
        assert "Decide blind" in row["second_title"] and "blind review" not in row["second_title"]
    assert one(page, ids.PAGE_LABEL).children == f"Quality samples: 1–{len(grid.rowData)}"
    assert one(page, ids.INBOX_QUERY).data == {"view": "samples", "kind": None, "entity": ""}
    team = one(inbox.layout(ctx, {"view": "team"}), ids.INBOX_GRID)
    assert not [row for row in team.rowData if row["kind"] == "quality_sample"]


def test_a_blind_answer_stages_through_the_tray_after_a_choice(sampled: Hub) -> None:
    ctx = context.for_test(sampled)
    row, case = a_sample(sampled, ctx)
    pane, shown = inbox.case_view(ctx, row.task_id)
    assert shown["shape"] == "blind" and shown["default"] is None
    assert prop(pane, "data-blind") == "yes" and _anchors(pane) == []
    unchosen = inbox.act(ctx, "link", task_id=row.task_id, stamp=shown)
    assert unchosen.advance is False and unchosen.notices[0]["message"] == messages.sentence_for(
        "choose_first"
    )
    assert staged_entries(sampled, ctx) == []

    target = case.choices[0].master_id
    staged = inbox.act(ctx, "link", task_id=row.task_id, candidate=target, stamp=shown)
    label = f"Quality sample: {row.subject} belongs to {target}"
    assert staged.advance is True and staged.row is not None and staged.row.staged.label == label
    assert staged.notices[0]["message"].startswith(label)
    assert [(e.decision, e.label) for e in staged_entries(sampled, ctx)] == [("blind_link", label)]
    undone = inbox.act(ctx, "undo", task_id=row.task_id)
    assert undone.touched == row.task_id and staged_entries(sampled, ctx) == []

    clicked = inbox.act(ctx, "blind_link", task_id=row.task_id, candidate=target, stamp=shown)
    assert clicked.advance and [e.decision for e in staged_entries(sampled, ctx)] == ["blind_link"]
    inbox.act(ctx, "undo", task_id=row.task_id)
    none = inbox.act(ctx, "not_a_match", task_id=row.task_id, stamp=shown)
    assert none.row.staged.label == f"Quality sample: {row.subject} belongs to none shown"
    assert [e.decision for e in staged_entries(sampled, ctx)] == ["blind_none"]


def test_a_disagreement_opens_a_dispute_that_a_keeps(sampled: Hub) -> None:
    ctx = context.for_test(sampled)
    row, _case = a_sample(sampled, ctx)
    inbox.act(ctx, "not_a_match", task_id=row.task_id, stamp=stamp(ctx, row.task_id))
    window_passed(sampled)
    assert sampled.tray.flush().committed == 1
    (settled,) = [v for v in sampled.tray.entries(actor=ctx.actor) if v.task_id == row.task_id]
    assert settled.outcome == "disagreed"
    other = context.for_test(sampled, role="coordinating_steward")
    disputes = [
        r
        for r in sampled.inbox.page("team", actor=other.actor).rows
        if r.reason == "a blind review disagreed"
    ]
    assert len(disputes) == 1
    dispute = disputes[0]
    assert dispute.suggestion == "Keep or correct the first decision"
    pane, shown = inbox.case_view(other, dispute.task_id)
    assert shown["shape"] == "disputed" and prop(pane, "data-quiet") == "yes"
    assert "keep_decision" in _decision_buttons(pane)
    kept = inbox.act(other, "approve", task_id=dispute.task_id, stamp=shown)
    assert kept.advance and kept.row.staged.label.startswith("Keep the first decision on ")


def test_a_blind_pair_is_answered_without_a_choice(sampled: Hub) -> None:
    helpers.golden_pair(sampled)
    pair = helpers.task_of(sampled, kind="possible_duplicate")
    steward = context.for_test(sampled)
    inbox.act(steward, "not_a_match", task_id=pair.task_id, stamp=stamp(steward, pair.task_id))
    window_passed(sampled)
    assert sampled.tray.flush().committed == 1
    other = context.for_test(sampled, role="coordinating_steward")
    rows = [r for r in sampled.inbox.page("samples", actor=other.actor).rows if ":" not in r.subject]
    assert len(rows) == 1
    # the steward who kept them apart never sees the sample among theirs
    assert rows[0].task_id not in {r.task_id for r in sampled.inbox.page("samples", actor=steward.actor).rows}
    pane, shown = inbox.case_view(other, rows[0].task_id)
    assert shown["shape"] == "blind_pair" and by_id(pane, ids.CANDIDATE_CHOICE) == []
    same = inbox.act(other, "link", task_id=rows[0].task_id, stamp=shown)
    first, second = rows[0].subject.split(" · ")
    assert same.advance and same.row.staged.label == f"Quality sample: {first} and {second} are the same"
    own = inbox.act(steward, "link", task_id=rows[0].task_id, stamp=stamp(steward, rows[0].task_id))
    assert own.advance is False


def test_an_empty_samples_view_says_so(world: Hub) -> None:
    ctx = context.for_test(world)
    nothing, _ = inbox.case_view(ctx, None, query={"view": "samples", "kind": None, "entity": ""})
    assert text_of(nothing) == "No quality sample waits for you."


def test_a_records_quality_sample_opens_in_its_own_view_and_four_records_fit_side_by_side() -> None:
    from mdm.ui.pages import source as source_page

    assert source_page.task_href("TSK-1a", "quality_sample") == "/?view=samples&task=TSK-1a"
    assert (
        source_page.task_href("TSK-1a", "review")
        == source_page.task_href("TSK-1a")
        == "/?view=team&task=TSK-1a"
    )
    wide = compare.render(samples.CASE_BLIND.columns, samples.CASE_BLIND.compare)
    assert [classes(c) for c in walk(wide) if "mdm-compare" in classes(c)] == [
        {"mdm-table", "mdm-compare", "mdm-compare-wide", "mdm-compare-multi"}
    ]
    two = compare.render(samples.CASE_PERSON_STAGED.columns, samples.CASE_PERSON_STAGED.compare)
    assert [classes(c) for c in walk(two) if "mdm-compare" in classes(c)] == [{"mdm-table", "mdm-compare"}]


def test_claim_snooze_and_escalate_wrap_as_one_group_after_the_decisions() -> None:
    """A long answer ("Belongs to none of these") never leaves Escalate alone on a second line."""
    for case in (samples.CASE_BLIND, samples.CASE_CLOSE_CALL):
        (bar,) = [c for c in walk(decide.render(case, now=NOW)) if "mdm-actions" in classes(c)]
        main, moving = bar.children  # the arrows stay beside the group, never wrapped onto a line alone
        assert classes(main) == {"mdm-actions-main"} and classes(moving) == {"mdm-moving"}
        *decisions, work = main.children
        assert classes(work) == {"mdm-actions-work"}
        grouped = [str(getattr(c, "id", "")) for c in walk(work)]
        assert any("claim" in g for g in grouped) and not any("not_a_match" in g for g in grouped)
        ids_before = [str(getattr(c, "id", "")) for d in decisions for c in walk(d)]
        assert decisions and not any("claim" in i for i in ids_before)


# ---------------------------------------------------------------------------------------------- forced samples (story 3.3)


def test_a_forced_sample_review_says_so_and_nudges_no_decision() -> None:
    pane = decide.render(samples.CASE_SAMPLE, now=NOW)
    notices = [c for c in walk(pane) if "mdm-sample-notice" in classes(c)]
    assert len(notices) == 1
    assert text_of(notices[0]) == (
        f"Forced sample for batch {samples.BATCH_ID}: review 3 of 9. Decide it on its own: the rest are linked "
        "together only if every sample agrees. Open the batch"
    )
    [link] = [c for c in walk(notices[0]) if type(c).__name__ == "Anchor"]
    assert link.href == f"/batch/{samples.BATCH_ID}"
    assert prop(pane, "data-quiet") == "yes" and prop(pane, "data-sample") == "yes"
    assert prop(pane, "data-default") == "PER-000451"
    decisions = [
        c for c in walk(pane) if isinstance(getattr(c, "id", None), dict) and c.id.get("type") == ids.ACTION
    ]
    assert decisions and not [c for c in decisions if getattr(c, "variant", None) == "filled"]
    assert decide.quiet_case(samples.CASE_SAMPLE) and not decide.quiet_case(samples.CASE_PERSON_STAGED)
    plain = decide.render(samples.CASE_CLOSE_CALL, now=NOW)
    assert prop(plain, "data-sample") is None and prop(plain, "data-default") is None


def test_a_close_call_in_the_sample_has_no_default_so_its_link_names_no_comparison() -> None:
    close = replace(samples.CASE_CLOSE_CALL, sample=samples.SAMPLE_LINE)
    assert prop(decide.render(close, now=NOW), "data-default") == ""


def test_which_comparison_misled_is_a_closed_choice_with_no_default() -> None:
    pane = decide.render(samples.CASE_SAMPLE, now=NOW)
    [details] = [c for c in walk(pane) if "mdm-split" in classes(c)]
    assert not getattr(details, "open", False)
    assert text_of(details.children[0]) == (
        "Not a match, or another golden record? Name the comparison that misled"
    )
    group = one(pane, ids.SPLIT_CHOICE)
    assert group.label == "Which comparison misled?" and group.value is None
    radios = [c for c in walk(group) if type(c).__name__ == "Radio"]
    assert [r.value for r in radios] == [m.comparison for m in samples.MARKS_PERSON] + ["all"]
    assert text_of(radios[2].label) == "Birth date ≈ similar"
    assert radios[-1].label == "Every alike review in this batch"
    assert decide.SPLIT_NOTE in [text_of(c) for c in walk(details) if type(c).__name__ == "P"]
    footer = next(c for c in walk(pane) if "mdm-decide-footer" in classes(c))
    assert details in list(walk(footer))  # under the decisions


def test_the_chosen_comparison_is_kept_on_the_same_case_and_never_invented() -> None:
    kept = decide.render(samples.CASE_SAMPLE, split="birth_date", now=NOW)
    assert one(kept, ids.SPLIT_CHOICE).value == "birth_date"
    [details] = [c for c in walk(kept) if "mdm-split" in classes(c)]
    assert details.open is True
    assert one(decide.render(samples.CASE_SAMPLE, split="all", now=NOW), ids.SPLIT_CHOICE).value == "all"
    assert one(decide.render(samples.CASE_SAMPLE, split="shoe_size", now=NOW), ids.SPLIT_CHOICE).value is None
    assert (
        decide.split_codes(samples.CASE_SAMPLE)[-1] == "all"
        and decide.split_codes(samples.CASE_CLOSE_CALL) == ()
    )


def test_no_comparison_is_asked_where_nothing_can_be_decided() -> None:
    for case in (samples.CASE_SAMPLE_STAGED, samples.CASE_BATCH_HELD, samples.CASE_PERSON_STAGED):
        assert not by_id(decide.render(case, now=NOW), ids.SPLIT_CHOICE)
    owner = replace(
        samples.CASE_SAMPLE,
        actions=tuple(replace(a, enabled=False, why_not="No.") for a in samples.CASE_SAMPLE.actions),
    )
    assert not by_id(decide.render(owner, now=NOW), ids.SPLIT_CHOICE)


def test_a_disagreeing_sample_decision_in_the_tray_says_what_leaves() -> None:
    at = samples.STAGED_SAMPLE.deadline.strftime("%H:%M:%S")
    named = decide.staged_line(samples.CASE_SAMPLE_STAGED, split="birth_date", now=NOW)
    assert text_of(named) == (
        f"Not a match, flagged on birth date: when it commits at {at} UTC, the reviews that share this record's "
        f"value on it leave batch {samples.BATCH_ID}. U undoes it, and nothing leaves."
    )
    every = decide.staged_line(samples.CASE_SAMPLE_STAGED, split="all", now=NOW)
    assert f"every alike review leaves batch {samples.BATCH_ID}" in text_of(every)
    unnamed = decide.staged_line(samples.CASE_SAMPLE_STAGED, now=NOW)
    assert text_of(unnamed).startswith("In the tray: Not a match: crm:C001377. When it commits at ")
    assert text_of(unnamed).endswith("U undoes it, and nothing leaves.")
    agreeing = replace(
        samples.CASE_SAMPLE_STAGED,
        staged=replace(samples.STAGED_SAMPLE, decision="link", label="Link crm:C001377 to PER-000451"),
    )
    assert text_of(decide.staged_line(agreeing, now=NOW)).startswith(
        "In the tray: Link crm:C001377 to PER-000451."
    )


def test_a_review_a_batch_holds_says_so_and_its_decisions_wait() -> None:
    pane = decide.render(samples.CASE_BATCH_HELD, now=NOW)
    at = samples.STAGED_BATCH.deadline.strftime("%H:%M:%S")
    line = decide.staged_line(samples.CASE_BATCH_HELD, now=NOW)
    assert text_of(line) == (
        f"Part of batch {samples.BATCH_ID} in the tray: it commits at {at} UTC unless the batch is undone. U undoes "
        "the whole batch. Open the batch"
    )
    [link] = [c for c in walk(line) if type(c).__name__ == "Anchor"]
    assert link.href == f"/batch/{samples.BATCH_ID}"
    committing = decide.staged_line(samples.CASE_BATCH_COMMITTING, now=NOW)
    assert text_of(committing) == f"Part of batch {samples.BATCH_ID}, which is committing. Open the batch"
    decisions = [
        c for c in walk(pane) if isinstance(getattr(c, "id", None), dict) and c.id.get("type") == ids.ACTION
    ]
    link_and_no = [c for c in decisions if c.id["decision"] in ("link", "not_a_match")]
    assert link_and_no and all(c.disabled for c in link_and_no)
    assert samples.HELD_BY_BATCH in text_of(one(pane, ids.ACTION_REASONS))
    assert prop(pane, "data-sample") is None  # held by the batch: no longer a sample to decide


def test_the_keys_name_the_comparison_before_a_disagreeing_decision() -> None:
    source = (Path(__file__).resolve().parents[1] / "src" / "mdm" / "ui" / "assets" / "inbox.js").read_text(
        "utf-8"
    )
    start = source.index("needsSplit: function (action)")
    body = source[start : source.index("focusSplit: function", start)]
    assert '[data-sample="yes"]' in body and ".mdm-split-choice input[type=radio]:checked" in body
    assert 'action === "not_a_match"' in body and 'getAttribute("data-default")' in body
    assert "naming" in source[source.index("actRequest: function") : source.index("needsChoice: function")]


def test_a_group_or_a_batch_filters_the_inbox_and_names_its_list() -> None:
    entities = ("organisation", "person")
    group, batch = samples.GROUP_KEY_PERSON, samples.BATCH_ID
    assert inbox.query_from_search(f"?group={group}&batch={batch}&task=TSK-1a&name=x") == {
        "group": group,
        "batch": batch,
        "task": "TSK-1a",
    }
    parsed = inbox.parse_query({"view": "team", "group": group}, entities)
    assert parsed["group"] == group and "batch" not in parsed
    assert inbox.parse_query({"batch": batch, "group": group}, entities)["batch"] == batch
    assert "group" not in inbox.parse_query({"group": "given_name="}, entities)  # not a key's shape
    assert "batch" not in inbox.parse_query({"batch": "BAT-1"}, entities)
    assert inbox.address_query({"search": f"?batch={batch}", "entity": "", "path": "/"}, entities) == {
        "view": "mine",
        "kind": None,
        "entity": "",
        "batch": batch,
    }
    kept = {"view": "mine", "kind": None, "entity": ""}
    moved = inbox.new_query({"search": f"?group={group}", "entity": "", "path": "/"}, kept, entities)
    assert moved == {"view": "mine", "kind": None, "entity": "", "group": group}
    assert inbox.new_query({"search": f"?group={group}", "entity": "", "path": "/"}, moved, entities) is None
    assert inbox.list_name({"view": "team", "group": group}) == "Alike reviews"
    assert inbox.page_label({"view": "mine", "batch": batch}, 0, 9) == "Forced sample: 1–9"
    assert not inbox.mixed_kinds({"view": "team", "group": group})
    assert not inbox.mixed_kinds({"view": "team", "batch": batch})


def test_the_filter_line_names_the_group_or_the_batch_and_links_back() -> None:
    group = inbox.filter_line({"group": samples.GROUP_KEY_PERSON})
    assert text_of(group) == "Alike reviews: every open review with one pattern. Back to Alike reviews"
    assert [c.href for c in walk(group) if type(c).__name__ == "Anchor"] == ["/groups"]
    batch = inbox.filter_line({"batch": samples.BATCH_ID})
    assert text_of(batch) == (
        f"Forced sample of batch {samples.BATCH_ID}: decide each review on its own. Back to the batch"
    )
    assert [c.href for c in walk(batch) if type(c).__name__ == "Anchor"] == [f"/batch/{samples.BATCH_ID}"]
    assert inbox.filter_line({"view": "team"}) is None and inbox.filter_line(None) is None
    skeleton = inbox.skeleton()
    assert len(by_id(skeleton, ids.INBOX_FILTER)) == 1


def test_an_empty_group_or_sample_says_so_in_the_pane() -> None:
    assert text_of(inbox.filtered_empty({"group": samples.GROUP_KEY_PERSON})) == (
        "No review with this pattern is open."
    )
    sample = inbox.filtered_empty({"batch": samples.BATCH_ID})
    assert text_of(sample) == "Every review of this sample is decided. Back to the batch to go on."
    assert inbox.filtered_empty({"view": "mine"}) is None


# ---------------------------------------------------------------------------------------------- alike reviews, on a hub
#
# Story 3.3: the inbox filtered to a group's reviews or a batch's forced sample, the comparison named with a
# disagreeing sample decision, a review a batch holds, U on it, and the page read again when a batch settles.


@pytest.fixture
def alike() -> Iterator[Hub]:
    """12 alike Person reviews on an in-memory DuckDB: one group, a forced sample of 5."""
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


def drawn(hub: Hub) -> tuple[str, str]:
    """(the group's key, the batch a data steward draws from it)."""
    ctx = context.for_test(hub)
    group = hub.batches.groups(actor=ctx.actor, entity="person").groups[0]
    return group.group_key, hub.batches.draw(group.group_key, actor=ctx.actor, entity="person").batch_id


def sample_tasks(hub: Hub, batch_id: str) -> list[str]:
    return [i.task_id for i in hub.store.batch_items(batch_id, ("sample",), ("open",), None, 1000)]


def test_a_group_or_a_batch_in_the_address_lists_its_reviews_whatever_the_view(alike: Hub) -> None:
    group, batch_id = drawn(alike)
    ctx = context.for_test(alike)
    reviews = inbox.load_page(ctx, {"view": "escalated", "group": group}, None)
    assert len(reviews.rows) == 12 and {r.kind for r in reviews.rows} == {"review"}
    sample = inbox.load_page(ctx, {"view": "mine", "batch": batch_id}, None)
    assert sorted(r.task_id for r in sample.rows) == sorted(sample_tasks(alike, batch_id))
    shown = inbox.layout(ctx, {"batch": batch_id})
    assert one(shown, ids.PAGE_LABEL).children == "Forced sample: 1–5"
    assert text_of(one(shown, ids.INBOX_FILTER)).startswith(f"Forced sample of batch {batch_id}:")
    assert one(shown, ids.INBOX_QUERY).data == {"view": "mine", "kind": None, "entity": "", "batch": batch_id}
    grouped = inbox.layout(ctx, {"group": group})
    assert one(grouped, ids.PAGE_LABEL).children == "Alike reviews: 1–12"


def test_a_disagreeing_sample_decision_names_its_comparison_or_is_refused_and_focuses_the_choice(
    alike: Hub,
) -> None:
    _, batch_id = drawn(alike)
    ctx = context.for_test(alike)
    first, second, third = sample_tasks(alike, batch_id)[:3]
    pane, seen = inbox.case_view(ctx, first)
    assert prop(pane, "data-sample") == "yes"
    assert [c for c in walk(pane) if getattr(c, "id", None) == ids.SPLIT_CHOICE]
    unnamed = inbox.act(ctx, "not_a_match", task_id=first, stamp=seen)
    assert not unnamed.advance and unnamed.focus == "split"
    assert unnamed.notices[0]["title"] == "Name the comparison"
    stored = inbox.result_store(unnamed, first, None)
    assert stored["focus"] == "split"
    named = inbox.act(ctx, "not_a_match", task_id=first, stamp=seen, split="birth_date")
    assert named.advance and named.focus is None and named.named == "birth_date"
    [entry] = [e for e in alike.tray.entries(actor=ctx.actor) if e.task_id == first]
    assert alike.store.tray_entries([entry.entry_id])[entry.entry_id].subject.get("split_on") == "birth_date"
    # the choice is kept across a redraw of the same case, and says what leaves when it commits
    kept, _ = inbox.case_view(ctx, first, split="birth_date")
    assert "Not a match, flagged on birth date: when it commits" in text_of(kept)
    # its decision in the tray says what it named even after another case came between
    back, _ = inbox.case_view(ctx, first, split="birth_date", kept=False)
    assert "Not a match, flagged on birth date: when it commits" in text_of(back)
    # an open review drawn afresh chooses nothing for the steward
    fresh, _ = inbox.case_view(ctx, third, split="birth_date", kept=False)
    [choice] = [c for c in walk(fresh) if getattr(c, "id", None) == ids.SPLIT_CHOICE]
    assert choice.value is None
    again, _ = inbox.case_view(ctx, third, split="birth_date")
    [choice] = [c for c in walk(again) if getattr(c, "id", None) == ids.SPLIT_CHOICE]
    assert choice.value == "birth_date"
    # an agreeing link names no comparison, whatever the store holds
    _, seen_second = inbox.case_view(ctx, second)
    agreed = inbox.act(ctx, "link", task_id=second, stamp=seen_second, split="birth_date")
    assert agreed.advance and agreed.named is None, agreed.notices
    linked, _ = inbox.case_view(ctx, second, split=None, kept=False)
    assert "flagged on" not in text_of(linked) and "In the tray: Link" in text_of(linked)
    assert inbox.split_of({"task": third, "on": "birth_date"}, second) is None
    assert inbox.split_of({"task": third, "on": "birth_date"}, third) == "birth_date"
    assert inbox.split_of({"task": third, "on": "Tamsin Quorrel"}, third) is None
    assert inbox.disagrees("not_a_match", None, "PER-000001")
    assert inbox.disagrees("link", "PER-000002", "PER-000001")
    assert not inbox.disagrees("link", "PER-000001", "PER-000001")
    assert not inbox.disagrees("link", "PER-000002", None)  # a close call suggests nothing: void


def test_u_on_a_review_a_batch_holds_undoes_the_batch_and_u_elsewhere_never_does(alike: Hub) -> None:
    _, batch_id = drawn(alike)
    ctx = context.for_test(alike)
    helpers.decide_sample(alike, batch_id, actor=ctx.actor)
    alike.batches.refresh(batch_id)
    alike.batches.prepare(batch_id, actor=ctx.actor)
    alike.batches.stage(batch_id, actor=ctx.actor)
    held = next(i.task_id for i in alike.store.batch_items(batch_id, ("bulk",), ("planned",), None, 1000))
    pane, _ = inbox.case_view(ctx, held)
    assert f"Part of batch {batch_id} in the tray" in text_of(pane)
    nothing = inbox.act(ctx, "undo", task_id=None)
    assert nothing.notices[0]["message"] == "Nothing of yours is waiting in the tray."
    assert alike.batches.batch(batch_id, actor=ctx.actor).status == "staged"
    undone = inbox.act(ctx, "undo", task_id=held)
    assert undone.notices[0]["message"] == f"Undone: batch {batch_id} is ready again, and nothing was linked."
    assert undone.bump_case and undone.bump_tray and undone.row is None and not undone.remove
    assert alike.batches.batch(batch_id, actor=ctx.actor).status == "ready"


def test_a_batch_settlement_reads_the_page_on_screen_again() -> None:
    batch_doc = [
        {"entry_id": "TR-1", "task_id": samples.BATCH_ID, "status": "committed", "batch_id": samples.BATCH_ID}
    ]
    single = [{"entry_id": "TR-2", "task_id": "TSK-1", "status": "committed", "outcome": "committed"}]
    assert (
        inbox.settled_batch(batch_doc) and not inbox.settled_batch(single) and not inbox.settled_batch(None)
    )
    assert inbox.reloaded({"stack": [["2026-09-27T12:00:00", "TSK-9"]], "moved": "next"}) == {
        "stack": [["2026-09-27T12:00:00", "TSK-9"]],
        "moved": None,
        "reload": 1,
    }
    assert inbox.reloaded({"stack": [], "moved": None, "reload": 4})["reload"] == 5
    assert inbox.reloaded(None) == {"stack": [], "moved": None, "reload": 1}


def test_the_skeleton_holds_the_split_choice_and_its_store() -> None:
    skeleton = inbox.skeleton()
    one(skeleton, ids.SPLIT_CHOICE)
    one(skeleton, ids.SELECTED_SPLIT)
    one(skeleton, ids.INBOX_FILTER)
    app = Dash(__name__)
    inbox.register(app)
    [split] = [c for c in app._callback_list if c["output"] == f"{ids.SELECTED_SPLIT}.data"]
    assert split["prevent_initial_call"] is True
    [settled] = [
        c
        for c in app._callback_list
        if f"{ids.INBOX_CURSOR}.data@" in c["output"] and "rowTransaction" in c["output"]
    ]
    assert settled["prevent_initial_call"] is True
