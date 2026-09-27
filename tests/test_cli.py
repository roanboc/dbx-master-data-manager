"""The command line on a DuckDB file, through Typer's runner (owner: CLI, B.13).

One small world (200 persons, 60 organisations, seed 7) is initialised, landed
as an initial load, loaded, landed again with its updates and deletes, and
arrived once per module through the commands themselves; the read-only tests
share it. Exit codes: 0 ok, 1 refused or invalid, 2 usage.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

import mdm.cli as cli
from mdm.backend.factory import open_store
from mdm.config import PLATFORM_VARIABLES, Settings
from mdm.demo import DemoConfig, generate

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
WORLD = ("--persons", "200", "--organisations", "60", "--seed", "7")
runner = CliRunner()


def _env(path: Path, **extra: str) -> dict[str, str | None]:
    """Only the settings a test names: every MDM_, PG and platform variable of the shell is removed."""
    env: dict[str, str | None] = {
        name: None for name in os.environ if name.startswith(("MDM_", "PG")) or name in PLATFORM_VARIABLES
    }
    env["MDM_DUCKDB_PATH"] = str(path)
    env.update(extra)
    return env


def mdm(env: dict[str, str | None], *args: str, input: str | None = None, code: int | None = 0):
    """Run `mdm ARGS`; check the exit code unless `code` is None."""
    result = runner.invoke(cli.app, list(args), env=env, input=input)
    if code is not None:
        assert result.exit_code == code, (args, result.exit_code, result.output, result.exception)
    return result


def as_json(result) -> object:
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def world_env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, str | None]]:
    env = _env(tmp_path_factory.mktemp("cli") / "mdm.duckdb")
    mdm(env, "init", "--models", str(MODELS))
    mdm(env, "demo", "land", *WORLD, "--initial-load", "--updates", "0", "--deletes", "0")
    mdm(env, "load")
    landed = mdm(env, "--json", "demo", "land", *WORLD)
    assert as_json(landed)["already_landed"] > 0, "the creations were landed by the initial load"
    mdm(env, "arrive")
    yield env


@pytest.fixture
def fresh_env(tmp_path: Path) -> dict[str, str | None]:
    return _env(tmp_path / "fresh.duckdb")


# ---------------------------------------------------------------------------------------------- without a store


def test_help_lists_every_command() -> None:
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    for command in ("init", "whoami", "status", "ddl", "arrive", "load", "match", "estimate", "profile"):
        assert command in result.output
    for group in ("model", "rules", "codelists", "demo", "task", "record", "feed"):
        assert group in result.output


def test_ddl_prints_the_landing_table_without_touching_a_store(fresh_env, tmp_path: Path) -> None:
    result = mdm(fresh_env, "ddl", "--group", "landing")
    assert "CREATE TABLE IF NOT EXISTS mdm_landing.source_change" in result.stdout
    assert "mdm_core" not in result.stdout
    assert not (tmp_path / "fresh.duckdb").exists(), "printing DDL opens no store"
    postgres = mdm({**fresh_env, "MDM_BACKEND": "postgres"}, "--json", "ddl", "--group", "landing")
    statements = as_json(postgres)
    assert isinstance(statements, list) and any("jsonb" in s for s in statements)


def test_ddl_grants(fresh_env) -> None:
    result = mdm(
        fresh_env, "ddl", "--grants", "--hub-role", "hub", "--reader-role", "ip", "--notifier-role", "cn"
    )
    assert "ALTER DEFAULT PRIVILEGES" in result.stdout and "GRANT USAGE ON SCHEMA mdm_core" in result.stdout
    mdm(fresh_env, "ddl", "--grants", code=2)
    mdm(fresh_env, "ddl", "--group", "nowhere", code=2)


@pytest.mark.parametrize(
    "args",
    [
        ("match", "--entity", "person"),
        ("match", "--entity", "person", "--record-file", "-", "--source", "hr", "--key", "H1"),
        ("match", "--entity", "person", "--source", "hr"),
        ("match", "--entity", "person", "--record-file", "-", "--top", "0"),
        ("feed", "read", "--cursor", "twelve"),
        ("feed", "read", "--limit", "0"),
        ("task", "list", "--limit", "0"),
        ("task", "list", "--kind", "chores"),
        ("record", "show", "not a master id"),
        ("rules", "show", "person", "--kind", "colour"),
        ("estimate", "--entity", "person", "--method", "guess"),
        ("demo", "reset"),
        ("demo", "land", "--updates", "2"),
        ("no-such-command",),
    ],
)
def test_usage_errors_exit_2(fresh_env, args: tuple[str, ...]) -> None:
    result = mdm(fresh_env, *args, code=2)
    assert "Traceback" not in result.output


def test_reset_refused_on_a_shared_store(fresh_env) -> None:
    result = mdm({**fresh_env, "DATABRICKS_APP_PORT": "8000"}, "demo", "reset", "--yes", code=1)
    assert result.stderr.startswith("mdm: reset_refused")


def test_persona_refused_on_the_platform(fresh_env) -> None:
    result = mdm({**fresh_env, "DATABRICKS_APP_PORT": "8000"}, "--as", "data_steward", "whoami", code=1)
    assert "persona_refused" in result.stderr and "\n" == result.stderr[-1] and result.stderr.count("\n") == 1


def test_init_on_a_shared_store_leaves_the_landing_table_to_the_integration_platform(fresh_env) -> None:
    shared = {**fresh_env, "DATABRICKS_APP_PORT": "8000"}
    result = mdm(shared, "init")
    assert "mdm ddl --group landing" in result.stdout and "shared" in result.stdout
    assert "simulator_refused" in mdm(shared, "demo", "land", *WORLD, code=1).stderr
    assert "forbidden" in mdm(shared, "init", "--models", str(MODELS), code=1).stderr


def test_unexpected_failures_print_their_type_only(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    def broken() -> None:
        raise KeyError("Briette Ingov")

    monkeypatch.setattr(cli, "app", broken)
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 1
    err = capsys.readouterr().err
    assert err == "mdm: unexpected_error type=KeyError\n"


# ---------------------------------------------------------------------------------------------- the small world


def test_whoami_and_status(world_env) -> None:
    who = as_json(mdm(world_env, "--json", "whoami"))
    assert who["actor"] == {
        "kind": "person",
        "name": "persona:data_owner",
        "persona": True,
        "role": "data_owner",
    }
    assert who["store"] == "local" and who["engine"] == "duckdb"
    steward = as_json(mdm(world_env, "--as", "data_steward", "--json", "whoami"))
    assert steward["actor"]["role"] == "data_steward"
    status = as_json(mdm(world_env, "--json", "status"))
    assert status["last_commit_version"] > 0 and status["queue"] == 0
    assert status["reader"]["high_water"] == status["landing_max_seq"] > 0
    assert status["rows"]["mdm_core.person"] > 0
    text = mdm(world_env, "status").stdout
    assert "last commit version" in text and "open tasks:" in text


def test_the_world_is_resolved_well(world_env) -> None:
    for entity in ("person", "organisation"):
        scores = as_json(mdm(world_env, "--json", "demo", "evaluate", "--entity", entity, *WORLD))
        assert scores["precision"] >= 0.9 and scores["recall"] >= 0.8, scores
    assert "precision" in mdm(world_env, "demo", "evaluate", "--entity", "person", *WORLD).stdout


def test_a_second_arrival_commits_nothing(world_env) -> None:
    report = as_json(mdm(world_env, "--json", "arrive"))["arrival"]
    assert report["read"] == 0 and report["commits"] == 0
    assert "arrival: read 0" in mdm(world_env, "arrive", "--reconcile").stdout


def test_arrive_with_the_lease_held_prints_one_line(world_env) -> None:
    store = open_store(Settings(duckdb_path=world_env["MDM_DUCKDB_PATH"]))
    try:
        with store.exclusive_lease("arrival") as held:
            assert held
            result = mdm(world_env, "arrive")
            assert result.stdout.strip() == "arrival skipped: another run holds the lease"
            assert as_json(mdm(world_env, "--json", "load")) == {"skipped_busy": True}
    finally:
        store.close()


def test_task_list(world_env) -> None:
    tasks = as_json(mdm(world_env, "--json", "task", "list", "--limit", "500"))
    assert tasks and all(t["status"] == "open" for t in tasks)
    kind = tasks[0]["kind"]
    only = as_json(mdm(world_env, "--json", "task", "list", "--kind", kind, "--entity", tasks[0]["entity"]))
    assert only and {t["kind"] for t in only} == {kind}
    assert "TSK-" in mdm(world_env, "task", "list", "--limit", "3").stdout


def _person_ids(env) -> list[str]:
    page = as_json(mdm(env, "--json", "feed", "read", "--since", "0", "--entity", "person", "--limit", "50"))
    return [c["master_id"] for c in page["changes"] if c["change_kind"] == "created"]


def test_record_show_masks_until_revealed_with_a_reason(world_env) -> None:
    master_id = _person_ids(world_env)[0]
    masked = mdm(world_env, "record", "show", master_id).stdout
    shown = as_json(mdm(world_env, "--json", "record", "show", master_id))
    assert shown["master_id"] == master_id and shown["values"]["given_name"].endswith("***")
    assert "given_name" in masked
    refused = mdm(world_env, "record", "show", master_id, "--reveal", "given_name", code=1)
    assert refused.stderr == "mdm: reason_required action=reveal\n"
    revealed = as_json(
        mdm(
            world_env,
            "--json",
            "record",
            "show",
            master_id,
            "--reveal",
            "given_name",
            "--reason",
            "demo check",
        )
    )
    name = revealed["values"]["given_name"]
    assert name and not name.endswith("***") and name not in masked
    consumer = mdm(
        world_env,
        "--as",
        "consumer",
        "record",
        "show",
        master_id,
        "--reveal",
        "given_name",
        "--reason",
        "x",
        code=1,
    )
    assert "forbidden" in consumer.stderr
    missing = mdm(world_env, "--json", "record", "show", "PER-999999", code=1)
    assert json.loads(missing.stderr)["error"]["code"]


def _hr_payload() -> dict:
    world = generate(DemoConfig(persons=200, organisations=60, seed=7))
    return next(r.payload for r in world.rows if r.source_system == "hr" and r.op == "upsert")


def test_match_reads_a_record_from_standard_input_and_shows_no_value(world_env) -> None:
    payload = _hr_payload()
    result = mdm(world_env, "match", "--entity", "person", "--record-file", "-", input=json.dumps(payload))
    assert "PER-" in result.stdout and "score" in result.stdout and "prior" in result.stdout
    for attribute in ("given_name", "family_name", "email", "person_ref"):
        if payload.get(attribute):
            assert payload[attribute] not in result.stdout, attribute
    data = as_json(
        mdm(
            world_env,
            "--json",
            "match",
            "--entity",
            "person",
            "--record-file",
            "-",
            input=json.dumps(payload),
        )
    )
    best = data["candidates"][0]
    assert best["best"]["explanation"]["band"] == "auto"
    assert best["golden"]["given_name"].endswith("***")


def test_match_from_a_file_or_a_stored_record(world_env, tmp_path: Path) -> None:
    record = tmp_path / "record.json"
    record.write_text(json.dumps(_hr_payload()), encoding="utf-8")
    top = as_json(
        mdm(world_env, "--json", "match", "--entity", "person", "--record-file", str(record), "--top", "2")
    )
    assert 1 <= len(top["candidates"]) <= 2
    stored = as_json(
        mdm(world_env, "--json", "match", "--entity", "person", "--source", "hr", "--key", "H000001")
    )
    assert stored["candidates"][0]["best"]["explanation"]["band"] == "auto"
    record.write_text("not json", encoding="utf-8")
    bad = mdm(world_env, "match", "--entity", "person", "--record-file", str(record), code=1)
    assert bad.stderr.startswith("mdm: bad_record_file")
    missing = mdm(
        world_env, "match", "--entity", "person", "--record-file", str(tmp_path / "none.json"), code=1
    )
    assert missing.stderr.startswith("mdm: file_unreadable")
    assert (
        "bad_record_file"
        in mdm(world_env, "match", "--entity", "person", "--record-file", "-", input="[1]", code=1).stderr
    )


def test_feed_read_pages_with_a_cursor_and_masks_rows(world_env) -> None:
    first = mdm(world_env, "feed", "read", "--since", "0", "--limit", "3").stdout
    assert first.rstrip().endswith("next: mdm feed read --since 0 --cursor 1:3")
    page = as_json(
        mdm(world_env, "--json", "feed", "read", "--since", "0", "--cursor", "1:3", "--limit", "5")
    )
    assert [c["change_seq"] for c in page["changes"]][:1] == [4]
    everything = as_json(mdm(world_env, "--json", "feed", "read", "--since", "0", "--entity", "person"))
    persons = everything["rows"].get("person", {})
    assert persons and all(
        row["given_name"].endswith("***") for row in persons.values() if row.get("given_name")
    )
    last = as_json(mdm(world_env, "--json", "status"))["last_commit_version"]
    end = as_json(mdm(world_env, "--json", "feed", "read", "--since", str(last)))
    assert end["changes"] == [] and end["next_watermark"] == last and end["cursor"] is None


def test_models_rules_and_code_lists(world_env) -> None:
    listed = as_json(mdm(world_env, "--json", "model", "list"))
    assert {"person", "organisation"} <= set(listed)
    assert "entity: person" in mdm(world_env, "model", "show", "person").stdout
    rules = as_json(mdm(world_env, "--json", "rules", "show", "person", "--kind", "validation"))
    assert rules["status"] == "published" and "PER-V1" in json.dumps(rules["rules"])
    assert "bands" in mdm(world_env, "rules", "show", "organisation").stdout
    assert "rule_set_not_found" in mdm(world_env, "rules", "show", "person", "--version", "99", code=1).stderr
    lists = as_json(mdm(world_env, "--json", "codelists", "load", str(MODELS / "codelists")))
    assert lists["country"] >= 2
    assert "not_found" in mdm(world_env, "codelists", "load", str(MODELS / "nowhere"), code=1).stderr


def test_rules_publish_refused_after_arrival(world_env) -> None:
    draft = as_json(mdm(world_env, "--json", "model", "load", str(MODELS / "person.yaml")))
    assert draft["published"] is False and draft["version"] >= 2
    refused = mdm(
        world_env, "rules", "publish", "person", "--kind", "match", "--version", str(draft["version"]), code=1
    )
    assert refused.stderr.startswith("mdm: publish_refused") and "initiative-4" in refused.stderr
    assert "publish_refused" in mdm(world_env, "model", "publish", "person", code=1).stderr
    assert (
        "forbidden"
        in mdm(world_env, "--as", "data_steward", "model", "load", str(MODELS / "person.yaml"), code=1).stderr
    )


def test_profile_masks_personal_values(world_env) -> None:
    profile = as_json(mdm(world_env, "--json", "profile", "--entity", "person", "--source", "hr"))
    assert profile["records"] > 0
    given = next(a for a in profile["attributes"] if a["name"] == "given_name")
    assert given["top"] and all(value.endswith("***") for value, _ in given["top"])
    assert "filled" in mdm(world_env, "profile", "--entity", "organisation").stdout


def test_estimate_drafts_or_refuses_safely(world_env) -> None:
    result = mdm(world_env, "estimate", "--entity", "organisation", code=None)
    assert result.exit_code in (0, 1) and "Traceback" not in result.output
    if result.exit_code == 0:
        assert "drafted" in result.stdout
    else:
        assert result.stderr.startswith("mdm: ") and result.stderr.count("\n") == 1


@pytest.mark.parametrize(
    "args",
    [
        ("model", "show", "Ann Smith"),
        ("rules", "show", "person.name"),
        ("--as", "Data Owner", "whoami"),
        ("match", "--entity", "person", "--source", "hr", "--key", "a key with spaces"),
        ("record", "show", "PER-000001", "--reveal", "given name"),
        ("profile", "--entity", "person", "--source", "Crm"),
    ],
)
def test_free_text_never_reaches_a_refusal(fresh_env, args: tuple[str, ...]) -> None:
    """Names and keys are checked before anything runs: a usage error, never a message quoting them."""
    result = mdm(fresh_env, *args, code=2)
    assert "Traceback" not in result.output


def test_a_store_never_initialised_is_refused(fresh_env) -> None:
    assert mdm(fresh_env, "status", code=1).stderr.startswith("mdm: store_not_initialised")
    mdm(fresh_env, "init")
    assert "landing_table_missing" not in mdm(fresh_env, "demo", "land", *WORLD).stderr
    assert "no entity models loaded" in mdm(fresh_env, "model", "list").stdout
    assert mdm(fresh_env, "init", "--models", "/nowhere/at/all", code=2).exit_code == 2


def test_match_narrative_is_a_labelled_suggestion(world_env) -> None:
    payload = _hr_payload()
    args = ("match", "--entity", "person", "--record-file", "-", "--narrative", "--top", "1")
    result = mdm(world_env, *args, input=json.dumps(payload), code=None)
    if isinstance(result.exception, NotImplementedError):
        pytest.skip("the assistance plumbing (owner: SERVICES) is not built yet")
    assert result.exit_code == 0, result.output
    assert "suggestion from stub, not a decision:" in result.stdout
    for attribute in ("given_name", "family_name", "email"):
        if payload.get(attribute):
            assert payload[attribute] not in result.stdout, attribute
    data = as_json(mdm(world_env, "--json", *args, input=json.dumps(payload)))
    assert data["narrative"]["provider"] == "stub" and data["narrative"]["labelled"] is True


@pytest.mark.postgres
def test_the_command_line_on_a_test_postgres(postgres_dsn: str, tmp_path: Path) -> None:
    """The same commands on Postgres: a prefix of its own, personas allowed; `demo reset` refuses prefix mdm."""
    from tests.conftest import new_prefix

    env = _env(
        tmp_path / "unused.duckdb",
        MDM_BACKEND="postgres",
        MDM_POSTGRES_DSN=postgres_dsn,
        MDM_ALLOW_PERSONAS="1",
        MDM_SCHEMA_PREFIX=new_prefix(),
    )
    try:
        mdm(env, "init", "--models", str(MODELS))
        mdm(env, "demo", "land", "--persons", "30", "--organisations", "10")
        report = as_json(mdm(env, "--json", "arrive"))["arrival"]
        assert report["read"] > 0 and report["commits"] > 0
        assert as_json(mdm(env, "--json", "status"))["queue"] == 0
        assert as_json(mdm(env, "--json", "whoami"))["engine"] == "postgres"
    finally:
        mdm(env, "demo", "reset", "--yes")
    refused = mdm({**env, "MDM_SCHEMA_PREFIX": "mdm"}, "demo", "reset", "--yes", code=1)
    assert refused.stderr.startswith("mdm: drop_refused")
