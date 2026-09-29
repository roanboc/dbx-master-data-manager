"""The workbench's shell without a browser: the app, its callbacks and IDs, the route, the header, the
tray, the rail, the keys, the messages and the small shared parts (plan B.9.2; DuckDB only).

The pure functions behind the callbacks are called directly; the app is built on an in-memory DuckDB with
the starter models published.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import flask
import pytest
from dash import Dash, no_update
from dash.development.base_component import Component

from mdm import capacity
from mdm.backend.factory import open_store
from mdm.config import Settings
from mdm.models.authority import ACTIONS, Actor
from mdm.models.errors import Conflict, Forbidden, NotFound, PlatformRefused
from mdm.models.workbench import ALL_VIEWS, TrayView
from mdm.services.authority import AuthorityService
from mdm.services.context import Hub
from mdm.ui import app as app_module
from mdm.ui import context, ids, layout, messages
from mdm.ui.components import band, common, icons, keys, rail, reveal, tray
from mdm.ui.pages import record
from tests import workbench_samples as samples
from tests.conftest import base_settings, open_hub

SRC = Path(__file__).resolve().parents[1] / "src" / "mdm"


# ------------------------------------------------------------------------------------------ fixtures


@pytest.fixture
def hub() -> Iterator[Hub]:
    """An in-memory DuckDB with the starter models published, as the persona data_owner."""
    settings = base_settings()
    store = open_store(settings)
    store.init_schema(create_landing=True)
    opened = open_hub(settings, store, "duckdb")
    try:
        yield opened
    finally:
        opened.close()
        store.close()


@pytest.fixture
def app(hub: Hub) -> Iterator[Dash]:
    built = app_module.create_app(hub.settings, hub=hub, worker=False)
    try:
        yield built
    finally:
        built.server.extensions[context.EXTENSION].close()


def walk(component: Any) -> Iterator[Component]:
    """Every component in a tree, through `children` and every other prop that holds components."""
    if isinstance(component, (list, tuple)):
        for item in component:
            yield from walk(item)
        return
    if not isinstance(component, Component):
        return
    yield component
    for name in component._prop_names:
        value = getattr(component, name, None)
        if isinstance(value, (Component, list, tuple)):
            yield from walk(value)


def ids_in(tree: Any) -> list[Any]:
    return [c.id for c in walk(tree) if getattr(c, "id", None) is not None]


def texts_in(tree: Any) -> list[str]:
    found: list[str] = []
    for component in walk(tree):
        for name in component._prop_names:
            value = getattr(component, name, None)
            if isinstance(value, str):
                found.append(value)
            elif isinstance(value, (list, tuple)):
                found.extend(item for item in value if isinstance(item, str))
    return found


def text_of(tree: Any) -> str:
    """The visible text of a tree, in order (strings among children only)."""
    if isinstance(tree, str):
        return tree
    if isinstance(tree, (list, tuple)):
        return "".join(text_of(item) for item in tree)
    if isinstance(tree, Component):
        return text_of(getattr(tree, "children", None) or "")
    return ""


ID_VALUES = {value for name, value in vars(ids).items() if name.isupper() and isinstance(value, str)}


def dependency_ids(app: Dash) -> list[tuple[Any, str, str]]:
    """(component ID, property, role) of every output, input and state of every callback."""
    found = []
    for callback in app._callback_list:
        output = callback["output"]
        parts = output[2:-2].split("...") if output.startswith("..") else [output]
        for part in parts:
            raw, _, prop = part.rpartition(".")
            found.append((json.loads(raw) if raw.startswith("{") else raw, prop.split("@")[0], "output"))
        for role in ("inputs", "state"):
            for dependency in callback.get(role, []):
                raw = dependency["id"]
                parsed = json.loads(raw) if isinstance(raw, str) and raw.startswith("{") else raw
                found.append((parsed, dependency["property"], role))
    return found


def fake_ctx(**hub_parts: Any) -> context.UiContext:
    """A steward's context over a stand-in hub with only the services a test gives."""
    actor = Actor("persona:data_steward", "person", "data_steward", persona=True)
    return context.UiContext(SimpleNamespace(**hub_parts), Settings(), actor, samples.HUB_BADGES)


# ------------------------------------------------------------------------------------------ the app


def test_the_app_serves_the_shell_its_layout_and_its_callbacks(app: Dash) -> None:
    client = app.server.test_client()
    assert client.get("/").status_code == 200
    page = client.get("/_dash-layout")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    for shown in (ids.SHELL, ids.URL, ids.PAGE, ids.TRAY_BUTTON, ids.NOTIFY, ids.SKIP_LINK, ids.KEY_EVENT):
        assert f'"{shown}"' in body
    dependencies = client.get("/_dash-dependencies")
    assert dependencies.status_code == 200 and json.loads(dependencies.get_data(as_text=True))
    assert '<html lang="en-GB">' in client.get("/").get_data(as_text=True)


def test_the_app_keeps_its_title_and_asks_no_network(app: Dash) -> None:
    assert app.title == "Master Data Manager"
    assert app.config.update_title is None
    assert app.config.suppress_callback_exceptions is True
    assert app.config.external_stylesheets == [] and app.config.external_scripts == []
    assert isinstance(app.validation_layout, Component)


def test_every_id_is_declared_once() -> None:
    names = [name for name, value in vars(ids).items() if name.isupper() and isinstance(value, str)]
    values = [getattr(ids, name) for name in names]
    assert len(values) == len(set(values))
    assert all(re.fullmatch(r"[a-z][a-z0-9-]*", value) for value in values)


def test_every_callback_id_is_in_ids_and_in_the_validation_layout(app: Dash) -> None:
    present = set()
    for found in ids_in(app.validation_layout):
        present.add(json.dumps(found, sort_keys=True) if isinstance(found, dict) else found)
    known = ID_VALUES
    for component_id, prop, role in dependency_ids(app):
        if isinstance(component_id, dict):
            assert component_id.get("type") in known, (component_id, prop, role)
            wildcard = any(isinstance(v, list) for v in component_id.values())
            if not wildcard:
                assert json.dumps(component_id, sort_keys=True) in present, (component_id, prop, role)
            continue
        assert component_id in known, (component_id, prop, role)
        assert component_id in present, (component_id, prop, role)


def test_every_duplicate_output_waits_for_a_change(app: Dash) -> None:
    for callback in app._callback_list:
        if "@" in callback["output"]:
            assert callback["prevent_initial_call"] is True, callback["output"]


def test_no_callback_goes_to_the_global_registry(app: Dash) -> None:
    from dash._callback import GLOBAL_CALLBACK_LIST, GLOBAL_CALLBACK_MAP

    assert GLOBAL_CALLBACK_LIST == [] and GLOBAL_CALLBACK_MAP == {}


def test_no_two_callbacks_feed_each_other(app: Dash) -> None:
    """Dash refuses a cycle between callbacks; one callback may keep two props in step."""
    edges: dict[int, tuple[set, set]] = {}
    for index, callback in enumerate(app._callback_list):
        outputs = {
            (json.dumps(i, sort_keys=True), p) for i, p, r in dependency_ids_of(callback) if r == "output"
        }
        inputs = {
            (json.dumps(i, sort_keys=True), p) for i, p, r in dependency_ids_of(callback) if r == "inputs"
        }
        edges[index] = (inputs, outputs)
    for a, (_, out_a) in edges.items():
        for b, (in_b, out_b) in edges.items():
            if a != b and out_a & in_b:
                assert not (out_b & edges[a][0]), (a, b)


def dependency_ids_of(callback: dict) -> list[tuple[Any, str, str]]:
    fake = SimpleNamespace(_callback_list=[callback])
    return dependency_ids(fake)  # type: ignore[arg-type]


def test_the_app_closes_what_it_opened(hub: Hub) -> None:
    built = app_module.create_app(hub.settings, hub=hub, worker=True)
    state = built.server.extensions[context.EXTENSION]
    assert state.worker is not None and state.owns_hub is False
    state.close()
    state.close()  # twice is safe
    assert state.closed


# ------------------------------------------------------------------------------------------ the route


def test_the_route_reads_paths_and_keeps_only_codes_of_the_query() -> None:
    assert app_module.parse_route("/") == ("inbox", ())
    assert app_module.parse_route(None) == ("inbox", ())
    assert app_module.parse_route("/record/ORG-000123") == ("record", ("ORG-000123",))
    assert app_module.parse_route("/record/crm%3AC000123") == ("record", ("crm:C000123",))
    assert app_module.parse_route("/source/crm/C000123") == ("source", ("crm", "C000123"))
    assert app_module.parse_route("/records") == ("unknown", ())
    assert app_module.parse_route("/source/crm") == ("unknown", ())
    query = app_module.query_of("?view=team&kind=review&task=TSK-000001&name=Tamsin%20Quorrel&tab=x%40y")
    assert query == {"view": "team", "kind": "review", "task": "TSK-000001"}


def test_an_unknown_address_is_echoed_only_when_it_has_a_safe_shape() -> None:
    assert "There is no page at /records; this is the inbox." in text_of(app_module.address_note("/records"))
    shown = text_of(app_module.address_note("/record/Tamsin Quorrel"))
    assert "Tamsin" not in shown and "that address" in shown


def test_the_route_rebuilds_nothing_for_the_same_path_and_role(hub: Hub) -> None:
    ctx = context.for_test(hub, role="data_steward")
    on_screen = {"path": "/", "persona": "data_steward"}
    assert app_module.route_outputs(ctx, "/", "?view=team", on_screen, "") == (no_update,) * 5
    page, note, on_inbox, navbar, key = app_module.route_outputs(ctx, "/records", "", on_screen, "")
    assert key == {"path": "/records", "persona": "data_steward"}
    assert on_inbox is True and navbar == layout.NAV_READER and note is not None
    other = context.for_test(hub, role="data_owner")
    assert app_module.route_outputs(other, "/", "", on_screen, "")[4] == {
        "path": "/",
        "persona": "data_owner",
    }


def test_switching_between_the_two_data_steward_personas_rebuilds_the_page(hub: Hub) -> None:
    """The two data-steward personas share a role but are two stewards: the route is keyed on the persona,
    so a switch rebuilds the page (a batch the first prepared is the second's to confirm); a user a
    Databricks App forwards is keyed on the role, so no user name lands in the browser (review 3.3)."""
    first = context.for_test(hub, role="data_steward")
    second = context.for_test(hub, role="data_steward_2")
    assert first.actor.role == second.actor.role == "data_steward"
    on_screen = app_module.route_key("/batch/BAT-00000000000000000001", first)
    rebuilt = app_module.route_outputs(second, "/batch/BAT-00000000000000000001", "", on_screen, "")
    assert rebuilt[4] == {"path": "/batch/BAT-00000000000000000001", "persona": "data_steward_2"}
    assert rebuilt[0] is not no_update
    assert app_module.route_outputs(first, "/batch/BAT-00000000000000000001", "", on_screen, "") == (
        (no_update,) * 5
    )
    forwarded = replace(first, actor=Actor(name="user-7", kind="person", role="data_steward"))
    assert app_module.route_key("/", forwarded) == {"path": "/", "persona": "data_steward"}


def test_a_page_that_fails_becomes_a_notice(hub: Hub, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: Any, **kwargs: Any) -> None:
        raise KeyError("Tamsin Quorrel")

    monkeypatch.setattr(record, "layout", broken)
    ctx = context.for_test(hub)
    page, note, on_inbox = app_module.render_route(ctx, "/record/ORG-000001", "", "")
    shown = text_of(page)
    assert "KeyError" in shown and "Tamsin" not in shown
    assert note is None and on_inbox is False


def test_a_consumer_has_no_inbox_link_and_no_rail(hub: Hub) -> None:
    ctx = context.for_test(hub, role="consumer")
    assert app_module.navbar_class(ctx) == layout.NAV_NONE
    role, breaches, style, views, tray_style = layout.header_state(ctx, "", "/", "")
    assert role == "Consumer (persona)"
    assert breaches == "" and style == layout.HIDDEN and views == []
    assert tray_style == layout.HIDDEN  # a consumer decides nothing, so no tray
    assert app_module.navbar_class(context.for_test(hub)) == layout.NAV_READER


# ------------------------------------------------------------------------------------------ the header


def test_the_header_offers_personas_on_a_local_store_only() -> None:
    local = ids_in(layout.header(Settings(), samples.HUB_BADGES))
    shared = ids_in(layout.header(Settings(), samples.HUB_BADGES_SHARED))
    assert ids.PERSONA_SELECT in local and ids.PERSONA_SELECT not in shared
    for present in (ids.ENTITY_SELECT, ids.TRAY_BUTTON, ids.BREACHES, ids.ROLE_BADGE, ids.ENGINE_BADGE):
        assert present in local and present in shared
    assert ids.HELP_OPEN in local and ids.SCHEME_TOGGLE in local
    assert "DuckDB · stub" in texts_in(layout.header(Settings(), samples.HUB_BADGES))


def test_every_header_control_has_a_name() -> None:
    header = layout.header(Settings(), samples.HUB_BADGES)
    named = {
        ids.ENTITY_SELECT: "Entity",
        ids.PERSONA_SELECT: "Act as",
        ids.HELP_OPEN: "Keyboard shortcuts",
        ids.SCHEME_TOGGLE: "Colour scheme",
        ids.BURGER: "Navigation",
    }
    for component in walk(header):
        if getattr(component, "id", None) in named:
            assert getattr(component, "aria-label") == named[component.id]


def test_the_persona_menu_offers_every_role_and_a_second_data_steward(hub: Hub) -> None:
    select = next(
        c
        for c in walk(layout.header(Settings(), samples.HUB_BADGES))
        if getattr(c, "id", None) == ids.PERSONA_SELECT
    )
    assert [(o["value"], o["label"]) for o in select.data] == [
        ("data_owner", "Data owner"),
        ("data_steward", "Data steward"),
        ("data_steward_2", "Data steward 2"),
        ("coordinating_steward", "Coordinating steward"),
        ("technical_steward", "Technical steward"),
        ("consumer", "Consumer"),
        ("administrator", "Administrator"),
    ]
    assert layout.default_persona(Settings(role="data_steward_2")) == "data_steward_2"
    assert (
        context.persona_of("data_steward_2") == "data_steward_2" and context.persona_of("steward_3") is None
    )
    second = context.for_test(hub, role="data_steward_2")
    assert second.actor == Actor("persona:data_steward_2", "person", "data_steward", persona=True)
    role, *_ = layout.header_state(second, "", "/", "")
    assert role == "Data steward 2 (persona)"
    assert layout.role_text(context.for_test(hub).actor) == "Data steward (persona)"


def test_the_persona_select_starts_as_mdm_role() -> None:
    select = next(
        c
        for c in walk(layout.header(Settings(role="data_owner"), samples.HUB_BADGES))
        if getattr(c, "id", None) == ids.PERSONA_SELECT
    )
    assert select.value == "data_owner"
    assert layout.default_persona(Settings(role="nobody")) == "data_steward"


def test_the_shell_holds_every_store_the_pages_share() -> None:
    shell_ids = ids_in(layout.shell(Settings(), samples.HUB_BADGES))
    for shared in (
        ids.URL, ids.ROUTE, ids.PERSONA, ids.ENTITY, ids.KEYS_ENABLED, ids.KEY_EVENT, ids.TRAY_STATE,
        ids.TRAY_VERSION, ids.SETTLED, ids.TRAY_POLL, ids.CLOCK_TICK, ids.COUNTS_POLL, ids.NOTIFY,
        ids.SKIP_LINK, ids.HEADER, ids.NAVBAR, ids.MAIN, ids.PAGE, ids.ADDRESS_NOTE, ids.NAV_INBOX,
        ids.NAV_VIEWS, ids.HELP_MODAL, ids.KEYS_SWITCH, ids.TRAY_POPOVER, ids.TRAY_LIST,
    ):  # fmt: skip
        assert shared in shell_ids, shared
    assert len(shell_ids) == len(set(map(str, shell_ids)))
    stores = {c.id: c for c in walk(layout.shell()) if type(c).__name__ == "Store"}
    assert stores[ids.PERSONA].storage_type == "session" and stores[ids.ENTITY].storage_type == "session"
    assert stores[ids.KEYS_ENABLED].storage_type == "local"
    for kept_in_memory in (ids.ROUTE, ids.KEY_EVENT, ids.TRAY_STATE, ids.TRAY_VERSION, ids.SETTLED):
        assert stores[kept_in_memory].storage_type == "memory"


def test_the_header_state_counts_once_and_links_breaches(hub: Hub) -> None:
    ctx = context.for_test(hub)
    role, breaches, style, views, tray_style = layout.header_state(ctx, "", "/", "?view=team")
    assert role == "Data steward (persona)" and tray_style == {}
    assert (breaches == "" and style == layout.HIDDEN) or "breaching" in text_of(breaches)
    links = [c for c in walk(views) if type(c).__name__ == "NavLink"]
    assert [c for c in links if getattr(c, "aria-current", None) == "page"][0].href == "/?view=team"


def test_the_header_state_names_the_breaches() -> None:
    ctx = fake_ctx(inbox=SimpleNamespace(counts=lambda **kwargs: samples.COUNTS_AT_CAP))
    _, breaches, style, views, _tray = layout.header_state(ctx, "person", "/record/ORG-000001", "")
    assert text_of(breaches) == "999 breaching"
    assert style == {}
    assert not [c for c in walk(views) if getattr(c, "aria-current", None)]  # off the inbox: none chosen


def test_the_entity_filter_takes_published_entities_only() -> None:
    ctx = fake_ctx()
    assert layout.entity_filter(ctx, "person") == "person"
    assert layout.entity_filter(ctx, "") is None and layout.entity_filter(ctx, "nothing") is None
    assert layout.entity_filter(ctx, {"x": 1}) is None


# ------------------------------------------------------------------------------------------ the rail


def test_the_rail_links_every_view_and_kind_with_capped_counts() -> None:
    tree = rail.render(samples.COUNTS_AT_CAP, {"view": "team", "kind": "review"})
    links = [c for c in walk(tree) if type(c).__name__ == "NavLink"]
    hrefs = [c.href for c in links]
    assert hrefs[:5] == [f"/?view={view}" for view in ("mine", "team", "breaching", "snoozed", "escalated")]
    assert "/?view=team&kind=review" in hrefs and "/?view=team&kind=unresolved_reference" in hrefs
    chosen = [c for c in links if getattr(c, "aria-current", None) == "page"]
    assert [c.href for c in chosen] == ["/?view=team&kind=review"]
    assert "999+" in texts_in(tree)
    assert getattr(links[0], "aria-label") == "My queue, 999+, none claimed by you"
    assert rail.chosen({"view": "nonsense"}) == ("mine", None)
    assert rail.chosen({}) == (None, None)


def test_the_rail_counts_are_plain_text_and_a_breach_count_is_red() -> None:
    tree = rail.render(samples.COUNTS_AT_CAP, {"view": "team"})
    assert not [c for c in walk(tree) if type(c).__name__ == "Badge"]  # no pills
    links = [c for c in walk(tree) if type(c).__name__ == "NavLink"]
    assert {c.variant for c in links} == {"subtle"}
    counts = {c.href: c.rightSection for c in links}
    assert counts["/?view=breaching"].className == "mdm-count mdm-count-alert"
    assert counts["/?view=team"].className == "mdm-count"
    quiet = rail.render(replace(samples.COUNTS_AT_CAP, views={"breaching": 0}), {"view": "team"})
    breaching = next(c for c in walk(quiet) if getattr(c, "href", None) == "/?view=breaching")
    assert breaching.rightSection.className == "mdm-count"  # red only when a breach is counted


def test_the_rail_lists_quality_samples_after_escalated_and_not_as_a_kind() -> None:
    tree = rail.render(samples.COUNTS_SAMPLES, {"view": "samples", "kind": "review"})
    links = [c for c in walk(tree) if type(c).__name__ == "NavLink"]
    hrefs = [c.href for c in links]
    assert hrefs[:6] == [
        "/?view=mine",
        "/?view=team",
        "/?view=breaching",
        "/?view=snoozed",
        "/?view=escalated",
        "/?view=samples",
    ]
    assert not [h for h in hrefs if "quality_sample" in h]  # its own view, never a kind
    assert "Quality sample" not in [c.label for c in links]
    found = links[5]
    assert found.label == "Quality samples" and found.active is True
    assert found.to_plotly_json()["props"]["aria-current"] == "page"
    assert found.rightSection.className == "mdm-count mdm-count-alert"  # a sample is past its 72 h
    assert found.description == "1 overdue"  # said in words too, never by colour alone
    assert found.to_plotly_json()["props"]["aria-label"] == "Quality samples, 12, 1 overdue"
    assert not [c for c in links if c.active and c is not found]  # a kind is never marked in this view
    calm = rail.render(replace(samples.COUNTS_SAMPLES, samples_breaching=0), {"view": "mine"})
    quiet = next(c for c in walk(calm) if getattr(c, "href", None) == "/?view=samples")
    assert quiet.rightSection.className == "mdm-count" and getattr(quiet, "description", None) is None
    assert rail.chosen({"view": "samples", "kind": "held"}) == ("samples", None)
    assert rail.chosen({"view": "nowhere"}) == ("mine", None)
    assert layout.inbox_query("/", "?view=samples&kind=held") == {"view": "samples", "kind": None}


def test_the_rail_is_plain_links() -> None:
    tree = rail.render(samples.COUNTS_UNDER_CAP, {"view": "mine"})
    assert all(getattr(c, "id", None) is None for c in walk(tree))


# ------------------------------------------------------------------------------------------ the tray


def _views(*statuses: str) -> tuple[TrayView, ...]:
    return tuple(
        replace(samples.TRAY_VIEWS[0], entry_id=f"TR-{index:020d}", status=status, deadline=samples.NOW + timedelta(seconds=48))
        for index, status in enumerate(statuses)
    )  # fmt: skip


def test_the_tray_lists_staged_entries_with_a_countdown_and_undo() -> None:
    items = tray.entry_items(samples.TRAY_VIEWS, samples.NOW)
    found = ids_in(items)
    staged = samples.TRAY_VIEWS[0]
    assert ids.tray_countdown(staged.entry_id) in found and ids.tray_undo(staged.entry_id) in found
    for settled in samples.TRAY_VIEWS[1:]:
        assert ids.tray_undo(settled.entry_id) not in found
    shown = texts_in(items)
    assert "Committed as commit 6" in shown
    assert any(text.startswith("Not committed. The source record changed") for text in shown)
    assert "Nothing is waiting" in text_of(tray.entry_items((), samples.NOW, undo_seconds=60)[0])


def test_a_blind_answer_says_whether_it_matched_the_first_decision() -> None:
    agreed, disagreed = samples.TRAY_VIEWS_BLIND
    assert tray.outcome_text(agreed) == "Matched the first decision"
    assert tray.outcome_text(disagreed) == "Differed from the first decision: a review is open"
    shown = texts_in(tray.entry_items(samples.TRAY_VIEWS_BLIND, samples.NOW))
    assert any(text.startswith("Matched the first decision") for text in shown)
    note = tray.settlement_notice(disagreed)
    assert note is not None and note["title"] == "Answered"
    assert note["message"] == (
        "Quality sample: ORG-000211 and ORG-000388 are not the same. "
        "Differed from the first decision: a review is open."
    )


def test_the_tray_refresh_changes_only_when_an_entry_moves() -> None:
    views = list(_views("staged"))
    ctx = fake_ctx(tray=SimpleNamespace(entries=lambda **kwargs: tuple(views)))
    first = tray.refresh(ctx, None)
    assert first is not None and first.staged == 1 and first.settled == [] and first.notices == []
    assert tray.refresh(ctx, first.state) is None  # nothing moved: no update anywhere
    views[0] = replace(views[0], status="committed", outcome="committed", commit_version=7)
    second = tray.refresh(ctx, first.state)
    assert second is not None and second.staged == 0
    assert second.settled == [
        {
            "entry_id": views[0].entry_id,
            "task_id": views[0].task_id,
            "status": "committed",
            "outcome": "committed",
        }
    ]
    assert second.notices[0]["message"].startswith("Committed as commit 7")


def test_an_undone_entry_settles_without_a_second_notice() -> None:
    views = list(_views("staged"))
    ctx = fake_ctx(tray=SimpleNamespace(entries=lambda **kwargs: tuple(views)))
    first = tray.refresh(ctx, None)
    views[0] = replace(views[0], status="undone", outcome="undone")
    second = tray.refresh(ctx, first.state)
    assert second.settled[0]["status"] == "undone" and second.notices == []


def test_a_role_without_tasks_has_an_empty_tray() -> None:
    def refuse(**kwargs: Any) -> None:
        raise AssertionError("not asked")

    ctx = fake_ctx(tray=SimpleNamespace(entries=refuse))
    consumer = replace(ctx, actor=Actor("persona:consumer", "person", "consumer", persona=True))
    change = tray.refresh(consumer, None)
    assert change is not None and change.state == []
    assert text_of(change.children) == tray.NOT_A_WORKER
    assert tray.refresh(consumer, change.state) is None


def test_the_tray_state_holds_codes_ids_and_labels_only() -> None:
    state = tray.state_of(samples.TRAY_VIEWS)
    assert set(state[0]) == {
        "entry_id", "task_id", "label", "deadline_ms", "status", "outcome", "version", "batch_id", "progress",
        "mine",
    }  # fmt: skip
    assert state[0]["deadline_ms"] == int(samples.TRAY_VIEWS[0].deadline.timestamp() * 1000)
    assert state[0]["batch_id"] is None and state[0]["progress"] is None and state[0]["mine"] is True
    batch = tray.state_of([samples.TRAY_BATCH_COMMITTING])[0]
    assert (
        batch["batch_id"] == samples.BATCH_ID
        and batch["progress"] == [1, 3]
        and batch["outcome"] == "committing"
    )
    assert json.loads(json.dumps(batch)) == batch  # a plain document of codes, IDs and counts


def test_undo_from_the_tray_says_how_it_went() -> None:
    ok = fake_ctx(tray=SimpleNamespace(undo=lambda entry_id, **kwargs: None))
    assert tray.undo(ok, "TR-1")["color"] == "teal"

    def too_late(entry_id: str, **kwargs: Any) -> None:
        raise Conflict([entry_id], code="already_settled", status="committed", version=42)

    late = fake_ctx(tray=SimpleNamespace(undo=too_late))
    note = tray.undo(late, "TR-1")
    assert note["color"] == "yellow" and "commit 42" in note["message"]


# ------------------------------------------------------------------------------------------ keys, reveal, small parts


def test_the_key_help_lists_every_key_and_the_switch() -> None:
    modal = keys.help_modal()
    assert ids.KEYS_SWITCH in ids_in(modal)
    shown = texts_in(modal)
    for key, what in keys.KEY_HELP:
        assert what in shown
        for single in key.split(", "):
            assert single in shown
    switch = next(c for c in walk(modal) if getattr(c, "id", None) == ids.KEYS_SWITCH)
    assert switch.label == "Use single-key shortcuts" and switch.checked is True


def test_refusals_name_no_figure_that_is_a_setting() -> None:
    # finding 21: the forced sample's base, the second steward's threshold and the undo window are settings;
    # a refusal names the figure the service found, or none
    assert messages.sentence_for("group_too_small", {"reviews": 8}) == (
        "Too few alike reviews are free to draw: a forced sample would take all 8 of them. Decide them one by one."
    )
    assert messages.sentence_for("group_too_small", {"reviews": 1}) == (
        "Only 1 alike review is free to draw, so a forced sample would take it. Decide it on its own."
    )
    assert messages.sentence_for("group_too_small", {"reviews": 0}).startswith(
        "No alike review of this pattern"
    )
    assert messages.sentence_for("group_too_small", {"reviews": "8"}) == (
        "Too few alike reviews are free to draw: a forced sample would take all of them. Decide them one by one."
    )
    assert messages.sentence_for("checker_required") == (
        "This change needs a second steward to confirm it before it commits."
    )
    assert (
        messages.sentence_for("undo_window_passed")
        == "This batch committed too long ago to be undone as a batch."
    )
    for code in ("group_too_small", "checker_required", "undo_window_passed"):
        for figure in ("5", "250", "30"):
            assert figure not in messages.sentence_for(code), (code, figure)


def test_n_and_l_decide_from_the_comparison_choice_and_the_help_says_so() -> None:
    # finding 19: N (or L) moves focus onto "Which comparison misled?"; there the same key decides, every
    # other key yields to the radios as to any field, and the key help says so
    assert keys.NAMING in texts_in(keys.help_modal())
    assert "press the same key again to decide" in keys.NAMING
    script = (SRC / "ui" / "assets" / "keys.js").read_text(encoding="utf-8")
    assert "var NAMING_KEYS = {l: true, n: true};" in script
    naming = script[script.index("function naming(") : script.index("function onControl(")]
    assert '"#decide-pane .mdm-split-choice"' in naming and 'el.type === "radio"' in naming
    assert "if (typing(e.target) && !naming(e.target, key)) {" in script


def test_focus_is_kept_clear_of_the_sticky_footers() -> None:
    # finding 18 (WCAG 2.2 SC 2.4.11): each sticky footer's scroller keeps room for it, and a control a footer
    # still paints over is scrolled to the middle; the browser check walks the batch rows by Tab
    styles = (SRC / "ui" / "assets" / "styles.css").read_text(encoding="utf-8")
    assert "--mdm-foot-room: 10rem;" in styles
    assert styles.count("scroll-padding-bottom: var(--mdm-foot-room);") == 2
    assert "html:has(.mdm-batch-actions) {" in styles
    script = (SRC / "ui" / "assets" / "keys.js").read_text(encoding="utf-8")
    assert 'var FOOTERS = ".mdm-decide-footer, .mdm-batch-actions";' in script
    assert 'el.scrollIntoView({block: "center", inline: "nearest"});' in script


def test_the_reveal_modal_asks_one_of_four_reasons_with_none_chosen() -> None:
    for page in ids.REVEAL_PAGES:
        modal = reveal.modal(page)
        found = ids_in(modal)
        for part in ids.REVEAL_PARTS:
            if part != ids.REVEAL_OPEN:
                assert ids.reveal(part, page) in found
        group = next(c for c in walk(modal) if getattr(c, "id", None) == ids.reveal(ids.REVEAL_REASON, page))
        assert group.value is None
        radios = [c.value for c in walk(modal) if type(c).__name__ == "Radio"]
        assert radios == ["deciding_task", "source_defect", "subject_request", "audit_check"]
    assert "Deciding about this record" in texts_in(reveal.modal("record"))
    assert reveal.checked_reason("audit_check") == "audit_check"
    assert reveal.checked_reason("because I said so") is None and reveal.checked_reason(None) is None
    assert reveal.open_button("record").id == ids.reveal(ids.REVEAL_OPEN, "record")


def test_a_band_chip_says_its_band_in_words() -> None:
    chip = band.band_chip("review", 79.19)
    assert chip.children == "79 review" and "mdm-band-review" in chip.className
    assert band.band_chip("auto", 99.7).children == "99 automatic"
    assert band.band_chip("distinct", None).children == "distinct"
    assert band.score_text(None) == "" and band.score_text(100.0) == "100"


def test_counts_times_and_countdowns_read_plainly() -> None:
    assert common.count_text(capacity.COUNT_CAP) == "999+"
    assert common.count_text(capacity.COUNT_CAP - 1) == "999"
    assert common.count_text(3) == "3"
    assert common.duration(timedelta(hours=7, minutes=40)) == "7 h 40 min"
    assert common.duration(timedelta(minutes=5)) == "5 min"
    assert common.duration(timedelta(hours=-2)) == "breached 2 h ago"
    assert common.duration(timedelta(seconds=20)) == "under 1 min"
    assert common.duration(timedelta(days=3, hours=2)) == "3 d 2 h"
    now = samples.NOW
    assert common.relative_time(now - timedelta(seconds=10), now) == "just now"
    assert common.relative_time(now - timedelta(minutes=4), now) == "4 min ago"
    assert common.relative_time(now - timedelta(hours=2), now) == "2 h ago"
    assert common.relative_time(now - timedelta(days=3), now) == "3 d ago"
    assert common.relative_time(now + timedelta(minutes=4), now) == "in 4 min"
    assert common.relative_time(now - timedelta(days=12), now) == "15 Sep 2026"
    assert (
        common.countdown(47.2) == "0:48" and common.countdown(-3) == "0:00" and common.countdown(65) == "1:05"
    )


def test_a_notification_is_one_payload_with_a_title() -> None:
    [payload] = common.notify("Done it.")
    assert payload["action"] == "show" and payload["message"] == "Done it." and payload["color"] == "teal"
    assert common.notify("Nope.", "red")[0]["autoClose"] is False
    assert payload["role"] == "status" and common.notify("Nope.", "red")[0]["role"] == "alert"
    assert common.notify("a")[0]["id"] != common.notify("a")[0]["id"]


def test_every_icon_is_bundled_and_decorative_unless_named() -> None:
    for name in icons.NAMES:
        drawn = icons.icon(name)
        assert drawn.style["maskImage"].startswith('url("data:image/svg+xml')
        assert getattr(drawn, "aria-hidden") == "true"
    named = icons.icon("clock", label="Due")
    assert named.role == "img" and getattr(named, "aria-label") == "Due"
    with pytest.raises(ValueError):
        icons.icon("nowhere")


def test_a_notice_is_a_live_region() -> None:
    assert common.notice("error", "x").role == "alert"
    assert common.notice("info", "x").role == "status"
    with pytest.raises(ValueError):
        common.notice("loud", "x")


# ------------------------------------------------------------------------------------------ messages


#: the codes the new services raise, beside their sentences (plan C.3); kept honest by the scan below
SERVICE_CODES = (
    "forbidden", "unknown_task", "task_closed", "claimed_by_another", "already_staged", "close_call",
    "candidate_not_offered", "decision_not_offered", "not_held", "reason_required", "already_settled",
    "not_yours", "record_changed", "target_changed", "not_settled", "internal", "persona_refused",
    "bad_snooze", "bad_escalation", "unknown_role", "unknown_master_id", "unknown_source_record",
    "unknown_ref", "conflict", "stale_row", "stale_link", "workbench_needs_loopback", "unknown_entry",
    "bad_cursor", "unknown_view", "unknown_kind",
    # signature batches (story 3.3, plan 5 B.6)
    "bad_filter", "unknown_group", "unknown_batch", "batch_open", "group_too_small", "bulk_withdrawn",
    "not_ready", "not_prepared", "batch_empty", "not_the_maker", "checker_is_maker", "checker_required",
    "checker_not_recorded", "batch_unknown", "still_in_tray", "not_committing", "split_choice_needed",
    "bad_split_choice", "too_few_left", "batch_changed", "batch_stopped", "chunk_failed", "nothing_left",
    "own_batch", "not_compensable", "undo_window_passed", "already_compensated", "bad_compensate_reason",
)  # fmt: skip
_RAISED = re.compile(
    r"(?:Forbidden|NotFound|Conflict|PlatformRefused|CapacityError)\(\s*(?:\[[^\]]*\],\s*)?(?:code=)?\"([a-z_]+)\""
)


def test_every_code_the_workbench_services_raise_has_a_sentence() -> None:
    raised = set()
    for name in ("inbox", "decisions", "tray", "lookup", "display", "batches"):
        path = SRC / "services" / f"{name}.py"
        if path.exists():
            raised |= set(_RAISED.findall(path.read_text(encoding="utf-8")))
    raised.discard("none")
    assert raised <= set(SERVICE_CODES) | messages.KNOWN_CODES, sorted(raised - messages.KNOWN_CODES)
    for code in SERVICE_CODES:
        assert code in messages.KNOWN_CODES, code
        assert not messages.sentence_for(code).startswith("That did not work"), code


def test_already_staged_reads_differently_for_mine_and_another() -> None:
    mine = messages.sentence(Conflict(["TSK-1"], code="already_staged", mine=True))
    other = messages.sentence(Conflict(["TSK-1"], code="already_staged", mine=False))
    assert mine != other and "Your decision" in mine and "Another steward" in other


def test_already_settled_names_the_commit_or_the_undo() -> None:
    committed = messages.sentence(Conflict(["TR-1"], code="already_settled", status="committed", version=42))
    assert committed == "Too late to undo: it committed as commit 42. Reversing it is not on screen yet."
    assert (
        messages.sentence(Conflict(["TR-1"], code="already_settled", status="undone"))
        == "It was already undone."
    )


def test_forbidden_names_the_role_and_the_action_in_words() -> None:
    sentence = messages.sentence(Forbidden("forbidden", action="work_tasks", role="data_owner"))
    assert sentence == "Your role, data owner, cannot work on tasks."
    assert set(ACTIONS) <= set(messages.ACTION_WORDS)


def test_a_claim_says_how_long_it_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    until = (samples.NOW + timedelta(minutes=7, seconds=30)).isoformat()
    sentence = messages.sentence(Conflict(["TSK-1"], code="claimed_by_another", until=until), now=samples.NOW)
    assert sentence == "Another steward is working on this task for the next 8 min. Pick another task."


def test_an_unknown_record_echoes_only_an_id_or_a_source_key() -> None:
    assert (
        messages.sentence(NotFound("unknown_master_id", master_id="ORG-000123"))
        == "No record has the ID ORG-000123."
    )
    assert messages.sentence(NotFound("unknown_source_record", source="crm:C000123")) == (
        "No record has the ID crm:C000123."
    )
    assert messages.sentence(NotFound("unknown_ref", ref="Tamsin")) == "No record has that ID."
    assert messages.sentence(PlatformRefused("persona_refused")) == "Personas work only on a local store."
    assert messages.sentence(NotFound("something_new")) == "That did not work (something_new)."


# ------------------------------------------------------------------------------------------ the context


def test_guarded_turns_a_refusal_into_its_sentence() -> None:
    def refuse() -> None:
        raise Conflict(["TSK-1"], code="task_closed")

    result, note = context.guarded(refuse)
    assert result is None and note["color"] == "yellow"
    assert note["message"] == "That task has already been decided. Pick the next one."
    assert context.guarded(lambda x: x + 1, 1) == (2, None)


def test_guarded_names_only_the_type_of_anything_else(caplog: pytest.LogCaptureFixture) -> None:
    sentinel = "Tamsin Quorrel, 12 Umber Lane"

    def fail() -> None:
        raise ValueError(sentinel)

    with caplog.at_level(logging.INFO, logger="mdm.ui"):
        result, note = context.guarded(fail)
    assert result is None and note["color"] == "red"
    assert note["message"] == messages.UNEXPECTED.format(kind="ValueError")
    assert "ValueError" in caplog.text and sentinel not in caplog.text


def test_prefetch_goes_to_the_executor_and_never_raises() -> None:
    asked: list[str] = []
    decisions = SimpleNamespace(prefetch=lambda task_id, **kwargs: asked.append(task_id))
    with ThreadPoolExecutor(max_workers=1) as executor:
        ctx = replace(fake_ctx(decisions=decisions), executor=executor)
        context.prefetch(ctx, "TSK-000001")
        context.prefetch(ctx, None)
    assert asked == ["TSK-000001"]
    context.prefetch(ctx, "TSK-000002")  # after shutdown: nothing, no error
    context.prefetch(fake_ctx(decisions=decisions), "TSK-000003")  # no executor: nothing
    assert asked == ["TSK-000001"]


def _state(settings: Settings, hub: Any) -> context.UiState:
    return context.UiState(
        hub=hub, settings=settings, executor=ThreadPoolExecutor(max_workers=1), owns_hub=False
    )


def test_a_local_request_acts_as_the_tab_persona(hub: Hub) -> None:
    server = flask.Flask(__name__)
    server.extensions[context.EXTENSION] = _state(hub.settings, hub)
    with server.test_request_context("/", headers={"X-Forwarded-Email": "someone@example.org"}):
        ctx = context.current("data_owner")
        assert ctx.actor == Actor("persona:data_owner", "person", "data_owner", persona=True)
        assert context.current(None).actor.role == "data_steward"
        assert context.current("nobody").actor.role == "data_steward"  # not a role: the default


def test_inside_an_app_the_forwarded_user_acts_and_no_persona(on_platform: str) -> None:
    settings = Settings.from_env()
    assert settings.in_databricks_app
    stand_in = SimpleNamespace(
        authority=AuthorityService(settings, None, None),  # type: ignore[arg-type]
        badges=lambda: samples.HUB_BADGES_SHARED,
    )
    server = flask.Flask(__name__)
    server.extensions[context.EXTENSION] = _state(settings, stand_in)
    with server.test_request_context("/", headers={"X-Forwarded-Email": "someone@example.org"}):
        ctx = context.current("data_owner")
    assert ctx.actor.name == "someone@example.org" and ctx.actor.role == "consumer" and not ctx.actor.persona
    with server.test_request_context("/", headers={"X-Forwarded-User": "user-7"}):
        assert context.current(None).actor.name == "user-7"


def test_the_badges_are_read_once_in_a_while() -> None:
    read: list[int] = []

    def badges() -> Any:
        read.append(1)
        return samples.HUB_BADGES

    state = _state(Settings(), SimpleNamespace(badges=badges))
    assert state.badges() is samples.HUB_BADGES and state.badges() is samples.HUB_BADGES
    assert len(read) == 1


# ------------------------------------------------------------------------------------------ signature batches


def test_the_rail_links_alike_reviews_after_quality_samples_with_its_count() -> None:
    tree = rail.render(samples.COUNTS_ALIKE, {"view": "mine"})
    links = [c for c in walk(tree) if type(c).__name__ == "NavLink"]
    hrefs = [c.href for c in links]
    assert hrefs[:6] == [
        f"/?view={view}" for view in ("mine", "team", "breaching", "snoozed", "escalated", "samples")
    ]
    alike = links[6]
    assert alike.href == "/groups" and alike.label == "Alike reviews" and alike.active is False
    assert alike.rightSection.children == "612" and alike.description == "1 to confirm"
    assert alike.to_plotly_json()["props"]["aria-label"] == "Alike reviews, 612, 1 to confirm"
    assert getattr(alike, "id", None) is None
    none_to_confirm = rail.render(replace(samples.COUNTS_ALIKE, batches_to_confirm=0), {"view": "mine"})
    quiet = next(c for c in walk(none_to_confirm) if getattr(c, "href", None) == "/groups")
    assert getattr(quiet, "description", None) is None
    assert quiet.to_plotly_json()["props"]["aria-label"] == "Alike reviews, 612"
    assert rail.confirm_words(0) is None and rail.confirm_words(capacity.COUNT_CAP) == "999+ to confirm"


def test_the_rail_marks_alike_reviews_on_its_pages_and_the_filtered_inbox() -> None:
    for path, search in (
        ("/groups", ""),
        (f"/batch/{samples.BATCH_ID}", ""),
        ("/", f"?group={samples.GROUP_KEY_PERSON}"),
        ("/", f"?batch={samples.BATCH_ID}&task=TSK-000001"),
    ):
        query = layout.inbox_query(path, search)
        assert query == {"view": rail.ALIKE}, (path, search)
        assert rail.chosen(query) == (rail.ALIKE, None)
        tree = rail.render(samples.COUNTS_ALIKE, query)
        active = [c for c in walk(tree) if type(c).__name__ == "NavLink" and c.active]
        assert [c.href for c in active] == ["/groups"]
        assert active[0].to_plotly_json()["props"]["aria-current"] == "page"
    # a key of the wrong shape, or another path, is no mark
    assert layout.inbox_query("/", "?group=given_name%3D") == {"view": "mine", "kind": None}
    assert layout.inbox_query("/batch/Tamsin", "") == {}
    assert layout.inbox_query("/record/ORG-000123", "") == {}
    assert rail.ALIKE not in ALL_VIEWS  # the inbox never counts it as a view


def test_the_key_help_lists_g_after_undo() -> None:
    keys_listed = [key for key, _ in keys.KEY_HELP]
    assert keys_listed[keys_listed.index("U") + 1] == "G"
    assert dict(keys.KEY_HELP)["G"] == "Alike reviews: open reviews grouped by signature"
    script = (SRC / "ui" / "assets" / "keys.js").read_text(encoding="utf-8")
    help_at, g_at, decide_at = (
        script.index('e.key === "?"'),
        script.index('key === "g"'),
        script.index("DECIDE[key]"),
    )
    assert help_at < g_at < decide_at  # after the help, before the decision keys, and on the inbox only
    assert 'call("openGroups")' in script
    inbox_script = (SRC / "ui" / "assets" / "inbox.js").read_text(encoding="utf-8")
    assert 'setProps("url", {pathname: "/groups", search: ""})' in inbox_script
    assert "needsSplit" in script and "focusSplit" in script and "needsSplit" in inbox_script


def test_a_batch_entry_says_how_it_commits() -> None:
    assert tray.outcome_text(samples.TRAY_BATCH_COMMITTING) == "Committing: chunk 1 of 3"
    assert tray.outcome_text(samples.TRAY_BATCH_COMMITTED) == "Committed in 3 chunks"
    assert tray.outcome_text(samples.TRAY_BATCH_STOPPED) == "Stopped after chunk 2 of 3"
    assert tray.outcome_text(samples.TRAY_BATCH_WITHDRAWN) == (
        "Stopped after chunk 1 of 3: bulk decisions for this pattern were withdrawn"
    )
    assert tray.outcome_text(replace(samples.TRAY_BATCH_WITHDRAWN, outcome="chunk_failed")) == (
        "Stopped after chunk 1 of 3: a chunk could not commit"
    )
    assert tray.outcome_text(samples.TRAY_BATCH_FAILED) == (
        "Not committed. Every review moved before the batch could commit, so nothing was linked."
    )


def test_the_tray_lists_a_batch_waiting_committing_and_done_with_its_page() -> None:
    views = (samples.TRAY_BATCH_STAGED, samples.TRAY_BATCH_COMMITTING, samples.TRAY_BATCH_STOPPED)
    items = tray.entry_items(views, samples.NOW)
    shown = texts_in(items)
    assert "Waiting (1)" in shown and "Committing (1)" in shown and tray.DONE_HEADING in shown
    found = ids_in(items)
    staged = samples.TRAY_BATCH_STAGED
    assert ids.tray_undo(staged.entry_id) in found and ids.tray_countdown(staged.entry_id) in found
    undo = next(c for c in walk(items) if getattr(c, "id", None) == ids.tray_undo(staged.entry_id))
    assert getattr(undo, "aria-label") == f"Undo: Link 566 alike reviews ({samples.BATCH_ID})"
    links = [c for c in walk(items) if type(c).__name__ == "Anchor"]
    assert {c.href for c in links} == {f"/batch/{samples.BATCH_ID}"} and len(links) == 3
    assert all(c.children == "Open the batch" for c in links)
    assert "Committing: chunk 1 of 3" in shown


def test_the_second_stewards_tray_lists_the_batch_they_confirmed_with_its_undo() -> None:
    confirmed = samples.TRAY_BATCH_CONFIRMED
    items = tray.entry_items((confirmed,), samples.NOW)
    assert f"Link 566 alike reviews ({samples.BATCH_ID}), which you confirmed" in texts_in(items)
    assert ids.tray_undo(confirmed.entry_id) in ids_in(items)
    assert tray.label_of(samples.TRAY_BATCH_STAGED) == samples.TRAY_BATCH_STAGED.label


def test_an_undone_batch_stays_in_its_second_stewards_tray_with_its_outcome() -> None:
    # finding 14: the batch a second steward confirmed and its maker undid stays listed in the second steward's
    # tray as undone, which you confirmed, with no Undo; the inbox reads its page again, and no notice
    # repeats what the Undo said
    views = [samples.TRAY_BATCH_CONFIRMED]
    ctx = fake_ctx(tray=SimpleNamespace(entries=lambda **kwargs: tuple(views)))
    first = tray.refresh(ctx, None)
    assert first is not None and first.live == 1
    views[0] = samples.TRAY_BATCH_CONFIRMED_UNDONE
    undone = tray.refresh(ctx, first.state)
    assert undone is not None and undone.live == 0 and undone.notices == []
    assert undone.settled == [
        {
            "entry_id": samples.TRAY_BATCH_CONFIRMED.entry_id,
            "task_id": samples.BATCH_ID,
            "status": "undone",
            "outcome": "undone",
            "batch_id": samples.BATCH_ID,
        }
    ]
    shown = texts_in(undone.children)
    assert f"Link 566 alike reviews ({samples.BATCH_ID}), which you confirmed" in shown
    assert tray.DONE_HEADING in shown and "Waiting (1)" not in shown
    assert any(text.startswith("Undone") for text in shown)
    assert ids.tray_undo(samples.TRAY_BATCH_CONFIRMED.entry_id) not in ids_in(undone.children)
    links = [c for c in walk(undone.children) if type(c).__name__ == "Anchor"]
    assert [c.href for c in links] == [f"/batch/{samples.BATCH_ID}"]
    assert tray.refresh(ctx, undone.state) is None  # said once


def test_the_tray_polls_while_a_batch_commits_and_settles_each_chunk() -> None:
    views = [samples.TRAY_BATCH_STAGED]
    ctx = fake_ctx(tray=SimpleNamespace(entries=lambda **kwargs: tuple(views)))
    first = tray.refresh(ctx, None)
    assert first is not None and first.live == 1 and first.notices == []  # no "Confirmed" on a first load
    views[0] = samples.TRAY_BATCH_COMMITTING
    second = tray.refresh(ctx, first.state)
    assert second is not None and second.staged == 0 and second.live == 1  # the polls stay on
    assert second.settled == [
        {
            "entry_id": samples.TRAY_BATCH_STAGED.entry_id,
            "task_id": samples.BATCH_ID,
            "status": "committed",
            "outcome": "committing",
            "batch_id": samples.BATCH_ID,
        }
    ]
    assert [(n["title"], n["message"]) for n in second.notices] == [
        ("Committing", f"Committing: Link 566 alike reviews ({samples.BATCH_ID}), in 3 chunks.")
    ]
    assert tray.refresh(ctx, second.state) is None  # nothing moved
    views[0] = replace(samples.TRAY_BATCH_COMMITTING, progress=(2, 3))
    third = tray.refresh(ctx, second.state)
    assert third is not None and third.settled[0]["batch_id"] == samples.BATCH_ID and third.notices == []
    views[0] = samples.TRAY_BATCH_COMMITTED
    last = tray.refresh(ctx, third.state)
    assert last is not None and last.live == 0 and last.settled[0]["outcome"] == "committed"
    assert last.notices[0]["title"] == "Committed"
    assert (
        last.notices[0]["message"] == f"Committed in 3 chunks: Link 566 alike reviews ({samples.BATCH_ID})."
    )


def test_a_stopped_or_failed_batch_says_so_once() -> None:
    views = [samples.TRAY_BATCH_COMMITTING]
    ctx = fake_ctx(tray=SimpleNamespace(entries=lambda **kwargs: tuple(views)))
    first = tray.refresh(ctx, None)
    views[0] = samples.TRAY_BATCH_STOPPED
    stopped = tray.refresh(ctx, first.state)
    note = stopped.notices[0]
    assert note["title"] == "Stopped" and note["color"] == "yellow"
    assert note["message"] == (
        f"Stopped after chunk 2 of 3: Link 566 alike reviews ({samples.BATCH_ID}). The rest are back in the queue."
    )
    withdrawn = tray.batch_notice(samples.TRAY_BATCH_WITHDRAWN, {"outcome": "committing"})
    assert withdrawn is not None and "Bulk decisions for this pattern were withdrawn" in withdrawn["message"]
    failed = tray.batch_notice(samples.TRAY_BATCH_FAILED)
    assert failed is not None and failed["title"] == "Not committed"
    assert tray.batch_notice(replace(samples.TRAY_BATCH_STAGED, status="undone", outcome="undone")) is None


def test_the_maker_hears_once_that_a_second_steward_confirmed() -> None:
    views: list[TrayView] = []
    ctx = fake_ctx(tray=SimpleNamespace(entries=lambda **kwargs: tuple(views)))
    first = tray.refresh(ctx, None)
    views.append(samples.TRAY_BATCH_STAGED)
    second = tray.refresh(ctx, first.state)
    assert second is not None and second.live == 1
    [note] = second.notices
    assert note["title"] == "Confirmed" and note["color"] == "teal"
    at = samples.TRAY_BATCH_STAGED.deadline.strftime("%H:%M:%S")
    assert note["message"] == (
        f"Confirmed by a coordinating steward: Link 566 alike reviews ({samples.BATCH_ID}). "
        f"It commits at {at} UTC unless one of you undoes it."
    )
    assert tray.refresh(ctx, second.state) is None  # once
    # the second steward's own tray says nothing of the kind: they confirmed it
    views[:] = []
    other = tray.refresh(ctx, None)
    views.append(samples.TRAY_BATCH_CONFIRMED)
    assert tray.refresh(ctx, other.state).notices == []


def test_the_tray_button_keeps_its_mark_while_a_batch_commits() -> None:
    script = (SRC / "ui" / "assets" / "keys.js").read_text(encoding="utf-8")
    assert '"Tray · committing"' in script and 'entry.outcome === "committing"' in script
    assert tray.live_in(tray.state_of([samples.TRAY_BATCH_COMMITTING])) == 1
    assert tray.live_in(tray.state_of([samples.TRAY_BATCH_COMMITTED, samples.TRAY_VIEWS[1]])) == 0


def test_the_batch_refusals_read_as_sentences_with_their_titles() -> None:
    mine = messages.sentence(Conflict(["TSK-1"], code="already_staged", mine=True, batch=samples.BATCH_ID))
    assert mine == (
        f"This review is part of batch {samples.BATCH_ID}, which waits in the tray. Undo the batch to decide it on "
        "its own."
    )
    other = messages.sentence(Conflict(["TSK-1"], code="already_staged", mine=False, batch=samples.BATCH_ID))
    assert other == "This review is part of another steward's batch. Pick another task."
    late = messages.sentence(
        Conflict(["TR-1"], code="already_settled", status="committed", batch=samples.BATCH_ID)
    )
    assert late == (
        "Too late to undo: this batch has started to commit. Stop it on its page; its committed links can be "
        "undone on the command line."
    )
    assert messages.sentence(NotFound("unknown_batch", batch_id=samples.BATCH_ID)) == (
        f"No batch has the ID {samples.BATCH_ID}."
    )
    assert messages.sentence(NotFound("unknown_batch", batch_id="ORG-000123")) == "No batch has that ID."
    assert messages.sentence(Forbidden("batch_unknown", batch=samples.BATCH_ID)).endswith(
        f"{samples.BATCH_ID}."
    )
    assert messages.sentence(Forbidden("forbidden", action="batch_link", role="data_owner")) == (
        "Your role, data owner, cannot decide alike reviews together."
    )
    titles = {
        "bad_filter": "Not found", "unknown_group": "Group gone", "unknown_batch": "Batch gone",
        "batch_open": "Already a batch", "group_too_small": "Too few", "bulk_withdrawn": "Bulk decisions withdrawn",
        "not_ready": "Sample not complete", "not_prepared": "Check every row first", "batch_empty": "Nothing to link",
        "not_the_maker": "Not your batch", "checker_is_maker": "A second steward confirms",
        "checker_required": "A second steward confirms", "checker_not_recorded": "Not confirmed",
        "batch_unknown": "Batch gone", "still_in_tray": "Still in the tray", "not_committing": "Nothing to stop",
        "split_choice_needed": "Name the comparison", "bad_split_choice": "Choose a comparison",
        "too_few_left": "Too few left", "batch_changed": "The batch changed", "batch_stopped": "Stopped",
        "chunk_failed": "Stopped", "nothing_left": "Nothing linked", "own_batch": "Your own batch",
        "not_compensable": "Not done", "undo_window_passed": "Too late", "already_compensated": "Already undone",
        "bad_compensate_reason": "Choose a reason",
    }  # fmt: skip
    for code, wanted in titles.items():
        assert messages.title_for(code) == wanted, code
    assert messages.sentence_for("split_choice_needed").startswith("Name the comparison that misled first.")


def test_the_batch_ids_are_built_from_keys_and_codes_only() -> None:
    assert ids.group_draw(samples.GROUP_KEY_PERSON, "person") == {
        "type": ids.GROUP_DRAW,
        "group": samples.GROUP_KEY_PERSON,
        "entity": "person",
    }
    assert ids.batch_action("stage") == {"type": ids.BATCH_ACTION, "action": "stage"}
    with pytest.raises(ValueError):
        ids.group_draw("given_name= · family_name≈", "person")  # a signature never becomes an ID
    for value in (ids.GROUPS_ADDRESS, ids.GROUPS_SETTLED, ids.BATCH_SETTLED):
        assert value["type"] in ID_VALUES


def test_the_alike_reviews_and_a_batch_have_their_routes_and_their_query_keys() -> None:
    batch_id = samples.BATCH_ID
    assert app_module.parse_route("/groups") == ("groups", ())
    assert app_module.parse_route("/groups/") == ("groups", ())
    assert app_module.parse_route(f"/batch/{batch_id}") == ("batch", (batch_id,))
    assert app_module.parse_route("/batch/Tamsin") == ("unknown", ())
    assert app_module.parse_route(f"/batch/{batch_id}/rows") == ("unknown", ())
    assert app_module.parse_route("/records") == ("unknown", ())
    group = samples.GROUP_KEY_PERSON
    assert app_module.query_of(f"?group={group}&batch={batch_id}&name=Tamsin") == {
        "group": group,
        "batch": batch_id,
    }


def test_the_new_pages_render_through_the_route_and_are_not_the_inbox(hub: Hub) -> None:
    ctx = context.for_test(hub)
    groups_page, note, on_inbox = app_module.render_route(ctx, "/groups", "", "person")
    assert note is None and on_inbox is False and "Alike reviews" in text_of(groups_page)
    gone, note, on_inbox = app_module.render_route(ctx, f"/batch/{samples.BATCH_ID}", "", "")
    assert note is None and on_inbox is False
    assert f"No batch has the ID {samples.BATCH_ID}." in " ".join(texts_in(gone))


def test_the_counts_wake_a_resting_tray_when_its_steward_has_more_moving(hub: Hub) -> None:
    """S6 reads `ViewCounts.tray_live` with its one count read and hands it to TRAY_LIVE; S6b (clientside)
    wakes the tray's poll when it differs from what the tab's tray shows, and the tray's refresh rests the
    poll again once a poll finds nothing new and nothing moving."""
    ctx = context.for_test(hub)
    parts, live = layout.header_parts(ctx, "", "/", "")
    role, link, style, _rail, tray_style = layout.header_state(ctx, "", "/", "")
    assert (parts[0], parts[1], parts[2], parts[4]) == (role, link, style, tray_style) and live == 0
    consumer = context.for_test(hub, role="consumer")
    assert layout.header_parts(consumer, "", "/", "")[1] is None
    built = Dash(__name__)
    layout.register(built)
    [header] = [c for c in built._callback_list if f"{ids.ROLE_BADGE}.children" in c["output"]]
    assert f"{ids.TRAY_LIVE}.data" in header["output"]
    [wake] = [c for c in built._callback_list if c["output"].startswith(f"{ids.TRAY_POLL}.disabled@")]
    assert wake["prevent_initial_call"] is True
    assert [i["id"] for i in wake["inputs"]] == [ids.TRAY_LIVE]
    assert [s["id"] for s in wake["state"]] == [ids.TRAY_STATE]
    script = (SRC / "ui" / "assets" / "keys.js").read_text(encoding="utf-8")
    body = script[script.index("trayWake: function") :]
    assert "live !== shown ? false : noUpdate()" in body[: body.index("},")]
    assert tray.quiet_polls([]) and not tray.quiet_polls(None)
    assert not tray.quiet_polls(tray.state_of([samples.TRAY_BATCH_COMMITTING]))
    assert tray.quiet_polls(tray.state_of([samples.TRAY_BATCH_COMMITTED]))


def test_undo_from_the_tray_says_a_batch_is_ready_again() -> None:
    entry = SimpleNamespace(decision="batch_link")
    ctx = fake_ctx(tray=SimpleNamespace(undo=lambda entry_id, **kwargs: entry))
    note = tray.undo(ctx, "TR-1")
    assert note["title"] == "Undone"
    assert note["message"] == "Undone. The batch is ready again, and nothing was linked."
    entry.decision = "batch_compensate"
    assert tray.undo(ctx, "TR-1")["message"] == "Undone. The batch is ready again, and nothing was undone."
    entry.decision = "link"
    assert tray.undo(ctx, "TR-1")["message"] == "Undone. The task is back in your queue."
