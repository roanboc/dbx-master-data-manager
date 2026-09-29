"""The quality breaker: it demotes an entity's automatic band, and only a data owner restores it.

Actor `ACT8` (decision 3). Two triggers, each computing its own figures from the store: agreement, when the
one-sided upper bound on the agreement of the latest blind reviews of automatic links (those first decided
since the last restore) falls below the threshold; and volume, when the arrivals of the current clock hour
exceed a multiple of the same hour's mean over the previous days. A trip writes the demoted state and an audit
change set by the quality breaker in one transaction. While the band is demoted, arrival turns every record
the automatic band would have linked into a review (`breaker_demoted`), and the commit refuses an automatic
link. A data owner restores the band with `mdm breaker restore` and a reason code; arrival then hands the
waiting records back.

A signature's bulk rights (story 3.3, reading 14) live beside the automatic band, one row per entity and
signature under a band `bulk:<16 hex>`. Only blind review of that signature's batch samples withdraws them:
after a blind answer on a batch sample commits, `check_signature` reads the latest `bulk_window` reviewed batch
samples of the signature first decided since its last restore, and withdraws the rights when at least
`bulk_min_samples` are in and the one-sided 95% upper bound on their agreement is below `bulk_agreement`. A
volume spike never touches them, and an automatic-band demotion leaves them alone (decision 3). Only a data
owner restores them, on the command line, and only samples decided later count. Every read here is keyed on
the entity and the band, so an entity's many bulk rows never slow the automatic band's reads.

The breaker only reduces automation: no method here sets a band, a threshold or a rule set, and nothing but a
restore lifts a demotion. Its thresholds are settings until the governance policy of initiative 4 holds them.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from mdm import capacity
from mdm.backend.store import SqlStore
from mdm.config import Settings
from mdm.engine.sample import agreement_upper, below
from mdm.models.authority import QUALITY_BREAKER, Actor, Authority
from mdm.models.canonical import iso, utcnow
from mdm.models.changes import new_change_set
from mdm.models.errors import Conflict, Forbidden, NotFound, PlatformRefused
from mdm.models.quality import (
    AUTO_BAND,
    BULK_BAND_RE,
    BULK_PREFIX,
    BULK_RESTORE_REASONS,
    RESTORE_REASONS,
    BreakerState,
    BreakerStatus,
    BulkStatus,
    bulk_band,
)
from mdm.models.safety import safe_detail, safe_message
from mdm.models.workbench import BreakerView, BulkRightView, TaskQuery
from mdm.services.authority import require
from mdm.services.registry import ModelRegistry
from mdm.services.support import token

log = logging.getLogger("mdm.breaker")

#: the reason each trigger's audit change set carries
TRIP_REASONS: Mapping[str, str] = {"agreement": "agreement_low", "volume": "volume_spike"}
#: the reason a withdrawal of a signature's bulk rights carries
BULK_TRIP_REASON = "bulk_agreement_low"
#: the figures the workbench may show of a withdrawal: safe numbers only
BULK_FIGURES = ("agreed", "reviewed", "threshold", "window")
#: the figures the workbench may show of each trigger: safe numbers only
VIEW_FIGURES: Mapping[str, tuple[str, ...]] = {
    "agreement": ("agreed", "reviewed", "threshold"),
    "volume": ("arrivals", "mean", "multiple", "days", "hour"),
}


def floor_hour(moment: datetime) -> datetime:
    """The start of the clock hour `moment` falls in."""
    return moment.replace(minute=0, second=0, microsecond=0)


class BreakerService:
    """Checks the two triggers, trips a band, restores it, and says what it did; never widens anything."""

    def __init__(
        self,
        settings: Settings,
        store: SqlStore,
        registry: ModelRegistry,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        """Wires the service; reads nothing from the store (`mdm init` wires the hub before the schema)."""
        self.settings = settings
        self.store = store
        self.registry = registry
        self.clock = clock

    # ------------------------------------------------------------------ reads

    def state(self, entity: str) -> BreakerState | None:
        """The entity's automatic band as stored; None when no row was written yet (a normal band). One keyed
        read of the automatic band's row, never the entity's bulk rows."""
        return self.store.breaker_state(entity, AUTO_BAND)

    # ------------------------------------------------------------------ a signature's bulk rights (story 3.3)

    def bulk(self, entity: str, signature: str) -> BreakerState | None:
        """The signature's bulk-rights row as stored; None when none was written yet (the rights are held)."""
        if not signature:
            return None
        return self.store.breaker_state(entity, bulk_band(entity, signature))

    def bulk_withdrawn(self, entity: str, signature: str) -> BulkRightView | None:
        """What the workbench says of a signature whose bulk rights are withdrawn; None while they are held."""
        found = self.bulk(entity, signature)
        return self._bulk_view(found) if found is not None and found.demoted else None

    @staticmethod
    def _bulk_view(found: BreakerState) -> BulkRightView:
        figures = {k: found.figures[k] for k in BULK_FIGURES if k in found.figures}
        return BulkRightView(
            entity=found.entity,
            key=found.band,
            since=found.tripped_at or found.updated_at,
            figures=safe_detail(**figures),
        )

    def bulk_agreement(self, entity: str, signature: str) -> tuple[int, int]:
        """(agreed, reviewed) of the latest `bulk_window` reviewed batch samples of the signature, first decided
        since its last restore."""
        found = self.bulk(entity, signature)
        after = found.restored_at if found is not None else None
        rows = self.store.recent_reviews_of_signature(
            entity, "batch", signature, after, self.settings.bulk_window
        )
        return sum(1 for status, _, _ in rows if status == "agreed"), len(rows)

    def check_signature(self, entity: str, signature: str | None) -> BreakerState | None:
        """After a blind answer on a batch sample commits: withdraws the signature's bulk rights when at least
        `bulk_min_samples` of its latest `bulk_window` reviewed batch samples are in and the upper bound on
        their agreement is below `bulk_agreement` (reading 14). Returns the withdrawn state when it withdrew
        them now. Steward and automated samples of the signature never count: they measure other decisions."""
        if not signature:
            return None
        band = bulk_band(entity, signature)
        found = self.store.breaker_state(entity, band)
        if found is not None and found.demoted:
            return None
        agreed, reviewed = self.bulk_agreement(entity, signature)
        threshold = self.settings.bulk_agreement
        if reviewed < self.settings.bulk_min_samples or not below(
            agreed, reviewed, threshold, capacity.BREAKER_Z
        ):
            return None
        figures = {
            "agreed": agreed,
            "reviewed": reviewed,
            "threshold": threshold,
            "window": self.settings.bulk_window,
            "bound": round(agreement_upper(agreed, reviewed, capacity.BREAKER_Z), 3),
        }
        return self._trip(entity, "agreement", figures, band=band, signature=signature)

    def _withdrawn_rows(self, entity: str) -> list[BreakerState]:
        """One entity's withdrawn bulk rows, keyset-paged by band."""
        out: list[BreakerState] = []
        for page in capacity.pages(
            lambda after, limit: self.store.withdrawn_bulk(entity, after, limit), key=lambda b: b.band
        ):
            out.extend(page)
        return out

    def withdrawn(self, entities: Sequence[str]) -> tuple[BulkRightView, ...]:
        """Every signature whose bulk rights are withdrawn, by entity and key: paged per entity."""
        return tuple(
            self._bulk_view(found)
            for entity in sorted(set(entities))
            for found in self._withdrawn_rows(entity)
        )

    def demo_withdraw(
        self, entity: str, group_key: str, *, figures: Mapping[str, Any] | None = None
    ) -> BreakerState | None:
        """Withdraws a signature group's bulk rights on a local store, as a trip would, marked `demo` in the
        audit: the browser checks and the tests only. The signature is read from the group's first open
        review. `PlatformRefused(demo_only)` on a shared store, `NotFound(unknown_group)` for a group with no
        open review."""
        if not self.settings.local_mode:
            raise PlatformRefused("demo_only")
        self._published(entity)
        first = self.store.group_members(group_key, None, 1)
        if not first or first[0].entity != entity or not first[0].signature:
            raise NotFound("unknown_group", group=token(group_key))
        signature = first[0].signature
        shown = dict(figures or {"agreed": 3, "reviewed": 5, "threshold": self.settings.bulk_agreement})
        shown.setdefault("window", self.settings.bulk_window)
        return self._trip(
            entity,
            "agreement",
            safe_detail(**shown),
            demo=True,
            band=bulk_band(entity, signature),
            signature=signature,
        )

    def demoted(self, entity: str) -> BreakerState | None:
        """The entity's automatic band when the breaker demoted it, else None: one keyed read."""
        found = self.state(entity)
        return found if found is not None and found.demoted else None

    def paused(self, entities: Sequence[str]) -> tuple[BreakerView, ...]:
        """What the workbench says about each demoted band, by entity: the trigger, since when, safe numbers."""
        if not entities:
            return ()
        states = self.store.breaker_states(sorted(set(entities)))
        out: list[BreakerView] = []
        for (entity, band), found in sorted(states.items()):
            if band != AUTO_BAND or not found.demoted:
                continue
            trigger = found.trigger or "agreement"
            figures = {k: found.figures[k] for k in VIEW_FIGURES.get(trigger, ()) if k in found.figures}
            out.append(
                BreakerView(
                    entity=entity,
                    trigger=trigger,
                    since=found.tripped_at or found.updated_at,
                    figures=safe_detail(**figures),
                )
            )
        return tuple(out)

    # ------------------------------------------------------------------ the volume trigger

    def count_arrivals(self, counts: Mapping[str, int], now: datetime) -> None:
        """Inside arrival's intake transaction: a breaker row for each entity (watched from `now` when new), and
        the records queued with a new event outside an initial load added to the current clock hour."""
        if not counts:
            return
        self.store.ensure_breaker_rows(sorted(counts), now)
        hour = floor_hour(now)
        self.store.add_arrivals([(entity, hour, n) for entity, n in sorted(counts.items()) if n > 0])

    def volume(self, entity: str, now: datetime) -> tuple[int, float, bool, datetime]:
        """(arrivals this hour, the same hour's mean over the previous days, whether those days of history are
        there, the hour) of one entity. The history is there once the row has been watched that many days
        (since it was written, or since a restore of a volume trip for an expected load) and arrivals were
        counted that long ago: days in which only an initial load arrived count as none."""
        hour = floor_hour(now)
        days = self.settings.breaker_spike_days
        earlier = [hour - timedelta(days=k) for k in range(1, days + 1)]
        counts = self.store.arrival_hours(entity, [hour, *earlier])
        current = counts.get(hour, 0)
        mean = sum(counts.get(h, 0) for h in earlier) / days
        found = self.state(entity)
        ready = (
            found is not None
            and now - found.watch_since >= timedelta(days=days)
            and self.store.arrivals_counted_by(entity, earlier[-1])
        )
        return current, mean, ready, hour

    @staticmethod
    def resting(found: BreakerState, hour: datetime) -> bool:
        """The rest of the clock hour in which a data owner restored a volume trip (for a fixed cause or a
        false alarm): the hour that tripped the band is not counted against it twice."""
        return (
            found.trigger == "volume"
            and found.restored_at is not None
            and found.restore_reason != "load_expected"
            and hour <= floor_hour(found.restored_at)
        )

    def check_volume(self, entity: str, now: datetime) -> BreakerState | None:
        """Trips the band when the current hour holds at least `breaker_spike_min` arrivals and more than
        `breaker_spike_multiple` times the same hour's mean over the previous `breaker_spike_days` days, once
        that much history is there (`volume`), and never in the hour a volume trip was restored. Prunes
        counts older than `ARRIVAL_HOURS_KEPT` hours. Returns the demoted state when it tripped now."""
        self.store.prune_arrival_hours(floor_hour(now) - timedelta(hours=capacity.ARRIVAL_HOURS_KEPT))
        found = self.state(entity)
        if found is None or found.demoted or self.resting(found, floor_hour(now)):
            return None
        current, mean, ready, hour = self.volume(entity, now)
        multiple = self.settings.breaker_spike_multiple
        if not ready or current < self.settings.breaker_spike_min or current <= multiple * mean:
            return None
        figures = {
            "arrivals": current,
            "mean": round(mean, 1),
            "multiple": multiple,
            "days": self.settings.breaker_spike_days,
            "hour": iso(hour),
        }
        return self._trip(entity, "volume", figures)

    # ------------------------------------------------------------------ the agreement trigger

    def agreement(self, entity: str) -> tuple[int, int]:
        """(agreed, reviewed) of the latest reviews of automatic links, at most `breaker_window`, first decided
        since the last restore."""
        found = self.state(entity)
        after = found.restored_at if found is not None else None
        rows = self.store.recent_reviews(entity, "automated", AUTO_BAND, after, self.settings.breaker_window)
        return sum(1 for status, _, _ in rows if status == "agreed"), len(rows)

    def check_agreement(self, entity: str) -> BreakerState | None:
        """Trips the band when at least `breaker_min_samples` of the latest `breaker_window` reviews of
        automatic links are in and the upper bound on their agreement is below `breaker_agreement`. Returns
        the demoted state when it tripped now."""
        if self.demoted(entity) is not None:
            return None
        agreed, reviewed = self.agreement(entity)
        threshold = self.settings.breaker_agreement
        if reviewed < self.settings.breaker_min_samples or not below(
            agreed, reviewed, threshold, capacity.BREAKER_Z
        ):
            return None
        figures = {
            "agreed": agreed,
            "reviewed": reviewed,
            "threshold": threshold,
            "window": self.settings.breaker_window,
            "bound": round(agreement_upper(agreed, reviewed, capacity.BREAKER_Z), 3),
        }
        return self._trip(entity, "agreement", figures)

    # ------------------------------------------------------------------ trip and restore

    def _trip(
        self,
        entity: str,
        trigger: str,
        figures: Mapping[str, Any],
        *,
        demo: bool = False,
        band: str = AUTO_BAND,
        signature: str | None = None,
    ) -> BreakerState | None:
        """Demotes the band (the automatic band, or a signature's bulk rights) and audits it in one transaction,
        as the quality breaker; None when it was demoted already. Only the checks (and the local demos) call
        it: no caller supplies a figure. A withdrawal of bulk rights carries reason `bulk_agreement_low`, and
        the band's key and the figures as evidence, never the signature."""
        require(QUALITY_BREAKER, "trip_breaker")
        now = self.clock()
        bulk = band.startswith(BULK_PREFIX)
        evidence = safe_detail(band=band, **figures, **({"demo": True} if demo else {}))
        cs = new_change_set(
            entity,
            "breaker_trip",
            QUALITY_BREAKER,
            Authority("role", QUALITY_BREAKER.role),
            (),
            planning_version=self.store.last_commit_version(),
            reason=BULK_TRIP_REASON if bulk else TRIP_REASONS[trigger],
            evidence=evidence,
        )
        with self.store.transaction():
            self.store.ensure_breaker_rows([entity], now, band, signature)
            if not self.store.trip_breaker(
                entity, band, trigger, safe_detail(**figures), now, cs.change_set_id
            ):
                return None
            self.store.append_change_set(cs, None, 0)
        if bulk:
            log.warning(safe_message("bulk_withdrawn", entity=token(entity), band=band))
        else:
            log.warning(safe_message("breaker_tripped", entity=token(entity), trigger=trigger))
        return self.store.breaker_state(entity, band)

    def demo_trip(
        self, entity: str, *, figures: Mapping[str, Any], trigger: str = "agreement"
    ) -> BreakerState | None:
        """Trips the band on a local store with the figures given, marked `demo` in the audit: the browser
        checks and the tests only. `PlatformRefused(demo_only)` on a shared store."""
        if not self.settings.local_mode:
            raise PlatformRefused("demo_only")
        if trigger not in TRIP_REASONS:
            raise Forbidden("bad_trigger", trigger=token(trigger))
        self._published(entity)
        return self._trip(entity, trigger, safe_detail(**figures), demo=True)

    def _published(self, entity: str) -> None:
        try:
            self.registry.published(entity)
        except NotFound:
            raise NotFound("unknown_entity", entity=token(entity)) from None

    def restore(self, entity: str, *, actor: Actor, reason: str, bulk: str | None = None) -> BreakerState:
        """A data owner restores the demoted band with a reason code (`RESTORE_REASONS`): the state and an
        audit change set by that person in one transaction. The volume trigger keeps its history, except after
        a volume trip restored for an expected load, when it is watched afresh from now so the load becomes
        its history; after any other volume restore it rests for the rest of the hour. Nothing else changes:
        no rule set, no band. Arrival hands the records that waited back on its next run. `Forbidden` for
        another role or a bad code, `NotFound(unknown_entity)`, `Conflict(not_demoted)`.

        With `bulk`, a signature's bulk rights (`bulk:<16 hex>`, else `Forbidden(bad_bulk_key)`) are restored
        instead, for `cause_fixed` or `false_alarm` only (`load_expected` is `Forbidden(bad_restore_reason)`):
        a `breaker_restore` change set naming the band and the trip, and only samples decided later count."""
        require(actor, "restore_breaker")
        if bulk is not None:
            return self._restore_bulk(entity, bulk, actor=actor, reason=reason)
        if reason not in RESTORE_REASONS:
            raise Forbidden("bad_restore_reason")
        self._published(entity)
        found = self.demoted(entity)
        if found is None:
            raise Conflict([token(entity)], code="not_demoted")
        now = self.clock()
        cs = new_change_set(
            entity,
            "breaker_restore",
            actor,
            Authority("role", actor.role),
            (),
            planning_version=self.store.last_commit_version(),
            reason=reason,
            evidence=safe_detail(band=AUTO_BAND, trip_change_set=found.trip_change_set),
        )
        with self.store.transaction():
            if not self.store.restore_breaker(
                entity,
                AUTO_BAND,
                actor.name,
                actor.role,
                reason,
                now,
                cs.change_set_id,
                rewatch=found.trigger == "volume" and reason == "load_expected",
            ):
                raise Conflict([token(entity)], code="not_demoted")
            self.store.append_change_set(cs, None, 0)
        log.warning(safe_message("breaker_restored", entity=token(entity), reason=reason))
        restored = self.state(entity)
        assert restored is not None
        return restored

    def _restore_bulk(self, entity: str, bulk: str, *, actor: Actor, reason: str) -> BreakerState:
        if not BULK_BAND_RE.match(bulk):
            raise Forbidden("bad_bulk_key")
        if reason not in BULK_RESTORE_REASONS:
            raise Forbidden("bad_restore_reason")
        self._published(entity)
        found = self.store.breaker_state(entity, bulk)
        if found is None or not found.demoted:
            raise Conflict([token(entity), bulk], code="not_demoted")
        now = self.clock()
        cs = new_change_set(
            entity,
            "breaker_restore",
            actor,
            Authority("role", actor.role),
            (),
            planning_version=self.store.last_commit_version(),
            reason=reason,
            evidence=safe_detail(band=bulk, trip_change_set=found.trip_change_set),
        )
        with self.store.transaction():
            if not self.store.restore_breaker(
                entity, bulk, actor.name, actor.role, reason, now, cs.change_set_id
            ):
                raise Conflict([token(entity), bulk], code="not_demoted")
            self.store.append_change_set(cs, None, 0)
        log.warning(safe_message("bulk_restored", entity=token(entity), band=bulk, reason=reason))
        restored = self.store.breaker_state(entity, bulk)
        assert restored is not None
        return restored

    # ------------------------------------------------------------------ what `mdm breaker status` prints

    def status(self, entities: Sequence[str], *, actor: Actor) -> list[BreakerStatus]:
        """Per entity: the state with its last trip and restore; the agreement of the latest reviews of
        automatic links with its bound; quality samples open, overdue and voided (capped); this hour's
        arrivals against the same hour's mean; and since when the volume is watched. `view_breaker`."""
        require(actor, "view_breaker")
        now = self.clock()
        cap = capacity.COUNT_CAP
        out: list[BreakerStatus] = []
        for entity in entities:
            self._published(entity)
            found = self.state(entity)
            agreed, reviewed = self.agreement(entity)
            counts = self.store.sample_counts(entity, cap)
            overdue = self.store.task_count(
                TaskQuery(now, entity, "quality_sample", snoozed=None, breaching=True), cap
            )
            current, mean, ready, hour = self.volume(entity, now)
            automated_open = self.store.open_sample_count(entity, "automated", self.settings.sample_open_cap)
            out.append(
                BreakerStatus(
                    entity=entity,
                    state=found.state if found is not None else "normal",
                    watch_since=found.watch_since if found is not None else None,
                    trigger=found.trigger if found is not None else None,
                    tripped_at=found.tripped_at if found is not None else None,
                    trip_change_set=found.trip_change_set if found is not None else None,
                    figures=dict(found.figures) if found is not None else {},
                    restored_at=found.restored_at if found is not None else None,
                    restore_reason=found.restore_reason if found is not None else None,
                    restore_change_set=found.restore_change_set if found is not None else None,
                    agreed=agreed,
                    reviewed=reviewed,
                    bound=round(agreement_upper(agreed, reviewed, capacity.BREAKER_Z), 3)
                    if reviewed
                    else None,
                    threshold=self.settings.breaker_agreement,
                    window=self.settings.breaker_window,
                    min_samples=self.settings.breaker_min_samples,
                    samples_open=counts["open"],
                    samples_overdue=overdue,
                    samples_voided=counts["void"],
                    arrivals=current,
                    mean=round(mean, 1),
                    multiple=self.settings.breaker_spike_multiple,
                    spike_min=self.settings.breaker_spike_min,
                    days=self.settings.breaker_spike_days,
                    hour=hour,
                    history_ready=ready,
                    cap_reached=automated_open >= self.settings.sample_open_cap,
                    withdrawn=tuple(
                        BulkStatus(
                            entity=entity,
                            key=row.band,
                            signature=row.signature or "",
                            since=row.tripped_at or row.updated_at,
                            figures=dict(row.figures),
                        )
                        for row in self._withdrawn_rows(entity)
                    ),
                )
            )
        return out


__all__ = ["BreakerService", "floor_hour"]
