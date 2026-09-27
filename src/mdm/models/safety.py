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
#: a master ID ("ORG-000123"): what a notice, a link or a log line may echo back of a reference
MASTER_ID_RE = re.compile(r"^[A-Z][A-Z0-9]{0,9}-[0-9]{1,12}\Z")
#: a source key ("crm:C000123")
SOURCE_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}:[A-Za-z0-9_.\-]{1,80}\Z")
#: a task ID ("TSK-5cfa2e93a4720ad3")
TASK_ID_RE = re.compile(r"^TSK-[A-Za-z0-9]{1,40}\Z")
#: a comparison signature (`mdm.engine.score.signature`): comparison names, each with its agreement mark,
#: joined by " · " ("given_name= · phone∅"); the one text with spaces a staged decision or a label holds
SIGNATURE_RE = re.compile(r"^(?:[a-z][a-z0-9_]{0,40}[=≈≠∅](?: · [a-z][a-z0-9_]{0,40}[=≈≠∅]){0,63})?\Z")


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


def safe_signature(value: str | None) -> str | None:
    """`value` if it is None or a comparison signature (SIGNATURE_RE), else ValueError("free text in a
    detail"): a label and a staged decision keep one, and it must never carry a value."""
    if value is None or (isinstance(value, str) and SIGNATURE_RE.match(value)):
        return value
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
