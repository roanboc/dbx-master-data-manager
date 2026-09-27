"""The change feed read, as a listening system reads it (owner: SERVICES, B.8)."""

from __future__ import annotations

from mdm.models.authority import Actor
from tests.test_services_fixtures import (
    all_changes,
    arrive,
    golden_rows,
    hr_key,
    land,
    master_of,
    mini_world,
    person_payload,
    person_ref,
    row,
)


def test_a_commit_larger_than_a_page_is_read_across_pages_with_a_cursor(hub) -> None:
    land(
        hub,
        [
            row("hr", hr_key(i), "person", person_payload(i, person_ref=person_ref(1000 + i)), version=1)
            for i in range(7)
        ],
    )
    report = arrive(hub)
    assert report.commits == 1
    first = hub.feed.read(0, max_rows=4)
    assert [c.change_seq for c in first.changes] == [1, 2, 3, 4]
    assert first.cursor == (1, 4) and first.next_watermark == 0  # the commit is not read to its end yet
    assert [c.commit_version for c in first.commits] == [1] and first.commits[0].change_count == 7
    second = hub.feed.read(first.next_watermark, cursor=first.cursor, max_rows=4)
    assert [c.change_seq for c in second.changes] == [5, 6, 7]
    assert second.cursor is None and second.next_watermark == 1
    assert hub.feed.read(second.next_watermark).changes == ()


def test_the_watermark_moves_to_hi_even_past_commits_of_another_entity(hub) -> None:
    land(hub, mini_world(persons=3, organisations=2).rows)
    report = arrive(hub)
    page = hub.feed.read(0, entity="organisation")
    assert {c.entity for c in page.changes} == {"organisation"}
    assert page.next_watermark == report.last_version and page.cursor is None
    # current rows are joined: every master ID of the page has its published row
    assert set(page.rows["organisation"]) == {c.master_id for c in page.changes}
    some = next(iter(page.rows["organisation"].values()))
    assert {"master_id", "status", "_commit_version", "name"} <= set(some)


def test_tombstones_remapped_rows_and_initial_loads_are_visible(hub) -> None:
    land(
        hub,
        [
            row(
                "hr",
                hr_key(i),
                "person",
                person_payload(i, person_ref=person_ref(1000 + i)),
                version=1,
                initial=True,
            )
            for i in range(3)
        ],
    )
    arrive(hub, bulk=True)
    x, y, z = (master_of(hub, "person", "hr", hr_key(i)) for i in range(3))
    maker, checker = Actor("s1", "person", "data_steward"), Actor("s2", "person", "data_steward")
    hub.lifecycle.merge("person", y, x, maker=maker, checker=checker, reason="x is y")
    hub.lifecycle.merge("person", z, y, maker=maker, checker=checker, reason="y is z")
    changes = all_changes(hub, page_rows=2)
    kinds = [(c.master_id, c.change_kind) for c in changes]
    assert (x, "merged") in kinds and (y, "merged") in kinds and (x, "remapped") in kinds
    page = hub.feed.read(0)
    assert page.commits[0].initial_load and not page.commits[-1].initial_load
    tomb = page.rows["person"][x]
    assert tomb["status"] == "merged" and tomb["survivor_id"] == z
    assert golden_rows(hub, "person")[x].survivor_id == z


def test_reading_again_is_safe(hub) -> None:
    land(hub, mini_world(persons=2, organisations=1).rows)
    arrive(hub)
    assert hub.feed.read(0) == hub.feed.read(0)
