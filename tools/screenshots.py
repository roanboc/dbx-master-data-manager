"""The README's screenshots of the steward workbench, from the invented demo world with the stub assistant.

    make screenshots        # uv run --group gui python tools/screenshots.py

It seeds a temporary DuckDB store with the demo world (`tools/workbench_live.seed`: seed 7, 2,000 persons
and 500 organisations with hard cases, creations first and then the later events, the default share drawn
for blind review and no breaker tripped), picks an Organisation close call, a value of its first candidate
that has runners-up and a quality sample, and, for each colour scheme, serves
`mdm ui` on a fresh copy of the store as the persona data_steward with a ten-minute undo window (so a
staged decision holds still, and each scheme starts from the same world). It captures into
`docs/screenshots/`, at 1440 × 900, in the light and the dark colour scheme:

- `inbox-<scheme>.png`: the close call selected, candidate 1 chosen, the pane with its waterfall, what
  would flip it, the values that change and the impact line;
- `tray-<scheme>.png`: after L, the tray open with the staged decision and its countdown (undone after);
- `record-<scheme>.png`: the candidate's golden record, with the Why open under that value;
- `sample-<scheme>.png`: the Quality samples view with a blind case open and choice 1 selected, not
  staged (an Organisation sample with two golden records offered or more, when the world has one).

Organisation data is not personal, so the pictures show invented names; Person values stay masked. It
refuses to run with a platform variable or a backend other than DuckDB, and it never touches the local
store under `.mdm/`.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # `python tools/screenshots.py` puts tools/ first, not the repository
    sys.path.insert(0, str(ROOT))

from tools.workbench_live import _PLATFORM, MODELS, free_port, launch_chromium, seed, serve  # noqa: E402

OUT = ROOT / "docs" / "screenshots"
VIEWPORT = {"width": 1440, "height": 900}
SCHEMES = ("light", "dark")
PARTS = ("inbox", "tray", "record", "sample")
#: the world the pictures show (as `make demo` lands it)
WORLD = {"persons": 2000, "organisations": 500, "hard_cases": 0.03, "updates": 0.1, "deletes": 0.01}
#: a staged decision waits this long, so the tray holds still while it is captured
UNDO_SECONDS = 600
WAIT_MS = 30_000
#: counts the callback requests in flight, so a picture is taken only once every callback has answered
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


@dataclass(frozen=True)
class Scene:
    task_id: str  # an Organisation close call
    candidate: str  # its first candidate's master ID
    attribute: str  # a golden value of the candidate with runners-up
    label: str  # that attribute's label, as the Why names it
    sample: str  # a quality sample of a record, decided blind


def refuse_unsafe_environment() -> None:
    """Stops unless this is a local run on DuckDB: no platform variable, no other backend."""
    present = [name for name in _PLATFORM if name in os.environ]
    if present:
        raise SystemExit("screenshots: refused, a platform variable is set; run this on a workstation only")
    if os.environ.get("MDM_BACKEND", "duckdb").lower() != "duckdb":
        raise SystemExit("screenshots: refused, the pictures are taken on a DuckDB file only")


def find_sample(hub, steward) -> str:
    """A quality sample of a record for the picture: an Organisation one with the most golden records
    offered (up to three), else one of any entity with at least one."""
    best: tuple[int, str] | None = None
    for row in hub.inbox.page("samples", actor=steward).rows:
        if ":" not in row.subject or row.staged is not None:
            continue
        case = hub.decisions.case(row.task_id, actor=steward)
        if not case.blind or not case.choices:
            continue
        rank = len(case.choices) + (10 if row.entity == "organisation" else 0)
        if best is None or rank > best[0]:
            best = (rank, row.task_id)
    if best is None:
        raise SystemExit("screenshots: the seeded world has no quality sample with a golden record offered")
    return best[1]


def find_scene(path: Path) -> Scene:
    """The close call, the value and the quality sample the pictures show, read through the services before
    serving (the DuckDB file takes one process at a time)."""
    from mdm.config import Settings
    from mdm.models.authority import Actor
    from mdm.services.context import Hub

    steward = Actor("persona:data_steward", "person", "data_steward", persona=True)
    settings = Settings(
        duckdb_path=str(path), models_dir=str(MODELS), role="data_steward", agent_provider="stub"
    )
    hub = Hub.open(settings)
    fallback: Scene | None = None
    try:
        sample = find_sample(hub, steward)
        for row in hub.inbox.page("team", actor=steward, entity="organisation", kind="review").rows:
            case = hub.decisions.case(row.task_id, actor=steward)
            if case.shape != "source" or not case.close_call or len(case.candidates) < 2:
                continue
            candidate = case.candidates[0].master_id
            for value in hub.lookup.golden("organisation", candidate, actor=steward):
                why = hub.lookup.why("organisation", candidate, value.attribute, actor=steward)
                if not why.runners_up:
                    continue
                scene = Scene(row.task_id, candidate, value.attribute, value.label, sample)
                if any(r.value != why.winner.value for r in why.runners_up):
                    return scene  # a runner-up that lost with another value explains the most
                fallback = fallback or scene
    finally:
        hub.close()
    if fallback is not None:
        return fallback
    raise SystemExit("screenshots: the seeded world has no Organisation close call with a runner-up")


def settle(page) -> None:
    """Waits until no callback request is in flight, twice over a short pause."""
    for _ in range(2):
        page.wait_for_function("() => window.mdmPending === 0", timeout=WAIT_MS)
        page.wait_for_timeout(250)


def press(page, key: str) -> None:
    """A key as a steward presses it on the inbox: nothing focused, so the shortcuts do not yield."""
    page.evaluate("() => { const el = document.activeElement; if (el && el !== document.body) el.blur(); }")
    page.keyboard.press(key)


def _context(browser, base_url: str, scheme: str):
    context = browser.new_context(
        base_url=base_url,
        viewport=VIEWPORT,
        color_scheme=scheme,
        reduced_motion="reduce",
        locale="en-GB",
        timezone_id="UTC",
    )
    page = context.new_page()
    page.set_default_timeout(WAIT_MS)
    page.add_init_script(_COUNT_REQUESTS)
    return context, page


def _open_close_call(page, scene: Scene) -> None:
    """The inbox on the close call, with candidate 1 chosen."""
    from playwright.sync_api import expect

    page.goto(f"/?view=team&task={scene.task_id}")
    expect(page.get_by_role("radiogroup", name="Choose the candidate to link")).to_be_visible()
    settle(page)
    press(page, "1")
    expect(page.get_by_role("radio", name=re.compile(r"^1 · "))).to_be_checked()
    settle(page)


def capture(browser, base_url: str, scene: Scene, scheme: str, part: str) -> Path:
    """One picture, `part` one of PARTS, in one colour scheme. The tray's staged decision is undone
    afterwards, so the record shows the world the inbox showed."""
    from playwright.sync_api import expect

    context, page = _context(browser, base_url, scheme)
    shot = OUT / f"{part}-{scheme}.png"
    try:
        if part == "inbox":
            _open_close_call(page, scene)
            page.screenshot(path=str(shot))
        elif part == "tray":
            _open_close_call(page, scene)
            press(page, "l")
            tray = page.locator("#tray-button")
            expect(tray).to_have_text(re.compile(r"^Tray 1 · \d+:\d\d$"))
            settle(page)
            tray.click()
            undo = page.locator("#tray-list").get_by_role("button", name=re.compile(r"^Undo"))
            expect(undo).to_be_visible()
            page.wait_for_timeout(1200)  # the countdown has ticked at least once
            page.screenshot(path=str(shot))
            undo.first.click()
            expect(tray).to_have_text("Tray")
            page.keyboard.press("Escape")
            settle(page)
        elif part == "sample":
            page.goto(f"/?view=samples&task={scene.sample}")
            expect(
                page.get_by_role("radiogroup", name="Choose the golden record it belongs to")
            ).to_be_visible()
            settle(page)
            press(page, "1")
            expect(page.get_by_role("radio", name=re.compile(r"^1 · "))).to_be_checked()
            settle(page)
            page.screenshot(path=str(shot))
        else:
            page.goto(f"/record/{scene.candidate}")
            name = re.compile(rf", why this {re.escape(scene.label.lower())}\?$")
            chip = page.get_by_role("button", name=name)
            expect(chip).to_be_visible()
            settle(page)
            chip.click()
            expect(chip).to_have_attribute("aria-expanded", "true")
            expect(page.locator(".mdm-why-row:not(.mdm-hidden) .mdm-why-title")).to_be_visible()
            settle(page)
            page.screenshot(path=str(shot))
    finally:
        context.close()
    return shot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Take the README's screenshots of the workbench.")
    parser.add_argument("--port", type=int, default=0, help="Default: a free port.")
    args = parser.parse_args(argv)
    refuse_unsafe_environment()
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mdm-shots-") as folder:
        path = Path(folder) / "mdm.duckdb"
        print("seeding the invented demo world …", flush=True)
        seed(path, seed=7, **WORLD)
        scene = find_scene(path)
        print(
            f"close call {scene.task_id}, candidate {scene.candidate}, value {scene.attribute}, "
            f"sample {scene.sample}",
            flush=True,
        )
        env = {"MDM_UNDO_SECONDS": str(UNDO_SECONDS)}
        with sync_playwright() as playwright:
            browser = launch_chromium(playwright, headless=True)
            try:
                for scheme in SCHEMES:
                    copy = Path(folder) / f"mdm-{scheme}.duckdb"
                    shutil.copyfile(path, copy)  # each scheme starts from the seeded world, tray empty
                    with serve(copy, port=args.port or free_port(), env=env) as base_url:
                        for part in PARTS:  # the inbox before the tray's decision claims the task
                            shot = capture(browser, base_url, scene, scheme, part)
                            print(f"wrote {shot.relative_to(ROOT)}", flush=True)
            finally:
                browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
