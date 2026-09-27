"""The landing reader: every committed landing row read at least once, nothing stalls (owner: SERVICES, B.6.2).

State per reader: `H` the high-water mark (the highest landing sequence taken
into a batch), `L` the low-water mark (everything at or below it processed or
declared lost; for status only), and gap ranges `[lo, hi]`, `open` (missing,
within the timeout) or `lost` (missing past the timeout, re-probed daily until
the landing retention has passed).

- `next_batch(size)`: probe the open gaps first (rows found come first), then
  read above `H` with the room left; the numbers in `(H, top]` not returned
  become new gap ranges (a jump of a million numbers is one range); found
  numbers split the ranges they fall in.
- `record(batch)`: inside the intake transaction, `H` moves and the gaps are
  written, so the position moves only with the effect it stands for.
- `tick()`: open gaps older than the timeout become lost (logged at warning
  with the range and count); `L` = lowest open gap's `lo - 1`, else `H`.
- `reconcile()`: probe the lost ranges; drop those older than the retention.

Why nothing stalls: new rows are always read above `H`, never above `L`, so a
permanent gap costs one range row. Why nothing is lost: a row committed late
has a number inside a gap range, and open ranges are re-probed on every batch,
lost ones daily, until the integration platform may delete the row.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import mdm.capacity as capacity
from mdm.backend.store import SqlStore
from mdm.models.canonical import utcnow
from mdm.models.records import Gap, SourceChange
from mdm.models.safety import safe_message

log = logging.getLogger("mdm.landing")


@dataclass(frozen=True, slots=True)
class Batch:
    rows: tuple[SourceChange, ...]  # landing order
    high_water: int  # after this batch
    new_gaps: tuple[
        tuple[int, int], ...
    ]  # missing ranges between the old high-water mark and the top of the batch
    found: tuple[int, ...]  # numbers found inside open or lost gaps
    dropped: int = 0  # reconcile only: lost ranges dropped past the retention
    reconciled_at: datetime | None = None  # reconcile only: recorded with the batch

    @property
    def empty(self) -> bool:
        return not self.rows and not self.new_gaps and not self.found


@dataclass(frozen=True, slots=True)
class TickReport:
    high_water: int
    low_water: int
    gaps_open: int
    gaps_lost: int
    newly_lost: int  # open gaps declared lost by this tick


def missing_ranges(after: int, numbers: Sequence[int]) -> list[tuple[int, int]]:
    """The ranges in (after, max(numbers)] that `numbers` (ascending) leave out."""
    out: list[tuple[int, int]] = []
    previous = after
    for number in numbers:
        if number > previous + 1:
            out.append((previous + 1, number - 1))
        previous = max(previous, number)
    return out


def split_gap(gap: Gap, found: Iterable[int]) -> list[Gap]:
    """The pieces of `gap` left when the numbers `found` inside it are taken out (same state and times)."""
    pieces: list[Gap] = []
    start = gap.lo
    for number in sorted(n for n in set(found) if gap.lo <= n <= gap.hi):
        if number > start:
            pieces.append(Gap(start, number - 1, gap.state, gap.first_seen_at, gap.lost_at))
        start = number + 1
    if start <= gap.hi:
        pieces.append(Gap(start, gap.hi, gap.state, gap.first_seen_at, gap.lost_at))
    return pieces


class LandingReader:
    READER = "arrival"

    def __init__(
        self,
        store: SqlStore,
        *,
        reader: str = READER,
        gap_timeout: float = 600,
        retention_days: int = capacity.LANDING_RETENTION_DAYS,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.store = store
        self.reader = reader
        self.gap_timeout = float(gap_timeout)
        self.retention = timedelta(days=retention_days)
        self.clock = clock
        #: the gaps the last next_batch or reconcile probed, by lo: record() splits them
        self._probed: dict[int, Gap] = {}

    # ------------------------------------------------------------------ gaps

    def _gaps(self, state: str) -> Iterator[list[Gap]]:
        """Pages of the reader's gaps of one state, by lo."""
        yield from capacity.pages(
            lambda after, limit: self.store.gaps(self.reader, state, after, limit),
            key=lambda gap: gap.lo,
            page=capacity.READ_PAGE,
        )

    def _probe(self, gaps: Sequence[Gap], limit: int) -> list[SourceChange]:
        if not gaps or limit <= 0:
            return []
        for gap in gaps:
            self._probed[gap.lo] = gap
        return self.store.landing_in_ranges([(g.lo, g.hi) for g in gaps], limit)

    # ------------------------------------------------------------------ batches

    def next_batch(self, size: int) -> Batch:
        """Rows found in open gaps first, then new rows above H, at most `size` in all."""
        size = capacity.require_limit(size, capacity.BULK_BATCH * 10)
        self._probed = {}
        high = self.store.reader_state(self.reader).high_water
        found_rows: list[SourceChange] = []
        for page in self._gaps("open"):
            room = size - len(found_rows)
            if room <= 0:
                break
            found_rows.extend(self._probe(page, room))
        found_rows.sort(key=lambda c: c.landing_seq)
        room = size - len(found_rows)
        above = self.store.landing_above(high, room) if room > 0 else []
        top = above[-1].landing_seq if above else high
        new_gaps = missing_ranges(high, [c.landing_seq for c in above])
        return Batch(
            rows=(*found_rows, *above),
            high_water=max(high, top),
            new_gaps=tuple(new_gaps),
            found=tuple(c.landing_seq for c in found_rows),
        )

    def record(self, batch: Batch) -> None:
        """Writes the position and gaps; called inside the intake transaction."""
        now = self.clock()
        state = self.store.reader_state(self.reader)
        touched = self._gaps_holding(batch.found)
        upsert: list[Gap] = []
        delete: list[int] = []
        for gap in touched:
            delete.append(gap.lo)
            upsert.extend(split_gap(gap, batch.found))
        upsert.extend(Gap(lo, hi, "open", now, None) for lo, hi in batch.new_gaps)
        high = max(state.high_water, batch.high_water)
        self.store.save_reader(
            self.reader,
            high,
            min(state.low_water, high),
            upsert=upsert,
            delete=delete,
            reconciled_at=batch.reconciled_at,
        )

    def _gaps_holding(self, numbers: Sequence[int]) -> list[Gap]:
        """The stored gaps (open or lost) that hold any of `numbers`."""
        if not numbers:
            return []
        wanted = sorted(set(numbers))
        known = [g for g in self._probed.values() if any(g.lo <= n <= g.hi for n in wanted)]
        covered = {n for n in wanted for g in known if g.lo <= n <= g.hi}
        if len(covered) < len(wanted):  # a batch from another reader object: look the gaps up
            known = []
            for state in ("open", "lost"):
                for page in self._gaps(state):
                    known.extend(g for g in page if any(g.lo <= n <= g.hi for n in wanted))
        return sorted({g.lo: g for g in known}.values(), key=lambda g: g.lo)

    # ------------------------------------------------------------------ the tick and reconciliation

    def tick(self) -> TickReport:
        """Every run, even an empty one; its own transaction."""
        now = self.clock()
        timeout = timedelta(seconds=self.gap_timeout)
        with self.store.transaction():
            state = self.store.reader_state(self.reader)
            lost: list[Gap] = []
            lowest_open: int | None = None
            for page in self._gaps("open"):
                for gap in page:
                    if now - gap.first_seen_at >= timeout:
                        lost.append(Gap(gap.lo, gap.hi, "lost", gap.first_seen_at, now))
                    elif lowest_open is None:
                        lowest_open = gap.lo
            for gap in lost:
                log.warning(
                    safe_message(
                        "landing_gap_lost",
                        reader=self.reader,
                        lo=gap.lo,
                        hi=gap.hi,
                        count=gap.hi - gap.lo + 1,
                    )
                )
            low = lowest_open - 1 if lowest_open is not None else state.high_water
            self.store.save_reader(self.reader, state.high_water, low, upsert=lost)
        counts = {kind: sum(len(page) for page in self._gaps(kind)) for kind in ("open", "lost")}
        return TickReport(
            high_water=state.high_water,
            low_water=low,
            gaps_open=counts["open"],
            gaps_lost=counts["lost"],
            newly_lost=len(lost),
        )

    def due_for_reconcile(self) -> bool:
        """True when `reconciled_at` is older than RECONCILE_EVERY_HOURS (or never)."""
        reconciled = self.store.reader_state(self.reader).reconciled_at
        if reconciled is None:
            return True
        return self.clock() - reconciled >= timedelta(hours=capacity.RECONCILE_EVERY_HOURS)

    def reconcile(self, *, force: bool = False, limit: int = capacity.READ_PAGE) -> Batch:
        """Due every RECONCILE_EVERY_HOURS (or when forced, `mdm arrive --reconcile`).

        Probes the lost ranges; the rows found come back as a batch for intake (its `record` splits their
        ranges and stamps `reconciled_at`); a lost range past the landing retention with nothing found is
        dropped now, counted in `dropped`. With nothing found, the reconciliation is recorded here.
        """
        self._probed = {}
        state = self.store.reader_state(self.reader)
        if not force and not self.due_for_reconcile():
            return Batch((), state.high_water, (), ())
        now = self.clock()
        rows: list[SourceChange] = []
        expired: list[Gap] = []
        for page in self._gaps("lost"):
            room = limit - len(rows)
            if room > 0:
                rows.extend(self._probe(page, room))
            expired.extend(g for g in page if g.lost_at is not None and now - g.lost_at >= self.retention)
        rows.sort(key=lambda c: c.landing_seq)
        found = [c.landing_seq for c in rows]
        dropped = [g for g in expired if not any(g.lo <= n <= g.hi for n in found)]
        for gap in dropped:
            log.warning(
                safe_message(
                    "landing_gap_dropped", reader=self.reader, lo=gap.lo, hi=gap.hi, count=gap.hi - gap.lo + 1
                )
            )
        with self.store.transaction():
            current = self.store.reader_state(self.reader)
            self.store.save_reader(
                self.reader,
                current.high_water,
                current.low_water,
                delete=[g.lo for g in dropped],
                reconciled_at=None if rows else now,
            )
        return Batch(
            rows=tuple(rows),
            high_water=state.high_water,
            new_gaps=(),
            found=tuple(found),
            dropped=len(dropped),
            reconciled_at=now if rows else None,
        )

    # ------------------------------------------------------------------ position

    @property
    def high_water(self) -> int:
        return self.store.reader_state(self.reader).high_water

    @property
    def low_water(self) -> int:
        return self.store.reader_state(self.reader).low_water
