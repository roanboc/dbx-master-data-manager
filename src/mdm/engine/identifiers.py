"""Registered identifiers and their check digits (owner: ENGINE, B.9.1). Pure.

Three checksums, each with a `…_valid` test and the digit(s) that make a base
valid, so the demo generator and the tests build identifiers the same way the
engine checks them:

- `luhn`: the Luhn mod-10 digit (digits only);
- `mod97`: ISO 7064 MOD 97-10, letters A=10 … Z=35, two check digits appended;
- `mod11`: weights 2..7 from the right, cycling; a remainder giving 10 has no
  digit, so such a base cannot carry a valid identifier.
"""

from __future__ import annotations

import re
from typing import Any

from mdm.models.records import RegisteredId

_STRIP = re.compile(r"[\s.\-]+")
_ALNUM = re.compile(r"^[0-9A-Z]+$")


def _digits_only(text: str) -> bool:
    return bool(text) and text.isascii() and text.isdigit()


def _luhn_sum(digits: str, double_first: bool) -> int:
    """The Luhn sum of `digits` read from the right; `double_first` doubles the rightmost digit."""
    total = 0
    double = double_first
    for char in reversed(digits):
        value = ord(char) - 48
        if double:
            value *= 2
            if value > 9:
                value -= 9
        total += value
        double = not double
    return total


def luhn_valid(digits: str) -> bool:
    """True when `digits` (at least two, digits only) ends in its Luhn check digit."""
    if not isinstance(digits, str) or len(digits) < 2 or not _digits_only(digits):
        return False
    return _luhn_sum(digits, double_first=False) % 10 == 0


def luhn_digit(base: str) -> str:
    """The Luhn check digit to append to `base`."""
    if not _digits_only(base):
        raise ValueError("luhn needs digits")
    return str((10 - _luhn_sum(base, double_first=True) % 10) % 10)


def _mod97_number(text: str) -> int:
    """The text as the integer ISO 7064 reads: digits as they are, letters A=10 … Z=35."""
    return int("".join(str(int(char, 36)) for char in text))


def mod97_valid(value: str) -> bool:
    """ISO 7064 MOD 97-10: letters A=10…Z=35, int(value) % 97 == 1."""
    if not isinstance(value, str) or len(value) < 3 or not _ALNUM.match(value):
        return False
    return _mod97_number(value) % 97 == 1


def mod97_digits(base: str) -> str:
    """The two check digits to append to `base`: 98 - int(base + "00") % 97, zero-padded."""
    if not base or not _ALNUM.match(base):
        raise ValueError("mod97 needs digits or upper-case letters")
    return f"{98 - _mod97_number(base + '00') % 97:02d}"


def mod11_digit(base: str) -> str | None:
    """Weights 2..7 from the right; a remainder giving 10 has no digit (None)."""
    if not _digits_only(base):
        raise ValueError("mod11 needs digits")
    total = sum((ord(char) - 48) * (2 + index % 6) for index, char in enumerate(reversed(base)))
    check = 11 - total % 11
    if check == 11:
        return "0"
    if check == 10:
        return None
    return str(check)


def mod11_valid(value: str) -> bool:
    """True when the last digit of `value` (at least two digits) is the mod-11 digit of the rest."""
    if not isinstance(value, str) or len(value) < 2 or not _digits_only(value):
        return False
    return mod11_digit(value[:-1]) == value[-1]


_CHECKS = {"luhn": luhn_valid, "mod97": mod97_valid, "mod11": mod11_valid}


def normalise_id(raw: Any) -> str | None:
    """Spaces, dots and hyphens removed, upper case; None when nothing is left."""
    if raw is None or isinstance(raw, (bool, dict, list, tuple)):
        return None
    text = _STRIP.sub("", str(raw)).upper()
    return text or None


def registered_id(raw: Any, scheme: str, checksum: str | None) -> RegisteredId | None:
    """Spaces, dots and hyphens removed; upper case; valid = the checksum's verdict, None when none is configured."""
    value = normalise_id(raw)
    if value is None:
        return None
    if checksum is None:
        return RegisteredId(scheme=scheme, value=value, valid=None)
    check = _CHECKS.get(checksum)
    if check is None:
        raise ValueError("unknown checksum")
    return RegisteredId(scheme=scheme, value=value, valid=check(value))
