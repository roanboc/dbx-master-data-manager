"""The throughput spike runs end to end on a tiny world and reports every figure (owner: CLI, B.15)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _spike():
    spec = importlib.util.spec_from_file_location("spike_throughput", ROOT / "tools" / "spike_throughput.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.slow
def test_a_tiny_spike_reports_every_figure(tmp_path: Path, capsys) -> None:
    spike = _spike()
    assert spike.main(["--engine", "duckdb", "--records", "600", "--out", str(tmp_path)]) == 0
    printed = capsys.readouterr().out
    assert printed.startswith(spike.HEADER) and "| duckdb | " in printed
    result = json.loads((tmp_path / "duckdb-600.json").read_text(encoding="utf-8"))
    assert result["finished"] == ["init", "land", "bulk arrival", "incremental arrival", "evaluation"]
    assert result["stopped"] is None
    assert 500 <= result["records_landed"] <= 700 and result["updates_landed"] > 0
    assert set(result["mean_candidates"]) == {"25%", "50%", "100%"}
    assert {"person.family_birth_year", "organisation.registered_id"} <= set(result["blocks"])
    assert all(0.0 <= result["evaluation"][e]["recall"] <= 1.0 for e in ("person", "organisation"))
    assert result["projection"]["linear_seconds"] > 0
    assert spike.markdown_row(result).count("|") == spike.HEADER.count("|")


def test_the_time_limit_stops_cleanly(tmp_path: Path) -> None:
    spike = _spike()
    args = ["--engine", "duckdb", "--records", "300", "--out", str(tmp_path), "--time-limit", "0"]
    result = spike.run(spike.parse_args(args))
    assert result["stopped"] == "time_limit during the bulk arrival"
    assert "bulk arrival" not in result["finished"] and "evaluation" in result["finished"]
    assert result["projection"]["linear_seconds"] is None
