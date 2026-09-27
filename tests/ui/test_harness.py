"""The browser harness itself: a seeded workbench answers, a page opens on it, and the helpers work."""

from __future__ import annotations

import json

from playwright.sync_api import expect

from tests.ui.harness import WAIT_MS, axe, requests_during, storage_dump


def test_a_page_opens_on_the_seeded_workbench(page, live) -> None:
    page.goto("/")
    expect(page).to_have_title("Master Data Manager", timeout=WAIT_MS)
    page.wait_for_load_state("networkidle")  # the shell's first callbacks have answered
    assert set(json.loads(storage_dump(page))) == {"local", "session"}
    assert requests_during(page, lambda: None, settle_ms=50) == []
    assert isinstance(axe(page), list)
    assert live.state.hub.registry.published_entities() == ["organisation", "person"]
