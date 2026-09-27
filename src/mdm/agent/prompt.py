"""Masked prompts: what a provider may see (owner: SERVICES, B.11).

A prompt carries identifiers (the entity, the master ID, source keys), the
scores and the explanation's comparison names, levels and weights, and the
values of both sides masked by the `mdm_read` rule (`PrivacyService.masked`):
a personal text value becomes its first character and `***`, any other
personal value becomes null. A key the entity model does not name is left
out, since nothing says whether it is personal. `assert_masked` is the
backstop that runs before every call (rule RULE10, decision 17).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from mdm.backend.ddl import attribute_type
from mdm.models.canonical import canonical_json, iso
from mdm.models.entity_model import Attribute, EntityModel
from mdm.models.match import GoldenCandidate
from mdm.services.privacy import is_vault_ref, mask_value

#: what a purpose may be called: it is written to the access log
PURPOSE_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}\Z")
#: the instruction each purpose opens its prompt with; it carries no value
INSTRUCTIONS: Mapping[str, str] = {
    "case_narrative": (
        "Explain to a data steward why this record scores as it does against the golden record: which "
        "comparisons weigh most for and against, what is missing, and which single change would move the "
        "band. Weights are in bits (log2 of m/u); scores are 0 to 100."
    ),
}
#: letters and digits; a value and a prompt are compared as sequences of these words
_WORD = re.compile(r"[^\W_]+")


@dataclass(frozen=True, slots=True)
class MaskedPrompt:
    purpose: str
    text: str
    fields: Mapping[str, Any]


def build_prompt(
    purpose: str,
    model: EntityModel,
    candidate: GoldenCandidate,
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> MaskedPrompt:
    """The explanation and the masked values of both sides; no personal value."""
    if not PURPOSE_RE.match(purpose):
        raise ValueError("bad purpose")
    explanation = candidate.best.explanation
    fields: dict[str, Any] = {
        "entity": model.entity,
        "master_id": candidate.master_id,
        "record": candidate.best.left.text(),
        "member": candidate.best.right.text(),
        "members_scored": candidate.members_scored,
        "blocked_by": candidate.blocked_by,
        "bands": {"upper": model.match.bands.upper, "lower": model.match.bands.lower},
        "explanation": explanation.to_dict(),
        "record_values": masked_values(model, left),
        "golden_values": masked_values(model, right),
    }
    instruction = INSTRUCTIONS.get(purpose, "Advise a data steward on the facts below.")
    text = (
        f"Purpose: {purpose}\n{instruction}\n"
        "Values shown as one character followed by *** are masked; null is masked or missing.\n"
        f"Facts (JSON): {canonical_json(_rounded(fields))}"
    )
    return MaskedPrompt(purpose=purpose, text=text, fields=fields)


def masked_values(model: EntityModel, values: Mapping[str, Any]) -> dict[str, Any]:
    """The values of the attributes the model names, in model order, personal ones masked; other keys dropped."""
    out: dict[str, Any] = {}
    for attribute in model.attributes:
        if attribute.name in values:
            out[attribute.name] = _masked(attribute, values[attribute.name])
    return out


def _masked(attribute: Attribute, value: Any) -> Any:
    if not attribute.personal:
        return _plain(value)
    if attribute.is_reference or is_vault_ref(value):
        return None
    return mask_value(attribute_type(attribute.type, attribute.repeating), value)


def _plain(value: Any) -> Any:
    """A JSON-ready copy: dates as ISO text, tuples and sets as lists, Decimal as text."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime, date)):
        return iso(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return _plain(value.value)
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(_plain(v) for v in value)
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return str(value)


def _rounded(value: Any) -> Any:
    """Floats to three decimals, for the text a provider reads; the fields keep full precision."""
    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, Mapping):
        return {k: _rounded(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_rounded(v) for v in value]
    return value


# ---------------------------------------------------------------------------------------------- the check


def assert_masked(prompt: MaskedPrompt, model: EntityModel, originals: Iterable[Mapping[str, Any]]) -> None:
    """ValueError when any personal value of `originals` appears in the prompt's text or fields.

    Values and prompt are compared as sequences of words (letters and digits, case folded), so "Ann" is
    not found inside "announce" but "ANN" and "ann" are both found as a word. A value of one character
    is not checked, since its masked form shows it, nor a value that equals one of the originals'
    non-personal values, which the prompt carries by right (a city inside a personal address). A doubt
    refuses the prompt, and the caller answers with the stub, so nothing leaves the process.
    """
    haystack = _words(prompt.text)
    for leaf in _leaves(prompt.fields, keys=True):
        haystack.extend(_words(leaf))
    if not haystack:
        return
    records = list(originals)
    allowed = {
        tuple(_words(leaf))
        for record in records
        for attribute in model.attributes
        if not attribute.personal and attribute.name in record
        for leaf in _leaves(record[attribute.name])
    }
    personal = model.personal_attributes()
    for record in records:
        for name in personal:
            for needle in _needles(record.get(name)):
                if tuple(needle) not in allowed and _contains(haystack, needle):
                    raise ValueError(f"personal value of {name} in a prompt")


def _words(text: str) -> list[str]:
    return [word.casefold() for word in _WORD.findall(text)]


def _needles(value: Any) -> Iterator[list[str]]:
    """The word sequences of a personal value's leaves, each at least two characters long in all."""
    for leaf in _leaves(value):
        words = _words(leaf)
        if sum(len(word) for word in words) >= 2:
            yield words


def _leaves(value: Any, *, keys: bool = False) -> Iterator[str]:
    """Every scalar inside `value` as text, and the keys of its mappings when `keys`; vault references
    are skipped."""
    if value is None or isinstance(value, bool) or is_vault_ref(value):
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, (datetime, date)):
        yield iso(value)
        yield value.isoformat()
    elif isinstance(value, (int, float, Decimal)):
        yield str(value)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if keys:
                yield str(key)
            yield from _leaves(item, keys=keys)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            yield from _leaves(item, keys=keys)
    else:
        yield str(value)


def _contains(haystack: list[str], needle: list[str]) -> bool:
    """Whether `needle` occurs in `haystack` as a run of consecutive words."""
    size = len(needle)
    if size == 0 or size > len(haystack):
        return False
    first = needle[0]
    for start, word in enumerate(haystack[: len(haystack) - size + 1]):
        if word == first and haystack[start : start + size] == needle:
            return True
    return False
