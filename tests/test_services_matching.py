"""The match test: a record scored against the golden records, nothing written (owner: SERVICES, B.10)."""

from __future__ import annotations

from mdm.models.match import Band
from mdm.models.records import SourceKey
from tests.test_services_fixtures import (
    arrive,
    crm_person_key,
    hr_key,
    land,
    master_of,
    mini_world,
    person_payload,
    person_ref,
)


def test_a_payload_is_scored_against_the_golden_records_and_nothing_is_written(hub) -> None:
    land(hub, mini_world(persons=8, organisations=2).rows)
    arrive(hub)
    version = hub.store.last_commit_version()
    probe = person_payload(3, person_ref=person_ref(1003))
    found = hub.matching.match_test("person", payload={**probe, "source_system": "hr"}, top=3)
    assert found and found[0].master_id == master_of(hub, "person", "hr", hr_key(3))
    assert found[0].best.explanation.band == Band.AUTO
    assert found[0].best.explanation.hard_rule == "must_link:person_ref"
    assert len(found) <= 3
    assert [g.best.explanation.score for g in found] == sorted(
        (g.best.explanation.score for g in found), reverse=True
    )
    assert hub.store.last_commit_version() == version
    assert hub.store.queue_size() == 0


def test_a_stored_record_is_scored_without_itself(hub) -> None:
    land(hub, mini_world(persons=6, organisations=0).rows)
    arrive(hub)
    source = SourceKey("crm", crm_person_key(2))
    found = hub.matching.match_test("person", source=source)
    master = master_of(hub, "person", "crm", crm_person_key(2))
    assert found[0].master_id == master
    assert all(g.best.right != source for g in found)
