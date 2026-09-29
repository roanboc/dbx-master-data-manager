"""No personal value reaches the screen of a role that may not see it, and a reveal stays where it was asked
(decision 20; plan B.9.2; DuckDB only).

For a Person golden record and a Person review task, as a data steward without a reveal and as a consumer,
every page, pane, Why, header, tray, notification and store document the workbench builds is rendered,
and every string in it (text, labels, IDs, data attributes, store data) is checked against the personal
values the test landed. After a reveal, the clear values appear only inside the compare table, and in no
store, ID or later case. For signature batches (story 3.3), the Alike reviews page, a batch's page in each
state with every row, its tray entry and the forced sample's pane are checked the same way.
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Callable, Iterable, Iterator
from typing import Any

import pytest
from dash.development.base_component import Component

from mdm import capacity
from mdm.backend.factory import open_store
from mdm.models.records import SourceKey
from mdm.services.context import Hub
from mdm.ui import app as app_module
from mdm.ui import context, ids, layout
from mdm.ui.components import decide, provenance, tray
from mdm.ui.pages import batch as batch_page
from mdm.ui.pages import groups as groups_page
from mdm.ui.pages import inbox, record
from tests import helpers
from tests.conftest import base_settings, open_hub

#: the Person attributes the starter model masks, as the test landed them
PERSONAL = ("given_name", "family_name", "birth_date", "email", "phone", "postcode", "person_ref")
PERSONS = 8
REVIEWED = 4  # the hr person the crafted crm record resembles (helpers.person_review)


@pytest.fixture(scope="module")
def world() -> Iterator[tuple[Hub, str, str, SourceKey]]:
    """The mini world and a Person review task: (hub, the task's ID, the golden record's ID, the crm
    record under review)."""
    settings = base_settings()
    store = open_store(settings)
    store.init_schema(create_landing=True)
    hub = open_hub(settings, store, "duckdb")
    try:
        helpers.workbench_world(hub, persons=PERSONS, organisations=3)
        source = helpers.person_review(hub, REVIEWED)
        task = helpers.task_of(hub, kind="review", source=source)
        master_id = helpers.master_of(hub, "person", "hr", helpers.hr_key(REVIEWED))
        assert master_id is not None
        yield hub, task.task_id, master_id, source
    finally:
        hub.close()
        store.close()


def personal_values() -> set[str]:
    """Every personal value the world landed, in the forms a screen could show it."""
    rows = list(helpers.mini_world(persons=PERSONS, organisations=3).rows)
    found: set[str] = set()
    for landed in rows:
        if landed.entity != "person":
            continue
        for attribute in PERSONAL:
            value = landed.payload.get(attribute)
            if not isinstance(value, str) or len(value) < 4:
                continue
            found.add(value)
            if attribute == "phone":
                found.add(re.sub(r"\D", "", value))
            if attribute == "postcode":
                found.add(value.replace(" ", ""))
    return {value.lower() for value in found if len(value) >= 4}


VALUES = personal_values()


def strings(value: Any) -> Iterator[str]:
    """Every string in a component tree, a payload, a store document or a dataclass: text, props, IDs."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, Component):
        for name in value._prop_names:
            yield from strings(getattr(value, name, None))
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from strings(item)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            yield from strings(item)
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        yield from strings(dataclasses.asdict(value))
    elif value is not None and not isinstance(value, (int, float, bool)):
        yield str(value)


def leaks(*pieces: Any) -> list[str]:
    """The personal values found in `pieces`."""
    text = "\n".join(s for piece in pieces for s in strings(piece)).lower()
    return sorted(value for value in VALUES if value in text)


class Renders:
    """What a role's screens show, piece by piece."""

    def __init__(self) -> None:
        self.pieces: list[tuple[str, Any]] = []

    def add(self, name: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        shown = fn(*args, **kwargs)
        self.pieces.append((name, shown))
        return shown

    def leaking(self) -> dict[str, list[str]]:
        return {name: found for name, piece in self.pieces if (found := leaks(piece))}


def screens(hub: Hub, role: str, task_id: str, master_id: str, source: SourceKey) -> Renders:
    """Everything the workbench builds for `role` around the review task and the golden record."""
    ctx = context.for_test(hub, role=role)
    shown = Renders()
    shown.add("shell", layout.shell, hub.settings, ctx.badges)
    shown.add("header", layout.header_state, ctx, "", "/", "?view=team")
    shown.add("tray", tray.refresh, ctx, None)
    for path in (
        "/",
        f"/?view=team&task={task_id}",
        f"/record/{master_id}",
        f"/record/{source.system}:{source.key}",
        f"/source/{source.system}/{source.key}",
        "/nowhere",
    ):
        pathname, _, search = path.partition("?")
        shown.add(f"route {pathname}", app_module.render_route, ctx, pathname, search, "")
    page = shown.add("inbox page", inbox.load_page, ctx, {"view": "team"}, None)
    for row in getattr(page, "rows", ()):
        shown.add("grid row", inbox.grid_row, row)
    case, refused = context.guarded(hub.decisions.case, task_id, actor=ctx.actor)
    shown.pieces.append(("case refusal", refused))
    if case is not None:
        shown.pieces.append(("case", case))
        shown.add("decide pane", decide.render, case)
    for tab in record.TABS:
        shown.add(f"record tab {tab}", record.tab_content, ctx, "person", master_id, tab)
    for attribute in PERSONAL:
        why, refused = context.guarded(hub.lookup.why, "person", master_id, attribute, actor=ctx.actor)
        shown.pieces.append((f"why refusal {attribute}", refused))
        if why is not None:
            shown.add(f"why {attribute}", provenance.why_body, why)
    return shown


def test_the_world_holds_the_values_the_check_looks_for(world) -> None:
    hub, task_id, master_id, source = world
    assert len(VALUES) > 30
    owner = context.for_test(hub, role="data_owner")
    clear = hub.lookup.golden("person", master_id, actor=owner.actor, reveal=True, reason="audit_check")
    assert leaks(clear), "a reveal shows the values the check looks for"


@pytest.mark.parametrize("role", ["data_steward", "consumer"])
def test_no_screen_shows_a_personal_value_without_a_reveal(world, role: str) -> None:
    hub, task_id, master_id, source = world
    shown = screens(hub, role, task_id, master_id, source)
    assert shown.leaking() == {}


def test_a_staged_decision_and_its_notices_show_no_personal_value(world) -> None:
    hub, task_id, master_id, source = world
    steward = context.for_test(hub)
    entry = hub.tray.stage(task_id, "not_a_match", actor=steward.actor, **helpers.seen(hub, task_id))
    try:
        change = tray.refresh(steward, None)
        assert change is not None and change.state
        assert leaks(change.state, change.children, change.notices, change.settled) == []
        undone = tray.undo(steward, entry.entry_id)
        assert leaks(undone) == [] and undone["color"] == "teal"
        again = tray.undo(steward, entry.entry_id)  # too late: its refusal names no value either
        assert leaks(again) == [] and again["color"] == "yellow"
    finally:
        if hub.tray.entries(actor=steward.actor)[0].status == "staged":
            hub.tray.undo(entry.entry_id, actor=steward.actor)


def test_a_reveal_stays_in_the_compare_table(world) -> None:
    hub, task_id, master_id, source = world
    steward = context.for_test(hub)
    case = hub.decisions.case(task_id, actor=steward.actor)
    revealed = hub.decisions.reveal(task_id, actor=steward.actor, reason="deciding_task")
    assert leaks(revealed.compare), "the reveal shows values"
    pane = decide.render(case, revealed=revealed)
    inside = [c for c in _walk(pane) if getattr(c, "id", None) == ids.DECIDE_COMPARE]
    assert len(inside) == 1
    assert leaks(inside[0].children)
    outside = _without(pane, inside[0])
    assert leaks(outside) == []
    assert not [c for c in _walk(pane) if type(c).__name__ == "Store"]
    assert leaks([c.id for c in _walk(pane) if getattr(c, "id", None) is not None]) == []
    later = hub.decisions.case(task_id, actor=steward.actor)  # the cache keeps masked cases only
    assert leaks(later) == []


def test_a_consumer_is_offered_no_reveal(world) -> None:
    hub, task_id, master_id, source = world
    consumer = context.for_test(hub, role="consumer")
    assert not consumer.can("reveal")
    page, _, _ = app_module.render_route(consumer, f"/record/{master_id}", "", "")
    offered = [c for c in _walk(page) if getattr(c, "id", None) == ids.reveal(ids.REVEAL_OPEN, "record")]
    assert offered == []


def _walk(tree: Any) -> Iterable[Component]:
    if isinstance(tree, (list, tuple)):
        for item in tree:
            yield from _walk(item)
    elif isinstance(tree, Component):
        yield tree
        for name in tree._prop_names:
            value = getattr(tree, name, None)
            if isinstance(value, (Component, list, tuple)):
                yield from _walk(value)


def _without(tree: Any, cut: Component) -> list[str]:
    """Every string in `tree` except those inside `cut`."""
    found: list[str] = []

    def visit(value: Any) -> None:
        if value is cut:
            return
        if isinstance(value, Component):
            for name in value._prop_names:
                visit(getattr(value, name, None))
        elif isinstance(value, (list, tuple)):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            found.append(json.dumps(value, default=str))
        elif isinstance(value, str):
            found.append(value)

    visit(tree)
    return found


# ---------------------------------------------------------------------------------------------- alike reviews
#
# Story 3.3: the Alike reviews page, a batch's page in each state it passes through (with its split lines and
# every row of the prepared batch), the tray with the batch's entry, the inbox filtered to the forced sample
# and its pane with "Which comparison misled?" open, for a data steward and a consumer.

ALIKE_PERSONS = 32
ALIKE_FIRST = 20
ALIKE_REVIEWS = 12


def alike_values() -> set[str]:
    """Every personal value the alike world landed: the mini world's, and the alike records' birth dates."""
    rows = list(helpers.mini_world(persons=ALIKE_PERSONS, organisations=3).rows)
    found: set[str] = set()
    for landed in rows:
        if landed.entity != "person":
            continue
        for attribute in PERSONAL:
            value = landed.payload.get(attribute)
            if isinstance(value, str) and len(value) >= 4:
                found.add(value)
                if attribute == "phone":
                    found.add(re.sub(r"\D", "", value))
                if attribute == "postcode":
                    found.add(value.replace(" ", ""))
    for i in range(ALIKE_REVIEWS):
        held = helpers.person_payload(ALIKE_FIRST + i)
        found.add(helpers._alike_payload(held, None)["birth_date"])  # noqa: SLF001 - the landed record's own
    return {value.lower() for value in found if len(value) >= 4}


ALIKE_VALUES = alike_values()


def alike_leaks(*pieces: Any) -> list[str]:
    text = "\n".join(s for piece in pieces for s in strings(piece)).lower()
    return sorted(value for value in ALIKE_VALUES if value in text)


@pytest.fixture(scope="module")
def alike() -> Iterator[Hub]:
    settings = base_settings()
    store = open_store(settings)
    store.init_schema(create_landing=True)
    hub = open_hub(settings, store, "duckdb")
    try:
        helpers.workbench_world(hub, persons=ALIKE_PERSONS, organisations=3)
        helpers.alike_reviews(hub, ALIKE_REVIEWS, first=ALIKE_FIRST)
        yield hub
    finally:
        hub.close()
        store.close()


def batch_screens(hub: Hub, role: str, batch_id: str) -> list[tuple[str, Any]]:
    """Everything the workbench builds for `role` around one batch: the Alike reviews page, the batch's page
    with every page of its rows, the tray, the inbox filtered to its forced sample, and the pane of each open
    sample review with its comparison named."""
    ctx = context.for_test(hub, role=role)
    shown: list[tuple[str, Any]] = [
        ("groups route", app_module.render_route(ctx, "/groups", "", "person")),
        ("groups view", groups_page.view(ctx, None)),
        ("batch route", app_module.render_route(ctx, f"/batch/{batch_id}", "", "")),
        ("sample inbox", app_module.render_route(ctx, "/", f"?batch={batch_id}", "")),
        ("header", layout.header_parts(ctx, "", f"/batch/{batch_id}", "")),
        ("tray", tray.refresh(ctx, None)),
    ]
    view, refused = context.guarded(hub.batches.batch, batch_id, actor=ctx.actor)
    shown.append(("batch refusal", refused))
    if view is None:
        return shown
    shown.append(("batch view", view))
    stack: list[int] = []
    while True:
        page = batch_page.rows_at(ctx, view, stack)
        shown.append(("rows", batch_page.body(ctx, view, page, len(stack))))
        if page is None or page.after is None:
            break
        stack.append(page.after)
    for review in view.sample:
        if not review.open:
            continue
        case, refused = context.guarded(hub.decisions.case, review.task_id, actor=ctx.actor)
        shown.append(("sample case refusal", refused))
        if case is not None:
            shown.append(("sample pane", decide.render(case, split="birth_date")))
    return shown


def test_the_alike_reviews_and_a_batch_show_no_personal_value_in_any_state(
    alike: Hub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(capacity, "BATCH_PAGE", 3)  # every row is seen, over several pages
    steward = context.for_test(alike)
    assert len(ALIKE_VALUES) > 60
    group = alike.batches.groups(actor=steward.actor, entity="person").groups[0]
    batch_id = alike.batches.draw(group.group_key, actor=steward.actor, entity="person").batch_id
    first = alike.store.batch_items(batch_id, ("sample",), ("open",), None, 10)[0].task_id
    states: dict[str, list[tuple[str, Any]]] = {}

    def look(state: str) -> None:
        for role in ("data_steward", "consumer"):
            states[f"{state} as {role}"] = batch_screens(alike, role, batch_id)

    look("drawn")
    helpers.decide_sample(
        alike, batch_id, actor=steward.actor, answers={first: ("not_a_match", "birth_date")}
    )
    alike.batches.refresh(batch_id)
    helpers.decide_sample(alike, batch_id, actor=steward.actor)  # the top-up
    view = alike.batches.batch(batch_id, actor=steward.actor)
    assert view.splits and view.status == "ready", (view.status, view.splits)
    look("split")
    alike.batches.prepare(batch_id, actor=steward.actor)
    look("prepared")
    entry = alike.batches.stage(batch_id, actor=steward.actor)
    assert entry.status == "staged"
    look("staged")
    helpers.flush_past_window(alike)
    assert alike.batches.batch(batch_id, actor=steward.actor).status == "committed"
    look("committed")
    leaking = {
        f"{state}: {name}": found
        for state, pieces in states.items()
        for name, piece in pieces
        if (found := alike_leaks(piece))
    }
    assert leaking == {}
    rows = [piece for name, piece in states["prepared as data_steward"] if name == "rows"]
    assert len(rows) >= 2  # the check saw more than one page of rows
