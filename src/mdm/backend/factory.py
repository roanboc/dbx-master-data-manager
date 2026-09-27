"""Opening the store the settings name (owner: BACKEND)."""

from __future__ import annotations

from mdm.backend.lakebase_auth import Credentials, LakebaseCredentials
from mdm.backend.store import SqlStore
from mdm.config import Settings
from mdm.models.errors import ConfigError


def open_store(settings: Settings, *, credentials: Credentials | None = None) -> SqlStore:
    """`DuckDBStore` for backend "duckdb"; `PostgresStore` for "postgres", signed in through
    `LakebaseCredentials(settings.lakebase_endpoint)` when an endpoint is set and no `credentials` are given.

    Opening creates nothing: `init_schema` does. Each engine module is imported only when its engine
    is asked for.
    """
    if settings.backend == "duckdb":
        from mdm.backend.duckdb_engine import DuckDBStore

        return DuckDBStore(settings)
    if settings.backend == "postgres":
        from mdm.backend.postgres_engine import PostgresStore

        if credentials is None and settings.lakebase_endpoint:
            credentials = LakebaseCredentials(settings.lakebase_endpoint)
        return PostgresStore(settings, credentials)
    raise ConfigError("bad_setting", variable="MDM_BACKEND")
