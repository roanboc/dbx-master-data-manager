"""The browser checks' fixtures: one seeded store template per run, one workbench per test module, one
Chromium per run, and a fresh page per check.

The template is the invented demo world (`tools/workbench_live.seed`: creations and hard cases, arrival,
then the later events, arrival), seeded once through the command line and copied for each module, since
the DuckDB file takes one writer and modules must not share state. Each module's workbench is built in
this process and served from a thread (`serve_in_thread`), as the persona data_steward with the stub
assistant, a 4-second undo window and the tray worker on, so a check can also read the hub it serves
(`live.state.hub`). Chromium is the one installed under PLAYWRIGHT_BROWSERS_PATH, headless; this
machine never runs `playwright install` (CI's browser job installs its own).

Every test in `tests/ui/` is marked `gui`: `make test` leaves them out, `make test-gui` runs them. When
Playwright is not installed (`uv sync` without `--group gui`), nothing here is even imported.
"""

from __future__ import annotations

import importlib.util
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

UI_DIR = Path(__file__).resolve().parent
ROOT = UI_DIR.parents[1]
#: screenshots of a failed check (the invented demo world only); gitignored
TESTRUN = ROOT / ".testrun"
VIEWPORT = {"width": 1440, "height": 900}

if (
    importlib.util.find_spec("playwright") is None
    or importlib.util.find_spec("axe_playwright_python") is None
):
    # Deselecting by marker is not enough: pytest imports a module before it reads its markers.
    collect_ignore_glob = ["test_*.py"]
else:
    from tools.workbench_live import (
        SAMPLE_SHARE,
        LiveApp,
        copy_store,
        free_port,
        launch_chromium,
        seed,
        serve_in_thread,
    )

    from mdm.config import Settings
    from tests.ui.harness import WAIT_MS, console_errors

    @pytest.fixture(scope="session")
    def store_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
        """The invented demo world, seeded once per run through the command line, with the default share
        drawn for blind review named, so the quality samples a check meets are the same every run. No
        breaker is tripped here: `test_breaker.py` trips one on a copy of its own."""
        path = tmp_path_factory.mktemp("workbench-template") / "mdm.duckdb"
        seed(path, share=SAMPLE_SHARE)
        return path

    def workbench_settings(path: Path, **changes) -> Settings:
        """The settings a module's workbench runs with: a local DuckDB file, the persona data_steward, the
        stub assistant, a 4-second undo window, the tray worker on."""
        values = {
            "duckdb_path": str(path),
            "models_dir": str(ROOT / "models"),
            "role": "data_steward",
            "agent_provider": "stub",
            "undo_seconds": 4,
            "tray_worker": "on",
            **changes,
        }
        return Settings(**values)

    @pytest.fixture(scope="module")
    def live(store_template: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[LiveApp]:
        """This module's workbench over its own copy of the template, served from a thread."""
        path = copy_store(store_template, tmp_path_factory.mktemp("workbench") / "mdm.duckdb")
        with serve_in_thread(workbench_settings(path), port=free_port()) as served:
            yield served

    @pytest.fixture(scope="session")
    def browser() -> Iterator:
        """One headless Chromium for the run."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            chromium = launch_chromium(playwright, headless=True)
            try:
                yield chromium
            finally:
                chromium.close()

    @pytest.fixture
    def page(browser, live: LiveApp, request: pytest.FixtureRequest) -> Iterator:
        """A fresh page on the module's workbench, 1440 × 900, in the colour scheme the check asks for
        (`@pytest.mark.parametrize("page", ["dark"], indirect=True)`; light by default). The check fails
        when the page logged a console error; a failed check leaves a screenshot in `.testrun/`."""
        scheme = getattr(request, "param", "light")
        context = browser.new_context(
            base_url=live.base_url,
            viewport=VIEWPORT,
            color_scheme=scheme,
            reduced_motion="reduce",
            locale="en-GB",
            timezone_id="UTC",
        )
        opened = context.new_page()
        opened.set_default_timeout(WAIT_MS)
        errors = console_errors(opened)
        try:
            yield opened
            report = getattr(request.node, "rep_call", None)
            if report is not None and report.failed:
                TESTRUN.mkdir(exist_ok=True)
                name = re.sub(r"[^A-Za-z0-9_.-]+", "-", request.node.nodeid)[-120:]
                opened.screenshot(path=str(TESTRUN / f"{name}.png"), full_page=True)
            assert errors == [], f"the page logged {len(errors)} console error(s)"
        finally:
            context.close()


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Marks every test under `tests/ui/` `gui`, before `-m` selects."""
    for item in items:
        if UI_DIR in Path(str(item.path)).resolve().parents:
            item.add_marker(pytest.mark.gui)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    """Keeps each phase's report on the item, so the `page` fixture knows whether its check failed."""
    report = yield
    setattr(item, f"rep_{report.when}", report)
    return report
