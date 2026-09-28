"""`mdm breaker status` and `mdm breaker restore`, and what `mdm arrive` says of the checkpoint (story 3.2).

The command line runs on a DuckDB file, as a persona; the breaker's trip comes from the local demo trip.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mdm.cli import _breaker_line
from mdm.config import Settings
from mdm.models.quality import BreakerStatus
from mdm.services.context import Hub
from tests.test_cli_workbench import MODELS, _env, mdm

DEMO = {"agreed": 30, "reviewed": 40, "threshold": 0.95, "window": 100}


@pytest.fixture
def initialised(tmp_path: Path) -> dict[str, str | None]:
    env = _env(tmp_path / "mdm.duckdb", MDM_SAMPLE_SHARE="1")
    mdm(env, "init", "--models", str(MODELS))
    mdm(env, "demo", "land", "--persons", "20", "--organisations", "6", "--seed", "7")
    return env


def _trip(env: dict[str, str | None], entity: str) -> None:
    settings = Settings.from_env({k: v for k, v in env.items() if v is not None})
    with Hub.open(settings, as_role="data_owner") as hub:
        assert hub.breaker.demo_trip(entity, figures=DEMO) is not None


def test_arrive_says_what_the_checkpoint_did(initialised) -> None:
    out = mdm(initialised, "arrive").stdout
    line = next(line for line in out.splitlines() if line.strip().startswith("checkpoint:"))
    assert (
        "quality samples drawn " in line
        and "skipped at the cap 0" in line
        and "breaker tripped: none" in line
    )
    data = json.loads(mdm(initialised, "--json", "arrive").stdout)
    assert {"samples", "samples_skipped", "demoted", "handed_back", "tripped"} <= set(data["arrival"])


def test_status_in_text_and_json(initialised) -> None:
    mdm(initialised, "arrive")
    _trip(initialised, "organisation")
    out = mdm(initialised, "breaker", "status").stdout.splitlines()
    organisation = next(line for line in out if line.startswith("organisation:"))
    person = next(line for line in out if line.startswith("person:"))
    assert organisation.startswith("organisation: automatic band demoted since ")
    assert "blind review agreed 30 of 40 (75%), confidently below 95%" in organisation
    assert organisation.endswith("mdm breaker restore --entity organisation --reason cause_fixed")
    assert person.startswith("person: automatic band normal · no automatic link reviewed blind yet; ")
    assert "it trips once 20 are reviewed and it is confidently below 95%" in person
    assert " open, 0 overdue, 0 voided · " in person
    assert "days of history are needed" in person  # the volume is watched from the first arrival
    one = json.loads(mdm(initialised, "breaker", "status", "--entity", "person", "--json").stdout)
    assert [row["entity"] for row in one] == ["person"] and one[0]["state"] == "normal"
    assert json.loads(mdm(initialised, "--json", "breaker", "status").stdout)[0]["entity"] == "organisation"


def _status(**changes) -> BreakerStatus:
    base = BreakerStatus(
        entity="person",
        state="normal",
        watch_since=datetime(2026, 9, 20, 9, 0, tzinfo=UTC),
        trigger=None,
        tripped_at=None,
        trip_change_set=None,
        figures={},
        restored_at=None,
        restore_reason=None,
        restore_change_set=None,
        agreed=39,
        reviewed=40,
        bound=0.99,
        threshold=0.95,
        window=100,
        min_samples=20,
        samples_open=12,
        samples_overdue=1,
        samples_voided=0,
        arrivals=120,
        mean=110.0,
        multiple=5.0,
        spike_min=1000,
        days=7,
        hour=datetime(2026, 9, 27, 14, 0, tzinfo=UTC),
        history_ready=True,
    )
    return replace(base, **changes)


def test_the_status_line_rounds_down_and_names_every_figure() -> None:
    assert _breaker_line(_status()) == (
        "person: automatic band normal · blind review agreed 39 of the last 40 automatic links (97%); it trips "
        "once 20 are reviewed and it is confidently below 95% · quality samples 12 open, 1 overdue, 0 voided · "
        "120 arrivals this hour, 110 in this hour on average over the last 7 days; it trips above 5 times that "
        "and 1,000"
    )
    assert "(94%)" in _breaker_line(_status(agreed=949, reviewed=1000))  # never "95%, below 95%"
    assert "none in this hour over the last 7 days; it trips at 1,000" in _breaker_line(_status(mean=0.0))
    short = _breaker_line(_status(history_ready=False, watch_since=datetime(2026, 10, 3, tzinfo=UTC)))
    assert "volume watched from 3 Oct 2026; 7 days of history are needed" in short
    volume = _status(
        state="demoted",
        trigger="volume",
        tripped_at=datetime(2026, 9, 27, 14, 2, tzinfo=UTC),
        figures={
            "arrivals": 12400,
            "mean": 1100.0,
            "multiple": 5.0,
            "days": 7,
            "hour": "2026-09-27T14:00:00+00:00",
        },
    )
    assert _breaker_line(volume) == (
        "person: automatic band demoted since 2026-09-27 14:02 UTC by the quality breaker: 12,400 records "
        "arrived in the hour from 14:00 UTC, more than 5 times the mean for that hour over the last 7 days "
        "(1,100). A data owner restores it: mdm breaker restore --entity person --reason cause_fixed"
    )
    quiet = replace(volume, figures={**volume.figures, "mean": 0.0})
    assert "; none arrived in that hour over the last 7 days." in _breaker_line(quiet)


def test_restore_as_a_data_owner_and_its_refusals(initialised) -> None:
    mdm(initialised, "arrive")
    _trip(initialised, "person")
    steward = {**initialised, "MDM_ROLE": "data_steward"}
    refused = mdm(steward, "breaker", "restore", "--entity", "person", "--reason", "cause_fixed", code=1)
    assert (
        refused.stderr.strip()
        == "mdm: Only a data owner restores the automatic band; you act as a data steward."
    )
    bad = mdm(initialised, "breaker", "restore", "--entity", "person", "--reason", "just_because", code=1)
    assert bad.stderr.strip() == "mdm: A restore names its reason: cause_fixed, false_alarm or load_expected."
    owner = {**initialised, "MDM_ROLE": "data_owner"}
    done = mdm(owner, "breaker", "restore", "--entity", "person", "--reason", "cause_fixed").stdout
    assert done.startswith("Restored the automatic band of person (change set CS-")
    assert done.rstrip().endswith("go back to arrival on its next run.")
    again = mdm(owner, "breaker", "restore", "--entity", "person", "--reason", "cause_fixed", code=1)
    assert (
        again.stderr.strip()
        == "mdm: The automatic band of person is not demoted, so there is nothing to restore."
    )
    as_json = json.loads(
        mdm(
            owner, "--json", "breaker", "restore", "--entity", "person", "--reason", "cause_fixed", code=1
        ).stderr
    )
    assert as_json["error"]["code"] == "not_demoted"
    status = json.loads(mdm(owner, "breaker", "status", "--entity", "person", "--json").stdout)
    assert status[0]["state"] == "normal" and status[0]["restore_reason"] == "cause_fixed"
