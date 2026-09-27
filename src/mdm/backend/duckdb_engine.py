"""The DuckDB engine: the local mode's store (owner: BACKEND, B.5.3).

One `duckdb.connect(path)` per store, sessions in UTC (`SET TimeZone = 'UTC'`;
DuckDB needs `pytz` to return TIMESTAMPTZ values). One `threading.RLock` per
database file (per store for ":memory:"), held for a whole transaction (taken
at the outermost `transaction()`, released at commit or rollback) and per
statement outside one; the commit lock is that lock, taken before BEGIN, so the
snapshot follows the lock. The run lease is a non-blocking `threading.Lock` per
database file and lease name.

Row source: `(SELECT unnest(json_transform(CAST(? AS JSON), '[["VARCHAR"]]')) AS r) AS rs`,
each row parsed once into a list of text read as `rs.r[i + 1]`: measured 1.3–1.5x faster
than `(r->>i)` on a 21-column row, which reparses the row for every column.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb

from mdm.backend.ddl import sql_type
from mdm.backend.store import SqlStore
from mdm.config import Settings
from mdm.models.errors import MdmError

_REGISTRY = threading.Lock()
_FILE_LOCKS: dict[str, threading.RLock] = {}
_LEASES: dict[tuple[str, str], threading.Lock] = {}
#: each row parsed once into a list of text (`->>` would reparse the row for every column)
_ROW_SOURCE = "(SELECT unnest(json_transform(CAST(? AS JSON), '[[\"VARCHAR\"]]')) AS r) AS rs"


def _file_lock(key: str) -> threading.RLock:
    with _REGISTRY:
        return _FILE_LOCKS.setdefault(key, threading.RLock())


def _lease_lock(key: str, name: str) -> threading.Lock:
    with _REGISTRY:
        return _LEASES.setdefault((key, name), threading.Lock())


class DuckDBStore(SqlStore):
    engine = "duckdb"

    def __init__(self, settings: Settings) -> None:
        """Opens `settings.duckdb_path` (":memory:" allowed; a file's directory is created)."""
        super().__init__(settings)
        path = settings.duckdb_path
        if path == ":memory:":
            self._key = f":memory:{id(self)}"
        else:
            file = Path(path)
            # the store holds source records: on a shared machine only its owner reads the directory
            file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._key = str(file.resolve())
        self.path = path
        self._lock = _file_lock(self._key)
        self._conn = duckdb.connect(path)
        self._tx_depth = 0
        self._tx_thread: int | None = None
        with self._lock:
            self._conn.execute("SET TimeZone = 'UTC'")

    def server_version(self) -> str:
        return f"DuckDB {duckdb.__version__}"

    # ------------------------------------------------------------------ statements

    def _execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        self._check(sql)
        with self._lock:
            result = self._conn.execute(sql, list(params))
            description = result.description
            if description and len(description) == 1 and description[0][0] == "Count":
                row = result.fetchone()
                return int(row[0]) if row else 0
            return 0

    def _fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[tuple]:
        self._check(sql)
        with self._lock:
            return self._conn.execute(sql, list(params)).fetchall()

    def _row_source(self) -> str:
        return _ROW_SOURCE

    def _row_value(self, index: int) -> str:
        return f"rs.r[{index + 1}]"

    # ------------------------------------------------------------------ transactions, the commit lock, leases

    def _in_transaction(self) -> bool:
        return self._tx_depth > 0 and self._tx_thread == threading.get_ident()

    @contextmanager
    def _engine_transaction(self) -> Iterator[None]:
        with self._lock:
            if self._tx_depth:  # this thread holds the lock, so the open transaction is its own: join it
                self._tx_depth += 1
                try:
                    yield
                finally:
                    self._tx_depth -= 1
                return
            self._conn.execute("BEGIN TRANSACTION")
            self._tx_depth = 1
            self._tx_thread = threading.get_ident()
            try:
                yield
            except BaseException:
                self._tx_depth = 0
                self._tx_thread = None
                self._conn.execute("ROLLBACK")
                raise
            self._tx_depth = 0
            self._tx_thread = None
            self._conn.execute("COMMIT")

    @contextmanager
    def _commit_lock(self) -> Iterator[None]:
        with self._lock:  # the lock first, then BEGIN: the snapshot follows the lock
            if self._tx_depth:
                raise MdmError("nested_commit_scope")
            with self._engine_transaction():
                yield

    @contextmanager
    def _lease(self, name: str) -> Iterator[bool]:
        lock = _lease_lock(self._key, name)
        held = lock.acquire(blocking=False)
        try:
            yield held
        finally:
            if held:
                lock.release()

    # ------------------------------------------------------------------ types

    def _json_param(self) -> str:
        return "CAST(? AS JSON)"

    def _decode_json(self, value: Any) -> Any:
        return json.loads(value) if isinstance(value, str) else value

    def _sql_type(self, logical: str) -> str:
        return sql_type(logical, "duckdb")

    def _row_estimates(self, tables: Sequence[str]) -> dict[str, int]:
        if not tables:
            return {}
        sql = "/*mdm:aggregate*/ " + " UNION ALL ".join(
            f"SELECT '{name}' AS name, COUNT(*) AS n FROM {name}" for name in tables
        )
        return {name: int(n) for name, n in self._fetch_all(sql)}

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except duckdb.Error:
                pass

    def __repr__(self) -> str:
        return f"DuckDBStore(path={self.path!r}, prefix={self.prefix!r})"
