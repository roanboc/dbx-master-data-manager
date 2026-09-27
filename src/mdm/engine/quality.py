"""Quality rules on arrival (owner: ENGINE, B.9.8).

Kinds: `required` (present, not a placeholder) -> completeness; `pattern`
(named pattern) -> validity; `range` (`min`/`max`, `today` allowed) ->
validity; `code_list` (value in the copy) -> validity; `checksum` (registered
ID valid, None passes) -> validity; `placeholder` (the value was a placeholder)
-> accuracy. Each result carries a code, never the value. Pure.

A result's dimension is the one its rule declares. An absent value passes every
kind but `required` (completeness is `required`'s question alone), except that
a value which failed standardisation (`StdRecord.invalid`) fails `pattern`. A
pattern tests the value's comparison form (the lower-case e-mail, the postcode
without spaces, the web domain), which is what the named patterns describe. A
code list with no copy loaded fails `code_list`: no value can be in it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from mdm.models.entity_model import EntityModel, ValidationRule, ValidationRules
from mdm.models.errors import NotFound
from mdm.models.records import RuleResult, StdRecord

#: compiled here, never in YAML (`mdm.models.entity_model.PATTERN_NAMES` lists the names)
NAMED_PATTERNS: Mapping[str, re.Pattern[str]] = {
    "email": re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+\Z"),
    "postcode": re.compile(r"^[A-Z0-9]{3,10}\Z"),
    "url": re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+\Z"),
}


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return len(value) > 0
    return True


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _bound(raw: Any, today: date, as_date: bool) -> Any:
    if as_date:
        return today if raw == "today" else _as_date(raw)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return raw


def _in_range(rule: ValidationRule, value: Any, today: date) -> bool:
    """True when the value lies within the rule's bounds; a bound of another type than the value is ignored."""
    as_date = not isinstance(value, (int, float)) or isinstance(value, bool)
    subject = _as_date(value) if as_date else value
    if subject is None:
        return True
    low = _bound(rule.params.get("min"), today, as_date) if "min" in rule.params else None
    high = _bound(rule.params.get("max"), today, as_date) if "max" in rule.params else None
    if low is not None and subject < low:
        return False
    return not (high is not None and subject > high)


def _pattern_text(record: StdRecord, attribute: str) -> str | None:
    form = record.match.get(attribute)
    if isinstance(form, str) and form:
        return form
    value = record.values.get(attribute)
    return str(value) if _present(value) else None


def _one(
    rule: ValidationRule,
    model: EntityModel,
    record: StdRecord,
    code_lists: Mapping[str, frozenset[str]],
    today: date,
) -> str:
    """The rule's code for this record: `ok` or the failure."""
    name = rule.attribute
    value = record.values.get(name)
    present = _present(value)
    kind = rule.kind
    if kind == "required":
        if name in record.placeholders:
            return "placeholder"
        return "ok" if present else "missing"
    if kind == "placeholder":
        return "placeholder" if name in record.placeholders else "ok"
    if kind == "pattern":
        if name in record.invalid:
            return "bad_pattern"
        text = _pattern_text(record, name)
        pattern = NAMED_PATTERNS.get(str(rule.params.get("pattern")))
        if text is None or pattern is None:
            return "ok"
        return "ok" if pattern.match(text) else "bad_pattern"
    if kind == "range":
        return "ok" if not present or _in_range(rule, value, today) else "out_of_range"
    if kind == "code_list":
        if not present:
            return "ok"
        try:
            default_list = model.attribute(name).code_list
        except NotFound:
            default_list = None
        allowed = code_lists.get(str(rule.params.get("code_list", default_list)), frozenset())
        text = str(value).strip()
        return "ok" if text in allowed or text.upper() in allowed else "not_in_code_list"
    if kind == "checksum":
        try:
            scheme = model.attribute(name).scheme
        except NotFound:
            scheme = None
        for registered in record.ids:
            if registered.scheme == scheme and registered.valid is False:
                return "bad_checksum"
        return "ok"
    return "ok"


def check(
    rules: ValidationRules,
    model: EntityModel,
    record: StdRecord,
    code_lists: Mapping[str, frozenset[str]],
    today: date,
) -> list[RuleResult]:
    """One result per rule (passed or not); arrival stores the failures only."""
    results: list[RuleResult] = []
    for rule in rules.rules:
        code = _one(rule, model, record, code_lists, today)
        results.append(
            RuleResult(
                rule_id=rule.rule_id,
                attribute=rule.attribute,
                dimension=rule.dimension,
                passed=code == "ok",
                code=code,
                severity=rule.severity,
            )
        )
    return results
