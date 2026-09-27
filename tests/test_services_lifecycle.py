"""A person's actions on golden records: link, detach, merge, unmerge, retire, reinstate (owner: SERVICES)."""

from __future__ import annotations

import pytest

from mdm.models.authority import Actor
from mdm.models.errors import Forbidden
from mdm.models.records import SourceKey
from tests.helpers import (
    all_changes,
    arrive,
    crm_person_key,
    finance_key,
    golden_rows,
    hr_key,
    land,
    master_of,
    open_tasks,
    org_payload,
    person_payload,
    person_ref,
    row,
)

MAKER = Actor("steward-one", "person", "data_steward")
CHECKER = Actor("steward-two", "person", "coordinating_steward")


def _three_people(hub) -> tuple[str, str, str]:
    """Three golden records, X (hr 1 + crm 1), Y (hr 2), Z (hr 3), each hr record with an employer."""
    land(hub, [row("finance", finance_key(0), "organisation", org_payload(0), version=1)])
    for i in (1, 2, 3):
        land(
            hub,
            [
                row(
                    "hr",
                    hr_key(i),
                    "person",
                    person_payload(i, person_ref=person_ref(1000 + i), employer=finance_key(0)),
                    version=1,
                )
            ],
        )
    land(hub, [row("crm", crm_person_key(1), "person", person_payload(1))])
    arrive(hub)
    x, y, z = (master_of(hub, "person", "hr", hr_key(i)) for i in (1, 2, 3))
    assert master_of(hub, "person", "crm", crm_person_key(1)) == x
    assert len({x, y, z}) == 3
    return x, y, z


def _kinds(hub, version: int) -> dict[str, tuple[str, str | None, tuple[str, ...]]]:
    return {
        c.master_id: (c.change_kind, c.survivor_id, c.parts)
        for c in all_changes(hub)
        if c.commit_version == version
    }


def test_link_and_detach_recompute_both_records(hub) -> None:
    x, y, _ = _three_people(hub)
    crm = SourceKey("crm", crm_person_key(1))
    moved = hub.lifecycle.link("person", crm, y, actor=MAKER, reason="the crm record is this person")
    assert moved.commit_version is not None
    assert master_of(hub, "person", "crm", crm_person_key(1)) == y
    kinds = _kinds(hub, moved.commit_version)
    assert "xref" in kinds[x][2]
    assert "xref" in kinds[y][2]
    detached = hub.lifecycle.detach("person", crm, actor=MAKER, reason="not this person either")
    assert master_of(hub, "person", "crm", crm_person_key(1)) is None
    assert "xref" in _kinds(hub, detached.commit_version)[y][2]
    relinked = hub.lifecycle.link("person", crm, x, actor=MAKER, reason="back where it was")
    ids = {moved.change_set_id, detached.change_set_id, relinked.change_set_id}
    assert len(ids) == 3  # three change sets, three distinct IDs
    assert hub.store.change_sets(sorted(ids))[moved.change_set_id]["actor"] == MAKER.name


def test_merge_retires_an_id_and_unmerge_reinstates_it(hub) -> None:
    x, y, z = _three_people(hub)
    result = hub.lifecycle.merge("person", x, y, maker=MAKER, checker=CHECKER, reason="the same person")
    v = result.commit_version
    rows = golden_rows(hub, "person")
    assert rows[y].status == "merged" and rows[y].survivor_id == x and rows[y].commit_version == v
    assert master_of(hub, "person", "hr", hr_key(2)) == x
    retired = hub.store.retired_rows([y])[y]
    assert (retired.merged_into, retired.survivor_id, retired.merge_version, retired.active) == (
        x,
        x,
        v,
        True,
    )
    assert hub.store.merge_members(y, v, 10) == [SourceKey("hr", hr_key(2))]
    kinds = _kinds(hub, v)
    assert kinds[y][0] == "merged" and kinds[y][1] == x
    assert kinds[x][0] == "updated" and "xref" in kinds[x][2]
    # relationships repointed at the person's end: both employments now start at the survivor
    employer = master_of(hub, "organisation", "finance", finance_key(0))
    rels = [r for r in hub.store.relationships_of([employer], 10) if r.status == "active"]
    assert sorted(r.from_master_id for r in rels) == sorted([x, x, z])
    assert "relationship" in kinds[employer][2]
    assert hub.store.resolve_retired([y]) == {y: x}

    back = hub.lifecycle.unmerge("person", y, maker=MAKER, checker=CHECKER, reason="two people after all")
    rows = golden_rows(hub, "person")
    assert rows[y].status == "active" and rows[y].survivor_id is None
    assert master_of(hub, "person", "hr", hr_key(2)) == y
    assert hub.store.resolve_retired([y]) == {}
    assert hub.store.retired_rows([y])[y].active is False
    assert _kinds(hub, back.commit_version)[y][0] == "unmerged"
    rels = [r for r in hub.store.relationships_of([employer], 10) if r.status == "active"]
    assert sorted(r.from_master_id for r in rels) == sorted([x, y, z])
    # a re-merge after the unmerge replaces the map row
    again = hub.lifecycle.merge("person", z, y, maker=MAKER, checker=CHECKER, reason="merged elsewhere")
    retired = hub.store.retired_rows([y])[y]
    assert (retired.merged_into, retired.merge_version, retired.active) == (z, again.commit_version, True)


def test_a_chain_then_unmerging_its_middle_remaps_the_first(hub) -> None:
    x, y, z = _three_people(hub)
    hub.lifecycle.merge("person", y, x, maker=MAKER, checker=CHECKER, reason="x is y")
    second = hub.lifecycle.merge("person", z, y, maker=MAKER, checker=CHECKER, reason="y is z")
    assert hub.store.resolve_retired([x, y]) == {x: z, y: z}
    kinds = _kinds(hub, second.commit_version)
    assert kinds[x] == ("remapped", z, ("survivor",))
    assert golden_rows(hub, "person")[x].survivor_id == z
    # unmerge Y: X resolves to Y again, Y's members (its own and X's) come back, X gets a remapped row
    back = hub.lifecycle.unmerge("person", y, maker=MAKER, checker=CHECKER, reason="y is not z")
    assert hub.store.resolve_retired([x, y]) == {x: y}
    assert golden_rows(hub, "person")[x].survivor_id == y
    assert _kinds(hub, back.commit_version)[x] == ("remapped", y, ("survivor",))
    assert {master_of(hub, "person", "hr", hr_key(i)) for i in (1, 2)} == {y}
    assert master_of(hub, "person", "hr", hr_key(3)) == z


def test_unmerging_the_first_of_a_chain_moves_exactly_its_members_back(hub) -> None:
    x, y, z = _three_people(hub)
    hub.lifecycle.merge("person", y, x, maker=MAKER, checker=CHECKER, reason="x is y")
    hub.lifecycle.merge("person", z, y, maker=MAKER, checker=CHECKER, reason="y is z")
    hub.lifecycle.unmerge("person", x, maker=MAKER, checker=CHECKER, reason="x is someone else")
    assert master_of(hub, "person", "hr", hr_key(1)) == x
    assert master_of(hub, "person", "crm", crm_person_key(1)) == x
    assert master_of(hub, "person", "hr", hr_key(2)) == z
    assert master_of(hub, "person", "hr", hr_key(3)) == z
    assert hub.store.resolve_retired([x, y]) == {y: z}


def test_merges_need_a_checker_other_than_the_maker(hub) -> None:
    x, y, _ = _three_people(hub)
    with pytest.raises(Forbidden):
        hub.lifecycle.merge("person", x, y, maker=MAKER, checker=MAKER, reason="alone")
    with pytest.raises(Forbidden):
        hub.lifecycle.merge("person", x, y, maker=MAKER, checker=None, reason="alone")  # type: ignore[arg-type]
    owner = Actor("owner-one", "person", "data_owner")
    with pytest.raises(Forbidden):
        hub.lifecycle.merge("person", x, y, maker=owner, checker=CHECKER, reason="not a steward's action")


def test_a_merge_across_entities_is_refused(hub) -> None:
    x, _, _ = _three_people(hub)
    organisation = master_of(hub, "organisation", "finance", finance_key(0))
    with pytest.raises(Forbidden):
        hub.lifecycle.merge("person", x, organisation, maker=MAKER, checker=CHECKER, reason="wrong")


def test_retire_needs_an_orphan_and_reinstate_takes_a_retired_record_only(hub) -> None:
    x, y, z = _three_people(hub)
    with pytest.raises(Forbidden):
        hub.lifecycle.retire("person", z, maker=MAKER, checker=CHECKER, reason="still has a member")
    hub.lifecycle.detach("person", SourceKey("hr", hr_key(3)), actor=MAKER, reason="not a person")
    assert [t.master_ids for t in open_tasks(hub, "person", "orphan")] == [(z,)]
    retired = hub.lifecycle.retire("person", z, maker=MAKER, checker=CHECKER, reason="nobody left")
    assert golden_rows(hub, "person")[z].status == "retired"
    assert _kinds(hub, retired.commit_version)[z] == ("retired", None, ("status",))
    hub.lifecycle.merge("person", x, y, maker=MAKER, checker=CHECKER, reason="the same person")
    with pytest.raises(Forbidden):
        hub.lifecycle.reinstate("person", y, actor=MAKER, reason="a merged record needs unmerge")
    back = hub.lifecycle.reinstate("person", z, actor=MAKER, reason="back after all")
    assert golden_rows(hub, "person")[z].status == "active"
    assert _kinds(hub, back.commit_version)[z] == ("reinstated", None, ("status",))


def test_an_automated_merge_is_refused(hub) -> None:
    from mdm.models.authority import AUTOMATED_MATCHER

    x, y, _ = _three_people(hub)
    with pytest.raises(Forbidden):
        hub.lifecycle.merge("person", x, y, maker=AUTOMATED_MATCHER, checker=CHECKER, reason="automatic")
