"""The golden record and source record views without a browser (plan B.8.9, B.9.2; DuckDB only).

View tests render the invented samples of `tests/workbench_samples.py`; flow tests call the pure functions
behind the record's callbacks (`layout`, `tab_content`, `fill_tab`, `why`, `reveal_golden`,
`older_events`, and the source page's `reveal_values`) on a hub over an in-memory DuckDB holding the mini
world, a crm Person record under review (not linked) and one merge. Two checks post a callback through the
Flask test client, as the browser would, so the wiring of the Why rows and the reveal is tested too.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from dash import Dash, no_update
from dash.development.base_component import Component

from mdm import capacity
from mdm.backend.factory import open_store
from mdm.models.records import SourceKey
from mdm.services.context import Hub
from mdm.ui import app as app_module
from mdm.ui import context, ids
from mdm.ui.components import provenance, reveal, timeline
from mdm.ui.pages import record, source
from tests import helpers
from tests import workbench_samples as samples
from tests.conftest import base_settings, open_hub

PORT = 8050


# ------------------------------------------------------------------------------------------ helpers


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


def strings(tree: Any) -> list[str]:
    """Every string prop in a tree (text, labels, hrefs, IDs as JSON)."""
    found: list[str] = []
    for component in walk(tree):
        for name in component._prop_names:
            value = getattr(component, name, None)
            if isinstance(value, str):
                found.append(value)
            elif isinstance(value, dict):
                found.append(json.dumps(value, sort_keys=True, default=str))
            elif isinstance(value, (list, tuple)):
                found.extend(item for item in value if isinstance(item, str))
    return found


def text(tree: Any) -> str:
    """The visible text of a tree, in order."""
    if isinstance(tree, str):
        return tree
    if isinstance(tree, (list, tuple)):
        return "".join(text(item) for item in tree)
    if isinstance(tree, Component):
        return text(getattr(tree, "children", None) or "")
    return ""


def by_id(tree: Any, component_id: Any) -> Component:
    found = [c for c in walk(tree) if getattr(c, "id", None) == component_id]
    assert len(found) == 1, (component_id, len(found))
    return found[0]


def has_id(tree: Any, component_id: Any) -> bool:
    return any(getattr(c, "id", None) == component_id for c in walk(tree))


def hrefs(tree: Any) -> list[str]:
    return [c.href for c in walk(tree) if isinstance(getattr(c, "href", None), str)]


def buttons(tree: Any) -> list[Component]:
    return [c for c in walk(tree) if type(c).__name__ == "Button"]


# ------------------------------------------------------------------------------------------ the world


@dataclass(frozen=True)
class World:
    hub: Hub
    survivor: str  # hr person 0's golden record, into which person 1's was merged
    retired: str  # hr person 1's golden record, merged
    other: str  # hr person 2's golden record
    organisation: str  # finance organisation 0's golden record
    unlinked: SourceKey  # the crm Person record under review, linked to nothing


@pytest.fixture(scope="module")
def world() -> Iterator[World]:
    settings = base_settings()
    store = open_store(settings)
    store.init_schema(create_landing=True)
    hub = open_hub(settings, store, "duckdb")
    try:
        helpers.workbench_world(hub, persons=8, organisations=3)
        unlinked = helpers.person_review(hub)
        masters = [helpers.master_of(hub, "person", "hr", helpers.hr_key(i)) for i in range(3)]
        organisation = helpers.master_of(hub, "organisation", "finance", helpers.finance_key(0))
        assert all(masters) and organisation
        survivor, retired, other = masters
        hub.lifecycle.merge(
            "person",
            survivor,
            retired,
            maker=helpers.STEWARD,
            checker=helpers.COORDINATOR,
            reason="duplicate",
        )
        yield World(hub, survivor, retired, other, organisation, unlinked)
    finally:
        hub.close()
        store.close()


def steward(world: World) -> context.UiContext:
    return context.for_test(world.hub)


def consumer(world: World) -> context.UiContext:
    return context.for_test(world.hub, role="consumer")


def ref(world: World, master_id: str | None = None, *, entity: str = "person", **extra: Any) -> dict:
    return {"entity": entity, "master_id": master_id or world.survivor, "filled": ["golden"], **extra}


def clear_values(world: World, master_id: str) -> set[str]:
    """The personal values of a golden record in clear, as the data owner reveals them for the check."""
    owner = context.for_test(world.hub, role="data_owner")
    shown = world.hub.lookup.golden("person", master_id, actor=owner.actor, reveal=True, reason="audit_check")
    return {v.value for v in shown if v.personal and v.value and len(v.value) >= 4}


# ------------------------------------------------------------------------------------------ views on the samples


def test_a_chip_is_a_button_that_names_what_it_opens() -> None:
    name, phone, website, registered = samples.VALUES
    chip = provenance.chip(name)
    assert chip.id == ids.prov_chip("name") and chip.type == "button"
    assert chip.className == "mdm-prov" and chip.n_clicks == 0
    assert text(chip) == "finance · source trust · 2 d, why this name?"
    assert provenance.chip(phone).className == "mdm-prov mdm-prov-pin"
    assert text(provenance.chip(website)).startswith("crm · rules · 14 d")
    assert getattr(provenance.chip(registered), "id", None) is None  # no value, no chip
    assert provenance.why_title("Registered ID") == "Why this registered ID?"
    assert provenance.why_title("Birth date") == "Why this birth date?"


def test_a_value_cell_says_when_a_value_is_masked_or_missing() -> None:
    masked = provenance.value_cell(samples.VALUE_MASKED.value, masked=True)
    assert text(masked) == "B*** (masked)"
    assert [c.className for c in walk(masked) if getattr(c, "className", None)] == [
        "mdm-masked",
        "mdm-sr-only",
    ]
    assert text(provenance.value_cell(None, masked=False)) == provenance.NO_VALUE
    assert text(provenance.value_cell("Norvale", masked=False)) == "Norvale"


def test_the_why_body_gives_the_sentence_the_values_used_and_the_rule_version() -> None:
    body = provenance.why_body(samples.WHY)
    shown = text(body)
    assert samples.WHY.sentence in shown
    assert "Used" in shown and "Runner-up" in shown and "QUORANE WORKS ltd." in shown
    assert "2 days old" in shown and "today" in shown
    assert shown.count("Survivorship rules") == 1  # the sentence names the rules once
    tied = replace(samples.WHY, runners_up=tuple(replace(r, age_days=2) for r in samples.WHY.runners_up))
    assert provenance.ages_tie([tied.winner, *tied.runners_up])
    assert "Changed" in text(provenance.why_body(tied))
    # two changes within one minute: the seconds tell which is the most recent
    at = datetime(2026, 1, 5, 0, 2, 19, tzinfo=UTC)
    close = replace(
        tied,
        winner=replace(tied.winner, occurred_at=at),
        runners_up=(
            replace(tied.runners_up[0], age_days=tied.winner.age_days, occurred_at=at - timedelta(seconds=1)),
        ),
    )
    assert "00:02:19 UTC" in text(provenance.why_body(close)) and "00:02:18 UTC" in text(
        provenance.why_body(close)
    )
    assert "00:02:" not in text(provenance.why_body(replace(close, runners_up=())))
    assert provenance.MASKED_NOTE not in shown
    assert "/source/finance/F000123" in hrefs(body) and "/source/crm/C000812" in hrefs(body)
    assert [c.scope for c in walk(body) if type(c).__name__ == "Th"] == ["col"] * 4
    personal = text(provenance.why_body(samples.WHY, personal=True))
    assert provenance.MASKED_NOTE in personal
    lonely = provenance.why_body(
        samples.ValueWhy(
            attribute="left_on",
            label="Left on",
            sentence="No source holds a value for Left on.",
            winner=None,
            runners_up=(),
            strategies=(),
            decided_by=None,
            rule_version=None,
        )
    )
    assert not [c for c in walk(lonely) if type(c).__name__ == "Table"]
    assert "was not recorded" in text(lonely)


def test_the_timeline_marks_merges_and_the_automated_matcher() -> None:
    items = timeline.render(samples.TIMELINE_PAGE, samples.NOW)
    assert [type(i).__name__ for i in items] == ["Li"] * 3
    updated, merge, created = (text(i) for i in items)
    assert "4 min ago · commit 6" in updated and "Values updated: phone" in updated
    assert "Data steward" in updated and updated.count("Data steward") == 1  # the authority once
    assert "Merge" in merge and "Data steward; checker Data owner" in merge
    assert "Automated matcher · rules v1 · finance.new=auto" in created
    labels = [
        c.to_plotly_json()["props"].get("aria-label")
        for c in walk(items)
        if getattr(c, "role", None) == "img"
    ]
    assert labels == ["A person", "A person", "Automated matcher"]
    assert [i.className for i in items] == ["mdm-tl-item", "mdm-tl-item mdm-tl-identity", "mdm-tl-item"]
    times = [c.dateTime for c in walk(items) if type(c).__name__ == "Time"]
    assert times == [e.at.isoformat() for e in samples.TIMELINE_PAGE.events]
    assert text(timeline.render(samples.TimelinePage(events=(), before=None))) == timeline.EMPTY


def test_the_relationships_name_the_other_end_masked_and_every_asserting_source() -> None:
    table = record.relationships_table("ORG-000123", samples.RELATIONSHIPS)
    shown = text(table)
    assert "subsidiary of" in shown and "employs" in shown and "K*** B***" in shown
    assert "/record/PER-000451" in hrefs(table) and "/source/hr/H000451" in hrefs(table)
    assert "/source/crm/C001377" in hrefs(table)
    assert "not given" in shown  # no end date
    assert (
        text(record.relationships_table("ORG-000123", ())) == "No relationship is recorded for this record."
    )


def test_the_members_link_their_source_records_and_tasks(world: World) -> None:
    table = record.members_table(steward(world), "ORG-000123", samples.MEMBERS, 3, samples.NOW)
    shown = text(table)
    assert "/source/finance/F000123" in hrefs(table) and "/source/crm/C000812" in hrefs(table)
    assert "phone: bad_pattern" in shown and "held" in shown and "v3 · " in shown
    assert f"/?view=team&task={samples.ROW_CLOSE_CALL.task_id}" in hrefs(table)
    assert "Showing 2 of 3 source records." in shown
    as_consumer = record.members_table(consumer(world), "ORG-000123", samples.MEMBERS, 2, samples.NOW)
    assert not [h for h in hrefs(as_consumer) if h.startswith("/?")] and "1 open task" in text(as_consumer)
    assert "Showing" not in text(as_consumer)
    assert "Detach" not in shown


def test_a_source_record_marks_its_held_update_and_lists_versions_newest_first() -> None:
    values = source.values_table(samples.SOURCE_VIEW)
    assert "held update" in text(values) and "critical" in text(values)
    versions = source.versions_table(samples.SOURCE_VIEW, samples.NOW)
    cells = [text(c) for c in walk(versions) if type(c).__name__ == "Td"]
    assert cells[:3] == ["not given", "1 h ago", "5120"] and cells[-1] == "812"


# ------------------------------------------------------------------------------------------ the page


def test_a_master_id_opens_the_record_with_its_header_and_four_tabs(world: World) -> None:
    page = record.layout(steward(world), world.survivor, {})
    assert page.id == ids.RECORD
    header = by_id(page, ids.RECORD_HEADER)
    shown = text(header)
    title = world.hub.lookup.header("person", world.survivor, actor=steward(world).actor).title
    assert title.endswith("***") and title in shown
    assert world.survivor in shown and "Person" in shown and "active" in shown
    assert f"retired IDs: {world.retired}" in shown
    assert record.LATER in shown and "no open task" in shown
    assert f"/record/{world.retired}" in hrefs(header)
    tabs = by_id(page, ids.RECORD_TABS)
    assert tabs.value == "golden"
    names = {c.value: text(c) for c in walk(tabs) if type(c).__name__ == "TabsTab"}
    assert list(names) == list(record.TABS)
    members = len(world.hub.lookup.members("person", world.survivor, actor=steward(world).actor))
    assert names["sources"] == f"Sources and cross-references ({members})"
    golden = by_id(page, ids.GOLDEN_TABLE)
    assert [c.id for c in walk(golden) if isinstance(getattr(c, "id", None), dict)]  # provenance chips
    assert text(by_id(page, ids.MEMBERS_TABLE)) == record.LOADING  # filled when first shown
    assert by_id(page, ids.RECORD_REF).data == {
        "entity": "person",
        "master_id": world.survivor,
        "filled": ["golden"],
        "personal": sorted(world.hub.registry.published("person").personal_attributes()),
    }
    assert by_id(page, ids.RECORD_REF).storage_type == "memory"
    assert has_id(page, ids.reveal(ids.REVEAL_OPEN, "record"))
    rows = [
        c for c in walk(golden) if isinstance(getattr(c, "id", None), dict) and c.id["type"] == ids.WHY_ROW
    ]
    assert rows and all("mdm-hidden" in c.className for c in rows)  # a Why row under each value, closed
    assert not [b for b in buttons(page) if "Detach" in text(b) or "Merge" in text(b)]
    assert len([c for c in walk(page) if type(c).__name__ == "H1"]) == 1


def test_a_merged_id_shows_its_survivor_with_a_notice(world: World) -> None:
    page = record.layout(steward(world), world.retired, {})
    assert f"{world.retired} was merged into {world.survivor}; showing the survivor." in text(page)
    assert by_id(page, ids.RECORD_REF).data["master_id"] == world.survivor
    timeline_page = world.hub.lookup.timeline("person", world.survivor, actor=steward(world).actor)
    assert timeline_page.events[0].headline == f"{world.retired} merged into this record"


def test_a_source_key_opens_its_golden_record_or_else_its_source_record(world: World) -> None:
    linked = f"hr:{helpers.hr_key(2)}"
    page = record.layout(steward(world), linked, {})
    assert page.id == ids.RECORD and by_id(page, ids.RECORD_REF).data["master_id"] == world.other
    assert f"{linked} is linked to {world.other}; this is its golden record." in text(page)
    assert f"/source/hr/{helpers.hr_key(2)}" in hrefs(page)
    unlinked = record.layout(steward(world), world.unlinked.text(), {})
    assert unlinked.id == ids.SOURCE
    assert f"{world.unlinked.text()} is not linked to a golden record" in text(unlinked)
    assert "Not linked to a golden record." in text(unlinked)
    tasks = [h for h in hrefs(unlinked) if h.startswith("/?view=team&task=")]
    assert len(tasks) == 1  # its review task
    narrowed = record.layout(steward(world), linked, {"entity": "organisation"})
    assert getattr(narrowed, "id", None) is None and "No record has the ID" in text(narrowed)


def test_an_unknown_reference_says_so_and_echoes_only_an_id(world: World) -> None:
    page = record.layout(steward(world), "PER-999999", {})
    assert "No record has the ID PER-999999." in text(page) and hrefs(page) == ["/"]
    free = record.layout(steward(world), "Tamsin Quorrel", {})
    assert "No record has that ID." in text(free) and "Tamsin" not in " ".join(strings(free))
    assert hrefs(record.layout(consumer(world), "PER-999999", {})) == []  # a consumer has no inbox
    gone = source.layout(steward(world), "crm", "C9999999", {})
    assert "No record has the ID crm:C9999999." in text(gone)


def test_the_tab_the_address_asks_for_is_filled_first(world: World) -> None:
    page = record.layout(steward(world), world.survivor, {"tab": "timeline"})
    assert by_id(page, ids.RECORD_TABS).value == "timeline"
    items = by_id(page, ids.TIMELINE_LIST).children
    assert items and record.LOADING not in text(items)
    assert by_id(page, ids.RECORD_REF).data["filled"] == ["golden", "timeline"]
    assert by_id(
        record.layout(steward(world), world.survivor, {"tab": "elsewhere"}), ids.RECORD_TABS
    ).value == ("golden")
    sources = record.layout(steward(world), world.survivor, {"tab": "sources"})
    assert f"/source/hr/{helpers.hr_key(0)}" in hrefs(by_id(sources, ids.MEMBERS_TABLE))


def test_each_tab_is_filled_once_when_first_shown(world: World) -> None:
    ctx = steward(world)
    members, items, cursor, more, relationships, kept = record.fill_tab(ctx, ref(world), "sources")
    assert (items, cursor, more, relationships) == (no_update,) * 4
    assert f"/source/hr/{helpers.hr_key(1)}" in hrefs(members)  # the merged record's member moved here
    assert kept["filled"] == ["golden", "sources"]
    assert record.fill_tab(ctx, kept, "sources") == (no_update,) * 6
    assert record.fill_tab(ctx, kept, "golden") == (no_update,) * 6
    outputs = record.fill_tab(ctx, kept, "timeline")
    assert outputs[0] is no_update and outputs[4] is no_update
    assert [type(i).__name__ for i in outputs[1]] == ["Li"] * len(outputs[1]) and outputs[3] == "mdm-hidden"
    assert outputs[5]["filled"] == ["golden", "sources", "timeline"]
    related = record.fill_tab(ctx, outputs[5], "relationships")[4]
    assert "works at" in text(related)
    assert record.fill_tab(ctx, {"entity": 1}, "sources") == (no_update,) * 6
    assert record.fill_tab(ctx, ref(world), "nowhere") == (no_update,) * 6


def test_a_tab_that_fails_says_so_in_its_place(world: World) -> None:
    members = record.fill_tab(steward(world), ref(world, "PER-999999"), "sources")[0]
    assert "No record has the ID PER-999999." in text(members)
    assert "mdm-notice" in members.className


def test_every_tab_renders_for_the_privacy_check(world: World) -> None:
    for tab in record.TABS:
        assert isinstance(record.tab_content(steward(world), "person", world.survivor, tab), Component)
    with pytest.raises(ValueError):
        record.tab_content(steward(world), "person", world.survivor, "elsewhere")


def test_show_older_pages_the_timeline_by_its_cursor(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = steward(world)
    everything = world.hub.lookup.timeline("person", world.survivor, actor=ctx.actor).events
    assert len(everything) >= 2  # created, then the merge
    monkeypatch.setattr(capacity, "TIMELINE_PAGE", 1)
    page = record.layout(ctx, world.survivor, {"tab": "timeline"})
    cursor = by_id(page, ids.TIMELINE_CURSOR).data
    assert len(by_id(page, ids.TIMELINE_LIST).children) == 1 and cursor
    assert by_id(page, ids.TIMELINE_MORE).className == ""
    seen = 1
    data = by_id(page, ids.RECORD_REF).data
    while cursor:
        items, cursor = record.older_events(ctx, data, cursor)
        assert len(items) == 1
        seen += 1
    assert seen == len(everything) and record.more_class(cursor) == "mdm-hidden"
    assert record.older_events(ctx, data, ["1", 2]) == ([], None)
    assert record.older_events(ctx, data, None) == ([], None)


def test_the_why_explains_a_value_and_stays_masked(world: World) -> None:
    ctx = steward(world)
    data = by_id(record.layout(ctx, world.survivor, {}), ids.RECORD_REF).data
    title, body = record.why(ctx, data, "given_name")
    assert title == "Why this given name?"
    expected = world.hub.lookup.why("person", world.survivor, "given_name", actor=ctx.actor)
    shown = text(body)
    assert expected.sentence in shown and provenance.MASKED_NOTE in shown
    assert not clear_values(world, world.survivor) & set(strings(body))
    title, body = record.why(ctx, ref(world, entity="organisation", master_id=world.organisation), "name")
    assert title == "Why this name?" and provenance.MASKED_NOTE not in text(body)
    with pytest.raises(ValueError):
        record.why(ctx, None, "name")


def test_a_chip_opens_the_why_only_when_it_was_clicked() -> None:
    chip = {"type": ids.PROV_CHIP, "attr": "phone"}
    other = {"type": ids.PROV_CHIP, "attr": "name"}
    inputs = [
        {"id": other, "property": "n_clicks", "value": 0},
        {"id": chip, "property": "n_clicks", "value": 2},
    ]
    assert record.clicked_attribute(chip, inputs) == "phone"
    assert record.clicked_attribute(other, inputs) is None  # drawn again after a reveal, never clicked
    assert record.clicked_attribute(None, inputs) is None
    assert record.clicked_attribute({"type": ids.ACTION, "decision": "link"}, inputs) is None


def test_a_reveal_asks_a_reason_code_and_is_logged_per_value(world: World) -> None:
    ctx = steward(world)
    data = by_id(record.layout(ctx, world.other, {}), ids.RECORD_REF).data
    before = world.hub.store.access_log(None, 1000)
    for reason in (None, "", "because I was asked", "Deciding this task"):
        assert record.reveal_golden(ctx, data, reason) == (None, None, reveal.REASON_REQUIRED)
    assert world.hub.store.access_log(None, 1000) == before
    table, notice, error = record.reveal_golden(ctx, data, "subject_request")
    assert error is None and notice["color"] == "teal"
    clear = clear_values(world, world.other)
    assert clear and clear <= set(strings(table))
    assert not clear & set(strings(notice)) and "shown. Each is logged with your reason." in notice["message"]
    logged = [r for r in world.hub.store.access_log(None, 1000) if r["reason"] == "subject_request"]
    shown = [v for v in world.hub.lookup.golden("person", world.other, actor=ctx.actor) if v.masked]
    assert len(logged) == len(shown) and {r["master_id"] for r in logged} == {world.other}
    assert notice["message"].startswith(f"{len(shown)} personal values")


def test_a_consumer_is_offered_no_reveal_and_is_refused_one(world: World) -> None:
    ctx = consumer(world)
    page = record.layout(ctx, world.survivor, {})
    assert not has_id(page, ids.reveal(ids.REVEAL_OPEN, "record"))
    assert not has_id(page, ids.reveal(ids.REVEAL_MODAL, "record"))
    table, notice, error = record.reveal_golden(ctx, by_id(page, ids.RECORD_REF).data, "audit_check")
    assert table is None and error is None
    assert notice["message"] == "Your role, consumer, cannot show personal values."
    linked = source.layout(ctx, "hr", helpers.hr_key(0), {})
    assert not has_id(linked, ids.reveal(ids.REVEAL_OPEN, "source"))
    organisation = record.layout(steward(world), world.organisation, {})  # nothing personal: nothing to show
    assert not has_id(organisation, ids.reveal(ids.REVEAL_OPEN, "record"))


def test_a_source_record_is_masked_and_its_reveal_is_logged(world: World) -> None:
    ctx = steward(world)
    page = source.layout(ctx, "crm", "C100000", {})
    assert page.id == ids.SOURCE
    shown = text(page)
    assert "crm:C100000" in shown and f"Linked to {world.survivor}." in shown and source.LATER in shown
    assert f"/record/{world.survivor}" in hrefs(page)
    data = by_id(page, ids.SOURCE_REF).data
    assert data == {"entity": "person", "source": "crm:C100000"}
    clear = clear_values(world, world.survivor)
    assert not clear & set(strings(page))
    assert source.reveal_values(ctx, data, None) == (None, None, reveal.REASON_REQUIRED)
    table, notice, error = source.reveal_values(ctx, data, "source_defect")
    assert error is None and "Each is logged with your reason." in notice["message"]
    assert set(strings(table)) & clear
    logged = [r for r in world.hub.store.access_log(None, 1000) if r["reason"] == "source_defect"]
    assert logged and {r["detail"]["subject"] for r in logged} == {"crm:C100000"}
    assert source.reveal_values(ctx, {"source": "nothing"}, "audit_check")[1]["color"] == "yellow"


def test_nothing_the_record_keeps_in_a_store_is_a_value(world: World) -> None:
    ctx = steward(world)
    page = record.layout(ctx, world.survivor, {"tab": "timeline"})
    stores = [c for c in walk(page) if type(c).__name__ == "Store"]
    assert [s.id for s in stores if isinstance(s.id, dict)] == [ids.WHY_REF]
    assert {s.id for s in stores if isinstance(s.id, str)} == {ids.RECORD_REF, ids.TIMELINE_CURSOR}
    assert all(s.storage_type == "memory" for s in stores)
    kept = json.dumps([s.data for s in stores])
    assert not any(value in kept for value in clear_values(world, world.survivor))
    assert not any(
        value in json.dumps(getattr(c, "id", None))
        for c in walk(page)
        for value in clear_values(world, world.survivor)
    )


# ------------------------------------------------------------------------------------------ the callbacks, wired


@pytest.fixture
def app(world: World) -> Iterator[Dash]:
    """The whole workbench over the world."""
    built = app_module.create_app(world.hub.settings, hub=world.hub, worker=False, listen=("127.0.0.1", PORT))
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
        raw, _, prop = part.rpartition(".")
        found.append({"id": json.loads(raw) if raw.startswith("{") else raw, "property": prop})
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


def test_the_record_registers_its_callbacks_as_planned(app: Dash) -> None:
    for fragment in (
        f"{ids.MEMBERS_TABLE}.children",
        '"type":"why-row"}.className',
        f"{ids.GOLDEN_TABLE}.children",
        f"{ids.SOURCE_VALUES}.children",
    ):
        callback_of(app, fragment)
    older = callback_of(app, f"{ids.TIMELINE_LIST}.children@")
    assert older["prevent_initial_call"] is True
    toggles = [c for c in app._callback_list if '"page":"record"' in c["output"] and "opened" in c["output"]]
    assert len(toggles) == 2  # R3 (clientside) and R4


def test_a_chip_click_through_dash_opens_the_why_under_its_value(app: Dash, world: World) -> None:
    data = ref(world, personal=["given_name"])
    names = ("city", "given_name")

    def each(kind: str, prop: str, value: Any = None, with_value: bool = False) -> list[dict]:
        found = []
        for name in names:
            item = {"id": {"attr": name, "type": kind}, "property": prop}
            if with_value:
                item["value"] = value if not callable(value) else value(name)
            found.append(item)
        return found

    callback = callback_of(app, '"type":"why-row"}.className')
    body = {
        "output": callback["output"],
        "outputs": [
            each(ids.WHY_ROW, "className"),
            each(ids.WHY_PANEL, "children"),
            each(ids.PROV_CHIP, "aria-expanded"),
            {"id": ids.NOTIFY, "property": "sendNotifications"},
        ],
        "inputs": [each(ids.PROV_CHIP, "n_clicks", lambda name: 1 if name == "given_name" else 0, True)],
        "state": [
            each(ids.WHY_ROW, "className", "mdm-why-row mdm-hidden", True),
            [{"id": ids.WHY_REF, "property": "data", "value": data}],
            {"id": ids.PERSONA, "property": "data", "value": "data_steward"},
        ],
        "changedPropIds": ['{"attr":"given_name","type":"prov-chip"}.n_clicks'],
    }
    answer = app.server.test_client().post(
        "/_dash-update-component",
        data=json.dumps(body),
        headers={"Content-Type": "application/json", "Host": f"127.0.0.1:{PORT}"},
    )
    assert answer.status_code == 200, answer.get_data(as_text=True)[:300]
    response = json.loads(answer.get_data(as_text=True))["response"]
    row = response['{"attr":"given_name","type":"why-row"}']
    assert row["className"] == "mdm-why-row"
    assert response['{"attr":"given_name","type":"prov-chip"}']["aria-expanded"] == "true"
    panel = json.dumps(response['{"attr":"given_name","type":"why-panel"}'])
    assert "Why this given name?" in panel and provenance.MASKED_NOTE in panel
    assert '{"attr":"city","type":"why-row"}' not in response  # the other row stays as it is


def test_a_reveal_through_dash_fills_the_table_and_keeps_nothing_else(app: Dash, world: World) -> None:
    reason_id = ids.reveal(ids.REVEAL_REASON, "record")
    state = [
        {"id": reason_id, "property": "value", "value": None},
        {"id": ids.RECORD_REF, "property": "data", "value": ref(world, world.other)},
        {"id": ids.PERSONA, "property": "data", "value": "data_steward"},
    ]
    inputs = [{"id": ids.reveal(ids.REVEAL_CONFIRM, "record"), "property": "n_clicks", "value": 1}]
    changed = ['{"page":"record","type":"reveal-confirm"}.n_clicks']
    refused = fire(app, f"{ids.GOLDEN_TABLE}.children", inputs, state, changed)["response"]
    assert list(refused) == [json.dumps(reason_id, sort_keys=True, separators=(",", ":"))]
    assert (
        refused[json.dumps(reason_id, sort_keys=True, separators=(",", ":"))]["error"]
        == reveal.REASON_REQUIRED
    )
    state[0]["value"] = "audit_check"
    answer = fire(app, f"{ids.GOLDEN_TABLE}.children", inputs, state, changed)
    body = json.dumps(answer)
    clear = clear_values(world, world.other)
    table = json.dumps(answer["response"][ids.GOLDEN_TABLE])
    assert all(value in table for value in clear)
    outside = body.replace(table, "")
    assert not any(value in outside for value in clear)
    modal = answer["response"][
        json.dumps(ids.reveal(ids.REVEAL_MODAL, "record"), sort_keys=True, separators=(",", ":"))
    ]
    assert modal["opened"] is False
