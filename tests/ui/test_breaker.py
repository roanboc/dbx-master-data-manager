"""The quality breaker in a browser (story 3.2): with Organisation's automatic linking paused, the inbox shows
one quiet line under its health figures, with no live role; the decide pane of an Organisation task says
why and what waits, and a Person task says nothing of it; no control on screen restores the band; and axe
finds nothing serious with the line and the notice on screen, light and dark.

This module serves a copy of the seeded template of its own, with the breaker's demo trip on Organisation
(local stores only), so every other module keeps a template with no breaker tripped.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from tools.workbench_live import LiveApp, copy_store, free_port, serve_in_thread, trip_breaker

from mdm.models.authority import Actor
from mdm.ui import ids
from tests.ui.conftest import workbench_settings
from tests.ui.harness import WAIT_MS, axe, navigate, open_inbox, settle

STEWARD = Actor("persona:data_steward", "person", "data_steward", persona=True)
LINE = re.compile(r"^Organisation: automatic linking paused since .+ UTC$")
WHY = "Blind review confirmed 30 of the last 40 automatic links (75%), confidently below 95%."


@pytest.fixture(scope="module")
def live(store_template: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[LiveApp]:
    """This module's workbench over a copy of the template with Organisation's automatic linking paused."""
    path = copy_store(store_template, tmp_path_factory.mktemp("breaker") / "mdm.duckdb")
    trip_breaker(path, "organisation")
    with serve_in_thread(workbench_settings(path), port=free_port()) as served:
        yield served


@pytest.fixture(autouse=True)
def _settled(page: Page) -> Iterator[None]:
    """Every check leaves the page only once its callbacks have answered."""
    yield
    try:
        settle(page)
    except Exception:  # noqa: BLE001 - a page that never counted requests has none in flight
        pass


def task_of(live: LiveApp, entity: str) -> str:
    """An open task of `entity` in Team, as the served persona sees it."""
    rows = live.state.hub.inbox.page("team", actor=STEWARD, entity=entity).rows
    if not rows:
        pytest.skip(f"the seeded world has no open {entity} task")
    return rows[0].task_id


def test_the_inbox_shows_one_quiet_line_for_the_paused_entity(page: Page, live) -> None:
    open_inbox(page)
    lines = page.locator(f"#{ids.HEALTH_STRIP} .mdm-breaker-line")
    expect(lines).to_have_count(1, timeout=WAIT_MS)
    expect(lines.first).to_have_text(LINE)
    assert lines.first.get_attribute("role") is None and lines.first.get_attribute("aria-live") is None
    expect(lines.first.locator(".mdm-icon")).to_have_attribute("aria-hidden", "true")
    expect(page.get_by_role("button", name=re.compile("restore", re.IGNORECASE))).to_have_count(0)
    expect(page.get_by_role("link", name=re.compile("restore", re.IGNORECASE))).to_have_count(0)


def test_the_decide_pane_of_a_paused_entity_says_why_and_what_waits(page: Page, live) -> None:
    organisation = task_of(live, "organisation")
    open_inbox(page, f"/?view=team&task={organisation}")
    pane = page.locator(f"#{ids.DECIDE_PANE}")
    expect(pane.locator(f"[data-task-id='{organisation}']")).to_be_visible(timeout=WAIT_MS)
    notice = pane.locator(".mdm-breaker-notice")
    expect(notice).to_have_count(1)
    summary = notice.locator("summary")
    expect(summary).to_have_text("Automatic linking for Organisation is paused.")
    if notice.locator("details[open]").count() == 0:
        summary.focus()
        page.keyboard.press("Enter")  # the disclosure opens with the keyboard; the shortcuts yield to it
        expect(notice.locator("details[open]")).to_have_count(1)
        assert page.url.endswith(f"/?view=team&task={organisation}")
    expect(notice.locator("p")).to_be_visible()
    expect(notice).to_contain_text(WHY)
    expect(notice).to_contain_text("Only a data owner restores automatic linking, on the command line")
    expect(notice).to_contain_text("there is no button for it here")
    expect(pane.get_by_role("button", name=re.compile("restore", re.IGNORECASE))).to_have_count(0)
    person = task_of(live, "person")
    navigate(page, f"/?view=team&task={person}")
    expect(pane.locator(f"[data-task-id='{person}']")).to_be_visible(timeout=WAIT_MS)
    expect(pane.locator(".mdm-breaker-notice")).to_have_count(0)


@pytest.mark.parametrize("page", ["light", "dark"], indirect=True)
def test_axe_finds_nothing_serious_with_the_breaker_line_and_notice(page: Page, live) -> None:
    organisation = task_of(live, "organisation")
    open_inbox(page, f"/?view=team&task={organisation}")
    expect(page.locator(f"#{ids.DECIDE_PANE} .mdm-breaker-notice")).to_be_visible(timeout=WAIT_MS)
    expect(page.locator(f"#{ids.HEALTH_STRIP} .mdm-breaker-line")).to_be_visible()
    assert axe(page) == []
