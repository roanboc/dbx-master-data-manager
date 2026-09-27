"""The golden record and the source record in a browser (plan B.9.3): the four tabs, a provenance chip
opening the Why under a value, a merged ID showing its survivor, source keys and source records, a reveal that
asks a reason, is logged and leaves nothing in the browser, a consumer offered no reveal, and axe in both
schemes.

The records are read from the hub the module's workbench serves (`live.state.hub`); one merge is made
through the services before the first check, so a merged ID exists in the invented demo world.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from mdm.models.authority import Actor
from mdm.ui import ids
from mdm.ui.components import provenance, reveal
from mdm.ui.pages import record, source
from tests.helpers import golden_rows
from tests.ui.harness import WAIT_MS, axe, data_attribute_values, storage_dump

STEWARD = Actor("persona:data_steward", "person", "data_steward", persona=True)
COORDINATOR = Actor("persona:coordinating_steward", "person", "coordinating_steward", persona=True)
OWNER = Actor("persona:data_owner", "person", "data_owner", persona=True)


#: counts the callback requests in flight, so a check leaves a page only once they have answered (a
#: request cut off by a navigation is logged as a console error by Dash)
_COUNT_REQUESTS = """(() => {
    const original = window.fetch;
    window.mdmPending = 0;
    window.fetch = function (...args) {
        const target = String((args[0] && args[0].url) || args[0]);
        const counted = target.includes("_dash-update-component");
        if (counted) { window.mdmPending += 1; }
        return original.apply(this, args).finally(() => { if (counted) { window.mdmPending -= 1; } });
    };
})();"""


def settle(page: Page) -> None:
    """Waits until no callback request is in flight, twice over a short pause."""
    for _ in range(2):
        page.wait_for_function("() => window.mdmPending === 0", timeout=WAIT_MS)
        page.wait_for_timeout(150)


def open_page(page: Page, path: str, ready: str) -> None:
    """Opens `path` and waits until `ready` (a CSS selector) shows and every callback has answered."""
    page.add_init_script(_COUNT_REQUESTS)
    page.goto(path)
    expect(page.locator(ready).first).to_be_visible(timeout=WAIT_MS)
    settle(page)


def choose(page: Page, select_id: str, option: str) -> None:
    page.locator(f"#{select_id}").click()
    page.get_by_role("option", name=option, exact=True).click()


# ------------------------------------------------------------------------------------------ the records


@dataclass(frozen=True)
class Records:
    organisation: str  # an organisation whose name has a runner-up
    person: str  # a person with personal values to reveal
    survivor: str  # a person another was merged into
    retired: str  # the person merged
    linked: str  # a source key linked to `person`
    unlinked: str | None  # a source key under review, linked to nothing


def _organisation_with_runner_up(hub) -> str:
    for master_id in sorted(golden_rows(hub, "organisation", status="active")):
        if hub.lookup.why("organisation", master_id, "name", actor=STEWARD).runners_up:
            return master_id
    pytest.skip("the seeded world has no organisation whose name has a runner-up")


def _unlinked(hub) -> str | None:
    for row in hub.inbox.page("team", actor=STEWARD, kind="review").rows:
        found = hub.lookup.resolve(row.subject, actor=STEWARD)
        if found.kind == "source" and found.master_id is None:
            return row.subject
    return None


@pytest.fixture(scope="module")
def records(live) -> Records:
    hub = live.state.hub
    persons = sorted(golden_rows(hub, "person", status="active"))
    assert len(persons) >= 3
    person, survivor, retired = persons[0], persons[1], persons[2]
    hub.lifecycle.merge("person", survivor, retired, maker=STEWARD, checker=COORDINATOR, reason="duplicate")
    linked = hub.lookup.members("person", person, actor=STEWARD)[0].source
    return Records(_organisation_with_runner_up(hub), person, survivor, retired, linked, _unlinked(hub))


def clear_values(hub, master_id: str) -> set[str]:
    """The personal values of a golden record in clear, as the data owner reveals them for the check."""
    shown = hub.lookup.golden("person", master_id, actor=OWNER, reveal=True, reason="audit_check")
    return {v.value for v in shown if v.personal and v.value and len(v.value) >= 3}


def access_rows(hub, reason: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    after = None
    while True:
        page = hub.store.access_log(after, 500)
        rows.extend(r for r in page if r["reason"] == reason)
        if len(page) < 500:
            return rows
        after = page[-1]["access_id"]


def golden(page: Page):
    return page.locator(f"#{ids.GOLDEN_TABLE} table.mdm-golden")


# ------------------------------------------------------------------------------------------ the record


def test_the_record_shows_its_header_and_four_tabs(page: Page, live, records: Records) -> None:
    open_page(page, f"/record/{records.organisation}", f"#{ids.GOLDEN_TABLE} table.mdm-golden")
    header = live.state.hub.lookup.header("organisation", records.organisation, actor=STEWARD)
    expect(page.get_by_role("heading", level=1)).to_have_text(header.title)
    expect(page.locator(f"#{ids.RECORD_HEADER}")).to_contain_text(records.organisation)
    expect(page.get_by_text(record.LATER)).to_be_visible()
    tabs = page.get_by_role("tab")
    expect(tabs).to_have_count(4)
    expect(page.get_by_role("tab", name="Golden")).to_have_attribute("aria-selected", "true")
    expect(golden(page).locator("tbody tr").first).to_be_visible()

    page.get_by_role("tab", name=re.compile(r"^Sources and cross-references \(\d+\)$")).click()
    members = page.locator(f"#{ids.MEMBERS_TABLE}")
    expect(members.get_by_role("link").first).to_have_attribute("href", re.compile(r"^/source/"))
    page.get_by_role("tab", name="Timeline").click()
    expect(page.locator(f"#{ids.TIMELINE_LIST} li").first).to_contain_text("commit")
    page.get_by_role("tab", name="Relationships").click()
    expect(page.locator(f"#{ids.RELATIONSHIPS_TABLE}")).not_to_contain_text(record.LOADING, timeout=WAIT_MS)
    page.get_by_role("tab", name="Golden").click()
    expect(golden(page)).to_be_visible()
    assert page.get_by_role("button", name=re.compile(r"Detach|Merge|Retire|Edit")).count() == 0
    settle(page)


def why_box(page: Page, label: str):
    """The Why opened under a value: the box headed "Why this <label>?"."""
    return page.locator(".mdm-why-box").filter(has_text=f"Why this {label}?")


def test_a_provenance_chip_opens_the_why_under_its_value(page: Page, live, records: Records) -> None:
    open_page(page, f"/record/{records.organisation}", f"#{ids.GOLDEN_TABLE} table.mdm-golden")
    why = live.state.hub.lookup.why("organisation", records.organisation, "name", actor=STEWARD)
    chip = page.get_by_role("button", name=re.compile(r", why this name\?$"))
    expect(chip).to_have_attribute("aria-expanded", "false")
    chip.click()
    box = why_box(page, "name")
    expect(box).to_be_visible(timeout=WAIT_MS)
    expect(chip).to_have_attribute("aria-expanded", "true")
    expect(box).to_contain_text(why.sentence)
    expect(box.get_by_role("cell", name="Runner-up").first).to_be_visible()
    assert box.inner_text().count("Survivorship rules") == 1  # the rules named once
    # the value it explains stays in view, right above it
    value = golden(page).get_by_role("row").filter(has=chip)
    assert value.bounding_box()["y"] < box.bounding_box()["y"]
    chip.click()
    expect(box).to_be_hidden(timeout=WAIT_MS)
    expect(chip).to_have_attribute("aria-expanded", "false")
    settle(page)


def test_a_merged_id_shows_its_survivor_with_a_notice(page: Page, live, records: Records) -> None:
    open_page(page, f"/record/{records.retired}", f"#{ids.GOLDEN_TABLE} table.mdm-golden")
    expect(
        page.get_by_text(f"{records.retired} was merged into {records.survivor}; showing the survivor.")
    ).to_be_visible()
    expect(page.locator(f"#{ids.RECORD_HEADER}")).to_contain_text(f"retired IDs: {records.retired}")
    page.get_by_role("tab", name="Timeline").click()
    expect(page.locator(f"#{ids.TIMELINE_LIST} li").first).to_contain_text(
        f"{records.retired} merged into this record"
    )
    expect(page.locator(f"#{ids.TIMELINE_LIST} li").first).to_contain_text("Merge")
    settle(page)


def test_a_source_key_and_a_source_record_open(page: Page, live, records: Records) -> None:
    open_page(page, f"/record/{records.linked}", f"#{ids.GOLDEN_TABLE} table.mdm-golden")
    expect(
        page.get_by_text(f"{records.linked} is linked to {records.person}; this is its golden record.")
    ).to_be_visible()
    page.get_by_role("link", name="Open the source record").click()
    expect(page.locator(f"#{ids.SOURCE_VALUES} table")).to_be_visible(timeout=WAIT_MS)
    expect(page.get_by_role("heading", level=1)).to_be_visible()
    expect(page.get_by_text(source.LATER)).to_be_visible()
    expect(page.get_by_role("link", name=records.person)).to_have_attribute(
        "href", f"/record/{records.person}"
    )
    settle(page)
    assert records.unlinked is not None, "the seeded world has a review task on a record linked to nothing"
    page.goto(f"/record/{records.unlinked}")
    expect(page.get_by_text(f"{records.unlinked} is not linked to a golden record")).to_be_visible(
        timeout=WAIT_MS
    )
    expect(page.get_by_text("Not linked to a golden record.")).to_be_visible()
    settle(page)
    page.goto("/record/ORG-999999")
    expect(page.get_by_text("No record has the ID ORG-999999.")).to_be_visible(timeout=WAIT_MS)
    settle(page)


def test_show_values_asks_a_reason_is_logged_and_leaves_nothing_in_the_browser(
    page: Page, live, records: Records
) -> None:
    hub = live.state.hub
    before = len(access_rows(hub, "subject_request"))
    open_page(page, f"/record/{records.person}", f"#{ids.GOLDEN_TABLE} table.mdm-golden")
    expect(golden(page).locator(".mdm-masked").first).to_be_visible()
    page.get_by_role("button", name="Show values").click()
    dialog = page.get_by_role("dialog", name="Show personal values")
    expect(dialog).to_be_visible(timeout=WAIT_MS)
    dialog.get_by_role("button", name="Show values").click()
    expect(dialog.get_by_text(reveal.REASON_REQUIRED)).to_be_visible(timeout=WAIT_MS)
    assert len(access_rows(hub, "subject_request")) == before  # nothing shown, nothing logged
    dialog.get_by_role("radio", name="Answering the person's request").check()
    dialog.get_by_role("button", name="Show values").click()
    expect(dialog).to_be_hidden(timeout=WAIT_MS)
    expect(
        page.get_by_text(re.compile(r"personal values? shown\. Each is logged with your reason\."))
    ).to_be_visible(timeout=WAIT_MS)
    expect(page.get_by_role("button", name=source.SHOWN)).to_be_disabled()
    clear = clear_values(hub, records.person)
    assert clear
    table = golden(page).inner_text()
    assert all(value in table for value in clear)
    logged = access_rows(hub, "subject_request")
    shown = [v for v in hub.lookup.golden("person", records.person, actor=STEWARD) if v.masked]
    assert len(logged) - before == len(shown)
    kept = storage_dump(page) + page.url + "\n".join(data_attribute_values(page))
    assert not [value for value in clear if value in kept]
    # the Why stays masked after a reveal
    page.get_by_role("button", name=re.compile(r", why this given name\?$")).click()
    box = why_box(page, "given name")
    expect(box).to_be_visible(timeout=WAIT_MS)
    expect(box).to_contain_text(provenance.MASKED_NOTE)
    assert not [value for value in clear if value in box.inner_text()]
    # a tab moved to and back keeps the revealed values
    page.get_by_role("tab", name="Timeline").click()
    expect(page.locator(f"#{ids.TIMELINE_LIST} li").first).to_be_visible(timeout=WAIT_MS)
    page.get_by_role("tab", name="Golden").click()
    assert all(value in golden(page).inner_text() for value in clear)
    settle(page)


def test_a_consumer_is_offered_no_reveal(page: Page, live, records: Records) -> None:
    open_page(page, f"/record/{records.person}", f"#{ids.GOLDEN_TABLE} table.mdm-golden")
    expect(page.get_by_role("button", name="Show values")).to_be_visible()
    choose(page, ids.PERSONA_SELECT, "Consumer")
    expect(page.locator(f"#{ids.ROLE_BADGE}")).to_have_text("Consumer (persona)", timeout=WAIT_MS)
    expect(golden(page)).to_be_visible(timeout=WAIT_MS)
    expect(page.get_by_role("button", name="Show values")).to_have_count(0, timeout=WAIT_MS)
    expect(golden(page).locator(".mdm-masked").first).to_be_visible()
    settle(page)


# ------------------------------------------------------------------------------------------ axe


@pytest.mark.parametrize("page", ["light", "dark"], indirect=True)
def test_axe_finds_nothing_serious_on_the_record_and_the_source_record(
    page: Page, live, records: Records
) -> None:
    open_page(page, f"/record/{records.organisation}", f"#{ids.GOLDEN_TABLE} table.mdm-golden")
    assert axe(page) == []
    page.get_by_role("button", name=re.compile(r", why this name\?$")).click()
    expect(why_box(page, "name")).to_be_visible(timeout=WAIT_MS)
    assert axe(page) == []
    for tab in ("Timeline", "Relationships"):
        page.get_by_role("tab", name=tab).click()
        settle(page)
        assert axe(page) == []
    page.get_by_role("tab", name=re.compile(r"^Sources")).click()
    settle(page)
    assert axe(page) == []
    page.goto(f"/record/{records.person}")
    expect(golden(page)).to_be_visible(timeout=WAIT_MS)
    settle(page)
    page.get_by_role("button", name="Show values").click()
    expect(page.get_by_role("dialog", name="Show personal values")).to_be_visible(timeout=WAIT_MS)
    assert axe(page) == []
    page.keyboard.press("Escape")
    page.goto(f"/source/{records.linked.replace(':', '/', 1)}")
    expect(page.locator(f"#{ids.SOURCE_VALUES} table")).to_be_visible(timeout=WAIT_MS)
    settle(page)
    assert axe(page) == []
