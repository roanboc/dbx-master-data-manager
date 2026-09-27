"""The hub: every service wired over one store, for one actor (owner: SERVICES, B.10)."""

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
from mdm.services.arrival import ArrivalService
from mdm.services.authority import AuthorityService
from mdm.services.codelists import CodeListService
from mdm.services.commit import CommitService
from mdm.services.estimation import EstimationService
from mdm.services.feed import FeedReader
from mdm.services.lifecycle import LifecycleService
from mdm.services.matching import MatchService
from mdm.services.privacy import PrivacyService, Vault
from mdm.services.profiling import ProfileService
from mdm.services.registry import ModelRegistry


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
        self.commit = CommitService(store, self.registry, self.authority, self.vault, clock)
        self.lifecycle = LifecycleService(store, self.registry, self.commit, clock)
        self.matching = MatchService(store, self.registry)
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
        )
        self.estimation = EstimationService(store, self.registry)
        self.profiling = ProfileService(store, self.registry)
        self.feed = FeedReader(store)

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
