"""Settings, read from the environment only (no `.env` file).

`local_mode` is the single test for every local-only action (decision 13):
personas, the simulator, `mdm demo reset` and creating the landing tables. It
is decided by the store, not by where the process runs: a laptop pointed at
Lakebase has `lakebase_endpoint` set and is not local; a plain Postgres is not
local either unless `MDM_ALLOW_PERSONAS=1` marks it as a test database, and
the store then opens only when that database is on this machine (a unix
socket or a loopback address) and is not Lakebase's `databricks_postgres`.
"""

from __future__ import annotations

import dataclasses
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from mdm.models.errors import ConfigError
from mdm.models.tasks import TASK_KINDS
from mdm.models.workbench import FALLBACK_SERVICE_HOURS

#: variables a Databricks App or runtime sets; any of them present makes the store shared.
#: DATABRICKS_CLIENT_ID is not one: it is the normal way to run a service principal from a laptop.
PLATFORM_VARIABLES = ("DATABRICKS_APP_NAME", "DATABRICKS_APP_PORT", "DATABRICKS_RUNTIME_VERSION")
BACKENDS = ("duckdb", "postgres")
AGENT_PROVIDERS = ("auto", "endpoint", "stub")
#: whether the workbench's process flushes the undo tray itself: auto = on a local store only
TRAY_WORKER_MODES = ("auto", "on", "off")
#: hours to decide a task, per kind, until the governance policy of initiative 4 sets them (adopted)
DEFAULT_SLA_HOURS: tuple[tuple[str, int], ...] = (
    ("review", 8),
    ("possible_duplicate", 24),
    ("held", 8),
    ("exception", 24),
    ("orphan", 72),
    ("unresolved_reference", 72),
)
SCHEMA_PREFIX_RE = re.compile(r"[a-z][a-z0-9_]{0,30}")  # matched whole (fullmatch): no trailing newline
#: the TLS modes a connection that carries a Lakebase token over a network may use: none sends it in clear
SAFE_SSLMODES = ("require", "verify-ca", "verify-full")
#: hosts no network lies between: a unix socket directory, or loopback
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
#: the smallest pool: the arrival lease holds one connection for the whole run, statements need another
POOL_MIN = 2
_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"", "0", "false", "no", "off"})


@dataclass(frozen=True)
class Settings:
    backend: str = "duckdb"  # MDM_BACKEND: duckdb | postgres
    duckdb_path: str = ".mdm/mdm.duckdb"  # MDM_DUCKDB_PATH (":memory:" allowed)
    postgres_dsn: str = field(default="", repr=False)  # MDM_POSTGRES_DSN (may hold a password); empty = PG*
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
    pool_max: int = 8  # MDM_POOL_MAX, at least POOL_MIN
    connection_max_age: int = 2700  # MDM_CONNECTION_MAX_AGE seconds (45 min, under the 1 h token)
    agent_provider: str = "auto"  # MDM_AGENT_PROVIDER: auto | endpoint | stub
    agent_endpoint: str = ""  # MDM_AGENT_ENDPOINT
    throttle_rows_per_hour: int = 0  # MDM_THROTTLE_ROWS_PER_HOUR; 0 = off (bulk commits)
    gap_timeout_seconds: int = 600  # MDM_GAP_TIMEOUT_SECONDS
    undo_seconds: int = 60  # MDM_UNDO_SECONDS: how long a staged decision waits in the undo tray
    claim_minutes: int = 10  # MDM_CLAIM_MINUTES: a claim lapses by itself after this
    sla_hours: tuple[tuple[str, int], ...] = DEFAULT_SLA_HOURS  # MDM_SLA_HOURS "review=8,held=8,…"
    close_call_points: float = 10.0  # MDM_CLOSE_CALL_POINTS: the top two candidates this close need a choice
    tray_worker: str = "auto"  # MDM_TRAY_WORKER: auto (on a local store) | on | off
    ui_port: int = 8050  # MDM_UI_PORT
    app_port: int = 0  # DATABRICKS_APP_PORT: the platform's own, read and never set; 0 = not in an App
    platform_signals: tuple[str, ...] = ()  # set by from_env: the PLATFORM_VARIABLES present

    @property
    def shared_store(self) -> bool:
        """The store may be the operational database."""
        return bool(self.lakebase_endpoint) or bool(self.platform_signals)

    @property
    def local_mode(self) -> bool:
        """Personas, the simulator, reset and landing DDL allowed.

        On Postgres, `MDM_ALLOW_PERSONAS=1` is honoured only for a server on this machine: the store
        refuses to open otherwise (`PostgresStore`, `personas_need_a_local_postgres`)."""
        return not self.shared_store and (self.backend == "duckdb" or self.allow_personas)

    @property
    def in_databricks_app(self) -> bool:
        """A Databricks App runs this process: the one test for listening beyond loopback and for reading
        the user the platform forwards (decision 21)."""
        return (
            "DATABRICKS_APP_NAME" in self.platform_signals or "DATABRICKS_APP_PORT" in self.platform_signals
        )

    @property
    def tray_worker_on(self) -> bool:
        """The workbench's process flushes the undo tray itself: `on`, or `auto` on a local store."""
        return self.tray_worker == "on" or (self.tray_worker == "auto" and self.local_mode)

    def service_level_hours(self, kind: str) -> int:
        """Hours to decide a task of `kind`; a kind the service levels do not name gets 24."""
        return dict(self.sla_hours).get(kind, FALLBACK_SERVICE_HOURS)

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

        def number(name: str, default: float, minimum: float, maximum: float) -> float:
            raw = text(name)
            if not raw:
                return default
            try:
                value = float(raw)
            except ValueError:
                raise ConfigError("bad_setting", variable=name) from None
            if not minimum <= value <= maximum:  # also refuses nan
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
            "pool_max": integer("MDM_POOL_MAX", cls.pool_max, minimum=POOL_MIN),
            "connection_max_age": integer("MDM_CONNECTION_MAX_AGE", cls.connection_max_age, minimum=60),
            "agent_provider": text("MDM_AGENT_PROVIDER", cls.agent_provider).lower() or cls.agent_provider,
            "agent_endpoint": text("MDM_AGENT_ENDPOINT"),
            "throttle_rows_per_hour": integer("MDM_THROTTLE_ROWS_PER_HOUR", cls.throttle_rows_per_hour),
            "gap_timeout_seconds": integer("MDM_GAP_TIMEOUT_SECONDS", cls.gap_timeout_seconds, minimum=1),
            "undo_seconds": integer("MDM_UNDO_SECONDS", cls.undo_seconds, minimum=1),
            "claim_minutes": integer("MDM_CLAIM_MINUTES", cls.claim_minutes, minimum=1),
            "sla_hours": _sla_hours(text("MDM_SLA_HOURS")),
            "close_call_points": number("MDM_CLOSE_CALL_POINTS", cls.close_call_points, 0.0, 100.0),
            "tray_worker": text("MDM_TRAY_WORKER", cls.tray_worker).lower() or cls.tray_worker,
            "ui_port": integer("MDM_UI_PORT", cls.ui_port, minimum=1),
            "app_port": integer("DATABRICKS_APP_PORT", cls.app_port),
            "platform_signals": tuple(name for name in PLATFORM_VARIABLES if name in env),
        }
        settings = cls(**values)
        settings.validate()
        return settings

    def validate(self) -> None:
        """`ConfigError` when the backend, the schema prefix, the agent provider or the pool size is not one
        allowed (the TLS mode is checked where the host is known: `lakebase_auth.tls_mode`)."""
        if self.backend not in BACKENDS:
            raise ConfigError("bad_setting", variable="MDM_BACKEND")
        if not SCHEMA_PREFIX_RE.fullmatch(self.schema_prefix):
            raise ConfigError("bad_setting", variable="MDM_SCHEMA_PREFIX")
        if self.agent_provider not in AGENT_PROVIDERS:
            raise ConfigError("bad_setting", variable="MDM_AGENT_PROVIDER")
        if self.pool_max < POOL_MIN:
            raise ConfigError("bad_setting", variable="MDM_POOL_MAX")
        if self.undo_seconds < 1:
            raise ConfigError("bad_setting", variable="MDM_UNDO_SECONDS")
        if self.claim_minutes < 1:
            raise ConfigError("bad_setting", variable="MDM_CLAIM_MINUTES")
        if not _sla_complete(self.sla_hours):
            raise ConfigError("bad_setting", variable="MDM_SLA_HOURS")
        if not 0.0 <= self.close_call_points <= 100.0:
            raise ConfigError("bad_setting", variable="MDM_CLOSE_CALL_POINTS")
        if self.tray_worker not in TRAY_WORKER_MODES:
            raise ConfigError("bad_setting", variable="MDM_TRAY_WORKER")
        if self.ui_port < 1:
            raise ConfigError("bad_setting", variable="MDM_UI_PORT")
        if self.app_port < 0:
            raise ConfigError("bad_setting", variable="DATABRICKS_APP_PORT")

    def with_(self, **changes: Any) -> Settings:
        """A copy with `changes` applied and validated (`dataclasses.replace`)."""
        settings = dataclasses.replace(self, **changes)
        settings.validate()
        return settings


def _sla_complete(hours: tuple[tuple[str, int], ...]) -> bool:
    """Every task kind has a whole number of hours, at least 1, and no kind is named twice."""
    kinds = [kind for kind, _ in hours]
    return (
        len(kinds) == len(set(kinds))
        and set(kinds) >= set(TASK_KINDS)
        and all(isinstance(h, int) and not isinstance(h, bool) and h >= 1 for _, h in hours)
    )


def _sla_hours(raw: str) -> tuple[tuple[str, int], ...]:
    """`MDM_SLA_HOURS` ("review=4,orphan=48") merged over the defaults, in task-kind order.

    A pair that is not `kind=hours`, a kind that is not a task kind, or hours below 1 is refused, naming
    the variable only (the text could be anything)."""
    merged = dict(DEFAULT_SLA_HOURS)
    for part in (p.strip() for p in raw.split(",")) if raw else ():
        if not part:
            continue
        kind, sep, hours = (s.strip() for s in part.partition("="))
        if not sep or kind not in TASK_KINDS:
            raise ConfigError("bad_setting", variable="MDM_SLA_HOURS")
        try:
            value = int(hours)
        except ValueError:
            raise ConfigError("bad_setting", variable="MDM_SLA_HOURS") from None
        if value < 1:
            raise ConfigError("bad_setting", variable="MDM_SLA_HOURS")
        merged[kind] = value
    return tuple((kind, merged[kind]) for kind in TASK_KINDS)
