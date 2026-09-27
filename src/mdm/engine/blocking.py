"""Blocking: key expressions, keys per record, candidate ranking (owner: ENGINE, B.9.2).

Grammar (YAML `keys`): `FORM | fn(FORM[, INT])`, FORM a match-form name;
functions `metaphone`, `soundex`, `nysiis` (jellyfish; a precomputed form is
used when it exists), `first(x, n)`, `last(x, n)`, `sorted_tokens(x, n)` (x a
token list: the first n sorted tokens joined by a space), `id(x)` (only when the
registered ID is valid: `"<scheme>:<value>"`). A pass's key is its parts joined
by `|`, lower case; if any part is missing the record has no key for that pass.
`mdm.models.entity_model.split_key_expression` and `KEY_FUNCTIONS` hold the
syntax the model validator already checks.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import jellyfish

from mdm.models.entity_model import KEY_FUNCTIONS, MatchRules, split_key_expression
from mdm.models.errors import ModelError
from mdm.models.records import RegisteredId, SourceKey, StdRecord

_UNSAFE = re.compile(r"[^A-Za-z0-9_.:=/+\-]")
_PHONETIC: Mapping[str, Callable[[str], str]] = {
    "metaphone": jellyfish.metaphone,
    "soundex": jellyfish.soundex,
    "nysiis": jellyfish.nysiis,
}


@dataclass(frozen=True, slots=True)
class KeyExpr:
    function: str | None  # None: the form itself
    form: str
    arg: int | None = None

    @property
    def attribute(self) -> str:
        """The attribute whose forms the expression reads: the part of the form before the first dot."""
        return self.form.split(".", 1)[0]


@lru_cache(maxsize=1024)
def parse_key(expr: str) -> KeyExpr:
    """`ModelError` on a bad expression."""
    parts = split_key_expression(expr)
    if parts is None:
        raise ModelError([f"bad_key:{_UNSAFE.sub('_', str(expr))[:60]}"])
    function, form, arg = parts
    if function is not None:
        if function not in KEY_FUNCTIONS:
            raise ModelError([f"unknown_function:{_UNSAFE.sub('_', function)[:60]}"])
        if KEY_FUNCTIONS[function] != (arg is not None) or (arg is not None and arg < 1):
            raise ModelError([f"bad_argument:{_UNSAFE.sub('_', str(expr))[:60]}"])
    return KeyExpr(function=function, form=form, arg=arg)


def key_attributes(rules: MatchRules) -> dict[str, frozenset[str]]:
    """Pass -> the attributes its keys read (for estimation: those comparisons are fixed in the pass's EM)."""
    return {p.name: frozenset(parse_key(expr).attribute for expr in p.keys) for p in rules.blocking}


def fixed_comparisons(rules: MatchRules) -> dict[str, frozenset[str]]:
    """Pass -> the comparisons on the attributes its keys read: the `fixed` argument of that pass's `em`."""
    read = key_attributes(rules)
    return {
        name: frozenset(c.name for c in rules.comparisons if c.attribute in attributes)
        for name, attributes in read.items()
    }


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, (list, tuple, dict)):
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip()
    return text or None


def _id_for(value: str, ids: Sequence[RegisteredId]) -> RegisteredId | None:
    for registered in ids:
        if registered.value == value and registered.valid is True:
            return registered
    return None


def _part(expr: KeyExpr, match: Mapping[str, Any], ids: Sequence[RegisteredId]) -> str | None:
    raw = match.get(expr.form)
    function = expr.function
    if function is None:
        return _text(raw)
    if function == "sorted_tokens":
        if not isinstance(raw, (list, tuple)):
            return None
        tokens = sorted(str(token) for token in raw if token not in (None, ""))
        return " ".join(tokens[: expr.arg]) or None
    if function in _PHONETIC:
        precomputed = _text(match.get(f"{expr.form}.{function}"))
        if precomputed is not None:
            return precomputed
        text = _text(raw)
        if text is None:
            return None
        joined = text.replace(" ", "")
        return (_PHONETIC[function](joined) or None) if joined else None
    text = _text(raw)
    if text is None:
        return None
    if function == "first":
        return text[: expr.arg]
    if function == "last":
        return text[-(expr.arg or 1) :]
    if function == "id":
        registered = _id_for(text, ids)
        return f"{registered.scheme}:{registered.value}" if registered is not None else None
    return None


def keys_from_forms(
    rules: MatchRules, match: Mapping[str, Any], ids: Sequence[RegisteredId]
) -> dict[str, tuple[str, ...]]:
    """Pass -> keys (0 or 1 each), from a record's match forms and registered IDs."""
    out: dict[str, tuple[str, ...]] = {}
    for blocking_pass in rules.blocking:
        parts: list[str] = []
        for expression in blocking_pass.keys:
            part = _part(parse_key(expression), match, ids)
            if part is None:
                break
            parts.append(part)
        else:
            if parts:
                out[blocking_pass.name] = ("|".join(parts).lower(),)
                continue
        out[blocking_pass.name] = ()
    return out


def blocking_keys(rules: MatchRules, record: StdRecord) -> dict[str, tuple[str, ...]]:
    """Pass -> keys (0 or 1 each)."""
    return keys_from_forms(rules, record.match, record.ids)


def rank_candidates(shared: Mapping[SourceKey, int], cap: int) -> tuple[list[SourceKey], int]:
    """Keep the `cap` candidates sharing the most passes (then by source key); returns (kept, dropped count)."""
    ordered = sorted(shared, key=lambda source: (-shared[source], source.system, source.key))
    keep = max(cap, 0)
    return ordered[:keep], max(len(ordered) - keep, 0)
