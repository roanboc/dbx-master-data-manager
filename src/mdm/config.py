"""Settings, read from the environment only (no `.env` file).

`local_mode` is the single test for every local-only action (decision 13):
personas, the simulator, `mdm demo reset` and creating the landing tables. It
is decided by the store, not by where the process runs: a laptop pointed at
Lakebase has `lakebase_endpoint` set and is not local; a plain Postgres is not
local either unless `MDM_ALLOW_PERSONAS=1` marks it as a test database.
"""

from __future__ import annotations

import dataclasses
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from mdm.models.errors import ConfigError

#: variables a Databricks App or runtime sets; any of them present makes the store shared.
#: DATABRICKS_CLIENT_ID is not one: it is the normal way to run a service principal from a laptop.
PLATFORM_VARIABLES = ("DATABRICKS_APP_NAME", "DATABRICKS_APP_PORT", "DATABRICKS_RUNTIME_VERSION")
BACKENDS = ("duckdb", "postgres")
AGENT_PROVIDERS = ("auto", "endpoint", "stub")
SCHEMA_PREFIX_RE = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"", "0", "false", "no", "off"})


@dataclass(frozen=True)
class Settings:
    backend: str = "duckdb"  # MDM_BACKEND: duckdb | postgres
    duckdb_path: str = ".mdm/mdm.duckdb"  # MDM_DUCKDB_PATH (":memory:" allowed)
    postgres_dsn: str = ""  # MDM_POSTGRES_DSN, libpq URL or key=value; empty = PG* variables
    lakebase_endpoint: str = ""  # MDM_LAKEBASE_ENDPOINT projects/<p>/branches/<b>/endpoints/<e>
    pg_host: str = ""  # PGHOST
    pg_port: int = 5432  # PGPORT
    pg_database: str = ""  # PGDATABASE (Lakebase: databricks_postgres)
    pg_user: str = ""  # PGUSER
    pg_sslmode: str = ""  # PGSSLMODE
    schema_prefix: str = "mdm"  # MDM_SCHEMA_PREFIX, ^[a-z][a-z0-9_]{0,30}$
    models_dir: str = "models"  # MDM_MODELS_DIR
    role: str = ""  # MDM_ROLE: the local persona
    allow_personas: bool = (
        False  # MDM_ALLOW_PERSONAS=1: a test Postgres may take personas, run the simulator and reset
    )
    pool_max: int = 8  # MDM_POOL_MAX
    connection_max_age: int = 2700  # MDM_CONNECTION_MAX_AGE seconds (45 min, under the 1 h token)
    agent_provider: str = "auto"  # MDM_AGENT_PROVIDER: auto | endpoint | stub
    agent_endpoint: str = ""  # MDM_AGENT_ENDPOINT
    throttle_rows_per_hour: int = 0  # MDM_THROTTLE_ROWS_PER_HOUR; 0 = off (bulk commits)
    gap_timeout_seconds: int = 600  # MDM_GAP_TIMEOUT_SECONDS
    platform_signals: tuple[str, ...] = ()  # set by from_env: the PLATFORM_VARIABLES present

    @property
    def shared_store(self) -> bool:
        """The store may be the operational database."""
        return bool(self.lakebase_endpoint) or bool(self.platform_signals)

    @property
    def local_mode(self) -> bool:
        """Personas, the simulator, reset and landing DDL allowed."""
        return not self.shared_store and (self.backend == "duckdb" or self.allow_personas)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Settings from `env` (default `os.environ`); `ConfigError` names the first malformed variable.

        `MDM_BACKEND` defaults to `postgres` when `MDM_LAKEBASE_ENDPOINT` or `MDM_POSTGRES_DSN` is set,
        and to `duckdb` otherwise.
        """
        env = os.environ if env is None else env

        def text(name: str, default: str = "") -> str:
            return env.get(name, default).strip()

        def integer(name: str, default: int, minimum: int = 0) -> int:
            raw = text(name)
            if not raw:
                return default
            try:
                value = int(raw)
            except ValueError:
                raise ConfigError("bad_setting", variable=name) from None
            if value < minimum:
                raise ConfigError("bad_setting", variable=name)
            return value

        def flag(name: str) -> bool:
            raw = text(name).lower()
            if raw in _TRUE:
                return True
            if raw in _FALSE:
                return False
            raise ConfigError("bad_setting", variable=name)

        lakebase_endpoint = text("MDM_LAKEBASE_ENDPOINT")
        postgres_dsn = text("MDM_POSTGRES_DSN")
        backend = text("MDM_BACKEND").lower() or (
            "postgres" if lakebase_endpoint or postgres_dsn else "duckdb"
        )
        values: dict[str, Any] = {
            "backend": backend,
            "duckdb_path": text("MDM_DUCKDB_PATH", cls.duckdb_path) or cls.duckdb_path,
            "postgres_dsn": postgres_dsn,
            "lakebase_endpoint": lakebase_endpoint,
            "pg_host": text("PGHOST"),
            "pg_port": integer("PGPORT", cls.pg_port, minimum=1),
            "pg_database": text("PGDATABASE"),
            "pg_user": text("PGUSER"),
            "pg_sslmode": text("PGSSLMODE"),
            "schema_prefix": text("MDM_SCHEMA_PREFIX", cls.schema_prefix) or cls.schema_prefix,
            "models_dir": text("MDM_MODELS_DIR", cls.models_dir) or cls.models_dir,
            "role": text("MDM_ROLE"),
            "allow_personas": flag("MDM_ALLOW_PERSONAS"),
            "pool_max": integer("MDM_POOL_MAX", cls.pool_max, minimum=1),
            "connection_max_age": integer("MDM_CONNECTION_MAX_AGE", cls.connection_max_age, minimum=60),
            "agent_provider": text("MDM_AGENT_PROVIDER", cls.agent_provider).lower() or cls.agent_provider,
            "agent_endpoint": text("MDM_AGENT_ENDPOINT"),
            "throttle_rows_per_hour": integer("MDM_THROTTLE_ROWS_PER_HOUR", cls.throttle_rows_per_hour),
            "gap_timeout_seconds": integer("MDM_GAP_TIMEOUT_SECONDS", cls.gap_timeout_seconds, minimum=1),
            "platform_signals": tuple(name for name in PLATFORM_VARIABLES if name in env),
        }
        settings = cls(**values)
        settings.validate()
        return settings

    def validate(self) -> None:
        """`ConfigError` when the backend, the schema prefix or the agent provider is not one allowed."""
        if self.backend not in BACKENDS:
            raise ConfigError("bad_setting", variable="MDM_BACKEND")
        if not SCHEMA_PREFIX_RE.match(self.schema_prefix):
            raise ConfigError("bad_setting", variable="MDM_SCHEMA_PREFIX")
        if self.agent_provider not in AGENT_PROVIDERS:
            raise ConfigError("bad_setting", variable="MDM_AGENT_PROVIDER")

    def with_(self, **changes: Any) -> Settings:
        """A copy with `changes` applied and validated (`dataclasses.replace`)."""
        settings = dataclasses.replace(self, **changes)
        settings.validate()
        return settings
