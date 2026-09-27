"""The guard: writes the store refuses outside their scope (B.5.4)."""

from __future__ import annotations

import threading
from datetime import UTC, datetime

import pytest

from mdm.backend import guard
from mdm.backend.factory import open_store
from mdm.backend.store import SqlStore
from mdm.config import Settings
from mdm.models.errors import GuardError, PlatformRefused
from mdm.models.records import LandingRow, SourceKey, XrefRow

T0 = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)


def landing_row(event: str = "ev1") -> LandingRow:
    return LandingRow(event, "hr", "H1", "person", "upsert", T0, {"given_name": "Given01"})


def test_a_core_write_outside_the_commit_scope_is_refused(store: SqlStore) -> None:
    row = XrefRow("person", SourceKey("hr", "H1"), "PER-000001", "active", 1)
    with pytest.raises(GuardError, match="core_write_outside_commit"):
        store.write_xrefs([row])
    with pytest.raises(GuardError), store.transaction():
        store.write_xrefs([row])
    with store.commit_scope():
        assert store.write_xrefs([row]) == 1
    assert store.xrefs_for_sources("person", [row.source]) == {row.source: "PER-000001"}


def test_versions_and_ids_need_the_commit_scope(store: SqlStore) -> None:
    with pytest.raises(GuardError, match="outside_commit_scope"):
        store.next_commit_version()
    with pytest.raises(GuardError, match="outside_commit_scope"):
        store.allocate_ids("person", "PER", 1)


def test_a_landing_write_needs_the_simulator(store: SqlStore) -> None:
    with pytest.raises(GuardError, match="landing_write_refused"):
        store.landing_insert([landing_row()])
    with guard.simulating_integration_platform(store.settings, store.prefix):
        assert store.landing_insert([landing_row()]) == 1
    assert "simulator" not in guard.active_scopes()


def test_the_simulator_is_refused_on_a_shared_store() -> None:
    shared = Settings(duckdb_path=":memory:", platform_signals=("DATABRICKS_APP_NAME",))
    with pytest.raises(PlatformRefused), guard.simulating_integration_platform(shared, "mdm"):
        pass
    lakebase = Settings(
        backend="postgres", lakebase_endpoint="projects/p/branches/b/endpoints/e", allow_personas=True
    )
    with pytest.raises(PlatformRefused), guard.simulating_integration_platform(lakebase, "mdm"):
        pass
    plain = Settings(backend="postgres", postgres_dsn="host=nowhere")
    with pytest.raises(PlatformRefused), guard.simulating_integration_platform(plain, "t1"):
        pass


def test_the_live_suite_writes_landing_rows_only_in_its_run_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    lakebase = Settings(backend="postgres", lakebase_endpoint="projects/p/branches/b/endpoints/e")
    monkeypatch.setenv("MDM_LIVE_LAKEBASE", "1")
    with guard.simulating_integration_platform(lakebase, "t0123456789"):
        assert "simulator" in guard.active_scopes()
    with pytest.raises(PlatformRefused), guard.simulating_integration_platform(lakebase, "mdm"):
        pass


def test_audit_is_insert_only(store: SqlStore) -> None:
    for sql in (
        f"UPDATE {store.t('audit', 'change_set')} SET reason = 'x'",
        f"DELETE FROM {store.t('audit', 'change_log')}",
        f"/*mdm:small*/ TRUNCATE {store.t('audit', 'access_log')}",
    ):
        with pytest.raises(GuardError, match="audit_is_insert_only"):
            store._execute(sql)
        with pytest.raises(GuardError), store.commit_scope():  # not even the commit may
            store._execute(sql)


def test_ddl_on_published_schemas_needs_the_ddl_scope(store: SqlStore) -> None:
    with pytest.raises(GuardError, match="ddl_outside_scope"):
        store._execute(f"CREATE TABLE {store.t('core', 'extra')} (a INTEGER)")
    with pytest.raises(GuardError, match="ddl_outside_scope"):
        store._execute(f"ALTER TABLE {store.t('core', 'xref')} ADD COLUMN note INTEGER")
    with pytest.raises(GuardError, match="ddl_outside_scope"):
        store._execute(f"CREATE OR REPLACE VIEW {store.t('read', 'x')} AS SELECT 1 AS a")
    with pytest.raises(GuardError, match="drop_outside_ddl"):
        store._execute(f"DROP TABLE IF EXISTS {store.t('work', 'job_run')}")
    with pytest.raises(GuardError, match="ddl_outside_scope"):  # no schema is left out
        store._execute(f"CREATE TABLE IF NOT EXISTS {store.t('work', 'scratch')} (a INTEGER)")
    with pytest.raises(GuardError, match="ddl_outside_scope"):
        store._execute(f"ALTER TABLE {store.t('audit', 'access_log')} DROP COLUMN reason")
    with guard.ddl_scope():
        store._execute(f"CREATE TABLE IF NOT EXISTS {store.t('work', 'scratch')} (a INTEGER)")
        store._execute(f"DROP TABLE {store.t('work', 'scratch')}")


def test_the_vault_is_redacted_never_deleted(store: SqlStore) -> None:
    table = store.t("vault", "personal_value")
    for sql in (f"DELETE FROM {table}", f"/*mdm:small*/ TRUNCATE {table}"):
        with pytest.raises(GuardError, match="vault_values_are_redacted_not_deleted"):
            store._execute(sql)
    update = f"UPDATE {table} SET value = NULL WHERE value_id = 'none'"
    with pytest.raises(GuardError, match="vault_update_outside_redact"):
        store._execute(update)
    with guard.redact_scope():
        store._execute(update)


def test_the_target_is_read_whatever_its_case() -> None:
    for sql in ("INSERT INTO MDM_CORE.person VALUES (1)", "insert into Mdm_Core.person values (1)"):
        with pytest.raises(GuardError, match="core_write_outside_commit"):
            guard.check_statement(sql, "mdm")
    with pytest.raises(GuardError, match="audit_is_insert_only"):
        guard.check_statement("DELETE FROM MDM_AUDIT.change_log", "mdm")


def test_check_statement_reads_the_first_target() -> None:
    guard.check_statement("SELECT * FROM mdm_core.xref", "mdm")
    guard.check_statement("/*mdm:keyed*/ INSERT INTO mdm_work.task SELECT * FROM mdm_core.xref", "mdm")
    guard.check_statement("INSERT INTO mdmx_core.xref VALUES (1)", "mdm")  # another prefix
    with pytest.raises(GuardError):
        guard.check_statement("  /*mdm:keyed*/\n  insert into mdm_core.xref values (1)", "mdm")
    with pytest.raises(GuardError):
        guard.check_statement("-- a note\nUPDATE mdm_core.xref SET status = 'active'", "mdm")
    with pytest.raises(GuardError):
        guard.check_statement("WITH x AS (SELECT 1) INSERT INTO mdm_core.xref SELECT * FROM x", "mdm")
    guard.check_statement("WITH x AS (SELECT 1) SELECT * FROM x", "mdm")
    with pytest.raises(GuardError):
        guard.check_statement("MERGE INTO mdm_landing.source_change USING x ON true", "mdm")
    with pytest.raises(GuardError):
        guard.check_statement("COPY mdm_core.change FROM '/tmp/x'", "mdm")
    with pytest.raises(GuardError):
        guard.check_statement("CREATE SCHEMA IF NOT EXISTS mdm_core", "mdm")
    with pytest.raises(GuardError):
        guard.check_statement("CREATE SCHEMA IF NOT EXISTS mdm_work", "mdm")
    with guard.ddl_scope():
        guard.check_statement("CREATE SCHEMA IF NOT EXISTS mdm_work", "mdm")
    assert guard.statement_kind("/* a */ /* b */ select 1") == "SELECT"


def test_scopes_do_not_leak_to_another_thread() -> None:
    seen: list[frozenset[str]] = []
    with guard.commit_scope_flag(), guard.ddl_scope():
        assert guard.active_scopes() == {"commit", "ddl"}
        worker = threading.Thread(target=lambda: seen.append(guard.active_scopes()))
        worker.start()
        worker.join(timeout=60)
    assert seen == [frozenset()]
    assert guard.active_scopes() == frozenset()


@pytest.mark.postgres
def test_drop_all_refuses_the_default_prefix_on_postgres(request: pytest.FixtureRequest) -> None:
    dsn = request.getfixturevalue("postgres_dsn")
    store = open_store(Settings(backend="postgres", postgres_dsn=dsn, allow_personas=True))
    try:
        assert store.prefix == "mdm"
        with pytest.raises(GuardError, match="drop_refused"), guard.ddl_scope():
            store.drop_all()
    finally:
        store.close()


def test_drop_all_on_duckdb_and_a_shared_store(settings: Settings) -> None:
    local = open_store(settings)
    try:
        local.init_schema(create_landing=True)
        local.drop_all()  # the local mode may reset, whatever the prefix
        assert local.table_columns("core", "xref") == []
    finally:
        local.close()
    shared = open_store(settings.with_(platform_signals=("DATABRICKS_RUNTIME_VERSION",)))
    try:
        with pytest.raises(PlatformRefused):
            shared.drop_all()
    finally:
        shared.close()
