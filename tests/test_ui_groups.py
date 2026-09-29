"""The Alike reviews page without a browser (story 3.3, plan 5 B.2 and B.8): its regions, the groups' table
with marks in words, capped counts and their links, the figures behind their disclosures, every Batch cell,
the withdrawn notice with no restore control, the disabled draw of a data owner and the consumer's page.

The views are rendered from the invented samples of `tests/workbench_samples.py`. The page's callbacks (G1 the
list, G2 a draw) run last, on a world of alike reviews on an in-memory DuckDB, as pure functions and posted
through Dash as the browser would; nothing here starts a server or a browser.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
from dash import Dash
from dash.development.base_component import Component

from mdm import capacity
from mdm.backend.factory import open_store
from mdm.models.batch import BATCH_ID_RE, SIGNATURE_KEY_RE
from mdm.services.context import Hub
from mdm.ui import app as app_module
from mdm.ui import context, ids
from mdm.ui.components import breaker, groups
from mdm.ui.pages import groups as groups_page
from tests import helpers
from tests import workbench_samples as samples
from tests.conftest import base_settings, open_hub

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
    """The text of a tree: every string among its children (and a button's label), in order."""
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


def page(**kwargs: Any) -> list[Component]:
    options = {"can_draw": True, "role": "data_steward", "sample_base": 5, **kwargs}
    return groups.render(samples.GROUP_LIST, **options)


def row_of(tree: Any, group_key: str) -> Component:
    [found] = [c for c in of_type(tree, "Tr") if prop(c, "data-group") == group_key]
    return found


def cell(row: Component, column: str) -> Component:
    [found] = [c for c in row.children if prop(c, "data-column") == column]
    return found


# ---------------------------------------------------------------------------------------------- the page


def test_the_page_has_one_title_and_sections_that_open_with_their_heading() -> None:
    tree = page()
    [title] = of_type(tree, "H1")
    assert title.children == "Alike reviews"
    assert text_of(tree[1]) == groups.INTRODUCTION
    sections = of_type(tree, "Section")
    assert [prop(s, "aria-label") for s in sections] == ["Waiting for your confirmation", "Groups"]
    for section in sections:
        assert type(section.children[0]).__name__ == "H2"
        assert section.children[0].children == prop(section, "aria-label")


def test_the_groups_table_has_a_caption_and_column_headers() -> None:
    tree = page()
    [table] = of_type(tree, "Table")
    assert "mdm-group-table" in table.className and "mdm-table" in table.className
    [caption] = of_type(table, "Caption")
    assert caption.children == "Groups of alike reviews, largest first" and caption.className == "mdm-sr-only"
    headers = of_type(table, "Th")
    assert [h.children for h in headers] == list(groups.COLUMNS)
    assert all(h.scope == "col" for h in headers)
    rows = [c for c in of_type(table, "Tr") if prop(c, "data-group")]
    assert [prop(r, "data-group") for r in rows] == [g.group_key for g in samples.GROUP_LIST.groups]
    for row in rows:
        assert [prop(c, "data-column") for c in row.children] == list(groups.COLUMNS)


def test_a_pattern_is_its_marks_each_beside_its_words() -> None:
    tree = groups.marks(samples.MARKS_PERSON)
    assert text_of(tree) == (
        "Given name = the same · Family name = the same · Birth date ≈ similar · Email ∅ missing · "
        "Phone ∅ missing · Postcode ∅ missing · Person reference ∅ missing"
    )
    symbols = [c for c in walk(tree) if getattr(c, "className", None) == "mdm-symbol"]
    assert len(symbols) == 7 and all(prop(s, "aria-hidden") == "true" for s in symbols)
    marks = [c for c in walk(tree) if "mdm-mark" in (getattr(c, "className", None) or "").split()]
    assert [m.className for m in marks][:3] == [
        "mdm-mark mdm-agree",
        "mdm-mark mdm-agree",
        "mdm-mark mdm-partial",
    ]
    organisation = groups.marks(samples.MARKS_ORGANISATION)
    different = [c for c in walk(organisation) if "mdm-disagree" in (getattr(c, "className", None) or "")]
    assert (
        len(different) == 1 and "Postcode" in text_of(different[0]) and "different" in text_of(different[0])
    )


def test_a_count_is_capped_and_opens_the_groups_reviews_in_the_inbox() -> None:
    link = groups.count_link(samples.GROUP_PERSON)
    assert link.children == "612" and link.href == f"/?group={samples.GROUP_KEY_PERSON}"
    assert prop(link, "aria-label") == "612 reviews with this pattern: open them in the inbox"
    capped = groups.count_link(samples.GROUP_AT_CAP)
    assert capped.children == "999+"
    assert prop(capped, "aria-label") == "999+ reviews with this pattern: open them in the inbox"


def test_the_label_history_and_the_agreement_open_their_figures() -> None:
    labels = groups.labels_cell(samples.LABELS)
    assert type(labels).__name__ == "Details"
    assert text_of(labels.children[0]) == "Linked in 136 of 140 labels (97%)"
    assert text_of(labels.children[1]) == (
        "The latest steward label on each pair with this pattern: 136 linked, 4 Not a match."
    )
    agreement = groups.agreement_cell(samples.AGREEMENT)
    assert text_of(agreement.children[0]) == "Blind review agreed 70 of 71 (98%)"
    assert (
        text_of(agreement.children[1])
        == "Automatic links 60 of 60 · stewards' decisions 8 of 9 · batches 2 of 2"
    )
    assert text_of(groups.labels_cell(samples.LABELS_NONE)) == "No labels yet"
    assert text_of(groups.agreement_cell(samples.AGREEMENT_NONE)) == "No blind review yet"
    assert groups.labels_summary(samples.GROUP_AT_CAP.labels) == "Linked in 999+ of 999+ labels"
    only_batches = replace(samples.AGREEMENT, agreed=2, reviewed=2, by_origin={"batch": (2, 2)})
    assert groups.by_origin(only_batches) == "Batches 2 of 2"


def test_percentages_round_down() -> None:
    assert groups.percent(39, 40) == 97 and groups.percent(70, 71) == 98 and groups.percent(0, 0) == 0
    assert groups.labels_summary(replace(samples.LABELS, matched=199, not_matched=1)) == (
        "Linked in 199 of 200 labels (99%)"
    )


def test_the_batch_cell_offers_a_draw_names_the_open_batch_or_says_why_not() -> None:
    tree = page()
    draw = cell(row_of(tree, samples.GROUP_KEY_ORGANISATION), "Batch")
    [button] = of_type(draw, "Button")
    assert button.children == "Draw a forced sample"  # no size: the draw says how many in its notification
    assert button.variant == "default" and not button.disabled
    assert button.id == ids.group_draw(samples.GROUP_KEY_ORGANISATION, "organisation")
    opened = cell(row_of(tree, samples.GROUP_KEY_PERSON), "Batch")
    [link] = of_type(opened, "Anchor")
    assert link.href == f"/batch/{samples.BATCH_ID}"
    assert link.children == f"Batch {samples.BATCH_ID}: forced sample, 3 of 9 decided"
    assert not of_type(opened, "Button")
    small = cell(row_of(tree, samples.GROUP_KEY_SMALL), "Batch")
    assert text_of(small) == "Too few to link together: a forced sample takes at least 5."
    assert not of_type(small, "Button")
    words = " ".join(text_of(c) for c in walk(tree) if isinstance(getattr(c, "children", None), str))
    assert "ready to check" not in words.lower()
    agreed = replace(samples.GROUP_PERSON, batch_status="ready", batch_words="sample agreed")
    assert text_of(groups.batch_link(agreed)) == f"Batch {samples.BATCH_ID}: sample agreed"


def test_withdrawn_bulk_decisions_show_the_notice_and_no_draw_and_no_restore() -> None:
    tree = page()
    withdrawn = cell(row_of(tree, samples.GROUP_KEY_WITHDRAWN), "Batch")
    assert not of_type(withdrawn, "Button")
    shown = text_of(withdrawn)
    assert shown.startswith(
        "Bulk decisions for this pattern are withdrawn: blind review agreed 3 of the last 5 batch links (60%), "
        "confidently below 95%."
    )
    assert (
        "Only a data owner restores bulk decisions, on the command line; there is no button for it here."
        in shown
    )
    [notice] = [c for c in walk(withdrawn) if "mdm-bulk-notice" in (getattr(c, "className", None) or "")]
    assert "mdm-notice-warning" in notice.className and prop(notice, "role") is None  # no live region
    for component in walk(tree):
        if type(component).__name__ in ("Button", "Anchor", "A"):
            assert "restore" not in text_of(component).lower()
    assert breaker.bulk_sentence(replace(samples.BULK_WITHDRAWN, figures={})) == (
        "Bulk decisions for this pattern are withdrawn: blind review agreed with too few of its batch links."
    )


def test_a_data_owner_sees_every_draw_disabled_with_its_reason() -> None:
    tree = page(can_draw=False, role="data_owner")
    [why] = [c for c in walk(tree) if getattr(c, "id", None) == ids.GROUPS_DRAW_WHY]
    assert why.children == "Your role, data owner, cannot decide alike reviews together."
    buttons = of_type(tree, "Button")
    assert buttons and all(b.disabled for b in buttons)
    assert all(prop(b, "aria-describedby") == ids.GROUPS_DRAW_WHY for b in buttons)
    steward = page()
    assert not [c for c in walk(steward) if getattr(c, "id", None) == ids.GROUPS_DRAW_WHY]


def test_the_batches_to_confirm_name_their_maker_by_role() -> None:
    tree = page()
    [item] = [c for c in of_type(tree, "Li") if prop(c, "data-batch") == samples.BATCH_ID_OTHER]
    assert text_of(item) == (
        f"Batch {samples.BATCH_ID_OTHER} · Person · 566 links, prepared by a data steward Check and confirm"
    )
    [link] = of_type(item, "Anchor")
    assert link.children == "Check and confirm" and link.href == f"/batch/{samples.BATCH_ID_OTHER}"
    undo = replace(samples.BATCH_LINE, kind="compensate", decisions=1)
    assert "undoes 1 link, prepared by a data steward" in text_of(groups.confirm_line(undo))
    none = groups.render(replace(samples.GROUP_LIST, to_confirm=()), can_draw=True, role="data_steward")
    assert "Waiting for your confirmation" not in [c.children for c in of_type(none, "H2")]


def test_the_page_says_what_it_grouped_and_when_nothing_is_alike() -> None:
    # finding 20: the window is a limit per entity, never a count of open reviews
    assert groups.grouped_line(samples.GROUP_LIST) == (
        "Grouped from the open reviews due soonest, at most 1,000 per entity. 12 reviews scored under an earlier "
        "rule version are not grouped."
    )
    assert groups.grouped_line(replace(samples.GROUP_LIST, window=250, older_rules=0)) == (
        "Grouped from the open reviews due soonest, at most 250 per entity."
    )
    one = replace(samples.GROUP_LIST, older_rules=1)
    assert groups.grouped_line(one).endswith("1 review scored under an earlier rule version is not grouped.")
    empty = groups.render(samples.GROUP_LIST_EMPTY, can_draw=True, role="data_steward")
    assert "No two open reviews share a pattern yet." in [text_of(c) for c in of_type(empty, "P")]
    assert not of_type(empty, "Table")
    assert groups.grouped_line(samples.GROUP_LIST_EMPTY) == (
        "Grouped from the open reviews due soonest, at most 1,000 per entity."
    )
    assert capacity.GROUP_WINDOW == capacity.COUNT_CAP


def test_a_consumer_is_told_why_no_alike_reviews_wait() -> None:
    tree = groups.consumer("consumer")
    assert [c.children for c in of_type(tree, "H1")] == ["Alike reviews"]
    assert text_of(tree[1]) == "Your role, consumer, decides no tasks, so no alike reviews wait here for you."


def test_no_signature_reaches_an_id_an_address_or_a_data_attribute() -> None:
    tree = page()
    for component in walk(tree):
        found = component.to_plotly_json()["props"]
        for name, value in found.items():
            if name == "id" or name == "href" or name.startswith("data-") or name.startswith("aria-"):
                shown = json.dumps(value, ensure_ascii=False)
                assert not any(mark in shown for mark in ("≈", "∅", "≠", " · ")), (name, shown)
                assert "given_name" not in shown and "birth_date" not in shown, (name, shown)


# ---------------------------------------------------------------------------------------------- on a real hub
#
# G1 (the list) and G2 (a draw) over 12 alike Person reviews on an in-memory DuckDB.


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


def the_group(tree: Any) -> Component:
    rows = [c for c in of_type(tree, "Tr") if prop(c, "data-group")]
    assert rows, "no group listed"
    return rows[0]


def test_the_page_lists_the_open_groups_with_their_count_and_a_draw(alike: Hub) -> None:
    ctx = context.for_test(alike)
    shown = groups_page.layout(ctx, {"entity": "person"})
    [root] = [c for c in walk(shown) if getattr(c, "id", None) == ids.GROUPS]
    assert prop(root, "data-mdm-keys") is None  # no single key acts here
    row = the_group(shown)
    key = prop(row, "data-group")
    assert SIGNATURE_KEY_RE.match(key)
    [count] = [c for c in of_type(row, "Anchor") if c.href == f"/?group={key}"]
    assert text_of(count) == "12"
    [draw] = [c for c in walk(row) if getattr(c, "id", None) == ids.group_draw(key, "person")]
    assert draw.disabled is False and text_of(draw) == groups.DRAW
    stores = [c for c in walk(shown) if type(c).__name__ == "Store"]
    assert [c.data for c in stores if c.id == ids.GROUPS_VERSION] == [0]
    assert [c.data for c in stores if c.id == ids.GROUPS_ADDRESS] == [{"entity": "person"}]
    assert [c for c in walk(shown) if getattr(c, "id", None) == ids.GROUPS_SETTLED]
    page_, note, on_inbox = app_module.render_route(ctx, "/groups", "", "person")
    assert note is None and on_inbox is False and "Alike reviews" in text_of(page_)


def test_a_draw_opens_the_batch_and_says_how_many_it_drew(alike: Hub) -> None:
    ctx = context.for_test(alike)
    key = prop(the_group(groups_page.view(ctx, "person")), "data-group")
    done = groups_page.draw(ctx, key, "person")
    assert done.path is not None and BATCH_ID_RE.match(done.path.removeprefix("/batch/"))
    assert done.note["title"] == "Sample drawn"
    assert done.note["message"] == "Forced sample drawn: 5 of 12 reviews. Decide them in the inbox."
    again = groups_page.view(ctx, "person")
    batch_id = done.path.removeprefix("/batch/")
    [link] = [c for c in of_type(the_group(again), "Anchor") if c.href == done.path]
    assert text_of(link) == f"Batch {batch_id}: forced sample, 0 of 5 decided"
    twice = groups_page.draw(ctx, key, "person")
    assert twice.path is None and twice.note["title"] == "Already a batch"
    unknown = groups_page.draw(ctx, "SIG-0000000000000000", "person")
    assert unknown.path is None and unknown.note["title"] == "Group gone"
    garbled = groups_page.draw(ctx, "given_name= · family_name=", "person")
    assert garbled.path is None and garbled.note["color"] == "yellow"


def test_a_draw_refused_as_too_small_names_the_reviews_it_found_never_a_setting(alike: Hub) -> None:
    # finding 21: with a forced sample of at least 11 (a setting), a group of 12 offers a draw; two of its
    # reviews claimed by another steward leave 10, which the refusal names, never a fixed base of 5
    local = Hub.open(alike.settings.with_(forced_sample_base=11), store=alike.store, as_role="data_owner")
    ctx = context.for_test(local)
    key = prop(the_group(groups_page.view(ctx, "person")), "data-group")
    other = context.for_test(local, role="coordinating_steward")
    rows = local.inbox.page("team", actor=other.actor, group=key).rows
    for row in rows[:2]:
        local.inbox.claim(row.task_id, actor=other.actor)
    refused = groups_page.draw(ctx, key, "person")
    assert refused.path is None and refused.note["title"] == "Too few"
    assert refused.note["message"] == (
        "Too few alike reviews are free to draw: a forced sample would take all 10 of them. Decide them one by one."
    )
    assert "5" not in refused.note["message"]


def test_a_data_owner_sees_the_groups_but_every_draw_waits_and_a_consumer_sees_none(alike: Hub) -> None:
    owner = context.for_test(alike, role="data_owner")
    shown = groups_page.view(owner, None)
    key = prop(the_group(shown), "data-group")
    [draw] = [c for c in walk(shown) if getattr(c, "id", None) == ids.group_draw(key, "person")]
    assert draw.disabled is True and prop(draw, "aria-describedby") == ids.GROUPS_DRAW_WHY
    [why] = [c for c in walk(shown) if getattr(c, "id", None) == ids.GROUPS_DRAW_WHY]
    assert text_of(why) == "Your role, data owner, cannot decide alike reviews together."
    refused = groups_page.draw(owner, key, "person")
    assert refused.path is None and refused.note["color"] == "yellow"
    consumer = groups_page.view(context.for_test(alike, role="consumer"), None)
    assert "Your role, consumer, decides no tasks, so no alike reviews wait here for you." in text_of(
        consumer
    )
    assert not of_type(consumer, "Table")


PORT = 8050


@pytest.fixture
def app(alike: Hub) -> Iterator[Dash]:
    built = app_module.create_app(alike.settings, hub=alike, worker=False, listen=("127.0.0.1", PORT))
    state = built.server.extensions[context.EXTENSION]
    state.owns_hub = False
    try:
        yield built
    finally:
        state.close()


def _post(app: Dash, body: dict) -> dict:
    answer = app.server.test_client().post(
        "/_dash-update-component",
        data=json.dumps(body),
        headers={"Content-Type": "application/json", "Host": f"127.0.0.1:{PORT}"},
    )
    assert answer.status_code in (200, 204), answer.get_data(as_text=True)[:300]
    return json.loads(answer.get_data(as_text=True) or "{}")


def test_a_draw_posted_through_dash_lands_on_the_batch_page(app: Dash, alike: Hub) -> None:
    key = prop(the_group(groups_page.view(context.for_test(alike), "person")), "data-group")
    [callback] = [c for c in app._callback_list if c["output"].startswith(f"{ids.GROUPS_VERSION}.data@")]
    draw = ids.group_draw(key, "person")
    body = {
        "output": callback["output"],
        "outputs": {"id": ids.GROUPS_VERSION, "property": "data"},
        "inputs": [[{"id": draw, "property": "n_clicks", "value": 1}]],
        "state": [
            {"id": ids.PERSONA, "property": "data", "value": "data_steward"},
            {"id": ids.GROUPS_VERSION, "property": "data", "value": 2},
        ],
        "changedPropIds": [json.dumps(draw, sort_keys=True, separators=(",", ":")) + ".n_clicks"],
    }
    answer = _post(app, body)
    assert answer.get("response", {}) == {}  # the page is left, not drawn again
    path = answer["sideUpdate"][ids.URL]["pathname"]
    assert path.startswith("/batch/BAT-") and answer["sideUpdate"][ids.URL]["search"] == ""
    note = answer["sideUpdate"][ids.NOTIFY]["sendNotifications"][0]
    assert note["title"] == "Sample drawn"
    again = _post(app, body)  # the group has its batch now: refused, and the list is read again
    assert again["response"][ids.GROUPS_VERSION]["data"] == 3
    assert again["sideUpdate"][ids.NOTIFY]["sendNotifications"][0]["title"] == "Already a batch"
    body["inputs"] = [[{"id": draw, "property": "n_clicks", "value": None}]]
    assert _post(app, body).get("response", {}) == {}  # a redrawn button with no click draws nothing


def test_the_list_follows_the_header_entity_through_its_address_bridge(app: Dash, alike: Hub) -> None:
    [callback] = [c for c in app._callback_list if c["output"] == f"{ids.GROUPS_LIST}.children"]
    address = ids.GROUPS_ADDRESS
    key = json.dumps(address, sort_keys=True, separators=(",", ":"))

    def load(entity: str) -> str:
        body = {
            "output": callback["output"],
            "outputs": {"id": ids.GROUPS_LIST, "property": "children"},
            "inputs": [
                {
                    "id": address,
                    "property": "data",
                    "value": {"entity": entity, "search": "", "path": "/groups"},
                },
                {"id": ids.GROUPS_SETTLED, "property": "data", "value": None},
                {"id": ids.GROUPS_VERSION, "property": "data", "value": 0},
            ],
            "state": [{"id": ids.PERSONA, "property": "data", "value": "data_steward"}],
            "changedPropIds": [f"{key}.data"],
        }
        return json.dumps(_post(app, body)["response"][ids.GROUPS_LIST]["children"])

    assert "SIG-" in load("person")
    assert "SIG-" not in load("organisation")
