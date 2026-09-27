"""Small helpers the services share: safe tokens, JSON forms, value comparison, relationship IDs (owner: SERVICES).

Nothing here reads or writes the store.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from mdm.backend.ddl import attribute_type
from mdm.backend.store import encode
from mdm.models.authority import Actor, Authority
from mdm.models.canonical import canonical_json, iso
from mdm.models.changes import ChangeItem, item_kind
from mdm.models.entity_model import EntityModel
from mdm.models.records import SourceKey
from mdm.models.safety import SAFE_TEXT_RE

#: a stored document holds {"$vault": value_id} in place of a personal value
VAULT_REF = "$vault"
#: the key under which a source record's master-ID hint is kept in its state's references
HINT_KEY = "$master_id"
#: CreateGolden references start with this prefix; master IDs never do
REF_PREFIX = "new:"


def token(text: str) -> str:
    """`text` when it may appear in a safe detail, else a stable hash of it (`h-` + 16 hex)."""
    if isinstance(text, str) and SAFE_TEXT_RE.match(text):
        return text
    return "h-" + hashlib.sha256(str(text).encode()).hexdigest()[:16]


def source_token(source: SourceKey) -> str:
    """A source key as safe text: `system:key`, hashed when the key is not safe."""
    return token(source.text())


def plain(value: Any) -> Any:
    """The JSON form of a value as the store returns it: dates as ISO text, tuples as lists, keys sorted."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    return json.loads(canonical_json(value))


def to_plain(obj: Any) -> Any:
    """What `canonical_json` writes for `obj`, as lists and dicts, without `dataclasses.asdict`'s deep copies.

    `canonical_json(to_plain(x)) == canonical_json(x)` for every value a change item holds.
    """
    if obj is None or isinstance(obj, (str, bool, int, float)):
        return obj
    if isinstance(obj, (datetime, date)):
        return iso(obj)
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, Enum):
        return obj.value
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_plain(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, Mapping):
        return {k: to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_plain(v) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return [to_plain(v) for v in sorted(obj)]
    raise TypeError(f"not JSON serialisable: {type(obj).__name__}")


def fingerprint(
    entity: str,
    action: str,
    actor: Actor,
    authority: Authority,
    items: Sequence[ChangeItem],
    planning_version: int,
) -> str:
    """`models.changes.change_set_fingerprint`, computed without deep copies (large change sets)."""
    document = [
        entity,
        action,
        actor.kind,
        actor.name,
        authority,
        [[item_kind(item), item] for item in items],
        planning_version,
    ]
    text = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=_shallow,
    )
    return hashlib.sha256(text.encode()).hexdigest()[:32]


def _shallow(obj: Any) -> Any:
    """`canonical_json`'s conversions, one level at a time (the encoder recurses): no deep copies."""
    if isinstance(obj, (datetime, date)):
        return iso(obj)
    if isinstance(obj, Decimal):
        return str(obj)
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: getattr(obj, f.name) for f in dataclasses.fields(obj)}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    raise TypeError(f"not JSON serialisable: {type(obj).__name__}")


def is_ref(target: str | None) -> bool:
    """True for a CreateGolden reference (`new:…`), False for a master ID."""
    return isinstance(target, str) and target.startswith(REF_PREFIX)


def relationship_id(
    rel_type: str, origin: SourceKey, attribute: str, target_system: str, target_key: str
) -> str:
    """`"REL-" + sha256(f"{rel_type}|{origin.system}|{origin.key}|{attribute}|{target_system}|{target_key}")[:16]`
    (B.9.9): a changed reference target is a new relationship."""
    text = f"{rel_type}|{origin.system}|{origin.key}|{attribute}|{target_system}|{target_key}"
    return "REL-" + hashlib.sha256(text.encode()).hexdigest()[:16]


def column_types(model: EntityModel) -> dict[str, str]:
    """Attribute -> the logical type of its column in the entity table (references have none)."""
    return {a.name: attribute_type(a.type, a.repeating) for a in model.column_attributes()}


def same_value(left: Any, right: Any, logical: str) -> bool:
    """Equal as the column would store them (a date and its ISO text are equal; 1 and 1.0 in numeric)."""
    try:
        return encode(left, logical) == encode(right, logical)
    except (TypeError, ValueError):
        return canonical_json(left) == canonical_json(right)


def changed_attributes(
    types: Mapping[str, str], before: Mapping[str, Any] | None, after: Mapping[str, Any]
) -> list[str]:
    """The column attributes whose value differs between `before` and `after`, in model order."""
    old = before or {}
    return [
        name for name, logical in types.items() if not same_value(old.get(name), after.get(name), logical)
    ]


def sorted_unique(items: Iterable[Any]) -> list[Any]:
    return sorted(dict.fromkeys(items))


def first_of(items: Sequence[Any], default: Any = None) -> Any:
    return items[0] if items else default
