"""The guard: writes the store refuses outside their scope (owner: BACKEND, B.5.4).

`SqlStore._execute` calls `check_statement` on every statement: a cheap regex
on the leading keyword and the first `<prefix>_<group>.` target.

The scopes live in a context variable, so a scope opened in one thread or task
never lets another one write.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache

from mdm.config import Settings
from mdm.models.errors import GuardError, PlatformRefused

WRITE_SCOPES = ("commit", "ddl", "simulator")
#: the active write scopes of the current context
_scope: ContextVar[frozenset[str]] = ContextVar("mdm_write_scope", default=frozenset())
#: the prefix the platform's own hub uses; the live suite never writes landing rows there
DEFAULT_PREFIX = "mdm"
#: the variable that marks a live test run against a Lakebase endpoint
LIVE_VARIABLE = "MDM_LIVE_LAKEBASE"

_WRITES = frozenset({"INSERT", "UPDATE", "DELETE", "MERGE", "TRUNCATE", "COPY", "UPSERT"})
_DDL = frozenset({"CREATE", "ALTER", "DROP"})
_LEADING = re.compile(r"\A(?:\s|/\*.*?\*/|--[^\n]*\n)*([A-Za-z]+)", re.S)
_CTE_WRITE = re.compile(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|MERGE\s+INTO)\s+", re.I)


def active_scopes() -> frozenset[str]:
    """The write scopes open in the current context."""
    return _scope.get()


@contextmanager
def _opened(name: str) -> Iterator[None]:
    token = _scope.set(_scope.get() | {name})
    try:
        yield
    finally:
        _scope.reset(token)


@contextmanager
def commit_scope_flag() -> Iterator[None]:
    """Opens the "commit" scope; used only by `SqlStore.commit_scope()`."""
    with _opened("commit"):
        yield


@contextmanager
def ddl_scope() -> Iterator[None]:
    """Opens the "ddl" scope; used only by `init_schema`, `ensure_entity_tables` and `drop_all`."""
    with _opened("ddl"):
        yield


def live_run(prefix: str) -> bool:
    """True for the live suite's own run prefix: `MDM_LIVE_LAKEBASE=1` and a prefix other than "mdm"."""
    return os.environ.get(LIVE_VARIABLE) == "1" and prefix != DEFAULT_PREFIX


def landing_allowed(settings: Settings, prefix: str) -> bool:
    """Whether the hub may create or write the landing tables itself: the local mode, or the live run."""
    return settings.local_mode or live_run(prefix)


@contextmanager
def simulating_integration_platform(settings: Settings, prefix: str) -> Iterator[None]:
    """The only way to write the landing tables: opens the "simulator" scope.

    Raises `PlatformRefused` unless `settings.local_mode`, or `MDM_LIVE_LAKEBASE=1` with a prefix other
    than "mdm" (the live suite's own run prefix).
    """
    if not landing_allowed(settings, prefix):
        raise PlatformRefused("simulator_refused", prefix=prefix)
    with _opened("simulator"):
        yield


@lru_cache(maxsize=64)
def _target_re(prefix: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(prefix)}_([a-z]+)(?![A-Za-z0-9_])")


def statement_kind(sql: str) -> str:
    """The leading keyword of a statement, upper-cased, after any comments ("" when there is none)."""
    found = _LEADING.match(sql)
    return found.group(1).upper() if found else ""


def _first_group(sql: str, prefix: str, start: int = 0) -> str | None:
    found = _target_re(prefix).search(sql, start)
    return found.group(1) if found else None


def check_statement(sql: str, prefix: str) -> None:
    """`GuardError` when the statement is a write outside its scope.

    A write (INSERT/UPDATE/DELETE/MERGE/TRUNCATE/COPY) to <p>_core.* without "commit"; any write to
    <p>_landing.* without "simulator"; UPDATE/DELETE/TRUNCATE on <p>_audit.*; DDL (CREATE/ALTER/DROP) on
    <p>_core, <p>_read or <p>_landing without "ddl"; DROP anywhere without "ddl".
    """
    kind = statement_kind(sql)
    if kind == "WITH":  # a data-modifying common table expression: judge the write inside it
        write = _CTE_WRITE.search(sql)
        if write is None:
            return
        kind = write.group(1).split()[0].upper()
        group = _first_group(sql, prefix, write.end())
    elif kind in _WRITES or kind in _DDL:
        group = _first_group(sql, prefix)
    else:
        return
    scopes = _scope.get()
    if kind in _WRITES:
        if group == "core" and "commit" not in scopes:
            raise GuardError("core_write_outside_commit", statement=kind.lower())
        if group == "landing" and "simulator" not in scopes:
            raise GuardError("landing_write_refused", statement=kind.lower())
        if group == "audit" and kind in ("UPDATE", "DELETE", "TRUNCATE", "MERGE", "UPSERT"):
            raise GuardError("audit_is_insert_only", statement=kind.lower())
        return
    if "ddl" in scopes:
        return
    if kind == "DROP":
        raise GuardError("drop_outside_ddl")
    if group in ("core", "read", "landing"):
        raise GuardError("ddl_outside_scope", statement=kind.lower(), group=group)
