"""What a callback works with: the hub, the settings and the actor of the request.

`UiState` is the process's one hub, kept in `app.server.extensions["mdm"]` by `create_app`; `current`
builds a `UiContext` for one request, with the actor the authority service resolves (decision 21): inside
a Databricks App, the user the platform forwards, never a persona; on a local store, the persona the tab
chose. Callbacks call services through `guarded`, which turns a refusal into a notification with one plain
sentence and any other failure into a sentence naming only the exception's type, logged by type only.
Callbacks reach the hub through its services only.

Owner: SHELL (B.8.1).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from mdm import capacity
from mdm.config import Settings
from mdm.models.authority import PERSONAS, Actor, allowed
from mdm.models.errors import MdmError
from mdm.models.workbench import HubBadges
from mdm.services.authority import PERSONA_PREFIX
from mdm.services.context import Hub
from mdm.services.tray import TrayWorker
from mdm.ui import messages
from mdm.ui.components import common

logger = logging.getLogger("mdm.ui")
#: where create_app keeps the UiState: app.server.extensions[EXTENSION]
EXTENSION = "mdm"
#: the headers a Databricks App forwards the signed-in user in, in the order they are read
FORWARDED_USER_HEADERS = ("X-Forwarded-Email", "X-Forwarded-User")
#: how long the header's badges (engine, assistant, published entities) are kept before they are read again
BADGES_SECONDS = capacity.COUNTS_REFRESH_SECONDS


@dataclass
class UiState:
    """The process's hub and what runs beside it; `close()` stops them in order and is safe to call twice."""

    hub: Hub
    settings: Settings
    executor: ThreadPoolExecutor  # one thread: prepares the next row's case while the steward reads
    worker: TrayWorker | None = None  # flushes the tray on a local store (Settings.tray_worker_on)
    owns_hub: bool = True  # create_app opened the hub, so it closes it
    closed: bool = field(default=False, init=False)
    _badges: tuple[float, HubBadges] | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def badges(self) -> HubBadges:
        """What the header says about the hub, read at most every BADGES_SECONDS (it reads the registry)."""
        with self._lock:
            kept = self._badges
            if kept is not None and time.monotonic() - kept[0] < BADGES_SECONDS:
                return kept[1]
            badges = self.hub.badges()
            self._badges = (time.monotonic(), badges)
            return badges

    def close(self) -> None:
        """Stops the tray worker, then the prefetch thread, then closes the hub when this state opened it."""
        if self.closed:
            return
        self.closed = True
        if self.worker is not None:
            self.worker.stop()
        self.executor.shutdown(wait=True, cancel_futures=True)
        if self.owns_hub:
            self.hub.close()


@dataclass(frozen=True)
class UiContext:
    """One request's view of the hub: its actor, and what the header says about the hub."""

    hub: Hub
    settings: Settings
    actor: Actor
    badges: HubBadges
    executor: ThreadPoolExecutor | None = None  # the state's prefetch thread; None in tests

    def can(self, action: str) -> bool:
        """Whether the actor's role allows `action` (`models.authority.allowed`), without raising."""
        return allowed(self.actor, action)

    @property
    def route_persona(self) -> str:
        """Who the page on screen was built for, as the route store keys it: a persona's code, so switching
        between the two data-steward personas rebuilds the page as it does between roles; for anyone else
        (a user a Databricks App forwards) the role, so no user name lands in the browser."""
        if self.actor.persona and self.actor.name.startswith(PERSONA_PREFIX):
            return self.actor.name.removeprefix(PERSONA_PREFIX)
        return self.actor.role


def state() -> UiState:
    """The UiState of the app serving this request (`flask.current_app.extensions["mdm"]`)."""
    import flask

    return flask.current_app.extensions[EXTENSION]


def persona_of(value: Any) -> str | None:
    """The persona a tab asked for, when it names one (`PERSONAS`: every role, and a second data steward);
    anything else is no persona (the default)."""
    return value if isinstance(value, str) and value in PERSONAS else None


def forwarded_user() -> str | None:
    """The user a Databricks App forwards with this request, or None. Read only inside an App: anywhere
    else a header is whatever the caller wrote."""
    import flask

    for name in FORWARDED_USER_HEADERS:
        value = (flask.request.headers.get(name) or "").strip()
        if value:
            return value
    return None


def current(persona: str | None) -> UiContext:
    """The context of this request: inside a Databricks App, the user the platform forwards
    (`X-Forwarded-Email`, else `X-Forwarded-User`) as the authority service decides, and never a persona;
    on a local store, the persona `persona` (else `MDM_ROLE`, else the data steward); `Forbidden
    (unknown_role)` for a role that does not exist. Through `hub.authority.actor_for_request`."""
    kept = state()
    forwarded = forwarded_user() if kept.settings.in_databricks_app else None
    actor = kept.hub.authority.actor_for_request(persona=persona_of(persona), forwarded_user=forwarded)
    return UiContext(kept.hub, kept.settings, actor, kept.badges(), kept.executor)


def for_test(hub: Hub, *, role: str = "data_steward") -> UiContext:
    """A persona context over `hub` with no Flask request, for tests of the pure functions behind the
    callbacks (a local store only)."""
    actor = hub.authority.actor_for_request(persona=role, forwarded_user=None)
    return UiContext(hub, hub.settings, actor, hub.badges())


def notice(error: BaseException) -> dict:
    """One notification (an item of `components.common.notify`'s payload; send it as `[notice]`) with
    `messages.sentence(error)`: yellow for a refusal, red for anything else (its type only)."""
    if isinstance(error, MdmError):
        return common.notify(
            messages.sentence(error), "yellow", title=messages.title_for(error.code), key=error.code
        )[0]
    return common.notify(messages.unexpected(error), "red")[0]


def log_failure(error: BaseException) -> None:
    """Logs a failure by its type (and a refusal by its code) only: a message could quote a value."""
    if isinstance(error, MdmError):
        logger.info("refused code=%s", error.code)
    else:
        logger.warning("failed type=%s", type(error).__name__)


def guarded(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> tuple[Any, dict | None]:
    """`(fn(*args, **kwargs), None)`, or `(None, notice)`: an `MdmError` gives its sentence; any other
    exception is logged as its type only (logger `mdm.ui`) and reads "Something went wrong (KeyError).
    The details are not shown, because they could hold a personal value." """
    try:
        return fn(*args, **kwargs), None
    except Exception as error:  # noqa: BLE001 - every failure becomes one safe sentence
        log_failure(error)
        return None, notice(error)


def prefetch(ctx: UiContext, task_id: str | None) -> None:
    """Submits `decisions.prefetch(task_id, actor=…)` to the state's executor; nothing for None, and
    nothing without an executor (tests) or after the workbench stopped."""
    if not task_id or ctx.executor is None:
        return
    try:
        ctx.executor.submit(ctx.hub.decisions.prefetch, task_id, actor=ctx.actor)
    except RuntimeError:  # the executor was shut down: the workbench is stopping
        return
