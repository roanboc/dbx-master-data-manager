"""How a match explanation and a golden value's provenance read in words: one answer for every reader.

The assistant's stub (`mdm.agent.provider`) and the workbench's services use these sentences, so a score,
a level, a counterfactual or a hard rule reads the same wherever it is shown (principle `P7`). Built from
comparison names, level labels, band names, scores and strategy codes only: never a value. Pure.
"""

from __future__ import annotations

import re

from mdm.models.match import Band, Contribution, Counterfactual

BAND_WORDS = {Band.AUTO: "automatic", Band.REVIEW: "review", Band.DISTINCT: "distinct"}
#: how a level reads after a comparison's name: "family name exact", "birth date in the same year"
LEVEL_WORDS = {
    "exact": "exact",
    "else": "differs",
    "null": "missing",
    "phonetic": "sounding alike",
    "swap_or_digit": "with day and month swapped or one digit different",
    "same_year": "in the same year",
    "equal_unchecked": "equal without a valid checksum",
    "last7": "equal in the last seven digits",
    "local_part": "alike in the local part",
    "prefix": "sharing a prefix",
}
#: how a strategy that decided a golden value reads: "finance won on source trust"
STRATEGY_WORDS = {
    "source_trust": "source trust",
    "recency": "recency",
    "completeness": "completeness",
    "frequency": "frequency",
    "pin": "a steward's pin",
    "only": "the only value",
    "keyed_union": "entries combined by key",
    "tie_break": "the source key order",
}
_THRESHOLD_LABEL = re.compile(r"^(jw|lev|tokens)(>=|<=)([0-9.]+)\Z")
_NAME_WORDS = {"id": "ID", "ref": "reference", "reg": "registration"}
_MINUS = "−"


def score_text(score: float) -> str:
    """A whole-number score; a score short of certainty never reads 100."""
    shown = round(score)
    if shown >= 100 and score < 100:
        shown = 99
    return str(int(shown))


def signed(weight: float) -> str:
    """A weight with its sign and one decimal: "+8.7", "−1.4"."""
    return f"{weight:+.1f}".replace("-", _MINUS)


def comparison_name(comparison: str) -> str:
    """`person_ref` -> "person reference", `registered_id` -> "registered ID"."""
    return " ".join(_NAME_WORDS.get(part, part) for part in comparison.split("_") if part)


def attribute_label(name: str) -> str:
    """An attribute's name as a label: `registered_id` -> "Registered ID", `person_ref` -> "Person reference"."""
    words = comparison_name(name)
    return words[:1].upper() + words[1:]


def level_words(label: str) -> str:
    """A level label in words: "exact", "differs", "similar (Jaro-Winkler at least 0.9)"."""
    if label in LEVEL_WORDS:
        return LEVEL_WORDS[label]
    found = _THRESHOLD_LABEL.match(label)
    if found is None:
        return label
    measure, _relation, value = found.groups()
    if measure == "jw":
        return f"similar (Jaro-Winkler at least {value})"
    if measure == "lev":
        return f"within {value} edit{'' if value == '1' else 's'}"
    return f"sharing most words (token-set ratio at least {value})"


def agreement_of(label: str) -> str:
    """How a comparison level colours the compare table: "exact" -> agree, "else" -> disagree, "null" ->
    missing, any level between -> partial."""
    if label == "exact":
        return "agree"
    if label == "else":
        return "disagree"
    if label == "null":
        return "missing"
    return "partial"


def contribution_words(contribution: Contribution) -> str:
    """ "family name exact (+8.7)"."""
    return (
        f"{comparison_name(contribution.comparison)} {level_words(contribution.label)} "
        f"({signed(contribution.weight)})"
    )


def band_of(score: float, edges: tuple[float, float]) -> Band:
    """The band of a score between the edges (upper, lower), in percent."""
    upper, lower = edges
    if score >= upper:
        return Band.AUTO
    return Band.REVIEW if score >= lower else Band.DISTINCT


def counterfactual_sentence(counterfactual: Counterfactual, edges: tuple[float, float] | None) -> str:
    """The smallest change that moves the band, read: `A matching person reference would give 99
    (automatic).`, or, when a hard rule and not the score would set the band, `A different person
    reference would make it distinct by a hard rule.` `edges` are the bands (upper, lower) in percent."""
    name = comparison_name(counterfactual.comparison)
    if counterfactual.to_label == "exact":
        subject = f"A matching {name}"
    elif counterfactual.to_label == "else":
        subject = f"A different {name}"
    else:
        subject = f"{name[:1].upper()}{name[1:]} {level_words(counterfactual.to_label)}"
    band = BAND_WORDS[counterfactual.band]
    if edges is not None and band_of(counterfactual.score, edges) is not counterfactual.band:
        return f"{subject} would make it {band} by a hard rule."
    return f"{subject} would give {score_text(counterfactual.score)} ({band})."


def plural_name(name: str) -> str:
    """ "postcode" -> "postcodes", "city" -> "cities", "address" -> "addresses", "registered ID" ->
    "registered IDs"."""
    if name.endswith(("s", "x", "ch", "sh")):
        return name + "es"
    if name.endswith("y") and len(name) > 1 and name[-2] not in "aeiou":
        return name[:-1] + "ies"
    return name + "s"


#: how a level reads as a condition on both records: "If the postcodes shared their first part"
_CONDITIONS = {
    "exact": "were the same",
    "else": "differed",
    "phonetic": "only sounded alike",
    "swap_or_digit": "differed only by swapped day and month, or by one digit",
    "same_year": "fell in the same year",
    "equal_unchecked": "were equal but failed their checksum",
    "last7": "matched in their last seven digits",
    "local_part": "shared the part before the @",
    "prefix": "shared their first part",
}


def _condition(name: str, label: str, from_label: str | None, direction: str) -> str:
    """ "If the postcodes shared their first part"; "If the city were only similar, not the same"; "If the
    arriving record gave a matching registered ID". Never an algorithm's name."""
    names = plural_name(name)
    if label == "exact" and from_label == "null":
        return f"If both records gave a {name} and they matched"
    if label == "null":
        return f"Without a {name} on one side"
    if label in _CONDITIONS:
        return f"If the {names} {_CONDITIONS[label]}"
    found = _THRESHOLD_LABEL.match(label)
    if found is not None:
        measure, _relation, value = found.groups()
        if measure == "jw":
            return (
                f"If the {names} were only similar, not the same"
                if direction == "down"
                else (f"If the {names} were similar")
            )
        if measure == "lev":
            return f"If the {names} were within {value} edit{'' if value == '1' else 's'} of each other"
        return f"If the {names} shared most of their words"
    return f"If the {names} compared differently"


def flip_sentence(
    counterfactual: Counterfactual,
    edges: tuple[float, float] | None,
    *,
    weight: float | None = None,
    from_label: str | None = None,
) -> str:
    """What would flip the band, from the steward's side: which way the score moves, to what, what it would
    mean, and the weight the change adds or takes away. "If the postcodes shared their first part (+3.1),
    the score would rise to 98 and link on its own." "If the cities were only similar, not the same
    (−2.9), the score would fall to 53: not a match." A missing identifier that would decide it
    adds a next step: "Ask the source for the registered ID before linking." Built from comparison names,
    level labels, scores and weights only; never a value, never an algorithm's name."""
    name = comparison_name(counterfactual.comparison)
    condition = _condition(name, counterfactual.to_label, from_label, counterfactual.direction)
    change = f" ({signed(weight)})" if weight is not None and abs(weight) >= 0.05 else ""
    band = counterfactual.band
    if edges is not None and band_of(counterfactual.score, edges) is not band:
        rule = "link them" if band is Band.AUTO else "keep them apart"
        return f"{condition}{change}, a hard rule would {rule}."
    score = score_text(counterfactual.score)
    if counterfactual.direction == "up":
        if band is Band.AUTO:
            outcome = f"the score would rise to {score} and link on its own"
        else:
            outcome = f"the score would rise to {score}: a review"
    elif band is Band.DISTINCT:
        outcome = f"the score would fall to {score}: not a match"
    else:
        outcome = f"the score would fall to {score}: a review, not linked on its own"
    sentence = f"{condition}{change}, {outcome}."
    if counterfactual.direction == "up" and from_label == "null" and counterfactual.to_label == "exact":
        sentence += f" Ask the source for the {name} before linking."
    return sentence


def hard_rule_sentence(rule: str) -> str:
    """ "Decided by the must-link rule: the person reference matches." """
    kind, _, attribute = rule.partition(":")
    if kind == "must_link":
        return f"Decided by the must-link rule: the {comparison_name(attribute)} matches."
    if kind == "cannot_link":
        return f"Kept apart by the cannot-link rule: the {comparison_name(attribute)} differs."
    return f"Decided by the rule {rule}."


def strategy_words(code: str) -> str:
    """A survivorship strategy's code in words: "source_trust" -> "source trust", "tie_break" -> "the
    source key order"; an unknown code reads as its words."""
    return STRATEGY_WORDS.get(code, code.replace("_", " "))
