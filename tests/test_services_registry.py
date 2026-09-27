"""The model registry: drafts, publication, compatibility, a new entity as data (owner: SERVICES, B.10)."""

from __future__ import annotations

import copy

import pytest
import yaml

from mdm.models.errors import Forbidden, ModelError, NotFound
from mdm.services.context import Hub
from tests.conftest import CODELISTS, MODELS
from tests.test_services_fixtures import arrive, land, row


def _doc(name: str) -> dict:
    return yaml.safe_load((MODELS / f"{name}.yaml").read_text(encoding="utf-8"))


@pytest.fixture
def bare(store, engine_settings) -> Hub:
    """A hub over an initialised store with nothing loaded."""
    hub = Hub.open(engine_settings, store=store, as_role="data_owner")
    hub.codelists.load_dir(CODELISTS, hub.actor)
    return hub


def test_a_draft_is_published_under_a_flagged_bootstrap_authority(bare) -> None:
    model = bare.registry.load_doc(_doc("organisation"), bare.actor)
    assert (model.version, model.match.version) == (1, 1)
    assert bare.registry.versions("organisation")[0][:2] == (1, "draft")
    with pytest.raises(NotFound):
        bare.registry.published("organisation")
    published = bare.registry.publish("organisation", None, bare.actor)
    assert published.version == 1 and bare.registry.published_entities() == ["organisation"]
    assert bare.store.table_columns("core", "organisation")[:3] == ["master_id", "status", "survivor_id"]
    assert "name" in bare.store.table_columns("read", "organisation")
    audit = [r for r in _audit(bare) if r["action"] == "publish_model"]
    assert audit and audit[0]["authority_kind"] == "bootstrap" and audit[0]["commit_version"] is None
    assert "initiative 4" in audit[0]["authority_ref"] and audit[0]["evidence"]["flag"] == "bootstrap"


def _audit(hub) -> list[dict]:
    # governance events are change sets without a commit version: read them through their fingerprints' table
    rows = hub.store._fetch_all(
        f"/*mdm:paged*/ SELECT change_set_id FROM {hub.store.t('audit', 'change_set')} ORDER BY created_at LIMIT 100"
    )
    found = hub.store.change_sets([r[0] for r in rows])
    return sorted(found.values(), key=lambda r: r["created_at"])


def test_a_compatible_version_adds_a_column_before_any_record(bare) -> None:
    bare.registry.publish(
        "organisation", bare.registry.load_doc(_doc("organisation"), bare.actor).version, bare.actor
    )
    doc = _doc("organisation")
    doc["attributes"].append({"name": "sector", "type": "text"})
    second = bare.registry.load_doc(doc, bare.actor)
    assert second.version == 2
    bare.registry.publish("organisation", 2, bare.actor)
    assert bare.store.table_columns("core", "organisation")[-1] == "sector"
    assert bare.store.table_columns("read", "organisation")[-1] == "sector"
    assert [s for _, s, _ in bare.registry.versions("organisation")] == ["retired", "published"]


def test_an_incompatible_version_is_refused(bare) -> None:
    bare.registry.publish(
        "organisation", bare.registry.load_doc(_doc("organisation"), bare.actor).version, bare.actor
    )
    doc = _doc("organisation")
    doc["attributes"] = [a for a in doc["attributes"] if a["name"] != "website"]
    doc["match"]["comparisons"] = [c for c in doc["match"]["comparisons"] if c["attribute"] != "website"]
    doc["validation"]["rules"] = [r for r in doc["validation"]["rules"] if r["attribute"] != "website"]
    doc["sources"] = [{k: v for k, v in s.items() if k != "trust_by_attribute"} for s in doc["sources"]]
    doc["survivorship"]["attributes"].pop("website")
    bare.registry.load_doc(doc, bare.actor)
    with pytest.raises(ModelError) as raised:
        bare.registry.publish("organisation", 2, bare.actor)
    assert raised.value.code == "incompatible_model"
    assert any(p.startswith("attribute_removed:") for p in raised.value.problems)


def test_republishing_is_refused_once_golden_records_exist(hub) -> None:
    land(hub, [row("finance", "F000001", "organisation", {"name": "Tessel Holdings Ltd"}, version=1)])
    arrive(hub)
    doc = _doc("organisation")
    doc["attributes"].append({"name": "sector", "type": "text"})
    hub.registry.load_doc(doc, hub.actor)
    with pytest.raises(Forbidden) as raised:
        hub.registry.publish("organisation", None, hub.actor)
    message = str(raised.value)
    assert "golden_records_exist" in message and "initiative-4" in message and "dry-run" in message
    version = hub.registry.save_match_draft(
        "organisation", hub.registry.match_rules("organisation"), hub.actor
    )
    assert version == 3  # the model draft took match v2 with it
    assert hub.registry.match_rules("organisation", version).version == version
    with pytest.raises(Forbidden):
        hub.registry.publish_rules("organisation", "match", version, hub.actor)


def test_only_a_data_owner_publishes(bare) -> None:
    from mdm.models.authority import Actor

    steward = Actor("persona:technical_steward", "person", "technical_steward", persona=True)
    model = bare.registry.load_doc(_doc("organisation"), steward)
    with pytest.raises(Forbidden):
        bare.registry.publish("organisation", model.version, steward)
    consumer = Actor("reader", "person", "consumer")
    with pytest.raises(Forbidden):
        bare.registry.load_doc(_doc("organisation"), consumer)


def test_a_new_entity_lands_and_arrives_with_no_code_change(hub, tmp_path) -> None:
    doc = {
        "entity": "vessel",
        "code": "VES",
        "domain": "asset",
        "style": "consolidated",
        "display_name": "{name}",
        "attributes": [
            {"name": "name", "type": "text", "standardise": "text", "required": True},
            {"name": "hull_ref", "type": "text", "standardise": "code"},
            {"name": "tonnage", "type": "number"},
        ],
        "sources": [{"system": "fleet", "trust": 1, "policy": {"new": "auto"}}],
        "match": {
            "version": 1,
            "mode": "consolidate",
            "prior": 0.01,
            "bands": {"upper": 90, "lower": 60},
            "blocking": [{"name": "hull", "keys": ["hull_ref"]}],
            "comparisons": [
                {
                    "name": "hull_ref",
                    "attribute": "hull_ref",
                    "comparator": "exact",
                    "m": [0.95, 0.05],
                    "u": [0.001, 0.999],
                },
                {"name": "name", "attribute": "name", "comparator": "name"},
            ],
        },
        "survivorship": {"version": 1, "default": ["source_trust", "recency"], "attributes": {}},
        "validation": {
            "version": 1,
            "rules": [
                {"rule_id": "VES-V1", "attribute": "name", "kind": "required", "dimension": "completeness"}
            ],
        },
    }
    path = tmp_path / "vessel.yaml"
    path.write_text(yaml.safe_dump(copy.deepcopy(doc)), encoding="utf-8")
    model = hub.registry.load_file(path, hub.actor)
    hub.registry.publish(model.entity, model.version, hub.actor)
    land(
        hub,
        [
            row("fleet", "V1", "vessel", {"name": "Morning Tern", "hull_ref": "hx-100", "tonnage": 1200.5}),
            row("fleet", "V2", "vessel", {"name": "Morning Tern", "hull_ref": "HX-100", "tonnage": 1200.5}),
            row("fleet", "V3", "vessel", {"name": "Grey Plover", "hull_ref": "HX-200"}),
        ],
    )
    report = arrive(hub)
    assert report.rejected == 0
    rows = hub.store.golden_page("vessel", None, 10)
    assert len(rows) == 2
    assert {str(r.values["tonnage"]) for r in rows if r.values["tonnage"] is not None} == {"1200.5000000000"}
