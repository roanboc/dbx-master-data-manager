"""Lakebase sign-in: the only touchpoint between the store and the Databricks SDK (owner: BACKEND, B.5.3).

All SDK drift stays in this file; the SDK is imported lazily, never at module
import, and tests hand in a fake workspace object.

A Lakebase endpoint (`projects/<p>/branches/<b>/endpoints/<e>`) is reached
with an OAuth token as the password. A token lives an hour; `token()` keeps
the one it has until fewer than ten minutes remain, then asks for another. A
connection outlives its token, so the pool calls `connection_kwargs()` only
when it opens a new connection, and each call gets a token good for at least
ten more minutes.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from mdm.config import LOCAL_HOSTS, SAFE_SSLMODES, Settings
from mdm.models.errors import ConfigError, MdmError

#: the database every Lakebase endpoint is created with
DEFAULT_DATABASE = "databricks_postgres"
#: a token is refreshed when fewer than this many seconds remain
REFRESH_MARGIN_SECONDS = 600
#: how long a token is taken to live when the credential names no expiry
DEFAULT_TOKEN_SECONDS = 3600
APPLICATION_NAME = "master-data-manager"


class Credentials(Protocol):
    def host(self) -> str: ...

    def token(self) -> str: ...  # cached; refreshed when fewer than 10 minutes remain


def workspace_client() -> Any:
    """A `databricks.sdk.WorkspaceClient()`, imported here and only here (the `databricks` extra)."""
    try:
        from databricks.sdk import WorkspaceClient
    except ImportError:  # the extra is not installed
        raise MdmError("databricks_sdk_missing", extra="databricks") from None
    return WorkspaceClient()


def _expiry_seconds(expire_time: Any, fallback: float) -> float:
    """An SDK expiry as epoch seconds: a protobuf Timestamp, a datetime, RFC 3339 text, or a number."""
    if expire_time is None:
        return fallback
    if isinstance(expire_time, (int, float)):
        return float(expire_time)
    if isinstance(expire_time, datetime):
        aware = expire_time if expire_time.tzinfo else expire_time.replace(tzinfo=UTC)
        return aware.timestamp()
    if isinstance(expire_time, str):
        try:
            return datetime.fromisoformat(expire_time.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return fallback
    seconds = getattr(expire_time, "seconds", None)
    if isinstance(seconds, int) and seconds > 0:
        return float(seconds) + getattr(expire_time, "nanos", 0) / 1e9
    return fallback


class LakebaseCredentials:
    """Host and short-lived token of one Lakebase endpoint (`projects/<p>/branches/<b>/endpoints/<e>`)."""

    def __init__(
        self,
        endpoint: str,
        workspace: Callable[[], Any] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not endpoint:
            raise ConfigError("bad_setting", variable="MDM_LAKEBASE_ENDPOINT")
        self.endpoint = endpoint
        self._workspace = workspace or workspace_client
        self._clock = clock
        self._lock = threading.Lock()
        self._client: Any = None
        self._host: str | None = None
        self._user: str | None = None
        self._token: str | None = None
        self._expires_at = 0.0
        #: tokens generated so far (tests read it)
        self.generated = 0

    def _w(self) -> Any:
        if self._client is None:
            self._client = self._workspace()
        return self._client

    def host(self) -> str:
        """PGHOST if set, else w.postgres.get_endpoint(name=endpoint).status.hosts.host."""
        named = os.environ.get("PGHOST", "").strip()
        if named:
            return named
        with self._lock:
            if self._host is None:
                endpoint = self._w().postgres.get_endpoint(name=self.endpoint)
                status = getattr(endpoint, "status", None)
                hosts = getattr(status, "hosts", None)
                host = getattr(hosts, "host", None)
                if not host:
                    raise MdmError("lakebase_host_unknown")
                self._host = host
            return self._host

    def token(self) -> str:
        """w.postgres.generate_database_credential(endpoint=endpoint).token, cached with its expire_time."""
        with self._lock:
            now = self._clock()
            if self._token is None or self._expires_at - now < REFRESH_MARGIN_SECONDS:
                credential = self._w().postgres.generate_database_credential(endpoint=self.endpoint)
                token = getattr(credential, "token", None)
                if not token:
                    raise MdmError("lakebase_token_missing")
                self._token = token
                self._expires_at = _expiry_seconds(
                    getattr(credential, "expire_time", None), now + DEFAULT_TOKEN_SECONDS
                )
                self.generated += 1
            return self._token

    def expires_at(self) -> float:
        """Epoch seconds when the cached token expires (0 before the first token)."""
        return self._expires_at

    def user(self) -> str:
        """PGUSER, else DATABRICKS_CLIENT_ID, else w.current_user.me().user_name."""
        for variable in ("PGUSER", "DATABRICKS_CLIENT_ID"):
            named = os.environ.get(variable, "").strip()
            if named:
                return named
        with self._lock:
            if self._user is None:
                me = self._w().current_user.me()
                name = getattr(me, "user_name", None)
                if not name:
                    raise MdmError("lakebase_user_unknown")
                self._user = name
            return self._user


def tls_mode(settings: Settings, host: str) -> str:
    """The `sslmode` of a connection that carries a token: `PGSSLMODE`, default require.

    `ConfigError("bad_setting", variable="PGSSLMODE")` for a mode that could fall back to clear text
    (disable, allow, prefer) unless the host is a unix socket or loopback, where no network lies between.
    """
    sslmode = settings.pg_sslmode or "require"
    if sslmode not in SAFE_SSLMODES and not (host.startswith("/") or host in LOCAL_HOSTS):
        raise ConfigError("bad_setting", variable="PGSSLMODE")
    return sslmode


def connection_kwargs(settings: Settings, credentials: Credentials | None) -> Callable[[], dict[str, Any]]:
    """A callable the pool calls for every new connection.

    host, port, dbname (default databricks_postgres on Lakebase), user (PGUSER, else DATABRICKS_CLIENT_ID,
    else the SDK's current user), password=credentials.token() (fresh each call), sslmode (require on
    Lakebase). Without credentials: `MDM_POSTGRES_DSN` alone when it is set, else the PG* settings
    given (libpq reads the rest, `PGPASSWORD` included, from the environment). With credentials, the
    TLS mode is `tls_mode`'s, so the token never travels over a network connection that could fall back
    to clear text. Every connection is
    in autocommit: the store opens transactions itself.
    """

    def kwargs() -> dict[str, Any]:
        out: dict[str, Any] = {"autocommit": True, "application_name": APPLICATION_NAME}
        if credentials is None:
            if settings.postgres_dsn:
                return out
            if settings.pg_host:
                out["host"] = settings.pg_host
                out["port"] = settings.pg_port
            if settings.pg_database:
                out["dbname"] = settings.pg_database
            if settings.pg_user:
                out["user"] = settings.pg_user
            if settings.pg_sslmode:
                out["sslmode"] = settings.pg_sslmode
            return out
        host = settings.pg_host or credentials.host()
        sslmode = tls_mode(settings, host)
        user = settings.pg_user
        if not user:
            named = getattr(credentials, "user", None)
            user = named() if callable(named) else os.environ.get("DATABRICKS_CLIENT_ID", "")
        out.update(
            host=host,
            port=settings.pg_port or 5432,
            dbname=settings.pg_database or DEFAULT_DATABASE,
            user=user,
            password=credentials.token(),
            sslmode=sslmode,
        )
        return out

    return kwargs
