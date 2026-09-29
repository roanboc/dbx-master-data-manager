"""The shell in a browser (plan B.9.3): the header's controls, the persona switch, a consumer's
navigation, the colour scheme kept across a reload, the key help and its switch, the tray counting down,
undoing and committing a staged decision, navigation without a console error, and axe in both schemes.

Decisions are staged through the services (the inbox's own keys are checked in `test_inbox.py`), as the
persona the workbench serves, so the tab's tray shows them.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from mdm.models.authority import Actor
from mdm.ui import ids
from tests.helpers import seen
from tests.ui.harness import WAIT_MS, axe, count_requests, navigate, press, requests_during, settle

STEWARD = Actor("persona:data_steward", "person", "data_steward", persona=True)


def open_inbox(page: Page, path: str = "/") -> None:
    count_requests(page)
    page.goto(path)
    expect(page.locator(f"#{ids.ROLE_BADGE}")).to_have_text(re.compile(r"\w"), timeout=WAIT_MS)
    settle(page)


def leave(page: Page, path: str | None = None) -> None:
    """Reloads, or goes to `path`, once every callback has answered."""
    navigate(page, path)
    expect(page.locator(f"#{ids.ROLE_BADGE}")).to_have_text(re.compile(r"\w"), timeout=WAIT_MS)
    settle(page)


def keys_page(page: Page) -> None:
    """The shortcuts act only on a page that carries `data-mdm-keys="on"`: the inbox."""
    expect(page.locator(f'#{ids.INBOX}[data-mdm-keys="on"]')).to_have_count(1, timeout=WAIT_MS)


def choose(page: Page, select_id: str, option: str) -> None:
    page.locator(f"#{select_id}").click()
    page.get_by_role("option", name=option, exact=True).click()


#: decisions that change no golden value, by the kind of task that offers them, in the order tried
QUIET_DECISIONS = (("orphan", "keep_orphan"), ("held", "reject_update"))


def stage_quietly(hub) -> tuple[str, str]:
    """Stages, as the served persona, a decision that changes no golden value on the first open task that
    offers one; returns the entry's ID and its label."""
    for kind, decision in QUIET_DECISIONS:
        rows = hub.inbox.page("team", actor=STEWARD, kind=kind).rows
        for row in rows:
            if row.staged is None and row.claimed_by in (None, "you"):
                entry = hub.tray.stage(row.task_id, decision, actor=STEWARD, **seen(hub, row.task_id))
                label = next(v.label for v in hub.tray.entries(actor=STEWARD) if v.entry_id == entry.entry_id)
                return entry.entry_id, label
    pytest.skip("the seeded world has no open task left to decide quietly")


# ------------------------------------------------------------------------------------------ the header


def test_the_header_names_every_control(page: Page, live) -> None:
    open_inbox(page)
    header = page.get_by_role("banner")
    expect(header.get_by_role("link", name="Master Data Manager")).to_be_visible()
    expect(header.get_by_role("textbox", name="Entity")).to_have_value("All entities")
    expect(header.get_by_role("button", name=re.compile(r"^Tray"))).to_be_visible()
    # the persona select says the role; the badge, which would say it twice, shows only on a narrower screen
    expect(page.locator(f"#{ids.ROLE_BADGE}")).to_have_text("Data steward (persona)")
    expect(page.locator(f"#{ids.ROLE_BADGE}")).to_be_hidden()
    expect(header.get_by_role("textbox", name="Act as")).to_have_value("Data steward")
    expect(page.locator(f"#{ids.ENGINE_BADGE}")).to_contain_text("DuckDB · stub")
    expect(header.get_by_role("button", name="Keyboard shortcuts")).to_be_visible()
    expect(header.get_by_role("radiogroup", name="Colour scheme")).to_be_visible()
    nav = page.get_by_role("navigation", name="Work")
    expect(nav.get_by_role("link", name="Inbox")).to_be_visible()
    expect(nav.get_by_role("link", name=re.compile(r"^My queue"))).to_have_attribute("aria-current", "page")
    assert page.title() == "Master Data Manager"


def test_the_persona_switch_changes_the_role_and_a_consumer_has_no_inbox(page: Page, live) -> None:
    open_inbox(page)
    choose(page, ids.PERSONA_SELECT, "Data owner")
    expect(page.locator(f"#{ids.ROLE_BADGE}")).to_have_text("Data owner (persona)", timeout=WAIT_MS)
    choose(page, ids.PERSONA_SELECT, "Consumer")
    expect(page.locator(f"#{ids.ROLE_BADGE}")).to_have_text("Consumer (persona)", timeout=WAIT_MS)
    nav = page.get_by_role("navigation", name="Work")
    expect(nav.get_by_role("link", name="Inbox")).to_be_hidden()
    expect(nav.get_by_text("Your role has no inbox.")).to_be_visible()
    expect(page.get_by_role("button", name=re.compile(r"^Tray"))).to_be_hidden()  # a consumer decides nothing
    expect(page.get_by_text("Your role, consumer, decides no tasks")).to_be_visible()
    leave(page)  # the tab keeps its persona
    expect(page.locator(f"#{ids.ROLE_BADGE}")).to_have_text("Consumer (persona)", timeout=WAIT_MS)
    expect(page.get_by_role("banner").get_by_role("textbox", name="Act as")).to_have_value("Consumer")
    choose(page, ids.PERSONA_SELECT, "Data steward")
    expect(page.locator(f"#{ids.ROLE_BADGE}")).to_have_text("Data steward (persona)", timeout=WAIT_MS)
    expect(nav.get_by_role("link", name="Inbox")).to_be_visible()


def test_the_entity_filter_is_kept_for_the_tab(page: Page, live) -> None:
    open_inbox(page)
    choose(page, ids.ENTITY_SELECT, "Person")
    page.wait_for_function(f"() => sessionStorage.getItem('{ids.ENTITY}') === JSON.stringify('person')")
    leave(page)
    expect(page.get_by_role("banner").get_by_role("textbox", name="Entity")).to_have_value(
        "Person", timeout=WAIT_MS
    )
    choose(page, ids.ENTITY_SELECT, "All entities")
    settle(page)  # the inbox reloads for every entity; its answer must land before the page closes


# ------------------------------------------------------------------------------------------ scheme and keys


def test_dark_mode_applies_and_survives_a_reload(page: Page, live) -> None:
    open_inbox(page)
    html = page.locator("html")
    expect(html).to_have_attribute("data-mantine-color-scheme", "light")
    page.get_by_role("radiogroup", name="Colour scheme").get_by_text("Dark").click()
    expect(html).to_have_attribute("data-mantine-color-scheme", "dark")
    leave(page)
    expect(html).to_have_attribute("data-mantine-color-scheme", "dark", timeout=WAIT_MS)
    page.get_by_role("radiogroup", name="Colour scheme").get_by_text("Light").click()
    expect(html).to_have_attribute("data-mantine-color-scheme", "light")


@pytest.mark.parametrize("page", ["dark"], indirect=True)
def test_a_dark_browser_starts_dark(page: Page, live) -> None:
    open_inbox(page)
    expect(page.locator("html")).to_have_attribute("data-mantine-color-scheme", "dark", timeout=WAIT_MS)


def test_the_help_lists_the_keys_and_turning_them_off_stops_them(page: Page, live) -> None:
    open_inbox(page)
    page.get_by_role("button", name="Keyboard shortcuts").click()
    dialog = page.get_by_role("dialog", name="Keyboard shortcuts")
    expect(dialog).to_be_visible()
    for what in (
        "Next task",
        "Link to the chosen candidate; in a blind review, it belongs there (a pair: the same)",
        "Undo",
        "This help",
    ):
        expect(dialog.get_by_role("cell", name=what, exact=True)).to_be_visible()
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()

    keys_page(page)
    press(page, "?")
    expect(dialog).to_be_visible()
    switch = dialog.get_by_role("switch", name="Use single-key shortcuts")
    expect(switch).to_be_checked()
    switch.click()  # the label toggles it
    expect(switch).not_to_be_checked()
    expect(page.locator("body")).to_have_attribute("data-mdm-keys", "off")
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    address = page.url
    assert requests_during(page, lambda: [press(page, key) for key in ("j", "k", "2", "l", "g")]) == []
    assert page.url == address  # G, too, does nothing with the keys off (story 3.3)
    press(page, "?")
    page.wait_for_timeout(300)
    expect(dialog).to_be_hidden()

    leave(page)  # kept per browser
    expect(page.locator("body")).to_have_attribute("data-mdm-keys", "off", timeout=WAIT_MS)
    page.get_by_role("button", name="Keyboard shortcuts").click()
    dialog.get_by_role("switch", name="Use single-key shortcuts").click()
    expect(page.locator("body")).to_have_attribute("data-mdm-keys", "on")


def test_the_keys_yield_to_a_field(page: Page, live) -> None:
    open_inbox(page)
    keys_page(page)
    page.get_by_role("banner").get_by_role("textbox", name="Entity").focus()
    page.keyboard.press("?")
    page.wait_for_timeout(300)
    expect(page.get_by_role("dialog", name="Keyboard shortcuts")).to_be_hidden()


# ------------------------------------------------------------------------------------------ the tray


def test_the_tray_counts_down_and_undoes_a_staged_decision(page: Page, live) -> None:
    hub = live.state.hub
    window = hub.tray.settings
    hub.tray.settings = window.with_(undo_seconds=120)  # long enough to undo in a browser
    try:
        entry_id, label = stage_quietly(hub)
    finally:
        hub.tray.settings = window
    open_inbox(page)
    button = page.get_by_role("button", name=re.compile(r"^Tray"))
    expect(button).to_have_text(re.compile(r"Tray 1 · [12]:\d\d"), timeout=WAIT_MS)
    expect(button).to_have_class(re.compile(r"mdm-staged"))
    expect(button).to_have_attribute("aria-expanded", "false")
    button.click()
    expect(button).to_have_attribute("aria-expanded", "true")
    undo = page.get_by_role("button", name=f"Undo: {label}")
    expect(undo).to_be_visible()
    expect(undo).to_be_focused()  # opening the tray moves the focus into it
    expect(page.locator(f"[id*='{entry_id}']").first).to_have_text(re.compile(r"[12]:\d\d"))
    assert axe(page) == []
    undo.click()
    expect(page.get_by_text("Undone. The task is back in your queue.")).to_be_visible(timeout=WAIT_MS)
    expect(button).to_have_text("Tray", timeout=WAIT_MS)
    expect(
        page.locator(f"#{ids.TRAY_LIST}").get_by_text(re.compile(r"^Undone · \d\d:\d\d UTC$"))
    ).to_be_visible()
    expect(
        page.locator(f"#{ids.TRAY_LIST}").get_by_role("heading", name="Done in the last 10 minutes")
    ).to_be_visible()
    assert next(v for v in hub.tray.entries(actor=STEWARD) if v.entry_id == entry_id).status == "undone"
    settle(page)


def test_the_tray_reports_a_decision_that_committed(page: Page, live) -> None:
    hub = live.state.hub
    stage_quietly(hub)
    open_inbox(page)
    notification = page.locator(".mantine-Notification-root").filter(has_text="Committed")
    expect(notification.first).to_be_visible(timeout=WAIT_MS)
    expect(page.get_by_role("button", name=re.compile(r"^Tray"))).to_have_text("Tray", timeout=WAIT_MS)
    settle(page)


# ------------------------------------------------------------------------------------------ navigation and axe


def test_moving_between_pages_logs_no_console_error(page: Page, live) -> None:
    open_inbox(page)
    leave(page, "/record/ORG-000001")
    expect(page.locator(f"#{ids.PAGE}")).not_to_be_empty(timeout=WAIT_MS)
    page.get_by_role("navigation", name="Work").get_by_role("link", name="Inbox").click()
    expect(page).to_have_url(re.compile(r"/$"))
    leave(page, "/nowhere")
    expect(page.get_by_text("There is no page at /nowhere; this is the inbox.")).to_be_visible(
        timeout=WAIT_MS
    )
    page.get_by_role("link", name="Master Data Manager").click()
    expect(page).to_have_url(re.compile(r"/$"))
    settle(page)


def test_the_skip_link_is_first_and_reaches_the_main_content(page: Page, live) -> None:
    open_inbox(page)
    page.keyboard.press("Tab")
    skip = page.get_by_role("link", name="Skip to the main content")
    expect(skip).to_be_focused()
    expect(skip).to_be_visible()


@pytest.mark.parametrize("page", ["light", "dark"], indirect=True)
def test_axe_finds_nothing_serious_on_the_shell(page: Page, live) -> None:
    open_inbox(page)
    assert axe(page) == []
    page.get_by_role("button", name="Keyboard shortcuts").click()
    expect(page.get_by_role("dialog", name="Keyboard shortcuts")).to_be_visible()
    assert axe(page) == []
    page.keyboard.press("Escape")
    page.get_by_role("button", name=re.compile(r"^Tray")).click()
    expect(page.get_by_text("Staged decisions")).to_be_visible()
    assert axe(page) == []
