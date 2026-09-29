"""Signature batches (story 3.3): the forced sample's size, strata and draw, the blind-review count, the
chunk packer and the split. Pure and deterministic: no input or output, no clock.

The forced sample is 5 + ⌊n/150⌋ of a batch's n reviews (both settings), shared out over strata (the
source-system pair of each record and its best candidate member) by largest remainder, with at least one
per stratum while the size allows, and drawn within each stratum by the smallest keyed draw values
(`engine.sample.draw_value`, which the service computes). So both engines draw the same reviews, and
drawing again picks the same reviews still open. A split takes the reviews whose record holds the same
match form on the named comparison as the disagreeing record's; the service reads the forms, and this
module only compares them.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: guards `review_count` against a product such as 0.02 × 50 landing a hair above a whole number
_EPSILON = 1e-9


def forced_sample_size(n: int, base: int, per: int) -> int:
    """The forced sample a batch of `n` reviews needs: `min(n, base + n // per)`, rounded down, so 612
    gives 9, 574 gives 8, 149 gives 5 and 150 gives 6. A batch whose `n` does not exceed it has nothing
    left to link together."""
    if n <= 0:
        return 0
    return min(n, base + n // per)


@dataclass(frozen=True, slots=True)
class Member:
    """A review a forced sample may draw: its task ID, its stratum ("crm/hr") and its keyed draw value."""

    task_id: str
    stratum: str
    draw: int


def allocate(counts: Mapping[str, int], size: int) -> dict[str, int]:
    """`size` shared out over the strata of `counts` (stratum -> reviews it holds).

    - A proportional share by largest remainder (exact whole-number arithmetic), remainders tied by
      stratum code.
    - Each stratum gets at least one while the size allows: when the size is smaller than the number of
      strata, the largest strata get one each first (ties by code); otherwise a stratum with none takes one
      from the stratum most over its share that holds more than one.
    - A stratum never gets more than it holds, and the size never exceeds the reviews there are.

    Every stratum of `counts` with a review is in the result, with 0 when it gets none."""
    strata = sorted((s for s, c in counts.items() if c > 0), key=lambda s: (-counts[s], s))
    out = {s: 0 for s in sorted(counts) if counts[s] > 0}
    total = sum(counts[s] for s in strata)
    size = max(0, min(size, total))
    if size == 0:
        return out
    if size <= len(strata):
        for stratum in strata[:size]:
            out[stratum] = 1
        return out
    # each stratum's quota is size × count ÷ total: kept as its numerator over `total`, so it is exact
    numerators = {s: size * counts[s] for s in strata}
    for stratum in strata:
        out[stratum] = numerators[stratum] // total
    left = size - sum(out.values())
    for stratum in sorted(strata, key=lambda s: (-(numerators[s] % total), s))[:left]:
        out[stratum] += 1
    for stratum in strata:  # the largest first
        if out[stratum] > 0:
            continue
        donors = [s for s in strata if out[s] > 1]
        # the donor most over its quota: the largest (share − quota) × total
        donor = min(donors, key=lambda s: (-(out[s] * total - numerators[s]), s))
        out[donor] -= 1
        out[stratum] = 1
    return out


def pick(members: Sequence[Member], size: int, held: Mapping[str, int]) -> list[str]:
    """The task IDs a forced sample of `size` draws from `members`, when `held` (stratum -> reviews) is
    already in the sample.

    - It allocates `size` over the strata of the members and of what is held (`allocate`), less what `held`
      already holds per stratum, so the strata that lack a member are filled first.
    - It fills each stratum's shortfall with its smallest draws, then any shortfall left with the smallest
      draws overall.
    - When the shortfalls ask for more than the sample lacks (a stratum holds more than its share now, as
      after a split), the strata that hold no member come first, the smallest draw of each, then the other
      shortfalls in draw order.
    - It returns task IDs in draw order (draw, then task ID), the same for any input order.
    """
    need = size - sum(max(0, n) for n in held.values())
    if need <= 0 or not members:
        return []
    ordered = sorted(members, key=lambda m: (m.draw, m.task_id))
    by_stratum: dict[str, list[Member]] = {}
    for member in ordered:
        by_stratum.setdefault(member.stratum, []).append(member)
    counts = {s: len(ms) for s, ms in by_stratum.items()}
    for stratum, n in held.items():
        counts[stratum] = counts.get(stratum, 0) + max(0, n)
    shares = allocate(counts, size)
    chosen: set[str] = set()
    for stratum in sorted(by_stratum):
        shortfall = shares.get(stratum, 0) - max(0, held.get(stratum, 0))
        for member in by_stratum[stratum][: max(0, shortfall)]:
            chosen.add(member.task_id)
    wanted = [m for m in ordered if m.task_id in chosen]
    lacking: dict[str, Member] = {}  # the smallest wanted draw of each stratum that holds no member yet
    for member in wanted:
        if max(0, held.get(member.stratum, 0)) == 0:
            lacking.setdefault(member.stratum, member)
    firsts = sorted(lacking.values(), key=lambda m: (m.draw, m.task_id))
    first_ids = {m.task_id for m in firsts}
    picked = (firsts + [m for m in wanted if m.task_id not in first_ids])[:need]
    if len(picked) < need:
        taken = {m.task_id for m in picked}
        picked += [m for m in ordered if m.task_id not in taken][: need - len(picked)]
    return [m.task_id for m in sorted(picked, key=lambda m: (m.draw, m.task_id))]


def review_count(n: int, share: float) -> int:
    """The batch links drawn for blind review: 0 when the share or `n` is 0, else ⌈share × n⌉ and at least
    one, so 566 at 2% gives 12, 50 gives 1 and 51 gives 2."""
    if n <= 0 or share <= 0:
        return 0
    return max(1, math.ceil(share * n - _EPSILON))


def pick_reviews(draws: Sequence[tuple[str, int]], count: int) -> frozenset[str]:
    """The `count` keys with the smallest draws ((key, draw) pairs; ties by key)."""
    if count <= 0:
        return frozenset()
    return frozenset(key for key, _ in sorted(draws, key=lambda d: (d[1], d[0]))[:count])


@dataclass(frozen=True, slots=True)
class PackItem:
    """One planned review for the packer: its key, its target, and its own published rows (1 for the
    cross-reference, plus an upper bound on its relationship rows). The target's golden row is counted once
    per chunk."""

    key: str
    target: str
    own_rows: int


def pack(items: Sequence[PackItem], max_rows: int, max_items: int) -> list[list[int]]:
    """The planned reviews in chunks, as lists of indexes into `items`, greedily in their order.

    A chunk counts each item's `own_rows`, plus 1 for each target new to the chunk, and never exceeds
    `max_rows` or `max_items`; its count is exact or high, never low. A single item over `max_rows` gets a
    chunk of its own (it cannot happen for a link, whose rows are at most 2 plus the reference attributes)."""
    chunks: list[list[int]] = []
    current: list[int] = []
    rows = 0
    targets: set[str] = set()
    for index, item in enumerate(items):
        cost = item.own_rows + (0 if item.target in targets else 1)
        if current and (rows + cost > max_rows or len(current) + 1 > max_items):
            chunks.append(current)
            current, rows, targets = [], 0, set()
            cost = item.own_rows + 1
        current.append(index)
        rows += cost
        targets.add(item.target)
    if current:
        chunks.append(current)
    return chunks


def _missing(value: Any) -> bool:
    return value is None or value == "" or (isinstance(value, (list, tuple)) and not value)


def split_members(forms: Mapping[str, Any], disagreeing: str) -> frozenset[str]:
    """The task IDs whose form equals the disagreeing task's form on the named comparison (the product owner's
    answer: the reviews that share the disagreeing record's value leave the batch). A missing form (absent,
    None, empty text or an empty list) matches only a missing one; the disagreeing task is always among
    them. The service passes the forms; nothing is read here."""
    theirs = forms.get(disagreeing)
    out = {disagreeing}
    for task_id, form in forms.items():
        if _missing(theirs):
            if _missing(form):
                out.add(task_id)
        elif not _missing(form) and form == theirs:
            out.add(task_id)
    return frozenset(out)
