"""The hub: every service wired over one store, for one actor (owner: SERVICES, B.10, B.6.1)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from mdm.backend.factory import open_store
from mdm.backend.store import SqlStore
from mdm.config import Settings
from mdm.models.authority import Actor
from mdm.models.canonical import utcnow
from mdm.models.errors import PlatformRefused
from mdm.models.workbench import HubBadges, ServiceLevels
from mdm.services.arrival import ArrivalService
from mdm.services.authority import AuthorityService
from mdm.services.batches import BatchService
from mdm.services.breaker import BreakerService
from mdm.services.codelists import CodeListService
from mdm.services.commit import CommitService
from mdm.services.decisions import DecisionService
from mdm.services.estimation import EstimationService
from mdm.services.feed import FeedReader
from mdm.services.inbox import InboxService
from mdm.services.lifecycle import LifecycleService
from mdm.services.lookup import LookupService
from mdm.services.matching import MatchService
from mdm.services.privacy import PrivacyService, Vault
from mdm.services.profiling import ProfileService
from mdm.services.quality import QualityService
from mdm.services.registry import ModelRegistry
from mdm.services.tray import TrayService

#: how the workbench names each engine; Lakebase is Postgres behind a Lakebase endpoint
_ENGINE_BADGES = {"duckdb": "DuckDB", "postgres": "Postgres"}


class Hub:
    settings: Settings
    store: SqlStore
    actor: Actor
    registry: ModelRegistry
    codelists: CodeListService
    authority: AuthorityService
    vault: Vault
    privacy: PrivacyService
    commit: CommitService
    lifecycle: LifecycleService
    arrival: ArrivalService
    matching: MatchService
    estimation: EstimationService
    profiling: ProfileService
    feed: FeedReader
    inbox: InboxService
    decisions: DecisionService
    tray: TrayService
    lookup: LookupService
    breaker: BreakerService
    quality: QualityService
    batches: BatchService

    def __init__(
        self,
        settings: Settings,
        store: SqlStore,
        *,
        as_role: str | None = None,
        actor: Actor | None = None,
        clock: Callable[[], datetime] = utcnow,
        owns_store: bool = False,
        workspace: Callable[[], Any] | None = None,
    ) -> None:
        """Wires the services; the actor is `actor` or `authority.resolve_actor(as_role)`."""
        self.settings = settings
        self.store = store
        self._owns_store = owns_store
        if clock is not utcnow:
            store.clock = clock
        self.registry = ModelRegistry(store, clock)
        self.authority = AuthorityService(settings, store, self.registry, workspace)
        if actor is not None:
            if actor.persona and not settings.local_mode:
                raise PlatformRefused("persona_refused", role=actor.role)
            self.actor = actor
        else:
            self.actor = self.authority.resolve_actor(as_role)
        self.codelists = CodeListService(store)
        self.vault = Vault(store)
        self.privacy = PrivacyService(store, self.registry, self.vault)
        self.commit = CommitService(
            store,
            self.registry,
            self.authority,
            self.vault,
            clock,
            service_levels=ServiceLevels(settings.sla_hours),
        )
        self.lifecycle = LifecycleService(store, self.registry, self.commit, clock)
        self.matching = MatchService(store, self.registry)
        # the automated matcher's checkpoint (story 3.2): blind review and the quality breaker
        self.breaker = BreakerService(settings, store, self.registry, clock)
        self.quality = QualityService(settings, store, self.registry, self.matching, self.lifecycle, clock)
        self.arrival = ArrivalService(
            settings,
            store,
            self.registry,
            self.codelists,
            self.authority,
            self.vault,
            self.commit,
            self.matching,
            clock=clock,
            breaker=self.breaker,
            quality=self.quality,
        )
        self.inbox = InboxService(settings, store, self.registry, self.privacy, clock, breaker=self.breaker)
        self.decisions = DecisionService(
            settings,
            store,
            self.registry,
            self.matching,
            self.lifecycle,
            self.privacy,
            clock,
            quality=self.quality,
            breaker=self.breaker,
        )
        # signature batches (story 3.3): after the decisions, before the tray, which commits their chunks
        self.batches = BatchService(
            settings,
            store,
            self.registry,
            self.matching,
            self.lifecycle,
            self.commit,
            self.quality,
            self.breaker,
            self.privacy,
            self.arrival,
            clock,
        )
        self.tray = TrayService(
            settings,
            store,
            self.decisions,
            self.inbox,
            self.arrival,
            clock,
            breaker=self.breaker,
            batches=self.batches,
        )
        self.lookup = LookupService(settings, store, self.registry, self.privacy, clock)
        self.estimation = EstimationService(store, self.registry)
        self.profiling = ProfileService(store, self.registry)
        self.feed = FeedReader(store)

    def badges(self) -> HubBadges:
        """What the workbench's header says about this hub: the engine ("Lakebase" behind a Lakebase
        endpoint), the assistant ("endpoint" only when a published model enables it and an endpoint is
        named), whether the store is local, and the published entities. Reads the registry, so call it
        after `mdm init`."""
        entities = tuple(self.registry.published_entities())
        engine = "Lakebase" if self.settings.lakebase_endpoint else _ENGINE_BADGES.get(self.store.engine, "")
        endpoint = (
            self.settings.agent_provider in ("auto", "endpoint")
            and bool(self.settings.agent_endpoint)
            and any(self.registry.published(entity).ai_enabled for entity in entities)
        )
        return HubBadges(
            engine=engine or self.store.engine,
            assistant="endpoint" if endpoint else "stub",
            local=self.settings.local_mode,
            entities=entities,
        )

    @classmethod
    def open(
        cls,
        settings: Settings | None = None,
        *,
        as_role: str | None = None,
        actor: Actor | None = None,
        store: SqlStore | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> Hub:
        """The hub over `store`, or over `open_store(settings)` (settings default: `Settings.from_env()`).

        `actor`: an explicit, non-persona actor for tests and (from initiative 4) jobs; the command line
        never passes one. A store opened here is closed by `close()`; a store passed in is not.
        """
        settings = settings if settings is not None else Settings.from_env()
        owns = store is None
        opened = store if store is not None else open_store(settings)
        try:
            return cls(settings, opened, as_role=as_role, actor=actor, clock=clock, owns_store=owns)
        except BaseException:
            if owns:
                opened.close()
            raise

    def close(self) -> None:
        if self._owns_store:
            self.store.close()

    def __enter__(self) -> Hub:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
