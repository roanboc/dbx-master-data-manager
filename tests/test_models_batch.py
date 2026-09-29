"""Signature batches' contract (story 3.3): keys and IDs, the work a chunk carries, the actions, the access rows
a split writes, and the seven settings with their bounds and shared-store floors."""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
from datetime import UTC, datetime

import pytest

import mdm.models.batch as batch_model
from mdm.config import Settings
from mdm.models.authority import ACTIONS, AccessRow, Actor, allowed
from mdm.models.batch import (
    BATCH_DECISIONS,
    BATCH_ID_RE,
    BATCH_OUTCOMES,
    BATCH_STATUSES,
    BEFORE_TRAY,
    OPEN_BATCH_STATUSES,
    SIGNATURE_KEY_RE,
    BatchChunkWrite,
    BatchSampleWrite,
    chunk_change_set_id,
    new_batch_id,
    signature_key,
)
from mdm.models.changes import WorkWrites
from mdm.models.errors import ConfigError
from mdm.models.quality import (
    BULK_BAND_RE,
    SHARED_AGREEMENT_FLOOR,
    BreakerState,
    QualitySample,
    bulk_band,
)
from mdm.models.records import SourceKey
from mdm.models.safety import SAFE_TEXT_RE
from mdm.models.tasks import Task
from mdm.models.workbench import DECISIONS

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
SIGNATURE = "given_name= · family_name= · birth_date≈ · email∅ · phone∅ · postcode∅ · person_ref∅"


def test_every_dataclass_of_batches_is_frozen_with_slots() -> None:
    classes = [
        obj
        for _, obj in inspect.getmembers(batch_model, inspect.isclass)
        if obj.__module__ == batch_model.__name__ and dataclasses.is_dataclass(obj)
    ]
    assert {c.__name__ for c in classes} == {
        "Batch",
        "BatchItem",
        "BatchChunk",
        "BatchChunkWrite",
        "BatchSampleWrite",
    }
    for cls in classes:
        assert cls.__dataclass_params__.frozen and hasattr(cls, "__slots__"), cls.__name__  # type: ignore[attr-defined]


def test_the_code_lists_of_batches() -> None:
    assert OPEN_BATCH_STATUSES == ("sampling", "ready", "awaiting_checker", "staged", "committing")
    assert set(BEFORE_TRAY) < set(OPEN_BATCH_STATUSES) < set(BATCH_STATUSES)
    assert "committing" in BATCH_OUTCOMES and batch_model.COMMITTING == "committing"
    assert not set(BATCH_DECISIONS) & set(DECISIONS)  # the decide pane never stages a batch
    for action in ("batch_link", "batch_compensate", "confirm_batch", "stop_batch"):
        steward = Actor("persona:data_steward", "person", "data_steward", persona=True)
        owner = Actor("persona:data_owner", "person", "data_owner", persona=True)
        assert action in ACTIONS and allowed(steward, action) and not allowed(owner, action)


def test_a_group_key_is_derived_from_the_entity_the_rule_version_and_the_signature() -> None:
    key = signature_key("person", 3, SIGNATURE)
    digest = hashlib.sha256(f"person|3|{SIGNATURE}".encode()).hexdigest()[:16]
    assert key == f"SIG-{digest}" and SIGNATURE_KEY_RE.match(key) and SAFE_TEXT_RE.match(key)
    assert signature_key("person", 4, SIGNATURE) != key != signature_key("organisation", 3, SIGNATURE)
    assert signature_key("person", 3, "") is None and signature_key("person", None, SIGNATURE) is None
    assert signature_key("person", 3, None) is None


def test_a_task_derives_its_group_key_and_a_task_before_batches_has_none() -> None:
    task = Task("TSK-1", "TK-1", "person", "review", "open", None, (), "review_band", {}, {}, None, NOW, NOW)
    assert (task.signature, task.rule_version, task.signature_key) == (None, None, None)
    grouped = dataclasses.replace(task, signature=SIGNATURE, rule_version=3)
    assert grouped.signature_key == signature_key("person", 3, SIGNATURE)
    assert dataclasses.replace(task, signature="", rule_version=3).signature_key is None


def test_batch_ids_bulk_bands_and_chunk_change_sets_are_safe_keys() -> None:
    batch_id = new_batch_id()
    assert BATCH_ID_RE.match(batch_id) and SAFE_TEXT_RE.match(batch_id) and new_batch_id() != batch_id
    band = bulk_band("person", SIGNATURE)
    assert band == "bulk:" + hashlib.sha256(f"person|{SIGNATURE}".encode()).hexdigest()[:16]
    assert BULK_BAND_RE.match(band) and SAFE_TEXT_RE.match(band)
    assert chunk_change_set_id("BAT-" + "a" * 20, 2) == "CS-" + "a" * 20 + "-2"
    assert SAFE_TEXT_RE.match(chunk_change_set_id(batch_id, 12))


def test_the_breaker_and_samples_read_as_before_with_their_new_fields() -> None:
    state = BreakerState(
        "person", "auto", "normal", NOW, None, {}, None, None, None, None, None, None, None, NOW
    )
    assert state.signature is None and not state.demoted
    sample = QualitySample(
        "QS-1",
        "person",
        "batch",
        "link",
        SourceKey("crm", "C1"),
        (),
        "ev-1",
        "PER-000001",
        (),
        "review",
        SIGNATURE,
        81.5,
        3,
        "persona:data_steward",
        "data_steward",
        NOW,
        "TR-1",
        "TSK-1",
        NOW,
    )
    assert sample.checked_by is None


def _chunk(number: int = 1) -> BatchChunkWrite:
    return BatchChunkWrite("BAT-" + "0" * 20, number, "link", "TR-1", number == 1, False, ("TSK-1",))


def test_a_chunk_rides_on_the_work_merges_once_and_stays_in_the_rest() -> None:
    assert WorkWrites("person").empty()
    sample = BatchSampleWrite("BAT-" + "0" * 20, "TSK-2", "agreed", "TR-2")
    for name, value in (
        ("batch", _chunk()),
        ("batch_samples", (sample,)),
        ("unlabel", (("crm:C1", "PER-000001", "TR-1"),)),
    ):
        assert not WorkWrites("person", **{name: value}).empty(), name
    chunk = WorkWrites("person", batch=_chunk(), unlabel=(("crm:C1", "PER-000001", "TR-1"),))
    decision = WorkWrites("person", batch_samples=(sample,), close_task_ids=("TSK-2",))
    merged = chunk.merged(decision)
    assert merged.batch == _chunk() and merged.batch_samples == (sample,) and merged.unlabel == chunk.unlabel
    assert decision.merged(chunk).batch == _chunk()
    with pytest.raises(ValueError):
        chunk.merged(WorkWrites("person", batch=_chunk(2)))
    part, rest = merged.split([SourceKey("crm", "C1")])
    assert (part.batch, part.batch_samples, part.unlabel) == (None, (), ())
    assert (rest.batch, rest.batch_samples, rest.unlabel) == (merged.batch, (sample,), merged.unlabel)


def test_an_access_row_carries_codes_only() -> None:
    row = AccessRow(
        "persona:data_steward",
        "data_steward",
        "batch_split",
        "person",
        None,
        "birth_date",
        "batch_split",
        {"batch_id": "BAT-" + "0" * 20, "source": "crm:C000101", "task_id": "TSK-1"},
    )
    assert row.detail["source"] == "crm:C000101"
    with pytest.raises(ValueError, match="free text"):
        AccessRow(
            "a",
            "data_steward",
            "batch_split",
            "person",
            None,
            "birth_date",
            "batch_split",
            {"n": "Ada Quill"},
        )
    with pytest.raises(ValueError, match="free text"):
        AccessRow("a", "data_steward", "batch split", "person", None, None, "batch_split")


# ---------------------------------------------------------------------------------------------- the settings

BATCH_SETTINGS = {
    # variable: (field, a good value, parsed, bad values)
    "MDM_FORCED_SAMPLE_BASE": ("forced_sample_base", "7", 7, ["0", "101", "x", "2.5"]),
    "MDM_FORCED_SAMPLE_PER": ("forced_sample_per", "100", 100, ["0", "10001", "x"]),
    "MDM_BATCH_CHECKER_ABOVE": ("batch_checker_above", "3", 3, ["0", "1001", "x"]),
    "MDM_BATCH_UNDO_DAYS": ("batch_undo_days", "7", 7, ["0", "366", "x"]),
    "MDM_BULK_AGREEMENT": ("bulk_agreement", "0.9", 0.9, ["1.1", "-0.1", "nan", "x"]),
    "MDM_BULK_WINDOW": ("bulk_window", "80", 80, ["0", "4", "1001", "x"]),
    "MDM_BULK_MIN_SAMPLES": ("bulk_min_samples", "3", 3, ["0", "x"]),
}


def test_the_batch_settings_parse_and_their_bounds_are_refused_by_name() -> None:
    default = Settings.from_env({})
    assert (
        default.forced_sample_base,
        default.forced_sample_per,
        default.batch_checker_above,
        default.batch_undo_days,
        default.bulk_agreement,
        default.bulk_window,
        default.bulk_min_samples,
    ) == (5, 150, 250, 30, 0.95, 50, 5)
    for variable, (field, good, parsed, bad) in BATCH_SETTINGS.items():
        assert getattr(Settings.from_env({variable: good}), field) == parsed
        for value in bad:
            with pytest.raises(ConfigError) as refused:
                Settings.from_env({variable: value})
            assert refused.value.fields == {"variable": variable}, (variable, value)
    with pytest.raises(ConfigError) as window:
        Settings().with_(bulk_min_samples=60)  # the window holds at least the minimum
    assert window.value.fields == {"variable": "MDM_BULK_WINDOW"}
    assert Settings().with_(bulk_min_samples=60, bulk_window=60).bulk_window == 60
    with pytest.raises(ConfigError) as whole:
        Settings().with_(forced_sample_base=True)
    assert whole.value.fields == {"variable": "MDM_FORCED_SAMPLE_BASE"}


def test_on_a_shared_store_a_batchs_checks_cannot_be_weakened() -> None:
    local = Settings(
        duckdb_path=":memory:",
        sample_share=0.0,
        forced_sample_base=1,
        forced_sample_per=10_000,
        batch_checker_above=1_000,
        bulk_agreement=0.0,
    )
    local.validate_checkpoint_in_force()  # a local store may do without them
    shared = Settings(duckdb_path=":memory:", platform_signals=("DATABRICKS_APP_NAME",), sample_key="k" * 16)
    shared.validate_checkpoint_in_force()
    stricter = shared.with_(
        forced_sample_base=9, forced_sample_per=50, batch_checker_above=10, bulk_agreement=0.99
    )
    stricter.validate_checkpoint_in_force()  # stricter is always allowed
    for changes, variable in (
        ({"forced_sample_base": 4}, "MDM_FORCED_SAMPLE_BASE"),
        ({"forced_sample_per": 151}, "MDM_FORCED_SAMPLE_PER"),
        ({"batch_checker_above": 251}, "MDM_BATCH_CHECKER_ABOVE"),
        ({"bulk_agreement": SHARED_AGREEMENT_FLOOR - 0.01}, "MDM_BULK_AGREEMENT"),
    ):
        with pytest.raises(ConfigError) as refused:
            shared.with_(**changes).validate_checkpoint_in_force()
        assert refused.value.fields == {"variable": variable}
