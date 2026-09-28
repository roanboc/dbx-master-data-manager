"""Clustering a batch against golden records and each other (B.9.6)."""

from __future__ import annotations

import random

from mdm.engine.cluster import ClusterInput, Resolution, resolve_batch
from mdm.models.match import Band, Explanation, GoldenCandidate, PairScore
from mdm.models.records import SourceKey
from tests.test_engine_support import key

A, B, C, D, E = (key(f"crm:C{i}") for i in range(1, 6))
H1, H2 = key("hr:H1"), key("hr:H2")


def x(score: float, hard_rule: str | None = None) -> Explanation:
    band = Band.AUTO if score >= 90 else Band.REVIEW if score >= 60 else Band.DISTINCT
    if hard_rule:
        band = Band.AUTO if hard_rule.startswith("must_link") else Band.DISTINCT
    return Explanation(
        prior=-10.0,
        contributions=(),
        hard_rule=hard_rule,
        weight=0.0,
        score=score,
        band=band,
        counterfactuals=(),
        signature="",
        rule_version=1,
    )


def pair(left: SourceKey, right: SourceKey, score: float, hard_rule: str | None = None) -> PairScore:
    return PairScore(left, right, x(score, hard_rule))


def golden(master_id: str, source: SourceKey, member: SourceKey, score: float, blocked: str | None = None):
    return GoldenCandidate(master_id, pair(source, member, score), members_scored=1, blocked_by=blocked)


def plain(
    *sources: SourceKey, ids: dict[SourceKey, set[tuple[str, str]]] | None = None
) -> list[ClusterInput]:
    ids = ids or {}
    return [ClusterInput(s, frozenset(ids.get(s, set()))) for s in sources]


# ------------------------------------------------------------------------------------------------ golden records


def test_one_auto_golden_links() -> None:
    out = resolve_batch(plain(A), {A: [golden("PER-1", A, H1, 97.0), golden("PER-2", A, H2, 70.0)]}, [], {})
    assert out == [Resolution("link", (A,), ("PER-1",), None, x(97.0), None, reason="auto_band")]


def test_two_auto_goldens_are_ambiguous_never_merged() -> None:
    out = resolve_batch(plain(A), {A: [golden("PER-1", A, H1, 93.0), golden("PER-2", A, H2, 99.0)]}, [], {})
    (only,) = out
    assert only.kind == "ambiguous" and only.reason == "ambiguous_auto"
    assert only.master_ids == ("PER-2", "PER-1")  # strongest first
    assert only.best == x(99.0)


def test_a_review_band_golden_is_a_review() -> None:
    out = resolve_batch(plain(A), {A: [golden("PER-1", A, H1, 75.0), golden("PER-2", A, H2, 20.0)]}, [], {})
    assert out == [Resolution("review", (A,), ("PER-1",), None, x(75.0), None, reason="review_band")]


def test_a_blocked_auto_golden_is_a_review_not_a_link() -> None:
    """★ The best member scores auto, but another active member's valid ID conflicts: never linked."""
    blocked = golden("PER-1", A, H1, 99.0, blocked="cannot_link:person_ref")
    out = resolve_batch(plain(A), {A: [blocked]}, [], {})
    (only,) = out
    assert only.kind == "review" and only.reason == "cannot_link_conflict"
    assert only.master_ids == ("PER-1",)


def test_a_blocked_golden_beside_a_linkable_one_links_to_the_linkable() -> None:
    blocked = golden("PER-1", A, H1, 99.0, blocked="cannot_link:person_ref")
    fine = golden("PER-2", A, H2, 92.0)
    out = resolve_batch(plain(A), {A: [blocked, fine]}, [], {})
    assert out[0].kind == "link" and out[0].master_ids == ("PER-2",)


def test_a_pair_with_an_unlinked_record_outside_the_batch_is_a_review() -> None:
    outside = key("student_records:S9")
    out = resolve_batch(plain(A), {}, [], {A: [pair(A, outside, 80.0), pair(outside, A, 10.0)]})
    (only,) = out
    assert only.kind == "review" and only.reason == "unlinked_candidate"
    assert only.review_with == (outside,)
    assert only.master_ids == ()
    distinct_only = resolve_batch(plain(B), {}, [], {B: [pair(B, outside, 40.0)]})
    assert distinct_only[0].kind == "new_cluster"


# ------------------------------------------------------------------------------------------------ new clusters


def test_a_chain_becomes_one_cluster_with_its_weakest_link() -> None:
    out = resolve_batch(plain(C, A, B), {}, [pair(A, B, 95.0), pair(B, C, 91.0)], {})
    assert out == [
        Resolution("new_cluster", (C, A, B), (), f"new:{C.text()}", None, 91.0, reason="new_cluster")
    ]


def test_a_lone_record_is_its_own_cluster() -> None:
    out = resolve_batch(plain(A, B), {}, [pair(A, B, 70.0)], {})
    assert [r.kind for r in out[:2]] == ["new_cluster", "new_cluster"]
    assert out[0].sources == (A,) and out[0].weakest is None
    assert out[0].cluster == "new:crm:C1"
    # the review-band pair between the two new clusters is a possible duplicate
    (duplicate,) = out[2:]
    assert duplicate.kind == "review" and duplicate.reason == "possible_duplicate"
    assert duplicate.clusters == ("new:crm:C1", "new:crm:C2") and duplicate.is_cluster_pair
    assert duplicate.sources == (A, B) and duplicate.master_ids == ()


def test_the_strongest_edge_wins_a_transitive_cannot_link() -> None:
    """★ A~B, B~C, A≠C by valid ID: A and C never share a cluster; the stronger edge decides B's side."""
    ids = {A: {("PERSON_REF", "111")}, C: {("PERSON_REF", "222")}}
    out = resolve_batch(plain(A, B, C, ids=ids), {}, [pair(A, B, 95.0), pair(B, C, 97.0)], {})
    clusters = [r for r in out if r.kind == "new_cluster"]
    assert [r.sources for r in clusters] == [(A,), (B, C)]
    (conflict,) = [r for r in out if r.is_cluster_pair]
    assert conflict.reason == "cannot_link_conflict"
    assert set(conflict.clusters) == {"new:crm:C1", "new:crm:C2"}
    assert set(conflict.sources) == {A, B}
    # the other way round: the stronger A~B keeps B with A
    swapped = resolve_batch(plain(A, B, C, ids=ids), {}, [pair(A, B, 97.0), pair(B, C, 95.0)], {})
    assert [r.sources for r in swapped if r.kind == "new_cluster"] == [(A, B), (C,)]


def test_the_same_id_on_both_sides_is_no_conflict() -> None:
    ids = {A: {("PERSON_REF", "111")}, C: {("PERSON_REF", "111")}, B: {("ORG_REG", "9")}}
    out = resolve_batch(plain(A, B, C, ids=ids), {}, [pair(A, B, 95.0), pair(B, C, 97.0)], {})
    assert [r.sources for r in out] == [(A, B, C)]


def test_one_shared_id_is_no_conflict_even_when_one_side_holds_another() -> None:
    """The union test agrees with the cannot-link rule: values in common mean no conflict."""
    ids = {A: {("PERSON_REF", "111"), ("PERSON_REF", "333")}, B: {("PERSON_REF", "111")}}
    out = resolve_batch(plain(A, B, ids=ids), {}, [pair(A, B, 95.0)], {})
    assert [r.sources for r in out] == [(A, B)]


def test_a_must_link_pair_joins_and_a_cannot_link_pair_does_not() -> None:
    joined = resolve_batch(plain(A, B), {}, [pair(A, B, 30.0, "must_link:person_ref")], {})
    assert [r.sources for r in joined] == [(A, B)]
    kept_apart = resolve_batch(plain(A, B), {}, [pair(A, B, 99.0, "cannot_link:person_ref")], {})
    assert [r.sources for r in kept_apart] == [(A,), (B,)]


def test_a_record_under_review_stays_out_of_new_clusters() -> None:
    """A review record is not clustered, and a record paired with it waits for it too."""
    goldens = {A: [golden("PER-1", A, H1, 70.0)]}
    out = resolve_batch(plain(A, B, C, D), goldens, [pair(A, B, 96.0), pair(B, C, 95.0)], {})
    by_first = {r.sources[0]: r for r in out}
    assert by_first[A].kind == "review" and by_first[A].reason == "review_band"
    assert by_first[B].kind == "review" and by_first[B].reason == "batch_candidate"
    assert by_first[B].review_with == (A, C)  # every pending record it pairs with, strongest first
    assert by_first[C].kind == "review" and by_first[C].review_with == (B,)  # repeated until nothing changes
    assert by_first[D].kind == "new_cluster" and by_first[D].sources == (D,)


def test_a_record_paired_with_a_linking_record_waits_instead_of_duplicating() -> None:
    goldens = {A: [golden("PER-1", A, H1, 99.0)]}
    out = resolve_batch(plain(A, B), goldens, [pair(A, B, 93.0)], {})
    assert out[0].kind == "link"
    assert out[1].kind == "review" and out[1].reason == "batch_candidate" and out[1].review_with == (A,)


def test_one_review_per_pair_of_clusters() -> None:
    out = resolve_batch(
        plain(A, B, C, D),
        {},
        [pair(A, B, 95.0), pair(C, D, 94.0), pair(A, C, 70.0), pair(B, D, 65.0), pair(D, A, 61.0)],
        {},
    )
    clusters = [r for r in out if r.kind == "new_cluster"]
    assert [r.sources for r in clusters] == [(A, B), (C, D)]
    reviews = [r for r in out if r.is_cluster_pair]
    assert len(reviews) == 1
    assert reviews[0].clusters == ("new:crm:C1", "new:crm:C3")
    assert reviews[0].best == x(70.0)  # the strongest pair between them


def test_output_is_in_input_order_and_independent_of_pair_order() -> None:
    inputs = plain(E, D, C, B, A)
    pairs = [pair(A, B, 95.0), pair(C, D, 92.0), pair(E, A, 60.0), pair(B, A, 91.0), pair(D, E, 99.0)]
    goldens = {C: [golden("PER-7", C, H1, 91.0)]}
    first = resolve_batch(inputs, goldens, pairs, {})
    for seed in range(5):
        shuffled = list(pairs)
        random.Random(seed).shuffle(shuffled)
        assert resolve_batch(inputs, goldens, shuffled, {}) == first
    firsts = [r.sources[0] for r in first if not r.is_cluster_pair]
    order = [s.source for s in inputs]
    assert firsts == sorted(firsts, key=order.index)


def test_every_record_has_exactly_one_resolution() -> None:
    rng = random.Random(41)
    sources = [key(f"crm:K{i:03d}") for i in range(60)]
    ids = {s: {("PERSON_REF", str(rng.randint(0, 5)))} for s in sources if rng.random() < 0.3}
    pairs = [
        pair(rng.choice(sources), rng.choice(sources), rng.choice([50.0, 65.0, 80.0, 91.0, 95.0, 99.0]))
        for _ in range(150)
    ]
    goldens = {s: [golden(f"PER-{i}", s, H1, rng.choice([70.0, 95.0]))] for i, s in enumerate(sources[:10])}
    out = resolve_batch(plain(*sources, ids=ids), goldens, pairs, {})
    seen = [s for r in out if not r.is_cluster_pair for s in r.sources]
    assert sorted(seen) == sorted(sources)
    for r in out:
        if r.kind == "new_cluster":
            schemes: dict[str, set[str]] = {}
            for s in r.sources:
                for scheme, value in ids.get(s, set()):
                    schemes.setdefault(scheme, set()).add(value)
            assert all(len(values) == 1 for values in schemes.values()), (
                "a cluster holds two IDs of one scheme"
            )


def test_duplicate_inputs_and_foreign_pairs_are_ignored() -> None:
    outside = key("crm:C99")
    out = resolve_batch(plain(A, A, B), {}, [pair(A, outside, 99.0), pair(A, A, 99.0), pair(A, B, 95.0)], {})
    assert out == [Resolution("new_cluster", (A, B), (), "new:crm:C1", None, 95.0, reason="new_cluster")]


# ------------------------------------------------------------------------------------------------ the quality breaker


def test_a_demoted_auto_link_waits_for_a_steward_naming_every_candidate() -> None:
    goldens = {A: [golden("PER-1", A, H1, 97.0), golden("PER-2", A, H2, 70.0)]}
    (only,) = resolve_batch(plain(A), goldens, [], {}, demoted=True)
    assert only == Resolution(
        "review", (A,), ("PER-1", "PER-2"), None, x(97.0), None, reason="breaker_demoted"
    )


def test_a_demoted_band_leaves_two_auto_goldens_ambiguous() -> None:
    goldens = {A: [golden("PER-1", A, H1, 93.0), golden("PER-2", A, H2, 99.0)]}
    (only,) = resolve_batch(plain(A), goldens, [], {}, demoted=True)
    assert only.kind == "ambiguous" and only.reason == "ambiguous_auto"


def test_an_outside_auto_partner_gives_the_breakers_reason_and_a_review_one_does_not() -> None:
    outside = key("crm:C99")
    (auto,) = resolve_batch(plain(A), {}, [], {A: [pair(A, outside, 97.0)]}, demoted=True)
    assert (auto.kind, auto.reason, auto.review_with) == ("review", "breaker_demoted", (outside,))
    (review,) = resolve_batch(plain(A), {}, [], {A: [pair(A, outside, 70.0)]}, demoted=True)
    assert review.reason == "unlinked_candidate"
    (normal,) = resolve_batch(plain(A), {}, [], {A: [pair(A, outside, 97.0)]})
    assert normal.reason == "unlinked_candidate"


def test_demoted_records_of_one_batch_wait_instead_of_joining() -> None:
    pairs = [pair(A, B, 97.0), pair(B, C, 70.0)]
    out = resolve_batch(plain(A, B, C, D), {}, pairs, {}, demoted=True)
    by_first = {r.sources[0]: r for r in out}
    assert (by_first[A].kind, by_first[A].reason, by_first[A].review_with) == (
        "review",
        "breaker_demoted",
        (B,),
    )
    assert (by_first[B].kind, by_first[B].reason, by_first[B].review_with) == (
        "review",
        "breaker_demoted",
        (A,),
    )
    assert (by_first[C].kind, by_first[C].reason) == ("review", "batch_candidate")  # waits with its partner
    assert by_first[D] == Resolution("new_cluster", (D,), (), "new:crm:C4", None, None, reason="new_cluster")


def _random_batch(seed: int) -> tuple[list[ClusterInput], dict, list[PairScore], dict]:
    rng = random.Random(seed)
    sources = [key(f"crm:R{seed:03d}{i:02d}") for i in range(rng.randint(1, 14))]
    ids = {s: {("PERSON_REF", str(rng.randint(0, 3)))} for s in sources if rng.random() < 0.3}
    scores = [40.0, 65.0, 80.0, 91.0, 95.0, 99.0]
    pairs = [
        pair(rng.choice(sources), rng.choice(sources), rng.choice(scores)) for _ in range(rng.randint(0, 20))
    ]
    goldens = {
        s: [
            golden(
                f"PER-{seed}{j}", s, H1 if j % 2 else H2, rng.choice(scores), rng.choice([None, None, "x"])
            )
            for j in range(rng.randint(0, 3))
        ]
        for s in sources
        if rng.random() < 0.5
    }
    outside = key("crm:OUTSIDE")
    unlinked = {s: [pair(s, outside, rng.choice(scores))] for s in sources if rng.random() < 0.2}
    return plain(*sources, ids=ids), goldens, pairs, unlinked


def test_the_breaker_never_widens_a_band_over_random_batches() -> None:
    task_kinds = {"ambiguous", "review"}
    for seed in range(500):
        inputs, goldens, pairs, unlinked = _random_batch(seed)
        normal = resolve_batch(inputs, goldens, pairs, unlinked)
        demoted = resolve_batch(inputs, goldens, pairs, unlinked, demoted=True)
        assert all(r.kind != "link" for r in demoted), seed
        assert all(len(r.sources) == 1 for r in demoted if r.kind == "new_cluster"), seed
        seen = sorted(s for r in demoted if not r.is_cluster_pair for s in r.sources)
        assert seen == sorted(i.source for i in inputs), seed
        after = {r.sources[0]: r for r in demoted if not r.is_cluster_pair}
        for before in normal:
            if before.is_cluster_pair:
                continue
            first = before.sources[0]
            if before.kind in task_kinds:
                assert after[first].kind in (before.kind, "review"), seed
            if before.kind == "link":
                assert (after[first].kind, after[first].reason) == ("review", "breaker_demoted"), seed
        paired = {s for p in pairs if p.left != p.right for s in (p.left, p.right)}
        for item in inputs:
            lonely = (
                item.source not in paired and not goldens.get(item.source) and not unlinked.get(item.source)
            )
            if lonely:
                assert after[item.source].kind == "new_cluster", seed
