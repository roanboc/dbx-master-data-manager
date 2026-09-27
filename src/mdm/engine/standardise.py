"""Standardisation: raw payload values to display values and comparison forms (owner: ENGINE, B.9.1).

`values` (display): cleaned originals in their own case, dates as `date`, phones
E.164, emails lower case, registered IDs normalised, repeating groups as lists
of dicts (each field cleaned). References go to `StdRecord.references`
(attribute -> the referenced source key), never to `values`. A placeholder date
is absent from `values` and `match` and listed in `placeholders`.

`match` holds the comparison forms of `mdm.models.entity_model.MATCH_FORMS`
(`attr` and `attr.<form>`); phonetic codes are computed here once per record,
never per pair. Every match form is plain JSON (text, a boolean, a list of
text), so a form read back from the store equals the form computed here.

A value that cannot be standardised (a phone with too few digits, an e-mail
without a domain, a date that does not parse, a mapping where a text was
expected) is left out of `values` and `match` and its attribute is listed in
`invalid`. Pure: no I/O, no clock.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import jellyfish

from mdm.engine.blocking import keys_from_forms
from mdm.engine.identifiers import registered_id
from mdm.models.canonical import canonical_json
from mdm.models.entity_model import Attribute, EntityModel, PlaceholderSpec, SourceSpec
from mdm.models.records import RegisteredId, SourceChange, SourceKey, StdRecord

#: legal forms, folded, to their canonical token ("&" becomes "and" before folding)
LEGAL_FORMS: Mapping[str, str] = {
    "ltd": "ltd",
    "limited": "ltd",
    "inc": "inc",
    "incorporated": "inc",
    "llc": "llc",
    "plc": "plc",
    "corp": "corp",
    "corporation": "corp",
    "co": "co",
    "company": "co",
    "gmbh": "gmbh",
    "ag": "ag",
    "sa": "sa",
    "sas": "sas",
    "bv": "bv",
    "nv": "nv",
    "pty": "pty",
    "proprietary": "pty",
    "srl": "srl",
    "oy": "oy",
    "ab": "ab",
}
STOPWORDS = frozenset({"the", "and", "of"})

_WHITESPACE = re.compile(r"\s+")
_SCHEME_PREFIX = re.compile(r"^[a-z][a-z0-9+.\-]*://")
_ISO_DATE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ].*)?$")
_COMPACT_DATE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_YMD_SLASH = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})$")
_SLASH_OR_DASH = re.compile(r"^(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})$")
_DOTTED = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")
#: letters NFKD does not decompose into a base letter and a mark
_LETTERS = str.maketrans({"ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe", "ł": "l", "Ł": "l",
                          "đ": "d", "Đ": "d", "ð": "d", "Ð": "d", "þ": "th", "Þ": "th", "ı": "i"})  # fmt: skip


@dataclass(frozen=True, slots=True)
class OrgName:
    core: str  # folded name without legal forms and stop words
    first: str | None  # the first core token
    tokens: tuple[str, ...]  # the core tokens
    legal: str  # the canonical legal form, "" when none


# --------------------------------------------------------------------------------------------- text


def clean_text(raw: Any) -> str | None:
    """None/"" -> None; NFKC; strip; collapse whitespace; drop control characters."""
    if raw is None or isinstance(raw, (dict, list, tuple, set)):
        return None
    text = raw if isinstance(raw, str) else str(raw)
    text = unicodedata.normalize("NFKC", text)
    text = "".join(" " if char in "\t\n\r\f\v" else char for char in text)
    text = "".join(char for char in text if unicodedata.category(char) not in ("Cc", "Cf"))
    text = _WHITESPACE.sub(" ", text).strip()
    return text or None


def fold(text: str) -> str:
    """NFKD, drop combining marks, casefold, punctuation -> space, collapse whitespace."""
    decomposed = unicodedata.normalize("NFKD", text.translate(_LETTERS))
    out: list[str] = []
    for char in decomposed:
        category = unicodedata.category(char)
        if category.startswith("M"):
            continue
        if category[0] in "PSZC":
            out.append(" ")
        else:
            out.append(char)
    return _WHITESPACE.sub(" ", "".join(out).casefold()).strip()


def phonetic(function: Any, text: str) -> str | None:
    """The phonetic code of a folded text with its spaces removed, so "o brenna", "obrenna" and
    "o-brenna" share a code; None for an empty text."""
    joined = text.replace(" ", "")
    return (function(joined) or None) if joined else None


def org_name(raw: str) -> OrgName | None:
    """The folded core of an organisation's name: "&" -> "and", dots joined ("B.V." -> "bv"), trailing legal
    forms and then stop words removed, never down to nothing ("Company" stays "company")."""
    text = clean_text(raw)
    if text is None:
        return None
    tokens = fold(text.replace("&", " and ").replace(".", "")).split()
    if not tokens:
        return None
    legal: list[str] = []
    while len(tokens) > 1 and tokens[-1] in LEGAL_FORMS:
        legal.insert(0, LEGAL_FORMS[tokens.pop()])
    core = [token for token in tokens if token not in STOPWORDS] or tokens
    return OrgName(core=" ".join(core), first=core[0], tokens=tuple(core), legal=" ".join(legal))


# --------------------------------------------------------------------------------------------- contact data


def phone_e164(raw: Any, calling_code: str) -> str | None:
    """Keep digits and a leading "+"; "00…" -> "+…"; "+…" kept; "0…" -> "+<cc>" + rest; bare -> "+<cc>" + digits.

    Valid when 8 <= digits <= 15, else None (the attribute is listed in `invalid`).
    """
    text = clean_text(raw)
    if text is None:
        return None
    plus = text.startswith("+")
    if plus:
        text = text.replace("(0)", "")
    digits = "".join(char for char in text if char.isascii() and char.isdigit())
    cc = "".join(char for char in str(calling_code or "") if char.isdigit())
    if plus:
        number = digits
    elif digits.startswith("00"):
        number = digits[2:]
    elif digits.startswith("0"):
        if not cc:
            return None
        number = cc + digits[1:]
    else:
        if not cc:
            return None
        number = cc + digits
    if not 8 <= len(number) <= 15:
        return None
    return "+" + number


def email(raw: Any) -> str | None:
    """Strip, lower case; valid when it has one "@", a dot in the domain and no space."""
    text = clean_text(raw)
    if text is None:
        return None
    text = text.lower()
    if " " in text or text.count("@") != 1:
        return None
    local, domain = text.split("@")
    if not local or "." not in domain or domain.startswith(".") or domain.endswith(".") or ".." in domain:
        return None
    return text


def postcode(raw: Any) -> str | None:
    """Upper case, spaces and hyphens removed."""
    text = clean_text(raw)
    if text is None:
        return None
    return text.upper().replace(" ", "").replace("-", "") or None


def url_domain(raw: Any) -> str | None:
    """Lower case; scheme, "www." and path removed."""
    text = clean_text(raw)
    if text is None:
        return None
    text = _SCHEME_PREFIX.sub("", text.lower())
    for separator in ("/", "?", "#"):
        text = text.split(separator, 1)[0]
    text = text.rsplit("@", 1)[-1].split(":", 1)[0]
    if text.startswith("www."):
        text = text[4:]
    return text.strip(".") or None


def code(raw: Any) -> str | None:
    """Upper case, stripped."""
    text = clean_text(raw)
    return text.upper() if text is not None else None


# --------------------------------------------------------------------------------------------- dates


def _make_date(year: str | int, month: str | int, day: str | int) -> date | None:
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def iso_date(raw: Any, order: str) -> date | None:
    """date/datetime, YYYY-MM-DD, YYYYMMDD, YYYY/MM/DD, D/M/YYYY or M/D/YYYY per order ("dmy"|"mdy"|"ymd"), D.M.YYYY.

    A day-month-year text with slashes or hyphens is read month first only for an `mdy` source.
    """
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int):
        raw = str(raw)
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    for pattern in (_ISO_DATE, _COMPACT_DATE, _YMD_SLASH):
        found = pattern.match(text)
        if found:
            return _make_date(*found.groups())
    found = _SLASH_OR_DASH.match(text)
    if found:
        first, second, year = found.groups()
        if order == "mdy":
            return _make_date(year, first, second)
        return _make_date(year, second, first)
    found = _DOTTED.match(text)
    if found:
        day, month, year = found.groups()
        return _make_date(year, month, day)
    return None


def is_placeholder(value: date, specs: Sequence[PlaceholderSpec], source_system: str) -> bool:
    """True when a spec for this source (or every source) names the date or matches its pattern."""
    text = value.isoformat()
    parts = text.split("-")
    for spec in specs:
        if spec.sources and source_system not in spec.sources:
            continue
        if spec.value is not None and spec.value == text:
            return True
        if spec.pattern is not None:
            wanted = spec.pattern.split("-")
            if len(wanted) == 3 and all(w == "*" or w == p for w, p in zip(wanted, parts, strict=True)):
                return True
    return False


def _timestamp(raw: Any) -> datetime | None:
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


# --------------------------------------------------------------------------------------------- one record


class _Builder:
    """Collects one record's display values, match forms, identifiers and problems."""

    def __init__(self, model: EntityModel, source: SourceSpec) -> None:
        self.model = model
        self.source = source
        self.calling_code = str(model.defaults.get("calling_code", "") or "")
        self.values: dict[str, Any] = {}
        self.match: dict[str, Any] = {}
        self.ids: list[RegisteredId] = []
        self.references: dict[str, str] = {}
        self.placeholders: list[str] = []
        self.invalid: list[str] = []

    def add(self, attribute: Attribute, raw: Any) -> None:
        if _empty(raw):
            return
        name = attribute.name
        if attribute.is_reference:
            key = clean_text(raw) if not isinstance(raw, (dict, list, tuple)) else None
            if key is None:
                self.invalid.append(name)
            else:
                self.references[name] = key
            return
        if attribute.repeating:
            self.group(attribute, raw)
            return
        if isinstance(raw, (dict, list, tuple, set)) and attribute.type != "json":
            self.invalid.append(name)
            return
        if attribute.type == "date" or attribute.standardise == "date":
            self.date(attribute, raw)
            return
        handler = getattr(self, f"std_{attribute.standardise}", None)
        if handler is None or attribute.standardise == "none":
            self.std_none(attribute, raw)
        else:
            handler(attribute, raw)

    # -- one per standardiser

    def std_none(self, attribute: Attribute, raw: Any) -> None:
        name = attribute.name
        value: Any
        if attribute.type == "integer":
            value = _as_int(raw)
        elif attribute.type == "number":
            value = _as_number(raw)
        elif attribute.type == "boolean":
            value = _as_bool(raw)
        elif attribute.type == "timestamp":
            value = _timestamp(raw)
        elif attribute.type == "json":
            value = raw
        else:
            value = clean_text(raw)
        if value is None:
            self.invalid.append(name)
            return
        self.values[name] = value
        if isinstance(value, (bool, int, float, str)):
            self.match[name] = value
        elif isinstance(value, datetime):
            self.match[name] = value.isoformat()
        else:
            self.match[name] = canonical_json(value)

    def std_text(self, attribute: Attribute, raw: Any) -> None:
        text = clean_text(raw)
        if text is None:
            return
        self.values[attribute.name] = text
        self.match[attribute.name] = fold(text)

    def std_person_name(self, attribute: Attribute, raw: Any) -> None:
        text = clean_text(raw)
        if text is None:
            return
        folded = fold(text)
        if not folded:
            self.invalid.append(attribute.name)
            return
        name = attribute.name
        self.values[name] = text
        self.match[name] = folded
        self.match[f"{name}.metaphone"] = phonetic(jellyfish.metaphone, folded)
        self.match[f"{name}.nysiis"] = phonetic(jellyfish.nysiis, folded)

    def std_org_name(self, attribute: Attribute, raw: Any) -> None:
        text = clean_text(raw)
        parsed = org_name(text) if text is not None else None
        if text is None or parsed is None:
            if text is not None:
                self.invalid.append(attribute.name)
            return
        name = attribute.name
        self.values[name] = text
        self.match[name] = parsed.core
        self.match[f"{name}.first"] = parsed.first
        self.match[f"{name}.first.metaphone"] = phonetic(jellyfish.metaphone, parsed.first or "")
        self.match[f"{name}.tokens"] = list(parsed.tokens)
        self.match[f"{name}.legal"] = parsed.legal

    def std_email(self, attribute: Attribute, raw: Any) -> None:
        value = email(raw)
        if value is None:
            self.invalid.append(attribute.name)
            return
        name = attribute.name
        local, domain = value.split("@")
        self.values[name] = value
        self.match[name] = value
        self.match[f"{name}.local"] = local
        self.match[f"{name}.domain"] = domain

    def std_phone(self, attribute: Attribute, raw: Any) -> None:
        value = phone_e164(raw, self.calling_code)
        if value is None:
            self.invalid.append(attribute.name)
            return
        name = attribute.name
        self.values[name] = value
        self.match[name] = value
        self.match[f"{name}.last7"] = value[-7:]

    def std_postcode(self, attribute: Attribute, raw: Any) -> None:
        value = postcode(raw)
        if value is None:
            self.invalid.append(attribute.name)
            return
        name = attribute.name
        self.values[name] = clean_text(raw)
        self.match[name] = value
        self.match[f"{name}.prefix"] = value[:3]

    def std_registered_id(self, attribute: Attribute, raw: Any) -> None:
        found = registered_id(raw, attribute.scheme or "", attribute.checksum)
        if found is None:
            self.invalid.append(attribute.name)
            return
        name = attribute.name
        self.ids.append(found)
        self.values[name] = found.value
        self.match[name] = found.value
        self.match[f"{name}.valid"] = found.valid

    def std_url(self, attribute: Attribute, raw: Any) -> None:
        value = url_domain(raw)
        if value is None:
            self.invalid.append(attribute.name)
            return
        self.values[attribute.name] = clean_text(raw)
        self.match[attribute.name] = value

    def std_code(self, attribute: Attribute, raw: Any) -> None:
        value = code(raw)
        if value is None:
            return
        self.values[attribute.name] = value
        self.match[attribute.name] = value

    def date(self, attribute: Attribute, raw: Any) -> None:
        name = attribute.name
        value = iso_date(raw, self.source.date_order)
        if value is None:
            self.invalid.append(name)
            return
        if attribute.placeholders and is_placeholder(value, attribute.placeholders, self.source.system):
            self.placeholders.append(name)
            return
        self.values[name] = value
        self.match[name] = value.isoformat()
        if attribute.standardise == "date":
            self.match[f"{name}.year"] = f"{value.year:04d}"

    def group(self, attribute: Attribute, raw: Any) -> None:
        if not isinstance(raw, (list, tuple)):
            self.invalid.append(attribute.name)
            return
        entries: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, Mapping):
                self.invalid.append(attribute.name)
                continue
            entry = {field: _clean_field(item.get(field)) for field in attribute.fields}
            if any(value is not None for value in entry.values()):
                entries.append(entry)
        if entries:
            self.values[attribute.name] = entries


def _clean_field(raw: Any) -> Any:
    if raw is None or isinstance(raw, bool) or isinstance(raw, (int, float)):
        return raw
    if isinstance(raw, (dict, list, tuple)):
        return None
    return clean_text(raw)


def _empty(raw: Any) -> bool:
    if raw is None:
        return True
    if isinstance(raw, str):
        return not raw.strip()
    if isinstance(raw, (list, tuple, dict, set)):
        return len(raw) == 0
    return False


def _as_int(raw: Any) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float) and raw.is_integer():
        return int(raw)
    if isinstance(raw, str) and re.fullmatch(r"\s*[+-]?\d+\s*", raw):
        return int(raw)
    return None


def _as_number(raw: Any) -> int | float | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return raw if raw == raw and raw not in (float("inf"), float("-inf")) else None
    if isinstance(raw, str):
        try:
            value = float(raw)
        except ValueError:
            return None
        return value if value == value and abs(value) != float("inf") else None
    return None


def _as_bool(raw: Any) -> bool | None:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        text = raw.strip().lower()
        if text in ("true", "yes", "y", "1"):
            return True
        if text in ("false", "no", "n", "0"):
            return False
    if isinstance(raw, int) and raw in (0, 1):
        return bool(raw)
    return None


def standardise_record(model: EntityModel, source: SourceSpec, change: SourceChange) -> StdRecord:
    """One landing row (an upsert) to its standardised record: values, match forms, IDs, keys, references."""
    builder = _Builder(model, source)
    payload = change.payload if isinstance(change.payload, Mapping) else {}
    for attribute in model.attributes:
        builder.add(attribute, payload.get(attribute.name))
    ids = tuple(builder.ids)
    return StdRecord(
        entity=model.entity,
        source=change.source,
        values=builder.values,
        match=builder.match,
        ids=ids,
        keys=keys_from_forms(model.match, builder.match, ids),
        references=builder.references,
        sample_hash=sample_hash(model.entity, change.source),
        placeholders=tuple(builder.placeholders),
        invalid=tuple(dict.fromkeys(builder.invalid)),
    )


def sample_hash(entity: str, source: SourceKey) -> int:
    """int.from_bytes(sha256(f"{entity}|{system}|{key}").digest()[:8], "big") >> 1: non-negative, 63 bits."""
    digest = hashlib.sha256(f"{entity}|{source.system}|{source.key}".encode()).digest()
    return int.from_bytes(digest[:8], "big") >> 1
