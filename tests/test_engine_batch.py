"""The forced sample's size, strata and draw, the blind-review count, the chunk packer and the split (story 3.3):
pure, so no engine."""

from __future__ import annotations

import random

import pytest

from mdm.engine.batch import (
    Member,
    PackItem,
    allocate,
    forced_sample_size,
    pack,
    pick,
    pick_reviews,
    review_count,
    split_members,
)
from mdm.engine.sample import draw_value
from mdm.models.batch import FORCED_SAMPLE_BASE, FORCED_SAMPLE_PER

BASE, PER = FORCED_SAMPLE_BASE, FORCED_SAMPLE_PER


@pytest.mark.parametrize(("n", "size"), [(612, 9), (574, 8), (149, 5), (150, 6), (6, 5), (3, 3), (0, 0)])
def test_the_forced_sample_is_five_and_one_per_hundred_and_fifty(n: int, size: int) -> None:
    assert forced_sample_size(n, BASE, PER) == size


def test_a_group_of_six_has_one_review_to_link_together() -> None:
    assert 6 - forced_sample_size(6, BASE, PER) == 1
    assert 5 - forced_sample_size(5, BASE, PER) == 0  # too few: the sample would take every review


def test_the_size_is_shared_by_largest_remainder() -> None:
    # quotas 3.5, 2.1 and 1.4: floors 3, 2 and 1, and the one seat left to the largest remainder
    assert allocate({"crm/hr": 50, "finance/hr": 30, "hr/hr": 20}, 7) == {
        "crm/hr": 4,
        "finance/hr": 2,
        "hr/hr": 1,
    }
    # a remainder tie goes by stratum code
    assert allocate({"y/y": 2, "x/x": 2}, 3) == {"x/x": 2, "y/y": 1}
    assert sum(allocate({"a/a": 17, "b/b": 5, "c/c": 3}, 9).values()) == 9


def test_every_stratum_gets_one_while_the_size_allows_and_the_largest_first() -> None:
    # quotas 4.5, 0.45 and 0.05: the two small strata each take one from the largest
    assert allocate({"crm/hr": 90, "finance/hr": 9, "hr/hr": 1}, 5) == {
        "crm/hr": 3,
        "finance/hr": 1,
        "hr/hr": 1,
    }
    # fewer seats than strata: the largest strata get one each, a tie of size by code
    assert allocate({"a/a": 5, "c/c": 3, "b/b": 3, "d/d": 1}, 2) == {"a/a": 1, "b/b": 1, "c/c": 0, "d/d": 0}
    assert allocate({"b/b": 1, "a/a": 1}, 1) == {"a/a": 1, "b/b": 0}


def test_no_stratum_gets_more_than_it_holds() -> None:
    shares = allocate({"a/a": 1, "b/b": 100}, 50)
    assert shares == {"a/a": 1, "b/b": 49}
    assert allocate({"a/a": 2, "b/b": 3}, 10) == {"a/a": 2, "b/b": 3}  # never more than there are
    assert allocate({"a/a": 0, "b/b": 4}, 2) == {"b/b": 2}  # an empty stratum takes nothing
    assert allocate({}, 3) == {} and allocate({"a/a": 4}, 0) == {"a/a": 0}
    for seed in range(50):
        rng = random.Random(seed)
        counts = {f"s{i}/hr": rng.randint(0, 40) for i in range(rng.randint(1, 6))}
        size = rng.randint(0, 60)
        shares = allocate(counts, size)
        assert sum(shares.values()) == min(size, sum(counts.values()))
        assert all(shares[s] <= counts[s] for s in shares)
        if size >= len(shares):
            assert all(v >= 1 for v in shares.values())


def _members() -> list[Member]:
    crm = [Member(f"TSK-m{n}", "crm/hr", n) for n in range(1, 7)]
    finance = [Member("TSK-f1", "finance/hr", 10), Member("TSK-f2", "finance/hr", 11)]
    return crm + finance


def test_the_draw_is_deterministic_and_independent_of_input_order() -> None:
    members = _members()
    first = pick(members, 3, {})
    assert first == ["TSK-m1", "TSK-m2", "TSK-f1"]  # crm 2.25 -> 2, finance 0.75 -> 1, in draw order
    for seed in range(10):
        shuffled = list(members)
        random.Random(seed).shuffle(shuffled)
        assert pick(shuffled, 3, {}) == first
    assert pick(members, 3, {"crm/hr": 3}) == []  # the sample holds its size already
    assert pick([], 3, {}) == []


def test_a_top_up_fills_the_strata_that_lack_a_member_first() -> None:
    members = _members()
    rest = [m for m in members if m.task_id not in ("TSK-m1", "TSK-m2")]
    # two crm reviews held: finance lacks its member, so its smallest draw comes before crm's next
    assert pick(rest, 3, {"crm/hr": 2}) == ["TSK-f1"]


def test_a_top_up_after_a_split_prefers_a_stratum_with_no_member_over_a_smaller_draw() -> None:
    # a split left crm holding two, more than its share of one now; finance holds one and hr none. The
    # shares ask for one more finance and one hr, but the sample lacks only one: hr comes first, although
    # finance's next draw is smaller
    members = [
        Member("TSK-c3", "crm/hr", 60),
        *(Member(f"TSK-f{n}", "finance/hr", n) for n in range(2, 12)),
        Member("TSK-h1", "hr/hr", 50),
    ]
    assert allocate({"crm/hr": 3, "finance/hr": 11, "hr/hr": 1}, 4) == {
        "crm/hr": 1,
        "finance/hr": 2,
        "hr/hr": 1,
    }
    assert pick(members, 4, {"crm/hr": 2, "finance/hr": 1}) == ["TSK-h1"]
    assert pick(list(reversed(members)), 4, {"crm/hr": 2, "finance/hr": 1}) == ["TSK-h1"]
    # room for both: the hr review and finance's smallest draw, in draw order
    assert pick(members, 5, {"crm/hr": 2, "finance/hr": 1}) == ["TSK-f2", "TSK-h1"]


def test_a_top_up_draws_the_next_reviews_again() -> None:
    members = _members()
    # the first draw took m1, m2 and f1; m2 turned void and left: the top-up takes m3, the next crm draw
    rest = [m for m in members if m.task_id not in ("TSK-m1", "TSK-m2", "TSK-f1")]
    assert pick(rest, 3, {"crm/hr": 1, "finance/hr": 1}) == ["TSK-m3"]
    # a shortfall no stratum covers is filled with the smallest draws overall
    assert pick([Member("TSK-x", "crm/hr", 5), Member("TSK-y", "crm/hr", 2)], 3, {"finance/hr": 1}) == [
        "TSK-y",
        "TSK-x",
    ]


def test_the_draw_values_are_the_keyed_draw_of_the_group() -> None:
    members = [
        Member(f"TSK-{n}", "crm/hr", draw_value("person", f"TSK-{n}", "forced_sample", "SIG-0f", ""))
        for n in range(40)
    ]
    keyed = [
        Member(m.task_id, m.stratum, draw_value("person", m.task_id, "forced_sample", "SIG-0f", "k" * 16))
        for m in members
    ]
    assert pick(members, 5, {}) == pick(list(reversed(members)), 5, {})
    assert len(pick(keyed, 5, {})) == 5 and pick(keyed, 5, {}) != pick(members, 5, {})


@pytest.mark.parametrize(
    ("n", "share", "count"),
    [
        (0, 0.02, 0),
        (6, 0.02, 1),
        (50, 0.02, 1),
        (51, 0.02, 2),
        (566, 0.02, 12),
        (1000, 0.02, 20),
        (566, 0.0, 0),
    ],
)
def test_two_percent_of_a_batch_goes_to_blind_review(n: int, share: float, count: int) -> None:
    assert review_count(n, share) == count


def test_the_reviews_drawn_are_the_smallest_draws() -> None:
    draws = [("crm:C3", 30), ("crm:C1", 10), ("crm:C2", 10), ("crm:C4", 40)]
    assert pick_reviews(draws, 2) == frozenset({"crm:C1", "crm:C2"})
    assert pick_reviews(draws, 3) == frozenset({"crm:C1", "crm:C2", "crm:C3"})
    assert pick_reviews(draws, 0) == frozenset()


def _rows(items: list[PackItem], chunk: list[int]) -> int:
    targets = {items[i].target for i in chunk}
    return sum(items[i].own_rows for i in chunk) + len(targets)


def test_no_chunk_goes_over_its_rows_or_its_items_and_the_order_is_kept() -> None:
    rng = random.Random(3)
    items = [PackItem(f"k{i}", f"PER-{rng.randint(1, 40):06d}", 1 + rng.randint(0, 2)) for i in range(300)]
    chunks = pack(items, 50, 20)
    assert [i for chunk in chunks for i in chunk] == list(range(300))
    for chunk in chunks:
        assert _rows(items, chunk) <= 50 and len(chunk) <= 20
    assert pack([], 50, 20) == []


def test_a_targets_golden_row_counts_once_per_chunk() -> None:
    items = [PackItem(f"k{i}", "PER-000001", 1) for i in range(4)]
    assert pack(items, 5, 10) == [[0, 1, 2, 3]]  # four links and one golden row
    # a target with more joiners than fit spans chunks, and its golden row counts in each
    assert pack(items, 3, 10) == [[0, 1], [2, 3]]


def test_relationship_rows_count_toward_a_chunk() -> None:
    items = [PackItem(f"k{i}", f"PER-{i:06d}", 3) for i in range(5)]  # a link with two relationships
    assert pack(items, 8, 10) == [[0, 1], [2, 3], [4]]
    lone = [PackItem("big", "PER-1", 9), PackItem("small", "PER-2", 1)]
    assert pack(lone, 5, 10) == [[0], [1]]  # an item over the limit gets a chunk of its own


def test_566_links_into_distinct_targets_make_chunks_of_250_250_and_66() -> None:
    items = [PackItem(f"k{i}", f"PER-{i:06d}", 1) for i in range(566)]
    assert [len(c) for c in pack(items, 500, 500)] == [250, 250, 66]


def test_a_split_takes_the_reviews_that_share_the_disagreeing_form() -> None:
    forms = {"TSK-1": "1970-01-01", "TSK-2": "1970-01-01", "TSK-3": "1984-06-12", "TSK-4": None, "TSK-5": ""}
    assert split_members(forms, "TSK-1") == frozenset({"TSK-1", "TSK-2"})
    assert split_members(forms, "TSK-3") == frozenset({"TSK-3"})
    # a missing form matches only a missing one, whatever missing looks like
    assert split_members({**forms, "TSK-6": []}, "TSK-4") == frozenset({"TSK-4", "TSK-5", "TSK-6"})
    # the disagreeing review is always among them, even with no form given
    assert split_members({"TSK-2": "x"}, "TSK-9") == frozenset({"TSK-9"})
    assert split_members({"TSK-1": ["a", "b"], "TSK-2": ["a", "b"], "TSK-3": ["a"]}, "TSK-1") == frozenset(
        {"TSK-1", "TSK-2"}
    )


def test_the_worked_example_adds_up() -> None:
    """612 alike reviews: a sample of 9; one disagreement splits 38 off; 574 left need a sample of 8, and
    566 are linked together, 12 of them to blind review, in 3 chunks."""
    assert forced_sample_size(612, BASE, PER) == 9
    placeholder = {f"TSK-{n}": ("1970-01-01" if n < 38 else f"19{n % 90 + 10}-02-03") for n in range(612)}
    split = split_members(placeholder, "TSK-7")
    assert len(split) == 38 and "TSK-7" in split
    left = 612 - len(split)
    assert left == 574 and forced_sample_size(left, BASE, PER) == 8
    linked = left - forced_sample_size(left, BASE, PER)
    assert linked == 566 and review_count(linked, 0.02) == 12
    assert len(pack([PackItem(f"k{i}", f"PER-{i:06d}", 1) for i in range(linked)], 500, 500)) == 3
