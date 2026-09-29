"""Signature batches in a browser (story 3.3, plan 5 B.9): one flow over one batch, in order. G on the inbox
opens Alike reviews; a group shows its pattern, its count and its figures, and its count opens its reviews;
a draw opens the batch's page and says how many it drew; the forced sample is decided in the inbox, with no
decision filled; N on a sample review first asks which comparison misled, and once it is named stages "Not a
match" that splits the reviews sharing the record's value off; every change is listed, masked; above the
threshold a second steward (the coordinating steward persona) confirms, and their own tray counts it down;
the maker's tray lists it, the first chunk commits, and Stop ends it before the next chunk, even while the
throttle holds it; and neither the batch page nor the filtered inbox keeps a value in the browser. axe finds
nothing serious on every state it reaches, light and dark.

This module serves a copy of the seeded template of its own, with a second steward above 3 links, a slow
throttle (a chunk of 6 published rows waits a minute for the next) and chunks of at most 6 published rows
(`capacity.COMMIT_CHUNK_ROWS`, patched for the module), so the world's largest Person group (a dozen
reviews) needs a second steward and commits in more than one chunk. The checks assert the counts the page
reports, never a fixed number. The rest of the sample, after the two decided by keys, is decided through
the services, to keep the flow short.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from tools.workbench_live import LiveApp, copy_store, free_port, serve_in_thread

from mdm import capacity
from mdm.models.authority import Actor
from mdm.ui import ids
from tests import helpers
from tests.ui.conftest import workbench_settings
from tests.ui.harness import (
    WAIT_MS,
    axe,
    data_attribute_values,
    navigate,
    open_batch,
    open_groups,
    open_inbox,
    press,
    requests_during,
    settle,
    storage_dump,
)

STEWARD = Actor("persona:data_steward", "person", "data_steward", persona=True)
OWNER = Actor("persona:data_owner", "person", "data_owner", persona=True)
#: the second steward above this many links (the default is 250)
CHECKER_ABOVE = 3
#: published rows an hour: a chunk of 6 rows waits a minute for the next
THROTTLE = 360
#: the published rows a chunk holds at most, for this module
CHUNK_ROWS = 6
#: seconds a confirmed batch waits in the tray here, long enough to see both trays count it down
BATCH_WINDOW = 20
#: what the flow carries from one check to the next: the group's key and the batch's ID
FLOW: dict[str, str] = {}


@pytest.fixture(scope="module")
def live(store_template: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[LiveApp]:
    """This module's workbench over a copy of the template, with a second steward above 3 links, a slow
    throttle and chunks of at most 6 published rows."""
    path = copy_store(store_template, tmp_path_factory.mktemp("batch") / "mdm.duckdb")
    settings = workbench_settings(path, batch_checker_above=CHECKER_ABOVE, throttle_rows_per_hour=THROTTLE)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(capacity, "COMMIT_CHUNK_ROWS", CHUNK_ROWS)
        with serve_in_thread(settings, port=free_port()) as served:
            yield served


@pytest.fixture(autouse=True)
def _settled(page: Page) -> Iterator[None]:
    """Every check leaves the page only once its callbacks have answered."""
    yield
    try:
        settle(page)
    except Exception:  # noqa: BLE001 - a page that never counted requests has none in flight
        pass


# ---------------------------------------------------------------------------------------------- helpers


def group_key(live: LiveApp) -> str:
    """The largest Person group's key, as the Alike reviews page lists it first."""
    if "group" not in FLOW:
        found = live.state.hub.batches.groups(actor=STEWARD, entity="person").groups
        if not found:
            pytest.skip("the seeded world has no two alike Person reviews")
        FLOW["group"] = found[0].group_key
    return FLOW["group"]


def batch_id() -> str:
    if "batch" not in FLOW:
        pytest.skip("the flow drew no batch (an earlier check failed)")
    return FLOW["batch"]


def eventually(check: Callable[[], bool], page: Page, *, timeout_ms: int = WAIT_MS * 2) -> None:
    """Waits until `check()` holds, reading the served hub between short pauses of the page."""
    deadline = time.monotonic() + timeout_ms / 1000
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError("the served workbench did not get there in time")
        page.wait_for_timeout(250)


def sample_status(live: LiveApp, task_id: str) -> str | None:
    found = live.state.hub.store.items_by_task([task_id]).get(task_id) or ()
    return next((item.status for item in found if item.batch_id == FLOW.get("batch")), None)


def pane_task(page: Page) -> str:
    task = page.locator(f"#{ids.DECIDE_PANE} section.mdm-decide").get_attribute("data-task-id")
    assert task
    return task


def notification(page: Page, text: str | re.Pattern):
    return page.locator(".mantine-Notification-root").filter(has_text=text).first


def choose(page: Page, select_id: str, option: str) -> None:
    page.locator(f"#{select_id}").click()
    page.get_by_role("option", name=option, exact=True).click()


def acted(bodies: list[dict]) -> list[dict]:
    """The callback requests that asked the act callback to stage something."""
    return [body for body in bodies if ids.ACT_RESULT in str(body.get("output", ""))]


def footer(page: Page):
    return page.locator(f"#{ids.BATCH_VIEW} section.mdm-batch-actions")


# ---------------------------------------------------------------------------------------------- the flow


def test_g_on_the_inbox_opens_alike_reviews(page: Page, live) -> None:
    group_key(live)
    open_inbox(page)
    press(page, "g")
    expect(page.locator(f"#{ids.GROUPS_LIST} h1")).to_have_text("Alike reviews", timeout=WAIT_MS)
    assert page.url.endswith("/groups")
    rail = page.get_by_role("navigation", name="Work").get_by_role("link", name=re.compile(r"^Alike reviews"))
    expect(rail).to_have_attribute("aria-current", "page")
    assert page.locator('[data-mdm-keys="on"]:not(body)').count() == 0  # no single key acts on this page
    settle(page)
    assert requests_during(page, lambda: [press(page, key) for key in ("?", "g", "l", "j")]) == []
    expect(page.get_by_role("dialog", name="Keyboard shortcuts")).to_be_hidden()


@pytest.mark.parametrize("page", ["light", "dark"], indirect=True)
def test_axe_finds_nothing_serious_on_alike_reviews(page: Page, live) -> None:
    open_groups(page)
    expect(page.locator(f"tr[data-group='{group_key(live)}']")).to_be_visible(timeout=WAIT_MS)
    assert axe(page) == []


def test_a_group_shows_its_pattern_and_figures_and_its_count_opens_its_reviews(page: Page, live) -> None:
    key = group_key(live)
    open_groups(page)
    row = page.locator(f"tr[data-group='{key}']")
    expect(row).to_be_visible(timeout=WAIT_MS)
    pattern = row.locator("td[data-column='Pattern']")
    expect(pattern).to_contain_text("Given name = the same")
    expect(pattern).to_contain_text("Birth date ≈ similar")
    expect(row.locator("td[data-column='Labels']")).to_contain_text(re.compile(r"^(No labels yet|Linked in)"))
    expect(row.locator("td[data-column='Blind review']")).to_contain_text(
        re.compile(r"^(No blind review yet|Blind review agreed)")
    )
    count = row.get_by_role("link", name=re.compile(r"reviews with this pattern: open them in the inbox$"))
    shown = int(count.inner_text())
    assert count.get_attribute("href") == f"/?group={key}"
    navigate(page, f"/?group={key}")
    expect(page.locator(f"#{ids.PAGE_LABEL}")).to_have_text(f"Alike reviews: 1–{shown}", timeout=WAIT_MS)
    expect(page.locator(f"#{ids.INBOX_FILTER}")).to_contain_text("Alike reviews: every open review with one")
    # the grid's own code loads after the page: wait for it, then for every review of the group in it
    page.wait_for_function(
        "(n) => { try { return window.dash_ag_grid.getApi('inbox-grid').getDisplayedRowCount() === n; }"
        " catch (e) { return false; } }",
        arg=shown,
        timeout=WAIT_MS,
    )


def test_a_draw_opens_the_batch_and_its_sample_is_decided_in_the_inbox(page: Page, live) -> None:
    key = group_key(live)
    open_groups(page)
    page.locator(f"tr[data-group='{key}']").get_by_role("button", name="Draw a forced sample").click()
    page.wait_for_url(re.compile(r"/batch/BAT-[0-9a-f]{20}$"), timeout=WAIT_MS)
    FLOW["batch"] = page.url.rsplit("/", 1)[1]
    expect(notification(page, re.compile(r"^Sample drawn"))).to_contain_text(
        re.compile(r"Forced sample drawn: \d+ of \d+ reviews\. Decide them in the inbox\.")
    )
    expect(page.locator(f"#{ids.BATCH_VIEW} h1")).to_have_text("Alike Person reviews", timeout=WAIT_MS)
    expect(page.locator(f"#{ids.BATCH_VIEW} .mdm-batch-state")).to_have_text("Forced sample")
    decide = footer(page).get_by_role("link", name="Decide the sample in the inbox")
    expect(decide).to_have_attribute("href", f"/?batch={batch_id()}")
    assert (
        page.locator(
            f"#{ids.BATCH_VIEW} .mdm-link-button-filled, #{ids.BATCH_VIEW} [data-variant='filled']"
        ).count()
        == 1
    )
    navigate(page, f"/?batch={batch_id()}")
    pane = page.locator(f"#{ids.DECIDE_PANE}")
    expect(pane.locator(".mdm-sample-notice")).to_contain_text(
        f"Forced sample for batch {batch_id()}", timeout=WAIT_MS
    )
    expect(pane.locator("section.mdm-decide")).to_have_attribute("data-quiet", "yes")
    assert pane.locator("[data-variant='filled']").count() == 0  # the pane nudges no decision
    task = pane_task(page)
    press(page, "l")
    expect(notification(page, re.compile(r"^In the tray"))).to_be_visible(timeout=WAIT_MS)
    eventually(lambda: sample_status(live, task) == "agreed", page)


def test_a_disagreement_names_its_comparison_first_and_splits_its_reviews_off(page: Page, live) -> None:
    open_inbox(page, f"/?batch={batch_id()}")
    pane = page.locator(f"#{ids.DECIDE_PANE}")
    task = pane_task(page)
    assert acted(requests_during(page, lambda: press(page, "n"))) == []  # stages nothing yet
    choice = pane.locator("details.mdm-split")
    expect(choice).to_have_attribute("open", "")
    assert page.evaluate("() => !!document.activeElement.closest('.mdm-split-choice')")
    assert axe(page) == []
    radios = pane.locator(".mdm-split-choice input[type=radio]")
    values = [radios.nth(i).get_attribute("value") for i in range(radios.count())]
    assert "birth_date" in values and values[-1] == "all"
    for _ in range(values.index("birth_date")):
        page.keyboard.press("ArrowDown")
    expect(pane.locator(".mdm-split-choice input[type=radio][value='birth_date']")).to_be_checked()
    expect(pane.locator(".mdm-split-choice input[type=radio][value='birth_date']")).to_be_focused()
    # every other key yields to the radios, as to any field: J neither moves nor decides
    assert requests_during(page, lambda: press(page, "j", blur=False)) == []
    expect(pane.locator(".mdm-split-choice input[type=radio][value='birth_date']")).to_be_focused()
    hub = live.state.hub
    kept = hub.tray.settings
    hub.tray.settings = kept.with_(undo_seconds=12)  # long enough to read its staged line
    try:
        # the same key decides, focus still on the chosen comparison (finding 19): no blur first
        press(page, "n", blur=False)
        expect(notification(page, re.compile(r"^In the tray"))).to_be_visible(timeout=WAIT_MS)
    finally:
        hub.tray.settings = kept
    # the selection moves on to the next review not staged; back on the staged one, its line says what leaves
    expect(pane.locator(f"section.mdm-decide[data-task-id='{task}']")).to_have_count(0, timeout=WAIT_MS)
    settle(page)
    press(page, "k")
    expect(pane.locator(f"section.mdm-decide[data-task-id='{task}']")).to_be_visible(timeout=WAIT_MS)
    expect(pane).to_contain_text("Not a match, flagged on birth date: when it commits at", timeout=WAIT_MS)
    expect(pane).to_contain_text(f"leave batch {batch_id()}. U undoes it, and nothing leaves.")
    eventually(lambda: sample_status(live, task) in ("disagreed", "split"), page)
    navigate(page, f"/batch/{batch_id()}")
    expect(page.locator(f"#{ids.BATCH_VIEW} h1")).to_be_visible(timeout=WAIT_MS)
    split = page.locator(f"#{ids.BATCH_VIEW} .mdm-split-line").first
    expect(split).to_contain_text(
        re.compile(
            r"was decided Not a match, flagged on birth date: \d+ reviews? left the batch, to be decided one by one\."
        ),
        timeout=WAIT_MS,
    )
    view = hub.batches.batch(batch_id(), actor=STEWARD)
    open_now = sum(1 for review in view.sample if review.open)
    assert open_now >= 1  # a top-up review joins the sample
    expect(page.locator(f"#{ids.BATCH_VIEW} .mdm-sample-item")).to_have_count(len(view.sample))


def test_every_change_lists_the_rows_masked_with_the_summary(page: Page, live) -> None:
    hub = live.state.hub
    clock = hub.tray.clock
    try:
        helpers.decide_sample(hub, batch_id(), actor=STEWARD)  # the rest of the sample, through services
    finally:
        hub.tray.clock = clock
    assert hub.batches.refresh(batch_id()).status == "ready"
    open_batch(page, f"/batch/{batch_id()}")
    view = page.locator(f"#{ids.BATCH_VIEW}")
    expect(view.locator(".mdm-batch-state")).to_have_text("Sample agreed")
    show = footer(page).get_by_role("button", name="Show every change")
    expect(show).to_have_attribute("data-variant", "filled")
    show.click()
    expect(notification(page, re.compile(r"^Every change shown"))).to_be_visible(timeout=WAIT_MS)
    expect(view.locator(".mdm-batch-summary")).to_have_text(
        re.compile(
            rf"^\d+ cross-references? · \d+ golden records? updated · \d+ chunks? of at most {CHUNK_ROWS} "
        ),
        timeout=WAIT_MS,
    )
    rows = view.locator("table.mdm-batch-rows tbody tr")
    expect(rows.first).to_be_visible()
    titles = view.locator("table.mdm-batch-rows .mdm-batch-title")
    assert all("***" in titles.nth(i).inner_text() for i in range(titles.count()))  # Person values masked
    expect(view.locator(f"#{ids.BATCH_ROWS_LABEL}")).to_have_text(re.compile(r"^Rows 1–\d+ of \d+$"))
    assert axe(page) == []


FOCUS_PROBE = """() => {
    const el = document.activeElement;
    if (!el || el === document.body) return null;
    const box = el.getBoundingClientRect();
    const x = Math.min(Math.max(box.left + box.width / 2, 0), innerWidth - 1);
    const y = Math.min(Math.max(box.top + box.height / 2, 0), innerHeight - 1);
    const top = document.elementFromPoint(x, y);
    return {
        seen: !!top && (top === el || el.contains(top)),
        footer: !!el.closest('section.mdm-batch-actions'),
        rows: !!el.closest('table.mdm-batch-rows'),
        where: Math.round(box.top),
    };
}"""


@pytest.mark.parametrize("height", [900, 640])
def test_keyboard_focus_on_the_rows_is_never_hidden_under_the_actions(page: Page, live, height: int) -> None:
    # finding 18 (WCAG 2.2 SC 2.4.11): tabbing down every row's links and disclosures, each focused control
    # is what the browser paints at its middle, never the sticky actions footer, at a tall and a short window
    page.set_viewport_size({"width": 1440, "height": height})
    open_batch(page, f"/batch/{batch_id()}")
    view = page.locator(f"#{ids.BATCH_VIEW}")
    expect(view.locator("table.mdm-batch-rows tbody tr").first).to_be_visible(timeout=WAIT_MS)
    expect(footer(page)).to_have_css("position", "sticky")
    view.locator(".mdm-batch-summary").click()
    seen_rows = 0
    for _ in range(200):
        page.keyboard.press("Tab")
        page.evaluate("() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))")
        probe = page.evaluate(FOCUS_PROBE)
        if probe is None:
            continue
        if probe["footer"]:
            break
        assert probe["seen"], f"a focused control at y {probe['where']} is hidden at a height of {height}"
        seen_rows += probe["rows"]
    else:
        raise AssertionError("Tab never reached the actions footer")
    assert seen_rows >= 4  # the walk went through the rows, not round them


def test_a_second_steward_confirms_above_the_threshold(page: Page, live) -> None:
    open_batch(page, f"/batch/{batch_id()}")
    ask = footer(page).get_by_role("button", name="Ask a second steward to confirm")
    expect(ask).to_have_attribute("data-variant", "filled")
    ask.click()
    expect(notification(page, re.compile(r"^Sent for confirmation"))).to_be_visible(timeout=WAIT_MS)
    confirm = footer(page).get_by_role("button", name="Confirm the batch")
    expect(confirm).to_be_disabled(timeout=WAIT_MS)
    expect(page.locator(f"#{ids.BATCH_ACTION_REASONS}")).to_contain_text(
        "You prepared this batch, so another steward confirms it."
    )
    assert confirm.get_attribute("aria-describedby") == ids.BATCH_ACTION_REASONS
    settle(page)
    # the second data-steward persona shares the maker's role but is another steward: the page is rebuilt for
    # them, and Confirm is theirs (review 3.3)
    choose(page, ids.PERSONA_SELECT, "Data steward 2")
    confirm = footer(page).get_by_role("button", name="Confirm the batch")
    expect(confirm).to_be_enabled(timeout=WAIT_MS)
    expect(confirm).to_have_attribute("data-variant", "filled")
    expect(page.locator(f"#{ids.BATCH_ACTION_REASONS}")).not_to_contain_text("You prepared this batch")
    settle(page)
    choose(page, ids.PERSONA_SELECT, "Coordinating steward")
    confirm = footer(page).get_by_role("button", name="Confirm the batch")
    expect(confirm).to_be_enabled(timeout=WAIT_MS)
    expect(confirm).to_have_attribute("data-variant", "filled")
    expect(footer(page)).to_contain_text("Prepared by a data steward. Check every row")
    hub = live.state.hub
    kept = hub.batches.settings
    hub.batches.settings = kept.with_(undo_seconds=BATCH_WINDOW)  # long enough to see both trays count down
    try:
        confirm.click()
        expect(notification(page, re.compile(r"^Confirmed"))).to_contain_text(
            re.compile(r"Confirmed\. It waits in the tray until \d\d:\d\d:\d\d UTC\."), timeout=WAIT_MS
        )
    finally:
        hub.batches.settings = kept
    button = page.get_by_role("button", name=re.compile(r"^Tray"))
    expect(button).to_have_text(re.compile(r"^Tray 1 · 0:\d\d$"), timeout=WAIT_MS)
    expect(button).to_have_class(re.compile(r"mdm-staged"))


@pytest.mark.parametrize("page", ["dark"], indirect=True)
def test_the_maker_sees_it_commit_and_stops_it_before_the_next_chunk(page: Page, live) -> None:
    open_batch(page, f"/batch/{batch_id()}")  # a fresh tab acts as the data steward again
    button = page.get_by_role("button", name=re.compile(r"^Tray"))
    expect(button).to_have_class(re.compile(r"mdm-staged"), timeout=WAIT_MS)
    button.click()
    tray = page.locator(f"#{ids.TRAY_LIST}")
    expect(tray).to_contain_text(re.compile(rf"Link \d+ alike reviews \({batch_id()}\)"), timeout=WAIT_MS)
    button.click()
    view = page.locator(f"#{ids.BATCH_VIEW}")
    expect(view.locator(".mdm-batch-state")).to_have_text("Committing", timeout=BATCH_WINDOW * 1000 + WAIT_MS)
    expect(view).to_contain_text(re.compile(r"Chunk 1 of \d+ committed"), timeout=WAIT_MS)
    expect(view).to_contain_text("at the agreed rate.")  # the throttle holds the next chunk
    stop = footer(page).get_by_role("button", name="Stop")
    assert stop.get_attribute("data-variant") != "filled"
    stop.click()
    expect(notification(page, re.compile(r"^Stopping"))).to_be_visible(timeout=WAIT_MS)
    expect(view.locator(".mdm-batch-state")).to_have_text("Stopped", timeout=WAIT_MS)
    result = view.locator(".mdm-batch-result").first
    expect(result).to_have_text(
        re.compile(
            r"^Stopped by you after chunk 1 of \d+: \d+ links? committed; \d+ reviews? (is|are) back in the "
            r"queue\.( \d+ went to blind review\.)?$"
        )
    )
    hub = live.state.hub
    back = [item.task_id for item in hub.store.batch_items(batch_id(), ("bulk",), ("released",), None, 1000)]
    assert back, "no review went back to the queue"
    tasks = hub.store.tasks_by_id(back)
    assert all(task.status == "open" for task in tasks.values())
    assert axe(page) == []
    # those reviews are open in the inbox again, among the group's reviews, and nothing marks them waiting
    navigate(page, f"/?group={group_key(live)}")
    expect(page.locator(f"#{ids.INBOX_FILTER}")).to_contain_text("Alike reviews", timeout=WAIT_MS)
    settle(page)
    rows = page.evaluate(
        "(ids) => ids.map((id) => { const n = window.dash_ag_grid.getApi('inbox-grid').getRowNode(id);"
        " return n ? !!n.data.staged : null; })",
        back,
    )
    assert rows == [False] * len(back), rows


def test_the_batch_page_and_the_filtered_inbox_keep_no_value_in_the_browser(page: Page, live) -> None:
    hub = live.state.hub
    targets = {
        item.target
        for item in hub.store.batch_items(batch_id(), ("sample", "bulk", "split"), None, None, 1000)
        if item.target
    }
    clear: set[str] = set()
    for target in targets:
        shown = hub.lookup.golden("person", target, actor=OWNER, reveal=True, reason="audit_check")
        clear |= {v.value for v in shown if v.personal and v.value and len(v.value) >= 3}
    assert clear, "the check looks for the batch's values"
    open_batch(page, f"/batch/{batch_id()}")
    kept = storage_dump(page) + page.url + "\n".join(data_attribute_values(page))
    assert not [value for value in clear if value in kept]
    navigate(page, f"/?batch={batch_id()}")
    expect(page.locator(f"#{ids.INBOX_FILTER}")).to_contain_text(batch_id(), timeout=WAIT_MS)
    settle(page)
    kept = storage_dump(page) + page.url + "\n".join(data_attribute_values(page))
    assert not [value for value in clear if value in kept]
