"""Helpers the engine tests share, and the engine's import rule.

Everything here is invented: names, keys and identifiers. Identifiers are built
with the engine's own check-digit functions, so a valid one is valid by
construction.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mdm.engine.identifiers import luhn_digit, mod97_digits
from mdm.engine.standardise import standardise_record
from mdm.models.entity_model import EntityModel
from mdm.models.records import SourceChange, SourceKey, SourceState, StdRecord

ENGINE_DIR = Path(__file__).resolve().parents[1] / "src" / "mdm" / "engine"
WHEN = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)


def person_ref(base: str) -> str:
    """A valid PERSON_REF: eight digits and the Luhn digit."""
    return base + luhn_digit(base)


def org_reg(base: str) -> str:
    """A valid ORG_REG: eight digits and the two mod-97 digits."""
    return base + mod97_digits(base)


def change(
    system: str,
    key: str,
    payload: Mapping[str, Any],
    *,
    entity: str = "person",
    occurred_at: datetime = WHEN,
    seq: int = 1,
    version: int | None = None,
) -> SourceChange:
    return SourceChange(
        event_id=f"{system}-test-{seq:08d}",
        source_system=system,
        source_key=key,
        entity=entity,
        op="upsert",
        occurred_at=occurred_at,
        source_version=version,
        initial_load=False,
        payload=dict(payload),
        landed_at=occurred_at,
        landing_seq=seq,
    )


def std(model: EntityModel, system: str, key: str, payload: Mapping[str, Any], **kwargs: Any) -> StdRecord:
    """A payload standardised as one landing row of `system`."""
    return standardise_record(
        model, model.source(system), change(system, key, payload, entity=model.entity, **kwargs)
    )


def state(
    record: StdRecord, *, status: str = "active", occurred_at: datetime = WHEN, seq: int = 1
) -> SourceState:
    """A stored state holding a standardised record, as `source_state` would give it back."""
    return SourceState(
        entity=record.entity,
        source=record.source,
        status=status,
        values=record.values,
        match=record.match,
        ids=record.ids,
        references=record.references,
        value_ids={},
        sample_hash=record.sample_hash,
        source_version=None,
        occurred_at=occurred_at,
        landing_seq=seq,
        event_id=f"{record.source.system}-test-{seq:08d}",
        initial_load=False,
        held=False,
        approved_values=record.values,
        approved_event_id=None,
        rules_checked=0,
        rules_failed=0,
        updated_at=occurred_at,
    )


def key(text: str) -> SourceKey:
    return SourceKey.from_text(text)


# ----------------------------------------------------------------------------------------- the import rule

ALLOWED_ROOTS = {
    "__future__",
    "collections",
    "dataclasses",
    "datetime",
    "enum",
    "functools",
    "hashlib",
    "jellyfish",
    "math",
    "rapidfuzz",
    "re",
    "typing",
    "unicodedata",
}


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_engine_imports_only_models_capacity_and_pure_libraries() -> None:
    """No store, no services, no I/O, no clock: the engine imports the models, capacity and pure libraries."""
    for path in sorted(ENGINE_DIR.glob("*.py")):
        for name in _imports(path):
            if name.startswith("mdm."):
                assert name.split(".")[1] in ("engine", "models", "capacity"), (path.name, name)
            else:
                assert name.split(".")[0] in ALLOWED_ROOTS, (path.name, name)


def test_engine_never_reads_the_clock_or_randomness() -> None:
    for path in sorted(ENGINE_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for forbidden in ("datetime.now", "date.today", "utcnow", "time.time", "import random", "open("):
            assert forbidden not in text, (path.name, forbidden)
