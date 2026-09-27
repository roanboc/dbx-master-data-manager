"""Clustering a batch of new records against golden records and each other (owner: ENGINE, B.9.6).

1. Each new record, in input (landing) order: auto-band golden candidates not
   blocked -> one: `link`; two or more: `ambiguous` (merge is never automatic).
   A blocked auto candidate counts as a review (`cannot_link_conflict`).
2. No linkable auto golden but a review-band or blocked golden, or a review or
   auto pair with an unlinked record outside the batch: `review`. A record left
   over from step 1 with a review or auto pair to a batch record resolved by
   steps 1–2 is a review too (`batch_candidate`, `review_with` naming every
   such record), repeated until nothing changes: that record is linked or
   pending once this batch commits, which is exactly the case step 1 or the
   unlinked rule would see in the next batch.
3. The rest: union-find over their auto-band pairs, strongest first (score
   descending, then (left, right)); a union whose components hold the same
   scheme with different valid values is refused. Each component ->
   `new_cluster`, members in input order, `cluster = "new:" + first member`,
   `weakest` = the lowest union edge score.
4. A refused union -> both clusters and a `review` (`cannot_link_conflict`);
   a review-band pair between two new clusters -> a `review` with reason
   `possible_duplicate`. Both are cluster-pair resolutions: `clusters` names
   the two new clusters, `sources` the pair's two records, `master_ids` is
   empty; the caller raises the task once the clusters have master IDs, and
   never plans them as records (every record already has its own resolution).

Resolutions come in input order of their first record; the cluster-pair
resolutions follow, in the order of their clusters. Reason codes: `auto_band`,
`ambiguous_auto`, `review_band`, `cannot_link_conflict`, `unlinked_candidate`,
`batch_candidate`, `new_cluster`, `possible_duplicate`. Pure.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from mdm.models.match import Band, Explanation, GoldenCandidate, PairScore
from mdm.models.records import SourceKey

RESOLUTION_KINDS = ("link", "ambiguous", "review", "new_cluster")
REASONS = (
    "auto_band",
    "ambiguous_auto",
    "review_band",
    "cannot_link_conflict",
    "unlinked_candidate",
    "batch_candidate",
    "new_cluster",
    "possible_duplicate",
)
_PAIRED = (Band.AUTO, Band.REVIEW)


@dataclass(frozen=True, slots=True)
class ClusterInput:
    source: SourceKey
    strong_ids: frozenset[tuple[str, str]]  # (scheme, valid value)


@dataclass(frozen=True, slots=True)
class Resolution:
    kind: str  # link | ambiguous | review | new_cluster
    sources: tuple[SourceKey, ...]
    master_ids: tuple[str, ...]
    cluster: str | None
    best: Explanation | None
    weakest: float | None
    review_with: tuple[SourceKey, ...] = ()
    reason: str = ""  # a code: review_band, cannot_link_conflict, ambiguous_auto, …
    clusters: tuple[str, ...] = ()  # a cluster-pair review: the two new clusters it names

    @property
    def is_cluster_pair(self) -> bool:
        """A review between two new clusters (step 4), raised after they have master IDs."""
        return bool(self.clusters)


def _by_score(candidate: GoldenCandidate) -> tuple[float, str]:
    return (-candidate.best.explanation.score, candidate.master_id)


def _pair_order(pair: PairScore) -> tuple[float, SourceKey, SourceKey]:
    left, right = sorted((pair.left, pair.right))
    return (-pair.explanation.score, left, right)


class _Components:
    """Union-find over the records left for step 3, carrying each component's (scheme, value) set."""

    def __init__(self, members: Sequence[ClusterInput]) -> None:
        self.parent = {m.source: m.source for m in members}
        self.ids = {m.source: {scheme: {value} for scheme, value in m.strong_ids} for m in members}
        self.weakest: dict[SourceKey, float | None] = {m.source: None for m in members}

    def find(self, source: SourceKey) -> SourceKey:
        root = source
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[source] != root:
            self.parent[source], source = root, self.parent[source]
        return root

    def conflict(self, a: SourceKey, b: SourceKey) -> bool:
        left, right = self.ids[a], self.ids[b]
        return any(scheme in right and right[scheme] != values for scheme, values in left.items())

    def union(self, a: SourceKey, b: SourceKey, score: float) -> None:
        # the smaller root (by source key) stays the root, so the result never depends on dict order
        root, other = (a, b) if a < b else (b, a)
        self.parent[other] = root
        for scheme, values in self.ids.pop(other).items():
            self.ids[root].setdefault(scheme, set()).update(values)
        edges = [w for w in (self.weakest.pop(other), self.weakest[root], score) if w is not None]
        self.weakest[root] = min(edges)


def resolve_batch(
    new: Sequence[ClusterInput],
    golden: Mapping[SourceKey, Sequence[GoldenCandidate]],
    among_new: Sequence[PairScore],
    unlinked: Mapping[SourceKey, Sequence[PairScore]],
) -> list[Resolution]:
    inputs: list[ClusterInput] = []
    position: dict[SourceKey, int] = {}
    for item in new:
        if item.source not in position:
            position[item.source] = len(inputs)
            inputs.append(item)

    # pairs among the batch, per record (each pair once, both endpoints in the batch, not a self pair)
    pairs: dict[tuple[SourceKey, SourceKey], PairScore] = {}
    for pair in among_new:
        if pair.left == pair.right or pair.left not in position or pair.right not in position:
            continue
        key = (pair.left, pair.right) if pair.left < pair.right else (pair.right, pair.left)
        held = pairs.get(key)
        if held is None or pair.explanation.score > held.explanation.score:
            pairs[key] = pair
    neighbours: dict[SourceKey, list[tuple[SourceKey, PairScore]]] = {s: [] for s in position}
    for (a, b), pair in sorted(pairs.items(), key=lambda item: _pair_order(item[1])):
        neighbours[a].append((b, pair))
        neighbours[b].append((a, pair))

    resolved: dict[SourceKey, Resolution] = {}

    # steps 1 and 2 against golden records and unlinked records
    for item in inputs:
        source = item.source
        candidates = sorted(golden.get(source, ()), key=_by_score)
        auto = [g for g in candidates if g.best.explanation.band == Band.AUTO]
        linkable = [g for g in auto if not g.blocked_by]
        blocked = [g for g in auto if g.blocked_by]
        review = [g for g in candidates if g.best.explanation.band == Band.REVIEW]
        outside = sorted(
            (p for p in unlinked.get(source, ()) if p.explanation.band in _PAIRED),
            key=_pair_order,
        )
        outside_with = tuple(dict.fromkeys(p.right if p.left == source else p.left for p in outside))
        if len(linkable) == 1:
            chosen = linkable[0]
            resolved[source] = Resolution(
                "link",
                (source,),
                (chosen.master_id,),
                None,
                chosen.best.explanation,
                None,
                reason="auto_band",
            )
        elif len(linkable) > 1:
            resolved[source] = Resolution(
                "ambiguous",
                (source,),
                tuple(g.master_id for g in linkable),
                None,
                linkable[0].best.explanation,
                None,
                reason="ambiguous_auto",
            )
        elif blocked or review or outside:
            held = sorted([*blocked, *review], key=_by_score)
            best = held[0].best.explanation if held else outside[0].explanation
            reason = "cannot_link_conflict" if blocked else "review_band" if review else "unlinked_candidate"
            resolved[source] = Resolution(
                "review",
                (source,),
                tuple(g.master_id for g in held),
                None,
                best,
                None,
                review_with=outside_with,
                reason=reason,
            )

    # step 2, inside the batch: a record paired with one resolved above waits too, until nothing changes
    def paired(source: SourceKey, among: Collection[SourceKey]) -> list[tuple[SourceKey, PairScore]]:
        return [
            (other, pair)
            for other, pair in neighbours[source]
            if other in among and pair.explanation.band in _PAIRED
        ]

    waiting: set[SourceKey] = set()
    changed = True
    while changed:
        changed = False
        for item in inputs:
            source = item.source
            if source in resolved or source in waiting:
                continue
            if paired(source, resolved) or paired(source, waiting):
                waiting.add(source)
                changed = True
    for item in inputs:
        source = item.source
        if source not in waiting:
            continue
        touching = sorted(
            [*paired(source, resolved), *paired(source, waiting)], key=lambda found: _pair_order(found[1])
        )
        resolved[source] = Resolution(
            "review",
            (source,),
            (),
            None,
            touching[0][1].explanation,
            None,
            review_with=tuple(dict.fromkeys(other for other, _pair in touching)),
            reason="batch_candidate",
        )

    # step 3: union-find over the rest, strongest edge first; a union joining two IDs of one scheme is refused
    rest = [item for item in inputs if item.source not in resolved]
    components = _Components(rest)
    in_rest = {item.source for item in rest}
    refused: list[tuple[SourceKey, SourceKey, PairScore]] = []
    for (a, b), pair in sorted(pairs.items(), key=lambda item: _pair_order(item[1])):
        if a not in in_rest or b not in in_rest or pair.explanation.band != Band.AUTO:
            continue
        root_a, root_b = components.find(a), components.find(b)
        if root_a == root_b:
            continue
        if components.conflict(root_a, root_b):
            refused.append((a, b, pair))
            continue
        components.union(root_a, root_b, pair.explanation.score)

    members: dict[SourceKey, list[SourceKey]] = {}
    for item in rest:
        members.setdefault(components.find(item.source), []).append(item.source)
    cluster_of: dict[SourceKey, str] = {}
    first_index: dict[str, int] = {}
    cluster_resolution: dict[SourceKey, Resolution] = {}
    for root, sources in members.items():
        name = "new:" + sources[0].text()
        first_index[name] = position[sources[0]]
        for source in sources:
            cluster_of[source] = name
        cluster_resolution[sources[0]] = Resolution(
            "new_cluster",
            tuple(sources),
            (),
            name,
            None,
            components.weakest[root],
            reason="new_cluster",
        )

    out: list[Resolution] = []
    for item in inputs:
        if item.source in resolved:
            out.append(resolved[item.source])
        elif item.source in cluster_resolution:
            out.append(cluster_resolution[item.source])

    # step 4: one review per pair of new clusters, a refused union before a review-band pair
    reviews: dict[tuple[str, str], Resolution] = {}
    candidates: list[tuple[SourceKey, SourceKey, PairScore, str]] = [
        (a, b, pair, "cannot_link_conflict") for a, b, pair in refused
    ]
    for (a, b), pair in sorted(pairs.items(), key=lambda item: _pair_order(item[1])):
        if a in cluster_of and b in cluster_of and pair.explanation.band == Band.REVIEW:
            candidates.append((a, b, pair, "possible_duplicate"))
    for a, b, pair, reason in candidates:
        one, two = cluster_of[a], cluster_of[b]
        if one == two:
            continue
        if first_index[one] > first_index[two]:
            one, two, a, b = two, one, b, a
        if (one, two) in reviews:
            continue
        reviews[(one, two)] = Resolution(
            "review",
            (a, b),
            (),
            None,
            pair.explanation,
            None,
            reason=reason,
            clusters=(one, two),
        )
    out.extend(reviews[key] for key in sorted(reviews, key=lambda k: (first_index[k[0]], first_index[k[1]])))
    return out
