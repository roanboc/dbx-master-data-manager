"""The Postgres engine and Lakebase sign-in: markers, credentials, the pool, locks, leases, reconnects (B.5.3)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest

from mdm.backend import lakebase_auth
from mdm.backend.factory import open_store
from mdm.backend.lakebase_auth import (
    DEFAULT_DATABASE,
    REFRESH_MARGIN_SECONDS,
    LakebaseCredentials,
    connection_kwargs,
)
from mdm.backend.postgres_engine import PostgresStore, advisory_key, connection_gone, local_server, to_pg
from mdm.backend.store import SqlStore
from mdm.config import Settings
from mdm.models.errors import ConfigError, MdmError, PlatformRefused
from tests.conftest import ENGINES

ENDPOINT = "projects/demo/branches/production/endpoints/primary"
needs_postgres = pytest.mark.skipif("postgres" not in ENGINES, reason="the run leaves Postgres out")


class FakeWorkspace:
    """The slice of `WorkspaceClient` the store touches: postgres.get_endpoint, generate_database_credential, me."""

    def __init__(
        self, clock: Callable[[], float], *, lifetime: float = 3600.0, host: str = "instance.example.com"
    ):
        self.clock = clock
        self.lifetime = lifetime
        self.calls: list[str] = []
        workspace = self

        class Postgres:
            def get_endpoint(self, name: str) -> Any:
                workspace.calls.append(f"get_endpoint:{name}")
                return SimpleNamespace(status=SimpleNamespace(hosts=SimpleNamespace(host=host)))

            def generate_database_credential(self, endpoint: str) -> Any:
                workspace.calls.append(f"credential:{endpoint}")
                expires = datetime.fromtimestamp(workspace.clock() + workspace.lifetime, tz=UTC)
                return SimpleNamespace(token=f"token-{len(workspace.calls)}", expire_time=expires)

        class CurrentUser:
            def me(self) -> Any:
                workspace.calls.append("me")
                return SimpleNamespace(user_name="service-principal-app-id")

        self.postgres = Postgres()
        self.current_user = CurrentUser()


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def no_pg_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("PGHOST", "PGUSER", "PGPORT", "PGDATABASE", "PGSSLMODE", "DATABRICKS_CLIENT_ID"):
        monkeypatch.delenv(name, raising=False)


# ------------------------------------------------------------------------------------------ pure helpers


def test_question_marks_become_psycopg_markers() -> None:
    assert to_pg("SELECT a FROM t WHERE b = ? AND c = ?", [1, 2]) == "SELECT a FROM t WHERE b = %s AND c = %s"
    assert to_pg("SELECT '?' AS q, ? AS p", [1]) == "SELECT '?' AS q, %s AS p"
    assert to_pg("SELECT 'it''s ?' , ?", [1]) == "SELECT 'it''s ?' , %s"
    assert to_pg("SELECT 5 % 2, ?", [1]) == "SELECT 5 %% 2, %s"
    assert to_pg("SELECT 5 % 2") == "SELECT 5 % 2"  # nothing bound: psycopg reads no markers


def test_advisory_keys_differ_per_prefix_and_fit_a_bigint() -> None:
    keys = {advisory_key(f"{p}:commit") for p in ("mdm", "t0123456789", "mdm2")}
    assert len(keys) == 3
    assert all(0 <= k < 2**63 for k in keys)
    assert advisory_key("mdm:commit") != advisory_key("mdm:arrival")
    assert advisory_key("mdm:commit") == advisory_key("mdm:commit")


def test_connection_gone_reads_what_libpq_says() -> None:
    assert connection_gone(psycopg.OperationalError("server closed the connection unexpectedly"))
    assert connection_gone(psycopg.OperationalError("terminating connection due to administrator command"))
    assert connection_gone(psycopg.InterfaceError("the connection is closed"))
    assert not connection_gone(psycopg.OperationalError("canceling statement due to statement timeout"))
    assert not connection_gone(ValueError("closed"))


# ------------------------------------------------------------------------------------------ Lakebase credentials


def test_credentials_cache_the_token_and_refresh_it_before_expiry(no_pg_variables: None) -> None:
    clock = Clock()
    workspace = FakeWorkspace(clock)
    credentials = LakebaseCredentials(ENDPOINT, workspace=lambda: workspace, clock=clock)
    first = credentials.token()
    assert credentials.token() == first
    clock.now += 3600 - REFRESH_MARGIN_SECONDS - 1  # still more than ten minutes left
    assert credentials.token() == first
    clock.now += 2  # fewer than ten minutes left
    second = credentials.token()
    assert second != first
    assert credentials.generated == 2
    assert workspace.calls.count(f"credential:{ENDPOINT}") == 2
    assert credentials.expires_at() == pytest.approx(clock.now + 3600)


def test_credentials_find_the_host_and_user(no_pg_variables: None, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    workspace = FakeWorkspace(clock)
    credentials = LakebaseCredentials(ENDPOINT, workspace=lambda: workspace, clock=clock)
    assert credentials.host() == "instance.example.com"
    assert credentials.host() == "instance.example.com"
    assert workspace.calls.count(f"get_endpoint:{ENDPOINT}") == 1
    assert credentials.user() == "service-principal-app-id"
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "app-client-id")
    assert credentials.user() == "app-client-id"
    monkeypatch.setenv("PGUSER", "named-role")
    assert credentials.user() == "named-role"
    monkeypatch.setenv("PGHOST", "named.example.com")
    assert credentials.host() == "named.example.com"
    with pytest.raises(ConfigError):
        LakebaseCredentials("")


def test_credentials_read_every_expiry_shape() -> None:
    fallback = 99.0
    assert lakebase_auth._expiry_seconds(None, fallback) == fallback
    assert (
        lakebase_auth._expiry_seconds(SimpleNamespace(seconds=1_800_000_000, nanos=500_000_000), 0)
        == 1_800_000_000.5
    )
    assert (
        lakebase_auth._expiry_seconds("2027-01-15T08:00:00Z", 0)
        == datetime(2027, 1, 15, 8, tzinfo=UTC).timestamp()
    )
    assert lakebase_auth._expiry_seconds("not a time", fallback) == fallback
    assert lakebase_auth._expiry_seconds(123, fallback) == 123.0


def test_the_pool_kwargs_give_a_fresh_password(no_pg_variables: None) -> None:
    clock = Clock()
    workspace = FakeWorkspace(clock, lifetime=900)  # every token is inside the refresh margin after 5 minutes
    credentials = LakebaseCredentials(ENDPOINT, workspace=lambda: workspace, clock=clock)
    kwargs = connection_kwargs(Settings(backend="postgres", lakebase_endpoint=ENDPOINT), credentials)
    first = kwargs()
    assert first["host"] == "instance.example.com"
    assert first["dbname"] == DEFAULT_DATABASE
    assert first["sslmode"] == "require"
    assert first["user"] == "service-principal-app-id"
    assert first["autocommit"] is True
    clock.now += 301
    second = kwargs()
    assert second["password"] != first["password"]


def test_the_token_never_travels_over_a_connection_that_may_be_in_clear(no_pg_variables: None) -> None:
    clock = Clock()
    workspace = FakeWorkspace(clock)
    credentials = LakebaseCredentials(ENDPOINT, workspace=lambda: workspace, clock=clock)
    for mode in ("disable", "allow", "prefer"):
        settings = Settings(backend="postgres", lakebase_endpoint=ENDPOINT, pg_sslmode=mode)
        with pytest.raises(ConfigError, match="PGSSLMODE"):
            connection_kwargs(settings, credentials)()
        with pytest.raises(ConfigError, match="PGSSLMODE"):
            PostgresStore(settings, credentials)
    for mode in ("require", "verify-ca", "verify-full"):
        settings = Settings(backend="postgres", lakebase_endpoint=ENDPOINT, pg_sslmode=mode)
        assert connection_kwargs(settings, credentials)()["sslmode"] == mode
    # a unix socket or loopback has no network to intercept: a test server without TLS still signs in
    local = Settings(backend="postgres", lakebase_endpoint=ENDPOINT, pg_sslmode="disable", pg_host="/tmp/pg")
    assert lakebase_auth.tls_mode(local, "/tmp/pg") == "disable"
    assert lakebase_auth.tls_mode(local, "127.0.0.1") == "disable"


def test_the_pool_kwargs_without_lakebase() -> None:
    dsn_only = connection_kwargs(Settings(backend="postgres", postgres_dsn="host=/tmp dbname=x"), None)()
    assert dsn_only == {"autocommit": True, "application_name": lakebase_auth.APPLICATION_NAME}
    by_parts = connection_kwargs(
        Settings(backend="postgres", pg_host="db.example.com", pg_port=6432, pg_database="hub", pg_user="u"),
        None,
    )()
    assert (by_parts["host"], by_parts["port"], by_parts["dbname"], by_parts["user"]) == (
        "db.example.com",
        6432,
        "hub",
        "u",
    )
    assert "password" not in by_parts


def test_the_sdk_is_imported_only_when_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def refuse(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("databricks"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    with pytest.raises(MdmError, match="databricks_sdk_missing"):
        lakebase_auth.workspace_client()
    LakebaseCredentials(ENDPOINT)  # constructing asks nothing of the SDK


# ------------------------------------------------------------------------------------------ against a real Postgres


def pg_parts(dsn: str) -> dict[str, Any]:
    return psycopg.conninfo.conninfo_to_dict(dsn)


@needs_postgres
@pytest.mark.postgres
def test_open_store_signs_in_with_the_token(request: pytest.FixtureRequest, no_pg_variables: None) -> None:
    parts = pg_parts(request.getfixturevalue("postgres_dsn"))

    class Signed:
        def __init__(self) -> None:
            self.tokens = 0

        def host(self) -> str:
            return str(parts["host"])

        def token(self) -> str:
            self.tokens += 1
            return str(parts.get("password", "unused"))

        def user(self) -> str:
            return str(parts["user"])

    signed = Signed()
    settings = Settings(
        backend="postgres",
        lakebase_endpoint=ENDPOINT,
        pg_port=int(parts.get("port", 5432)),
        pg_database=str(parts["dbname"]),
        pg_sslmode="disable",
        schema_prefix="tsigned01",
    )
    store = open_store(settings, credentials=signed)
    try:
        assert isinstance(store, PostgresStore)
        assert store._fetch_all("/*mdm:small*/ SELECT current_user") == [(parts["user"],)]
        assert signed.tokens >= 1
    finally:
        store.close()


@needs_postgres
@pytest.mark.postgres
def test_the_run_lease_is_exclusive_across_connections(make_store: Callable[..., SqlStore]) -> None:
    one = make_store("postgres")
    two = open_store(one.settings)  # the same prefix, another pool
    try:
        with one.exclusive_lease("arrival") as held:
            assert held is True
            with two.exclusive_lease("arrival") as other:
                assert other is False
            with two.exclusive_lease("estimate") as another_name:
                assert another_name is True
        with two.exclusive_lease("arrival") as after:
            assert after is True
        with pytest.raises(ValueError):
            with one.exclusive_lease("commit"):
                pass
    finally:
        two.close()


def test_the_run_lease_on_duckdb(make_store: Callable[..., SqlStore]) -> None:
    store = make_store("duckdb")
    with store.exclusive_lease("arrival") as held:
        assert held is True
        with store.exclusive_lease("arrival") as again:
            assert again is False
    with store.exclusive_lease("arrival") as after:
        assert after is True


def backend_pid(store: SqlStore) -> int:
    return int(store._fetch_all("/*mdm:small*/ SELECT pg_backend_pid()")[0][0])


def kill(dsn: str, pid: int) -> None:
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute("SELECT pg_terminate_backend(%s)", [pid])


@needs_postgres
@pytest.mark.postgres
def test_a_dropped_connection_is_retried_once_outside_a_transaction(
    make_store: Callable[..., SqlStore],
) -> None:
    store = make_store("postgres")
    pid = backend_pid(store)
    kill(store.settings.postgres_dsn, pid)
    assert store.last_commit_version() == 0  # retried on a new connection
    assert backend_pid(store) != pid


@needs_postgres
@pytest.mark.postgres
def test_a_dropped_connection_fails_the_transaction(make_store: Callable[..., SqlStore]) -> None:
    store = make_store("postgres")
    with pytest.raises(psycopg.OperationalError), store.transaction():
        pid = backend_pid(store)
        kill(store.settings.postgres_dsn, pid)
        store.last_commit_version()
    assert store.last_commit_version() == 0  # the store carries on with a new connection


@needs_postgres
@pytest.mark.postgres
def test_an_unreachable_server_fails_fast(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    from mdm.backend import postgres_engine

    monkeypatch.setattr(postgres_engine, "OPEN_TIMEOUT", 1.0)
    with pytest.raises(MdmError, match="store_unreachable"):
        PostgresStore(Settings(backend="postgres", postgres_dsn=f"host={tmp_path} port=1 connect_timeout=1"))


def test_personas_are_honoured_only_for_a_server_on_this_machine() -> None:
    assert local_server("/tmp/mdm-pg", "", "mdm")
    assert local_server("localhost", "127.0.0.1", "mdm") and local_server("localhost", "::1", "mdm")
    assert not local_server("instance.example.com", "203.0.113.7", "mdm")
    assert not local_server("/tmp/mdm-pg", "", DEFAULT_DATABASE)  # Lakebase's database, even tunnelled
    with pytest.raises(ConfigError, match="MDM_POOL_MAX"):
        Settings.from_env({"MDM_POOL_MAX": "1"})


@needs_postgres
@pytest.mark.postgres
def test_a_persona_store_on_lakebases_database_is_refused(request: pytest.FixtureRequest) -> None:
    dsn = request.getfixturevalue("postgres_dsn")
    with psycopg.connect(dsn, autocommit=True) as admin:
        if not admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", [DEFAULT_DATABASE]).fetchone():
            admin.execute(f"CREATE DATABASE {DEFAULT_DATABASE}")
    parts = pg_parts(dsn)
    parts["dbname"] = DEFAULT_DATABASE
    shared = psycopg.conninfo.make_conninfo(**parts)
    with pytest.raises(PlatformRefused, match="personas_need_a_local_postgres"):
        open_store(
            Settings(backend="postgres", postgres_dsn=shared, allow_personas=True, schema_prefix="tpersona1")
        )
    open_store(Settings(backend="postgres", postgres_dsn=shared, schema_prefix="tpersona1")).close()


@needs_postgres
@pytest.mark.postgres
def test_connections_the_server_dropped_while_idle_are_replaced(make_store: Callable[..., SqlStore]) -> None:
    """★ After an idle disconnect or a scale to zero, the lease and a transaction start on live connections."""
    store = make_store("postgres")
    assert isinstance(store, PostgresStore) and store._pool.max_size >= 2
    pids: set[int] = set()
    with store.transaction():  # a second connection, taken by another thread, so the pool holds two
        pids.add(backend_pid(store))
        other = threading.Thread(target=lambda: pids.add(backend_pid(store)))
        other.start()
        other.join(timeout=30)
    assert len(pids) == 2
    for pid in pids:
        kill(store.settings.postgres_dsn, pid)
    with store.exclusive_lease("arrival") as held:
        assert held
        with store.transaction():
            assert store.last_commit_version() == 0
