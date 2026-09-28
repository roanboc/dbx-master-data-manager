"""What the browser checks share: opening the inbox and waiting for its callbacks, keys, axe, and what the
browser keeps.

Waits are locator expectations with a timeout of `WAIT_MS` or more
(`expect(locator).to_have_text(…, timeout=WAIT_MS)`), never a network-idle wait: the tray's poll keeps
the network busy while a decision is staged. Nothing here prints a value from the page: axe's findings
carry rule IDs, impacts and selectors, never the offending element's HTML.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import Page, Request, expect

from mdm.ui import ids

#: the least a browser check waits for anything the server does
WAIT_MS = 20_000
#: the success criteria axe checks: WCAG 2.0, 2.1 and 2.2, levels A and AA
AXE_TAGS = ("wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa")
#: the impacts that fail a check
AXE_FAILING = ("serious", "critical")
#: (rule ID, CSS selector) pairs axe may report for AG Grid internals that cannot be fixed from outside
#: the grid, each with a comment naming the internal and why. It starts empty, and an entry never names a
#: rule alone.
AXE_ALLOWED: tuple[tuple[str, str], ...] = ()
#: the path of every Dash callback request
UPDATE_PATH = "/_dash-update-component"

_axe = Axe()

#: counts the callback requests in flight, so a check leaves a page only once they have answered (a
#: request cut off by a navigation is logged as a console error by Dash); once `mdmLeaving` is set, a
#: callback request is held back rather than sent, since the polls never stop
_COUNT_REQUESTS = """(() => {
    const original = window.fetch;
    window.mdmPending = 0;
    window.fetch = function (...args) {
        const target = String((args[0] && args[0].url) || args[0]);
        const counted = target.includes("_dash-update-component");
        if (counted && window.mdmLeaving) { return new Promise(() => {}); }
        if (counted) { window.mdmPending += 1; }
        return original.apply(this, args).finally(() => { if (counted) { window.mdmPending -= 1; } });
    };
})();"""
#: true once nothing is in flight, and from then on holds every new callback request: the test and the
#: hold are one step in the page, so no poll can start between them
_HOLD_REQUESTS = "() => { if (window.mdmPending !== 0) return false; window.mdmLeaving = true; return true; }"


def settle(page: Page) -> None:
    """Waits until no callback request is in flight, twice over a short pause."""
    for _ in range(2):
        page.wait_for_function("() => window.mdmPending === 0", timeout=WAIT_MS)
        page.wait_for_timeout(150)


def count_requests(page: Page) -> None:
    """Counts the page's callback requests in flight, from its next navigation on; `settle` and `navigate`
    read the count."""
    page.add_init_script(_COUNT_REQUESTS)


def navigate(page: Page, path: str | None = None) -> None:
    """Reloads, or goes to `path`, once every callback has answered, holding back any request a poll starts
    after that, so the navigation cuts nothing off."""
    settle(page)
    page.wait_for_function(_HOLD_REQUESTS, timeout=WAIT_MS)
    if path is None:
        page.reload()
    else:
        page.goto(path)


def open_inbox(page: Page, path: str = "/?view=team") -> None:
    """Opens the inbox and waits for its decide pane and grid."""
    count_requests(page)
    page.goto(path)
    expect(page.locator(f"#{ids.DECIDE_PANE} section.mdm-decide")).to_be_visible(timeout=WAIT_MS)
    page.wait_for_function(
        "() => { try { return window.dash_ag_grid.getApi('inbox-grid').getDisplayedRowCount() >= 0; }"
        " catch (e) { return false; } }",
        timeout=WAIT_MS,
    )
    settle(page)


def press(page: Page, key: str, *, blur: bool = True) -> None:
    """Presses `key` as a steward would on the inbox; `blur` first takes focus off any control, since the
    shortcuts yield to a focused field, menu, dialog, button or link."""
    if blur:
        page.evaluate(
            "() => { const el = document.activeElement; if (el && el !== document.body) el.blur(); }"
        )
    page.keyboard.press(key)


def axe(page: Page, context: str | None = None) -> list[dict[str, Any]]:
    """The serious and critical violations axe finds on the page (or inside the `context` selector) at
    AXE_TAGS, less AXE_ALLOWED: `{"id", "impact", "help", "targets"}` each, with no HTML."""
    options = {"runOnly": {"type": "tag", "values": list(AXE_TAGS)}, "resultTypes": ["violations"]}
    results = _axe.run(page, context=context, options=options)
    found = []
    for violation in results.response["violations"]:
        if violation.get("impact") not in AXE_FAILING:
            continue
        targets = [
            ", ".join(str(part) for part in node.get("target", [])) for node in violation.get("nodes", [])
        ]
        targets = [t for t in targets if (violation["id"], t) not in AXE_ALLOWED]
        if targets:
            found.append(
                {
                    "id": violation["id"],
                    "impact": violation["impact"],
                    "help": violation.get("help", ""),
                    "targets": targets,
                }
            )
    return found


def storage_dump(page: Page) -> str:
    """Everything the page keeps in `localStorage` and `sessionStorage`, as one JSON text."""
    return page.evaluate(
        """() => {
            const dump = (store) => Object.fromEntries(Array.from({length: store.length}, (_, i) => {
                const key = store.key(i); return [key, store.getItem(key)];
            }));
            return JSON.stringify({local: dump(window.localStorage), session: dump(window.sessionStorage)});
        }"""
    )


def data_attribute_values(page: Page) -> list[str]:
    """The value of every `data-*` attribute on the page."""
    return page.evaluate(
        """() => Array.from(document.querySelectorAll('*')).flatMap(el =>
            Array.from(el.attributes).filter(a => a.name.startsWith('data-')).map(a => a.value))"""
    )


def requests_during(page: Page, action: Callable[[], None], *, settle_ms: int = 600) -> list[dict[str, Any]]:
    """The callback requests (`/_dash-update-component` posts) `action` caused, each as its JSON body,
    counting those that start up to `settle_ms` after it returns."""
    seen: list[Request] = []

    def listen(request: Request) -> None:
        if request.method == "POST" and request.url.split("?", 1)[0].endswith(UPDATE_PATH):
            seen.append(request)

    page.on("request", listen)
    try:
        action()
        page.wait_for_timeout(settle_ms)
    finally:
        page.remove_listener("request", listen)
    return [json.loads(request.post_data or "{}") for request in seen]


def console_errors(page: Page) -> list[str]:
    """Starts collecting the page's console errors and uncaught exceptions; returns the list it fills.
    The `page` fixture does this for every check and fails the check when the list is not empty."""
    errors: list[str] = []
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: errors.append(f"uncaught: {error.name}"))
    return errors
