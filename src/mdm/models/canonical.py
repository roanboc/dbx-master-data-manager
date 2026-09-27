"""Canonical JSON and the one clock default, shared by every package.

`canonical_json` is the only serialisation used for fingerprints, stored JSON
documents and `--json` output, so the same value always gives the same text on
both engines. Re-exported as `mdm.models.canonical_json`.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any


def utcnow() -> datetime:
    """The current time, timezone-aware in UTC: the default `clock` of every service."""
    return datetime.now(UTC)


def iso(value: date | datetime) -> str:
    """ISO 8601 text: a date as `YYYY-MM-DD`; a datetime in UTC ending in `Z` (a naive one is taken as UTC)."""
    if isinstance(value, datetime):
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return value.isoformat()


def _default(obj: Any) -> Any:
    if isinstance(obj, (datetime, date)):
        return iso(obj)
    if isinstance(obj, Decimal):
        return str(obj)
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    raise TypeError(f"not JSON serialisable: {type(obj).__name__}")


def canonical_json(obj: Any) -> str:
    """Deterministic JSON text.

    Keys sorted, no whitespace, UTF-8 kept, NaN and infinity refused (ValueError);
    date and datetime as ISO 8601 (UTC, `Z`), Decimal as text, tuples as lists,
    dataclasses as their fields, enums as their value, sets as sorted lists.
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=_default,
    )
