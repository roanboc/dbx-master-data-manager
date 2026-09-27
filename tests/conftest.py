"""Fixtures: the store on every engine, the hub with the starter models, the demo world.

Every store test runs on DuckDB and again on Postgres — the one
`MDM_TEST_POSTGRES` names, else a throwaway server `postgres_server.py` starts
for the run — each Postgres test in a schema prefix of its own (`t` + 10 hex),
dropped afterwards. Without a Postgres the Postgres half is skipped with a
warning, or fails when `MDM_REQUIRE_POSTGRES=1` (CI). `MDM_TEST_ENGINES`
narrows the engines (`make test-fast` runs DuckDB only). With
`MDM_LIVE_LAKEBASE=1` and `MDM_LAKEBASE_ENDPOINT` set, a third engine,
`lakebase`, runs the `live` tests in a run prefix that is dropped at the end,
under an explicit test actor, since personas are refused on a shared store.
"""

from __future__ import annotations

import os
import secrets
import warnings
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from mdm.backend.factory import open_store
from mdm.backend.store import SqlStore
from mdm.config import PLATFORM_VARIABLES, Settings
from mdm.demo import DemoConfig, DemoWorld, generate, land
from mdm.models.authority import Actor
from mdm.models.entity_model import EntityModel
from mdm.services.arrival import ArrivalReport
from mdm.services.context import Hub
from tests.postgres_server import NO_POSTGRES, postgres_for_the_run, postgres_required

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
CODELISTS = MODELS / "codelists"
LIVE = os.environ.get("MDM_LIVE_LAKEBASE") == "1"
ENGINES = [e for e in os.environ.get("MDM_TEST_ENGINES", "duckdb,postgres").split(",") if e] + (
    ["lakebase"] if LIVE else []
)
#: the environment as the run started: the live engine reads its endpoint from here
START_ENV = dict(os.environ)
#: the actor of the live engine: a person with the data owner's role, not a persona
LIVE_ACTOR = Actor(name="test-owner", kind="person", role="data_owner")
#: settings the tests must not inherit from a developer's shell or a platform runtime
_SETTING_VARIABLES = (
    *PLATFORM_VARIABLES,
    "MDM_BACKEND",
    "MDM_DUCKDB_PATH",
    "MDM_POSTGRES_DSN",
    "MDM_LAKEBASE_ENDPOINT",
    "MDM_SCHEMA_PREFIX",
    "MDM_MODELS_DIR",
    "MDM_ROLE",
    "MDM_ALLOW_PERSONAS",
    "MDM_POOL_MAX",
    "MDM_CONNECTION_MAX_AGE",
    "MDM_AGENT_PROVIDER",
    "MDM_AGENT_ENDPOINT",
    "MDM_THROTTLE_ROWS_PER_HOUR",
    "MDM_GAP_TIMEOUT_SECONDS",
    # libpq and Settings.from_env read these: a developer's shell must not point a test elsewhere
    "PGHOST",
    "PGPORT",
    "PGDATABASE",
    "PGUSER",
    "PGPASSWORD",
    "PGSSLMODE",
    "PGSERVICE",
)
_ENGINE_MARKS = {"postgres": [pytest.mark.postgres], "lakebase": [pytest.mark.live]}


def engine_params() -> list[Any]:
    """The engines of the run as pytest params, each Postgres one marked `postgres`, the live one `live`."""
    return [pytest.param(e, marks=_ENGINE_MARKS.get(e, []), id=e) for e in ENGINES]


#: a test that needs Postgres itself (another connection, a server-side kill): the Postgres param only, with
#: its `postgres` mark, so `-m postgres` selects it; an empty set when the run leaves Postgres out
ONLY_POSTGRES_ENGINE = pytest.mark.parametrize(
    "engine", [p for p in engine_params() if p.id == "postgres"], indirect=True
)
#: seconds a test waits for a barrier or a thread before it fails instead of hanging
THREAD_TIMEOUT = 60.0


def join_all(threads: list[Any], timeout: float = THREAD_TIMEOUT) -> None:
    """Join every thread, failing the test when one is still running after `timeout` (a deadlock)."""
    for thread in threads:
        thread.join(timeout=timeout)
    alive = [t.name for t in threads if t.is_alive()]
    assert not alive, f"threads still running after {timeout} s: {alive}"


def new_prefix() -> str:
    """A schema prefix for one test, so tests never see each other's rows: `t` + 10 hex."""
    return "t" + secrets.token_hex(5)


def base_settings() -> Settings:
    """The local mode on an in-memory DuckDB, reading the starter models."""
    return Settings(duckdb_path=":memory:", models_dir=str(MODELS))


def settings_for(engine: str, base: Settings, request: pytest.FixtureRequest) -> Settings:
    """The settings of one engine: DuckDB as given; Postgres in a fresh prefix with personas allowed;
    Lakebase from the starting environment, in a fresh run prefix."""
    if engine == "duckdb":
        return base
    if engine == "postgres":
        return base.with_(
            backend="postgres",
            postgres_dsn=request.getfixturevalue("postgres_dsn"),
            schema_prefix=new_prefix(),
            allow_personas=True,
        )
    if engine == "lakebase":
        pytest.importorskip("databricks.sdk", reason="the databricks extra is not installed")
        live = Settings.from_env(START_ENV)
        if not live.lakebase_endpoint:
            pytest.skip("MDM_LAKEBASE_ENDPOINT names the endpoint the live run signs in to")
        return live.with_(backend="postgres", schema_prefix=new_prefix(), models_dir=base.models_dir)
    raise ValueError(f"unknown engine {engine}")


def dispose(store: SqlStore) -> None:
    """Drop a Postgres test prefix and close the store (an in-memory DuckDB goes with its connection)."""
    try:
        if store.engine != "duckdb":
            store.drop_all()
    finally:
        store.close()


def open_hub(settings: Settings, store: SqlStore, engine: str) -> Hub:
    """A hub over `store` with the code lists and the starter models loaded and published.

    As the persona `data_owner` locally; as `LIVE_ACTOR` on Lakebase. Models load in file-name order, so
    organisation is published before person, which references it.
    """
    if engine == "lakebase":
        hub = Hub.open(settings, store=store, actor=LIVE_ACTOR)
    else:
        hub = Hub.open(settings, store=store, as_role="data_owner")
    hub.codelists.load_dir(CODELISTS, hub.actor)
    for path in sorted(MODELS.glob("*.yaml")):
        model = hub.registry.load_file(path, hub.actor)
        hub.registry.publish(model.entity, model.version, hub.actor)
    return hub


def load_model(name: str) -> EntityModel:
    """A starter model file parsed and validated."""
    with (MODELS / f"{name}.yaml").open(encoding="utf-8") as handle:
        return EntityModel.from_dict(yaml.safe_load(handle))


# ------------------------------------------------------------------------------------------ environment


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """No setting leaks in from the shell or a platform runtime; a test sets what it needs."""
    for name in _SETTING_VARIABLES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def on_platform(monkeypatch: pytest.MonkeyPatch) -> str:
    """As if running as a Databricks App: sets DATABRICKS_APP_PORT; returns the variable's name."""
    monkeypatch.setenv("DATABRICKS_APP_PORT", "8000")
    return "DATABRICKS_APP_PORT"


# ------------------------------------------------------------------------------------------ Postgres and stores


@pytest.fixture(scope="session")
def postgres_dsn() -> Iterator[str]:
    """The Postgres of the run: named by MDM_TEST_POSTGRES, or started here and stopped at the end.

    Skips every test that asks for it when the run leaves Postgres out (`MDM_TEST_ENGINES=duckdb`)."""
    if "postgres" not in ENGINES:
        pytest.skip("the run leaves Postgres out")
    dsn, let_go = postgres_for_the_run()
    if dsn is None:
        if postgres_required():
            pytest.fail(f"{NO_POSTGRES} (MDM_REQUIRE_POSTGRES=1)")
        warnings.warn(NO_POSTGRES, stacklevel=1)
        pytest.skip(NO_POSTGRES)
    yield dsn
    let_go()


@pytest.fixture
def settings() -> Settings:
    """The local mode on an in-memory DuckDB, reading the starter models."""
    return base_settings()


@pytest.fixture(params=engine_params())
def engine(request: pytest.FixtureRequest) -> str:
    """Each test that asks for it runs once per engine: duckdb, postgres (and lakebase when live)."""
    return request.param


@pytest.fixture
def engine_settings(engine: str, settings: Settings, request: pytest.FixtureRequest) -> Settings:
    """The settings of the engine under test (a fresh prefix on Postgres)."""
    return settings_for(engine, settings, request)


@pytest.fixture
def store(engine_settings: Settings) -> Iterator[SqlStore]:
    """An initialised store on the engine under test, landing tables included; dropped afterwards."""
    opened = open_store(engine_settings)
    try:
        opened.init_schema(create_landing=True)
        yield opened
    finally:
        dispose(opened)


@pytest.fixture
def make_store(settings: Settings, request: pytest.FixtureRequest) -> Iterator[Callable[..., SqlStore]]:
    """`make_store(engine, *, init=True, **setting_changes)`: another store, e.g. one per engine for parity."""
    opened: list[SqlStore] = []

    def make(engine: str, *, init: bool = True, **changes: Any) -> SqlStore:
        engine_settings = settings_for(engine, settings, request)
        if changes:
            engine_settings = engine_settings.with_(**changes)
        new = open_store(engine_settings)
        opened.append(new)
        if init:
            new.init_schema(create_landing=True)
        return new

    yield make
    for each in reversed(opened):
        dispose(each)


# ------------------------------------------------------------------------------------------ models and the hub


@pytest.fixture(scope="session")
def person_model() -> EntityModel:
    return load_model("person")


@pytest.fixture(scope="session")
def org_model() -> EntityModel:
    return load_model("organisation")


@pytest.fixture
def hub(store: SqlStore, engine_settings: Settings, engine: str) -> Iterator[Hub]:
    """Models and code lists loaded and published, as the persona data_owner (LIVE_ACTOR on Lakebase)."""
    opened = open_hub(engine_settings, store, engine)
    try:
        yield opened
    finally:
        opened.close()


# ------------------------------------------------------------------------------------------ the demo world


@pytest.fixture(scope="session")
def small_world() -> DemoWorld:
    return generate(DemoConfig(persons=200, organisations=60, seed=7))


@pytest.fixture
def landed(hub: Hub, small_world: DemoWorld) -> DemoWorld:
    """The small world written to the landing table of the hub's store."""
    land(hub.store, hub.settings, small_world)
    return small_world


@pytest.fixture
def arrived(hub: Hub, landed: DemoWorld) -> ArrivalReport:
    """One arrival run over the landed small world, for tests that change state afterwards."""
    return hub.arrival.run(started_by=hub.actor)


@pytest.fixture(scope="module", params=engine_params())
def arrived_world(
    request: pytest.FixtureRequest, small_world: DemoWorld
) -> Iterator[tuple[Hub, DemoWorld, ArrivalReport]]:
    """One landed and arrived store per module and engine, shared by read-only tests."""
    engine = request.param
    engine_settings = settings_for(engine, base_settings(), request)
    opened = open_store(engine_settings)
    try:
        opened.init_schema(create_landing=True)
        world_hub = open_hub(engine_settings, opened, engine)
        try:
            land(opened, engine_settings, small_world)
            report = world_hub.arrival.run(started_by=world_hub.actor)
            yield world_hub, small_world, report
        finally:
            world_hub.close()
    finally:
        dispose(opened)


# ------------------------------------------------------------------------------------------ time


_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass
class FakeClock:
    """A clock tests move by hand: `clock()` / `now()`, `advance(seconds)`, `sleep(seconds)`, `monotonic()`."""

    current: datetime = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
    slept: float = 0.0

    def now(self) -> datetime:
        return self.current

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> datetime:
        self.current += timedelta(seconds=seconds)
        return self.current

    def sleep(self, seconds: float) -> None:
        """A `sleep` for a rate limiter: records the time and advances the clock instead of waiting."""
        self.slept += seconds
        self.advance(seconds)

    def monotonic(self) -> float:
        return (self.current - _EPOCH).total_seconds()


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()
