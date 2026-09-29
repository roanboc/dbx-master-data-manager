"""Seed a demo store, serve the workbench over it, and launch Chromium: for the browser checks and the
screenshots, and by hand.

Only the invented demo world (`mdm demo land`), only on a DuckDB file this module is handed, never the
local store under `.mdm/`, and always with the stub assistant and no platform variable. The command
line runs as subprocesses (`python -m mdm …`), exactly as `make demo` runs it: creations and hard cases
first, then the later events, so stewards get reviews, close calls, held updates and orphans, and a share
of the matcher's decisions is drawn for blind review (quality samples). `trip_breaker` pauses an entity's
automatic linking with the breaker's demo trip, on the local store only, for the checks of its notice;
`largest_group` names an entity's largest group of alike reviews, and `withdraw_bulk` withdraws its bulk
decisions with the breaker's demo withdrawal (story 3.3).

    uv run --group gui python tools/workbench_live.py            # seed a temporary store and serve it
    uv run --group gui python tools/workbench_live.py --browser  # … and open Chromium on it

`serve` runs `mdm ui` in a process of its own (the real entry point); `serve_in_thread` builds the app in
this process and serves it from a thread, so a test can also read the hub it serves.
"""

from __future__ import annotations

import argparse
import contextlib
import glob
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
#: the real local store: never seeded, served or overwritten from here
LOCAL_STORE = ROOT / ".mdm"
#: variables a Databricks App or runtime sets (mdm.config.PLATFORM_VARIABLES); none reaches the workbench
_PLATFORM = ("DATABRICKS_APP_NAME", "DATABRICKS_APP_PORT", "DATABRICKS_RUNTIME_VERSION")
#: seconds to wait for the workbench to answer
START_TIMEOUT = 60.0
#: the world the browser checks use (plan B.9.3); the screenshots take a larger one
WORLD = {"persons": 300, "organisations": 100, "hard_cases": 0.05}
#: the share of decisions drawn for blind review: the default, named so the checks' samples never move with it
SAMPLE_SHARE = 0.02
#: what the breaker's demo trip says: blind review confirmed 30 of the last 40 automatic links
TRIP_FIGURES = {"agreed": 30, "reviewed": 40, "threshold": 0.95, "window": 100}


@dataclass(frozen=True)
class LiveApp:
    """A workbench served from a thread of this process."""

    base_url: str
    app: Any  # dash.Dash
    state: Any  # mdm.ui.context.UiState: its hub is the one served


def clean_env(**extra: str) -> dict[str, str]:
    """This process's environment without any `MDM_`, `PG` or platform variable, plus `extra`."""
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("MDM_", "PG")) and name not in _PLATFORM
    }
    env.update(extra)
    return env


def _checked(path: Path) -> Path:
    path = Path(path).resolve()
    if path == LOCAL_STORE or LOCAL_STORE in path.parents:
        raise ValueError("refusing the local store under .mdm/; hand a file of its own")
    return path


def mdm(path: Path, *args: str, env: Mapping[str, str] | None = None, timeout: float = 600.0) -> str:
    """Runs `python -m mdm ARGS` on the DuckDB file `path` with the stub assistant; its standard output.
    A failure raises RuntimeError with the command line's last lines (codes and counts only)."""
    run_env = clean_env(MDM_DUCKDB_PATH=str(_checked(path)), MDM_AGENT_PROVIDER="stub", **dict(env or {}))
    done = subprocess.run(
        [sys.executable, "-m", "mdm", *args],
        cwd=ROOT,
        env=run_env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if done.returncode != 0:
        tail = "\n".join((done.stdout + done.stderr).splitlines()[-8:])
        raise RuntimeError(f"mdm {' '.join(args)} exited {done.returncode}:\n{tail}")
    return done.stdout


def seed(
    path: Path,
    *,
    persons: int = WORLD["persons"],
    organisations: int = WORLD["organisations"],
    hard_cases: float = WORLD["hard_cases"],
    seed: int = 7,
    updates: float = 0.3,
    deletes: float = 0.03,
    share: float | None = None,
    trip: str | None = None,
) -> None:
    """A fresh store at `path` with the invented world: creations and hard cases, arrival, then the later
    events, arrival again. `share` is the share drawn for blind review (`MDM_SAMPLE_SHARE`; the default when
    None); `trip` names an entity whose automatic linking the breaker then pauses (`trip_breaker`)."""
    world = ("--persons", str(persons), "--organisations", str(organisations), "--seed", str(seed))
    cases = ("--hard-cases", str(hard_cases))
    env = {"MDM_SAMPLE_SHARE": str(share)} if share is not None else {}
    mdm(path, "demo", "reset", "--yes", env=env)
    mdm(path, "init", "--models", str(MODELS), env=env)
    mdm(path, "demo", "land", *world, *cases, "--creations-only", env=env)
    mdm(path, "arrive", env=env)
    mdm(path, "demo", "land", *world, *cases, "--updates", str(updates), "--deletes", str(deletes), env=env)
    mdm(path, "arrive", env=env)
    if trip is not None:
        trip_breaker(path, trip)


def trip_breaker(path: Path, entity: str, *, figures: Mapping[str, Any] | None = None) -> None:
    """Pauses `entity`'s automatic linking on the DuckDB file `path` with the breaker's demo trip (local
    stores only; the audit marks it `demo`): a hub opened in this process, then closed."""
    from mdm.config import Settings
    from mdm.services.context import Hub

    settings = Settings(duckdb_path=str(_checked(path)), models_dir=str(MODELS), agent_provider="stub")
    with Hub.open(settings, as_role="data_owner") as hub:
        hub.breaker.demo_trip(entity, figures=dict(figures or TRIP_FIGURES))


def largest_group(path: Path, entity: str) -> str:
    """The key (`SIG-…`) of the largest signature group of `entity`'s open reviews on the DuckDB file `path`,
    as the Alike reviews page lists it first (story 3.3): a hub opened in this process, then closed."""
    from mdm.config import Settings
    from mdm.services.context import Hub

    settings = Settings(duckdb_path=str(_checked(path)), models_dir=str(MODELS), agent_provider="stub")
    with Hub.open(settings, as_role="data_steward") as hub:
        found = hub.batches.groups(actor=hub.actor, entity=entity).groups
    if not found:
        raise RuntimeError(f"no two open {entity} reviews share a pattern")
    return found[0].group_key


def withdraw_bulk(path: Path, entity: str, *, figures: Mapping[str, Any] | None = None) -> str:
    """Withdraws the bulk decisions of `entity`'s largest signature group on the DuckDB file `path`, as the
    breaker's demo withdrawal does (local stores only; the audit marks it `demo`), and returns the group's
    key: a hub opened in this process as a data owner, then closed."""
    from mdm.config import Settings
    from mdm.services.context import Hub

    group = largest_group(path, entity)
    settings = Settings(duckdb_path=str(_checked(path)), models_dir=str(MODELS), agent_provider="stub")
    with Hub.open(settings, as_role="data_owner") as hub:
        hub.breaker.demo_withdraw(entity, group, figures=dict(figures) if figures is not None else None)
    return group


def copy_store(source: Path, target: Path) -> Path:
    """A copy of a closed DuckDB file (and its write-ahead log, when one was left), so each test module
    gets a store of its own from one seeded template."""
    target = _checked(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    wal = Path(f"{source}.wal")
    if wal.exists():
        shutil.copyfile(wal, Path(f"{target}.wal"))
    return target


def free_port() -> int:
    """A TCP port free on the loopback address now."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def wait_until_up(base_url: str, *, timeout: float = START_TIMEOUT, alive: Any = None) -> None:
    """Waits until `GET /` answers 200; `alive()` returning False (a process that exited) stops early."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if alive is not None and not alive():
            raise RuntimeError("the workbench stopped before it answered")
        try:
            with urllib.request.urlopen(base_url + "/", timeout=2) as answer:  # loopback only
                if answer.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(0.25)
    raise RuntimeError(f"the workbench did not answer within {timeout:.0f} s")


@contextlib.contextmanager
def serve(path: Path, *, port: int, env: Mapping[str, str] | None = None) -> Iterator[str]:
    """`mdm ui --port PORT` on `path` in a process group of its own, as the persona data_steward with the
    stub assistant unless `env` says otherwise; yields the base URL and stops the group afterwards."""
    run_env = clean_env(
        MDM_DUCKDB_PATH=str(_checked(path)),
        MDM_ROLE="data_steward",
        MDM_AGENT_PROVIDER="stub",
        **dict(env or {}),
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "mdm", "ui", "--port", str(port)],
        cwd=ROOT,
        env=run_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # its own group, so nothing outlives the run
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        wait_until_up(base_url, alive=lambda: process.poll() is None)
        yield base_url
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=15)


@contextlib.contextmanager
def serve_in_thread(settings: Any, *, port: int) -> Iterator[LiveApp]:
    """The workbench for `settings` (an `mdm.config.Settings` on a local DuckDB file), built in this
    process and served from a thread on `127.0.0.1:PORT`; yields it, then stops the server and closes the
    app's state (its tray worker, its prefetch thread and its hub)."""
    from werkzeug.serving import make_server

    from mdm.ui.app import create_app
    from mdm.ui.context import EXTENSION

    if not settings.local_mode or settings.backend != "duckdb":
        raise ValueError("the live workbench serves a local DuckDB store only")
    _checked(Path(settings.duckdb_path))
    app = create_app(settings, listen=("127.0.0.1", port))
    state = app.server.extensions[EXTENSION]
    try:
        server = make_server("127.0.0.1", port, app.server, threaded=True)
        thread = threading.Thread(target=server.serve_forever, name="mdm-live-workbench", daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{port}"
        try:
            wait_until_up(base_url, alive=thread.is_alive)
            yield LiveApp(base_url=base_url, app=app, state=state)
        finally:
            server.shutdown()
            thread.join(timeout=15)
    finally:
        state.close()


def _installed_chromium() -> str | None:
    """The newest Chromium under PLAYWRIGHT_BROWSERS_PATH, for a driver that looks for another build."""
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if not root:
        return None
    found = sorted(glob.glob(os.path.join(root, "chromium-*", "chrome-linux", "chrome")))
    return found[-1] if found else None


def launch_chromium(playwright: Any, **options: Any) -> Any:
    """`playwright.chromium.launch(**options)` (headless by default); when the driver finds no browser of
    its own build, the Chromium installed under PLAYWRIGHT_BROWSERS_PATH. Never installs one."""
    try:
        return playwright.chromium.launch(**options)
    except Exception as error:  # playwright.sync_api.Error, imported with the driver only
        executable = _installed_chromium()
        if executable is None or "Executable doesn't exist" not in str(error):
            raise
        return playwright.chromium.launch(executable_path=executable, **options)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed a temporary demo store and serve the workbench on it.")
    parser.add_argument("--persons", type=int, default=WORLD["persons"])
    parser.add_argument("--organisations", type=int, default=WORLD["organisations"])
    parser.add_argument("--hard-cases", type=float, default=WORLD["hard_cases"])
    parser.add_argument("--port", type=int, default=0, help="Default: a free port.")
    parser.add_argument("--browser", action="store_true", help="Open Chromium on the workbench.")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="mdm-live-") as folder:
        path = Path(folder) / "mdm.duckdb"
        print("seeding the invented demo world …", flush=True)
        seed(path, persons=args.persons, organisations=args.organisations, hard_cases=args.hard_cases)
        with serve(path, port=args.port or free_port()) as base_url:
            print(f"the workbench is on {base_url}; Ctrl-C stops it", flush=True)
            try:
                if args.browser:
                    from playwright.sync_api import sync_playwright

                    with sync_playwright() as playwright:
                        browser = launch_chromium(playwright, headless=False)
                        browser.new_page().goto(base_url)
                        while browser.is_connected():
                            time.sleep(0.5)
                else:
                    while True:
                        time.sleep(1)
            except KeyboardInterrupt:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
