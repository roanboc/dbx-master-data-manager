"""The band of a score as plain text after a dot in the band's colour ("● 79 review"; the dot is drawn by
styles.css), so colour is never the only cue.

The words are the engine's (`models.wording`): auto reads "automatic", and a score short of certainty
never reads 100.

Owner: SHELL (B.8.6).
"""

from __future__ import annotations

from dash import html
from dash.development.base_component import Component

from mdm.models import wording

#: the bands a chip knows; anything else is drawn as distinct
BANDS = ("auto", "review", "distinct")


def score_text(score: float | None) -> str:
    """A score as a steward reads it: "79", "99" (never 100 short of certainty), "" for none."""
    return "" if score is None else wording.score_text(score)


def band_words(band: str | None) -> str:
    """ "automatic", "review", "distinct"; "" for none."""
    if band is None:
        return ""
    return wording.BAND_WORDS.get(band, band) if band in BANDS else band


def band_chip(band: str | None, score: float | None = None) -> Component:
    """ "79 review": the band (auto, review, distinct), its dot in the band's colour, with the score when
    given."""
    if band is None and score is None:
        return html.Span()
    known = band if band in BANDS else "distinct"
    text = " ".join(part for part in (score_text(score), band_words(band)) if part)
    return html.Span(text, className=f"mdm-band mdm-band-{known}")
