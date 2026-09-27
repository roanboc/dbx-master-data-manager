"""Invented vocabulary for the demo world (owner: CLI, B.14).

Invented syllables and words only: given-name syllables, family prefixes and
suffixes, organisation stems, sector words (Holdings, Works, Supplies, Labs,
Foundry), generic legal forms, invented cities and streets. Names are drawn with
a Zipf distribution (exponent about 1.1 over the generated vocabulary), so
common family names form large blocks as real ones do. Run the public-safety
scan with the private denylist over this file; reword any hit.

Each vocabulary is built once, in a fixed order, then shuffled with a fixed
seed, so the most frequent name is not simply the first syllable pair; the
same vocabulary comes back in every run.
"""

from __future__ import annotations

import random
from bisect import bisect_right
from collections.abc import Sequence
from functools import lru_cache
from itertools import accumulate, product
from typing import TypeVar

T = TypeVar("T")
ZIPF_EXPONENT = 1.1
#: the seed that fixes the order of every vocabulary (never the world's seed)
_VOCABULARY_SEED = 20260927

_GIVEN_FIRST = (
    "Al", "Bri", "Cae", "Dar", "Ely", "Fen", "Gil", "Hal", "Isa", "Jor",
    "Kae", "Lio", "Mai", "Nel", "Ori", "Per", "Qui", "Ros", "Sel", "Tev",
    "Ula", "Vei", "Wen", "Xan", "Yor", "Zel", "Ari", "Bel", "Cor", "Dal",
    "Eir", "Fal", "Gwe", "Hed", "Ilo", "Jas", "Kir", "Lum", "Mer", "Nim",
)  # fmt: skip
_GIVEN_SECOND = (
    "a", "an", "ena", "is", "o", "wyn", "ric", "ia", "el", "ette",
    "ius", "ara", "on", "ine", "ey", "orn", "as", "yn", "ith", "ada",
)  # fmt: skip
_FAMILY_PREFIX = (
    "Ash", "Brom", "Cald", "Dun", "Ever", "Fenn", "Garr", "Holl", "Ing", "Kest",
    "Lang", "Morr", "Nord", "Orm", "Pell", "Quill", "Radd", "Stav", "Thorn", "Ull",
    "Vard", "Wex", "Yarr", "Zell", "Bryn", "Corr", "Dask", "Elm", "Frost", "Glen",
    "Hask", "Jarv", "Kell", "Lorr", "Marl", "Nesb", "Ostr", "Pryd", "Rusk", "Selw",
)  # fmt: skip
_FAMILY_SUFFIX = (
    "ley", "wick", "ton", "field", "more", "by", "dale", "worth", "stead", "holm",
    "ridge", "brook", "ard", "ov", "sen", "ini", "ez", "ian", "ach", "er",
    "ow", "ling", "mont", "berg", "ell", "is", "ane", "ock", "ett", "an",
)  # fmt: skip
_ORG_FIRST = (
    "Vel", "Quor", "Bras", "Mond", "Tess", "Kal", "Dru", "Fenn", "Gorm", "Hal",
    "Jast", "Lum", "Nex", "Orv", "Pry", "Rast", "Sorn", "Tav", "Umb", "Wyn",
    "Xan", "Yst", "Zor", "Crel", "Drav", "Esk", "Frim", "Glav", "Hurn", "Irv",
)  # fmt: skip
_ORG_SECOND = (
    "tara", "ix", "tel", "rel", "aly", "vor", "ane", "ico", "ora", "une",
    "eth", "ium", "ova", "ant", "ek", "ar", "is", "on", "yx", "ette",
)  # fmt: skip
SECTORS = (
    "Holdings", "Works", "Supplies", "Labs", "Foundry", "Logistics", "Textiles", "Systems",
    "Partners", "Instruments", "Kitchens", "Freight", "Printing", "Dairy", "Timber",
)  # fmt: skip
#: generic legal forms: (the short form, the long form a second source may write instead)
LEGAL_FORMS = (
    ("Ltd", "Limited"),
    ("Inc", "Incorporated"),
    ("Corp", "Corporation"),
    ("Co", "Company"),
    ("LLC", "LLC"),
    ("PLC", "PLC"),
)
_CITY_STEM = (
    "Quel", "Varn", "Ostr", "Tamb", "Zind", "Kelv", "Brisk", "Morv", "Ellan", "Fard",
    "Grel", "Hov", "Istr", "Jund", "Lask", "Nerr", "Pold", "Rimm", "Sval", "Tors",
)  # fmt: skip
_CITY_END = ("mouth", "holt", "stead", "vale", "ford", "by", "haven", "moor", "wick", "fell")
_STREET_STEM = (
    "Alder", "Birch", "Cinder", "Dove", "Elder", "Flint", "Garnet", "Heron", "Juniper", "Kestrel",
    "Linden", "Maple", "Nettle", "Oriel", "Plover", "Quarry", "Rowan", "Sorrel", "Teasel", "Umber",
    "Vetch", "Willow", "Yarrow", "Zephyr",
)  # fmt: skip
_STREET_KIND = ("Road", "Lane", "Row", "Way", "Close", "Yard", "Walk", "Rise", "Court", "Terrace")
_VOWELS = "aeiouy"


def _vocabulary(parts: Sequence[Sequence[str]]) -> tuple[str, ...]:
    """Every combination of the parts, deduplicated, in a fixed shuffled order."""
    words = sorted({"".join(combo) for combo in product(*parts)})
    random.Random(_VOCABULARY_SEED).shuffle(words)
    return tuple(words)


GIVEN_NAMES = _vocabulary((_GIVEN_FIRST, _GIVEN_SECOND))
FAMILY_NAMES = _vocabulary((_FAMILY_PREFIX, _FAMILY_SUFFIX))
ORG_STEMS = _vocabulary((_ORG_FIRST, _ORG_SECOND))
CITIES = _vocabulary((_CITY_STEM, _CITY_END))
STREETS = _vocabulary((_STREET_STEM, (" ",), _STREET_KIND))


@lru_cache(maxsize=32)
def _cumulative(size: int, exponent: float) -> tuple[float, ...]:
    return tuple(accumulate(1.0 / (rank**exponent) for rank in range(1, size + 1)))


def zipf_choice(rng: random.Random, items: Sequence[T], exponent: float = ZIPF_EXPONENT) -> T:
    """One item, the k-th (from 1) with weight 1 / k**exponent."""
    if not items:
        raise ValueError("no items to choose from")
    cumulative = _cumulative(len(items), exponent)
    index = bisect_right(cumulative, rng.random() * cumulative[-1])
    return items[min(index, len(items) - 1)]


def given_name(rng: random.Random) -> str:
    return zipf_choice(rng, GIVEN_NAMES)


def family_name(rng: random.Random) -> str:
    return zipf_choice(rng, FAMILY_NAMES)


def organisation_name(rng: random.Random) -> tuple[str, str]:
    """(stem and sector words, legal form): e.g. ("Quorix Foundry", "Ltd") or ("Velane & Tessora", "Inc")."""
    stem = zipf_choice(rng, ORG_STEMS)
    if rng.random() < 0.2:
        other = zipf_choice(rng, ORG_STEMS)
        while other == stem:
            other = zipf_choice(rng, ORG_STEMS)
        words = f"{stem} & {other}"
    else:
        words = f"{stem} {rng.choice(SECTORS)}"
    legal, _ = LEGAL_FORMS[min(int(rng.random() ** 2 * len(LEGAL_FORMS)), len(LEGAL_FORMS) - 1)]
    return words, legal


def long_legal_form(short: str) -> str:
    """The long form of a legal form ("Ltd" -> "Limited"); the form itself when it has none."""
    for form, long in LEGAL_FORMS:
        if form == short:
            return long
    return short


def city(rng: random.Random) -> str:
    return zipf_choice(rng, CITIES)


def street(rng: random.Random) -> str:
    return rng.choice(STREETS)


def typo(rng: random.Random, text: str) -> str:
    """One keying error, never on the first letter: two neighbours swapped, a letter dropped, or a vowel changed.

    Returns a different text whenever the word allows it (a single letter stays as it is).
    """
    if len(text) < 3:
        return text
    for _ in range(8):
        kind = rng.randrange(3)
        at = rng.randrange(1, len(text) - 1)
        if kind == 0:
            changed = text[:at] + text[at + 1] + text[at] + text[at + 2 :]
        elif kind == 1:
            changed = text[:at] + text[at + 1 :]
        else:
            vowels = [i for i in range(1, len(text)) if text[i].lower() in _VOWELS]
            if not vowels:
                continue
            at = rng.choice(vowels)
            replacement = rng.choice([v for v in _VOWELS if v != text[at].lower()])
            changed = text[:at] + replacement + text[at + 1 :]
        if changed != text:
            return changed
    return text


def slug(text: str) -> str:
    """Lower-case letters and digits joined by hyphens: "Quorix & Velane" -> "quorix-velane"."""
    out: list[str] = []
    word: list[str] = []
    for char in text.lower():
        if char.isascii() and char.isalnum():
            word.append(char)
        elif word:
            out.append("".join(word))
            word = []
    if word:
        out.append("".join(word))
    return "-".join(out)
