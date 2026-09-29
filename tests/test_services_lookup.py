"""The record reader on both engines: resolving references, golden values with provenance chips, the Why of a
value, members, the timeline and relationships, a source record, and reveals with a reason (owner: SERVICES,
B.6.7, B.9.1)."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from mdm import capacity
from mdm.models.canonical import utcnow
from mdm.models.errors import Forbidden, NotFound
from mdm.models.records import SourceKey, StewardValue
from mdm.services.context import Hub
from tests.helpers import (
    CONSUMER,
    COORDINATOR,
    STEWARD,
    arrive,
    finance_key,
    hr_key,
    land,
    master_of,
    person_payload,
    person_ref,
    person_review,
    row,
    seen,
    task_of,
    workbench_world,
)
from tests.test_services_tray import window_passed


@pytest.fixture
def world(hub: Hub) -> Hub:
    workbench_world(hub)
    return hub


def person_master(hub: Hub, i: int) -> str:
    master = master_of(hub, "person", "hr", hr_key(i))
    assert master is not None
    return master


# ---------------------------------------------------------------------------------------------- resolving


def test_references_resolve_to_what_they_name(world: Hub) -> None:
    master = person_master(world, 0)
    assert world.lookup.resolve(master, actor=CONSUMER) == type(world.lookup.resolve(master, actor=CONSUMER))(
        "golden", "person", master, None, None
    )
    linked = world.lookup.resolve("crm:C100000", actor=STEWARD)
    assert (linked.kind, linked.entity, linked.master_id, linked.source) == (
        "source",
        "person",
        master,
        "crm:C100000",
    )
    organisation = world.lookup.resolve(f"finance:{finance_key(0)}", actor=STEWARD)
    assert (organisation.kind, organisation.entity) == ("source", "organisation")
    unknown = world.lookup.resolve("ORG-999999", actor=STEWARD)
    assert (unknown.kind, unknown.notice) == ("unknown", "No record has the ID ORG-999999.")
    assert world.lookup.resolve("crm:C9999999", actor=STEWARD).notice == "No record has the ID crm:C9999999."
    assert world.lookup.resolve("Tamsin Quorrel", actor=STEWARD).notice == "No record has that ID."
    # an unlinked record, and a retired ID that resolves to its survivor
    source = person_review(world)
    unlinked = world.lookup.resolve(source.text(), actor=STEWARD)
    assert (unlinked.kind, unlinked.master_id) == ("source", None)
    retired = person_master(world, 1)
    world.lifecycle.merge("person", master, retired, maker=STEWARD, checker=COORDINATOR, reason="duplicate")
    merged = world.lookup.resolve(retired, actor=STEWARD)
    assert (merged.kind, merged.master_id) == ("golden", master)
    assert merged.notice == f"{retired} was merged into {master}; showing the survivor."
    assert world.lookup.header("person", master, actor=STEWARD).retired_ids == (retired,)


# ---------------------------------------------------------------------------------------------- golden values


def test_golden_values_are_masked_with_provenance_chips(world: Hub) -> None:
    master = person_master(world, 0)
    for actor in (STEWARD, CONSUMER):
        values = {v.attribute: v for v in world.lookup.golden("person", master, actor=actor)}
        given = values["given_name"]
        assert (given.value, given.masked, given.personal) == ("T***", True, True)
        assert given.source == f"hr:{hr_key(0)}" and given.decided_by == "source_trust"
        assert given.chip.startswith("hr · source trust · ") and given.chip.endswith(" d")
        assert values["birth_date"].value == "hidden" and values["family_name"].critical
        city = values["city"]
        assert (city.value, city.masked, city.personal) == ("Norvale", False, False)
        assert values["email"].decided_by == "recency" and values["email"].chip.startswith("crm · recency")
        assert values["person_ref"].decided_by == "only"
        assert values["left_on"].value is None and values["left_on"].chip is None
    header = world.lookup.header("person", master, actor=CONSUMER)
    assert (header.title, header.status, header.member_count) == ("T*** Q***", "active", 3)
    assert header.relationship_count == 1 and header.retired_ids == ()
    with pytest.raises(NotFound):
        world.lookup.header("person", "PER-999999", actor=STEWARD)


def test_a_reveal_asks_a_reason_code_and_logs_each_attribute(world: Hub) -> None:
    master = person_master(world, 0)
    with pytest.raises(Forbidden):
        world.lookup.golden("person", master, actor=CONSUMER, reveal=True, reason="audit_check")
    with pytest.raises(Forbidden) as free_text:
        world.lookup.golden("person", master, actor=STEWARD, reveal=True, reason="I was asked")
    assert free_text.value.code == "reason_required"
    values = {
        v.attribute: v
        for v in world.lookup.golden("person", master, actor=STEWARD, reveal=True, reason="audit_check")
    }
    held = person_payload(0)
    assert (values["given_name"].value, values["given_name"].masked) == (held["given_name"], False)
    assert values["birth_date"].value == held["birth_date"]
    log = world.store.access_log(None, 100)
    assert {(e["reason"], e["master_id"], e["detail"]["subject"]) for e in log} == {
        ("audit_check", master, master)
    }
    assert sorted(e["attribute"] for e in log) == sorted(
        v.attribute for v in values.values() if v.personal and v.value is not None
    )
    source = world.lookup.source(
        SourceKey("crm", "C100000"), actor=STEWARD, reveal=True, reason="source_defect"
    )
    shown = {v.attribute: v.value for v in source.values}
    assert shown["given_name"] == held["given_name"]
    later = [e for e in world.store.access_log(None, 100) if e["reason"] == "source_defect"]
    assert later and {(e["master_id"], e["detail"]["subject"]) for e in later} == {(master, "crm:C100000")}


def test_why_a_value_won(world: Hub) -> None:
    master = person_master(world, 0)
    given = world.lookup.why("person", master, "given_name", actor=CONSUMER)
    assert given.sentence == (
        "Survivorship rules v1 for Given name use source trust, then recency. The hr source ranks 1 and the "
        "student_records source ranks 2 for Given name, so the hr value wins."
    )
    assert given.winner.source == f"hr:{hr_key(0)}" and given.winner.value == "T***"
    assert all(r.value is None or r.value.endswith("***") or r.value == "hidden" for r in given.runners_up)
    email = world.lookup.why("person", master, "email", actor=STEWARD)
    assert (
        email.sentence
        == "Survivorship rules v1 for Email use recency, then source trust. The most recent value wins."
    )
    assert email.winner.value.endswith("***")
    only = world.lookup.why("person", master, "person_ref", actor=STEWARD)
    assert only.sentence == f"Only hr:{hr_key(0)} holds a value for Person reference."
    assert (
        world.lookup.why("person", master, "left_on", actor=STEWARD).sentence
        == "No source holds a value for Left on."
    )
    with pytest.raises(NotFound):
        world.lookup.why("person", master, "nickname", actor=STEWARD)


def test_a_pin_and_a_row_written_before_the_deciding_strategy_was_recorded(world: Hub) -> None:
    master = person_master(world, 0)
    documents = world.store.provenance("person", [master])[master]
    until = utcnow() + timedelta(days=30)
    pinned = {
        **documents["city"],
        "strategy": ["pin"],
        "decided_by": "pin",
        "winner": {"source": "steward:city", "value": "Norvale"},
    }
    older = {k: v for k, v in documents["country"].items() if k != "decided_by"}
    world.store.write_provenance("person", {master: {**documents, "city": pinned, "country": older}}, 1, 1)
    world.store.write_steward_values(
        "person", {master: {"city": StewardValue("Norvale", until, STEWARD.name, utcnow())}}, 1
    )
    city = world.lookup.why("person", master, "city", actor=STEWARD)
    assert city.sentence.startswith("A steward pinned this value until ") and city.sentence.endswith(
        ", so it wins over every source."
    )
    values = {v.attribute: v for v in world.lookup.golden("person", master, actor=STEWARD)}
    assert values["city"].source == "steward" and values["city"].chip.startswith("Steward pin · until ")
    assert values["city"].pinned_until == until
    country = world.lookup.why("person", master, "country", actor=STEWARD)
    assert country.decided_by is None and country.sentence == (
        "Survivorship rules v1 for Country use source trust, then recency; the strategies are applied in that order."
    )
    assert values["country"].decided_by is None and values["country"].chip.startswith("hr · rules · ")


# ---------------------------------------------------------------------------------------------- members, relationships


def test_members_with_trust_and_rule_failures(world: Hub) -> None:
    master = person_master(world, 0)
    land(
        world,
        [row("crm", "C100000", "person", person_payload(0, email="broken-address"), at=utcnow())],
    )
    arrive(world)
    members = {m.source: m for m in world.lookup.members("person", master, actor=CONSUMER)}
    assert set(members) == {f"hr:{hr_key(0)}", "crm:C100000", "student_records:S0000000"}
    assert (members[f"hr:{hr_key(0)}"].trust, members["crm:C100000"].trust) == (1, 3)
    assert members["crm:C100000"].rule_failures == ("email: bad_pattern",)
    assert members[f"hr:{hr_key(0)}"].source_version == 1 and not members["crm:C100000"].held


def test_relationships_are_grouped_with_every_asserting_source(world: Hub) -> None:
    master = person_master(world, 0)
    employer = master_of(world, "organisation", "finance", finance_key(0))
    land(world, [row("crm", "C100000", "person", person_payload(0, employer=finance_key(0)), at=utcnow())])
    arrive(world)
    (works_at,) = world.lookup.relationships("person", master, actor=STEWARD)
    assert (works_at.rel_type, works_at.label, works_at.direction) == ("works_at", "works at", "out")
    assert (works_at.other_entity, works_at.other_master_id, works_at.status) == (
        "organisation",
        employer,
        "active",
    )
    assert works_at.sources == ("crm:C100000", f"hr:{hr_key(0)}")
    assert works_at.other_title == "Brindle Works Ltd" and works_at.valid_from is not None
    inbound = world.lookup.relationships("organisation", employer, actor=STEWARD)
    assert {r.label for r in inbound} == {"employs"} and all(r.direction == "in" for r in inbound)
    assert all(r.other_title.endswith("***") for r in inbound)  # people are masked
    assert master in {r.other_master_id for r in inbound}


# ---------------------------------------------------------------------------------------------- the timeline


def test_the_timeline_is_newest_first_and_names_no_person(
    world: Hub, monkeypatch: pytest.MonkeyPatch
) -> None:
    master = person_master(world, 0)
    retired = person_master(world, 1)
    land(world, [row("crm", "C100000", "person", person_payload(0, phone="0999 5550099"), at=utcnow())])
    arrive(world)
    world.lifecycle.merge("person", master, retired, maker=STEWARD, checker=COORDINATOR, reason="duplicate")
    page = world.lookup.timeline("person", master, actor=CONSUMER)
    keys = [(e.commit_version, e.change_seq) for e in page.events]
    assert keys == sorted(keys, reverse=True) and page.before is None
    merge, update, created = page.events[0], page.events[1], page.events[-1]
    assert merge.headline == f"{retired} merged into this record" and merge.kind == "merged"
    assert (merge.actor, merge.automated) == ("Data steward", False)
    assert merge.authority.startswith("Data steward; checker Coordinating steward")
    assert update.headline == "Values updated: phone" and update.actor == "Automated matcher"
    # the policy's clauses stay in the audit; the line says how it was applied and where the value came from
    assert update.authority == "Applied automatically under rules v1 · from crm:C100000"
    assert created.kind == "created" and created.headline == "Created" and created.automated
    names = {STEWARD.name, COORDINATOR.name}
    assert not any(e.actor in names or any(n in e.authority for n in names) for e in page.events)
    retired_page = world.lookup.timeline("person", retired, actor=STEWARD)
    assert retired_page.events[0].headline == f"Merged into {master}"
    # paged by the cursor
    monkeypatch.setattr(capacity, "TIMELINE_PAGE", 1)
    first = world.lookup.timeline("person", master, actor=STEWARD)
    second = world.lookup.timeline("person", master, actor=STEWARD, before=first.before)
    assert [e.commit_version for e in first.events + second.events] == [k[0] for k in keys[:2]]


def test_a_create_after_a_stewards_not_a_match_says_so(world: Hub) -> None:
    source = person_review(world)
    task = task_of(world, kind="review", source=source)
    world.tray.stage(task.task_id, "not_a_match", actor=STEWARD, **seen(world, task.task_id))
    window_passed(world)
    world.tray.flush()
    created = master_of(world, "person", source.system, source.key)
    (event,) = world.lookup.timeline("person", created, actor=STEWARD).events
    assert event.kind == "created" and event.automated and event.actor == "Automated matcher"
    assert event.authority.startswith("Applied automatically under rules v1 · from ")
    assert event.authority.endswith("after a steward's not-a-match") and "=auto" not in event.authority


# ---------------------------------------------------------------------------------------------- a source record


def test_a_source_record(world: Hub) -> None:
    master = person_master(world, 0)
    view = world.lookup.source(SourceKey("hr", hr_key(0)), actor=CONSUMER)
    assert (view.entity, view.status, view.linked_to, view.held) == ("person", "active", master, False)
    assert view.title == "T*** Q***" and view.approved_differs == ()
    values = {v.attribute: v for v in view.values}
    assert values["person_ref"].value == person_ref(1000)[:1] + "***" and values["person_ref"].chip is None
    assert [v[0] for v in view.versions] == [1] and view.open_tasks == ()
    assert (
        world.lookup.source(SourceKey("hr", hr_key(0)), actor=STEWARD, entity="person").source
        == f"hr:{hr_key(0)}"
    )
    with pytest.raises(NotFound):
        world.lookup.source(SourceKey("crm", "C9999999"), actor=STEWARD)
    # a held update: the approved values differ
    land(world, [row("crm", "C100000", "person", person_payload(0, family_name="Wrenfield"), at=utcnow())])
    arrive(world)
    held = world.lookup.source(SourceKey("crm", "C100000"), actor=STEWARD)
    assert held.held and held.approved_differs == ("family_name",) and len(held.open_tasks) == 1
    assert len(held.versions) == 2 and held.versions[0][2] < held.versions[1][2]


def test_the_header_names_the_records_orphan_task(world: Hub) -> None:
    from tests.helpers import standalone_organisation

    source = standalone_organisation(world)
    master = master_of(world, "organisation", source.system, source.key)
    land(world, [row("crm", source.key, "organisation", {}, op="delete", at=utcnow())])
    arrive(world)
    orphan = task_of(world, kind="orphan")
    header = world.lookup.header("organisation", master, actor=STEWARD)
    assert header.open_tasks == (orphan.task_id,) and header.member_count == 0


def test_a_provenance_entry_is_masked_unless_revealed(world: Hub) -> None:
    master = person_master(world, 0)
    model = world.registry.published("person")
    entry = world.store.provenance("person", [master])[master]["given_name"]
    masked = world.privacy.masked_provenance(model, "given_name", entry, reveal=False)
    assert masked["winner"] == {"source": f"hr:{hr_key(0)}", "value": "hidden"}
    assert masked["strategy"] == entry["strategy"] and masked["decided_by"] == "source_trust"
    clear = world.privacy.masked_provenance(model, "given_name", entry, reveal=True)
    assert clear["winner"]["value"] == person_payload(0)["given_name"]
    city = world.privacy.masked_provenance(
        model, "city", world.store.provenance("person", [master])[master]["city"], reveal=False
    )
    assert city["winner"]["value"] == "Norvale"


def test_a_batchs_chunks_read_on_the_timeline() -> None:
    """Story 3.3: a chunk's links, and a compensation's, name the batch and the chunk; neither reads as a bare
    decision."""
    from mdm.services.lookup import LookupService

    batch = "BAT-" + "c" * 20
    proof = {"decision": "batch_link", "batch_id": batch, "chunk": 2, "chunks": 3}
    assert LookupService._headline("updated", ("xref",), None, (), (), (), proof) == (  # noqa: SLF001
        f"Members changed: linked in batch {batch}, chunk 2 of 3"
    )
    original = "BAT-" + "d" * 20
    undo = {
        "decision": "batch_compensate",
        "batch_id": batch,
        "compensates": original,
        "chunk": 1,
        "chunks": 1,
    }
    assert LookupService._headline("updated", ("xref",), None, (), (), (), undo) == (  # noqa: SLF001
        f"Members changed: a batch's link undone, batch {original}, chunk 1 of 1"
    )
    assert LookupService._decision_headline(undo, "PER-000001") == (  # noqa: SLF001
        "A batch's link was undone; nothing published"
    )
    assert "Decided; nothing published" not in LookupService._decision_headline(proof, "PER-000001")  # noqa: SLF001
    # whatever the headline ("Values updated" when the golden values change too), the event names its chunk
    commit = SimpleNamespace(
        authority_kind="role", authority_ref="data_steward; checker coordinating_steward"
    )
    assert LookupService._authority_text(commit, False, (), proof) == (  # noqa: SLF001
        f"Data steward; checker Coordinating steward · in batch {batch}, chunk 2 of 3"
    )
    assert LookupService._authority_text(commit, False, (), undo).endswith(  # noqa: SLF001
        f" · undoing batch {original}, chunk 1 of 1"
    )
    assert LookupService._authority_text(commit, False) == "Data steward; checker Coordinating steward"  # noqa: SLF001
