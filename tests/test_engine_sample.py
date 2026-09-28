"""The draw of quality samples and the bound on agreement (story 3.2): pure, so no engine is needed."""

from __future__ import annotations

import math
import random

import pytest

from mdm.capacity import BREAKER_Z, SAMPLE_BASIS
from mdm.engine.sample import agreement_upper, below, draw_value, drawn


def _subjects(n: int, seed: int = 11) -> list[tuple[str, str]]:
    rng = random.Random(seed)
    return [(f"crm:C{rng.randrange(10**9):09d}", f"ev-{rng.randrange(10**12)}") for _ in range(n)]


def test_draws_are_deterministic_and_63_bit() -> None:
    first = draw_value("person", "crm:C000001", "auto_link", "ev-1")
    assert first == draw_value("person", "crm:C000001", "auto_link", "ev-1")
    assert 0 <= first < 2**63
    assert first != draw_value("organisation", "crm:C000001", "auto_link", "ev-1")


def test_the_key_changes_every_draw_and_the_same_key_draws_the_same() -> None:
    subjects = _subjects(2_000, seed=7)
    plain = [draw_value("person", s, "link", e) for s, e in subjects]
    keyed = [draw_value("person", s, "link", e, "a-deployment-secret") for s, e in subjects]
    again = [draw_value("person", s, "link", e, "a-deployment-secret") for s, e in subjects]
    assert keyed == again
    assert sum(a == b for a, b in zip(plain, keyed, strict=True)) == 0
    # without the key, what a steward can see does not tell which decisions a share of 10% draws
    overlap = sum(drawn(a, 0.1) and drawn(b, 0.1) for a, b in zip(plain, keyed, strict=True))
    assert overlap < 0.1 * 0.1 * len(subjects) * 3


def test_a_share_of_nothing_draws_none_and_a_share_of_everything_draws_all() -> None:
    values = [draw_value("person", s, "auto_link", e) for s, e in _subjects(2_000)]
    assert not any(drawn(v, 0.0) for v in values)
    assert all(drawn(v, 1.0) for v in values)


def test_the_draw_is_monotone_in_the_share() -> None:
    values = [draw_value("person", s, "link", e) for s, e in _subjects(3_000)]
    shares = (0.0, 0.01, 0.02, 0.05, 0.1, 0.3, 0.5, 0.9, 1.0)
    for low, high in zip(shares, shares[1:], strict=False):
        assert {v for v in values if drawn(v, low)} <= {v for v in values if drawn(v, high)}


@pytest.mark.parametrize("share", [0.02, 0.1, 0.5])
def test_the_fraction_drawn_stays_within_four_sigma(share: float) -> None:
    n = 20_000
    hits = sum(drawn(draw_value("person", s, "auto_link", e), share) for s, e in _subjects(n, seed=3))
    sigma = math.sqrt(n * share * (1 - share))
    assert abs(hits - n * share) <= 4 * sigma


def test_draws_of_one_record_at_two_events_or_for_two_decisions_are_independent() -> None:
    share, n = 0.3, 20_000
    subjects = _subjects(n, seed=5)
    events = sum(
        drawn(draw_value("person", s, "auto_link", e), share)
        and drawn(draw_value("person", s, "auto_link", e + "-next"), share)
        for s, e in subjects
    )
    decisions = sum(
        drawn(draw_value("person", s, "auto_link", e), share)
        and drawn(draw_value("person", s, "link", e), share)
        for s, e in subjects
    )
    expected, sigma = n * share * share, math.sqrt(n * share * share * (1 - share * share))
    assert abs(events - expected) <= 4 * sigma
    assert abs(decisions - expected) <= 4 * sigma


def test_the_basis_rounds_the_share() -> None:
    assert drawn(SAMPLE_BASIS - 1, 1.0) and not drawn(SAMPLE_BASIS - 1, 0.999999)
    assert drawn(0, 0.000001) and not drawn(1, 0.000001)


def test_wilsons_upper_bound_matches_known_values() -> None:
    assert agreement_upper(0, 0, BREAKER_Z) == 1.0
    assert agreement_upper(20, 20, BREAKER_Z) == 1.0
    assert agreement_upper(17, 20, BREAKER_Z) == pytest.approx(0.9384, abs=5e-4)
    assert agreement_upper(18, 20, BREAKER_Z) == pytest.approx(0.9663, abs=5e-4)
    assert agreement_upper(91, 100, BREAKER_Z) == pytest.approx(0.9469, abs=5e-4)
    assert agreement_upper(92, 100, BREAKER_Z) == pytest.approx(0.9544, abs=5e-4)


@pytest.mark.parametrize(("reviewed", "trips_at"), [(20, 3), (50, 6), (100, 9)])
def test_the_bound_trips_at_the_declared_counts_and_not_at_one_fewer(reviewed: int, trips_at: int) -> None:
    assert below(reviewed - trips_at, reviewed, 0.95, BREAKER_Z)
    assert not below(reviewed - trips_at + 1, reviewed, 0.95, BREAKER_Z)
