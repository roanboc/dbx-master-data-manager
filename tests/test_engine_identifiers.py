"""Registered identifiers and their check digits (B.9.1)."""

from __future__ import annotations

import random

import pytest

from mdm.engine.identifiers import (
    luhn_digit,
    luhn_valid,
    mod11_digit,
    mod11_valid,
    mod97_digits,
    mod97_valid,
    registered_id,
)
from mdm.models.records import RegisteredId

RNG_SEED = 11


def _bases(count: int, length: int) -> list[str]:
    rng = random.Random(RNG_SEED + length)
    return ["".join(rng.choice("0123456789") for _ in range(length)) for _ in range(count)]


def _one_digit_changed(value: str) -> list[str]:
    out = []
    for index, char in enumerate(value):
        if not char.isdigit():
            continue
        for digit in "0123456789":
            if digit != char:
                out.append(value[:index] + digit + value[index + 1 :])
    return out


def test_luhn_known_value() -> None:
    assert luhn_valid("79927398713")
    assert luhn_digit("7992739871") == "3"
    assert not luhn_valid("79927398710")


@pytest.mark.parametrize("base", _bases(40, 8))
def test_luhn_round_trip_and_single_digit_errors(base: str) -> None:
    value = base + luhn_digit(base)
    assert luhn_valid(value)
    assert not any(luhn_valid(wrong) for wrong in _one_digit_changed(value))


def test_luhn_refuses_what_is_not_digits() -> None:
    assert not luhn_valid("")
    assert not luhn_valid("7")
    assert not luhn_valid("79927398A13")
    assert not luhn_valid("７９９２７３９８７１３")  # full-width digits are not ASCII digits
    with pytest.raises(ValueError):
        luhn_digit("12A")


@pytest.mark.parametrize("base", [*_bases(30, 8), "AB1234", "XY99ZZ00"])
def test_mod97_round_trip_and_single_digit_errors(base: str) -> None:
    value = base + mod97_digits(base)
    assert len(value) == len(base) + 2
    assert mod97_valid(value)
    assert not any(mod97_valid(wrong) for wrong in _one_digit_changed(value))


def test_mod97_letters_count_as_two_digits() -> None:
    # A=10: "A" + digits reads as "10" + digits
    assert mod97_digits("A1") == mod97_digits("101")
    assert not mod97_valid("ab12")  # lower case is not normalised here
    assert not mod97_valid("1")
    with pytest.raises(ValueError):
        mod97_digits("")


def test_mod97_digits_are_zero_padded() -> None:
    padded = [base for base in _bases(400, 6) if mod97_digits(base).startswith("0")]
    assert padded, "some base gives a check value below 10"
    assert all(len(mod97_digits(base)) == 2 for base in padded)


def test_mod11_round_trip_and_the_base_without_a_digit() -> None:
    bases = _bases(200, 8)
    without = [base for base in bases if mod11_digit(base) is None]
    assert without, "about one base in eleven has no mod-11 digit"
    for base in bases:
        digit = mod11_digit(base)
        if digit is None:
            assert not any(mod11_valid(base + d) for d in "0123456789")
            continue
        value = base + digit
        assert mod11_valid(value)
        assert not any(mod11_valid(wrong) for wrong in _one_digit_changed(value))


def test_mod11_weights_from_the_right() -> None:
    # 1·7 + 2·6 + 3·5 + 4·4 + 5·3 + 6·2 = 77 = 7·11 -> 11 - 0 = 11 -> "0"
    assert mod11_digit("123456") == "0"
    assert mod11_valid("1234560")
    # weights cycle back to 2 after 7: the seventh digit from the right weighs 2
    assert mod11_digit("1000000") == str(11 - 2)


def test_registered_id_normalises_and_checks() -> None:
    value = "12345678" + luhn_digit("12345678")
    spaced = f" {value[:3]} {value[3:6]}-{value[6:]}. "
    assert registered_id(spaced, "PERSON_REF", "luhn") == RegisteredId("PERSON_REF", value, True)
    assert registered_id("ab-12.cd", "ORG_REG", None) == RegisteredId("ORG_REG", "AB12CD", None)
    assert (
        registered_id(value[:-1] + "0" if value[-1] != "0" else value[:-1] + "1", "PERSON_REF", "luhn").valid
        is False
    )
    assert registered_id(" - . ", "PERSON_REF", "luhn") is None
    assert registered_id(None, "PERSON_REF", "luhn") is None
    assert registered_id(123456782, "PERSON_REF", "luhn").value == "123456782"
    with pytest.raises(ValueError):
        registered_id("123", "X", "crc32")
