"""`mdm batch`, the withdrawn signatures of `mdm breaker status`, `mdm breaker restore --signature`, the signature
backfill of `mdm init`, and `mdm ui` on a store from before signature batches (story 3.3).

The command line runs on a DuckDB file, as a persona; the batch is drawn, decided and prepared through the services
over the same file, each hub closed before a command runs.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from mdm.backend import guard
from mdm.config import Settings
from mdm.models.batch import BATCH_ID_RE
from mdm.models.quality import bulk_band
from mdm.services.context import Hub
from tests.helpers import (
    ALIKE_SIGNATURE,
    STEWARD,
    alike_reviews,
    decide_sample,
    flush_past_window,
    open_tasks,
    workbench_world,
)
from tests.test_cli_workbench import MODELS, _env, mdm


def _hub(env: dict[str, str | None], role: str = "data_owner") -> Hub:
    settings = Settings.from_env({k: v for k, v in env.items() if v is not None})
    return Hub.open(settings, as_role=role)


def as_role(env: dict[str, str | None], role: str) -> dict[str, str | None]:
    return {**env, "MDM_ROLE": role}


@pytest.fixture
def world(tmp_path: Path) -> dict[str, str | None]:
    """An initialised store with 12 alike reviews of one person pattern."""
    env = _env(tmp_path / "mdm.duckdb", MDM_BATCH_CHECKER_ABOVE="3")
    mdm(env, "init", "--models", str(MODELS))
    with _hub(env) as hub:
        workbench_world(hub, persons=32)
        alike_reviews(hub, 12)
    return env


def prepared_batch(env: dict[str, str | None]) -> str:
    """A batch drawn, its sample agreed, and every change shown, as the data steward persona: its ID."""
    with _hub(env) as hub:
        key = hub.batches.groups(actor=STEWARD).groups[0].group_key
        batch = hub.batches.draw(key, actor=STEWARD, entity="person")
        decide_sample(hub, batch.batch_id, actor=STEWARD)
        hub.batches.refresh(batch.batch_id)
        hub.batches.prepare(batch.batch_id, actor=STEWARD)
        return batch.batch_id


def test_list_show_stage_confirm_and_stop(world) -> None:
    steward = as_role(world, "data_steward")
    coordinator = as_role(world, "coordinating_steward")
    assert mdm(steward, "batch", "list").stdout.strip() == "no batch"
    batch_id = prepared_batch(world)
    line = mdm(steward, "batch", "list").stdout.strip()
    assert line == f"{batch_id} · person · link · ready · 7 reviews · prepared by a data steward"
    listed = json.loads(mdm(steward, "batch", "list", "--json").stdout)
    assert [b["batch_id"] for b in listed] == [batch_id]
    shown = mdm(steward, "batch", "show", batch_id, "--rows").stdout
    assert "pattern: given name the same · family name the same · birth date similar" in shown
    assert "forced sample: 5 of 5 decided · 5 agreed · 0 disagreed · 12 reviews drawn" in shown
    assert "every change: 7 cross-references · 7 golden records updated · 1 chunk of at most 500" in shown
    rows = [r for r in shown.splitlines() if r.startswith("  crm:") and "->" in r]
    assert len(rows) == 7 and all("+1 cross-reference" in r and "planned" in r for r in rows)
    waits = mdm(steward, "batch", "stage", batch_id).stdout.strip()
    assert waits == (
        f"{batch_id} waits for a second steward: mdm batch confirm {batch_id}, or its page in the workbench."
    )
    own = mdm(steward, "batch", "confirm", batch_id, code=1)
    assert own.stderr.strip() == "mdm: A second steward, not the one who prepared it, confirms this."
    confirmed = mdm(coordinator, "batch", "confirm", batch_id).stdout.strip()
    assert re.fullmatch(
        rf"Confirmed {batch_id}: it waits in the tray until \d\d:\d\d:\d\d UTC, then commits in 1 chunk\.",
        confirmed,
    )
    early = mdm(steward, "batch", "stop", batch_id, code=1)
    assert early.stderr.strip() == "mdm: It is still in the tray: undo it instead."
    bad = mdm(steward, "batch", "show", "BAT-nothex", code=2)
    assert bad.exit_code == 2
    unknown = mdm(steward, "batch", "show", "BAT-" + "0" * 20, code=1)
    assert unknown.stderr.strip() == "mdm: No batch has that ID."


def test_stop_asks_a_committing_batch_to_stop(world, monkeypatch: pytest.MonkeyPatch) -> None:
    from mdm import capacity

    steward = as_role(world, "data_steward")
    monkeypatch.setattr(capacity, "COMMIT_CHUNK_ROWS", 6)
    batch_id = prepared_batch(world)
    with _hub({**world, "MDM_BATCH_CHECKER_ABOVE": "250"}) as hub:
        hub.batches.stage(batch_id, actor=STEWARD)
        flush_past_window(hub)
        assert hub.store.batches([batch_id])[batch_id].status == "committing"
    said = mdm(steward, "batch", "stop", batch_id).stdout.strip()
    assert said == f"Stop asked for {batch_id}: it stops before its next chunk; committed chunks stay."
    flushed = mdm(as_role(world, "data_owner"), "tray", "flush").stdout.strip()
    assert flushed.endswith("; batch chunks committed 0, batches finished 1")
    shown = mdm(steward, "batch", "show", batch_id).stdout
    assert "outcome: stopped" in shown and "mdm batch compensate" in shown


def test_discard_and_compensate(world, monkeypatch: pytest.MonkeyPatch) -> None:
    steward = as_role(world, "data_steward")
    coordinator = as_role(world, "coordinating_steward")
    batch_id = prepared_batch(world)
    early = mdm(coordinator, "batch", "compensate", batch_id, "--reason", "pattern_wrong", code=1)
    assert early.stderr.strip() == "mdm: Only a committed batch of links can be undone."
    with _hub({**world, "MDM_BATCH_CHECKER_ABOVE": "250"}) as hub:
        hub.batches.stage(batch_id, actor=STEWARD)
        flush_past_window(hub)
        assert hub.store.batches([batch_id])[batch_id].status == "committed"
    bad = mdm(coordinator, "batch", "compensate", batch_id, "--reason", "oops", code=1)
    assert (
        bad.stderr.strip() == "mdm: An undo names its reason: pattern_wrong, source_defect or sample_missed."
    )
    prepared = mdm(coordinator, "batch", "compensate", batch_id, "--reason", "pattern_wrong").stdout.strip()
    found = re.match(r"Prepared (BAT-[0-9a-f]{20}) to undo (BAT-[0-9a-f]{20})'s 7 links: ", prepared)
    assert found and found.group(2) == batch_id and BATCH_ID_RE.match(found.group(1))
    compensation = found.group(1)
    assert "7 cross-references ended · 7 golden records recomputed · 1 chunk." in prepared
    assert prepared.endswith(
        f"Check every row with mdm batch show {compensation} --rows, then stage it with mdm batch stage "
        f"{compensation}."
    )
    again = mdm(coordinator, "batch", "compensate", batch_id, "--reason", "pattern_wrong", code=1)
    assert again.stderr.strip() == "mdm: This batch is already undone, or its undo is waiting."
    assert "being undone by" in mdm(steward, "batch", "show", batch_id).stdout
    discarded = mdm(coordinator, "batch", "discard", compensation).stdout.strip()
    assert discarded == f"Discarded {compensation}; its reviews stay in the inbox."
    staged = mdm(coordinator, "batch", "compensate", batch_id, "--reason", "source_defect").stdout.strip()
    assert staged.startswith("Prepared ")


def test_stage_at_the_threshold_and_a_batch_undone_in_part(world, monkeypatch: pytest.MonkeyPatch) -> None:
    """At or below the threshold `stage` puts the batch in the tray; a compensation is staged the same way, and
    once one stopped after its first chunk `show` says what it undid and what can still be undone."""
    from mdm import capacity

    monkeypatch.setattr(capacity, "COMMIT_CHUNK_ROWS", 6)  # 7 links in 4 chunks of at most two
    below = {**world, "MDM_BATCH_CHECKER_ABOVE": "250"}
    steward = as_role(below, "data_steward")
    coordinator = as_role(below, "coordinating_steward")
    batch_id = prepared_batch(world)
    staged = mdm(steward, "batch", "stage", batch_id).stdout.strip()
    assert re.fullmatch(
        rf"Staged {batch_id}: it waits in the tray until \d\d:\d\d:\d\d UTC, then commits in 4 chunks\.",
        staged,
    )
    with _hub(below) as hub:
        for _ in range(4):
            flush_past_window(hub)
        assert hub.store.batches([batch_id])[batch_id].status == "committed"
    prepared = mdm(coordinator, "batch", "compensate", batch_id, "--reason", "pattern_wrong").stdout
    compensation = re.match(r"Prepared (BAT-[0-9a-f]{20}) ", prepared).group(1)  # type: ignore[union-attr]
    rows = mdm(coordinator, "batch", "show", compensation, "--rows").stdout
    assert len([r for r in rows.splitlines() if "−1 cross-reference" in r]) == 7
    again = mdm(coordinator, "batch", "stage", compensation).stdout.strip()
    assert again.startswith(f"Staged {compensation}: it waits in the tray until ")
    assert again.endswith("then commits in 4 chunks.")
    with _hub(below) as hub:
        flush_past_window(hub)
        hub.batches.stop(compensation, actor=STEWARD)
        flush_past_window(hub)
        assert hub.store.batches([compensation])[compensation].status == "stopped"
    shown = mdm(steward, "batch", "show", batch_id).stdout
    assert re.search(
        rf"2 of 7 links undone by {compensation}, which stopped; the other 5 can be undone until "
        r"\d{1,2} [A-Z][a-z]+ \d{4}",
        shown,
    ), shown


def test_breaker_status_names_a_withdrawn_signature_and_a_data_owner_restores_it(world) -> None:
    with _hub(world) as hub:
        key = hub.batches.groups(actor=STEWARD).groups[0].group_key
        hub.breaker.demo_withdraw("person", key, figures={"agreed": 3, "reviewed": 5, "threshold": 0.95})
    band = bulk_band("person", ALIKE_SIGNATURE)
    owner = as_role(world, "data_owner")
    lines = mdm(owner, "breaker", "status", "--entity", "person").stdout.splitlines()
    (withdrawn,) = [line for line in lines if "bulk decisions withdrawn" in line]
    assert withdrawn.startswith(
        "person: bulk decisions withdrawn for given name the same · family name the same · birth date similar"
    )
    assert f"({band}) since " in withdrawn
    assert "blind review agreed 3 of 5 batch links (60%), confidently below 95%" in withdrawn
    assert withdrawn.endswith(
        f"A data owner restores them: mdm breaker restore --entity person --signature {band} --reason cause_fixed"
    )
    status = json.loads(mdm(owner, "breaker", "status", "--entity", "person", "--json").stdout)
    assert [w["key"] for w in status[0]["withdrawn"]] == [band]
    steward = as_role(world, "data_steward")
    refused = mdm(
        steward,
        "breaker",
        "restore",
        "--entity",
        "person",
        "--signature",
        band,
        "--reason",
        "cause_fixed",
        code=1,
    )
    assert (
        refused.stderr.strip() == "mdm: Only a data owner restores bulk decisions; you act as a data steward."
    )
    load = mdm(
        owner,
        "breaker",
        "restore",
        "--entity",
        "person",
        "--signature",
        band,
        "--reason",
        "load_expected",
        code=1,
    )
    assert (
        load.stderr.strip()
        == "mdm: A restore of bulk decisions names its reason: cause_fixed or false_alarm."
    )
    bad = mdm(
        owner,
        "breaker",
        "restore",
        "--entity",
        "person",
        "--signature",
        "bulk:12",
        "--reason",
        "cause_fixed",
        code=1,
    )
    assert bad.stderr.strip() == (
        "mdm: A pattern's key is bulk: and 16 hexadecimal characters, as mdm breaker status prints it."
    )
    done = mdm(
        owner, "breaker", "restore", "--entity", "person", "--signature", band, "--reason", "cause_fixed"
    )
    assert done.stdout.startswith(f"Restored bulk decisions for {band} of person (change set CS-")
    again = mdm(
        owner,
        "breaker",
        "restore",
        "--entity",
        "person",
        "--signature",
        band,
        "--reason",
        "cause_fixed",
        code=1,
    )
    assert (
        again.stderr.strip()
        == f"mdm: Bulk decisions for {band} of person are not withdrawn, so there is nothing to restore."
    )
    assert not [line for line in mdm(owner, "breaker", "status").stdout.splitlines() if "withdrawn" in line]


def test_init_gives_older_reviews_their_signature(world) -> None:
    with _hub(world) as hub:
        task = next(t for t in open_tasks(hub, "person", "review") if t.signature == ALIKE_SIGNATURE)
        with hub.store.transaction():
            hub.store._write_tasks([replace(task, signature=None, rule_version=None)])  # noqa: SLF001 - a 3.2 row
    out = mdm(world, "init").stdout
    assert "signatures given to 1 open reviews" in out
    assert json.loads(mdm(world, "--json", "init").stdout)["signatures"] == 0


def test_the_workbench_asks_for_mdm_init_on_a_store_without_batch_tables(tmp_path: Path, monkeypatch) -> None:
    import mdm.ui.server as ui_server

    served: list[object] = []
    monkeypatch.setattr(ui_server, "serve", lambda settings, **kwargs: served.append(kwargs))
    env = _env(tmp_path / "mdm.duckdb")
    mdm(env, "init", "--models", str(MODELS))
    with _hub(env) as hub, guard.ddl_scope():  # as a story-3.2 store left it: no batch table
        hub.store._execute(f"DROP TABLE {hub.store.t('work', 'batch')}")  # noqa: SLF001
    refused = mdm(env, "ui", code=1)
    assert refused.stderr == (
        "mdm: The store has no tables for batches of alike reviews yet; run `mdm init` first.\n"
    )
    assert served == []
    mdm(env, "init")
    mdm(env, "ui")
    assert len(served) == 1
