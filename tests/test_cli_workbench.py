"""The workbench's commands: `mdm ui` and its refusals, `mdm tray flush`, the demo's hard cases and
creations-only landing, and the due times `mdm init` gives (initiative 3, story 3.1).

`mdm ui` never starts a server here: `mdm.ui.server.serve` is replaced by a recorder. Refusals are one
plain sentence on stderr and exit code 1, before anything is opened.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import mdm.cli as cli
import mdm.ui.server as ui_server
from mdm.config import PLATFORM_VARIABLES
from mdm.demo import DemoConfig, generate
from mdm.models.workbench import FlushReport
from mdm.services.inbox import InboxService
from mdm.services.tray import TrayService

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
WORLD = ("--persons", "20", "--organisations", "6", "--seed", "7")
LOOPBACK_ONLY = "The workbench listens on 127.0.0.1 only"
runner = CliRunner()


def _env(path: Path, **extra: str) -> dict[str, str | None]:
    """Only the settings a test names: every MDM_, PG and platform variable of the shell is removed."""
    env: dict[str, str | None] = {
        name: None for name in os.environ if name.startswith(("MDM_", "PG")) or name in PLATFORM_VARIABLES
    }
    env["MDM_DUCKDB_PATH"] = str(path)
    env.update(extra)
    return env


def mdm(env: dict[str, str | None], *args: str, code: int | None = 0):
    result = runner.invoke(cli.app, list(args), env=env)
    if code is not None:
        assert result.exit_code == code, (args, result.exit_code, result.output, result.exception)
    return result


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Every call `mdm ui` makes to `serve`, recorded instead of serving."""
    calls: list[dict[str, Any]] = []

    def record(settings, **kwargs) -> None:
        calls.append({"settings": settings, **kwargs})

    monkeypatch.setattr(ui_server, "serve", record)
    return calls


@pytest.fixture
def initialised(tmp_path: Path) -> dict[str, str | None]:
    env = _env(tmp_path / "mdm.duckdb")
    mdm(env, "init", "--models", str(MODELS))
    return env


# ---------------------------------------------------------------------------------------------- mdm ui


def test_the_workbench_listens_on_loopback_only(tmp_path: Path, served) -> None:
    local = _env(tmp_path / "mdm.duckdb")
    lakebase = _env(tmp_path / "unused.duckdb", MDM_LAKEBASE_ENDPOINT="projects/p/branches/b/endpoints/e")
    for env in (local, lakebase):
        result = mdm(env, "ui", "--host", "0.0.0.0", code=1)
        assert result.stderr.startswith(f"mdm: {LOOPBACK_ONLY}") and result.stderr.count("\n") == 1
    refused = json.loads(mdm(local, "--json", "ui", "--host", "192.0.2.10", code=1).stderr)
    assert refused["error"]["code"] == "workbench_needs_loopback"
    assert served == []
    assert not (tmp_path / "mdm.duckdb").exists(), "a refusal opens nothing"


def test_a_persona_and_the_development_tools_need_a_local_store(tmp_path: Path, served) -> None:
    app = _env(tmp_path / "mdm.duckdb", DATABRICKS_APP_PORT="8000")
    result = mdm({**app, "MDM_ROLE": "data_steward"}, "ui", code=1)
    assert result.stderr == "mdm: MDM_ROLE names a persona, and personas work only on a local store.\n"
    assert "persona_refused" in mdm(app, "--as", "data_steward", "--json", "ui", code=1).stderr
    lakebase = _env(tmp_path / "mdm.duckdb", MDM_LAKEBASE_ENDPOINT="projects/p/branches/b/endpoints/e")
    assert "only on a local store" in mdm(lakebase, "ui", "--dev", code=1).stderr
    assert served == []


def test_a_store_without_the_workbench_tables_is_refused(tmp_path: Path, served) -> None:
    result = mdm(_env(tmp_path / "mdm.duckdb"), "ui", code=1)
    assert (
        result.stderr == "mdm: The store has no workbench tables yet; run `mdm init --models models` first.\n"
    )
    assert served == []


def test_a_store_another_process_holds_is_refused(tmp_path: Path, served) -> None:
    path = tmp_path / "held.duckdb"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import duckdb, sys, time; c = duckdb.connect(sys.argv[1]); print('ready', flush=True); time.sleep(60)",
            str(path),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "ready"
        result = mdm(_env(path), "ui", code=1)
        assert result.stderr == (
            "mdm: The store is in use by another process; stop it, or point MDM_DUCKDB_PATH elsewhere.\n"
        )
    finally:
        holder.kill()
        holder.wait(timeout=30)
    assert served == []


def test_the_workbench_is_served_with_the_port_and_worker_asked_for(
    initialised, served, monkeypatch: pytest.MonkeyPatch
) -> None:
    # the workbench's tables arrive with the store's workbench DDL; this test is about what is passed on
    monkeypatch.setattr(cli, "_workbench_store", lambda state, settings: "DuckDB")
    result = mdm(initialised, "ui", "--port", "8123", "--no-worker")
    assert result.stdout == (
        "the workbench is on http://127.0.0.1:8123 (DuckDB, persona data_steward); Ctrl-C stops it\n"
    )
    (call,) = served
    assert (call["host"], call["port"], call["dev"], call["worker"]) == ("127.0.0.1", 8123, False, False)
    assert call["settings"].local_mode and call["settings"].role == ""
    mdm({**initialised, "MDM_UI_PORT": "8200"}, "--as", "coordinating_steward", "ui", "--worker", "--dev")
    call = served[-1]
    assert (call["port"], call["dev"], call["worker"]) == (8200, True, True)
    assert call["settings"].role == "coordinating_steward"
    assert "persona coordinating_steward" in mdm({**initialised}, "--as", "coordinating_steward", "ui").stdout
    assert mdm(initialised, "ui", "--port", "0", code=2).exit_code == 2


def test_the_command_line_starts_without_the_workbench() -> None:
    """`python -m mdm` answers, and Dash is imported only when the workbench is asked for."""
    probe = (
        "import sys, runpy; sys.argv = ['mdm', '--help']\n"
        "try:\n    runpy.run_module('mdm', run_name='__main__')\nexcept SystemExit:\n    pass\n"
        "print('dash' in sys.modules)"
    )
    done = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=120, check=True
    )
    assert " ui " in done.stdout and " tray " in done.stdout
    assert done.stdout.strip().splitlines()[-1] == "False"


# ---------------------------------------------------------------------------------------------- mdm tray flush


def test_tray_flush_prints_its_line_and_its_report(initialised, monkeypatch: pytest.MonkeyPatch) -> None:
    reports = iter(
        [
            FlushReport(committed=3, failed=1, requeued=1, outcomes={"committed": 3, "record_changed": 1}),
            FlushReport(skipped_busy=True),
            FlushReport(),
        ]
    )
    asked: list[dict[str, Any]] = []

    def flush(self, *, started_by=None, limit=100) -> FlushReport:
        asked.append({"role": started_by.role if started_by else None, "limit": limit})
        return next(reports)

    monkeypatch.setattr(TrayService, "flush", flush)
    assert mdm(initialised, "tray", "flush").stdout == (
        "flushed: committed 3, failed 1 (record_changed 1), records queued again 1\n"
    )
    assert (
        mdm(initialised, "tray", "flush", "--limit", "7").stdout
        == "another flush holds the tray; nothing done\n"
    )
    report = json.loads(mdm(initialised, "--json", "tray", "flush").stdout)
    assert report == {"committed": 0, "failed": 0, "outcomes": {}, "requeued": 0, "skipped_busy": False}
    assert [a["limit"] for a in asked] == [100, 7, 100]
    assert asked[0]["role"] is not None, "the command line names who started the flush"
    assert mdm(initialised, "tray", "flush", "--limit", "0", code=2).exit_code == 2


def test_tray_flush_watch_runs_until_stopped(initialised, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def flush(self, *, started_by=None, limit=100) -> FlushReport:
        calls["n"] += 1
        if calls["n"] > 2:
            raise KeyboardInterrupt
        return FlushReport()

    monkeypatch.setattr(TrayService, "flush", flush)
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: None)
    result = mdm(initialised, "tray", "flush", "--watch", "1")
    assert result.stdout.count("flushed: committed 0") == 2 and calls["n"] == 3


# ---------------------------------------------------------------------------------------------- the demo and init


def test_hard_cases_are_a_share_from_0_to_1(initialised) -> None:
    for bad in ("-0.1", "1.5"):
        assert mdm(initialised, "demo", "land", *WORLD, "--hard-cases", bad, code=2).exit_code == 2
    with pytest.raises(ValueError):
        DemoConfig(hard_cases=2.0)


def test_creations_only_lands_no_later_event(initialised) -> None:
    config = DemoConfig(persons=20, organisations=6, seed=7)
    full = generate(config)
    first = generate(DemoConfig(persons=20, organisations=6, seed=7, creations_only=True))
    assert first.rows == full.rows[: len(first.rows)] and len(first.rows) < len(full.rows)
    assert first.truth == full.truth
    landed = json.loads(mdm(initialised, "--json", "demo", "land", *WORLD, "--creations-only").stdout)
    assert landed["landed"] == len(first.rows) and landed["config"]["creations_only"] is True
    again = json.loads(mdm(initialised, "--json", "demo", "land", *WORLD).stdout)
    assert again["already_landed"] == len(first.rows) and again["landed"] == len(full.rows) - len(first.rows)


def test_init_reports_the_due_times_it_gave(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _env(tmp_path / "mdm.duckdb")
    assert "due times" not in mdm(env, "init").stdout
    assert json.loads(mdm(env, "--json", "init").stdout)["due_times"] == 0
    monkeypatch.setattr(InboxService, "backfill_due_times", lambda self: 3)
    assert "due times given to 3 open tasks" in mdm(env, "init").stdout
