"""Comparators: levels, thresholds and nulls (B.9.3)."""

from __future__ import annotations

import random
from typing import Any

import pytest

from mdm.engine.compare import compare, exact_form, level_count, level_label, level_labels
from mdm.models.entity_model import COMPARATOR_LEVELS, COMPARATORS, ComparisonSpec
from mdm.models.match import LEVEL_NULL


def spec(comparator: str, thresholds: tuple[float, ...] = ()) -> ComparisonSpec:
    return ComparisonSpec(name="c", attribute="a", comparator=comparator, thresholds=thresholds)


def forms(value: Any, **extra: Any) -> dict[str, Any]:
    return {"a": value, **{f"a.{k.replace('__', '.')}": v for k, v in extra.items()}}


@pytest.mark.parametrize("comparator", COMPARATORS)
def test_levels_and_labels(comparator: str) -> None:
    s = spec(comparator)
    assert level_count(s) == COMPARATOR_LEVELS[comparator]
    labels = level_labels(s)
    assert len(labels) == level_count(s)
    assert labels[0] == "exact" and labels[-1] == "else"
    assert level_label(s, LEVEL_NULL) == "null"
    assert exact_form(s) == "a"


@pytest.mark.parametrize("comparator", COMPARATORS)
@pytest.mark.parametrize("missing", [None, "", [], "absent"])
def test_a_missing_side_is_null(comparator: str, missing: Any) -> None:
    present = forms(
        "x", valid=True, metaphone="X", nysiis="X", last7="1234567", prefix="X", local="x", domain="d"
    )
    empty: dict[str, Any] = {} if missing == "absent" else {"a": missing}
    assert compare(spec(comparator), present, empty) == LEVEL_NULL
    assert compare(spec(comparator), empty, present) == LEVEL_NULL
    assert compare(spec(comparator), empty, empty) == LEVEL_NULL


@pytest.mark.parametrize("comparator", COMPARATORS)
def test_equal_forms_are_level_zero(comparator: str) -> None:
    left = forms("same", valid=True)
    right = forms("same", valid=True)
    assert compare(spec(comparator), left, right) == 0


def test_exact() -> None:
    assert compare(spec("exact"), forms("a"), forms("b")) == 1
    assert compare(spec("exact"), forms(7), forms(7)) == 0


def test_name_thresholds_and_override() -> None:
    s = spec("name")
    assert compare(s, forms("martha"), forms("marhta")) == 1  # JW 0.961
    assert compare(s, forms("jonathon"), forms("jonathan")) == 1  # JW 0.95, the edge is inclusive
    assert compare(s, forms("katherine"), forms("kathryn")) == 2  # JW 0.905
    assert compare(s, forms("dwayne"), forms("duane")) == 3  # JW 0.84
    assert compare(s, forms("kestrel"), forms("umber")) == 3
    loose = spec("name", (0.80, 0.50))
    assert compare(loose, forms("dwayne"), forms("duane")) == 1


def test_name_levels_follow_jaro_winkler() -> None:
    from rapidfuzz.distance import JaroWinkler

    s = spec("name")
    rng = random.Random(5)
    for _ in range(300):
        x = "".join(rng.choice("abcde") for _ in range(rng.randint(3, 7)))
        y = "".join(rng.choice("abcde") for _ in range(rng.randint(3, 7)))
        level = compare(s, forms(x), forms(y))
        jw = JaroWinkler.similarity(x, y)
        expected = 0 if x == y else 1 if jw >= 0.95 else 2 if jw >= 0.88 else 3
        assert level == expected


def test_phonetic_name() -> None:
    s = spec("phonetic_name")
    same_sound = compare(
        s, forms("smyth", metaphone="SM0", nysiis="SNYT"), forms("smith", metaphone="SM0", nysiis="SNAT")
    )
    assert same_sound == 1
    nysiis_only = compare(s, forms("abc", metaphone="A", nysiis="N"), forms("abd", metaphone="B", nysiis="N"))
    assert nysiis_only == 1
    close = compare(
        s, forms("katherine", metaphone="K1", nysiis="K1"), forms("katherina", metaphone="K2", nysiis="K2")
    )
    assert close == 2
    far = compare(s, forms("kestrel", metaphone="K", nysiis="K"), forms("umber", metaphone="U", nysiis="U"))
    assert far == 3
    # an empty phonetic code never counts as agreement
    blank = compare(s, forms("xq", metaphone="", nysiis=None), forms("zv", metaphone="", nysiis=None))
    assert blank == 3


def test_levenshtein() -> None:
    s = spec("levenshtein")
    assert compare(s, forms("abcdef"), forms("abcdeg")) == 1
    assert compare(s, forms("abcdef"), forms("abcdxy")) == 2
    assert compare(s, forms("abcdef"), forms("uvwxyz")) == 3
    assert compare(spec("levenshtein", (0.0, 3.0)), forms("abcdef"), forms("abcdeg")) == 2


def test_token_set() -> None:
    s = spec("token_set")
    assert compare(s, forms("brindle sons"), forms("sons brindle")) == 1
    assert compare(s, forms("brindle sons holdings"), forms("brindle holdings")) == 1  # subset: ratio 100
    assert compare(s, forms("brindle holdings"), forms("brindel holding")) == 1  # ratio 0.903
    assert compare(s, forms("brindle holdings group"), forms("brindle holdings trust")) == 2  # 0.864
    assert compare(s, forms("brindle holdings"), forms("brindle works")) == 3  # 0.70
    assert compare(s, forms("brindle holdings"), forms("quarrow works")) == 3


@pytest.mark.parametrize(
    ("left", "right", "level"),
    [
        ("1990-05-17", "1990-05-17", 0),
        ("1990-05-07", "1990-07-05", 1),  # day and month swapped
        ("1990-05-17", "1990-05-18", 1),  # one digit
        ("1990-05-17", "1991-05-17", 1),  # one digit, in the year
        ("1990-05-17", "1990-11-30", 2),
        ("1990-05-17", "1984-11-30", 3),
    ],
)
def test_date(left: str, right: str, level: int) -> None:
    s = spec("date")
    assert compare(s, forms(left, year=left[:4]), forms(right, year=right[:4])) == level
    assert compare(s, forms(right), forms(left)) == level  # the year form is optional


@pytest.mark.parametrize(
    ("left_valid", "right_valid", "same", "level"),
    [
        (True, True, True, 0),
        (True, False, True, 1),
        (None, True, True, 1),
        (None, None, True, 1),
        (True, True, False, 2),
        (False, False, False, 2),
    ],
)
def test_identifier(left_valid: bool | None, right_valid: bool | None, same: bool, level: int) -> None:
    right_value = "123456782" if same else "987654321"
    s = spec("identifier")
    assert compare(s, forms("123456782", valid=left_valid), forms(right_value, valid=right_valid)) == level


def test_phone() -> None:
    s = spec("phone")
    assert compare(s, forms("+99912345678", last7="2345678"), forms("+99912345678", last7="2345678")) == 0
    assert compare(s, forms("+99912345678", last7="2345678"), forms("+99812345678", last7="2345678")) == 1
    assert compare(s, forms("+99912345678"), forms("+99812345678")) == 1  # last 7 derived when absent
    assert compare(s, forms("+99912345678", last7="2345678"), forms("+99912345670", last7="2345670")) == 2


def test_email() -> None:
    s = spec("email")

    def e(text: str) -> dict[str, Any]:
        local, domain = text.split("@")
        return forms(text, local=local, domain=domain)

    assert compare(s, e("tam.quell@example.org"), e("tam.quell@example.com")) == 1
    assert compare(s, e("tam.quell1@example.org"), e("tam.quell2@example.org")) == 1
    assert compare(s, e("tam.quell1@example.org"), e("tam.quell2@example.com")) == 2
    assert compare(s, e("tam@example.org"), e("rook@example.org")) == 2
    assert compare(spec("email", (0.999,)), e("tam.quell1@example.org"), e("tam.quell2@example.org")) == 2


def test_postcode() -> None:
    s = spec("postcode")
    assert compare(s, forms("AB12CD", prefix="AB1"), forms("AB19ZZ", prefix="AB1")) == 1
    assert compare(s, forms("AB12CD"), forms("AB19ZZ")) == 1
    assert compare(s, forms("AB12CD", prefix="AB1"), forms("ZZ12CD", prefix="ZZ1")) == 2


@pytest.mark.parametrize("comparator", COMPARATORS)
def test_symmetric(comparator: str) -> None:
    """compare(a, b) == compare(b, a) for every comparator, on random forms."""
    rng = random.Random(17)
    s = spec(comparator)
    alphabet = "ab1-"
    for _ in range(200):
        values = []
        for _side in range(2):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 5)))
            values.append(
                forms(
                    text,
                    valid=rng.choice([True, False, None]),
                    metaphone=text[:2],
                    nysiis=text[-2:],
                    last7=text[-3:],
                    prefix=text[:1],
                    local=text[:3],
                    domain=text[3:] or "d",
                    year=text[:1],
                )
            )
        assert compare(s, values[0], values[1]) == compare(s, values[1], values[0])
