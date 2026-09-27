"""The starter entity models: valid, self-consistent, and calibrated on the demo world (owner: CLI, B.12).

The calibration runs once per engine (`slow`): the 2,000-person world of seed 7
must resolve with pairwise precision of at least 0.95 and recall of at least
0.85 per entity under the starter weights, priors and bands.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from mdm.demo import DemoConfig, evaluate, generate, land
from mdm.engine.blocking import key_attributes
from mdm.engine.compare import level_count
from mdm.models.entity_model import EntityModel
from mdm.services.context import Hub

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
STARTERS = ("person", "organisation")
PRECISION, RECALL = 0.95, 0.85


def _doc(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@pytest.fixture(scope="module")
def models() -> dict[str, EntityModel]:
    return {name: EntityModel.from_dict(_doc(MODELS / f"{name}.yaml")) for name in STARTERS}


@pytest.fixture(scope="module")
def code_lists() -> dict[str, set[str]]:
    lists = {}
    for path in sorted((MODELS / "codelists").glob("*.yaml")):
        doc = _doc(path)
        lists[doc["name"]] = {v["code"] for v in doc["values"]}
    return lists


def test_every_model_file_is_a_starter_and_parses(models: dict[str, EntityModel]) -> None:
    assert sorted(p.stem for p in MODELS.glob("*.yaml")) == sorted(STARTERS)
    for name, model in models.items():
        assert model.entity == name and model.version >= 1
        assert EntityModel.from_dict(model.to_dict()) == model, "a model survives its own document"


def test_probabilities_sum_to_one_per_comparison(models: dict[str, EntityModel]) -> None:
    for model in models.values():
        rules = model.match
        assert 0 < rules.prior < 0.5
        assert 0 < rules.bands.lower < rules.bands.upper < 100
        for spec in rules.comparisons:
            assert len(spec.m) == len(spec.u) == level_count(spec), spec.name
            assert math.isclose(sum(spec.m), 1.0, abs_tol=1e-3), spec.name
            assert math.isclose(sum(spec.u), 1.0, abs_tol=1e-3), spec.name
            assert all(0 < p < 1 for p in (*spec.m, *spec.u)), spec.name
            # agreement is evidence for a match, the last level evidence against
            assert spec.m[0] > spec.u[0] and spec.m[-1] < spec.u[-1], spec.name


def test_every_referenced_attribute_and_code_list_exists(
    models: dict[str, EntityModel], code_lists: dict[str, set[str]]
) -> None:
    for model in models.values():
        names = {a.name for a in model.attributes}
        systems = {s.system for s in model.sources}
        for spec in model.match.comparisons:
            assert spec.attribute in names
        for attributes in key_attributes(model.match).values():
            assert attributes <= names
        for rule in model.match.hard_rules:
            attribute = model.attribute(rule.attribute)
            assert attribute.strong and attribute.checksum
        for attribute in model.survivorship.attributes:
            assert attribute in names
        for rule in model.validation.rules:
            assert rule.attribute in names
            if rule.kind == "code_list":
                assert rule.params["code_list"] in code_lists
        for attribute in model.attributes:
            if attribute.code_list:
                assert attribute.code_list in code_lists
            if attribute.reference:
                target = models[attribute.reference.entity]
                assert attribute.reference.key_source in {s.system for s in target.sources}
            for placeholder in attribute.placeholders:
                assert set(placeholder.sources) <= systems
        for source in model.sources:
            assert set(source.trust_by_attribute) <= names
        assert model.defaults.get("calling_code") == "999", "invented numbers never reach a real network"


def test_code_list_labels_are_plainly_invented(code_lists: dict[str, set[str]]) -> None:
    doc = _doc(MODELS / "codelists" / "country.yaml")
    assert all(v["code"].startswith("X") and v["label"].startswith("Example Country") for v in doc["values"])


@pytest.mark.slow
def test_starter_weights_resolve_the_demo_world(hub: Hub) -> None:
    world = generate(DemoConfig(persons=2000, organisations=500, seed=7))
    land(hub.store, hub.settings, world)
    report = hub.arrival.run(started_by=hub.actor)
    assert report.rejected == 0 and report.read == len(world.rows)
    for entity in STARTERS:
        scores = evaluate(hub.store, entity, world)
        assert scores["precision"] >= PRECISION, (entity, scores)
        assert scores["recall"] >= RECALL, (entity, scores)
