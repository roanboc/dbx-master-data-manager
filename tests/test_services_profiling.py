"""Profiling source records: counts, patterns, masked personal top values (owner: SERVICES, B.10)."""

from __future__ import annotations

import pytest

from mdm.models.authority import Actor
from mdm.models.errors import Forbidden, NotFound
from mdm.services.profiling import pattern_of
from tests.test_services_fixtures import GIVEN, land, mini_world


def test_pattern_of() -> None:
    assert pattern_of("Ab 12-z") == "Aa 99-a"


def test_a_profile_counts_and_masks(hub) -> None:
    world = mini_world(persons=12, organisations=3)
    land(hub, world.rows)
    hub.arrival.intake(hub.store.landing_above(0, 1000))
    profile = hub.profiling.profile("person", actor=hub.actor)
    persons = sum(1 for r in world.rows if r.entity == "person")
    assert profile.records == persons
    by_name = {a.name: a for a in profile.attributes}
    given = by_name["given_name"]
    assert given.filled == persons and given.empty == 0
    assert all(value.endswith("***") and len(value) == 4 for value, _ in given.top)
    assert not any(name in str(profile) for name in GIVEN)
    assert by_name["city"].top[0][0] in {r.payload.get("city") for r in world.rows}  # not personal: shown
    assert by_name["email"].empty > 0  # student records carry no e-mail
    assert by_name["birth_date"].patterns[0][0] == "9999-99-99"
    only_hr = hub.profiling.profile("person", "hr", actor=hub.actor)
    assert only_hr.records == 12
    with pytest.raises(NotFound):
        hub.profiling.profile("person", "ledger", actor=hub.actor)
    with pytest.raises(Forbidden):
        hub.profiling.profile("person", actor=Actor("reader", "person", "consumer"))
