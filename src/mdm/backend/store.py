"""`SqlStore`: every read and write of the hub, written once for both engines (owner: BACKEND, B.5.5).

All SQL lives here. An engine (`duckdb_engine.DuckDBStore`,
`postgres_engine.PostgresStore`) adds only the hooks that differ (B.5.3).

Rules every method follows:

- Every insert, upsert and keyed read binds one JSON document per chunk as the
  row source (`_row_source()`); multi-row VALUES and executemany are not used.
  A column is read `CAST(<value i> AS <type>)`, where `_row_value(i)` is
  `(rs.r->>i)` on Postgres, always parenthesised, and `rs.r[i + 1]` on DuckDB,
  whose row source parses each row once into a list of text; a JSON value is
  bound double-encoded and read `CAST(<value i> AS JSON)`, so a SQL NULL stays
  NULL.
- Every list-returning method takes `limit`/`after` or keys, never scans
  unbounded, and returns rows in a stated order.
- Every statement opens with a tag the capacity test reads: `/*mdm:keyed*/`,
  `/*mdm:paged*/` (has LIMIT), `/*mdm:aggregate*/` or `/*mdm:small*/`. DDL
  carries none.
- The store never uses jsonb's question-mark operators (the Postgres engine
  rewrites every question mark outside a literal into a parameter).
- `_execute` and `_fetch_all` run `guard.check_statement` on every statement.

Values read back: JSON columns come back as JSON types (a date stored inside a
JSON document is ISO text); `numeric` as `Decimal`; `timestamptz` as an aware
`datetime` in UTC; every other type as its Python type.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any

from mdm import capacity
from mdm.backend import ddl, guard
from mdm.backend.ddl import Column, Table, schema_name
from mdm.config import Settings
from mdm.models.authority import AccessRow, Actor
from mdm.models.batch import (
    BATCH_STATUSES,
    BEFORE_TRAY,
    BULK_STATUSES,
    ITEM_ROLES,
    OPEN_BATCH_STATUSES,
    SAMPLE_OUTCOMES,
    SPLIT_STATUS,
    Batch,
    BatchChunk,
    BatchChunkWrite,
    BatchItem,
    BatchSampleWrite,
)
from mdm.models.batch import signature_key as batch_signature_key
from mdm.models.canonical import canonical_json, iso, utcnow
from mdm.models.changes import ChangeRow, ChangeSet, CommitLogRow, WorkWrites
from mdm.models.entity_model import EntityModel
from mdm.models.errors import Conflict, GuardError, MdmError, NotFound, PlatformRefused
from mdm.models.match import PairScore
from mdm.models.quality import (
    AUTO_BAND,
    BULK_PREFIX,
    SAMPLE_ORIGINS,
    AgreementRow,
    BreakerState,
    QualitySample,
    SampleReview,
)
from mdm.models.records import (
    Gap,
    GoldenRow,
    LandingRow,
    ReaderState,
    RegisteredId,
    Reject,
    RelationshipRow,
    RetiredRow,
    RuleResult,
    SourceChange,
    SourceKey,
    SourceState,
    StewardValue,
    XrefRow,
)
from mdm.models.safety import SAFE_TEXT_RE, safe, safe_signature
from mdm.models.tasks import Task
from mdm.models.workbench import MatchLabel, TaskQuery, TrayEntry

_TENTH = Decimal("1E-10")
_T = ddl.table
#: every status a batch review may hold, the only ones a statement renders as literals
_ITEM_STATUSES = frozenset((*SAMPLE_OUTCOMES, *BULK_STATUSES, SPLIT_STATUS))
#: the bands a signature's agreement is kept under (quality_agreement): the pair's band, or '' for none
_AGREEMENT_BANDS = ("auto", "review", "distinct", "")


# ---------------------------------------------------------------------------------------------- encoding


def new_redaction_id() -> str:
    """A random redaction ID, `RD-` and 20 hex digits."""
    return "RD-" + secrets.token_hex(10)


def _decimal_text(value: Any) -> str:
    if isinstance(value, bool):
        raise TypeError("numeric column: bool")
    try:
        number = Decimal(repr(value)) if isinstance(value, float) else Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise TypeError(f"numeric column: {type(value).__name__}") from None
    if not number.is_finite():
        raise ValueError("numeric column: not a finite number")
    return str(number.quantize(_TENTH, rounding=ROUND_HALF_EVEN))


def _utc(value: datetime) -> datetime:
    return (value if value.tzinfo is not None else value.replace(tzinfo=UTC)).astimezone(UTC)


def encode(value: Any, logical: str) -> Any:
    """A Python value as it goes into a row document for a column of logical type `logical`."""
    if value is None:
        return None
    if logical == "json":
        return canonical_json(value)
    if logical == "text":
        if isinstance(value, str):
            return value
        if isinstance(value, (date, datetime)):
            return iso(value)
        if isinstance(value, (bool, dict, list, tuple)):
            raise TypeError(f"text column: {type(value).__name__}")
        return str(value)
    if logical in ("bigint", "int"):
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, (float, Decimal)) and value == int(value):
            return int(value)
        if isinstance(value, str):
            return int(value)
        raise TypeError(f"{logical} column: {type(value).__name__}")
    if logical == "numeric":
        return _decimal_text(value)
    if logical == "boolean":
        if isinstance(value, bool):
            return value
        raise TypeError(f"boolean column: {type(value).__name__}")
    if logical == "date":
        if isinstance(value, datetime):
            return _utc(value).date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, str):
            return date.fromisoformat(value).isoformat()
        raise TypeError(f"date column: {type(value).__name__}")
    if logical == "timestamptz":
        if isinstance(value, datetime):
            return iso(value)
        if isinstance(value, date):
            return iso(datetime(value.year, value.month, value.day, tzinfo=UTC))
        if isinstance(value, str):
            return iso(datetime.fromisoformat(value))
        raise TypeError(f"timestamptz column: {type(value).__name__}")
    raise ValueError(f"unknown logical type {logical!r}")


def _doc(rows: Sequence[Sequence[Any]]) -> str:
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


_TYPES: dict[tuple[int, tuple[str, ...]], tuple[Table, tuple[tuple[str, str], ...]]] = {}


def _types(table: Table, columns: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    """(column, logical type) of `columns` of `table`, computed once per table object and column list.

    Keyed by the table's identity (hashing a table hashes every column); the entry keeps the table,
    so an identity is never reused while its entry lives.
    """
    key = (id(table), columns)
    found = _TYPES.get(key)
    if found is not None and found[0] is table:
        return found[1]
    by_name = {c.name: c.type for c in table.columns}
    types = tuple((name, by_name[name]) for name in columns)
    if len(_TYPES) > 4096:
        _TYPES.clear()
    _TYPES[key] = (table, types)
    return types


def _dedupe_last(rows: Sequence[Any], key: Callable[[Any], Any]) -> list[Any]:
    """Rows with a unique key, the last of each key kept, in first-seen order."""
    by_key: dict[Any, Any] = {}
    for row in rows:
        by_key[key(row)] = row
    return list(by_key.values())


def _source(system: str | None, key: str | None) -> SourceKey | None:
    return SourceKey(system, key) if system is not None and key is not None else None


def _key_token(text: str) -> str:
    """A key a `Conflict` may carry: `text` when it is safe text, else a stable hash of it."""
    if SAFE_TEXT_RE.match(text):
        return text
    return "h-" + hashlib.sha256(text.encode()).hexdigest()[:16]


class SqlStore(ABC):
    """The hub's store on one engine, in the schemas `<prefix>_<group>`.

    **One lock order for batches (story 3.3).** Every method that writes a batch's rows, and every
    transaction a service opens around them, takes the batch row first, with a conditional update, before it
    touches `batch_item`, `tray_lock` or `tray_entry`: `_hold_batch` in a chunk's `apply_work`, `hold_batch`,
    `fail_items`, `undo_batch`, `finish_batch`, `end_batch`, `apply_split`, `set_batch` and `request_stop`,
    and a stage, whose `set_batch` to `staged` comes before `stage_tray`. `settle_tray` alone locks `tray_entry` before
    `tray_lock`; inside a batch transaction the batch row is already held, so no two batch transactions wait
    on each other in opposite orders. On Postgres they wait for one another and never deadlock, and DuckDB's
    single writer serialises them anyway.
    """

    engine: str = ""  # "duckdb" | "postgres"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.prefix = settings.schema_prefix
        #: the time the store stamps on rows it writes (tests may replace it)
        self.clock: Callable[[], datetime] = utcnow
        self._listeners: list[Callable[[str], None]] = []
        self._entity_tables: dict[str, Table] = {}

    def t(self, group: str, table: str) -> str:
        """The fully qualified name of a table: `<prefix>_<group>.<table>`."""
        return f"{schema_name(self.prefix, group)}.{table}"

    def _q(self, table: Table) -> str:
        return self.t(table.group, table.name)

    def add_listener(self, listener: Callable[[str], None]) -> Callable[[], None]:
        """Call `listener(sql)` with every statement before it runs (the capacity test records them).

        Returns a function that removes the listener.
        """
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def _check(self, sql: str) -> None:
        """The guard, then the listeners: every engine hook calls this first."""
        guard.check_statement(sql, self.prefix)
        for listener in tuple(self._listeners):
            listener(sql)

    # ------------------------------------------------------------------ engine hooks (B.5.3)

    @abstractmethod
    def server_version(self) -> str:
        """The engine and its version, as a report prints them: "DuckDB 1.5.0", "Postgres 17.2"."""

    @abstractmethod
    def _execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Run one statement (after `_check`), in the open transaction if there is one; rows affected."""

    @abstractmethod
    def _fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[tuple]:
        """Run one query (after `_check`) and return its rows."""

    @abstractmethod
    def _row_source(self) -> str:
        """The FROM item, aliased `rs` with the one column `r`, that turns one bound JSON array into rows."""

    @abstractmethod
    def _row_value(self, index: int) -> str:
        """The text of the `index`-th value of a row `rs.r` (0-based), before its CAST."""

    @abstractmethod
    def _engine_transaction(self) -> AbstractContextManager[None]:
        """BEGIN … COMMIT/ROLLBACK on the engine's connection for this context; joins an open one."""

    @abstractmethod
    def _commit_lock(self) -> AbstractContextManager[None]:
        """The commit transaction under the commit-order lock: DuckDB the store's RLock before BEGIN;
        Postgres BEGIN, READ COMMITTED, pg_advisory_xact_lock."""

    @abstractmethod
    def _in_transaction(self) -> bool:
        """True when this context (thread) has a transaction open on this store."""

    @abstractmethod
    def _lease(self, name: str) -> AbstractContextManager[bool]:
        """A non-blocking run lease: True when taken, False when another run holds it."""

    @abstractmethod
    def _json_param(self) -> str:
        """The placeholder text binding one JSON value: `CAST(? AS JSON)` or `CAST(? AS JSONB)`."""

    @abstractmethod
    def _decode_json(self, value: Any) -> Any:
        """A JSON column value as Python: DuckDB returns text, Postgres already decoded values."""

    @abstractmethod
    def _sql_type(self, logical: str) -> str:
        """The engine's spelling of a logical type."""

    @abstractmethod
    def _row_estimates(self, tables: Sequence[str]) -> dict[str, int]:
        """Row counts for `mdm status`: DuckDB COUNT(*); Postgres reltuples from pg_class."""

    @abstractmethod
    def close(self) -> None:
        """Release the connection or the pool."""

    # ------------------------------------------------------------------ shared helpers (the SQL text, once)

    def _cast(self, index: int, logical: str) -> str:
        return f"CAST({self._row_value(index)} AS {self._sql_type(logical)})"

    def _decode(self, value: Any, logical: str) -> Any:
        if value is None:
            return None
        if logical == "json":
            return self._decode_json(value)
        if logical == "timestamptz" and isinstance(value, datetime):
            return _utc(value)
        if logical in ("bigint", "int") and not isinstance(value, int):
            return int(value)
        return value

    def _decode_row(self, table: Table, columns: Sequence[str], row: Sequence[Any]) -> dict[str, Any]:
        decode = self._decode
        return {
            name: decode(value, logical) if value is not None else None
            for (name, logical), value in zip(_types(table, tuple(columns)), row, strict=True)
        }

    def _encode_rows(self, columns: Sequence[Column], rows: Sequence[Mapping[str, Any]]) -> list[list[Any]]:
        return [[encode(row[c.name], c.type) for c in columns] for row in rows]

    def _row_columns(self, table: Table, rows: Sequence[Mapping[str, Any]]) -> list[Column]:
        names = set(rows[0])
        unknown = names - set(table.column_names())
        if unknown:
            raise MdmError("unknown_column", table=table.name, columns=tuple(sorted(unknown)))
        for row in rows:
            if set(row) != names:
                raise ValueError(f"rows for {table.name} name different columns")
        return [c for c in table.columns if c.name in names]

    def _insert(
        self,
        table: Table,
        rows: Sequence[Mapping[str, Any]],
        *,
        on_conflict_nothing: tuple[str, ...] = (),
    ) -> int:
        """INSERT … SELECT from the row source, chunks of WRITE_CHUNK_ROWS; the number of rows written."""
        if not rows:
            return 0
        columns = self._row_columns(table, rows)
        select = ", ".join(self._cast(i, c.type) for i, c in enumerate(columns))
        sql = (
            f"/*mdm:keyed*/ INSERT INTO {self._q(table)} ({', '.join(c.name for c in columns)}) "
            f"SELECT {select} FROM {self._row_source()}"
        )
        if on_conflict_nothing:
            sql += f" ON CONFLICT ({', '.join(on_conflict_nothing)}) DO NOTHING"
        written = 0
        for chunk in capacity.chunks(rows, capacity.WRITE_CHUNK_ROWS):
            written += self._execute(sql, [_doc(self._encode_rows(columns, chunk))])
        return written

    def _upsert(
        self,
        table: Table,
        rows: Sequence[Mapping[str, Any]],
        *,
        conflict: tuple[str, ...],
        update: Sequence[str],
    ) -> int:
        """INSERT … ON CONFLICT (conflict) DO UPDATE SET c = excluded.c, …

        Raises ValueError, before binding, when two rows share a conflict key: callers dedupe first,
        last version wins (Postgres fails on a repeated key in one statement; DuckDB keeps the first).
        """
        if not rows:
            return 0
        seen: set[tuple[Any, ...]] = set()
        for row in rows:
            key = tuple(row[c] for c in conflict)
            if key in seen:
                raise ValueError(f"two rows for {table.name} share a conflict key")
            seen.add(key)
        columns = self._row_columns(table, rows)
        select = ", ".join(self._cast(i, c.type) for i, c in enumerate(columns))
        sql = (
            f"/*mdm:keyed*/ INSERT INTO {self._q(table)} ({', '.join(c.name for c in columns)}) "
            f"SELECT {select} FROM {self._row_source()} ON CONFLICT ({', '.join(conflict)}) "
        )
        if update:
            sql += "DO UPDATE SET " + ", ".join(f"{c} = excluded.{c}" for c in update)
        else:
            sql += "DO NOTHING"
        written = 0
        for chunk in capacity.chunks(rows, capacity.WRITE_CHUNK_ROWS):
            written += self._execute(sql, [_doc(self._encode_rows(columns, chunk))])
        return written

    def _key_doc(self, table: Table, key_columns: Sequence[str], keys: Sequence[Sequence[Any]]) -> str:
        types = [table.column(k).type for k in key_columns]
        return _doc([[encode(v, t) for v, t in zip(key, types, strict=True)] for key in keys])

    def _key_join(self, table: Table, key_columns: Sequence[str], alias: str = "t") -> str:
        return " AND ".join(
            f"{alias}.{k} = {self._cast(i, table.column(k).type)}" for i, k in enumerate(key_columns)
        )

    @staticmethod
    def _unique_keys(keys: Sequence[Sequence[Any]]) -> list[tuple[Any, ...]]:
        return sorted(dict.fromkeys(tuple(k) for k in keys))

    def _select_keyed(
        self,
        table: Table,
        columns: Sequence[str],
        key_columns: Sequence[str],
        keys: Sequence[tuple],
        *,
        order_by: Sequence[str],
        where: str = "",
        params: Sequence[Any] = (),
    ) -> list[tuple]:
        """SELECT … FROM t JOIN <row source> ON t.k0 = CAST(<value 0> AS K0) AND … ORDER BY …; chunks of KEY_CHUNK.

        `where` is SQL over the alias `t`; its parameters follow the key document. The rows of several
        chunks are merged in `order_by` order when every order column is selected.
        """
        unique = self._unique_keys(keys)
        if not unique:
            return []
        sql = (
            f"/*mdm:keyed*/ SELECT {', '.join('t.' + c for c in columns)} FROM {self._q(table)} AS t "
            f"JOIN {self._row_source()} ON {self._key_join(table, key_columns)}"
        )
        if where:
            sql += f" WHERE {where}"
        if order_by:
            sql += " ORDER BY " + ", ".join("t." + c for c in order_by)
        out: list[tuple] = []
        batches = 0
        for chunk in capacity.chunks(unique, capacity.KEY_CHUNK):
            out.extend(self._fetch_all(sql, [self._key_doc(table, key_columns, chunk), *params]))
            batches += 1
        if batches > 1 and order_by and all(c in columns for c in order_by):
            positions = [list(columns).index(c) for c in order_by]
            out.sort(key=lambda row: tuple((row[p] is None, row[p]) for p in positions))
        return out

    def _update_keyed(
        self,
        table: Table,
        set_columns: Sequence[str],
        key_columns: Sequence[str],
        rows: Sequence[Sequence[Any]],
        *,
        extra_set: str = "",
        where: str = "",
    ) -> int:
        """UPDATE t SET c = <row value>, … FROM <row source> WHERE <keys match> [AND where].

        Each row is the key values, then one value per `set_columns`; `extra_set` is SQL appended to
        the SET list. Returns the rows updated.
        """
        if not rows:
            return 0
        n = len(key_columns)
        sets = [f"{c} = {self._cast(n + i, table.column(c).type)}" for i, c in enumerate(set_columns)]
        if extra_set:
            sets.append(extra_set)
        sql = (
            f"/*mdm:keyed*/ UPDATE {self._q(table)} AS t SET {', '.join(sets)} FROM {self._row_source()} "
            f"WHERE {self._key_join(table, key_columns)}"
        )
        if where:
            sql += f" AND {where}"
        types = [table.column(c).type for c in (*key_columns, *set_columns)]
        updated = 0
        for chunk in capacity.chunks(rows, capacity.WRITE_CHUNK_ROWS):
            doc = _doc([[encode(v, t) for v, t in zip(row, types, strict=True)] for row in chunk])
            updated += self._execute(sql, [doc])
        return updated

    def _delete_keyed(self, table: Table, key_columns: Sequence[str], keys: Sequence[Sequence[Any]]) -> int:
        """DELETE FROM t USING <row source> WHERE <keys match>; rows deleted."""
        unique = self._unique_keys(keys)
        if not unique:
            return 0
        name = self._q(table)
        on = " AND ".join(
            f"{name}.{k} = {self._cast(i, table.column(k).type)}" for i, k in enumerate(key_columns)
        )
        sql = f"/*mdm:keyed*/ DELETE FROM {name} USING {self._row_source()} WHERE {on}"
        deleted = 0
        for chunk in capacity.chunks(unique, capacity.KEY_CHUNK):
            deleted += self._execute(sql, [self._key_doc(table, key_columns, chunk)])
        return deleted

    # ------------------------------------------------------------------ lifecycle

    def _schemas(self, groups: Sequence[str] = ddl.GROUPS) -> list[str]:
        return [schema_name(self.prefix, g) for g in groups]

    def _columns_of(self, groups: Sequence[str] = ddl.GROUPS) -> dict[tuple[str, str], list[tuple[str, str]]]:
        """(schema, table) -> [(column, logical type)] in ordinal order, for the prefix's tables and views."""
        schemas = self._schemas(groups)
        marks = ", ".join("?" for _ in schemas)
        rows = self._fetch_all(
            "/*mdm:small*/ SELECT table_schema, table_name, column_name, data_type, ordinal_position "
            f"FROM information_schema.columns WHERE table_schema IN ({marks}) "
            "ORDER BY table_schema, table_name, ordinal_position",
            schemas,
        )
        out: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for schema, name, column, data_type, _ in rows:
            out.setdefault((schema, name), []).append(
                (column, ddl.DATA_TYPE_LOGICAL.get(data_type.lower(), ""))
            )
        return out

    def table_columns(self, group: str, name: str) -> list[str]:
        """The columns of one table or view of the prefix, in ordinal order ([] when it does not exist)."""
        columns = self._columns_of((group,)).get((schema_name(self.prefix, group), name), [])
        return [c for c, _ in columns]

    def init_schema(self, *, create_landing: bool) -> None:
        """Schemas, fixed tables, sequences, indexes and passthrough views; landing only when asked.

        Idempotent, and it migrates by appending: a fixed table missing a column the code declares
        gains it, and the passthrough views are recreated with every column. Creating the landing
        tables is refused (`PlatformRefused`) unless the store is local or the live suite's run prefix.
        """
        if create_landing and not guard.landing_allowed(self.settings, self.prefix):
            raise PlatformRefused("landing_ddl_refused", prefix=self.prefix)
        groups = [g for g in ddl.GROUPS if g != "landing" or create_landing]
        with guard.ddl_scope(), self.transaction():
            existing = self._columns_of(groups)
            statements = [ddl.render_schema(self.prefix, g) for g in groups]
            statements.extend(
                ddl.render_sequence(self.prefix, g, name, self.engine)
                for g, name in ddl.SEQUENCES
                if g in groups
            )
            for fixed in ddl.tables_of(groups):
                have = [c for c, _ in existing.get((schema_name(self.prefix, fixed.group), fixed.name), [])]
                if not have:
                    statements.extend(ddl.render_table(fixed, self.prefix, self.engine))
                    continue
                statements.extend(
                    ddl.add_column_sql(fixed, c, self.prefix, self.engine)
                    for c in fixed.columns
                    if c.name not in have
                )
                statements.extend(ddl.render_table(fixed, self.prefix, self.engine)[1:])  # indexes
            for statement in statements:
                self._execute(statement)
            self._recreate_passthrough_views()
        self._entity_tables.clear()

    def _recreate_passthrough_views(self) -> list[str]:
        core = self._columns_of(("core",))
        columns = {
            name: [c for c, _ in core[(schema_name(self.prefix, "core"), name)]]
            for name in ddl.PASSTHROUGH_VIEWS
            if (schema_name(self.prefix, "core"), name) in core
        }
        statements = ddl.passthrough_views_sql(self.prefix, columns)
        for statement in statements:
            self._execute(statement)
        return statements

    def ensure_entity_tables(self, model: EntityModel) -> list[str]:
        """Create the entity table or ADD COLUMN (appended); recreate its views; add its id_counter row.

        Returns the statements run.
        """
        wanted = ddl.entity_table(model)
        run: list[str] = []
        with guard.ddl_scope(), self.transaction():
            have = self.table_columns("core", model.entity)
            if not have:
                statements = ddl.render_table(wanted, self.prefix, self.engine)
            else:
                statements = [
                    ddl.add_column_sql(wanted, c, self.prefix, self.engine)
                    for c in wanted.columns
                    if c.name not in have
                ]
            for statement in statements:
                self._execute(statement)
                run.append(statement)
            ordered = self.table_columns("core", model.entity)
            view = ddl.masked_view_sql(model, self.prefix, ordered, engine=self.engine)
            self._execute(view)
            run.append(view)
            self._insert(
                _T("hub", "id_counter"),
                [{"name": model.entity, "code": model.code, "last_value": 0}],
                on_conflict_nothing=("name",),
            )
        self._entity_tables.pop(model.entity, None)
        return run

    def ddl(self, groups: Sequence[str] | None = None) -> list[str]:
        """The DDL of `groups` for this engine and prefix (for `mdm ddl`)."""
        return ddl.all_ddl(self.prefix, self.engine, groups)

    def drop_all(self) -> None:
        """Drop every schema of the prefix: tests and `mdm demo reset` only.

        Refuses the prefix "mdm" on Postgres (`GuardError`), and any store outside the local mode other
        than the live suite's run prefix (`PlatformRefused`).
        """
        if self.engine == "postgres" and self.prefix == guard.DEFAULT_PREFIX:
            raise GuardError("drop_refused", prefix=self.prefix)
        if not (self.settings.local_mode or guard.live_run(self.prefix, self.settings)):
            raise PlatformRefused("drop_refused", prefix=self.prefix)
        with guard.ddl_scope():
            for group in reversed(ddl.GROUPS):
                self._execute(f"DROP SCHEMA IF EXISTS {schema_name(self.prefix, group)} CASCADE")
        self._entity_tables.clear()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """A transaction; joins one already open in this context."""
        with self._engine_transaction():
            yield

    @contextmanager
    def commit_scope(self) -> Iterator[None]:
        """The commit transaction: outermost only (MdmError("nested_commit_scope") inside another).

        DuckDB: the store lock, then BEGIN. Postgres: BEGIN, SET TRANSACTION ISOLATION LEVEL READ
        COMMITTED, pg_advisory_xact_lock. Opens the guard's "commit" scope. The lock is released after
        COMMIT, so readers see versions in order.
        """
        if self._in_transaction():
            raise MdmError("nested_commit_scope")
        with self._commit_lock(), guard.commit_scope_flag():
            yield

    @contextmanager
    def exclusive_lease(self, name: str) -> Iterator[bool]:
        """A run lease: yields False when another run holds it; never waits."""
        if name == "commit":
            raise ValueError("the lease name 'commit' is the commit lock's")
        with self._lease(name) as held:
            yield held

    # ------------------------------------------------------------------ model group

    def save_entity_model(self, entity: str, version: int, status: str, doc: dict, actor: str) -> None:
        """A new model version; `Conflict` when the version exists (a saved version never changes)."""
        now = self.clock()
        row = {
            "entity": entity,
            "version": version,
            "status": status,
            "doc": doc,
            "doc_hash": hashlib.sha256(canonical_json(doc).encode()).hexdigest(),
            "created_at": now,
            "created_by": actor,
            "published_at": now if status == "published" else None,
            "published_by": actor if status == "published" else None,
        }
        if not self._insert(_T("model", "entity_model"), [row], on_conflict_nothing=("entity", "version")):
            raise Conflict([f"{entity}:v{version}"], code="model_version_exists")

    def entity_model_versions(self, entity: str) -> list[tuple[int, str, datetime]]:
        """(version, status, created_at), by version."""
        rows = self._fetch_all(
            f"/*mdm:small*/ SELECT version, status, created_at FROM {self.t('model', 'entity_model')} "
            "WHERE entity = ? ORDER BY version",
            [entity],
        )
        return [(int(v), s, _utc(c)) for v, s, c in rows]

    def entity_model_doc(self, entity: str, version: int | None = None) -> tuple[int, dict] | None:
        """(version, doc) of `version`, or of the published version when None; None when absent."""
        table = self.t("model", "entity_model")
        if version is None:
            rows = self._fetch_all(
                f"/*mdm:small*/ SELECT version, doc FROM {table} WHERE entity = ? AND status = 'published' "
                "ORDER BY version DESC LIMIT 1",
                [entity],
            )
        else:
            rows = self._fetch_all(
                f"/*mdm:small*/ SELECT version, doc FROM {table} WHERE entity = ? AND version = ?",
                [entity, version],
            )
        return (int(rows[0][0]), self._decode_json(rows[0][1])) if rows else None

    def published_entities(self) -> list[str]:
        """Entities with a published model, by name."""
        rows = self._fetch_all(
            f"/*mdm:small*/ SELECT DISTINCT entity FROM {self.t('model', 'entity_model')} "
            "WHERE status = 'published' ORDER BY entity"
        )
        return [r[0] for r in rows]

    def model_entities(self) -> list[str]:
        """Every entity with a model in any status, by name."""
        rows = self._fetch_all(
            f"/*mdm:small*/ SELECT DISTINCT entity FROM {self.t('model', 'entity_model')} ORDER BY entity"
        )
        return [r[0] for r in rows]

    def _set_status(
        self, table: str, where: str, params: Sequence[Any], status: str, actor: str, what: str
    ) -> None:
        now = self.clock()
        if status == "published":
            sql = f"/*mdm:small*/ UPDATE {table} SET status = ?, published_at = CAST(? AS TIMESTAMP WITH TIME ZONE), published_by = ? WHERE {where}"
            args: list[Any] = [status, iso(now), actor, *params]
        else:
            sql = f"/*mdm:small*/ UPDATE {table} SET status = ? WHERE {where}"
            args = [status, *params]
        if not self._execute(sql, args):
            raise NotFound("unknown_version", version=what)

    def set_entity_model_status(self, entity: str, version: int, status: str, actor: str) -> None:
        self._set_status(
            self.t("model", "entity_model"),
            "entity = ? AND version = ?",
            [entity, version],
            status,
            actor,
            f"{entity}:v{version}",
        )

    def save_rule_set(
        self, entity: str, kind: str, version: int, status: str, doc: dict, model_version: int, actor: str
    ) -> None:
        """A new rule-set version; `Conflict` when the version exists."""
        now = self.clock()
        row = {
            "entity": entity,
            "kind": kind,
            "version": version,
            "status": status,
            "doc": doc,
            "model_version": model_version,
            "created_at": now,
            "created_by": actor,
            "published_at": now if status == "published" else None,
            "published_by": actor if status == "published" else None,
        }
        if not self._insert(
            _T("model", "rule_set"), [row], on_conflict_nothing=("entity", "kind", "version")
        ):
            raise Conflict([f"{entity}:{kind}:v{version}"], code="rule_set_version_exists")

    def rule_set_doc(self, entity: str, kind: str, version: int | None = None) -> tuple[int, dict] | None:
        """(version, doc) of `version`, or of the published rule set when None; None when absent."""
        table = self.t("model", "rule_set")
        if version is None:
            rows = self._fetch_all(
                f"/*mdm:small*/ SELECT version, doc FROM {table} WHERE entity = ? AND kind = ? "
                "AND status = 'published' ORDER BY version DESC LIMIT 1",
                [entity, kind],
            )
        else:
            rows = self._fetch_all(
                f"/*mdm:small*/ SELECT version, doc FROM {table} WHERE entity = ? AND kind = ? AND version = ?",
                [entity, kind, version],
            )
        return (int(rows[0][0]), self._decode_json(rows[0][1])) if rows else None

    def rule_set_versions(self, entity: str, kind: str) -> list[tuple[int, str, int, datetime]]:
        """(version, status, model_version, created_at) of one entity's rule sets of one kind, by version."""
        rows = self._fetch_all(
            f"/*mdm:small*/ SELECT version, status, model_version, created_at FROM {self.t('model', 'rule_set')} "
            "WHERE entity = ? AND kind = ? ORDER BY version",
            [entity, kind],
        )
        return [(int(v), s, int(m), _utc(c)) for v, s, m, c in rows]

    def next_rule_set_version(self, entity: str, kind: str) -> int:
        rows = self._fetch_all(
            f"/*mdm:small*/ SELECT COALESCE(MAX(version), 0) + 1 FROM {self.t('model', 'rule_set')} "
            "WHERE entity = ? AND kind = ?",
            [entity, kind],
        )
        return int(rows[0][0])

    def set_rule_set_status(self, entity: str, kind: str, version: int, status: str, actor: str) -> None:
        self._set_status(
            self.t("model", "rule_set"),
            "entity = ? AND kind = ? AND version = ?",
            [entity, kind, version],
            status,
            actor,
            f"{entity}:{kind}:v{version}",
        )

    def save_code_list(
        self, name: str, source: str, values: Sequence[tuple[str, str | None]], actor: str
    ) -> int:
        """A new version of a code list from (code, label) pairs; returns the version.

        A code listed twice keeps its first label.
        """
        unique: dict[str, str | None] = {}
        for code, label in values:
            unique.setdefault(code, label)
        with self.transaction():
            rows = self._fetch_all(
                f"/*mdm:small*/ SELECT COALESCE(MAX(version), 0) + 1 FROM {self.t('model', 'code_list_version')} "
                "WHERE name = ?",
                [name],
            )
            version = int(rows[0][0])
            self._insert(
                _T("model", "code_list_version"),
                [
                    {
                        "name": name,
                        "version": version,
                        "source": source,
                        "loaded_at": self.clock(),
                        "loaded_by": actor,
                        "value_count": len(unique),
                    }
                ],
            )
            self._insert(
                _T("model", "code_list_value"),
                [{"name": name, "version": version, "code": c, "label": lbl} for c, lbl in unique.items()],
            )
        return version

    def code_list(self, name: str) -> tuple[int, frozenset[str]] | None:
        """(latest version, codes); None when the list was never loaded."""
        rows = self._fetch_all(
            f"/*mdm:small*/ SELECT version FROM {self.t('model', 'code_list_version')} WHERE name = ? "
            "ORDER BY version DESC LIMIT 1",
            [name],
        )
        if not rows:
            return None
        version = int(rows[0][0])
        codes = self._fetch_all(
            f"/*mdm:small*/ SELECT code FROM {self.t('model', 'code_list_value')} WHERE name = ? AND version = ? "
            "ORDER BY code",
            [name, version],
        )
        return version, frozenset(c for (c,) in codes)

    def code_list_names(self) -> list[tuple[str, int]]:
        """(name, latest version) of every code list, by name."""
        rows = self._fetch_all(
            f"/*mdm:small*/ SELECT name, MAX(version) FROM {self.t('model', 'code_list_version')} "
            "GROUP BY name ORDER BY name"
        )
        return [(n, int(v)) for n, v in rows]

    # ------------------------------------------------------------------ landing (read; insert only in the simulator)

    _LANDING_COLUMNS = (
        "event_id",
        "source_system",
        "source_key",
        "entity",
        "op",
        "occurred_at",
        "source_version",
        "initial_load",
        "payload",
        "landed_at",
        "landing_seq",
    )

    def _landing_rows(self, rows: Sequence[Sequence[Any]]) -> list[SourceChange]:
        table = _T("landing", "source_change")
        out = []
        for row in rows:
            d = self._decode_row(table, self._LANDING_COLUMNS, row)
            out.append(SourceChange(**d))
        return out

    def landing_above(self, high_water: int, limit: int) -> list[SourceChange]:
        """Rows with landing_seq > high_water, in landing order, at most `limit`."""
        rows = self._fetch_all(
            f"/*mdm:paged*/ SELECT {', '.join(self._LANDING_COLUMNS)} FROM {self.t('landing', 'source_change')} "
            "WHERE landing_seq > ? ORDER BY landing_seq LIMIT ?",
            [int(high_water), int(limit)],
        )
        return self._landing_rows(rows)

    def landing_in_ranges(self, ranges: Sequence[tuple[int, int]], limit: int) -> list[SourceChange]:
        """Rows inside (landing_seq BETWEEN ? AND ?) OR …, GAP_PROBE_RANGES ranges per statement, ordered, LIMIT."""
        ordered = sorted((int(lo), int(hi)) for lo, hi in ranges)
        out: list[SourceChange] = []
        for chunk in capacity.chunks(ordered, capacity.GAP_PROBE_RANGES):
            room = limit - len(out)
            if room <= 0:
                break
            where = " OR ".join("(landing_seq BETWEEN ? AND ?)" for _ in chunk)
            params: list[Any] = [v for pair in chunk for v in pair]
            rows = self._fetch_all(
                f"/*mdm:paged*/ SELECT {', '.join(self._LANDING_COLUMNS)} FROM {self.t('landing', 'source_change')} "
                f"WHERE {where} ORDER BY landing_seq LIMIT ?",
                [*params, room],
            )
            out.extend(self._landing_rows(rows))
        out.sort(key=lambda c: c.landing_seq)
        return out[:limit]

    def landing_max_seq(self) -> int:
        """The highest landing sequence landed (0 when none): one row off the unique index, not a scan."""
        rows = self._fetch_all(
            f"/*mdm:paged*/ SELECT landing_seq FROM {self.t('landing', 'source_change')} "
            "ORDER BY landing_seq DESC LIMIT 1"
        )
        return int(rows[0][0]) if rows else 0

    def landing_insert(self, rows: Sequence[LandingRow]) -> int:
        """ON CONFLICT (event_id) DO NOTHING; only inside `guard.simulating_integration_platform`.

        Returns the rows inserted; a repeated event ID in `rows` keeps its first row.
        """
        first: dict[str, LandingRow] = {}
        for row in rows:
            first.setdefault(row.event_id, row)
        return self._insert(
            _T("landing", "source_change"),
            [
                {
                    "event_id": r.event_id,
                    "source_system": r.source_system,
                    "source_key": r.source_key,
                    "entity": r.entity,
                    "op": r.op,
                    "occurred_at": r.occurred_at,
                    "source_version": r.source_version,
                    "initial_load": r.initial_load,
                    "payload": r.payload,
                }
                for r in first.values()
            ],
            on_conflict_nothing=("event_id",),
        )

    # ------------------------------------------------------------------ arrival position

    def reader_state(self, reader: str) -> ReaderState:
        """The reader's position row; zeros when absent."""
        rows = self._fetch_all(
            f"/*mdm:small*/ SELECT high_water, low_water, reconciled_at FROM {self.t('work', 'arrival_position')} "
            "WHERE reader = ?",
            [reader],
        )
        if not rows:
            return ReaderState(0, 0, None)
        high, low, reconciled = rows[0]
        return ReaderState(int(high), int(low), _utc(reconciled) if reconciled is not None else None)

    def gaps(self, reader: str, state: str, after_lo: int | None, limit: int) -> list[Gap]:
        """Gaps of one state, by lo, after `after_lo`."""
        sql = (
            f"/*mdm:paged*/ SELECT lo, hi, state, first_seen_at, lost_at FROM {self.t('work', 'arrival_gap')} "
            "WHERE reader = ? AND state = ?"
        )
        params: list[Any] = [reader, state]
        if after_lo is not None:
            sql += " AND lo > ?"
            params.append(int(after_lo))
        rows = self._fetch_all(sql + " ORDER BY lo LIMIT ?", [*params, int(limit)])
        return [
            Gap(int(lo), int(hi), st, _utc(first), _utc(lost) if lost is not None else None)
            for lo, hi, st, first, lost in rows
        ]

    def save_reader(
        self,
        reader: str,
        high_water: int,
        low_water: int,
        *,
        upsert: Sequence[Gap] = (),
        delete: Sequence[int] = (),
        reconciled_at: datetime | None = None,
    ) -> None:
        """The position, the gaps upserted by lo and the gaps deleted by lo, in the caller's transaction.

        `reconciled_at` None keeps the stored one. A lo both deleted and upserted ends upserted.
        """
        with self.transaction():
            position = _T("work", "arrival_position")
            sql = (
                f"/*mdm:small*/ INSERT INTO {self._q(position)} (reader, high_water, low_water, reconciled_at, updated_at) "
                "VALUES (?, ?, ?, CAST(? AS TIMESTAMP WITH TIME ZONE), CAST(? AS TIMESTAMP WITH TIME ZONE)) "
                "ON CONFLICT (reader) DO UPDATE SET high_water = excluded.high_water, low_water = excluded.low_water, "
                f"reconciled_at = COALESCE(excluded.reconciled_at, {self._q(position)}.reconciled_at), "
                "updated_at = excluded.updated_at"
            )
            self._execute(
                sql,
                [
                    reader,
                    int(high_water),
                    int(low_water),
                    iso(reconciled_at) if reconciled_at is not None else None,
                    iso(self.clock()),
                ],
            )
            gap = _T("work", "arrival_gap")
            upserted = {g.lo for g in upsert}
            self._delete_keyed(
                gap, ("reader", "lo"), [(reader, int(lo)) for lo in delete if lo not in upserted]
            )
            rows = _dedupe_last(
                [
                    {
                        "reader": reader,
                        "lo": g.lo,
                        "hi": g.hi,
                        "state": g.state,
                        "first_seen_at": g.first_seen_at,
                        "lost_at": g.lost_at,
                    }
                    for g in upsert
                ],
                key=lambda r: r["lo"],
            )
            self._upsert(
                gap, rows, conflict=("reader", "lo"), update=("hi", "state", "first_seen_at", "lost_at")
            )

    def gap_counts(self, reader: str) -> dict[str, int]:
        """state -> gap ranges of the reader, for `mdm status`."""
        rows = self._fetch_all(
            f"/*mdm:aggregate*/ SELECT state, COUNT(*) FROM {self.t('work', 'arrival_gap')} WHERE reader = ? "
            "GROUP BY state ORDER BY state",
            [reader],
        )
        return {s: int(n) for s, n in rows}

    # ------------------------------------------------------------------ work group: rejects

    _REJECT_COLUMNS = (
        "event_id",
        "landing_seq",
        "entity",
        "source_system",
        "source_key",
        "reason",
        "attributes",
    )

    def put_rejects(self, rejects: Sequence[Reject]) -> None:
        """Upsert by event ID: a row rejected again after a replay is a reject not yet replayed."""
        now = self.clock()
        rows = _dedupe_last(
            [
                {
                    "event_id": r.event_id,
                    "landing_seq": r.landing_seq,
                    "entity": r.entity,
                    "source_system": r.source.system if r.source else None,
                    "source_key": r.source.key if r.source else None,
                    "reason": safe(r.reason),
                    "attributes": safe(list(r.attributes)),
                    "rejected_at": now,
                    "replayed_at": None,
                }
                for r in rejects
            ],
            key=lambda r: r["event_id"],
        )
        self._upsert(
            _T("work", "landing_reject"),
            rows,
            conflict=("event_id",),
            update=(
                "landing_seq",
                "entity",
                "source_system",
                "source_key",
                "reason",
                "attributes",
                "rejected_at",
                "replayed_at",
            ),
        )

    def rejects(self, limit: int, after: str | None = None, replayed: bool = False) -> list[Reject]:
        """Rejects by event_id after `after`; `replayed` selects those already replayed."""
        sql = (
            f"/*mdm:paged*/ SELECT {', '.join(self._REJECT_COLUMNS)} FROM {self.t('work', 'landing_reject')} "
            f"WHERE replayed_at IS {'NOT ' if replayed else ''}NULL"
        )
        params: list[Any] = []
        if after is not None:
            sql += " AND event_id > ?"
            params.append(after)
        rows = self._fetch_all(sql + " ORDER BY event_id LIMIT ?", [*params, int(limit)])
        table = _T("work", "landing_reject")
        out = []
        for row in rows:
            d = self._decode_row(table, self._REJECT_COLUMNS, row)
            out.append(
                Reject(
                    event_id=d["event_id"],
                    landing_seq=int(d["landing_seq"]),
                    entity=d["entity"],
                    source=_source(d["source_system"], d["source_key"]),
                    reason=d["reason"],
                    attributes=tuple(d["attributes"] or ()),
                )
            )
        return out

    def reject_count(self, replayed: bool = False) -> int:
        """Rejects not yet replayed (or replayed), for `mdm status`."""
        rows = self._fetch_all(
            f"/*mdm:aggregate*/ SELECT COUNT(*) FROM {self.t('work', 'landing_reject')} "
            f"WHERE replayed_at IS {'NOT ' if replayed else ''}NULL"
        )
        return int(rows[0][0])

    def mark_rejects_replayed(self, event_ids: Sequence[str]) -> None:
        """Set replayed_at on the rejects replayed successfully."""
        now = self.clock()
        self._update_keyed(
            _T("work", "landing_reject"),
            ("replayed_at",),
            ("event_id",),
            [(e, now) for e in dict.fromkeys(event_ids)],
        )

    # ------------------------------------------------------------------ work group: source state

    _STATE_COLUMNS = (
        "entity",
        "source_system",
        "source_key",
        "status",
        "std_values",
        "match_forms",
        "ids",
        "refs",
        "value_ids",
        "sample_hash",
        "source_version",
        "occurred_at",
        "landing_seq",
        "event_id",
        "initial_load",
        "held",
        "approved_values",
        "approved_event_id",
        "rules_checked",
        "rules_failed",
        "updated_at",
    )
    # set on insert only; afterwards the commit's work writes own them (see put_source_states)
    _STATE_KEPT = ("held", "approved_values", "approved_event_id")

    @staticmethod
    def _state_row(s: SourceState) -> dict[str, Any]:
        return {
            "entity": s.entity,
            "source_system": s.source.system,
            "source_key": s.source.key,
            "status": s.status,
            "std_values": dict(s.values),
            "match_forms": dict(s.match),
            "ids": [{"scheme": i.scheme, "value": i.value, "valid": i.valid} for i in s.ids],
            "refs": dict(s.references),
            "value_ids": dict(s.value_ids),
            "sample_hash": s.sample_hash,
            "source_version": s.source_version,
            "occurred_at": s.occurred_at,
            "landing_seq": s.landing_seq,
            "event_id": s.event_id,
            "initial_load": s.initial_load,
            "held": s.held,
            "approved_values": dict(s.approved_values) if s.approved_values is not None else None,
            "approved_event_id": s.approved_event_id,
            "rules_checked": s.rules_checked,
            "rules_failed": s.rules_failed,
            "updated_at": s.updated_at,
        }

    def _state_of(self, row: Sequence[Any]) -> SourceState:
        d = self._decode_row(_T("work", "source_state"), self._STATE_COLUMNS, row)
        return SourceState(
            entity=d["entity"],
            source=SourceKey(d["source_system"], d["source_key"]),
            status=d["status"],
            values=d["std_values"],
            match=d["match_forms"],
            ids=tuple(RegisteredId(i["scheme"], i["value"], i["valid"]) for i in d["ids"]),
            references=d["refs"],
            value_ids=d["value_ids"],
            sample_hash=int(d["sample_hash"]),
            source_version=d["source_version"],
            occurred_at=d["occurred_at"],
            landing_seq=int(d["landing_seq"]),
            event_id=d["event_id"],
            initial_load=d["initial_load"],
            held=d["held"],
            approved_values=d["approved_values"],
            approved_event_id=d["approved_event_id"],
            rules_checked=int(d["rules_checked"]),
            rules_failed=int(d["rules_failed"]),
            updated_at=d["updated_at"],
        )

    def source_states(self, entity: str, sources: Sequence[SourceKey]) -> dict[SourceKey, SourceState]:
        """Keyed read of the states that exist."""
        rows = self._select_keyed(
            _T("work", "source_state"),
            self._STATE_COLUMNS,
            ("entity", "source_system", "source_key"),
            [(entity, s.system, s.key) for s in sources],
            order_by=("source_system", "source_key"),
        )
        states = (self._state_of(r) for r in rows)
        return {s.source: s for s in states}

    def put_source_states(
        self, states: Sequence[SourceState], *, stored: Mapping[SourceKey, SourceState | None] | None = None
    ) -> int:
        """Upsert; only rows that changed are written (`updated_at` alone is no change). Returns the rows written.

        `held`, `approved_values` and `approved_event_id` are written when a record's state is first
        inserted and never by this upsert afterwards: only a commit's work writes (hold, release,
        approve) move them, so a steward's release or approval committed between the caller's read
        and this write is never undone. A record given twice keeps its last state. `stored` (an addition to the plan's signature) is
        what the caller already read for these records (None for a record with no state yet): the
        comparison then uses it instead of reading the states again. It must be what is stored now.
        """
        if not states:
            return 0
        table = _T("work", "source_state")
        latest = _dedupe_last(list(states), key=lambda s: (s.entity, s.source))
        compared = [c for c in table.columns if c.name != "updated_at" and c.name not in self._STATE_KEPT]
        changed: list[dict[str, Any]] = []
        by_entity: dict[str, list[SourceState]] = {}
        for state in latest:
            by_entity.setdefault(state.entity, []).append(state)
        for entity, group in by_entity.items():
            if stored is None:
                known: Mapping[SourceKey, SourceState | None] = self.source_states(
                    entity, [s.source for s in group]
                )
            else:
                missing = [s.source for s in group if s.source not in stored]
                known = {**stored, **self.source_states(entity, missing)} if missing else stored
            for state in group:
                row = self._state_row(state)
                before = known.get(state.source)
                if before is not None:
                    old = self._state_row(before)
                    if all(
                        row[c.name] == old[c.name]
                        or encode(row[c.name], c.type) == encode(old[c.name], c.type)
                        for c in compared
                    ):
                        continue
                changed.append(row)
        return self._upsert(
            table,
            changed,
            conflict=("entity", "source_system", "source_key"),
            update=[
                c
                for c in self._STATE_COLUMNS
                if c not in ("entity", "source_system", "source_key") and c not in self._STATE_KEPT
            ],
        )

    def states_by_sample_hash(
        self, entity: str, after: tuple[int, str, str] | None, limit: int
    ) -> list[SourceState]:
        """Active states only, ordered by (sample_hash, source_system, source_key)."""
        sql = (
            f"/*mdm:paged*/ SELECT {', '.join(self._STATE_COLUMNS)} FROM {self.t('work', 'source_state')} "
            "WHERE entity = ? AND status = 'active'"
        )
        params: list[Any] = [entity]
        if after is not None:
            sql += " AND (sample_hash, source_system, source_key) > (?, ?, ?)"
            params.extend([int(after[0]), after[1], after[2]])
        rows = self._fetch_all(
            sql + " ORDER BY sample_hash, source_system, source_key LIMIT ?", [*params, int(limit)]
        )
        return [self._state_of(r) for r in rows]

    def states_page(
        self, entity: str, source_system: str | None, after: SourceKey | None, limit: int
    ) -> list[SourceState]:
        """States by (source_system, source_key) after `after`, optionally one source."""
        sql = f"/*mdm:paged*/ SELECT {', '.join(self._STATE_COLUMNS)} FROM {self.t('work', 'source_state')} WHERE entity = ?"
        params: list[Any] = [entity]
        if source_system is not None:
            sql += " AND source_system = ?"
            params.append(source_system)
        if after is not None:
            sql += " AND (source_system, source_key) > (?, ?)"
            params.extend([after.system, after.key])
        rows = self._fetch_all(sql + " ORDER BY source_system, source_key LIMIT ?", [*params, int(limit)])
        return [self._state_of(r) for r in rows]

    # ------------------------------------------------------------------ work group: blocking keys

    def replace_blocking_keys(
        self, entity: str, keys: Mapping[SourceKey, Mapping[str, Sequence[str]]]
    ) -> None:
        """Make each record's keys equal to `keys` by difference: a kept key is never deleted and re-inserted."""
        if not keys:
            return
        table = _T("work", "blocking_key")
        wanted = {
            (entity, p, k, source.system, source.key)
            for source, passes in keys.items()
            for p, values in passes.items()
            for k in values
        }
        with self.transaction():
            rows = self._select_keyed(
                table,
                ("entity", "pass_name", "key_value", "source_system", "source_key"),
                ("entity", "source_system", "source_key"),
                [(entity, s.system, s.key) for s in keys],
                order_by=("source_system", "source_key", "pass_name", "key_value"),
            )
            have = {tuple(r) for r in rows}
            self._delete_keyed(
                table,
                ("entity", "pass_name", "key_value", "source_system", "source_key"),
                sorted(have - wanted),
            )
            self._insert(
                table,
                [
                    {"entity": e, "pass_name": p, "key_value": k, "source_system": s, "source_key": sk}
                    for e, p, k, s, sk in sorted(wanted - have)
                ],
            )

    def key_counts(self, entity: str, pass_keys: Sequence[tuple[str, str]]) -> dict[tuple[str, str], int]:
        """(pass, key) -> the number of records holding it."""
        table = _T("work", "blocking_key")
        unique = self._unique_keys([(entity, p, k) for p, k in pass_keys])
        sql = (
            f"/*mdm:keyed*/ SELECT t.pass_name, t.key_value, COUNT(*) FROM {self._q(table)} AS t "
            f"JOIN {self._row_source()} ON {self._key_join(table, ('entity', 'pass_name', 'key_value'))} "
            "GROUP BY t.pass_name, t.key_value"
        )
        out: dict[tuple[str, str], int] = {}
        for chunk in capacity.chunks(unique, capacity.KEY_CHUNK):
            for p, k, n in self._fetch_all(
                sql, [self._key_doc(table, ("entity", "pass_name", "key_value"), chunk)]
            ):
                out[(p, k)] = int(n)
        return out

    def records_with_keys(
        self, entity: str, pass_keys: Sequence[tuple[str, str]], limit: int
    ) -> list[tuple[str, str, SourceKey]]:
        """(pass, key, source) for the records holding the keys, ordered by (pass, key, source), at most `limit`."""
        table = _T("work", "blocking_key")
        unique = self._unique_keys([(entity, p, k) for p, k in pass_keys])
        sql = (
            f"/*mdm:keyed*/ SELECT t.pass_name, t.key_value, t.source_system, t.source_key FROM {self._q(table)} AS t "
            f"JOIN {self._row_source()} ON {self._key_join(table, ('entity', 'pass_name', 'key_value'))} "
            "ORDER BY t.pass_name, t.key_value, t.source_system, t.source_key LIMIT ?"
        )
        out: list[tuple[str, str, SourceKey]] = []
        for chunk in capacity.chunks(unique, capacity.KEY_CHUNK):
            room = limit - len(out)
            if room <= 0:
                break
            rows = self._fetch_all(
                sql, [self._key_doc(table, ("entity", "pass_name", "key_value"), chunk), room]
            )
            out.extend((p, k, SourceKey(s, sk)) for p, k, s, sk in rows)
        return out

    def blocking_keys_of(
        self, entity: str, sources: Sequence[SourceKey]
    ) -> dict[SourceKey, dict[str, list[str]]]:
        """source -> pass -> keys, for the records given."""
        rows = self._select_keyed(
            _T("work", "blocking_key"),
            ("source_system", "source_key", "pass_name", "key_value"),
            ("entity", "source_system", "source_key"),
            [(entity, s.system, s.key) for s in sources],
            order_by=("source_system", "source_key", "pass_name", "key_value"),
        )
        out: dict[SourceKey, dict[str, list[str]]] = {}
        for s, k, p, v in rows:
            out.setdefault(SourceKey(s, k), {}).setdefault(p, []).append(v)
        return out

    # ------------------------------------------------------------------ work group: the arrival queue

    def queue_put(self, rows: Sequence[tuple[str, SourceKey, str, int]]) -> None:
        """Upsert (entity, source, event_id, landing_seq) into mdm_work.arrival_queue; a record given twice keeps its last row."""
        now = self.clock()
        self._upsert(
            _T("work", "arrival_queue"),
            _dedupe_last(
                [
                    {
                        "entity": e,
                        "source_system": s.system,
                        "source_key": s.key,
                        "event_id": ev,
                        "landing_seq": int(seq),
                        "queued_at": now,
                    }
                    for e, s, ev, seq in rows
                ],
                key=lambda r: (r["entity"], r["source_system"], r["source_key"]),
            ),
            conflict=("entity", "source_system", "source_key"),
            update=("event_id", "landing_seq", "queued_at"),
        )

    def queue_page(
        self, entity: str, after: tuple[int, str, str] | None, limit: int
    ) -> list[tuple[SourceKey, str, int]]:
        """(source, event_id, landing_seq) ordered by (landing_seq, source_system, source_key)."""
        sql = (
            f"/*mdm:paged*/ SELECT source_system, source_key, event_id, landing_seq FROM {self.t('work', 'arrival_queue')} "
            "WHERE entity = ?"
        )
        params: list[Any] = [entity]
        if after is not None:
            sql += " AND (landing_seq, source_system, source_key) > (?, ?, ?)"
            params.extend([int(after[0]), after[1], after[2]])
        rows = self._fetch_all(
            sql + " ORDER BY landing_seq, source_system, source_key LIMIT ?", [*params, int(limit)]
        )
        return [(SourceKey(s, k), ev, int(seq)) for s, k, ev, seq in rows]

    def queue_entities(self) -> list[str]:
        """Entities with queued records, by name (one bounded probe per entity with a model)."""
        out = []
        for entity in self.model_entities():
            if self._fetch_all(
                f"/*mdm:paged*/ SELECT entity FROM {self.t('work', 'arrival_queue')} WHERE entity = ? LIMIT 1",
                [entity],
            ):
                out.append(entity)
        return out

    def queue_size(self) -> int:
        """Queued records in total, for `mdm status`."""
        rows = self._fetch_all(f"/*mdm:aggregate*/ SELECT COUNT(*) FROM {self.t('work', 'arrival_queue')}")
        return int(rows[0][0])

    # ------------------------------------------------------------------ work group: settle, approve, hold, tasks, pairs

    def apply_work(self, work: WorkWrites) -> None:
        """Inside the caller's transaction: settle (DELETE … WHERE key AND event_id = ?), approve, hold,
        release, tasks through open_task (update the open task of the same key, else insert task and
        open_task), pairs (upsert).

        The workbench's writes (initiative 3): first, before any other write, `expect_events` (each record
        still at the event the steward saw, else `Conflict(record_changed)`) and `close_task_ids` (each task
        still open, else `Conflict(task_closed)`), by conditional statements that also hold those rows until
        the transaction ends; then the labels and the records queued again; then the matcher's checkpoint (story
        3.2): quality samples inserted once each, a deleted record's open samples voided with their tasks
        closed (never raising), and each blind answer on a sample still open (`Conflict(sample_void)` or
        `Conflict(sample_settled)` otherwise) with its agreement counted; last, each tray settlement (an entry
        no longer staged is `Conflict(tray_entry_settled)`). A `Conflict` rolls back everything the
        transaction wrote.

        A batch's chunk (story 3.3) adds three steps, which do nothing when their fields are empty. First of
        all, before `expect_events`, `_hold_batch` holds the batch row (`staged` or `committing`, no stop
        asked; else `Conflict(batch_stopped)` or `Conflict(batch_changed)`) and, for a link, its signature's
        bulk rights (`Conflict(bulk_withdrawn)`), so a stop or a withdrawal waits for a chunk in flight and
        stops the next. After the blind answers: the labels a compensation withdraws (`unlabel`); then
        `_write_batch_chunk` (the chunk row exactly once, else `Conflict(chunk_committed)`; its reviews from
        `planned` to `committed`, else `Conflict(item_settled)`; the batch's counts; its locks released);
        then the forced-sample outcomes (`batch_samples`, never raising). The tray settlements stay last, and
        one with `keep_locks` leaves the entry's locks to the batch.
        """
        if work.empty():
            return
        entity = work.entity
        with self.transaction():
            self._hold_batch(entity, work.batch)
            self._expect_events(entity, work.expect_events)
            self._close_tasks(work.close_task_ids)
            self._delete_keyed(
                _T("work", "arrival_queue"),
                ("entity", "source_system", "source_key", "event_id"),
                [(entity, s.system, s.key, ev) for s, ev in work.settle],
            )
            if work.approve:
                table = _T("work", "source_state")
                on = self._key_join(table, ("entity", "source_system", "source_key", "event_id"))
                sql = (
                    f"/*mdm:keyed*/ UPDATE {self._q(table)} AS t SET approved_values = t.std_values, "
                    f"approved_event_id = t.event_id FROM {self._row_source()} WHERE {on}"
                )
                keys = self._unique_keys([(entity, s.system, s.key, ev) for s, ev in work.approve])
                for chunk in capacity.chunks(keys, capacity.KEY_CHUNK):
                    self._execute(
                        sql,
                        [self._key_doc(table, ("entity", "source_system", "source_key", "event_id"), chunk)],
                    )
            state = _T("work", "source_state")
            key3 = ("entity", "source_system", "source_key")
            self._update_keyed(
                state, ("held",), key3, [(entity, s.system, s.key, True) for s in dict.fromkeys(work.hold)]
            )
            self._update_keyed(
                state,
                ("held",),
                key3,
                [(entity, s.system, s.key, False) for s in dict.fromkeys(work.release)],
            )
            self._write_tasks(work.tasks)
            self._write_pairs(entity, work.pairs)
            self.put_labels(work.labels)
            if work.requeue:
                self.queue_put([(entity, s, ev, seq) for s, ev, seq in work.requeue])
            self.put_samples(work.samples)
            self._void_samples(work.void_samples)
            self._write_reviews(work.reviews)
            self.withdraw_labels(entity, work.unlabel)
            self._write_batch_chunk(work.batch)
            self._write_batch_samples(work.batch_samples)
            for settlement in work.tray:
                if not self.settle_tray(
                    settlement.entry_id,
                    settlement.status,
                    change_set_id=settlement.change_set_id,
                    outcome=settlement.outcome,
                    at=self.clock(),
                    keep_locks=settlement.keep_locks,
                ):
                    raise Conflict([_key_token(settlement.entry_id)], code="tray_entry_settled")

    def _expect_events(self, entity: str, expected: Sequence[tuple[SourceKey, str]]) -> None:
        """Each record still at its expected event; a no-op update holds the rows until the transaction ends
        (Postgres row locks; DuckDB serialises on the store lock), so arrival cannot move them meanwhile."""
        if not expected:
            return
        wanted = sorted({(s, ev) for s, ev in expected})
        if len({s for s, _ in wanted}) != len(wanted):  # one record expected at two events: never both
            raise Conflict(sorted({_key_token(s.text()) for s, _ in wanted}), code="record_changed")
        table = _T("work", "source_state")
        key = ("entity", "source_system", "source_key", "event_id")
        sql = (
            f"/*mdm:keyed*/ UPDATE {self._q(table)} AS t SET event_id = t.event_id FROM {self._row_source()} "
            f"WHERE {self._key_join(table, key)}"
        )
        held = self._execute(
            sql, [self._key_doc(table, key, [(entity, s.system, s.key, ev) for s, ev in wanted])]
        )
        if held != len(wanted):
            raise Conflict(sorted({_key_token(s.text()) for s, _ in wanted}), code="record_changed")

    def _close_tasks(self, task_ids: Sequence[str]) -> None:
        """Each task closed by its ID while it is still open, else `Conflict(task_closed)`."""
        ids = sorted(set(task_ids))
        if not ids:
            return
        now = self.clock()
        closed = self._update_keyed(
            _T("work", "task"),
            ("status", "updated_at", "decided_at"),
            ("task_id",),
            [(i, "closed", now, now) for i in ids],
            where="t.status = 'open'",
        )
        if closed != len(ids):
            raise Conflict([_key_token(i) for i in ids], code="task_closed")
        self._delete_keyed(_T("work", "open_task"), ("task_id",), [(i,) for i in ids])

    def _task_row(self, task: Task) -> dict[str, Any]:
        return {
            "task_id": task.task_id,
            "task_key": task.task_key,
            "entity": task.entity,
            "kind": task.kind,
            "status": task.status,
            "source_system": task.source.system if task.source else None,
            "source_key": task.source.key if task.source else None,
            "master_ids": list(task.master_ids),
            # checked where they are written, whatever built them: no free text reaches a task (RULE10)
            "reason": safe(task.reason),
            "suggestion": safe(dict(task.suggestion)),
            "evidence": safe(dict(task.evidence)),
            "event_id": task.event_id,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "due_at": task.due_at,
            "claimed_by": task.claimed_by,
            "claimed_at": task.claimed_at,
            "snoozed_until": task.snoozed_until,
            "snoozed_by": task.snoozed_by,
            "escalated_at": task.escalated_at,
            "escalated_by": task.escalated_by,
            "escalation": task.escalation,
            "decided_at": None,
            # signature batches (story 3.3): the key is derived from the task, never passed separately
            "signature": safe_signature(task.signature),
            "rule_version": task.rule_version,
            "signature_key": task.signature_key,
        }

    #: what a task opened again under its old ID takes from the new row: it starts afresh (a new due time,
    #: no claim, snooze, escalation or decision time); `created_at` stays the first opening's
    _TASK_REOPENED = (
        "status",
        "reason",
        "master_ids",
        "suggestion",
        "evidence",
        "event_id",
        "updated_at",
        "due_at",
        "claimed_by",
        "claimed_at",
        "snoozed_until",
        "snoozed_by",
        "escalated_at",
        "escalated_by",
        "escalation",
        "decided_at",
        "signature",
        "rule_version",
        "signature_key",
    )

    def _write_tasks(self, tasks: Sequence[Task]) -> None:
        if not tasks:
            return
        task_table = _T("work", "task")
        open_table = _T("work", "open_task")
        latest = _dedupe_last(list(tasks), key=lambda t: t.task_key)
        open_now = {
            k: tid
            for k, tid in self._select_keyed(
                open_table,
                ("task_key", "task_id"),
                ("task_key",),
                [(t.task_key,) for t in latest],
                order_by=("task_key",),
            )
        }
        inserts: list[dict[str, Any]] = []
        updates: list[tuple[Any, ...]] = []
        closes: list[tuple[Any, ...]] = []
        for task in latest:
            existing = open_now.get(task.task_key)
            if task.status == "open":
                if existing is None:
                    inserts.append(self._task_row(task))
                else:
                    updates.append(
                        (
                            existing,
                            task.reason,
                            list(task.master_ids),
                            dict(task.suggestion),
                            dict(task.evidence),
                            task.event_id,
                            task.updated_at,
                            safe_signature(task.signature),
                            task.rule_version,
                            task.signature_key,
                        )
                    )
            else:
                closes.append((existing or task.task_id, task.status, task.updated_at, task.updated_at))
        if inserts:
            self._upsert(task_table, inserts, conflict=("task_id",), update=self._TASK_REOPENED)
            self._upsert(
                open_table,
                [{"task_key": r["task_key"], "task_id": r["task_id"]} for r in inserts],
                conflict=("task_key",),
                update=("task_id",),
            )
        self._update_keyed(
            task_table,
            (
                "reason",
                "master_ids",
                "suggestion",
                "evidence",
                "event_id",
                "updated_at",
                "signature",
                "rule_version",
                "signature_key",
            ),
            ("task_id",),
            updates,
        )
        if closes:
            self._update_keyed(task_table, ("status", "updated_at", "decided_at"), ("task_id",), closes)
            self._delete_keyed(open_table, ("task_id",), [(c[0],) for c in closes])

    def _write_pairs(self, entity: str, pairs: Sequence[PairScore]) -> None:
        if not pairs:
            return
        now = self.clock()
        rows = []
        for pair in pairs:
            left, right = (pair.left, pair.right) if pair.left <= pair.right else (pair.right, pair.left)
            e = pair.explanation
            rows.append(
                {
                    "entity": entity,
                    "left_system": left.system,
                    "left_key": left.key,
                    "right_system": right.system,
                    "right_key": right.key,
                    "rule_version": e.rule_version,
                    "score": e.score,
                    "band": e.band.value,
                    "levels": {c.comparison: c.level for c in e.contributions},
                    "explanation": e.to_dict(),
                    "signature": e.signature,
                    "scored_at": now,
                }
            )
        self._upsert(
            _T("work", "candidate_pair"),
            _dedupe_last(
                rows,
                key=lambda r: (
                    r["left_system"],
                    r["left_key"],
                    r["right_system"],
                    r["right_key"],
                    r["rule_version"],
                ),
            ),
            conflict=("entity", "left_system", "left_key", "right_system", "right_key", "rule_version"),
            update=("score", "band", "levels", "explanation", "signature", "scored_at"),
        )

    _PAIR_COLUMNS = (
        "left_system",
        "left_key",
        "right_system",
        "right_key",
        "rule_version",
        "score",
        "band",
        "levels",
        "explanation",
        "signature",
        "scored_at",
    )

    def candidate_pairs(
        self, entity: str, after: tuple[str, str, str, str, int] | None, limit: int
    ) -> list[dict[str, Any]]:
        """Stored candidate pairs by (left, right, rule_version) after `after`, as column -> value."""
        sql = f"/*mdm:paged*/ SELECT {', '.join(self._PAIR_COLUMNS)} FROM {self.t('work', 'candidate_pair')} WHERE entity = ?"
        params: list[Any] = [entity]
        if after is not None:
            sql += " AND (left_system, left_key, right_system, right_key, rule_version) > (?, ?, ?, ?, ?)"
            params.extend([after[0], after[1], after[2], after[3], int(after[4])])
        rows = self._fetch_all(
            sql + " ORDER BY left_system, left_key, right_system, right_key, rule_version LIMIT ?",
            [*params, int(limit)],
        )
        table = _T("work", "candidate_pair")
        return [self._decode_row(table, self._PAIR_COLUMNS, r) for r in rows]

    def pairs_of(self, entity: str, sources: Sequence[SourceKey]) -> list[dict[str, Any]]:
        """Stored candidate pairs with either end among `sources`, by (left, right, rule_version)."""
        table = _T("work", "candidate_pair")
        keys = [(entity, s.system, s.key) for s in sources]
        left = self._select_keyed(
            table, self._PAIR_COLUMNS, ("entity", "left_system", "left_key"), keys, order_by=()
        )
        right = self._select_keyed(
            table, self._PAIR_COLUMNS, ("entity", "right_system", "right_key"), keys, order_by=()
        )
        rows = {tuple(r[:5]): r for r in (*left, *right)}
        return [self._decode_row(table, self._PAIR_COLUMNS, rows[k]) for k in sorted(rows)]

    _TASK_COLUMNS = (
        "task_id",
        "task_key",
        "entity",
        "kind",
        "status",
        "source_system",
        "source_key",
        "master_ids",
        "reason",
        "suggestion",
        "evidence",
        "event_id",
        "created_at",
        "updated_at",
        "due_at",
        "claimed_by",
        "claimed_at",
        "snoozed_until",
        "snoozed_by",
        "escalated_at",
        "escalated_by",
        "escalation",
        "signature",
        "rule_version",
        "signature_key",
    )

    def _task_of(self, row: Sequence[Any]) -> Task:
        d = self._decode_row(_T("work", "task"), self._TASK_COLUMNS, row)
        return Task(
            task_id=d["task_id"],
            task_key=d["task_key"],
            entity=d["entity"],
            kind=d["kind"],
            status=d["status"],
            source=_source(d["source_system"], d["source_key"]),
            master_ids=tuple(d["master_ids"] or ()),
            reason=d["reason"],
            suggestion=d["suggestion"],
            evidence=d["evidence"],
            event_id=d["event_id"],
            created_at=d["created_at"],
            updated_at=d["updated_at"],
            due_at=d["due_at"],
            claimed_by=d["claimed_by"],
            claimed_at=d["claimed_at"],
            snoozed_until=d["snoozed_until"],
            snoozed_by=d["snoozed_by"],
            escalated_at=d["escalated_at"],
            escalated_by=d["escalated_by"],
            escalation=d["escalation"],
            signature=d["signature"],
            rule_version=d["rule_version"],
        )

    def tasks(
        self, entity: str | None, kind: str | None, status: str, limit: int, after: str | None
    ) -> list[Task]:
        """Tasks by task_id after `after`."""
        sql = f"/*mdm:paged*/ SELECT {', '.join(self._TASK_COLUMNS)} FROM {self.t('work', 'task')} WHERE status = ?"
        params: list[Any] = [status]
        if entity is not None:
            sql += " AND entity = ?"
            params.append(entity)
        if kind is not None:
            sql += " AND kind = ?"
            params.append(kind)
        if after is not None:
            sql += " AND task_id > ?"
            params.append(after)
        rows = self._fetch_all(sql + " ORDER BY task_id LIMIT ?", [*params, int(limit)])
        return [self._task_of(r) for r in rows]

    def tasks_by_key(self, task_keys: Sequence[str]) -> dict[str, Task]:
        """The open task of each task key that has one."""
        open_rows = self._select_keyed(
            _T("work", "open_task"),
            ("task_key", "task_id"),
            ("task_key",),
            [(k,) for k in task_keys],
            order_by=("task_key",),
        )
        ids = {tid: key for key, tid in open_rows}
        rows = self._select_keyed(
            _T("work", "task"), self._TASK_COLUMNS, ("task_id",), [(t,) for t in ids], order_by=("task_id",)
        )
        return {ids[r[0]]: self._task_of(r) for r in rows}

    def task_counts(self, status: str = "open") -> dict[tuple[str, str], int]:
        """(entity, kind) -> tasks in `status`, for `mdm status`."""
        rows = self._fetch_all(
            f"/*mdm:aggregate*/ SELECT entity, kind, COUNT(*) FROM {self.t('work', 'task')} WHERE status = ? "
            "GROUP BY entity, kind ORDER BY entity, kind",
            [status],
        )
        return {(e, k): int(n) for e, k, n in rows}

    # ------------------------------------------------------------------ work group: the workbench (initiative 3)

    def _task_filter(self, query: TaskQuery) -> tuple[str, list[Any]]:
        """The inbox's filters over open tasks, as SQL over the task table and its parameters."""
        sql = "status = 'open'"
        params: list[Any] = []
        if query.entity is not None:
            sql += " AND entity = ?"
            params.append(query.entity)
        if query.kind is not None:
            sql += " AND kind = ?"
            params.append(query.kind)
        if query.mine is not None:
            sql += " AND (claimed_by IS NULL OR claimed_by = ? OR claimed_at < ?)"
            params.extend([query.mine, query.lapsed_before or query.now])
        if query.snoozed is False:
            sql += " AND (snoozed_until IS NULL OR snoozed_until <= ?)"
            params.append(query.now)
        elif query.snoozed is True:
            sql += " AND snoozed_until > ?"
            params.append(query.now)
        if query.breaching:
            sql += " AND due_at < ?"
            params.append(query.now)
        if query.claimed_by is not None:
            sql += " AND claimed_by = ? AND claimed_at >= ?"
            params.extend([query.claimed_by, query.lapsed_before or query.now])
        if query.escalated is True:
            sql += " AND escalated_at IS NOT NULL"
        elif query.escalated is False:
            sql += " AND escalated_at IS NULL"
        if query.exclude_kind is not None:
            sql += " AND kind <> ?"
            params.append(query.exclude_kind)
        if query.not_first_decider is not None:
            # keyed by the sample table's (task_id) index: the samples this actor decided first, or confirmed
            # as a batch's second steward, stay out
            sql += (
                f" AND NOT EXISTS (SELECT 1 FROM {self.t('work', 'quality_sample')} AS qs "
                f"WHERE qs.task_id = {self.t('work', 'task')}.task_id AND (qs.decided_by = ? OR qs.checked_by = ?))"
            )
            params.extend([query.not_first_decider, query.not_first_decider])
        # signature batches (story 3.3)
        if query.signature_key is not None:
            sql += " AND signature_key = ?"
            params.append(query.signature_key)
        if query.grouped:
            sql += " AND signature_key IS NOT NULL"
        if query.batch_id is not None:
            # keyed by batch_item's key: only the batch's forced-sample reviews
            sql += (
                f" AND EXISTS (SELECT 1 FROM {self.t('work', 'batch_item')} AS bi WHERE bi.batch_id = ? "
                f"AND bi.task_id = {self.t('work', 'task')}.task_id AND bi.role = 'sample')"
            )
            params.append(query.batch_id)
        return sql, params

    def task_page(self, query: TaskQuery, after: tuple[datetime, str] | None, limit: int) -> list[Task]:
        """Open tasks of the query by (due time, task ID) after the cursor `after`, at most `limit`: keyed by
        the (status, due_at, task_id) index, never an offset. Every open task has a due time (the commit path
        stamps it; `set_due_times` gives one to an older row)."""
        where, params = self._task_filter(query)
        sql = f"/*mdm:paged*/ SELECT {', '.join(self._TASK_COLUMNS)} FROM {self.t('work', 'task')} WHERE {where}"
        if after is not None:
            sql += " AND (due_at, task_id) > (?, ?)"
            params.extend([after[0], after[1]])
        rows = self._fetch_all(sql + " ORDER BY due_at, task_id LIMIT ?", [*params, int(limit)])
        return [self._task_of(r) for r in rows]

    def task_count(self, query: TaskQuery, cap: int) -> int:
        """Open tasks of the query, counted up to `cap`: reads at most `cap` index entries, never the table."""
        where, params = self._task_filter(query)
        rows = self._fetch_all(
            f"/*mdm:paged*/ SELECT COUNT(*) FROM (SELECT 1 FROM {self.t('work', 'task')} WHERE {where} LIMIT ?) "
            "AS capped",
            [*params, int(cap)],
        )
        return int(rows[0][0])

    def open_tasks_without_due(self, after: str | None, limit: int) -> list[Task]:
        """Open tasks with no due time (written before initiative 3), by task ID after `after`."""
        sql = (
            f"/*mdm:paged*/ SELECT {', '.join(self._TASK_COLUMNS)} FROM {self.t('work', 'task')} "
            "WHERE status = 'open' AND due_at IS NULL"
        )
        params: list[Any] = []
        if after is not None:
            sql += " AND task_id > ?"
            params.append(after)
        rows = self._fetch_all(sql + " ORDER BY task_id LIMIT ?", [*params, int(limit)])
        return [self._task_of(r) for r in rows]

    def set_due_times(self, rows: Sequence[tuple[str, datetime]]) -> int:
        """(task ID, due time) for tasks that have none yet; returns the tasks given one."""
        return self._update_keyed(
            _T("work", "task"),
            ("due_at",),
            ("task_id",),
            [(task_id, due) for task_id, due in dict(rows).items()],
            where="t.due_at IS NULL",
        )

    def open_tasks_by_reason(
        self, entity: str, kind: str, reason: str, after: str | None, limit: int
    ) -> list[Task]:
        """Open tasks of one entity, kind and reason by task ID after `after`, at most `limit`: the records the
        quality breaker held, on the (entity, reason, status) index."""
        sql = (
            f"/*mdm:paged*/ SELECT {', '.join(self._TASK_COLUMNS)} FROM {self.t('work', 'task')} "
            "WHERE entity = ? AND reason = ? AND status = 'open' AND kind = ?"
        )
        params: list[Any] = [entity, reason, kind]
        if after is not None:
            sql += " AND task_id > ?"
            params.append(after)
        rows = self._fetch_all(sql + " ORDER BY task_id LIMIT ?", [*params, int(limit)])
        return [self._task_of(r) for r in rows]

    def tasks_by_id(self, task_ids: Sequence[str]) -> dict[str, Task]:
        """Tasks by ID, in any status."""
        rows = self._select_keyed(
            _T("work", "task"),
            self._TASK_COLUMNS,
            ("task_id",),
            [(t,) for t in task_ids],
            order_by=("task_id",),
        )
        return {r[0]: self._task_of(r) for r in rows}

    def tasks_for_sources(self, entity: str, sources: Sequence[SourceKey]) -> list[Task]:
        """The open tasks of these source records, by task ID."""
        rows = self._select_keyed(
            _T("work", "task"),
            self._TASK_COLUMNS,
            ("source_system", "source_key"),
            [(s.system, s.key) for s in sources],
            order_by=("task_id",),
            where="t.status = 'open' AND t.entity = ?",
            params=[entity],
        )
        return sorted((self._task_of(r) for r in rows), key=lambda t: t.task_id)

    # signature groups (story 3.3)

    def signature_groups(self, entity: str, rule_version: int, window: int) -> list[tuple[str, str, int]]:
        """(group key, signature, reviews) of the signature groups among the `window` open reviews of one entity
        and match rule version due soonest, largest first (then by key): one capped aggregate on the task
        table's (signature_key, status, due_at, task_id) index, never an unbounded read."""
        rows = self._fetch_all(
            "/*mdm:paged*/ SELECT signature_key, MIN(signature), COUNT(*) FROM (SELECT signature_key, signature "
            f"FROM {self.t('work', 'task')} WHERE status = 'open' AND kind = 'review' AND entity = ? "
            "AND rule_version = ? AND signature_key IS NOT NULL ORDER BY due_at, task_id LIMIT ?) AS capped "
            "GROUP BY signature_key ORDER BY COUNT(*) DESC, signature_key",
            [entity, int(rule_version), int(window)],
        )
        return [(str(key), str(signature), int(n)) for key, signature, n in rows]

    def older_rules_count(self, entity: str, rule_version: int, cap: int) -> int:
        """Open reviews of one entity that carry a group key under another match rule version, counted up to
        `cap`: the reviews scored under an earlier version, which are not grouped."""
        rows = self._fetch_all(
            f"/*mdm:paged*/ SELECT COUNT(*) FROM (SELECT 1 FROM {self.t('work', 'task')} WHERE status = 'open' "
            "AND kind = 'review' AND entity = ? AND signature_key IS NOT NULL AND rule_version <> ? LIMIT ?) "
            "AS capped",
            [entity, int(rule_version), int(cap)],
        )
        return int(rows[0][0])

    def group_members(self, signature_key: str, after: tuple[datetime, str] | None, limit: int) -> list[Task]:
        """The open reviews of one signature group by (due time, task ID) after the cursor `after`, at most
        `limit`: keyed by the group key's index, never an offset."""
        sql = (
            f"/*mdm:paged*/ SELECT {', '.join(self._TASK_COLUMNS)} FROM {self.t('work', 'task')} "
            "WHERE signature_key = ? AND status = 'open' AND kind = 'review'"
        )
        params: list[Any] = [safe(signature_key)]
        if after is not None:
            sql += " AND (due_at, task_id) > (?, ?)"
            params.extend([after[0], after[1]])
        rows = self._fetch_all(sql + " ORDER BY due_at, task_id LIMIT ?", [*params, int(limit)])
        return [self._task_of(r) for r in rows]

    def open_tasks_without_signature(self, after: str | None, limit: int) -> list[Task]:
        """Open reviews of a source record with no signature yet (written before story 3.3), by task ID after
        `after`, at most `limit`: the backfill's pages."""
        sql = (
            f"/*mdm:paged*/ SELECT {', '.join(self._TASK_COLUMNS)} FROM {self.t('work', 'task')} "
            "WHERE status = 'open' AND kind = 'review' AND source_system IS NOT NULL AND signature IS NULL"
        )
        params: list[Any] = []
        if after is not None:
            sql += " AND task_id > ?"
            params.append(after)
        rows = self._fetch_all(sql + " ORDER BY task_id LIMIT ?", [*params, int(limit)])
        return [self._task_of(r) for r in rows]

    def set_task_signatures(self, rows: Sequence[tuple[str, str, int | None]]) -> int:
        """(task ID, signature, rule version) for tasks that have no signature yet; the group key is derived
        from each task's entity, rule version and signature, as `Task.signature_key` derives it. `''` stores
        no signature, so the backfill never scans the task again. Returns the tasks given one."""
        wanted = dict((task_id, (signature, version)) for task_id, signature, version in rows)
        if not wanted:
            return 0
        with self.transaction():
            entities = dict(
                self._select_keyed(
                    _T("work", "task"),
                    ("task_id", "entity"),
                    ("task_id",),
                    [(t,) for t in wanted],
                    order_by=("task_id",),
                    where="t.signature IS NULL",
                )
            )
            updates = []
            for task_id, entity in entities.items():
                signature, version = wanted[task_id]
                key = batch_signature_key(entity, version, signature)
                updates.append((task_id, safe_signature(signature or ""), version, key))
            return self._update_keyed(
                _T("work", "task"),
                ("signature", "rule_version", "signature_key"),
                ("task_id",),
                updates,
                where="t.signature IS NULL",
            )

    def _not_staged(self) -> str:
        """The condition that no staged decision holds the task's tray lock: one parameter, `task:<task ID>`
        (a keyed read of the lock table)."""
        return f" AND NOT EXISTS (SELECT 1 FROM {self.t('work', 'tray_lock')} AS l WHERE l.subject = ?)"

    def claim_task(
        self, task_id: str, actor: str, at: datetime, lapsed_before: datetime, *, staging: bool = False
    ) -> bool:
        """Claims an open task for `actor` unless another actor's claim runs (taken at or after
        `lapsed_before`) or, unless `staging` (the tray claims just before it takes the lock), a staged
        decision holds it; a claim wakes a snoozed task. True when claimed (or claimed again)."""
        sql = (
            f"/*mdm:keyed*/ UPDATE {self.t('work', 'task')} SET claimed_by = ?, claimed_at = ?, "
            "snoozed_until = NULL, snoozed_by = NULL WHERE task_id = ? AND status = 'open' "
            "AND (claimed_by IS NULL OR claimed_by = ? OR claimed_at < ?)"
        )
        params: list[Any] = [actor, at, task_id, actor, lapsed_before]
        if not staging:
            sql += self._not_staged()
            params.append(f"task:{task_id}")
        return self._execute(sql, params) == 1

    def release_task(self, task_id: str, actor: str) -> bool:
        """Releases `actor`'s own claim; False when the actor holds none."""
        return (
            self._execute(
                f"/*mdm:keyed*/ UPDATE {self.t('work', 'task')} SET claimed_by = NULL, claimed_at = NULL "
                "WHERE task_id = ? AND claimed_by = ?",
                [task_id, actor],
            )
            == 1
        )

    def snooze_task(
        self, task_id: str, actor: str, until: datetime, lapsed_before: datetime | None = None
    ) -> bool:
        """Snoozes an open task until `until` and releases its claim, unless a staged decision holds it; with
        `lapsed_before`, only while no other actor's claim runs. True when snoozed."""
        sql = (
            f"/*mdm:keyed*/ UPDATE {self.t('work', 'task')} SET snoozed_until = ?, snoozed_by = ?, "
            "claimed_by = NULL, claimed_at = NULL WHERE task_id = ? AND status = 'open'" + self._not_staged()
        )
        params: list[Any] = [until, actor, task_id, f"task:{task_id}"]
        if lapsed_before is not None:
            sql += " AND (claimed_by IS NULL OR claimed_by = ? OR claimed_at < ?)"
            params.extend([actor, lapsed_before])
        return self._execute(sql, params) == 1

    def escalate_task(
        self, task_id: str, actor: str, code: str, at: datetime, lapsed_before: datetime | None = None
    ) -> bool:
        """Marks an open task escalated with a reason code and releases its claim, unless a staged decision
        holds it; with `lapsed_before`, only while no other actor's claim runs. True when escalated."""
        sql = (
            f"/*mdm:keyed*/ UPDATE {self.t('work', 'task')} SET escalated_at = ?, escalated_by = ?, "
            "escalation = ?, claimed_by = NULL, claimed_at = NULL WHERE task_id = ? AND status = 'open'"
            + self._not_staged()
        )
        params: list[Any] = [at, actor, safe(code), task_id, f"task:{task_id}"]
        if lapsed_before is not None:
            sql += " AND (claimed_by IS NULL OR claimed_by = ? OR claimed_at < ?)"
            params.extend([actor, lapsed_before])
        return self._execute(sql, params) == 1

    # the undo tray

    _TRAY_COLUMNS = (
        "entry_id",
        "task_id",
        "entity",
        "decision",
        "target",
        "subject",
        "signature",
        "actor",
        "actor_role",
        "persona",
        "event_id",
        "planning_version",
        "staged_at",
        "deadline",
        "status",
        "attempts",
        "settled_at",
        "change_set_id",
        "outcome",
    )

    def _tray_of(self, row: Sequence[Any]) -> TrayEntry:
        """A tray row (its columns, then the commit version joined from the audit, or None)."""
        table = _T("work", "tray_entry")
        d = self._decode_row(table, self._TRAY_COLUMNS, row[: len(self._TRAY_COLUMNS)])
        version = row[len(self._TRAY_COLUMNS)] if len(row) > len(self._TRAY_COLUMNS) else None
        return TrayEntry(
            entry_id=d["entry_id"],
            task_id=d["task_id"],
            entity=d["entity"],
            decision=d["decision"],
            target=d["target"],
            subject=d["subject"] or {},
            signature=d["signature"],
            actor=d["actor"],
            actor_role=d["actor_role"],
            persona=bool(d["persona"]),
            event_id=d["event_id"],
            planning_version=int(d["planning_version"]),
            staged_at=d["staged_at"],
            deadline=d["deadline"],
            status=d["status"],
            attempts=int(d["attempts"]),
            settled_at=d["settled_at"],
            change_set_id=d["change_set_id"],
            commit_version=int(version) if version is not None else None,
            outcome=d["outcome"],
        )

    def _tray_select(self) -> str:
        """The tray's columns and the commit version its change set got (the audit, keyed by change set ID)."""
        return (
            f"SELECT {', '.join('t.' + c for c in self._TRAY_COLUMNS)}, c.commit_version "
            f"FROM {self.t('work', 'tray_entry')} AS t"
        )

    def _tray_audit_join(self) -> str:
        return f" LEFT JOIN {self.t('audit', 'change_set')} AS c ON c.change_set_id = t.change_set_id"

    def stage_tray(self, entry: TrayEntry, locks: Sequence[str]) -> None:
        """One transaction: every lock, then the entry. A lock another staged entry holds refuses the whole
        stage with `Conflict(already_staged, mine=…)` (whether that entry is the same actor's), writing
        nothing."""
        subjects = sorted(set(locks))
        with self.transaction():
            taken = self._insert(
                _T("work", "tray_lock"),
                [{"subject": s, "entry_id": entry.entry_id} for s in subjects],
                on_conflict_nothing=("subject",),
            )
            if taken != len(subjects):
                holders = self.staged_by_locks(subjects)
                mine = any(h.actor == entry.actor for h in holders.values() if h.entry_id != entry.entry_id)
                raise Conflict([_key_token(entry.task_id)], code="already_staged", mine=mine)
            self._insert(
                _T("work", "tray_entry"),
                [
                    {
                        "entry_id": entry.entry_id,
                        "task_id": entry.task_id,
                        "entity": entry.entity,
                        "decision": entry.decision,
                        "target": entry.target,
                        "subject": safe(dict(entry.subject)),
                        "signature": safe_signature(entry.signature),
                        "actor": entry.actor,
                        "actor_role": entry.actor_role,
                        "persona": entry.persona,
                        "event_id": entry.event_id,
                        "planning_version": entry.planning_version,
                        "staged_at": entry.staged_at,
                        "deadline": entry.deadline,
                        "status": entry.status,
                        "attempts": entry.attempts,
                        "settled_at": entry.settled_at,
                        "change_set_id": entry.change_set_id,
                        "outcome": entry.outcome,
                    }
                ],
            )

    def settle_tray(
        self,
        entry_id: str,
        status: str,
        *,
        change_set_id: str | None,
        outcome: str,
        at: datetime,
        keep_locks: bool = False,
    ) -> bool:
        """Settles a staged entry (`committed`, `undone` or `failed`, with an outcome code) and frees its locks;
        False when it is no longer staged. Joins an open transaction. With `keep_locks` the locks stay: a
        batch's first chunk settles its entry `committed` with outcome `committing`, and the batch releases its
        reviews' locks chunk by chunk."""
        with self.transaction():
            changed = self._execute(
                f"/*mdm:keyed*/ UPDATE {self.t('work', 'tray_entry')} SET status = ?, change_set_id = ?, "
                "outcome = ?, settled_at = ? WHERE entry_id = ? AND status = 'staged'",
                [status, change_set_id, safe(outcome), at, entry_id],
            )
            if changed != 1:
                return False
            if not keep_locks:
                self._execute(
                    f"/*mdm:keyed*/ DELETE FROM {self.t('work', 'tray_lock')} WHERE entry_id = ?", [entry_id]
                )
        return True

    def finish_tray_batch(self, entry_id: str, outcome: str, at: datetime) -> bool:
        """The tray entry of a batch whose chunks committed (`committed`, outcome `committing`) gets its final
        outcome and settlement time; its status list is untouched, so its CHECK holds. False when the entry is
        not `committed`. Joins an open transaction."""
        return (
            self._execute(
                f"/*mdm:keyed*/ UPDATE {self.t('work', 'tray_entry')} SET outcome = ?, settled_at = ? "
                "WHERE entry_id = ? AND status = 'committed'",
                [safe(outcome), at, entry_id],
            )
            == 1
        )

    def fail_tray(self, entry_id: str, outcome: str, at: datetime, task_id: str, actor: str) -> bool:
        """One transaction: the entry settled `failed` with `outcome` and, when that changed it, the task's
        claim released when `actor` (the entry's) still holds it; another's claim stays. False when the entry
        was no longer staged (nothing written)."""
        with self.transaction():
            if not self.settle_tray(entry_id, "failed", change_set_id=None, outcome=outcome, at=at):
                return False
            self._execute(
                f"/*mdm:keyed*/ UPDATE {self.t('work', 'task')} SET claimed_by = NULL, claimed_at = NULL "
                "WHERE task_id = ? AND claimed_by = ?",
                [task_id, actor],
            )
        return True

    def bump_tray_attempts(self, entry_id: str) -> int:
        """One more attempt at a staged entry; returns the attempts now (0 when it is no longer staged)."""
        rows = self._fetch_all(
            f"/*mdm:keyed*/ UPDATE {self.t('work', 'tray_entry')} SET attempts = attempts + 1 "
            "WHERE entry_id = ? AND status = 'staged' RETURNING attempts",
            [entry_id],
        )
        return int(rows[0][0]) if rows else 0

    def tray_entries(self, entry_ids: Sequence[str]) -> dict[str, TrayEntry]:
        """Entries by ID, with the commit version their change set got."""
        table = _T("work", "tray_entry")
        unique = self._unique_keys([(i,) for i in entry_ids])
        sql = (
            f"/*mdm:keyed*/ {self._tray_select()} JOIN {self._row_source()} ON {self._key_join(table, ('entry_id',))}"
            f"{self._tray_audit_join()} ORDER BY t.entry_id"
        )
        out: dict[str, TrayEntry] = {}
        for chunk in capacity.chunks(unique, capacity.KEY_CHUNK):
            for row in self._fetch_all(sql, [self._key_doc(table, ("entry_id",), chunk)]):
                entry = self._tray_of(row)
                out[entry.entry_id] = entry
        return out

    def due_tray(self, now: datetime, limit: int) -> list[TrayEntry]:
        """Staged entries whose deadline has passed, by (deadline, entry ID), at most `limit`."""
        rows = self._fetch_all(
            f"/*mdm:paged*/ {self._tray_select()}{self._tray_audit_join()} "
            "WHERE t.status = 'staged' AND t.deadline <= ? ORDER BY t.deadline, t.entry_id LIMIT ?",
            [now, int(limit)],
        )
        return [self._tray_of(r) for r in rows]

    #: an entry still live: staged, or a batch's whose chunks still commit
    _TRAY_LIVE = "(t.status = 'staged' OR (t.status = 'committed' AND t.outcome = 'committing'))"

    def tray_of_actor(
        self, actor: str, since: datetime, limit: int, *, staged_since: datetime | None = None
    ) -> list[TrayEntry]:
        """The actor's entries still staged, still committing (a batch's, outcome `committing`) or settled
        since `since`, newest first, at most `limit`; only those staged since `staged_since` (default: a day
        before `since`, longer than any undo window and flush delay), so the walk of the (actor, staged_at)
        index stops there.

        It also lists the entries of the batches the actor confirmed as their second steward (story 3.3), in
        a second branch walked on the batch table's (checker, staged_at) index; each branch is paged, and the
        two are merged newest first under one limit. Each entry comes once: a maker never confirms their own
        batch."""
        floor = staged_since if staged_since is not None else since - timedelta(days=1)
        live = f"({self._TRAY_LIVE} OR t.settled_at >= ?)"
        columns = ", ".join("t." + c for c in self._TRAY_COLUMNS)
        audit = self._tray_audit_join()
        own = (
            f"SELECT {columns}, c.commit_version FROM {self.t('work', 'tray_entry')} AS t{audit} "
            f"WHERE t.actor = ? AND t.staged_at >= ? AND {live} ORDER BY t.staged_at DESC, t.entry_id LIMIT ?"
        )
        confirmed = (
            f"SELECT {columns}, c.commit_version FROM {self.t('work', 'batch')} AS b "
            f"JOIN {self.t('work', 'tray_entry')} AS t ON t.entry_id = b.entry_id{audit} "
            f"WHERE b.checker = ? AND b.staged_at >= ? AND {live} ORDER BY b.staged_at DESC, t.entry_id LIMIT ?"
        )
        names = ", ".join(f"u.{c}" for c in (*self._TRAY_COLUMNS, "commit_version"))
        rows = self._fetch_all(
            f"/*mdm:paged*/ SELECT {names} FROM (SELECT * FROM ({own}) AS mine UNION ALL "
            f"SELECT * FROM ({confirmed}) AS theirs) AS u ORDER BY u.staged_at DESC, u.entry_id LIMIT ?",
            [actor, floor, since, int(limit), actor, floor, since, int(limit), int(limit)],
        )
        return [self._tray_of(r) for r in rows]

    def tray_live_count(self, actor: str, now: datetime, cap: int) -> int:
        """The actor's live entries (staged, or a batch's still committing), those of the batches they
        confirmed as second steward included, counted up to `cap`: the same two branches as `tray_of_actor`,
        each capped, staged within the day before `now`."""
        floor = now - timedelta(days=1)
        rows = self._fetch_all(
            f"/*mdm:paged*/ SELECT (SELECT COUNT(*) FROM (SELECT 1 FROM {self.t('work', 'tray_entry')} AS t "
            f"WHERE t.actor = ? AND t.staged_at >= ? AND {self._TRAY_LIVE} LIMIT ?) AS mine), "
            f"(SELECT COUNT(*) FROM (SELECT 1 FROM {self.t('work', 'batch')} AS b "
            f"JOIN {self.t('work', 'tray_entry')} AS t ON t.entry_id = b.entry_id "
            f"WHERE b.checker = ? AND b.staged_at >= ? AND {self._TRAY_LIVE} LIMIT ?) AS theirs)",
            [actor, floor, int(cap), actor, floor, int(cap)],
        )
        return min(int(cap), int(rows[0][0]) + int(rows[0][1]))

    def staged_by_locks(self, locks: Sequence[str]) -> dict[str, TrayEntry]:
        """Lock subject -> the entry holding it, for the subjects held: a staged entry, or a batch's entry whose
        chunks still commit (`committed` with outcome `committing`), whose remaining locks still hold."""
        table = _T("work", "tray_lock")
        unique = self._unique_keys([(s,) for s in locks])
        sql = (
            f"/*mdm:keyed*/ SELECT l.subject, {', '.join('t.' + c for c in self._TRAY_COLUMNS)} "
            f"FROM {self._q(table)} AS l JOIN {self._row_source()} ON {self._key_join(table, ('subject',), 'l')} "
            f"JOIN {self.t('work', 'tray_entry')} AS t ON t.entry_id = l.entry_id "
            f"WHERE {self._TRAY_LIVE} ORDER BY l.subject"
        )
        out: dict[str, TrayEntry] = {}
        for chunk in capacity.chunks(unique, capacity.KEY_CHUNK):
            for row in self._fetch_all(sql, [self._key_doc(table, ("subject",), chunk)]):
                out[row[0]] = self._tray_of(row[1:])
        return out

    def staged_count(self, cap: int) -> int:
        """Staged entries, counted up to `cap`."""
        rows = self._fetch_all(
            f"/*mdm:paged*/ SELECT COUNT(*) FROM (SELECT 1 FROM {self.t('work', 'tray_entry')} "
            "WHERE status = 'staged' LIMIT ?) AS capped",
            [int(cap)],
        )
        return int(rows[0][0])

    # a steward's labels

    _LABEL_COLUMNS = (
        "entity",
        "left_ref",
        "right_ref",
        "label",
        "rule_version",
        "score",
        "band",
        "signature",
        "task_id",
        "entry_id",
        "decided_by",
        "decided_role",
        "decided_at",
    )

    def put_labels(self, labels: Sequence[MatchLabel]) -> int:
        """Upserts each pair's latest label (a pair given twice keeps its last)."""
        if not labels:
            return 0
        rows = [
            {
                "entity": lab.entity,
                "left_ref": lab.left_ref,
                "right_ref": lab.right_ref,
                "label": lab.label,
                "rule_version": lab.rule_version,
                "score": lab.score,
                "band": lab.band,
                "signature": safe_signature(lab.signature),
                "task_id": lab.task_id,
                "entry_id": lab.entry_id,
                "decided_by": lab.decided_by,
                "decided_role": lab.decided_role,
                "decided_at": lab.decided_at,
            }
            for lab in labels
        ]
        return self._upsert(
            _T("work", "match_label"),
            _dedupe_last(rows, key=lambda r: (r["entity"], r["left_ref"], r["right_ref"])),
            conflict=("entity", "left_ref", "right_ref"),
            update=[c for c in self._LABEL_COLUMNS if c not in ("entity", "left_ref", "right_ref")],
        )

    def labels_for(self, entity: str, left_refs: Sequence[str]) -> list[MatchLabel]:
        """The labels whose left side is one of `left_refs`, by (left, right)."""
        table = _T("work", "match_label")
        rows = self._select_keyed(
            table,
            self._LABEL_COLUMNS,
            ("entity", "left_ref"),
            [(entity, r) for r in left_refs],
            order_by=("left_ref", "right_ref"),
        )
        out = []
        for row in rows:
            d = self._decode_row(table, self._LABEL_COLUMNS, row)
            out.append(
                MatchLabel(
                    entity=d["entity"],
                    left_ref=d["left_ref"],
                    right_ref=d["right_ref"],
                    label=d["label"],
                    rule_version=int(d["rule_version"]) if d["rule_version"] is not None else None,
                    score=float(d["score"]) if d["score"] is not None else None,
                    band=d["band"],
                    signature=d["signature"],
                    task_id=d["task_id"],
                    entry_id=d["entry_id"],
                    decided_by=d["decided_by"],
                    decided_role=d["decided_role"],
                    decided_at=d["decided_at"],
                )
            )
        return out

    def label_counts(self, entity: str, signature: str, cap: int) -> dict[str, int]:
        """{"match": n, "not_a_match": n}: the labels that carry `signature` (the latest per pair, under any rule
        version), each counted up to `cap` on the (entity, signature, label) index. Keep-apart labels carry no
        signature, so only match and "not a match" count. The signature is bound as a parameter."""
        checked = safe_signature(signature) or ""
        out: dict[str, int] = {}
        for label in ("match", "not_a_match"):
            rows = self._fetch_all(
                f"/*mdm:paged*/ SELECT COUNT(*) FROM (SELECT 1 FROM {self.t('work', 'match_label')} "
                "WHERE entity = ? AND signature = ? AND label = ? LIMIT ?) AS capped",
                [entity, checked, label, int(cap)],
            )
            out[label] = int(rows[0][0])
        return out

    def withdraw_labels(self, entity: str, keys: Sequence[tuple[str, str, str]]) -> int:
        """(left_ref, right_ref, entry_id): each label deleted while it is still the one that entry wrote, so a
        label another decision wrote since stays (a compensation withdraws its batch's labels). Joins an open
        transaction; returns the labels withdrawn."""
        return self._delete_keyed(
            _T("work", "match_label"),
            ("entity", "left_ref", "right_ref", "entry_id"),
            [(entity, left, right, safe(entry_id)) for left, right, entry_id in keys],
        )

    def queue_rows(self, entity: str, sources: Sequence[SourceKey]) -> list[tuple[SourceKey, str, int]]:
        """The arrival queue's rows of these records, in landing order."""
        rows = self._select_keyed(
            _T("work", "arrival_queue"),
            ("source_system", "source_key", "event_id", "landing_seq"),
            ("entity", "source_system", "source_key"),
            [(entity, s.system, s.key) for s in sources],
            order_by=("landing_seq", "source_system", "source_key"),
        )
        out = [(SourceKey(s, k), ev, int(seq)) for s, k, ev, seq in rows]
        return sorted(out, key=lambda r: (r[2], r[0]))

    # ------------------------------------------------------------------ work group: the matcher's checkpoint (story 3.2)

    _SAMPLE_COLUMNS = (
        "sample_id",
        "entity",
        "origin",
        "decision",
        "source_system",
        "source_key",
        "master_ids",
        "event_id",
        "target",
        "declined",
        "band",
        "signature",
        "score",
        "rule_version",
        "decided_by",
        "decided_role",
        "decided_at",
        "entry_id",
        "task_id",
        "drawn_at",
        "pair_sources",
        "status",
        "answer",
        "reviewed_by",
        "reviewed_role",
        "reviewed_at",
        "review_entry_id",
        "dispute_task_id",
        "checked_by",
    )

    @staticmethod
    def _sample_row(sample: QualitySample) -> dict[str, Any]:
        """A sample as a row; codes, IDs and source keys checked by `safe`, the signature by `safe_signature`
        (whatever built them: no value reaches a sample)."""
        return {
            "sample_id": safe(sample.sample_id),
            "entity": safe(sample.entity),
            "origin": safe(sample.origin),
            "decision": safe(sample.decision),
            "source_system": sample.source.system if sample.source else None,
            "source_key": sample.source.key if sample.source else None,
            "master_ids": safe(list(sample.master_ids)),
            "event_id": sample.event_id,
            "target": safe(sample.target),
            "declined": safe(list(sample.declined)),
            "band": safe(sample.band or ""),
            "signature": safe_signature(sample.signature or "") or "",
            "score": sample.score,
            "rule_version": sample.rule_version,
            "decided_by": sample.decided_by,
            "decided_role": safe(sample.decided_role),
            "decided_at": sample.decided_at,
            "entry_id": safe(sample.entry_id),
            "task_id": safe(sample.task_id),
            "drawn_at": sample.drawn_at,
            "pair_sources": safe(list(sample.pair_sources)),
            "status": safe(sample.status),
            "answer": safe(sample.answer),
            "reviewed_by": sample.reviewed_by,
            "reviewed_role": safe(sample.reviewed_role),
            "reviewed_at": sample.reviewed_at,
            "review_entry_id": safe(sample.review_entry_id),
            "dispute_task_id": safe(sample.dispute_task_id),
            "checked_by": sample.checked_by,  # an actor name, as decided_by and reviewed_by are
        }

    def _sample_of(self, row: Sequence[Any]) -> QualitySample:
        d = self._decode_row(_T("work", "quality_sample"), self._SAMPLE_COLUMNS, row)
        return QualitySample(
            sample_id=d["sample_id"],
            entity=d["entity"],
            origin=d["origin"],
            decision=d["decision"],
            source=_source(d["source_system"], d["source_key"]),
            master_ids=tuple(d["master_ids"] or ()),
            event_id=d["event_id"],
            target=d["target"],
            declined=tuple(d["declined"] or ()),
            band=d["band"] or "",
            signature=d["signature"] or "",
            score=float(d["score"]) if d["score"] is not None else None,
            rule_version=int(d["rule_version"]) if d["rule_version"] is not None else None,
            decided_by=d["decided_by"],
            decided_role=d["decided_role"],
            decided_at=d["decided_at"],
            entry_id=d["entry_id"],
            task_id=d["task_id"],
            drawn_at=d["drawn_at"],
            pair_sources=tuple(d["pair_sources"] or ()),
            status=d["status"],
            answer=d["answer"],
            reviewed_by=d["reviewed_by"],
            reviewed_role=d["reviewed_role"],
            reviewed_at=d["reviewed_at"],
            review_entry_id=d["review_entry_id"],
            dispute_task_id=d["dispute_task_id"],
            checked_by=d["checked_by"],
        )

    def put_samples(self, samples: Sequence[QualitySample]) -> int:
        """Inserts each sample once: a sample ID drawn again (a page planned again) changes nothing."""
        if not samples:
            return 0
        rows = _dedupe_last([self._sample_row(s) for s in samples], key=lambda r: r["sample_id"])
        return self._insert(_T("work", "quality_sample"), rows, on_conflict_nothing=("sample_id",))

    def samples_by_id(self, sample_ids: Sequence[str]) -> dict[str, QualitySample]:
        """Samples by ID, in any status."""
        rows = self._select_keyed(
            _T("work", "quality_sample"),
            self._SAMPLE_COLUMNS,
            ("sample_id",),
            [(i,) for i in sample_ids],
            order_by=("sample_id",),
        )
        return {r[0]: self._sample_of(r) for r in rows}

    def open_samples_for(self, entity: str, sources: Sequence[SourceKey]) -> list[QualitySample]:
        """The open samples of these records, by sample ID."""
        rows = self._select_keyed(
            _T("work", "quality_sample"),
            self._SAMPLE_COLUMNS,
            ("entity", "source_system", "source_key"),
            [(entity, s.system, s.key) for s in sources],
            order_by=("sample_id",),
            where="t.status = 'open'",
        )
        return sorted((self._sample_of(r) for r in rows), key=lambda s: s.sample_id)

    def _capped_samples(self, where: str, params: Sequence[Any], cap: int) -> int:
        rows = self._fetch_all(
            f"/*mdm:paged*/ SELECT COUNT(*) FROM (SELECT 1 FROM {self.t('work', 'quality_sample')} "
            f"WHERE {where} LIMIT ?) AS capped",
            [*params, int(cap)],
        )
        return int(rows[0][0])

    def open_sample_count(self, entity: str, origin: str, cap: int) -> int:
        """Open samples of one entity and origin, counted up to `cap` (the open-sample cap's index)."""
        return self._capped_samples("entity = ? AND origin = ? AND status = 'open'", [entity, origin], cap)

    def sample_counts(self, entity: str, cap: int) -> dict[str, int]:
        """{"open": n, "void": n} of one entity's samples, each counted up to `cap`."""
        return {
            status: self._capped_samples("entity = ? AND status = ?", [entity, status], cap)
            for status in ("open", "void")
        }

    def recent_reviews(
        self, entity: str, origin: str, band: str, decided_after: datetime | None, limit: int
    ) -> list[tuple[str, datetime, str]]:
        """(status, reviewed_at, sample ID) of the latest reviewed samples of one entity, origin and band, newest
        first, at most `limit`; with `decided_after`, only samples first decided after it. A walk of the
        (entity, origin, band, reviewed_at, sample_id) index."""
        sql = (
            f"/*mdm:paged*/ SELECT status, reviewed_at, sample_id FROM {self.t('work', 'quality_sample')} "
            "WHERE entity = ? AND origin = ? AND band = ? AND reviewed_at IS NOT NULL"
        )
        params: list[Any] = [entity, origin, band]
        if decided_after is not None:
            sql += " AND decided_at > ?"
            params.append(decided_after)
        rows = self._fetch_all(
            sql + " ORDER BY reviewed_at DESC, sample_id DESC LIMIT ?", [*params, int(limit)]
        )
        return [(str(st), _utc(at), str(sid)) for st, at, sid in rows]

    def recent_reviews_of_signature(
        self, entity: str, origin: str, signature: str, decided_after: datetime | None, limit: int
    ) -> list[tuple[str, datetime, str]]:
        """(status, reviewed_at, sample ID) of the latest reviewed samples of one entity, origin and signature,
        newest first, at most `limit`; with `decided_after`, only samples first decided after it: the bulk
        trigger's window, a walk of the (entity, origin, signature, reviewed_at, sample_id) index."""
        sql = (
            f"/*mdm:paged*/ SELECT status, reviewed_at, sample_id FROM {self.t('work', 'quality_sample')} "
            "WHERE entity = ? AND origin = ? AND signature = ? AND reviewed_at IS NOT NULL"
        )
        params: list[Any] = [entity, origin, safe_signature(signature) or ""]
        if decided_after is not None:
            sql += " AND decided_at > ?"
            params.append(decided_after)
        rows = self._fetch_all(
            sql + " ORDER BY reviewed_at DESC, sample_id DESC LIMIT ?", [*params, int(limit)]
        )
        return [(str(st), _utc(at), str(sid)) for st, at, sid in rows]

    _AGREEMENT_COLUMNS = ("entity", "origin", "band", "signature", "reviewed", "agreed", "updated_at")

    def agreement_of(self, entity: str, signature: str) -> list[AgreementRow]:
        """The agreement counts of one entity and signature, of every origin and band that has a row, by
        (origin, band): one keyed read of quality_agreement on its key."""
        checked = safe_signature(signature) or ""
        keys = [(entity, origin, band, checked) for origin in SAMPLE_ORIGINS for band in _AGREEMENT_BANDS]
        rows = self._select_keyed(
            _T("work", "quality_agreement"),
            ("entity", "origin", "band", "signature", "reviewed", "agreed"),
            ("entity", "origin", "band", "signature"),
            keys,
            order_by=("origin", "band"),
        )
        return [AgreementRow(e, o, b, sig, int(n), int(a)) for e, o, b, sig, n, a in rows]

    def agreement_rows(
        self, entity: str, after: tuple[str, str, str] | None, limit: int
    ) -> list[AgreementRow]:
        """One entity's agreement counts by (origin, band, signature) after `after`, at most `limit`."""
        sql = (
            "/*mdm:paged*/ SELECT entity, origin, band, signature, reviewed, agreed "
            f"FROM {self.t('work', 'quality_agreement')} WHERE entity = ?"
        )
        params: list[Any] = [entity]
        if after is not None:
            sql += " AND (origin, band, signature) > (?, ?, ?)"
            params.extend(after)
        rows = self._fetch_all(sql + " ORDER BY origin, band, signature LIMIT ?", [*params, int(limit)])
        return [AgreementRow(e, o, b, sig, int(n), int(a)) for e, o, b, sig, n, a in rows]

    def void_samples(self, voids: Sequence[tuple[str, str, SourceKey | None]]) -> None:
        """(sample ID, task ID, record): each sample still open voided and each task still open closed, in a
        transaction of its own (or the one open); never raises for one settled already."""
        with self.transaction():
            self._void_samples(voids)

    def _void_samples(self, voids: Sequence[tuple[str, str, SourceKey | None]]) -> None:
        """Each sample still open voided, and each task still open closed; never raises (a sample answered
        or voided already stays as it is)."""
        if not voids:
            return
        now = self.clock()
        self._update_keyed(
            _T("work", "quality_sample"),
            ("status",),
            ("sample_id",),
            [(sid, "void") for sid in sorted({v[0] for v in voids})],
            where="t.status = 'open'",
        )
        task_ids = sorted({v[1] for v in voids})
        self._update_keyed(
            _T("work", "task"),
            ("status", "updated_at", "decided_at"),
            ("task_id",),
            [(i, "closed", now, now) for i in task_ids],
            where="t.status = 'open'",
        )
        self._delete_keyed(_T("work", "open_task"), ("task_id",), [(i,) for i in task_ids])

    def _write_reviews(self, reviews: Sequence[SampleReview]) -> None:
        """Each blind answer on a sample still open, then its agreement counted: a sample voided meanwhile is
        `Conflict(sample_void)`, one answered already `Conflict(sample_settled)`. The count is read and
        written again in the tray's transaction, which the tray's lease serialises."""
        if not reviews:
            return
        table = _T("work", "quality_sample")
        for review in reviews:
            changed = self._execute(
                f"/*mdm:keyed*/ UPDATE {self._q(table)} SET status = ?, answer = ?, reviewed_by = ?, "
                "reviewed_role = ?, reviewed_at = ?, review_entry_id = ?, dispute_task_id = ? "
                "WHERE sample_id = ? AND status = 'open'",
                [
                    "agreed" if review.agreed else "disagreed",
                    safe(review.answer),
                    review.reviewed_by,
                    safe(review.reviewed_role),
                    review.reviewed_at,
                    safe(review.review_entry_id),
                    safe(review.dispute_task_id),
                    safe(review.sample_id),
                ],
            )
            if changed != 1:
                found = self.samples_by_id([review.sample_id]).get(review.sample_id)
                code = "sample_void" if found is not None and found.status == "void" else "sample_settled"
                raise Conflict([_key_token(review.sample_id)], code=code)
        totals: dict[tuple[str, str, str, str], list[int]] = {}
        for review in reviews:
            key = (
                safe(review.entity),
                safe(review.origin),
                safe(review.band or ""),
                safe_signature(review.signature or "") or "",
            )
            counted = totals.setdefault(key, [0, 0])
            counted[0] += 1
            counted[1] += 1 if review.agreed else 0
        agreement = _T("work", "quality_agreement")
        key_columns = ("entity", "origin", "band", "signature")
        held = {
            tuple(r[:4]): (int(r[4]), int(r[5]))
            for r in self._select_keyed(
                agreement,
                ("entity", "origin", "band", "signature", "reviewed", "agreed"),
                key_columns,
                list(totals),
                order_by=key_columns,
            )
        }
        now = self.clock()
        rows = []
        for key, (reviewed, agreed) in sorted(totals.items()):
            before = held.get(key, (0, 0))
            rows.append(
                {
                    "entity": key[0],
                    "origin": key[1],
                    "band": key[2],
                    "signature": key[3],
                    "reviewed": before[0] + reviewed,
                    "agreed": before[1] + agreed,
                    "updated_at": now,
                }
            )
        self._upsert(agreement, rows, conflict=key_columns, update=("reviewed", "agreed", "updated_at"))

    # the quality breaker's state

    _BREAKER_COLUMNS = (
        "entity",
        "band",
        "state",
        "watch_since",
        "trigger",
        "figures",
        "tripped_at",
        "trip_change_set",
        "restored_at",
        "restored_by",
        "restored_role",
        "restore_reason",
        "restore_change_set",
        "updated_at",
        "signature",
    )

    def _breaker_of(self, row: Sequence[Any]) -> BreakerState:
        d = self._decode_row(_T("work", "breaker_state"), self._BREAKER_COLUMNS, row)
        return BreakerState(**{**d, "figures": d["figures"] or {}})

    def breaker_states(
        self, entities: Sequence[str], *, band: str = AUTO_BAND
    ) -> dict[tuple[str, str], BreakerState]:
        """(entity, band) -> its breaker state, for the entities that have a row of `band` (the automatic band
        by default): keyed on the full key, so it reads one row per entity, never an entity's bulk rows."""
        rows = self._select_keyed(
            _T("work", "breaker_state"),
            self._BREAKER_COLUMNS,
            ("entity", "band"),
            [(e, band) for e in entities],
            order_by=("entity", "band"),
        )
        return {(r[0], r[1]): self._breaker_of(r) for r in rows}

    def breaker_state(self, entity: str, band: str) -> BreakerState | None:
        """One band's breaker state (the automatic band, or a signature's bulk rights); None when it has no row
        yet. Keyed on the full key."""
        return self.breaker_states([entity], band=band).get((entity, band))

    def withdrawn_bulk(self, entity: str, after: str | None, limit: int) -> list[BreakerState]:
        """One entity's signatures whose bulk rights are withdrawn (demoted `bulk:` bands), by band after
        `after`, at most `limit`: keyset-paged on the key, never the automatic band. The prefix is matched with
        LIKE, so the bound assumes nothing about how punctuation sorts under the database's collation."""
        sql = (
            f"/*mdm:paged*/ SELECT {', '.join(self._BREAKER_COLUMNS)} FROM {self.t('work', 'breaker_state')} "
            "WHERE entity = ? AND band LIKE ? AND state = 'demoted'"
        )
        params: list[Any] = [entity, BULK_PREFIX + "%"]
        if after is not None:
            sql += " AND band > ?"
            params.append(after)
        rows = self._fetch_all(sql + " ORDER BY band LIMIT ?", [*params, int(limit)])
        return [self._breaker_of(r) for r in rows]

    def ensure_breaker_rows(
        self, entities: Sequence[str], at: datetime, band: str = AUTO_BAND, signature: str | None = None
    ) -> int:
        """A `normal` row of `band` for each entity that has none, watched from `at`; a bulk-rights row keeps
        its signature (through `safe_signature`). Returns the rows written."""
        wanted = sorted(set(entities))
        if not wanted:
            return 0
        return self._insert(
            _T("work", "breaker_state"),
            [
                {
                    "entity": safe(e),
                    "band": safe(band),
                    "state": "normal",
                    "watch_since": at,
                    "figures": {},
                    "updated_at": at,
                    "signature": safe_signature(signature),
                }
                for e in wanted
            ],
            on_conflict_nothing=("entity", "band"),
        )

    def trip_breaker(
        self,
        entity: str,
        band: str,
        trigger: str,
        figures: Mapping[str, Any],
        at: datetime,
        change_set_id: str,
    ) -> bool:
        """Demotes a normal band, with the trigger, safe figures and the audit change set; False when the band
        is demoted already (or has no row). Joins an open transaction."""
        return (
            self._update_keyed(
                _T("work", "breaker_state"),
                ("state", "trigger", "figures", "tripped_at", "trip_change_set", "updated_at"),
                ("entity", "band"),
                [(entity, band, "demoted", safe(trigger), safe(dict(figures)), at, safe(change_set_id), at)],
                where="t.state = 'normal'",
            )
            == 1
        )

    def restore_breaker(
        self,
        entity: str,
        band: str,
        actor: str,
        role: str,
        reason: str,
        at: datetime,
        change_set_id: str,
        *,
        rewatch: bool = False,
    ) -> bool:
        """Restores a demoted band; with `rewatch`, its volume is watched afresh from `at` (the days of history
        start again), else `watch_since` stays. False when it is not demoted."""
        columns = ["state", "restored_at", "restored_by", "restored_role", "restore_reason"]
        values: list[Any] = ["normal", at, actor, safe(role), safe(reason)]
        columns += ["restore_change_set", "updated_at"]
        values += [safe(change_set_id), at]
        if rewatch:
            columns.append("watch_since")
            values.append(at)
        return (
            self._update_keyed(
                _T("work", "breaker_state"),
                tuple(columns),
                ("entity", "band"),
                [(entity, band, *values)],
                where="t.state = 'demoted'",
            )
            == 1
        )

    def hold_band(self, entity: str, band: str, signature: str | None = None) -> bool:
        """Inside the commit's transaction: True while the band is normal, holding its row until the
        transaction ends (Postgres by the row lock of a no-op update, DuckDB on the store lock), so a trip
        waits for a commit in flight and a commit never outruns a trip; False when it is demoted. A band with
        no row gets a normal one first, so there is always a row to hold; a signature's bulk-rights row keeps
        its signature."""
        self.ensure_breaker_rows([entity], self.clock(), band, signature)
        held = self._update_keyed(
            _T("work", "breaker_state"),
            (),
            ("entity", "band"),
            [(entity, band)],
            extra_set="state = t.state",
            where="t.state = 'normal'",
        )
        if held == 1:
            return True
        found = self.breaker_state(entity, band)
        return found is None or found.state != "demoted"

    # arrivals per entity and clock hour, for the breaker's volume trigger

    def add_arrivals(self, rows: Sequence[tuple[str, datetime, int]]) -> None:
        """Adds (entity, hour start, arrivals) to the stored counts: a keyed read, then an upsert of the new
        totals (only arrival writes these rows, under its lease)."""
        totals: dict[tuple[str, datetime], int] = {}
        for entity, hour, n in rows:
            if n:
                key = (entity, _utc(hour))
                totals[key] = totals.get(key, 0) + int(n)
        if not totals:
            return
        table = _T("work", "arrival_hour")
        held = {
            (e, _utc(h)): int(n)
            for e, h, n in self._select_keyed(
                table,
                ("entity", "hour_start", "arrivals"),
                ("entity", "hour_start"),
                list(totals),
                order_by=("entity", "hour_start"),
            )
        }
        now = self.clock()
        self._upsert(
            table,
            [
                {"entity": e, "hour_start": h, "arrivals": held.get((e, h), 0) + n, "updated_at": now}
                for (e, h), n in sorted(totals.items())
            ],
            conflict=("entity", "hour_start"),
            update=("arrivals", "updated_at"),
        )

    def arrival_hours(self, entity: str, hour_starts: Sequence[datetime]) -> dict[datetime, int]:
        """Hour start -> arrivals of one entity, for the hours that have a count."""
        rows = self._select_keyed(
            _T("work", "arrival_hour"),
            ("hour_start", "arrivals"),
            ("entity", "hour_start"),
            [(entity, _utc(h)) for h in hour_starts],
            order_by=("hour_start",),
        )
        return {_utc(h): int(n) for h, n in rows}

    def arrivals_counted_by(self, entity: str, moment: datetime) -> bool:
        """Whether one entity has a count for an hour that starts at or before `moment`: the volume trigger's
        history is there only once arrivals were counted that long ago (an initial load counts none)."""
        rows = self._fetch_all(
            f"/*mdm:paged*/ SELECT hour_start FROM {self.t('work', 'arrival_hour')} "
            "WHERE entity = ? AND hour_start <= ? ORDER BY hour_start LIMIT 1",
            [entity, _utc(moment)],
        )
        return bool(rows)

    def prune_arrival_hours(self, before: datetime) -> int:
        """Drops the counts of hours that start before `before` (a few hundred rows per entity at most)."""
        return self._execute(
            f"/*mdm:small*/ DELETE FROM {self.t('work', 'arrival_hour')} WHERE hour_start < ?", [before]
        )

    # ------------------------------------------------------------------ work group: signature batches (story 3.3)
    #
    # Every transaction that touches a batch takes its batch row first (the class docstring's lock order).

    _BATCH_COLUMNS = _T("work", "batch").column_names()
    _ITEM_COLUMNS = _T("work", "batch_item").column_names()
    _CHUNK_COLUMNS = _T("work", "batch_chunk").column_names()
    #: the batch columns that hold an actor's name, written as a tray entry's actor is (never through safe())
    _BATCH_ACTORS = frozenset({"maker", "checker", "stop_requested_by"})

    @staticmethod
    def _statuses(values: Sequence[str], allowed: Sequence[str] | frozenset[str]) -> str:
        """`(…)` of status literals for a conditional statement: each must be one the service knows, so no
        text reaches a statement except a known code."""
        chosen = tuple(dict.fromkeys(values))
        if not chosen or any(v not in allowed for v in chosen):
            raise ValueError("not a known status")
        return "(" + ", ".join(f"'{v}'" for v in chosen) + ")"

    def _batch_value(self, column: str, value: Any) -> Any:
        """A batch column's value as it is written: an actor's name as given, the signature through
        `safe_signature`, figures and every other text through `safe`."""
        if value is None or column in self._BATCH_ACTORS:
            return value
        if column == "signature":
            return safe_signature(value) or ""
        if column == "figures":
            return safe(dict(value))
        if isinstance(value, str):
            return safe(value)
        return value

    def _batch_row(self, batch: Batch) -> dict[str, Any]:
        return {c: self._batch_value(c, getattr(batch, c)) for c in self._BATCH_COLUMNS}

    def _batch_of(self, row: Sequence[Any]) -> Batch:
        d = self._decode_row(_T("work", "batch"), self._BATCH_COLUMNS, row)
        return Batch(
            **{
                **d,
                "persona": bool(d["persona"]),
                "signature": d["signature"] or "",
                "figures": d["figures"] or {},
            }
        )

    def _item_value(self, column: str, value: Any) -> Any:
        """A batch review's column value as it is written: texts through `safe`, changes as a safe list."""
        if value is None:
            return None
        if column == "changes":
            return safe(list(value))
        if isinstance(value, str):
            return safe(value)
        return value

    def _item_row(self, item: BatchItem, at: datetime) -> dict[str, Any]:
        row: dict[str, Any] = {}
        for column in self._ITEM_COLUMNS:
            if column == "source_system":
                value: Any = item.source.system
            elif column == "source_key":
                value = item.source.key
            elif column == "updated_at":
                value = item.updated_at or at
            else:
                value = getattr(item, column)
            row[column] = self._item_value(column, value)
        return row

    def _item_of(self, row: Sequence[Any]) -> BatchItem:
        d = self._decode_row(_T("work", "batch_item"), self._ITEM_COLUMNS, row)
        system, key = d.pop("source_system"), d.pop("source_key")
        return BatchItem(
            **{
                **d,
                "source": SourceKey(system, key),
                "score": float(d["score"]) if d["score"] is not None else None,
                "review": bool(d["review"]),
                "changes": tuple(d["changes"] or ()),
                "split_applied": bool(d["split_applied"]),
            }
        )

    def _chunk_of(self, row: Sequence[Any]) -> BatchChunk:
        return BatchChunk(**self._decode_row(_T("work", "batch_chunk"), self._CHUNK_COLUMNS, row))

    def insert_batch(self, batch: Batch, items: Sequence[BatchItem]) -> None:
        """One transaction: for a link batch, its group's `open_batch` row (a group holding one already is
        `Conflict(batch_open, batch=<the holder>)`); for a compensation, which takes no `open_batch` row, the
        mark on its original first (`set_compensated_by`; a mark already there is
        `Conflict(already_compensated)`), so the mark and the compensation exist together or not at all; then
        the batch row and its reviews, chunked. A refusal writes nothing."""
        with self.transaction():
            if batch.kind == "link":
                if not batch.signature_key:
                    raise ValueError("a link batch needs its group's key")
                taken = self._insert(
                    _T("work", "open_batch"),
                    [{"signature_key": safe(batch.signature_key), "batch_id": safe(batch.batch_id)}],
                    on_conflict_nothing=("signature_key",),
                )
                if taken != 1:
                    holder = self._fetch_all(
                        f"/*mdm:keyed*/ SELECT batch_id FROM {self.t('work', 'open_batch')} WHERE signature_key = ?",
                        [batch.signature_key],
                    )
                    raise Conflict(
                        [batch.signature_key], code="batch_open", batch=holder[0][0] if holder else None
                    )
            if batch.compensates is not None and not self.set_compensated_by(
                batch.compensates, batch.batch_id
            ):
                raise Conflict([_key_token(batch.compensates)], code="already_compensated")
            self._insert(_T("work", "batch"), [self._batch_row(batch)])
            at = batch.updated_at
            self._insert(_T("work", "batch_item"), [self._item_row(item, at) for item in items])

    def set_compensated_by(self, original_id: str, batch_id: str) -> bool:
        """Marks the original batch with the compensation that reverses it, only while it has no mark; False
        otherwise. Called only inside `insert_batch`."""
        return (
            self._execute(
                f"/*mdm:keyed*/ UPDATE {self.t('work', 'batch')} SET compensated_by = ?, updated_at = ? "
                "WHERE batch_id = ? AND compensated_by IS NULL",
                [safe(batch_id), self.clock(), original_id],
            )
            == 1
        )

    def clear_compensated_by(self, original_id: str, batch_id: str) -> bool:
        """Clears the original batch's mark only when it names `batch_id`, so a compensation clears only its
        own mark; False otherwise. Joins an open transaction."""
        return (
            self._execute(
                f"/*mdm:keyed*/ UPDATE {self.t('work', 'batch')} SET compensated_by = NULL, updated_at = ? "
                "WHERE batch_id = ? AND compensated_by = ?",
                [self.clock(), original_id, batch_id],
            )
            == 1
        )

    def batches(self, batch_ids: Sequence[str]) -> dict[str, Batch]:
        """Batches by ID, in any status: keyed."""
        rows = self._select_keyed(
            _T("work", "batch"),
            self._BATCH_COLUMNS,
            ("batch_id",),
            [(b,) for b in batch_ids],
            order_by=("batch_id",),
        )
        return {r[0]: self._batch_of(r) for r in rows}

    def open_batches_of_groups(self, keys: Sequence[str]) -> dict[str, Batch]:
        """Group key -> its open batch, for the groups that have one: keyed through `open_batch`, and only a
        batch in an open status."""
        table = _T("work", "open_batch")
        unique = self._unique_keys([(k,) for k in keys])
        columns = ", ".join("b." + c for c in self._BATCH_COLUMNS)
        sql = (
            f"/*mdm:keyed*/ SELECT o.signature_key, {columns} FROM {self._q(table)} AS o "
            f"JOIN {self._row_source()} ON {self._key_join(table, ('signature_key',), 'o')} "
            f"JOIN {self.t('work', 'batch')} AS b ON b.batch_id = o.batch_id "
            f"WHERE b.status IN {self._statuses(OPEN_BATCH_STATUSES, BATCH_STATUSES)} ORDER BY o.signature_key"
        )
        out: dict[str, Batch] = {}
        for chunk in capacity.chunks(unique, capacity.KEY_CHUNK):
            for row in self._fetch_all(sql, [self._key_doc(table, ("signature_key",), chunk)]):
                out[row[0]] = self._batch_of(row[1:])
        return out

    def batch_items(
        self,
        batch_id: str,
        roles: Sequence[str],
        statuses: Sequence[str] | None,
        after_position: int | None,
        limit: int,
    ) -> list[BatchItem]:
        """One batch's reviews of `roles` (and `statuses`, when given) by position after `after_position`, at
        most `limit`: paged on the (batch_id, role, status, position) index. Positions are unique within a
        batch."""
        sql = (
            f"/*mdm:paged*/ SELECT {', '.join(self._ITEM_COLUMNS)} FROM {self.t('work', 'batch_item')} "
            f"WHERE batch_id = ? AND role IN {self._statuses(roles, ITEM_ROLES)}"
        )
        params: list[Any] = [batch_id]
        if statuses is not None:
            sql += f" AND status IN {self._statuses(statuses, _ITEM_STATUSES)}"
        if after_position is not None:
            sql += " AND position > ?"
            params.append(int(after_position))
        rows = self._fetch_all(sql + " ORDER BY position, task_id LIMIT ?", [*params, int(limit)])
        return [self._item_of(r) for r in rows]

    def items_by_task(self, task_ids: Sequence[str]) -> dict[str, list[BatchItem]]:
        """Task ID -> its reviews in every batch that holds it (a sample outcome, an earlier split, the case's
        sample line), by batch ID: keyed on the (task_id, status) index."""
        rows = self._select_keyed(
            _T("work", "batch_item"),
            self._ITEM_COLUMNS,
            ("task_id",),
            [(t,) for t in task_ids],
            order_by=("task_id", "batch_id"),
        )
        out: dict[str, list[BatchItem]] = {}
        for row in rows:
            item = self._item_of(row)
            out.setdefault(item.task_id, []).append(item)
        return out

    def update_items(
        self,
        batch_id: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        from_status: str | Sequence[str],
    ) -> int:
        """Each row (`task_id` and the columns to set, every row naming the same columns) written to the
        batch's review while it is in `from_status`; returns the reviews changed. `updated_at` is stamped
        unless given. Texts go through `safe`. Inside a batch transaction the caller holds the batch row first
        (`hold_batch` or `set_batch`); the status is a service constant, rendered as a literal."""
        if not rows:
            return 0
        names = [c for c in rows[0] if c != "task_id"]
        for row in rows:
            if set(row) != {"task_id", *names}:
                raise ValueError("rows for batch_item name different columns")
        unknown = set(names) - set(self._ITEM_COLUMNS) | ({"batch_id"} & set(names))
        if unknown:
            raise MdmError("unknown_column", table="batch_item", columns=tuple(sorted(unknown)))
        if "updated_at" not in names:
            names.append("updated_at")
        now = self.clock()
        statuses = (from_status,) if isinstance(from_status, str) else tuple(from_status)
        values = [
            (
                batch_id,
                row["task_id"],
                *(self._item_value(c, row[c] if c in row else now) for c in names),
            )
            for row in rows
        ]
        return self._update_keyed(
            _T("work", "batch_item"),
            tuple(names),
            ("batch_id", "task_id"),
            values,
            where=f"t.status IN {self._statuses(statuses, _ITEM_STATUSES)}",
        )

    def set_batch(self, batch_id: str, *, from_statuses: Sequence[str], **fields: Any) -> bool:
        """Writes `fields` (batch columns) to the batch while it is in one of `from_statuses`; True when it
        changed. `updated_at` is stamped unless given. It is a conditional update of the batch row, so it holds
        the row first, as the lock order asks; it joins an open transaction."""
        unknown = set(fields) - set(self._BATCH_COLUMNS) | ({"batch_id"} & set(fields))
        if unknown or not fields:
            raise MdmError("unknown_column", table="batch", columns=tuple(sorted(unknown)))
        values = dict(fields)
        values.setdefault("updated_at", self.clock())
        names = tuple(values)
        return (
            self._update_keyed(
                _T("work", "batch"),
                names,
                ("batch_id",),
                [(batch_id, *(self._batch_value(c, values[c]) for c in names))],
                where=f"t.status IN {self._statuses(from_statuses, BATCH_STATUSES)}",
            )
            == 1
        )

    def hold_batch(self, batch_id: str, statuses: Sequence[str]) -> Batch | None:
        """Inside the caller's transaction: holds the batch row with a no-op conditional update while it is in
        one of `statuses`, and returns it read back; None when it is in no such status (nothing held). The
        first statement of every batch transaction that `apply_work`'s own hold does not open."""
        with self.transaction():
            held = self._update_keyed(
                _T("work", "batch"),
                (),
                ("batch_id",),
                [(batch_id,)],
                extra_set="status = t.status",
                where=f"t.status IN {self._statuses(statuses, BATCH_STATUSES)}",
            )
            if held != 1:
                return None
            return self.batches([batch_id]).get(batch_id)

    def fail_items(
        self, batch_id: str, failures: Sequence[tuple[str, str, str]], subjects: Sequence[str]
    ) -> int:
        """One transaction, the batch held first (`staged` or `committing`; else nothing is written and 0 is
        returned): each (task ID, status, reason) review still `planned` moved to `failed` (a link) or `kept`
        (a compensation) with its reason, then `subjects` deleted from the tray's locks, so each task returns
        to the queue. Returns the reviews moved."""
        with self.transaction():
            if self.hold_batch(batch_id, ("staged", "committing")) is None:
                return 0
            moved = self.update_items(
                batch_id,
                [{"task_id": t, "status": status, "reason": reason} for t, status, reason in failures],
                from_status="planned",
            )
            self.release_locks(subjects)
            return moved

    def undo_batch(
        self, batch_id: str, entry_id: str, at: datetime, *, after_hold: Callable[[], None] | None = None
    ) -> bool:
        """One transaction: first the conditional update that takes a `staged` batch with this entry back to
        `ready` (its entry, stage time and checker cleared); no row changed returns False, having written
        nothing (on Postgres it waits for a chunk that holds the batch, and then matches nothing, since the
        chunk left it `committing`). Then `after_hold()` when given (a test's fault point), the entry settled
        `undone` with its locks freed (`Conflict(already_settled)` when it is no longer staged, rolling back),
        and the reviews' blind-review flags cleared."""
        with self.transaction():
            changed = self._execute(
                f"/*mdm:keyed*/ UPDATE {self.t('work', 'batch')} SET status = 'ready', entry_id = NULL, "
                "staged_at = NULL, checker = NULL, checker_role = NULL, checked_at = NULL, updated_at = ? "
                "WHERE batch_id = ? AND status = 'staged' AND entry_id = ?",
                [at, batch_id, entry_id],
            )
            if changed != 1:
                return False
            if after_hold is not None:
                after_hold()
            if not self.settle_tray(entry_id, "undone", change_set_id=None, outcome="undone", at=at):
                raise Conflict([_key_token(entry_id)], code="already_settled", batch=batch_id)
            self._execute(
                f"/*mdm:keyed*/ UPDATE {self.t('work', 'batch_item')} SET review = false, updated_at = ? "
                "WHERE batch_id = ? AND review = true",
                [at, batch_id],
            )
        return True

    def apply_split(
        self,
        batch_id: str,
        sample_task_id: str,
        task_ids: Sequence[str],
        reason: str,
        accesses: Sequence[AccessRow],
    ) -> int:
        """One transaction, the batch held first (`sampling`; else nothing is written and 0 is returned): the
        reviews moved to role `split` with `reason` (`split:<comparison>`) while they are still a sample or
        bulk review, so a review split meanwhile stays; a bulk review's status becomes `split`, and a sample
        review keeps its outcome. Then the disagreeing review's `split_applied` set, and the access rows
        appended (one per split record on a personal comparison). Returns the reviews moved."""
        now = self.clock()
        with self.transaction():
            if self.hold_batch(batch_id, ("sampling",)) is None:
                return 0
            moved = self._update_keyed(
                _T("work", "batch_item"),
                ("reason", "updated_at"),
                ("batch_id", "task_id"),
                [(batch_id, t, safe(reason), now) for t in sorted(set(task_ids))],
                extra_set=f"role = 'split', status = CASE WHEN t.role = 'bulk' THEN '{SPLIT_STATUS}' ELSE t.status END",
                where="t.role IN ('sample', 'bulk')",
            )
            self._execute(
                f"/*mdm:keyed*/ UPDATE {self.t('work', 'batch_item')} SET split_applied = true, updated_at = ? "
                "WHERE batch_id = ? AND task_id = ?",
                [now, batch_id, sample_task_id],
            )
            self.append_accesses(accesses)
            return moved

    def request_stop(self, batch_id: str, actor: str, at: datetime) -> bool:
        """Asks a committing batch to stop, once: it records who asked and when, and clears `not_before`, so a
        stop never waits for the throttle. False when the batch is not committing or a stop was asked
        already."""
        return (
            self._execute(
                f"/*mdm:keyed*/ UPDATE {self.t('work', 'batch')} SET stop_requested_by = ?, stop_requested_at = ?, "
                "not_before = NULL, updated_at = ? WHERE batch_id = ? AND status = 'committing' "
                "AND stop_requested_at IS NULL",
                [actor, at, at, batch_id],
            )
            == 1
        )

    def batches_due(self, now: datetime, limit: int) -> list[Batch]:
        """Committing batches whose next chunk may commit now, by batch ID, at most `limit`: past their throttle
        (`not_before`), or asked to stop, or whose signature's bulk rights are withdrawn (a keyed `EXISTS` on
        breaker_state's key), so a stop or a withdrawal is due at once, throttle or not."""
        columns = ", ".join("t." + c for c in self._BATCH_COLUMNS)
        rows = self._fetch_all(
            f"/*mdm:paged*/ SELECT {columns} FROM {self.t('work', 'batch')} AS t WHERE t.status = 'committing' "
            "AND (t.not_before IS NULL OR t.not_before <= ? OR t.stop_requested_at IS NOT NULL OR EXISTS "
            f"(SELECT 1 FROM {self.t('work', 'breaker_state')} AS b WHERE b.entity = t.entity "
            "AND b.band = t.bulk_band AND b.state = 'demoted')) ORDER BY t.batch_id LIMIT ?",
            [now, int(limit)],
        )
        return [self._batch_of(r) for r in rows]

    def batches_by_status(self, statuses: Sequence[str], after: str | None, limit: int) -> list[Batch]:
        """Batches in `statuses` by batch ID after `after`, at most `limit`."""
        sql = (
            f"/*mdm:paged*/ SELECT {', '.join(self._BATCH_COLUMNS)} FROM {self.t('work', 'batch')} "
            f"WHERE status IN {self._statuses(statuses, BATCH_STATUSES)}"
        )
        params: list[Any] = []
        if after is not None:
            sql += " AND batch_id > ?"
            params.append(after)
        rows = self._fetch_all(sql + " ORDER BY batch_id LIMIT ?", [*params, int(limit)])
        return [self._batch_of(r) for r in rows]

    def to_confirm_count(self, actor: str, entity: str | None, cap: int) -> int:
        """Batches waiting for a second steward (`awaiting_checker`) whose maker is not `actor`, of one entity
        when given, counted up to `cap`."""
        sql = (
            f"/*mdm:paged*/ SELECT COUNT(*) FROM (SELECT 1 FROM {self.t('work', 'batch')} "
            "WHERE status = 'awaiting_checker' AND maker <> ?"
        )
        params: list[Any] = [actor]
        if entity is not None:
            sql += " AND entity = ?"
            params.append(entity)
        rows = self._fetch_all(sql + " LIMIT ?) AS capped", [*params, int(cap)])
        return int(rows[0][0])

    def batch_chunks(self, batch_id: str) -> list[BatchChunk]:
        """One batch's committed chunks by number: a batch has at most `BATCH_MAX` chunks."""
        rows = self._fetch_all(
            f"/*mdm:paged*/ SELECT {', '.join(self._CHUNK_COLUMNS)} FROM {self.t('work', 'batch_chunk')} "
            "WHERE batch_id = ? ORDER BY chunk_no LIMIT ?",
            [batch_id, int(capacity.BATCH_MAX)],
        )
        return [self._chunk_of(r) for r in rows]

    def release_locks(self, subjects: Sequence[str]) -> int:
        """Deletes these subjects from the tray's locks (a batch's reviews, chunk by chunk); returns the locks
        freed. Joins an open transaction."""
        return self._delete_keyed(_T("work", "tray_lock"), ("subject",), [(s,) for s in subjects])

    def finish_batch(self, batch_id: str, *, status: str, outcome: str, at: datetime, entry_id: str) -> bool:
        """Ends a `staged` or `committing` batch with this entry (any other batch is left as it is, and False
        returned), in one transaction, the batch row first: its status (`stopped`, `failed`, or `committed` for
        a batch whose last reviews all failed after its first chunk), outcome and `finished_at`; every
        `planned` review moved to `released` with the outcome as its reason; the entry's remaining locks
        deleted; the tray entry finished (`finish_tray_batch` when `committed`, else settled `failed` with the
        outcome); the group's `open_batch` row deleted; and, for a compensation that ends `stopped` or
        `failed`, its original's mark cleared (`clear_compensated_by`), so the rest of the original can be
        compensated again, while the original chunks it undid keep their `compensated_by`."""
        with self.transaction():
            changed = self._execute(
                f"/*mdm:keyed*/ UPDATE {self.t('work', 'batch')} SET status = ?, outcome = ?, finished_at = ?, "
                "not_before = NULL, updated_at = ? WHERE batch_id = ? AND status IN ('staged', 'committing') "
                "AND entry_id = ?",
                [self._statuses_one(status), safe(outcome), at, at, batch_id, entry_id],
            )
            if changed != 1:
                return False
            batch = self.batches([batch_id])[batch_id]
            self._execute(
                f"/*mdm:keyed*/ UPDATE {self.t('work', 'batch_item')} SET status = 'released', reason = ?, "
                "updated_at = ? WHERE batch_id = ? AND status = 'planned'",
                [safe(outcome), at, batch_id],
            )
            self._execute(
                f"/*mdm:keyed*/ DELETE FROM {self.t('work', 'tray_lock')} WHERE entry_id = ?", [entry_id]
            )
            if not self.finish_tray_batch(entry_id, outcome, at):
                self.settle_tray(entry_id, "failed", change_set_id=None, outcome=outcome, at=at)
            self._execute(
                f"/*mdm:keyed*/ DELETE FROM {self.t('work', 'open_batch')} WHERE batch_id = ?", [batch_id]
            )
            if batch.compensates is not None and status in ("stopped", "failed"):
                self.clear_compensated_by(batch.compensates, batch_id)
        return True

    def end_batch(
        self, batch_id: str, *, outcome: str, at: datetime, from_statuses: Sequence[str] = BEFORE_TRAY
    ) -> bool:
        """Ends a batch before the tray (`BatchService._end`: a discard, a split of every alike review, or too
        few reviews left), in one transaction, the batch row first: `discarded` with `outcome` and
        `finished_at` while it is in one of `from_statuses` (statuses before the tray only; else nothing is
        written and False is returned); then its group's `open_batch` row deleted, so the group can draw again;
        and, for a compensation, its original's mark cleared (`clear_compensated_by`), so the original can be
        compensated again. Its reviews stay as they are: no lock held them, and their tasks stay in the inbox.
        Joins an open transaction, so a split of every review commits with the end."""
        if not set(from_statuses) <= set(BEFORE_TRAY):
            raise ValueError("a batch in the tray ends through finish_batch")
        with self.transaction():
            if not self.set_batch(
                batch_id,
                from_statuses=from_statuses,
                status="discarded",
                outcome=outcome,
                finished_at=at,
                updated_at=at,
            ):
                return False
            batch = self.batches([batch_id])[batch_id]
            if batch.signature_key is not None:
                self._execute(
                    f"/*mdm:keyed*/ DELETE FROM {self.t('work', 'open_batch')} WHERE signature_key = ? "
                    "AND batch_id = ?",
                    [batch.signature_key, batch_id],
                )
            if batch.compensates is not None:
                self.clear_compensated_by(batch.compensates, batch_id)
        return True

    @staticmethod
    def _statuses_one(status: str) -> str:
        """A batch status bound as a parameter, checked against the known statuses."""
        if status not in BATCH_STATUSES:
            raise ValueError("not a known status")
        return status

    def _hold_batch(self, entity: str, chunk: BatchChunkWrite | None) -> None:
        """A chunk's first step (`apply_work`): the batch row held while `staged` or `committing` with no stop
        asked, else `Conflict(batch_stopped)` for a stop or `Conflict(batch_changed)`; for a link, its
        signature's bulk rights held as the commit holds the automatic band, else `Conflict(bulk_withdrawn)`."""
        if chunk is None:
            return
        held = self._update_keyed(
            _T("work", "batch"),
            (),
            ("batch_id",),
            [(chunk.batch_id,)],
            extra_set="status = t.status",
            where="t.status IN ('staged', 'committing') AND t.stop_requested_at IS NULL",
        )
        key = [_key_token(chunk.batch_id)]
        if held != 1:
            found = self.batches([chunk.batch_id]).get(chunk.batch_id)
            if found is not None and found.stop_requested_at is not None:
                raise Conflict(key, code="batch_stopped")
            raise Conflict(key, code="batch_changed")
        if chunk.kind == "link" and chunk.bulk_band is not None:
            if not self.hold_band(entity, chunk.bulk_band, chunk.signature):
                raise Conflict(key, code="bulk_withdrawn")

    def _write_batch_chunk(self, chunk: BatchChunkWrite | None) -> None:
        """A chunk's own writes, after its published rows (`apply_work`): its `batch_chunk` row exactly once
        (else `Conflict(chunk_committed)`); its reviews from `planned` to `committed` with their chunk and
        change set (a short count is `Conflict(item_settled)`); for a compensation, the original's reviews
        moved to `compensated` and the original chunk marked; the batch's counts, throttle and attempts (set
        back to 0), `committing` after the first chunk and `committed` after the last, which finishes its
        entry and frees its group; last, the chunk's lock subjects released."""
        if chunk is None:
            return
        if chunk.change_set_id is None:
            raise ValueError("a chunk is written with its change set")
        now = self.clock()
        key = [_key_token(chunk.batch_id)]
        inserted = self._insert(
            _T("work", "batch_chunk"),
            [
                {
                    "batch_id": safe(chunk.batch_id),
                    "chunk_no": int(chunk.chunk_no),
                    "change_set_id": safe(chunk.change_set_id),
                    "commit_version": chunk.commit_version,
                    "items": len(set(chunk.task_ids)),
                    "rows": int(chunk.rows),
                    "committed_at": now,
                    "compensated_by": None,
                    "compensated_at": None,
                }
            ],
            on_conflict_nothing=("batch_id", "chunk_no"),
        )
        if inserted != 1:
            raise Conflict(key, code="chunk_committed")
        task_ids = sorted(set(chunk.task_ids))
        moved = self._update_keyed(
            _T("work", "batch_item"),
            ("status", "chunk_no", "change_set_id", "updated_at"),
            ("batch_id", "task_id"),
            [
                (chunk.batch_id, t, "committed", int(chunk.chunk_no), safe(chunk.change_set_id), now)
                for t in task_ids
            ],
            where="t.status = 'planned'",
        )
        if moved != len(task_ids):
            raise Conflict(key, code="item_settled")
        if chunk.compensates is not None:
            undone = self._update_keyed(
                _T("work", "batch_item"),
                ("status", "updated_at"),
                ("batch_id", "task_id"),
                [(chunk.compensates, t, "compensated", now) for t in task_ids],
                where="t.status = 'committed'",
            )
            if undone != len(task_ids):
                raise Conflict(key, code="item_settled")
            if chunk.undoes_chunk is not None:
                marked = self._execute(
                    f"/*mdm:keyed*/ UPDATE {self.t('work', 'batch_chunk')} SET compensated_by = ?, "
                    "compensated_at = ? WHERE batch_id = ? AND chunk_no = ? AND compensated_by IS NULL",
                    [safe(chunk.batch_id), now, chunk.compensates, int(chunk.undoes_chunk)],
                )
                if marked != 1:
                    raise Conflict(key, code="item_settled")
        batch = self.batches([chunk.batch_id])[chunk.batch_id]
        fields: dict[str, Any] = {
            "chunks_committed": batch.chunks_committed + 1,
            "rows_committed": batch.rows_committed + int(chunk.rows),
            "not_before": (
                now + timedelta(seconds=int(chunk.rows) * chunk.seconds_per_row)
                if chunk.seconds_per_row > 0 and not chunk.last
                else None
            ),
            "attempts": 0,
            "updated_at": now,
        }
        if chunk.first:
            fields["status"] = "committing"
        if chunk.last:
            fields.update(status="committed", outcome="committed", finished_at=now)
        if not self.set_batch(chunk.batch_id, from_statuses=("staged", "committing"), **fields):
            raise Conflict(key, code="batch_changed")
        if chunk.last:
            if not chunk.first:
                self.finish_tray_batch(chunk.entry_id, "committed", now)
            self._execute(
                f"/*mdm:keyed*/ DELETE FROM {self.t('work', 'open_batch')} WHERE batch_id = ?",
                [chunk.batch_id],
            )
        self.release_locks(chunk.subjects)

    def _write_batch_samples(self, samples: Sequence[BatchSampleWrite]) -> None:
        """Each forced-sample outcome, with its entry and the comparison a disagreeing decision named, written
        to a sample review still `open`; it never raises, since a sample split or voided meanwhile stays as it
        is. It touches no batch row, so it needs no place in the lock order."""
        if not samples:
            return
        now = self.clock()
        for sample in samples:
            if sample.status not in SAMPLE_OUTCOMES:
                raise ValueError("not a known status")
            self._update_keyed(
                _T("work", "batch_item"),
                ("status", "entry_id", "split_on", "updated_at"),
                ("batch_id", "task_id"),
                [
                    (
                        sample.batch_id,
                        sample.task_id,
                        sample.status,
                        safe(sample.entry_id),
                        safe(sample.split_on),
                        now,
                    )
                ],
                where="t.role = 'sample' AND t.status = 'open'",
            )

    # ------------------------------------------------------------------ work group: rule results, references, jobs

    def put_rule_failures(
        self,
        entity: str,
        source: SourceKey,
        failures: Sequence[RuleResult],
        event_id: str,
        code_list_versions: Mapping[str, int],
    ) -> None:
        """Replace the record's failures (failures only; the counts live in source_state)."""
        self.replace_rule_failures(entity, [(source, failures, event_id)], code_list_versions)

    def replace_rule_failures(
        self,
        entity: str,
        records: Sequence[tuple[SourceKey, Sequence[RuleResult], str]],
        code_list_versions: Mapping[str, int],
    ) -> None:
        """`put_rule_failures` for many records in two statements: (source, results, event_id) each.

        Only results that did not pass are kept. `code_list_versions` is looked up by rule ID, then
        by attribute; the version is recorded on every failure it names.
        """
        if not records:
            return
        table = _T("work", "rule_result")
        now = self.clock()
        rows: list[dict[str, Any]] = []
        for source, results, event_id in records:
            for r in results:
                if r.passed:
                    continue
                rows.append(
                    {
                        "entity": entity,
                        "source_system": source.system,
                        "source_key": source.key,
                        "rule_id": r.rule_id,
                        "dimension": r.dimension,
                        "code": r.code,
                        "severity": r.severity,
                        "code_list_version": code_list_versions.get(
                            r.rule_id, code_list_versions.get(r.attribute)
                        ),
                        "event_id": event_id,
                        "checked_at": now,
                    }
                )
        with self.transaction():
            self._delete_keyed(
                table,
                ("entity", "source_system", "source_key"),
                [(entity, s.system, s.key) for s, _, _ in records],
            )
            self._insert(
                table,
                _dedupe_last(rows, key=lambda r: (r["source_system"], r["source_key"], r["rule_id"])),
            )

    _RULE_COLUMNS = (
        "source_system",
        "source_key",
        "rule_id",
        "dimension",
        "code",
        "severity",
        "code_list_version",
        "event_id",
    )

    def rule_failures(
        self, entity: str, sources: Sequence[SourceKey]
    ) -> dict[SourceKey, list[dict[str, Any]]]:
        """source -> its failures (column -> value), by rule ID."""
        table = _T("work", "rule_result")
        rows = self._select_keyed(
            table,
            self._RULE_COLUMNS,
            ("entity", "source_system", "source_key"),
            [(entity, s.system, s.key) for s in sources],
            order_by=("source_system", "source_key", "rule_id"),
        )
        out: dict[SourceKey, list[dict[str, Any]]] = {}
        for r in rows:
            out.setdefault(SourceKey(r[0], r[1]), []).append(self._decode_row(table, self._RULE_COLUMNS, r))
        return out

    def put_pending_references(self, rows: Sequence[tuple]) -> None:
        """(entity, source, attribute, ref_entity, ref_source) upserted; first_seen_at kept on an update."""
        now = self.clock()
        self._upsert(
            _T("work", "pending_reference"),
            _dedupe_last(
                [
                    {
                        "entity": e,
                        "source_system": s.system,
                        "source_key": s.key,
                        "attribute": a,
                        "ref_entity": re_,
                        "ref_system": rs.system,
                        "ref_key": rs.key,
                        "first_seen_at": now,
                    }
                    for e, s, a, re_, rs in rows
                ],
                key=lambda r: (r["entity"], r["source_system"], r["source_key"], r["attribute"]),
            ),
            conflict=("entity", "source_system", "source_key", "attribute"),
            update=("ref_entity", "ref_system", "ref_key"),
        )

    _PENDING_COLUMNS = (
        "entity",
        "source_system",
        "source_key",
        "attribute",
        "ref_entity",
        "ref_system",
        "ref_key",
    )

    @staticmethod
    def _pending_of(row: Sequence[Any]) -> tuple:
        e, s, k, a, re_, rs, rk = row
        return (e, SourceKey(s, k), a, re_, SourceKey(rs, rk))

    def pending_references(self, entity: str, after: tuple | None, limit: int) -> list[tuple]:
        """(entity, source, attribute, ref_entity, ref_source) of one entity, keyset-paged by
        (source_system, source_key, attribute); `after` is that triple."""
        sql = f"/*mdm:paged*/ SELECT {', '.join(self._PENDING_COLUMNS)} FROM {self.t('work', 'pending_reference')} WHERE entity = ?"
        params: list[Any] = [entity]
        if after is not None:
            sql += " AND (source_system, source_key, attribute) > (?, ?, ?)"
            params.extend(list(after))
        rows = self._fetch_all(
            sql + " ORDER BY source_system, source_key, attribute LIMIT ?", [*params, int(limit)]
        )
        return [self._pending_of(r) for r in rows]

    def pending_references_to(self, ref_entity: str, ref_sources: Sequence[SourceKey]) -> list[tuple]:
        """Pending references naming any of `ref_sources` of `ref_entity` (a late target arrived)."""
        rows = self._select_keyed(
            _T("work", "pending_reference"),
            self._PENDING_COLUMNS,
            ("ref_entity", "ref_system", "ref_key"),
            [(ref_entity, s.system, s.key) for s in ref_sources],
            order_by=("entity", "source_system", "source_key", "attribute"),
        )
        return [self._pending_of(r) for r in rows]

    def drop_pending_references(self, keys: Sequence[tuple]) -> None:
        """Delete by (entity, source, attribute)."""
        self._delete_keyed(
            _T("work", "pending_reference"),
            ("entity", "source_system", "source_key", "attribute"),
            [(e, s.system, s.key, a) for e, s, a in keys],
        )

    def start_job(self, job: str, actor: str) -> str:
        """A job_run row; returns its run ID."""
        run_id = "RUN-" + secrets.token_hex(8)
        now = self.clock()
        self._insert(
            _T("work", "job_run"),
            [
                {
                    "run_id": run_id,
                    "job": job,
                    "status": "running",
                    "actor": actor,
                    "started_at": now,
                    "heartbeat_at": now,
                    "progress": {},
                }
            ],
        )
        return run_id

    def heartbeat(self, run_id: str, progress: dict) -> None:
        """Progress is a safe detail."""
        self._update_keyed(
            _T("work", "job_run"),
            ("heartbeat_at", "progress"),
            ("run_id",),
            [(run_id, self.clock(), progress)],
        )

    def finish_job(self, run_id: str, status: str, error_code: str | None = None) -> None:
        now = self.clock()
        self._update_keyed(
            _T("work", "job_run"),
            ("status", "error_code", "finished_at", "heartbeat_at"),
            ("run_id",),
            [(run_id, status, error_code, now, now)],
        )

    def jobs(self, limit: int, job: str | None = None) -> list[dict[str, Any]]:
        """The latest job runs, newest first."""
        table = _T("work", "job_run")
        columns = table.column_names()
        sql = f"/*mdm:paged*/ SELECT {', '.join(columns)} FROM {self._q(table)}"
        params: list[Any] = []
        if job is not None:
            sql += " WHERE job = ?"
            params.append(job)
        rows = self._fetch_all(sql + " ORDER BY started_at DESC, run_id LIMIT ?", [*params, int(limit)])
        return [self._decode_row(table, columns, r) for r in rows]

    # ------------------------------------------------------------------ hub group

    def put_source_versions(self, rows: Sequence[tuple[SourceChange, Mapping[str, Any]]]) -> int:
        """(change, payload with vault references); ON CONFLICT DO NOTHING; the number new."""
        now = self.clock()
        first: dict[str, dict[str, Any]] = {}
        for change, payload in rows:
            first.setdefault(
                change.event_id,
                {
                    "event_id": change.event_id,
                    "entity": change.entity,
                    "source_system": change.source_system,
                    "source_key": change.source_key,
                    "op": change.op,
                    "occurred_at": change.occurred_at,
                    "source_version": change.source_version,
                    "landing_seq": change.landing_seq,
                    "landed_at": change.landed_at,
                    "initial_load": change.initial_load,
                    "payload": payload,
                    "received_at": now,
                },
            )
        return self._insert(
            _T("hub", "source_version"), list(first.values()), on_conflict_nothing=("event_id",)
        )

    def seen_events(self, event_ids: Sequence[str]) -> set[str]:
        rows = self._select_keyed(
            _T("hub", "source_version"), ("event_id",), ("event_id",), [(e,) for e in event_ids], order_by=()
        )
        return {r[0] for r in rows}

    _VERSION_COLUMNS = (
        "event_id",
        "entity",
        "source_system",
        "source_key",
        "op",
        "occurred_at",
        "source_version",
        "landing_seq",
        "landed_at",
        "initial_load",
        "payload",
        "received_at",
    )

    def source_versions(self, entity: str, source: SourceKey, limit: int) -> list[dict[str, Any]]:
        """One record's kept versions (payload with vault references), by landing sequence, at most `limit`."""
        table = _T("hub", "source_version")
        rows = self._fetch_all(
            f"/*mdm:paged*/ SELECT {', '.join(self._VERSION_COLUMNS)} FROM {self._q(table)} "
            "WHERE entity = ? AND source_system = ? AND source_key = ? ORDER BY landing_seq LIMIT ?",
            [entity, source.system, source.key, int(limit)],
        )
        return [self._decode_row(table, self._VERSION_COLUMNS, r) for r in rows]

    def provenance(self, entity: str, master_ids: Sequence[str]) -> dict[str, dict]:
        rows = self._select_keyed(
            _T("hub", "provenance"),
            ("master_id", "doc"),
            ("entity", "master_id"),
            [(entity, m) for m in master_ids],
            order_by=("master_id",),
        )
        return {m: self._decode_json(doc) for m, doc in rows}

    def steward_values(self, entity: str, master_ids: Sequence[str]) -> dict[str, dict[str, StewardValue]]:
        table = _T("hub", "steward_value")
        columns = ("master_id", "attribute", "value", "pinned_until", "set_by", "set_at")
        rows = self._select_keyed(
            table,
            columns,
            ("entity", "master_id"),
            [(entity, m) for m in master_ids],
            order_by=("master_id", "attribute"),
        )
        out: dict[str, dict[str, StewardValue]] = {}
        for row in rows:
            d = self._decode_row(table, columns, row)
            out.setdefault(d["master_id"], {})[d["attribute"]] = StewardValue(
                d["value"], d["pinned_until"], d["set_by"], d["set_at"]
            )
        return out

    def merge_members(self, retired_id: str, merge_version: int, limit: int) -> list[SourceKey]:
        """The sources a merge moved, by source."""
        rows = self._fetch_all(
            f"/*mdm:paged*/ SELECT source_system, source_key FROM {self.t('hub', 'merge_member')} "
            "WHERE retired_id = ? AND merge_version = ? ORDER BY source_system, source_key LIMIT ?",
            [retired_id, int(merge_version), int(limit)],
        )
        return [SourceKey(s, k) for s, k in rows]

    # ------------------------------------------------------------------ core reads (anyone may read)

    def xrefs_for_sources(self, entity: str, sources: Sequence[SourceKey]) -> dict[SourceKey, str]:
        """Active cross-references only: source -> master ID."""
        rows = self._select_keyed(
            _T("core", "xref"),
            ("source_system", "source_key", "master_id"),
            ("entity", "source_system", "source_key"),
            [(entity, s.system, s.key) for s in sources],
            order_by=("source_system", "source_key"),
            where="t.status = 'active'",
        )
        return {SourceKey(s, k): m for s, k, m in rows}

    def xref_rows(self, entity: str, sources: Sequence[SourceKey]) -> dict[SourceKey, XrefRow]:
        """Cross-reference rows in any status: source -> row."""
        rows = self._select_keyed(
            _T("core", "xref"),
            ("source_system", "source_key", "master_id", "status", "_commit_version"),
            ("entity", "source_system", "source_key"),
            [(entity, s.system, s.key) for s in sources],
            order_by=("source_system", "source_key"),
        )
        return {SourceKey(s, k): XrefRow(entity, SourceKey(s, k), m, st, int(v)) for s, k, m, st, v in rows}

    def members(self, entity: str, master_ids: Sequence[str], limit_each: int) -> dict[str, list[SourceKey]]:
        """Active members of each golden record, by source, at most `limit_each` each."""
        table = _T("core", "xref")
        unique = self._unique_keys([(entity, m) for m in master_ids])
        sql = (
            "/*mdm:keyed*/ SELECT q.master_id, q.source_system, q.source_key FROM ("
            "SELECT t.master_id, t.source_system, t.source_key, ROW_NUMBER() OVER "
            "(PARTITION BY t.master_id ORDER BY t.source_system, t.source_key) AS rn "
            f"FROM {self._q(table)} AS t JOIN {self._row_source()} ON {self._key_join(table, ('entity', 'master_id'))} "
            "WHERE t.status = 'active') AS q WHERE q.rn <= ? ORDER BY q.master_id, q.source_system, q.source_key"
        )
        out: dict[str, list[SourceKey]] = {}
        for chunk in capacity.chunks(unique, capacity.KEY_CHUNK):
            for m, s, k in self._fetch_all(
                sql, [self._key_doc(table, ("entity", "master_id"), chunk), int(limit_each)]
            ):
                out.setdefault(m, []).append(SourceKey(s, k))
        return out

    def member_counts(self, entity: str, master_ids: Sequence[str]) -> dict[str, int]:
        """Active members of each golden record that has any."""
        table = _T("core", "xref")
        unique = self._unique_keys([(entity, m) for m in master_ids])
        sql = (
            f"/*mdm:keyed*/ SELECT t.master_id, COUNT(*) FROM {self._q(table)} AS t JOIN {self._row_source()} "
            f"ON {self._key_join(table, ('entity', 'master_id'))} WHERE t.status = 'active' GROUP BY t.master_id"
        )
        out: dict[str, int] = {}
        for chunk in capacity.chunks(unique, capacity.KEY_CHUNK):
            for m, n in self._fetch_all(sql, [self._key_doc(table, ("entity", "master_id"), chunk)]):
                out[m] = int(n)
        return out

    def xref_page(self, entity: str, after: SourceKey | None, limit: int) -> list[XrefRow]:
        """Active cross-references by source after `after`."""
        sql = (
            f"/*mdm:paged*/ SELECT source_system, source_key, master_id, status, _commit_version "
            f"FROM {self.t('core', 'xref')} WHERE entity = ? AND status = 'active'"
        )
        params: list[Any] = [entity]
        if after is not None:
            sql += " AND (source_system, source_key) > (?, ?)"
            params.extend([after.system, after.key])
        rows = self._fetch_all(sql + " ORDER BY source_system, source_key LIMIT ?", [*params, int(limit)])
        return [XrefRow(entity, SourceKey(s, k), m, st, int(v)) for s, k, m, st, v in rows]

    def _entity_table(self, entity: str, *, refresh: bool = False) -> Table:
        """The entity's published table as it is now (columns in ordinal order), cached."""
        if not refresh and entity in self._entity_tables:
            return self._entity_tables[entity]
        found = self._columns_of(("core",)).get((schema_name(self.prefix, "core"), entity))
        if not found or entity in ddl.PASSTHROUGH_VIEWS or entity in ("change", "commit_log"):
            raise NotFound("unknown_entity", entity=entity)
        table = Table(
            "core",
            entity,
            tuple(Column(n, logical or "text") for n, logical in found),
            ("master_id",),
        )
        self._entity_tables[entity] = table
        return table

    def entity_columns(self, entity: str) -> list[str]:
        """The entity table's columns in ordinal order (fixed columns included)."""
        return list(self._entity_table(entity).column_names())

    def _golden_of(self, entity: str, table: Table, row: Sequence[Any]) -> GoldenRow:
        d = self._decode_row(table, table.column_names(), row)
        return GoldenRow(
            entity=entity,
            master_id=d["master_id"],
            status=d["status"],
            survivor_id=d["survivor_id"],
            values={k: v for k, v in d.items() if k not in ddl.ENTITY_FIXED_COLUMNS},
            commit_version=int(d["_commit_version"]),
            row_version=int(d["_row_version"]),
            initial_load=d["_initial_load"],
        )

    def golden(self, entity: str, master_ids: Sequence[str]) -> dict[str, GoldenRow]:
        table = self._entity_table(entity)
        rows = self._select_keyed(
            table, table.column_names(), ("master_id",), [(m,) for m in master_ids], order_by=("master_id",)
        )
        return {r[0]: self._golden_of(entity, table, r) for r in rows}

    def golden_page(
        self, entity: str, after: str | None, limit: int, *, status: str | None = None
    ) -> list[GoldenRow]:
        """Golden rows by master ID after `after`, optionally of one status."""
        table = self._entity_table(entity)
        sql = f"/*mdm:paged*/ SELECT {', '.join(table.column_names())} FROM {self._q(table)} WHERE 1 = 1"
        params: list[Any] = []
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        if after is not None:
            sql += " AND master_id > ?"
            params.append(after)
        rows = self._fetch_all(sql + " ORDER BY master_id LIMIT ?", [*params, int(limit)])
        return [self._golden_of(entity, table, r) for r in rows]

    def masked_rows(self, entity: str, master_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        """The `<p>_read.<entity>` view's rows of `master_ids`: what a person reads without a reveal."""
        base = self._entity_table(entity)
        view = Table("read", entity, base.columns, ("master_id",))
        names = self.table_columns("read", entity)
        rows = self._select_keyed(
            view, names, ("master_id",), [(m,) for m in master_ids], order_by=("master_id",)
        )
        return {r[0]: self._decode_row(view, names, r) for r in rows}

    def resolve_retired(self, ids: Sequence[str]) -> dict[str, str]:
        """Active retired ID -> its collapsed survivor."""
        rows = self._select_keyed(
            _T("core", "retired_id"),
            ("retired_id", "survivor_id"),
            ("retired_id",),
            [(i,) for i in ids],
            order_by=("retired_id",),
            where="t.active = true",
        )
        return dict(rows)

    _RETIRED_COLUMNS = (
        "retired_id",
        "entity",
        "merged_into",
        "merge_version",
        "survivor_id",
        "active",
        "_commit_version",
    )

    @staticmethod
    def _retired_of(row: Sequence[Any]) -> RetiredRow:
        r, e, into, mv, surv, active, cv = row
        return RetiredRow(r, e, into, int(mv), surv, bool(active), int(cv))

    def retired_rows(self, ids: Sequence[str]) -> dict[str, RetiredRow]:
        rows = self._select_keyed(
            _T("core", "retired_id"),
            self._RETIRED_COLUMNS,
            ("retired_id",),
            [(i,) for i in ids],
            order_by=("retired_id",),
        )
        return {r[0]: self._retired_of(r) for r in rows}

    def retired_through(self, survivor_id: str, limit: int) -> list[RetiredRow]:
        """Active rows whose collapsed survivor is `survivor_id`, by retired ID."""
        rows = self._fetch_all(
            f"/*mdm:paged*/ SELECT {', '.join(self._RETIRED_COLUMNS)} FROM {self.t('core', 'retired_id')} "
            "WHERE survivor_id = ? AND active = true ORDER BY retired_id LIMIT ?",
            [survivor_id, int(limit)],
        )
        return [self._retired_of(r) for r in rows]

    def merged_into(self, master_id: str, limit: int) -> list[RetiredRow]:
        """Active rows whose direct edge points at `master_id` (`merged_into`), by retired ID."""
        rows = self._fetch_all(
            f"/*mdm:paged*/ SELECT {', '.join(self._RETIRED_COLUMNS)} FROM {self.t('core', 'retired_id')} "
            "WHERE merged_into = ? AND active = true ORDER BY retired_id LIMIT ?",
            [master_id, int(limit)],
        )
        return [self._retired_of(r) for r in rows]

    _REL_COLUMNS = (
        "rel_id",
        "rel_type",
        "from_entity",
        "from_master_id",
        "to_entity",
        "to_master_id",
        "valid_from",
        "valid_to",
        "status",
        "attributes",
        "origin_system",
        "origin_key",
        "origin_attribute",
        "_commit_version",
        "_row_version",
    )

    def _relationship_of(self, row: Sequence[Any]) -> RelationshipRow:
        d = self._decode_row(_T("core", "relationship"), self._REL_COLUMNS, row)
        return RelationshipRow(
            rel_id=d["rel_id"],
            rel_type=d["rel_type"],
            from_entity=d["from_entity"],
            from_master_id=d["from_master_id"],
            to_entity=d["to_entity"],
            to_master_id=d["to_master_id"],
            valid_from=d["valid_from"],
            valid_to=d["valid_to"],
            status=d["status"],
            attributes=d["attributes"],
            origin=_source(d["origin_system"], d["origin_key"]),
            origin_attribute=d["origin_attribute"],
            commit_version=int(d["_commit_version"]),
            row_version=int(d["_row_version"]),
        )

    def relationships(self, rel_ids: Sequence[str]) -> dict[str, RelationshipRow]:
        """Relationship rows by ID."""
        rows = self._select_keyed(
            _T("core", "relationship"),
            self._REL_COLUMNS,
            ("rel_id",),
            [(r,) for r in rel_ids],
            order_by=("rel_id",),
        )
        return {r[0]: self._relationship_of(r) for r in rows}

    def relationships_of(self, master_ids: Sequence[str], limit: int) -> list[RelationshipRow]:
        """Relationships with either end in `master_ids`, by rel_id, at most `limit`."""
        table = _T("core", "relationship")
        unique = self._unique_keys([(m,) for m in master_ids])
        found: dict[str, tuple] = {}
        for end in ("from_master_id", "to_master_id"):
            sql = (
                f"/*mdm:keyed*/ SELECT {', '.join('t.' + c for c in self._REL_COLUMNS)} FROM {self._q(table)} AS t "
                f"JOIN {self._row_source()} ON {self._key_join(table, (end,))} ORDER BY t.rel_id LIMIT ?"
            )
            for chunk in capacity.chunks(unique, capacity.KEY_CHUNK):
                for row in self._fetch_all(sql, [self._key_doc(table, (end,), chunk), int(limit)]):
                    found[row[0]] = row
        return [self._relationship_of(found[k]) for k in sorted(found)[:limit]]

    def relationships_by_origin(self, origins: Sequence[tuple[SourceKey, str]]) -> list[RelationshipRow]:
        """Active relationships asserted by (origin source, attribute), by rel_id."""
        wanted = {(s.system, s.key, a) for s, a in origins}
        rows = self._select_keyed(
            _T("core", "relationship"),
            self._REL_COLUMNS,
            ("origin_system", "origin_key"),
            [(s, k) for s, k, _ in wanted],
            order_by=("rel_id",),
            where="t.status = 'active'",
        )
        out = [self._relationship_of(r) for r in rows]
        return sorted(
            (r for r in out if r.origin and (r.origin.system, r.origin.key, r.origin_attribute) in wanted),
            key=lambda r: r.rel_id,
        )

    def last_commit_version(self) -> int:
        """COALESCE(MAX(commit_version), 0) of commit_log."""
        rows = self._fetch_all(
            f"/*mdm:aggregate*/ SELECT COALESCE(MAX(commit_version), 0) FROM {self.t('core', 'commit_log')}"
        )
        return int(rows[0][0])

    _COMMIT_COLUMNS = (
        "commit_version",
        "committed_at",
        "change_set_id",
        "actor_kind",
        "actor_role",
        "authority_kind",
        "authority_ref",
        "initial_load",
        "counts",
        "row_count",
        "change_count",
    )

    def commits_by_version(self, versions: Sequence[int]) -> list[CommitLogRow]:
        table = _T("core", "commit_log")
        rows = self._select_keyed(
            table,
            self._COMMIT_COLUMNS,
            ("commit_version",),
            [(int(v),) for v in versions],
            order_by=("commit_version",),
        )
        out = []
        for row in rows:
            d = self._decode_row(table, self._COMMIT_COLUMNS, row)
            d["counts"] = {k: int(v) for k, v in (d["counts"] or {}).items()}
            d["row_count"] = int(d["row_count"])
            d["change_count"] = int(d["change_count"])
            out.append(CommitLogRow(**d))
        return out

    def changes_page(
        self, since: int, cursor: tuple[int, int] | None, hi: int, entity: str | None, limit: int
    ) -> list[ChangeRow]:
        """Change rows with since < commit_version <= hi (or after the cursor), by (commit_version, change_seq)."""
        sql = (
            "/*mdm:paged*/ SELECT commit_version, change_seq, entity, master_id, change_kind, survivor_id, parts "
            f"FROM {self.t('core', 'change')} WHERE commit_version <= ?"
        )
        params: list[Any] = [int(hi)]
        if cursor is None:
            sql += " AND commit_version > ?"
            params.append(int(since))
        else:
            sql += " AND (commit_version > ? OR (commit_version = ? AND change_seq > ?))"
            params.extend([int(cursor[0]), int(cursor[0]), int(cursor[1])])
        if entity is not None:
            sql += " AND entity = ?"
            params.append(entity)
        rows = self._fetch_all(sql + " ORDER BY commit_version, change_seq LIMIT ?", [*params, int(limit)])
        return [
            ChangeRow(int(cv), int(seq), e, m, kind, surv, tuple(self._decode_json(parts) or ()))
            for cv, seq, e, m, kind, surv, parts in rows
        ]

    def changes_of_record(
        self, entity: str, master_id: str, before: tuple[int, int] | None, limit: int
    ) -> list[ChangeRow]:
        """One golden record's change rows, newest first, before the cursor (commit version, change sequence)."""
        sql = (
            "/*mdm:paged*/ SELECT commit_version, change_seq, entity, master_id, change_kind, survivor_id, parts "
            f"FROM {self.t('core', 'change')} WHERE master_id = ? AND entity = ?"
        )
        params: list[Any] = [master_id, entity]
        if before is not None:
            sql += " AND (commit_version < ? OR (commit_version = ? AND change_seq < ?))"
            params.extend([int(before[0]), int(before[0]), int(before[1])])
        rows = self._fetch_all(
            sql + " ORDER BY commit_version DESC, change_seq DESC LIMIT ?", [*params, int(limit)]
        )
        return [
            ChangeRow(int(cv), int(seq), e, m, kind, surv, tuple(self._decode_json(parts) or ()))
            for cv, seq, e, m, kind, surv, parts in rows
        ]

    def change_log_rows(self, keys: Sequence[tuple[int, str]]) -> dict[tuple[int, str], list[dict[str, Any]]]:
        """(commit version, row key) -> the audit rows written for that row by that commit, by change ID."""
        table = _T("audit", "change_log")
        rows = self._select_keyed(
            table,
            self._CHANGE_LOG_COLUMNS,
            ("commit_version", "row_key"),
            [(int(v), k) for v, k in keys],
            order_by=("change_id",),
        )
        out: dict[tuple[int, str], list[dict[str, Any]]] = {}
        for row in rows:
            d = self._decode_row(table, self._CHANGE_LOG_COLUMNS, row)
            out.setdefault((int(d["commit_version"]), d["row_key"]), []).append(d)
        return out

    def decisions_of_record(
        self,
        master_id: str,
        *,
        since: datetime | None,
        until: datetime | None,
        limit: int,
    ) -> list[tuple[str, datetime]]:
        """(change set ID, when) of the steward decisions about `master_id` that published nothing (the
        change log's `decision` rows, keyed by the record), at or after `since` and before `until`, newest
        first, at most `limit`: a walk of the row_key index."""
        sql = (
            f"/*mdm:paged*/ SELECT change_set_id, changed_at FROM {self.t('audit', 'change_log')} "
            "WHERE row_key = ? AND table_name = 'decision' AND commit_version IS NULL"
        )
        params: list[Any] = [master_id]
        if since is not None:
            sql += " AND changed_at >= ?"
            params.append(since)
        if until is not None:
            sql += " AND changed_at < ?"
            params.append(until)
        rows = self._fetch_all(sql + " ORDER BY changed_at DESC, change_id LIMIT ?", [*params, int(limit)])
        return [(str(cs), _utc(at)) for cs, at in rows]

    def change_set_evidence(self, change_set_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        """Change set ID -> its evidence document (codes and IDs only)."""
        rows = self._select_keyed(
            _T("audit", "change_set"),
            ("change_set_id", "evidence"),
            ("change_set_id",),
            [(i,) for i in change_set_ids],
            order_by=("change_set_id",),
        )
        return {cs: self._decode_json(evidence) or {} for cs, evidence in rows}

    def current_rows(self, entity: str, master_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        """The published rows of `master_ids` as column -> value, for the feed read."""
        table = self._entity_table(entity)
        names = table.column_names()
        rows = self._select_keyed(
            table, names, ("master_id",), [(m,) for m in master_ids], order_by=("master_id",)
        )
        return {r[0]: self._decode_row(table, names, r) for r in rows}

    # ------------------------------------------------------------------ writes inside commit_scope() only

    @staticmethod
    def _require_commit_scope() -> None:
        if "commit" not in guard.active_scopes():
            raise GuardError("outside_commit_scope")

    def next_commit_version(self) -> int:
        """SELECT COALESCE(MAX(commit_version), 0) + 1, under the commit lock (`GuardError` outside it)."""
        self._require_commit_scope()
        rows = self._fetch_all(
            f"/*mdm:aggregate*/ SELECT COALESCE(MAX(commit_version), 0) + 1 FROM {self.t('core', 'commit_log')}"
        )
        return int(rows[0][0])

    def allocate_ids(self, name: str, code: str, n: int) -> list[str]:
        """n master IDs `f"{code}-{value:06d}"` from the counter row, updated (inside the commit scope)."""
        self._require_commit_scope()
        if n <= 0:
            return []
        rows = self._fetch_all(
            f"/*mdm:small*/ UPDATE {self.t('hub', 'id_counter')} SET last_value = last_value + ? WHERE name = ? "
            "RETURNING last_value",
            [int(n), name],
        )
        if rows:
            last = int(rows[0][0])
        else:
            self._insert(_T("hub", "id_counter"), [{"name": name, "code": code, "last_value": n}])
            last = n
        return [f"{code}-{value:06d}" for value in range(last - n + 1, last + 1)]

    def write_golden(self, entity: str, rows: Sequence[tuple[GoldenRow | None, GoldenRow]]) -> int:
        """(before, after): insert new rows; update changed columns only; checks _row_version.

        `after.values` is the whole row: an attribute it leaves out is NULL. `after.commit_version`,
        `.row_version` and `.initial_load` are written as given; an update matches only while the
        stored `_row_version` is `before.row_version`, else `Conflict` names the stale master IDs.
        A value for a column the table does not have is `MdmError("unknown_column")`.
        """
        if not rows:
            return 0
        table = self._entity_table(entity)
        attributes = [c for c in table.columns if c.name not in ddl.ENTITY_FIXED_COLUMNS]
        names = {c.name for c in attributes}
        unknown = {k for _, after in rows for k in after.values if k not in names}
        if unknown:
            table = self._entity_table(entity, refresh=True)
            attributes = [c for c in table.columns if c.name not in ddl.ENTITY_FIXED_COLUMNS]
            names = {c.name for c in attributes}
            unknown = {k for _, after in rows for k in after.values if k not in names}
            if unknown:
                raise MdmError("unknown_column", table=entity, columns=tuple(sorted(unknown)))
        now = self.clock()
        inserts = [
            {
                "master_id": a.master_id,
                "status": a.status,
                "survivor_id": a.survivor_id,
                **{c.name: a.values.get(c.name) for c in attributes},
                "_commit_version": a.commit_version,
                "_row_version": a.row_version,
                "_initial_load": a.initial_load,
                "_created_at": now,
                "_updated_at": now,
            }
            for b, a in rows
            if b is None
        ]
        written = self._insert(table, inserts)
        groups: dict[tuple[str, ...], list[tuple[GoldenRow, GoldenRow]]] = {}
        for before, after in rows:
            if before is None:
                continue
            changed = [
                c.name
                for c in attributes
                if encode(before.values.get(c.name), c.type) != encode(after.values.get(c.name), c.type)
            ]
            if before.status != after.status:
                changed.append("status")
            if before.survivor_id != after.survivor_id:
                changed.append("survivor_id")
            groups.setdefault(tuple(changed), []).append((before, after))
        meta = ("_commit_version", "_row_version", "_initial_load", "_updated_at")
        for changed, pairs in groups.items():
            updated = self._update_golden(table, (*changed, *meta), pairs, now)
            if updated != len(pairs):
                # a row this statement updated carries this commit's version: under the commit lock no
                # other commit can have written that version, so every other row was stale
                current = self.golden(entity, [a.master_id for _, a in pairs])
                stale = sorted(
                    a.master_id
                    for _, a in pairs
                    if a.master_id not in current
                    or (current[a.master_id].commit_version, current[a.master_id].row_version)
                    != (a.commit_version, a.row_version)
                )
                raise Conflict(stale or [a.master_id for _, a in pairs], code="stale_row")
            written += updated
        return written

    def _update_golden(
        self,
        table: Table,
        set_columns: Sequence[str],
        pairs: Sequence[tuple[GoldenRow, GoldenRow]],
        now: datetime,
    ) -> int:
        """UPDATE … SET <changed>, meta FROM <rows> WHERE master_id matches AND _row_version = before's."""
        sets = [f"{c} = {self._cast(2 + i, table.column(c).type)}" for i, c in enumerate(set_columns)]
        sql = (
            f"/*mdm:keyed*/ UPDATE {self._q(table)} AS t SET {', '.join(sets)} FROM {self._row_source()} "
            f"WHERE t.master_id = {self._cast(0, 'text')} AND t._row_version = {self._cast(1, 'bigint')}"
        )
        types = ["text", "bigint", *[table.column(c).type for c in set_columns]]
        updated = 0
        for chunk in capacity.chunks(list(pairs), capacity.WRITE_CHUNK_ROWS):
            doc_rows = []
            for before, after in chunk:
                values = {
                    "status": after.status,
                    "survivor_id": after.survivor_id,
                    "_commit_version": after.commit_version,
                    "_row_version": after.row_version,
                    "_initial_load": after.initial_load,
                    "_updated_at": now,
                }
                row = [after.master_id, before.row_version]
                row.extend(values[c] if c in values else after.values.get(c) for c in set_columns)
                doc_rows.append([encode(v, t) for v, t in zip(row, types, strict=True)])
            updated += self._execute(sql, [_doc(doc_rows)])
        return updated

    def write_xrefs(self, rows: Sequence[XrefRow]) -> int:
        """Upsert by (entity, source): link -> active, detach -> detached; linked_at is now."""
        now = self.clock()
        return self._upsert(
            _T("core", "xref"),
            _dedupe_last(
                [
                    {
                        "entity": r.entity,
                        "source_system": r.source.system,
                        "source_key": r.source.key,
                        "master_id": r.master_id,
                        "status": r.status,
                        "linked_at": now,
                        "_commit_version": r.commit_version,
                    }
                    for r in rows
                ],
                key=lambda r: (r["entity"], r["source_system"], r["source_key"]),
            ),
            conflict=("entity", "source_system", "source_key"),
            update=("master_id", "status", "linked_at", "_commit_version"),
        )

    def write_retired(self, rows: Sequence[RetiredRow]) -> int:
        """Upsert by retired_id: a re-merge replaces the row."""
        now = self.clock()
        return self._upsert(
            _T("core", "retired_id"),
            _dedupe_last(
                [
                    {
                        "retired_id": r.retired_id,
                        "entity": r.entity,
                        "merged_into": r.merged_into,
                        "merge_version": r.merge_version,
                        "survivor_id": r.survivor_id,
                        "active": r.active,
                        "retired_at": now,
                        "_commit_version": r.commit_version,
                    }
                    for r in rows
                ],
                key=lambda r: r["retired_id"],
            ),
            conflict=("retired_id",),
            update=(
                "entity",
                "merged_into",
                "merge_version",
                "survivor_id",
                "active",
                "retired_at",
                "_commit_version",
            ),
        )

    def write_merge_members(
        self, entity: str, retired_id: str, merge_version: int, sources: Sequence[SourceKey]
    ) -> None:
        self._insert(
            _T("hub", "merge_member"),
            [
                {
                    "entity": entity,
                    "retired_id": retired_id,
                    "merge_version": merge_version,
                    "source_system": s.system,
                    "source_key": s.key,
                }
                for s in dict.fromkeys(sources)
            ],
            on_conflict_nothing=("retired_id", "merge_version", "source_system", "source_key"),
        )

    def write_relationships(self, rows: Sequence[RelationshipRow]) -> int:
        """Upsert by rel_id, every column as given."""
        return self._upsert(
            _T("core", "relationship"),
            _dedupe_last(
                [
                    {
                        "rel_id": r.rel_id,
                        "rel_type": r.rel_type,
                        "from_entity": r.from_entity,
                        "from_master_id": r.from_master_id,
                        "to_entity": r.to_entity,
                        "to_master_id": r.to_master_id,
                        "valid_from": r.valid_from,
                        "valid_to": r.valid_to,
                        "status": r.status,
                        "attributes": dict(r.attributes),
                        "origin_system": r.origin.system if r.origin else None,
                        "origin_key": r.origin.key if r.origin else None,
                        "origin_attribute": r.origin_attribute,
                        "_commit_version": r.commit_version,
                        "_row_version": r.row_version,
                    }
                    for r in rows
                ],
                key=lambda r: r["rel_id"],
            ),
            conflict=("rel_id",),
            update=[c for c in self._REL_COLUMNS if c != "rel_id"],
        )

    def repoint_relationships(
        self, from_id: str, to_id: str, version: int, *, origins: Sequence[SourceKey] | None = None
    ) -> list[str]:
        """Both ends equal to `from_id` become `to_id` (only rows whose origin is in `origins`, when given).

        Active relationships only; each repointed row gets `_commit_version = version` and its
        `_row_version` + 1. Returns the master IDs at the other ends (sorted, without `from_id` and
        `to_id`), which get a change row with part "relationship".
        """
        table = _T("core", "relationship")
        rows = self._fetch_all(
            f"/*mdm:keyed*/ SELECT rel_id, from_master_id, to_master_id, origin_system, origin_key FROM {self._q(table)} "
            "WHERE status = 'active' AND (from_master_id = ? OR to_master_id = ?) ORDER BY rel_id",
            [from_id, from_id],
        )
        allowed = None if origins is None else {(s.system, s.key) for s in origins}
        updates = []
        others: set[str] = set()
        for rel_id, f, t, os_, ok in rows:
            if allowed is not None and (os_, ok) not in allowed:
                continue
            updates.append((rel_id, to_id if f == from_id else f, to_id if t == from_id else t, int(version)))
            others.update(x for x in (f, t) if x not in (from_id, to_id))
        self._update_keyed(
            table,
            ("from_master_id", "to_master_id", "_commit_version"),
            ("rel_id",),
            updates,
            extra_set="_row_version = t._row_version + 1",
        )
        return sorted(others)

    def write_provenance(
        self, entity: str, docs: Mapping[str, dict], rule_version: int, version: int
    ) -> None:
        self._upsert(
            _T("hub", "provenance"),
            [
                {
                    "entity": entity,
                    "master_id": m,
                    "doc": doc,
                    "rule_version": rule_version,
                    "commit_version": version,
                }
                for m, doc in docs.items()
            ],
            conflict=("entity", "master_id"),
            update=("doc", "rule_version", "commit_version"),
        )

    def write_steward_values(
        self, entity: str, values: Mapping[str, Mapping[str, StewardValue]], version: int
    ) -> None:
        """Steward values by master ID and attribute (initiative 3 writes them; the table exists now)."""
        self._upsert(
            _T("hub", "steward_value"),
            [
                {
                    "entity": entity,
                    "master_id": m,
                    "attribute": a,
                    "value": sv.value,
                    "pinned_until": sv.pinned_until,
                    "set_by": sv.set_by,
                    "set_at": sv.set_at,
                    "commit_version": version,
                }
                for m, by_attribute in values.items()
                for a, sv in by_attribute.items()
            ],
            conflict=("entity", "master_id", "attribute"),
            update=("value", "pinned_until", "set_by", "set_at", "commit_version"),
        )

    def write_changes(self, rows: Sequence[ChangeRow]) -> None:
        self._insert(
            _T("core", "change"),
            [
                {
                    "commit_version": r.commit_version,
                    "change_seq": r.change_seq,
                    "entity": r.entity,
                    "master_id": r.master_id,
                    "change_kind": r.change_kind,
                    "survivor_id": r.survivor_id,
                    "parts": list(r.parts),
                }
                for r in rows
            ],
        )

    def write_commit_log(self, row: CommitLogRow) -> None:
        self._insert(
            _T("core", "commit_log"),
            [
                {
                    "commit_version": row.commit_version,
                    "committed_at": row.committed_at,
                    "change_set_id": row.change_set_id,
                    "actor_kind": row.actor_kind,
                    "actor_role": row.actor_role,
                    "authority_kind": row.authority_kind,
                    "authority_ref": row.authority_ref,
                    "initial_load": row.initial_load,
                    "counts": dict(row.counts),
                    "row_count": row.row_count,
                    "change_count": row.change_count,
                }
            ],
        )

    # ------------------------------------------------------------------ audit and vault (insert only; any transaction)

    def append_change_set(self, cs: ChangeSet, commit_version: int | None, item_count: int) -> None:
        self._insert(
            _T("audit", "change_set"),
            [
                {
                    "change_set_id": cs.change_set_id,
                    "fingerprint": cs.fingerprint,
                    "planning_version": cs.planning_version,
                    "entity": cs.entity,
                    "action": cs.action,
                    "commit_version": commit_version,
                    "actor": cs.actor.name,
                    "actor_kind": cs.actor.kind,
                    "actor_role": cs.actor.role,
                    "persona": cs.actor.persona,
                    "checker": cs.checker.name if cs.checker else None,
                    "authority_kind": cs.authority.kind,
                    "authority_ref": cs.authority.ref,
                    "reason": cs.reason,
                    "evidence": safe(dict(cs.evidence)),
                    "item_count": item_count,
                    "created_at": self.clock(),
                }
            ],
        )

    _CHANGE_SET_COLUMNS = (
        "change_set_id",
        "fingerprint",
        "planning_version",
        "entity",
        "action",
        "commit_version",
        "actor",
        "actor_kind",
        "actor_role",
        "persona",
        "checker",
        "authority_kind",
        "authority_ref",
        "reason",
        "evidence",
        "item_count",
        "created_at",
    )

    def change_sets(self, ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        """Audit rows of change sets by ID, as column -> value."""
        table = _T("audit", "change_set")
        rows = self._select_keyed(
            table,
            self._CHANGE_SET_COLUMNS,
            ("change_set_id",),
            [(i,) for i in ids],
            order_by=("change_set_id",),
        )
        return {r[0]: self._decode_row(table, self._CHANGE_SET_COLUMNS, r) for r in rows}

    def append_change_log(self, rows: Sequence[tuple]) -> None:
        """(change_id, version, cs_id, table, row_key, op, clause, before, after)."""
        now = self.clock()
        self._insert(
            _T("audit", "change_log"),
            [
                {
                    "change_id": cid,
                    "commit_version": v,
                    "change_set_id": cs,
                    "table_name": tbl,
                    "row_key": key,
                    "op": op,
                    "clause": clause,
                    "before": before,
                    "after": after,
                    "changed_at": now,
                }
                for cid, v, cs, tbl, key, op, clause, before, after in rows
            ],
        )

    _CHANGE_LOG_COLUMNS = (
        "change_id",
        "commit_version",
        "change_set_id",
        "table_name",
        "row_key",
        "op",
        "clause",
        "before",
        "after",
        "changed_at",
    )

    def change_log(self, commit_version: int, after: str | None, limit: int) -> list[dict[str, Any]]:
        """The audit rows of one commit version by change ID, as column -> value."""
        table = _T("audit", "change_log")
        sql = f"/*mdm:paged*/ SELECT {', '.join(self._CHANGE_LOG_COLUMNS)} FROM {self._q(table)} WHERE commit_version = ?"
        params: list[Any] = [int(commit_version)]
        if after is not None:
            sql += " AND change_id > ?"
            params.append(after)
        rows = self._fetch_all(sql + " ORDER BY change_id LIMIT ?", [*params, int(limit)])
        return [self._decode_row(table, self._CHANGE_LOG_COLUMNS, r) for r in rows]

    def append_access(
        self,
        actor: Actor,
        action: str,
        entity: str | None,
        master_id: str | None,
        attribute: str | None,
        reason: str,
        detail: dict,
    ) -> str:
        """One access_log row; returns its ID."""
        access_id = "AC-" + secrets.token_hex(10)
        self._insert(
            _T("audit", "access_log"),
            [
                {
                    "access_id": access_id,
                    "actor": actor.name,
                    "actor_role": actor.role,
                    "action": action,
                    "entity": entity,
                    "master_id": master_id,
                    "attribute": attribute,
                    "reason": reason,
                    "detail": safe(dict(detail)),
                    "accessed_at": self.clock(),
                }
            ],
        )
        return access_id

    def append_accesses(self, rows: Sequence[AccessRow]) -> int:
        """Access-log rows written through `_insert`, so many rows bind one JSON row document per
        `WRITE_CHUNK_ROWS` chunk; each action, reason and detail through `safe`. Joins an open transaction;
        returns the rows written. `append_access` stays for one row."""
        if not rows:
            return 0
        now = self.clock()
        return self._insert(
            _T("audit", "access_log"),
            [
                {
                    "access_id": "AC-" + secrets.token_hex(10),
                    "actor": row.actor,
                    "actor_role": safe(row.actor_role),
                    "action": safe(row.action),
                    "entity": safe(row.entity),
                    "master_id": safe(row.master_id),
                    "attribute": safe(row.attribute),
                    "reason": safe(row.reason),
                    "detail": safe(dict(row.detail)),
                    "accessed_at": now,
                }
                for row in rows
            ],
        )

    def access_log(self, after: str | None, limit: int) -> list[dict[str, Any]]:
        """Access rows by access ID after `after`, as column -> value."""
        table = _T("audit", "access_log")
        columns = table.column_names()
        sql = f"/*mdm:paged*/ SELECT {', '.join(columns)} FROM {self._q(table)}"
        params: list[Any] = []
        if after is not None:
            sql += " WHERE access_id > ?"
            params.append(after)
        rows = self._fetch_all(sql + " ORDER BY access_id LIMIT ?", [*params, int(limit)])
        return [self._decode_row(table, columns, r) for r in rows]

    def append_redaction(
        self,
        subject_keys: Sequence[str],
        value_ids: Sequence[str],
        actor: Actor,
        checker: Actor,
        authority_ref: str,
        reason: str,
        *,
        redaction_id: str | None = None,
    ) -> str:
        """One redaction_log row; returns its ID. Pass the ID given to `vault_redact`, so the emptied values
        name the redaction that emptied them; one is made when none is given."""
        redaction_id = redaction_id or new_redaction_id()
        self._insert(
            _T("audit", "redaction_log"),
            [
                {
                    "redaction_id": redaction_id,
                    "subject_keys": list(subject_keys),
                    "value_ids": list(value_ids),
                    "actor": actor.name,
                    "checker": checker.name,
                    "authority_ref": authority_ref,
                    "reason": reason,
                    "redacted_at": self.clock(),
                }
            ],
        )
        return redaction_id

    def vault_put(self, rows: Sequence[tuple[str, str, str, Any]]) -> list[str]:
        """(entity, subject_key, attribute, value) -> value IDs "pv_" + token_hex(12), reusing an equal live value.

        One ID per row, in order. A value that is not text is stored as its ISO text (date, datetime)
        or its canonical JSON (anything else but None).
        """
        if not rows:
            return []
        table = _T("vault", "personal_value")
        texts = [(e, s, a, self._vault_text(v)) for e, s, a, v in rows]
        existing = self._select_keyed(
            table,
            ("entity", "subject_key", "attribute", "value", "value_id"),
            ("subject_key", "attribute"),
            [(s, a) for _, s, a, _ in texts],
            order_by=("subject_key", "attribute", "created_at", "value_id"),
            where="t.redacted_at IS NULL",
        )
        live: dict[tuple[str, str, str, str | None], str] = {}
        for e, s, a, v, vid in existing:
            live.setdefault((e, s, a, v), vid)
        now = self.clock()
        ids: list[str] = []
        new: list[dict[str, Any]] = []
        for key in texts:
            vid = live.get(key)
            if vid is None:
                vid = "pv_" + secrets.token_hex(12)
                live[key] = vid
                e, s, a, v = key
                new.append(
                    {
                        "value_id": vid,
                        "entity": e,
                        "subject_key": s,
                        "attribute": a,
                        "value": v,
                        "created_at": now,
                        "redacted_at": None,
                        "redaction_id": None,
                    }
                )
            ids.append(vid)
        self._insert(table, new)
        return ids

    @staticmethod
    def _vault_text(value: Any) -> str | None:
        if value is None or isinstance(value, str):
            return value
        if isinstance(value, (date, datetime)):
            return iso(value)
        return canonical_json(value)

    def vault_get(self, value_ids: Sequence[str]) -> dict[str, str | None]:
        rows = self._select_keyed(
            _T("vault", "personal_value"),
            ("value_id", "value"),
            ("value_id",),
            [(v,) for v in value_ids],
            order_by=("value_id",),
        )
        return dict(rows)

    def vault_redact(self, subject_keys: Sequence[str], *, redaction_id: str | None = None) -> list[str]:
        """Empty every live value of the subjects; returns the value IDs emptied, sorted.

        `redaction_id` (an addition to the plan's signature) is recorded on each emptied value.
        """
        table = _T("vault", "personal_value")
        with self.transaction():
            rows = self._select_keyed(
                table,
                ("value_id",),
                ("subject_key",),
                [(s,) for s in subject_keys],
                order_by=("value_id",),
                where="t.redacted_at IS NULL",
            )
            ids = sorted(r[0] for r in rows)
            now = self.clock()
            self._update_keyed(
                table,
                ("value", "redacted_at", "redaction_id"),
                ("value_id",),
                [(vid, None, now, redaction_id) for vid in ids],
            )
        return ids

    # ------------------------------------------------------------------ status

    def row_estimates(self) -> dict[str, int]:
        """Qualified table name -> estimated rows: DuckDB COUNT(*); Postgres reltuples."""
        schemas = self._schemas()
        marks = ", ".join("?" for _ in schemas)
        rows = self._fetch_all(
            "/*mdm:small*/ SELECT table_schema, table_name FROM information_schema.tables "
            f"WHERE table_schema IN ({marks}) AND table_type = 'BASE TABLE' ORDER BY table_schema, table_name",
            schemas,
        )
        return self._row_estimates([f"{s}.{t}" for s, t in rows])
