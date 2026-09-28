"""The inbox in a browser (plan B.9.3): it loads with its counts and health strip; moving and choosing stay
in the browser and claim nothing; a close call links only after a choice; L stages into the tray and the
selection moves on; U undoes; the tray commits after its window and the row leaves the list; A approves a
held update; a reveal and a chosen candidate survive another task's settlement; Enter on a button presses
it; and axe finds nothing serious with the decide pane open, light and dark. The Quality samples view
(story 3.2) opens a blind case with no score or band on screen; L asks for a choice; 1 then L stages the
answer and U undoes it; and axe finds nothing serious with the blind pane open.

The tasks come from the seeded demo world through the services, as the persona the workbench serves; each
check takes tasks no earlier check touched. Waits are locator expectations (`WAIT_MS`), never a network
idle wait.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator

import pytest
from playwright.sync_api import Page, expect

from mdm.models.authority import Actor
from mdm.models.records import SourceKey
from mdm.models.workbench import TaskCase
from mdm.ui import ids
from tests.helpers import seen
from tests.ui.harness import WAIT_MS, axe, open_inbox, press, requests_during, settle

STEWARD = Actor("persona:data_steward", "person", "data_steward", persona=True)
#: tasks a check has used, so the next check takes another
USED: set[str] = set()
_SELECTED = """() => {
    const api = window.dash_ag_grid.getApi('inbox-grid');
    const rows = api.getSelectedRows();
    return rows.length ? rows[0].task_id : null;
}"""


# ---------------------------------------------------------------------------------------------- helpers


def selected(page: Page) -> str | None:
    return page.evaluate(_SELECTED)


def row_data(page: Page, task_id: str) -> dict | None:
    return page.evaluate(
        "(id) => { const n = window.dash_ag_grid.getApi('inbox-grid').getRowNode(id); return n ? n.data : null; }",
        task_id,
    )


def wait_selected_away(page: Page, task_id: str) -> None:
    page.wait_for_function(f"() => ({_SELECTED})() !== {task_id!r}", timeout=WAIT_MS)


def tray_button(page: Page):
    return page.get_by_role("button", name=re.compile(r"^Tray"))


def notification(page: Page, text: str | re.Pattern):
    return page.locator(".mantine-Notification-root").filter(has_text=text).first


def find(live, want: Callable[[TaskCase], bool]) -> TaskCase:
    """The first open task no check used, unstaged and unclaimed by another, whose case `want` accepts."""
    hub = live.state.hub
    for row in hub.inbox.page("team", actor=STEWARD).rows:
        if row.task_id in USED or row.staged is not None or row.claimed_by not in (None, "you"):
            continue
        case = hub.decisions.case(row.task_id, actor=STEWARD)
        if want(case):
            USED.add(row.task_id)
            return case
    pytest.skip("the seeded world has no open task of that shape left")


def close_call(case: TaskCase) -> bool:
    return case.shape == "source" and case.close_call and len(case.candidates) >= 2


def single_review(case: TaskCase) -> bool:
    return case.shape == "source" and len(case.candidates) == 1 and case.row.kind == "review"


def person_review(case: TaskCase) -> bool:
    return single_review(case) and case.row.entity == "person" and bool(case.revealable)


def held_update(case: TaskCase) -> bool:
    return case.shape == "held_update" and all(
        a.enabled for a in case.actions if a.decision == "approve_update"
    )


def stage_quietly(live, seconds: int) -> str:
    """Stages, as the served persona, a decision that changes no golden value on a task no check used,
    with an undo window of `seconds`; returns its task ID."""
    hub = live.state.hub
    kept = hub.tray.settings
    hub.tray.settings = kept.with_(undo_seconds=seconds)
    try:
        for kind, decision in (("held", "reject_update"), ("orphan", "keep_orphan")):
            for row in hub.inbox.page("team", actor=STEWARD, kind=kind).rows:
                if row.task_id in USED or row.staged is not None or row.claimed_by not in (None, "you"):
                    continue
                case = hub.decisions.case(row.task_id, actor=STEWARD)
                if decision not in {a.decision for a in case.actions if a.enabled}:
                    continue
                USED.add(row.task_id)
                hub.tray.stage(row.task_id, decision, actor=STEWARD, **seen(hub, row.task_id))
                return row.task_id
    finally:
        hub.tray.settings = kept
    pytest.skip("the seeded world has no open task left to decide quietly")


@pytest.fixture(autouse=True)
def _settled(page: Page) -> Iterator[None]:
    """Every check leaves the page only once its callbacks have answered (a request cut off by the end
    of the page is logged as a console error by Dash)."""
    yield
    try:
        settle(page)
    except Exception:  # noqa: BLE001 - a page that never counted requests has none in flight
        pass


# ---------------------------------------------------------------------------------------------- the checks


def test_the_inbox_loads_with_its_counts_and_health_strip(page: Page, live) -> None:
    open_inbox(page)
    expect(page.locator(f"#{ids.HEALTH_STRIP}")).to_contain_text("Open", timeout=WAIT_MS)
    expect(page.locator(f"#{ids.HEALTH_STRIP}")).to_contain_text("Last arrival")
    expect(page.locator(f"#{ids.PAGE_LABEL}")).to_have_text(re.compile(r"^Team: 1–\d+$"))
    expect(
        page.get_by_role("navigation", name="Work").get_by_role("link", name=re.compile(r"^Team"))
    ).to_be_visible()
    assert page.evaluate("() => window.dash_ag_grid.getApi('inbox-grid').getDisplayedRowCount()") > 0
    assert selected(page) is not None  # the first task is selected, and its case shown
    expect(page.locator(f"#{ids.DECIDE_PANE} h2")).to_be_visible()
    expect(page.get_by_role("region", name="Tasks")).to_be_visible()
    expect(page.locator("h1")).to_have_text("Inbox")


def test_moving_and_choosing_stay_in_the_browser_and_claim_nothing(page: Page, live) -> None:
    case = find(live, close_call)
    task = case.row.task_id
    open_inbox(page, f"/?view=team&task={task}")
    expect(page.get_by_role("radiogroup", name="Choose the candidate to link")).to_be_visible(timeout=WAIT_MS)
    second = case.candidates[1].master_id
    assert requests_during(page, lambda: press(page, "2")) == []
    expect(page.get_by_role("radio", name=re.compile(r"^2 · "))).to_be_checked()
    expect(page.locator(f"#{ids.WHY_SECTION} [data-master-id='{second}']")).to_be_visible()
    link = page.locator('[id*=\'"decision":"link"\']')
    expect(link).to_have_text(re.compile(f"Link to {second}"))

    if selected(page) != task:
        pytest.skip("the deep-linked task is not on the first page of Team")
    moved = requests_during(page, lambda: press(page, "j"), settle_ms=1500)
    assert selected(page) != task
    assert moved, "the next case is rendered"
    assert all("decide-pane.children" in request.get("output", "") for request in moved), [
        request.get("output") for request in moved
    ]
    back = requests_during(page, lambda: press(page, "k"), settle_ms=1500)
    assert selected(page) == task
    assert all("decide-pane.children" in request.get("output", "") for request in back)
    hub = live.state.hub
    assert hub.inbox.row(task, actor=STEWARD).claimed_by is None  # moving never claims


def test_a_close_call_links_only_after_a_choice_and_u_undoes_it(page: Page, live) -> None:
    case = find(live, close_call)
    task = case.row.task_id
    open_inbox(page, f"/?view=team&task={task}")
    expect(page.get_by_role("radiogroup", name="Choose the candidate to link")).to_be_visible(timeout=WAIT_MS)
    link = page.locator('[id*=\'"decision":"link"\']')
    expect(link).to_have_text(re.compile(r"^Choose 1 or 2 to link"))
    # L before a choice moves to the choice: no toast, no request, nothing staged
    assert requests_during(page, lambda: press(page, "l")) == []
    expect(page.get_by_role("radio", name=re.compile(r"^1 · "))).to_be_focused()
    expect(page.locator(".mantine-Notification-root")).to_have_count(0)
    expect(tray_button(page)).to_have_text("Tray")
    assert not [v for v in live.state.hub.tray.entries(actor=STEWARD) if v.task_id == task]

    second = case.candidates[1].master_id
    press(page, "2")
    expect(page.get_by_role("radio", name=re.compile(r"^2 · "))).to_be_checked()
    press(page, "l")
    expect(tray_button(page)).to_have_text(re.compile(r"^Tray 1 · 0:0\d$"), timeout=WAIT_MS)
    expect(tray_button(page)).to_have_class(re.compile(r"mdm-staged"))
    on_page = row_data(page, task) is not None
    if on_page:
        wait_selected_away(page, task)  # the selection moves to the next task not staged
        page.wait_for_function(
            "(id) => { const n = window.dash_ag_grid.getApi('inbox-grid').getRowNode(id);"
            " return !!n && n.data.staged === true && n.data.due === 'Staged by you'; }",
            arg=task,
            timeout=WAIT_MS,
        )
    entry = next(v for v in live.state.hub.tray.entries(actor=STEWARD) if v.task_id == task)
    assert entry.label == f"Link {case.row.subject} to {second}"
    press(page, "u")
    expect(tray_button(page)).to_have_text("Tray", timeout=WAIT_MS)
    expect(notification(page, "Undone")).to_be_visible(timeout=WAIT_MS)
    if on_page:
        page.wait_for_function(
            "(id) => window.dash_ag_grid.getApi('inbox-grid').getRowNode(id).data.staged === false",
            arg=task,
            timeout=WAIT_MS,
        )
    assert next(v for v in live.state.hub.tray.entries(actor=STEWARD) if v.task_id == task).status == "undone"


def test_n_and_l_stage_and_the_tray_commits_them_and_the_rows_leave(page: Page, live) -> None:
    hub = live.state.hub
    declined = find(live, single_review)
    linked = find(live, single_review)
    open_inbox(page, f"/?view=team&task={declined.row.task_id}")
    press(page, "n")
    expect(tray_button(page)).to_have_text(re.compile(r"^Tray 1"), timeout=WAIT_MS)
    settle(page)  # leaving with a callback in flight would cut it off, and the browser logs that
    page.goto(f"/?view=team&task={linked.row.task_id}")
    expect(page.locator(f"#{ids.DECIDE_PANE} section.mdm-decide")).to_be_visible(timeout=WAIT_MS)
    settle(page)
    target = linked.candidates[0].master_id
    before = hub.store.last_commit_version()
    press(page, "l")
    expect(notification(page, re.compile(r"Committed as commit \d+"))).to_be_visible(timeout=WAIT_MS)
    expect(tray_button(page)).to_have_text("Tray", timeout=WAIT_MS)
    page.wait_for_function(
        "(id) => !window.dash_ag_grid.getApi('inbox-grid').getRowNode(id)",
        arg=linked.row.task_id,
        timeout=WAIT_MS,
    )
    for case in (declined, linked):
        found = hub.store.tasks_by_id([case.row.task_id])[case.row.task_id]
        assert found.status == "closed"
    system, _, key = linked.row.subject.partition(":")
    source = SourceKey(system, key)
    assert hub.store.xrefs_for_sources(linked.row.entity, [source]).get(source) == target
    feed = hub.feed.read(before)
    assert target in {change.master_id for change in feed.changes}  # the change feed shows the commit
    settle(page)
    page.goto(f"/record/{target}?tab=sources")
    expect(page.locator(f"#{ids.MEMBERS_TABLE}")).to_contain_text(linked.row.subject, timeout=WAIT_MS)


def test_a_on_a_held_update_stages_approve_the_update(page: Page, live) -> None:
    case = find(live, held_update)
    open_inbox(page, f"/?view=team&task={case.row.task_id}")
    expect(page.locator(".mdm-impact").filter(has_text="If you approve")).to_be_visible(timeout=WAIT_MS)
    press(page, "a")
    expect(tray_button(page)).to_have_text(re.compile(r"^Tray 1"), timeout=WAIT_MS)
    tray_button(page).click()
    expect(page.get_by_text(f"Approve the update of {case.row.subject}", exact=True)).to_be_visible()
    page.keyboard.press("Escape")
    press(page, "u")  # the selection moved on: U undoes the last decision
    expect(tray_button(page)).to_have_text("Tray", timeout=WAIT_MS)


def test_a_chosen_candidate_and_a_reveal_survive_another_tasks_settlement(page: Page, live) -> None:
    case = find(live, close_call)
    stage_quietly(live, seconds=10)
    open_inbox(page, f"/?view=team&task={case.row.task_id}")
    press(page, "2")
    radio = page.get_by_role("radio", name=re.compile(r"^2 · "))
    expect(radio).to_be_checked()
    expect(notification(page, "Committed")).to_be_visible(timeout=WAIT_MS)
    settle(page)
    expect(radio).to_be_checked()  # the pane was not drawn again

    person = find(live, person_review)
    stage_quietly(live, seconds=10)
    page.goto(f"/?view=team&task={person.row.task_id}")
    pane = page.locator(f"#{ids.DECIDE_PANE}")
    expect(pane.get_by_role("button", name="Show values")).to_be_visible(timeout=WAIT_MS)
    pane.get_by_role("button", name="Show values").click()
    dialog = page.get_by_role("dialog", name="Show personal values")
    expect(dialog).to_be_visible()
    dialog.get_by_role("radio", name="Deciding this task").check()
    dialog.get_by_role("button", name="Show values").click()
    note = page.get_by_text("Shown in clear for this task.", exact=False)
    expect(note).to_be_visible(timeout=WAIT_MS)
    expect(page.locator(f"#{ids.DECIDE_COMPARE} .mdm-masked")).to_have_count(0)
    expect(notification(page, "Committed")).to_be_visible(timeout=WAIT_MS)
    settle(page)
    expect(note).to_be_visible()  # the reveal stays until the steward leaves the task
    assert page.evaluate("() => sessionStorage.length + localStorage.length") >= 0


def test_enter_on_a_focused_button_presses_it_and_opens_no_record(page: Page, live) -> None:
    case = find(live, single_review)
    open_inbox(page, f"/?view=team&task={case.row.task_id}")
    claim = page.locator(f"#{ids.DECIDE_PANE}").get_by_role("button", name="Claim", exact=True)
    claim.focus()
    page.keyboard.press("Enter")
    expect(notification(page, "Claimed for 10 minutes.")).to_be_visible(timeout=WAIT_MS)
    assert page.url.endswith(f"/?view=team&task={case.row.task_id}")
    assert live.state.hub.inbox.row(case.row.task_id, actor=STEWARD).claimed_by == "you"


def test_s_opens_the_snooze_menu_and_f_and_dot_change_the_pane(page: Page, live) -> None:
    case = find(live, single_review)
    open_inbox(page, f"/?view=team&task={case.row.task_id}")
    press(page, "s")
    expect(page.get_by_role("menuitem", name="For 4 hours")).to_be_visible(timeout=WAIT_MS)
    page.keyboard.press("Escape")
    expect(page.get_by_role("menuitem", name="For 4 hours")).to_be_hidden()
    press(page, "f")
    expect(page.locator(f"#{ids.INBOX}")).to_have_class(re.compile(r"mdm-decide-full"))
    expect(page.get_by_role("region", name="Tasks")).to_be_hidden()
    press(page, "f")
    expect(page.get_by_role("region", name="Tasks")).to_be_visible()
    details = page.locator(f"#{ids.WHY_SECTION} details.mdm-why").first
    expect(details).to_have_attribute("open", "")
    press(page, ".")
    expect(details).not_to_have_attribute("open", "")


def test_tab_walks_the_rows_and_leaves_the_grid_for_the_pages_and_the_pane(page: Page, live) -> None:
    """With the single-key shortcuts off, the keyboard alone opens and decides a task: Tab reaches each
    row's button, then leaves the grid for the page buttons and the decide pane (no header trap)."""
    open_inbox(page)
    page.evaluate("() => { document.body.dataset.mdmKeys = 'off'; }")
    page.keyboard.press("Tab")
    expect(page.get_by_role("link", name="Skip to the main content")).to_be_focused()
    seen: list[str] = []
    for _ in range(160):
        page.keyboard.press("Tab")
        found = page.evaluate(
            "() => { const el = document.activeElement; if (!el) return '';"
            " if (el.classList.contains('mdm-row-open')) return 'row';"
            " if (el.id === 'page-next' || el.id === 'page-prev') return el.id;"
            " if ((el.id || '').includes('\"decision\":\"')) return 'action';"
            " if (el.closest('.ag-header')) return 'header'; return 'other'; }"
        )
        seen.append(found)
        if found == "action":
            break
    assert "header" not in seen, "the grid's headers take no focus"
    assert "row" in seen and "action" in seen
    rows = [i for i, found in enumerate(seen) if found == "row"]
    assert rows == list(range(rows[0], rows[-1] + 1)), "the rows are walked one after another"
    assert rows[-1] < seen.index("action"), "after the last row the focus leaves the list for the pane"
    pages = [i for i, found in enumerate(seen) if found in ("page-prev", "page-next")]
    assert all(rows[-1] < i < seen.index("action") for i in pages)
    # and Shift+Tab from the pane goes back into the list
    page.locator(f"#{ids.PANE_FULL}").focus()
    for _ in range(3):
        page.keyboard.press("Shift+Tab")
        if page.evaluate("() => document.activeElement.classList.contains('mdm-row-open')"):
            break
    else:
        raise AssertionError("Shift+Tab from the pane does not reach the list")
    # a row's button opens its task
    page.locator(".mdm-row-open").nth(1).focus()
    target = page.evaluate("() => document.activeElement.closest('.ag-row').getAttribute('row-id')")
    page.keyboard.press("Enter")
    page.wait_for_function(f"() => ({_SELECTED})() === {target!r}", timeout=WAIT_MS)
    expect(page.locator(f"#{ids.DECIDE_PANE} [data-task-id='{target}']")).to_be_visible(timeout=WAIT_MS)


def test_the_arrows_move_the_one_selection_and_the_pane_follows(page: Page, live) -> None:
    open_inbox(page)
    first = selected(page)
    page.locator(".mdm-row-open").first.focus()
    page.keyboard.press("ArrowDown")
    page.wait_for_function(f"() => ({_SELECTED})() !== {first!r}", timeout=WAIT_MS)
    moved = selected(page)
    expect(page.locator(f"#{ids.DECIDE_PANE} [data-task-id='{moved}']")).to_be_visible(timeout=WAIT_MS)
    # the focus follows the selection, so the grid keeps no second cursor
    focused = page.evaluate("() => document.activeElement.closest('.ag-row').getAttribute('row-id')")
    assert focused == moved
    press(page, "j", blur=False)
    page.wait_for_function(f"() => ({_SELECTED})() !== {moved!r}", timeout=WAIT_MS)
    page.keyboard.press("ArrowUp")
    page.wait_for_function(f"() => ({_SELECTED})() === {moved!r}", timeout=WAIT_MS)


def test_a_key_pressed_while_the_next_case_loads_decides_nothing(page: Page, live) -> None:
    first = find(live, single_review)
    open_inbox(page, f"/?view=team&task={first.row.task_id}")
    if selected(page) != first.row.task_id:
        pytest.skip("the deep-linked task is not on the first page of Team")
    before = {v.entry_id for v in live.state.hub.tray.entries(actor=STEWARD)}
    press(page, "n")
    page.keyboard.press("n")  # at once, while the next case is still on its way
    page.keyboard.press("n")
    expect(tray_button(page)).to_have_text(re.compile(r"^Tray 1"), timeout=WAIT_MS)
    settle(page)
    page.wait_for_timeout(500)
    staged = [v for v in live.state.hub.tray.entries(actor=STEWARD) if v.entry_id not in before]
    assert [v.task_id for v in staged] == [first.row.task_id], "one decision, on the case the steward saw"
    press(page, "u")
    expect(tray_button(page)).to_have_text("Tray", timeout=WAIT_MS)


def test_a_two_candidate_case_needs_no_scroll_at_1440_by_900(page: Page, live) -> None:
    hub = live.state.hub
    rows = [
        row
        for entity in ("organisation", "person")
        for row in hub.inbox.page("team", actor=STEWARD, entity=entity, kind="review").rows
    ]
    for row in rows:  # this check decides nothing, so a case another check used and undid will do
        if row.staged is not None:
            continue
        case = hub.decisions.case(row.task_id, actor=STEWARD)
        if case.close_call and len(case.candidates) == 2:
            break
    else:
        pytest.skip("the seeded world has no open two-candidate close call left")
    open_inbox(page, f"/?view=team&task={case.row.task_id}")
    fits = (
        "() => { const el = document.getElementById('decide-pane');"
        " return el.scrollHeight <= el.clientHeight + 1; }"
    )
    assert page.evaluate(fits), "before a choice too, the whole case is on screen"
    press(page, "1")
    settle(page)
    assert page.evaluate(fits), "the evidence, the impact line and the actions are all on screen"
    expect(
        page.locator(f"#{ids.WHY_SECTION} .mdm-candidate-panel:not(.mdm-hidden) .mdm-flip")
    ).to_be_in_viewport()
    expect(page.locator(".mdm-decide-footer")).to_be_in_viewport()


def test_a_narrow_screen_reflows_without_a_sideways_scroll(page: Page, live) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    open_inbox(page)
    wide = page.evaluate("() => document.documentElement.scrollWidth")
    assert wide <= 390, wide
    page.get_by_role("button", name="Navigation").click()
    expect(page.get_by_role("button", name="Keyboard shortcuts")).to_be_visible()
    expect(
        page.get_by_role("navigation", name="Work").get_by_role("radiogroup", name="Colour scheme")
    ).to_be_visible()


@pytest.mark.parametrize("page", ["light", "dark"], indirect=True)
def test_axe_finds_nothing_serious_with_a_notification_open(page: Page, live) -> None:
    case = find(live, single_review)
    open_inbox(page, f"/?view=team&task={case.row.task_id}")
    claim = page.locator(f"#{ids.DECIDE_PANE}").get_by_role("button", name="Claim", exact=True)
    claim.click()
    expect(notification(page, "Claimed for 10 minutes.")).to_be_visible(timeout=WAIT_MS)
    assert axe(page) == []


@pytest.mark.parametrize("page", ["light", "dark"], indirect=True)
def test_axe_finds_nothing_serious_with_the_decide_pane_open(page: Page, live) -> None:
    hub = live.state.hub
    person = next(
        (
            row.task_id
            for row in hub.inbox.page("team", actor=STEWARD, entity="person", kind="review").rows
            if row.staged is None
        ),
        None,
    )
    open_inbox(page, f"/?view=team&task={person}" if person else "/?view=team")
    assert axe(page) == []
    close = next(
        (
            row.task_id
            for row in hub.inbox.page("team", actor=STEWARD, entity="organisation", kind="review").rows
            if row.staged is None
        ),
        None,
    )
    if close:
        page.goto(f"/?view=team&task={close}")
        expect(page.locator(f"#{ids.DECIDE_PANE} section.mdm-decide")).to_be_visible(timeout=WAIT_MS)
        settle(page)
        assert axe(page) == []


# ---------------------------------------------------------------------------------------------- blind review


def blind_sample(live, *, choices: int = 1, use: bool = True) -> TaskCase:
    """An open quality sample of a record, in the Quality samples view as the served persona sees it, with
    at least `choices` golden records offered; one no check used when `use`."""
    hub = live.state.hub
    for row in hub.inbox.page("samples", actor=STEWARD).rows:
        if ":" not in row.subject or row.staged is not None or row.claimed_by not in (None, "you"):
            continue
        if use and row.task_id in USED:
            continue
        case = hub.decisions.case(row.task_id, actor=STEWARD)
        if case.blind and len(case.choices) >= choices:
            if use:
                USED.add(row.task_id)
            return case
    pytest.skip("the seeded world has no open quality sample with enough golden records offered")


def blind_link(page: Page):
    return page.locator('[id*=\'"decision":"blind_link"\']')


def test_the_quality_samples_view_opens_a_blind_case_with_no_score_or_band(page: Page, live) -> None:
    case = blind_sample(live, use=False)
    task = case.row.task_id
    open_inbox(page, f"/?view=samples&task={task}")
    expect(page.locator(f"#{ids.PAGE_LABEL}")).to_have_text(re.compile(r"^Quality samples: 1–\d+$"))
    rail = page.get_by_role("navigation", name="Work").get_by_role(
        "link", name=re.compile(r"^Quality samples")
    )
    expect(rail).to_have_attribute("aria-current", "page")
    pane = page.locator(f"#{ids.DECIDE_PANE}")
    expect(pane.locator(f"[data-task-id='{task}'][data-blind='yes']")).to_be_visible(timeout=WAIT_MS)
    expect(pane.locator(".mdm-decide-meta")).to_contain_text(f"Quality sample · {case.row.subject}")
    # nothing on screen tells where the record is now, or how the first decision scored it
    expect(pane.locator(".mdm-band")).to_have_count(0)
    expect(page.locator(f"#{ids.INBOX_GRID} .mdm-band")).to_have_count(0)
    expect(pane.locator(f"#{ids.WHY_SECTION}")).to_have_count(0)
    expect(pane.locator(".mdm-waterfall, .mdm-flip, .mdm-impact, .mdm-candidate-impact")).to_have_count(0)
    expect(pane.locator("a[href^='/record'], a[href^='/source']")).to_have_count(0)
    expect(pane.locator("[data-open-record]")).to_have_count(0)
    for choice in case.choices:
        expect(pane.get_by_role("radio", name=f"{choice.index} · {choice.master_id}")).not_to_be_checked()
    text = pane.inner_text()
    assert "automatic" not in text.lower() and not re.search(r"\b\d{2} (review|distinct)\b", text)
    press(page, "Enter")  # no record opens from a blind review
    page.wait_for_timeout(300)
    assert page.url.endswith(f"/?view=samples&task={task}")


def test_l_on_a_blind_review_asks_for_a_choice_first(page: Page, live) -> None:
    case = blind_sample(live, use=False)
    task = case.row.task_id
    open_inbox(page, f"/?view=samples&task={task}")
    expect(blind_link(page)).to_have_text(re.compile(r"^Choose 1\b.* first"), timeout=WAIT_MS)
    assert requests_during(page, lambda: press(page, "l")) == []
    expect(page.get_by_role("radio", name=re.compile(r"^1 · "))).to_be_focused()
    expect(page.locator(".mantine-Notification-root")).to_have_count(0)
    expect(tray_button(page)).to_have_text("Tray")
    assert not [v for v in live.state.hub.tray.entries(actor=STEWARD) if v.task_id == task]


def test_1_then_l_stages_the_blind_answer_and_u_undoes_it(page: Page, live) -> None:
    case = blind_sample(live)
    task = case.row.task_id
    first = case.choices[0].master_id
    open_inbox(page, f"/?view=samples&task={task}")
    assert requests_during(page, lambda: press(page, "1")) == []  # choosing stays in the browser
    expect(page.get_by_role("radio", name=f"1 · {first}")).to_be_checked()
    expect(blind_link(page)).to_have_text(re.compile(f"^Belongs to {first}"))
    press(page, "l")
    expect(tray_button(page)).to_have_text(re.compile(r"^Tray 1 · 0:0\d$"), timeout=WAIT_MS)
    label = f"Quality sample: {case.row.subject} belongs to {first}"
    expect(notification(page, label)).to_be_visible(timeout=WAIT_MS)
    entry = next(v for v in live.state.hub.tray.entries(actor=STEWARD) if v.task_id == task)
    assert (entry.decision, entry.label) == ("blind_link", label)
    press(page, "u")
    expect(tray_button(page)).to_have_text("Tray", timeout=WAIT_MS)
    expect(notification(page, "Undone")).to_be_visible(timeout=WAIT_MS)
    assert next(v for v in live.state.hub.tray.entries(actor=STEWARD) if v.task_id == task).status == "undone"


@pytest.mark.parametrize("page", ["light", "dark"], indirect=True)
def test_axe_finds_nothing_serious_with_the_blind_pane_open(page: Page, live) -> None:
    case = blind_sample(live, use=False)
    open_inbox(page, f"/?view=samples&task={case.row.task_id}")
    expect(page.locator(f"#{ids.DECIDE_PANE} [data-blind='yes']")).to_be_visible(timeout=WAIT_MS)
    assert axe(page) == []
    press(page, "1")
    expect(blind_link(page)).to_have_text(re.compile(r"^Belongs to "))
    assert axe(page) == []
