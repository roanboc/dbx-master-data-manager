"""No personal value outside the vault and the value columns (rule RULE10).

Every reject detail, task reason, suggestion and evidence, rule result, job
progress and error, `Conflict` and `Forbidden` message, and log line is built
through these functions. They carry attribute names, codes, identifiers,
source keys and counts, never values. The pattern refuses spaces and `@`, so a
full name or an e-mail address cannot pass; a single word can, which is why
`tests/test_services_personal_data.py` is the backstop.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

#: attribute names, codes, identifiers, source keys; no space, no "@", at most 120 characters
SAFE_TEXT_RE = re.compile(r"^[A-Za-z0-9_.:=/+\-]{0,120}\Z")


def safe(value: Any) -> Any:
    """`value` if it may appear in a detail, else ValueError("free text in a detail").

    int, float, bool and None pass; a str must match `SAFE_TEXT_RE`; lists,
    tuples and dicts are checked recursively (dict keys too) and returned as
    lists and dicts; anything else is refused.
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if SAFE_TEXT_RE.match(value):
            return value
        raise ValueError("free text in a detail")
    if isinstance(value, (list, tuple)):
        return [safe(item) for item in value]
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("free text in a detail")
            out[safe(key)] = safe(item)
        return out
    raise ValueError("free text in a detail")


def safe_detail(**fields: Any) -> dict[str, Any]:
    """A detail document whose every key and value passed `safe`."""
    return {safe(key): safe(value) for key, value in fields.items()}


def _render(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ",".join(_render(item) for item in value)
    if isinstance(value, Mapping):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return str(value)


def safe_message(code: str, **fields: Any) -> str:
    """One line built from a code and safe fields: `stale_row master_id=PER-000012`.

    Raises ValueError when the code or a field is not safe, so a message can
    never carry a personal value.
    """
    safe(code)
    checked = safe_detail(**fields)
    parts = [code] + [f"{key}={_render(value)}" for key, value in checked.items()]
    return " ".join(parts)
