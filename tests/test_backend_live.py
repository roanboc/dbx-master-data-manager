"""Live: the store on a real Lakebase endpoint (`make test-live`; MDM_LIVE_LAKEBASE=1, MDM_LAKEBASE_ENDPOINT).

In a run prefix of its own, dropped at the end: the schema, the landing table
created from `mdm ddl --group landing` (the integration platform's DDL), landing
rows written through the simulator (allowed only in a live run prefix), one
arrival and commit under an explicit actor, one feed read. No persona is used,
since personas are refused on a shared store.
"""

from __future__ import annotations

import pytest

from tests.conftest import LIVE, LIVE_ACTOR, START_ENV, new_prefix

if not LIVE:
    pytest.skip("MDM_LIVE_LAKEBASE=1 runs the live suite", allow_module_level=True)

pytestmark = pytest.mark.live


def test_a_run_prefix_on_lakebase_end_to_end(small_world: object) -> None:
    pytest.importorskip("databricks.sdk", reason="the databricks extra is not installed")
    from mdm.backend import ddl, guard
    from mdm.backend.factory import open_store
    from mdm.config import Settings
    from mdm.demo import land
    from mdm.services.context import Hub
    from tests.conftest import CODELISTS, MODELS

    base = Settings.from_env(START_ENV)
    if not base.lakebase_endpoint:
        pytest.skip("MDM_LAKEBASE_ENDPOINT names the endpoint the live run signs in to")
    settings = base.with_(backend="postgres", schema_prefix=new_prefix(), models_dir=str(MODELS))
    assert not settings.local_mode
    store = open_store(settings)
    try:
        store.init_schema(create_landing=False)
        with guard.ddl_scope():
            for statement in ddl.all_ddl(store.prefix, store.engine, ["landing"]):
                store._execute(statement)
        assert store.table_columns("landing", "source_change")
        hub = Hub.open(settings, store=store, actor=LIVE_ACTOR)
        try:
            hub.codelists.load_dir(CODELISTS, hub.actor)
            for path in sorted(MODELS.glob("*.yaml")):
                model = hub.registry.load_file(path, hub.actor)
                hub.registry.publish(model.entity, model.version, hub.actor)
            land(store, settings, small_world)  # type: ignore[arg-type]
            report = hub.arrival.run(started_by=hub.actor)
            assert report.commits >= 1
            page = hub.feed.read(0)
            assert page.commits and page.next_watermark >= 1
        finally:
            hub.close()
    finally:
        store.drop_all()
        store.close()
