"""The Postgres engine: Lakebase on the platform, any Postgres in tests (owner: BACKEND, B.5.3).

A `psycopg_pool.ConnectionPool(conninfo, kwargs=<callable>, min_size=1,
max_size=pool_max, max_lifetime=connection_max_age, configure=<SET TIME ZONE
'UTC'>)`; every connection is in autocommit, and a transaction binds one
connection to the current context (`contextvars`). `?` markers become `%s`
outside literals and `%` is doubled when parameters are bound (`to_pg`). The
commit-order lock is `pg_advisory_xact_lock(advisory_key(prefix + ":commit"))`
after `SET TRANSACTION ISOLATION LEVEL READ COMMITTED`; the run lease is
`pg_try_advisory_lock` on a connection taken out of the pool for the run. A
statement outside a transaction that fails because the connection is gone is
retried once, never inside a transaction.

Row source: `jsonb_array_elements(CAST(? AS JSONB)) AS rs(r)`.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg_pool import ConnectionPool, PoolTimeout

from mdm.backend.ddl import sql_type
from mdm.backend.lakebase_auth import Credentials, connection_kwargs
from mdm.backend.store import SqlStore
from mdm.config import Settings
from mdm.models.errors import MdmError

log = logging.getLogger(__name__)

_LITERAL_OR_MARK = re.compile(r"'(?:[^']|'')*'|(\?)")
#: what libpq says when the server or the network dropped the connection under us
_CONNECTION_GONE = (
    "closed",
    "terminat",
    "ssl syscall",
    "eof detected",
    "connection reset",
    "broken pipe",
    "could not receive",
    "not open",
    "server closed",
    "connection refused",
)
#: seconds to wait for the pool's first connection when the store opens
OPEN_TIMEOUT = 30.0


def to_pg(sql: str, params: Sequence[Any] | None = None) -> str:
    """`?` -> `%s` outside string literals; `%` doubled when `params` are bound (psycopg's own marker)."""
    if params:
        sql = sql.replace("%", "%%")
    return _LITERAL_OR_MARK.sub(lambda m: "%s" if m.group(1) else m.group(0), sql)


def advisory_key(text: str) -> int:
    """A signed 64-bit advisory-lock key: int(sha256(text).hexdigest()[:15], 16)."""
    return int(hashlib.sha256(text.encode()).hexdigest()[:15], 16)


def connection_gone(exc: BaseException) -> bool:
    """True when libpq says the server or the network dropped the connection (safe to retry once)."""
    if not isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError)):
        return False
    text = str(exc).lower()
    return any(word in text for word in _CONNECTION_GONE)


def _configure(conn: psycopg.Connection[Any]) -> None:
    conn.execute("SET TIME ZONE 'UTC'")


@dataclass
class _Binding:
    """The connection a context's open transaction runs on, and how deep it is nested."""

    conn: psycopg.Connection[Any]
    depth: int = 1


class PostgresStore(SqlStore):
    engine = "postgres"

    def __init__(self, settings: Settings, credentials: Credentials | None = None) -> None:
        """A pool on `settings.postgres_dsn` (or the PG* settings); on Lakebase, `credentials` sign in."""
        super().__init__(settings)
        self.credentials = credentials
        conninfo = "" if credentials is not None else settings.postgres_dsn
        self._tx: ContextVar[_Binding | None] = ContextVar(f"mdm_pg_tx_{id(self)}", default=None)
        self._pool: ConnectionPool[Any] = ConnectionPool(
            conninfo,
            kwargs=connection_kwargs(settings, credentials),
            min_size=1,
            max_size=max(settings.pool_max, 1),
            max_lifetime=float(settings.connection_max_age),
            configure=_configure,
            open=False,
            name=f"mdm-{settings.schema_prefix}",
            num_workers=2,
        )
        try:
            self._pool.open(wait=True, timeout=OPEN_TIMEOUT)
        except PoolTimeout:
            self._pool.close()
            raise MdmError("store_unreachable", engine="postgres") from None

    # ------------------------------------------------------------------ statements

    @staticmethod
    def _run_on(conn: psycopg.Connection[Any], sql: str, params: Sequence[Any], fetch: bool) -> Any:
        bound = list(params) if params else None
        with conn.cursor() as cur:
            cur.execute(to_pg(sql, bound), bound)
            if fetch:
                return cur.fetchall() if cur.description is not None else []
            return max(cur.rowcount, 0)

    def _run(self, sql: str, params: Sequence[Any], fetch: bool) -> Any:
        self._check(sql)
        binding = self._tx.get()
        if binding is not None:  # inside a transaction: its connection, and no retry
            return self._run_on(binding.conn, sql, params, fetch)
        for attempt in (1, 2):
            try:
                with self._pool.connection() as conn:
                    return self._run_on(conn, sql, params, fetch)
            except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
                if attempt == 2 or not connection_gone(exc):
                    raise
                log.warning("the connection was closed; retrying once on a new one")
        raise AssertionError("unreachable")

    def _execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        return int(self._run(sql, params, fetch=False))

    def _fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[tuple]:
        return list(self._run(sql, params, fetch=True))

    def _row_source(self) -> str:
        return "jsonb_array_elements(CAST(? AS JSONB)) AS rs(r)"

    def _row_value(self, index: int) -> str:
        return f"(rs.r->>{index})"

    # ------------------------------------------------------------------ transactions, the commit lock, leases

    def _in_transaction(self) -> bool:
        return self._tx.get() is not None

    @contextmanager
    def _engine_transaction(self) -> Iterator[None]:
        binding = self._tx.get()
        if binding is not None:  # join the open one
            binding.depth += 1
            try:
                yield
            finally:
                binding.depth -= 1
            return
        with self._pool.connection() as conn, conn.transaction():
            token = self._tx.set(_Binding(conn))
            try:
                yield
            finally:
                self._tx.reset(token)

    @contextmanager
    def _commit_lock(self) -> Iterator[None]:
        if self._tx.get() is not None:
            raise MdmError("nested_commit_scope")
        with self._engine_transaction():
            self._execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            self._fetch_all(
                "/*mdm:small*/ SELECT pg_advisory_xact_lock(?)", [advisory_key(f"{self.prefix}:commit")]
            )
            yield

    @contextmanager
    def _lease(self, name: str) -> Iterator[bool]:
        key = advisory_key(f"{self.prefix}:{name}")
        conn = self._pool.getconn()
        held = False
        try:
            sql = "/*mdm:small*/ SELECT pg_try_advisory_lock(?)"
            self._check(sql)
            held = bool(self._run_on(conn, sql, [key], fetch=True)[0][0])
            yield held
        finally:
            try:
                if held and not conn.closed:
                    unlock = "/*mdm:small*/ SELECT pg_advisory_unlock(?)"
                    self._check(unlock)
                    self._run_on(conn, unlock, [key], fetch=True)
            except psycopg.Error:  # the session is gone, and its lock with it
                pass
            finally:
                self._pool.putconn(conn)

    # ------------------------------------------------------------------ types

    def _json_param(self) -> str:
        return "CAST(? AS JSONB)"

    def _decode_json(self, value: Any) -> Any:
        return value

    def _sql_type(self, logical: str) -> str:
        return sql_type(logical, "postgres")

    def _row_estimates(self, tables: Sequence[str]) -> dict[str, int]:
        if not tables:
            return {}
        schemas = sorted({t.split(".", 1)[0] for t in tables})
        marks = ", ".join("?" for _ in schemas)
        rows = self._fetch_all(
            "/*mdm:small*/ SELECT n.nspname, c.relname, c.reltuples, COALESCE(s.n_live_tup, 0) "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid "
            f"WHERE c.relkind IN ('r', 'p') AND n.nspname IN ({marks}) ORDER BY n.nspname, c.relname",
            schemas,
        )
        wanted = set(tables)
        out: dict[str, int] = {}
        for schema, name, reltuples, live in rows:
            qualified = f"{schema}.{name}"
            if qualified in wanted:
                out[qualified] = int(reltuples) if reltuples is not None and reltuples >= 0 else int(live)
        return out

    def close(self) -> None:
        self._pool.close()

    def __repr__(self) -> str:
        return f"PostgresStore(prefix={self.prefix!r}, lakebase={self.credentials is not None})"
