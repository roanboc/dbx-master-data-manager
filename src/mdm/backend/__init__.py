"""The store: one SQL text for DuckDB and Postgres/Lakebase (owner: BACKEND, B.5).

`factory.open_store(settings)` opens the store the settings name. All SQL lives
in `store.SqlStore`; `duckdb_engine` and `postgres_engine` add only the hooks
that differ; `guard` refuses writes outside their scope; `lakebase_auth` is the
only touchpoint with the Databricks SDK. `mdm.backend` may import
`mdm.models`, `mdm.config` and `mdm.capacity`, never `mdm.engine`.
"""
