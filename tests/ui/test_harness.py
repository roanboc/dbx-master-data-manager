"""The browser harness itself: a seeded workbench answers, a page opens on it, and the helpers work."""

from __future__ import annotations

import json

from playwright.sync_api import expect

from tests.ui.harness import WAIT_MS, axe, count_requests, navigate, requests_during, settle, storage_dump


def test_a_page_opens_on_the_seeded_workbench(page, live) -> None:
    page.goto("/")
    expect(page).to_have_title("Master Data Manager", timeout=WAIT_MS)
    page.wait_for_load_state("networkidle")  # the shell's first callbacks have answered
    assert set(json.loads(storage_dump(page))) == {"local", "session"}
    assert requests_during(page, lambda: None, settle_ms=50) == []
    assert isinstance(axe(page), list)
    assert live.state.hub.registry.published_entities() == ["organisation", "person"]


#: a callback request sent once the page is leaving; it answers whether the request settled and the count
_LATE_REQUEST = """async () => {
    window.mdmLeaving = true;
    let settled = false;
    fetch("/_dash-update-component", { method: "POST" }).finally(() => { settled = true; });
    await new Promise((resolve) => setTimeout(resolve, 300));
    return [settled, window.mdmPending];
}"""


def test_navigate_holds_back_what_a_poll_starts_while_the_page_is_leaving(page, live) -> None:
    count_requests(page)
    page.goto("/")
    expect(page).to_have_title("Master Data Manager", timeout=WAIT_MS)
    settle(page)
    assert page.evaluate(_LATE_REQUEST) == [False, 0]  # held back: never sent, so nothing to cut off
    navigate(page)
    expect(page).to_have_title("Master Data Manager", timeout=WAIT_MS)
    assert page.evaluate("() => [window.mdmLeaving === undefined, window.mdmPending >= 0]") == [True, True]
    settle(page)  # the new page counts its own requests, and they answer
