"""The guard: writes the store refuses outside their scope (owner: BACKEND, B.5.4).

`SqlStore._execute` calls `check_statement` on every statement: a cheap regex
on the leading keyword and the first `<prefix>_<group>.` target, whatever its
case.

The scopes live in a context variable, so a scope opened in one thread or task
never lets another one write.

The guard checks the hub's own statements, so a defect in the hub cannot write
where it must not. It is not the security boundary: on the platform the
database grants are (the hub's role, the landing grants, the listener grants).
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

WRITE_SCOPES = ("commit", "ddl", "simulator", "redact")
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


@contextmanager
def redact_scope() -> Iterator[None]:
    """Opens the "redact" scope; used only by `services.privacy.Vault.redact`, the one UPDATE of the vault."""
    with _opened("redact"):
        yield


def live_run(prefix: str, settings: Settings) -> bool:
    """True for the live suite's own run prefix: `MDM_LIVE_LAKEBASE=1`, a prefix other than "mdm", and a
    process the platform did not start (inside an App or a job the switch is never honoured)."""
    return os.environ.get(LIVE_VARIABLE) == "1" and prefix != DEFAULT_PREFIX and not settings.platform_signals


def landing_allowed(settings: Settings, prefix: str) -> bool:
    """Whether the hub may create or write the landing tables itself: the local mode, or the live run."""
    return settings.local_mode or live_run(prefix, settings)


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
    return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(prefix)}_([a-z]+)(?![A-Za-z0-9_])", re.I)


def statement_kind(sql: str) -> str:
    """The leading keyword of a statement, upper-cased, after any comments ("" when there is none)."""
    found = _LEADING.match(sql)
    return found.group(1).upper() if found else ""


def _first_group(sql: str, prefix: str, start: int = 0) -> str | None:
    found = _target_re(prefix).search(sql, start)
    return found.group(1).lower() if found else None


def check_statement(sql: str, prefix: str) -> None:
    """`GuardError` when the statement is a write outside its scope.

    A write (INSERT/UPDATE/DELETE/MERGE/TRUNCATE/COPY) to <p>_core.* without "commit"; any write to
    <p>_landing.* without "simulator"; UPDATE/DELETE/TRUNCATE/MERGE on <p>_audit.*; DELETE/TRUNCATE/MERGE
    on <p>_vault.*, and UPDATE there without "redact"; any DDL (CREATE/ALTER/DROP) without "ddl".
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
        if group == "vault" and kind in ("DELETE", "TRUNCATE", "MERGE", "UPSERT"):
            raise GuardError("vault_values_are_redacted_not_deleted", statement=kind.lower())
        if group == "vault" and kind == "UPDATE" and "redact" not in scopes:
            raise GuardError("vault_update_outside_redact", statement=kind.lower())
        return
    if "ddl" in scopes:
        return
    if kind == "DROP":
        raise GuardError("drop_outside_ddl")
    raise GuardError("ddl_outside_scope", statement=kind.lower(), group=group or "none")
