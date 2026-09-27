"""The DDL on both engines: groups, portable types, entity tables, masked views, appended columns (B.5.1–B.5.2)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any

import pytest

from mdm.backend import ddl, guard
from mdm.backend.factory import open_store
from mdm.backend.store import SqlStore
from mdm.config import Settings
from mdm.models.entity_model import SQL_RESERVED_WORDS, EntityModel
from mdm.models.errors import ModelError, PlatformRefused
from mdm.models.records import GoldenRow

PORTABLE = {
    "duckdb": {
        "VARCHAR",
        "BIGINT",
        "INTEGER",
        "DECIMAL(38,10)",
        "BOOLEAN",
        "DATE",
        "TIMESTAMP WITH TIME ZONE",
        "JSON",
    },
    "postgres": {
        "text",
        "bigint",
        "integer",
        "numeric",
        "boolean",
        "date",
        "timestamp with time zone",
        "jsonb",
    },
}


def portable(engine: str) -> set[str]:
    return PORTABLE["postgres" if engine == "lakebase" else engine]


def model_with(model: EntityModel, *extra: Mapping[str, Any], version: int | None = None) -> EntityModel:
    doc = model.to_dict()
    doc["attributes"] = [*doc["attributes"], *extra]
    if version is not None:
        doc["version"] = version
    return EntityModel.from_dict(doc)


def schemas_of(store: SqlStore) -> set[str]:
    rows = store._fetch_all(
        "/*mdm:small*/ SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE ?",
        [store.prefix + "%"],
    )
    return {r[0] for r in rows}


def golden(master_id: str, values: Mapping[str, Any], version: int = 1) -> GoldenRow:
    return GoldenRow("person", master_id, "active", None, dict(values), version, 1, False)


def test_every_group_is_created(store: SqlStore) -> None:
    assert {ddl.schema_name(store.prefix, g) for g in ddl.GROUPS} <= schemas_of(store)
    for table in ddl.TABLES:
        assert store.table_columns(table.group, table.name) == list(table.column_names())
    for view in ddl.PASSTHROUGH_VIEWS:
        assert store.table_columns("read", view) == list(ddl.table("core", view).column_names())


def test_init_schema_is_idempotent(store: SqlStore) -> None:
    store.init_schema(create_landing=True)
    store.init_schema(create_landing=True)
    assert store.table_columns("core", "xref") == list(ddl.table("core", "xref").column_names())


def test_every_type_is_portable(
    store: SqlStore, engine: str, person_model: EntityModel, org_model: EntityModel
) -> None:
    store.ensure_entity_tables(org_model)
    store.ensure_entity_tables(person_model)
    schemas = [ddl.schema_name(store.prefix, g) for g in ddl.GROUPS]
    marks = ", ".join("?" for _ in schemas)
    rows = store._fetch_all(
        f"/*mdm:small*/ SELECT table_schema, table_name, column_name, data_type FROM information_schema.columns "
        f"WHERE table_schema IN ({marks})",
        schemas,
    )
    assert rows
    found = {r[3] for r in rows}
    assert found <= portable(engine), found - portable(engine)
    core = {r[3] for r in rows if r[0] == ddl.schema_name(store.prefix, "core")}
    assert core <= portable(engine)


def test_fixed_names_are_never_reserved_words() -> None:
    for table in ddl.TABLES:
        assert table.name not in SQL_RESERVED_WORDS
        for column in table.columns:
            assert column.name.lstrip("_") not in SQL_RESERVED_WORDS, (table.name, column.name)
        for columns in table.indexes:
            assert len(ddl.index_name(table, columns)) <= 63


def test_reserved_words_cover_both_engines(store: SqlStore, engine: str) -> None:
    if engine == "duckdb":
        rows = store._fetch_all(
            "/*mdm:small*/ SELECT keyword_name FROM duckdb_keywords() WHERE keyword_category = 'reserved'"
        )
    else:
        rows = store._fetch_all("/*mdm:small*/ SELECT word FROM pg_get_keywords() WHERE catcode = 'R'")
    reserved = {r[0] for r in rows}
    assert reserved
    assert reserved <= SQL_RESERVED_WORDS, sorted(reserved - SQL_RESERVED_WORDS)


@pytest.mark.parametrize("word", ["order", "group", "user", "limit", "end", "at"])
def test_a_reserved_word_is_refused_as_a_name(person_model: EntityModel, word: str) -> None:
    doc = person_model.to_dict()
    doc["attributes"] = [*doc["attributes"], {"name": word, "type": "text"}]
    with pytest.raises(ModelError):
        EntityModel.from_dict(doc)
    doc = person_model.to_dict()
    doc["entity"] = word
    with pytest.raises(ModelError):
        EntityModel.from_dict(doc)


@pytest.mark.parametrize("name", ["change\n", "person\n", "family_name\n"])
def test_a_name_with_a_trailing_newline_is_refused(person_model: EntityModel, name: str) -> None:
    doc = person_model.to_dict()
    doc["entity"] = name
    with pytest.raises(ModelError):
        EntityModel.from_dict(doc)
    doc = person_model.to_dict()
    doc["attributes"] = [*doc["attributes"], {"name": name, "type": "text"}]
    with pytest.raises(ModelError):
        EntityModel.from_dict(doc)


def test_entity_table_has_no_reference_column(person_model: EntityModel) -> None:
    table = ddl.entity_table(person_model)
    names = table.column_names()
    assert "employer" not in names
    assert names[:3] == ddl.ENTITY_LEAD_COLUMNS
    assert names[-5:] == ddl.ENTITY_TRAIL_COLUMNS
    assert names[3:-5] == tuple(a.name for a in person_model.column_attributes())
    assert table.column("addresses").type == "json"
    assert table.column("birth_date").type == "date"
    assert ddl.attribute_type("integer", False) == "bigint"
    assert ddl.attribute_type("number", False) == "numeric"
    assert ddl.attribute_type("timestamp", False) == "timestamptz"
    assert ddl.attribute_type("text", True) == "json"
    with pytest.raises(ValueError):
        ddl.attribute_type("reference", False)


def test_entity_table_is_created_with_its_view_and_counter(
    store: SqlStore, person_model: EntityModel
) -> None:
    run = store.ensure_entity_tables(person_model)
    assert any("CREATE TABLE" in s for s in run)
    assert any("CREATE OR REPLACE VIEW" in s for s in run)
    assert store.table_columns("core", "person") == list(ddl.entity_table(person_model).column_names())
    assert store.table_columns("read", "person") == store.table_columns("core", "person")
    rows = store._fetch_all(
        f"/*mdm:small*/ SELECT code, last_value FROM {store.t('hub', 'id_counter')} WHERE name = ?",
        ["person"],
    )
    assert rows == [("PER", 0)]
    again = store.ensure_entity_tables(person_model)
    assert not any("CREATE TABLE" in s or "ADD COLUMN" in s for s in again)


def test_masked_view_masks_personal_columns(store: SqlStore, person_model: EntityModel) -> None:
    store.ensure_entity_tables(person_model)
    values = {
        "given_name": "Given01",
        "family_name": "Family01",
        "birth_date": date(1990, 4, 1),
        "email": "given01@example.org",
        "city": "Northtown",
        "country": "ZZ",
        "addresses": [{"kind": "home", "line1": "1 Test Road"}],
        "left_on": date(2025, 1, 31),
    }
    with store.commit_scope():
        store.write_golden("person", [(None, golden("PER-000001", values))])
    masked = store.masked_rows("person", ["PER-000001"])["PER-000001"]
    assert masked["given_name"] == "G***"
    assert masked["family_name"] == "F***"
    assert masked["email"] == "g***"
    assert masked["birth_date"] is None
    assert masked["addresses"] is None
    assert masked["phone"] is None  # a NULL stays NULL, never "***"
    assert masked["city"] == "Northtown"
    assert masked["left_on"] == date(2025, 1, 31)
    assert masked["master_id"] == "PER-000001"
    current = store.current_rows("person", ["PER-000001"])["PER-000001"]
    assert current["given_name"] == "Given01"
    assert current["addresses"] == [{"kind": "home", "line1": "1 Test Road"}]


def test_adding_a_column_keeps_rows_and_views(store: SqlStore, person_model: EntityModel) -> None:
    store.ensure_entity_tables(person_model)
    with store.commit_scope():
        store.write_golden(
            "person", [(None, golden("PER-000001", {"given_name": "Given01", "city": "Northtown"}))]
        )
    v2 = model_with(
        person_model,
        {"name": "nickname", "type": "text", "masking": "personal"},
        {"name": "visits", "type": "integer"},
        version=2,
    )
    run = store.ensure_entity_tables(v2)
    assert sum("ADD COLUMN" in s for s in run) == 2
    columns = store.table_columns("core", "person")
    assert columns[-2:] == ["nickname", "visits"]
    assert columns[:-2] == list(ddl.entity_table(person_model).column_names())
    assert store.table_columns("read", "person") == columns
    row = store.golden("person", ["PER-000001"])["PER-000001"]
    assert row.values["given_name"] == "Given01"
    assert row.values["nickname"] is None
    with store.commit_scope():
        store.write_golden(
            "person",
            [
                (
                    row,
                    GoldenRow(
                        "person",
                        "PER-000001",
                        "active",
                        None,
                        {**row.values, "nickname": "Nick01", "visits": 3},
                        2,
                        2,
                        False,
                    ),
                )
            ],
        )
    masked = store.masked_rows("person", ["PER-000001"])["PER-000001"]
    assert masked["nickname"] == "N***"
    assert masked["visits"] == 3


def test_landing_is_created_only_in_the_local_mode(engine_settings: Settings, engine: str) -> None:
    if engine == "lakebase":
        pytest.skip("the live run prefix may create the landing table")
    shared = open_store(engine_settings.with_(platform_signals=("DATABRICKS_APP_PORT",)))
    try:
        assert not shared.settings.local_mode
        with pytest.raises(PlatformRefused):
            shared.init_schema(create_landing=True)
        shared.init_schema(create_landing=False)
        schemas = schemas_of(shared)
        assert ddl.schema_name(shared.prefix, "landing") not in schemas
        assert ddl.schema_name(shared.prefix, "core") in schemas
        with pytest.raises(PlatformRefused):
            shared.drop_all()
    finally:
        shared.close()
        if engine != "duckdb":
            local = open_store(engine_settings)
            local.drop_all()
            local.close()


@pytest.mark.postgres
def test_a_plain_postgres_is_not_local(request: pytest.FixtureRequest) -> None:
    dsn = request.getfixturevalue("postgres_dsn")
    settings = Settings(backend="postgres", postgres_dsn=dsn, schema_prefix="tplain01")
    assert not settings.local_mode
    store = open_store(settings)
    try:
        with pytest.raises(PlatformRefused):
            store.init_schema(create_landing=True)
        assert not schemas_of(store)
    finally:
        store.close()


def test_the_published_ddl_runs_on_a_fresh_prefix(make_store: Any, engine: str) -> None:
    fresh = make_store(engine, init=False)
    statements = ddl.all_ddl(fresh.prefix, fresh.engine)
    assert statements[0].startswith("CREATE SCHEMA IF NOT EXISTS")
    with guard.ddl_scope():
        for statement in statements:
            fresh._execute(statement)
    assert fresh.table_columns("landing", "source_change") == list(
        ddl.table("landing", "source_change").column_names()
    )
    assert fresh.table_columns("read", "relationship") == list(
        ddl.table("core", "relationship").column_names()
    )


def test_the_landing_ddl_for_the_integration_platform() -> None:
    statements = ddl.all_ddl("mdm", "postgres", ["landing"])
    assert statements[0] == "CREATE SCHEMA IF NOT EXISTS mdm_landing"
    assert statements[1] == "CREATE SEQUENCE IF NOT EXISTS mdm_landing.landing_seq CACHE 1"
    assert "CREATE TABLE IF NOT EXISTS mdm_landing.source_change" in statements[2]
    assert "DEFAULT nextval('mdm_landing.landing_seq')" in statements[2]
    assert "payload jsonb NOT NULL" in statements[2]
    assert "UNIQUE (landing_seq)" in statements[2]
    assert len(statements) == 3
    duck = ddl.all_ddl("mdm", "duckdb", ["landing"])
    assert duck[1] == "CREATE SEQUENCE IF NOT EXISTS mdm_landing.landing_seq"
    assert "payload JSON NOT NULL" in duck[2]
    with pytest.raises(ValueError):
        ddl.all_ddl("mdm", "postgres", ["nothing"])


def test_grants_for_the_listener_interface() -> None:
    statements = ddl.grants_sql(
        "mdm",
        hub_role="hub-app",
        reader_roles=["listener"],
        notifier_role="notifier",
        people_roles=["stewards"],
    )
    assert 'GRANT USAGE ON SCHEMA mdm_core TO "listener"' in statements
    assert 'GRANT SELECT ON ALL TABLES IN SCHEMA mdm_core TO "listener"' in statements
    # the change notifier reads the commit log and nothing else: no record, no default privileges
    assert [s for s in statements if '"notifier"' in s] == [
        'GRANT USAGE ON SCHEMA mdm_core TO "notifier"',
        'GRANT SELECT ON mdm_core.commit_log TO "notifier"',
    ]
    assert (
        'ALTER DEFAULT PRIVILEGES FOR ROLE "hub-app" IN SCHEMA mdm_core GRANT SELECT ON TABLES TO "listener"'
        in statements
    )
    assert not any("mdm_core" in s and '"stewards"' in s for s in statements)
    assert 'GRANT SELECT ON ALL TABLES IN SCHEMA mdm_read TO "stewards"' in statements
    assert not any("mdm_read" in s and '"listener"' in s for s in statements)
    assert ddl.quote_role('odd"name') == '"odd""name"'


def test_a_name_a_statement_is_built_from_is_a_plain_identifier() -> None:
    assert ddl.ident("person") == "person"
    for bad in ("change\n", "Person", "a-b", "x;drop", "", "a" * 64, "1st"):
        with pytest.raises(ValueError):
            ddl.ident(bad)
    with pytest.raises(ValueError):
        ddl.schema_name("mdm\n", "core")


def test_types_per_engine() -> None:
    assert ddl.sql_type("json", "duckdb") == "JSON"
    assert ddl.sql_type("json", "postgres") == "jsonb"
    assert ddl.sql_type("numeric", "postgres") == "numeric(38,10)"
    assert ddl.logical_of("DECIMAL(38,10)") == "numeric"
    assert ddl.logical_of("timestamp with time zone") == "timestamptz"
    with pytest.raises(ValueError):
        ddl.sql_type("uuid", "postgres")
    with pytest.raises(ValueError):
        ddl.logical_of("uuid")


def test_timestamps_come_back_in_utc(store: SqlStore) -> None:
    store.save_entity_model("person", 1, "draft", {"entity": "person"}, "tester")
    ((version, status, created),) = store.entity_model_versions("person")
    assert (version, status) == (1, "draft")
    assert created.tzinfo is not None and created.utcoffset() == UTC.utcoffset(None)
    assert abs((datetime.now(UTC) - created).total_seconds()) < 120
