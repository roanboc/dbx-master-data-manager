"""Comparators: the level of agreement of one attribute between two records (owner: ENGINE, B.9.3).

Levels are indexed from 0 (strongest); `LEVEL_NULL` (-1) when either side is
missing. Default thresholds (`mdm.models.entity_model.COMPARATOR_THRESHOLDS`),
overridable by `ComparisonSpec.thresholds`:

| Comparator    | Levels (non-null)                                                                  |
| ------------- | ---------------------------------------------------------------------------------- |
| exact         | 0 equal · 1 else                                                                   |
| name          | 0 equal · 1 Jaro-Winkler >= 0.95 · 2 >= 0.88 · 3 else                              |
| phonetic_name | 0 equal · 1 a.metaphone or a.nysiis equal · 2 Jaro-Winkler >= 0.88 · 3 else        |
| levenshtein   | 0 equal · 1 distance <= 1 · 2 <= 2 · 3 else                                        |
| token_set     | 0 a (core) equal · 1 token-set ratio >= 0.90 · 2 >= 0.75 · 3 else                  |
| date          | 0 equal · 1 day and month swapped, or one digit differs · 2 same year · 3 else     |
| identifier    | 0 equal and both valid · 1 equal, a checksum missing or failing · 2 else           |
| phone         | 0 E.164 equal · 1 last 7 equal · 2 else                                            |
| email         | 0 equal · 1 local part equal, or local JW >= 0.92 with the same domain · 2 else    |
| postcode      | 0 equal · 1 same prefix · 2 else                                                   |

Level 0 of every comparator is equality of a form (`exact_form`), which
estimation uses. A missing side is a form absent, None, empty text or an empty
list. Both records of a comparison come from one entity model, so the two sides
of an identifier comparison always share its scheme. Pure.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import lru_cache
from typing import Any

from rapidfuzz.distance import JaroWinkler, Levenshtein
from rapidfuzz.fuzz import token_set_ratio

from mdm.models.entity_model import COMPARATOR_LEVELS, COMPARATOR_THRESHOLDS, ComparisonSpec
from mdm.models.match import LEVEL_NULL

#: a compiled comparator: (left match forms, right match forms) -> level
Comparer = Callable[[Mapping[str, Any], Mapping[str, Any]], int]


def level_count(spec: ComparisonSpec) -> int:
    """Non-null levels (`COMPARATOR_LEVELS[spec.comparator]`)."""
    return COMPARATOR_LEVELS[spec.comparator]


def thresholds(spec: ComparisonSpec) -> tuple[float, ...]:
    """The spec's thresholds, else the comparator's defaults."""
    return spec.thresholds or COMPARATOR_THRESHOLDS[spec.comparator]


def _fmt(value: float) -> str:
    return f"{value:.2f}"


def level_labels(spec: ComparisonSpec) -> tuple[str, ...]:
    """One label per level: `"exact"`, `"jw>=0.95"`, …, `"else"`."""
    t = thresholds(spec)
    labels: dict[str, tuple[str, ...]] = {
        "exact": ("exact", "else"),
        "name": ("exact", *(f"jw>={_fmt(v)}" for v in t), "else"),
        "phonetic_name": ("exact", "phonetic", *(f"jw>={_fmt(v)}" for v in t), "else"),
        "levenshtein": ("exact", *(f"lev<={int(v)}" for v in t), "else"),
        "token_set": ("exact", *(f"tokens>={_fmt(v)}" for v in t), "else"),
        "date": ("exact", "swap_or_digit", "same_year", "else"),
        "identifier": ("exact", "equal_unchecked", "else"),
        "phone": ("exact", "last7", "else"),
        "email": ("exact", "local_part", "else"),
        "postcode": ("exact", "prefix", "else"),
    }
    return labels[spec.comparator]


def level_label(spec: ComparisonSpec, level: int) -> str:
    """The label of one level; `"null"` for LEVEL_NULL."""
    return "null" if level == LEVEL_NULL else level_labels(spec)[level]


def exact_form(spec: ComparisonSpec) -> str:
    """The form whose equality is level 0."""
    return spec.attribute


def _missing(value: Any) -> bool:
    return value is None or value == "" or (isinstance(value, (list, tuple)) and not value)


def _date_near(left: str, right: str) -> bool:
    """Day and month swapped, or one digit different (Levenshtein 1 on the ISO text)."""
    if len(left) == 10 and len(right) == 10 and left[:4] == right[:4]:
        if left[5:7] == right[8:10] and left[8:10] == right[5:7]:
            return True
    return Levenshtein.distance(left, right) == 1


def _make(spec: ComparisonSpec) -> Comparer:
    a = spec.attribute
    kind = spec.comparator
    t = thresholds(spec)

    if kind == "exact":

        def exact(left: Mapping[str, Any], right: Mapping[str, Any]) -> int:
            x, y = left.get(a), right.get(a)
            if _missing(x) or _missing(y):
                return LEVEL_NULL
            return 0 if x == y else 1

        return exact

    if kind == "name":
        hi, lo = t

        def name(left: Mapping[str, Any], right: Mapping[str, Any]) -> int:
            x, y = left.get(a), right.get(a)
            if _missing(x) or _missing(y):
                return LEVEL_NULL
            if x == y:
                return 0
            similarity = JaroWinkler.similarity(str(x), str(y))
            return 1 if similarity >= hi else 2 if similarity >= lo else 3

        return name

    if kind == "phonetic_name":
        (lo,) = t
        a_metaphone, a_nysiis = f"{a}.metaphone", f"{a}.nysiis"

        def phonetic_name(left: Mapping[str, Any], right: Mapping[str, Any]) -> int:
            x, y = left.get(a), right.get(a)
            if _missing(x) or _missing(y):
                return LEVEL_NULL
            if x == y:
                return 0
            m1, m2 = left.get(a_metaphone), right.get(a_metaphone)
            n1, n2 = left.get(a_nysiis), right.get(a_nysiis)
            if (m1 and m1 == m2) or (n1 and n1 == n2):
                return 1
            return 2 if JaroWinkler.similarity(str(x), str(y)) >= lo else 3

        return phonetic_name

    if kind == "levenshtein":
        near, far = t

        def levenshtein(left: Mapping[str, Any], right: Mapping[str, Any]) -> int:
            x, y = left.get(a), right.get(a)
            if _missing(x) or _missing(y):
                return LEVEL_NULL
            if x == y:
                return 0
            distance = Levenshtein.distance(str(x), str(y))
            return 1 if distance <= near else 2 if distance <= far else 3

        return levenshtein

    if kind == "token_set":
        hi, lo = t

        def token_set(left: Mapping[str, Any], right: Mapping[str, Any]) -> int:
            x, y = left.get(a), right.get(a)
            if _missing(x) or _missing(y):
                return LEVEL_NULL
            if x == y:
                return 0
            ratio = token_set_ratio(str(x), str(y)) / 100.0
            return 1 if ratio >= hi else 2 if ratio >= lo else 3

        return token_set

    if kind == "date":
        a_year = f"{a}.year"

        def date(left: Mapping[str, Any], right: Mapping[str, Any]) -> int:
            x, y = left.get(a), right.get(a)
            if _missing(x) or _missing(y):
                return LEVEL_NULL
            if x == y:
                return 0
            x, y = str(x), str(y)
            if _date_near(x, y):
                return 1
            year_x = left.get(a_year) or x[:4]
            year_y = right.get(a_year) or y[:4]
            return 2 if year_x == year_y else 3

        return date

    if kind == "identifier":
        a_valid = f"{a}.valid"

        def identifier(left: Mapping[str, Any], right: Mapping[str, Any]) -> int:
            x, y = left.get(a), right.get(a)
            if _missing(x) or _missing(y):
                return LEVEL_NULL
            if x != y:
                return 2
            return 0 if left.get(a_valid) is True and right.get(a_valid) is True else 1

        return identifier

    if kind == "phone":
        a_last7 = f"{a}.last7"

        def phone(left: Mapping[str, Any], right: Mapping[str, Any]) -> int:
            x, y = left.get(a), right.get(a)
            if _missing(x) or _missing(y):
                return LEVEL_NULL
            if x == y:
                return 0
            last_x = left.get(a_last7) or str(x)[-7:]
            last_y = right.get(a_last7) or str(y)[-7:]
            return 1 if last_x == last_y else 2

        return phone

    if kind == "email":
        (lo,) = t
        a_local, a_domain = f"{a}.local", f"{a}.domain"

        def email(left: Mapping[str, Any], right: Mapping[str, Any]) -> int:
            x, y = left.get(a), right.get(a)
            if _missing(x) or _missing(y):
                return LEVEL_NULL
            if x == y:
                return 0
            local_x, _, domain_x = str(x).partition("@")
            local_y, _, domain_y = str(y).partition("@")
            local_x = left.get(a_local) or local_x
            local_y = right.get(a_local) or local_y
            domain_x = left.get(a_domain) or domain_x
            domain_y = right.get(a_domain) or domain_y
            if local_x == local_y:
                return 1
            if domain_x == domain_y and JaroWinkler.similarity(local_x, local_y) >= lo:
                return 1
            return 2

        return email

    if kind == "postcode":
        a_prefix = f"{a}.prefix"

        def postcode(left: Mapping[str, Any], right: Mapping[str, Any]) -> int:
            x, y = left.get(a), right.get(a)
            if _missing(x) or _missing(y):
                return LEVEL_NULL
            if x == y:
                return 0
            prefix_x = left.get(a_prefix) or str(x)[:3]
            prefix_y = right.get(a_prefix) or str(y)[:3]
            return 1 if prefix_x == prefix_y else 2

        return postcode

    raise ValueError("unknown comparator")


@lru_cache(maxsize=512)
def comparer(spec: ComparisonSpec) -> Comparer:
    """The comparison compiled once per spec: a function of the two records' match forms."""
    return _make(spec)


def compare(spec: ComparisonSpec, left: Mapping[str, Any], right: Mapping[str, Any]) -> int:
    """The level of agreement between two records' match forms, or LEVEL_NULL."""
    return comparer(spec)(left, right)
