"""The workbench as one Dash application: the shell, the routes, and every page's callbacks.

`create_app` opens the hub once (unless one is passed), gives open tasks without a due time one, keeps
`UiState(hub, settings, executor, worker)` in `app.server.extensions["mdm"]`, installs the web safety of
`web.py`, registers the shell and every page with `app.callback` and `app.clientside_callback` only
(never the global `dash.callback`, so tests build many apps in one process), and starts a `TrayWorker`
when `worker` is True, or None and `settings.tray_worker_on`. `atexit` closes the state.

Routes (B.8.3): `/` the inbox; `/record/<ref>` a golden record (a retired ID resolves to its survivor, a
source key to its source record); `/source/<system>/<key>` a source record; `/groups` the Alike reviews and
`/batch/<BAT-…>` a batch of them (story 3.3); anything else the inbox with a notice. The route callback
(S5) rebuilds a page only when the path or the actor's role changes; a change of `?…` on the same page is
the page's own business. Only codes and IDs of the query reach a page.

Owner: SHELL (B.8.1, B.8.6).
"""

from __future__ import annotations

import atexit
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qsl, unquote

from dash import Dash, Input, Output, State, html, no_update
from dash.development.base_component import Component

from mdm.config import Settings
from mdm.models.batch import BATCH_ID_RE
from mdm.models.safety import SAFE_TEXT_RE
from mdm.services.context import Hub
from mdm.services.tray import TrayWorker
from mdm.ui import context, ids, layout, web
from mdm.ui.components import common, keys, tray
from mdm.ui.context import EXTENSION, UiContext, UiState
from mdm.ui.pages import batch, groups, inbox, record, source

logger = logging.getLogger("mdm.ui")

#: the workbench's own scripts and styles, served by Dash from /assets/
ASSETS = Path(__file__).resolve().parent / "assets"
TITLE = "Master Data Manager"
#: the query keys a page may read; anything else in an address is dropped before a page sees it (a
#: signature group's key and a batch's ID filter the inbox to a group's reviews or a batch's forced sample)
QUERY_KEYS = ("view", "kind", "task", "tab", "entity", "group", "batch")
#: the page Dash serves around the app: Dash's own, with the language named (WCAG 3.1.1)
INDEX_STRING = """<!DOCTYPE html>
<html lang="en-GB">
    <head>
        {%metas%}
        <title>{%title%}</title>
        {%favicon%}
        {%css%}
    </head>
    <body>
        <noscript>The workbench needs JavaScript.</noscript>
        {%app_entry%}
        <footer>
            {%config%}
            {%scripts%}
            {%renderer%}
        </footer>
    </body>
</html>
"""
#: an address echoed in the unknown-page notice only when it has this shape
ECHO_PATH_RE = re.compile(r"^[A-Za-z0-9/_:.\-]{0,80}\Z")


def parse_route(pathname: str | None) -> tuple[str, tuple[str, ...]]:
    """ "/" → ("inbox", ()); "/record/<ref>" → ("record", (ref,)); "/source/<system>/<key>" → ("source",
    (system, key)); "/groups" → ("groups", ()); "/batch/<BAT-…>" → ("batch", (ID,)) when the ID has a batch
    ID's shape; anything else → ("unknown", ())."""
    parts = [unquote(part) for part in (pathname or "/").split("/") if part]
    if not parts:
        return "inbox", ()
    if parts == ["groups"]:
        return "groups", ()
    if parts[0] == "batch" and len(parts) == 2 and BATCH_ID_RE.match(parts[1]):
        return "batch", (parts[1],)
    if parts[0] == "record" and len(parts) >= 2:
        return "record", ("/".join(parts[1:]),)
    if parts[0] == "source" and len(parts) >= 3:
        return "source", (parts[1], "/".join(parts[2:]))
    return "unknown", ()


def query_of(search: str | None) -> dict[str, str]:
    """The address's query as codes and IDs: QUERY_KEYS only, each value safe text, else dropped."""
    found: dict[str, str] = {}
    for key, value in parse_qsl((search or "").lstrip("?"), keep_blank_values=False):
        if key in QUERY_KEYS and key not in found and SAFE_TEXT_RE.match(value):
            found[key] = value
    return found


def address_note(pathname: str | None) -> Component:
    """ "There is no page at /x; this is the inbox." (the address echoed only when it has a safe shape)."""
    path = pathname or "/"
    if ECHO_PATH_RE.match(path):
        return common.notice("info", f"There is no page at {path}; this is the inbox.")
    return common.notice("info", "There is no page at that address; this is the inbox.")


def _failed_page(failure: dict) -> Component:
    return common.notice("error" if failure.get("color") == "red" else "warning", failure.get("message", ""))


def render_route(
    ctx: UiContext, pathname: str | None, search: str | None, entity: str | None
) -> tuple[Component, Component | None, bool]:
    """S5 without Dash: the page for the address, the note above it, and whether it is the inbox. The
    inbox and the Alike reviews read the header's entity from `entity`; a record, a source or a batch reads
    only its own address."""
    kind, args = parse_route(pathname)
    query = query_of(search)
    if kind == "record":
        page, failure = context.guarded(record.layout, ctx, args[0], query)
    elif kind == "source":
        page, failure = context.guarded(source.layout, ctx, args[0], args[1], query)
    elif kind == "batch":
        page, failure = context.guarded(batch.layout, ctx, args[0])
    elif kind == "groups":
        known = entity if isinstance(entity, str) and entity in ctx.badges.entities else ""
        page, failure = context.guarded(groups.layout, ctx, {"entity": known})
    else:
        query["entity"] = entity if isinstance(entity, str) and entity in ctx.badges.entities else ""
        page, failure = context.guarded(inbox.layout, ctx, query)
    if failure is not None:
        page = _failed_page(failure)
    note = address_note(pathname) if kind == "unknown" else None
    return page, note, kind in ("inbox", "unknown")


def route_key(pathname: str | None, ctx: UiContext) -> dict[str, str]:
    """What the page on screen was built for: its path and the actor's role (ROUTE)."""
    return {"path": pathname or "/", "persona": ctx.actor.role}


def navbar_class(ctx: UiContext) -> str:
    """The navbar shows the Work group to a role that reads tasks, and a line on records to any other."""
    return layout.NAV_READER if ctx.can("view_tasks") else layout.NAV_NONE


def route_outputs(
    ctx: UiContext, pathname: str | None, search: str | None, on_screen: object, entity: str | None
) -> tuple:
    """S5's outputs without Dash: (page, note, inbox active, navbar class, route), or `no_update` on every
    output when the page on screen was built for this path and role (a change of `?…`, a tray poll or a
    restored store never rebuilds a page)."""
    key = route_key(pathname, ctx)
    if on_screen == key:
        return (no_update,) * 5
    page, note, on_inbox = render_route(ctx, pathname, search, entity)
    return page, note, on_inbox, navbar_class(ctx), key


def register_routes(app: Dash) -> None:
    """Registers S5, the route."""

    @app.callback(
        Output(ids.PAGE, "children"),
        Output(ids.ADDRESS_NOTE, "children"),
        Output(ids.NAV_INBOX, "active"),
        Output(ids.NAVBAR, "className"),
        Output(ids.ROUTE, "data"),
        Input(ids.URL, "pathname"),
        Input(ids.PERSONA, "data"),
        State(ids.URL, "search"),
        State(ids.ROUTE, "data"),
        State(ids.ENTITY, "data"),
    )
    def route(pathname, persona, search, on_screen, entity):
        request_ctx, failure = context.guarded(context.current, persona)
        if request_ctx is None:
            return _failed_page(failure or {}), None, False, layout.NAV_NONE, None
        return route_outputs(request_ctx, pathname, search, on_screen, entity)


def create_app(
    settings: Settings | None = None,
    *,
    hub: Hub | None = None,
    worker: bool | None = None,
    listen: tuple[str, int] | None = None,
) -> Dash:
    """The workbench for `settings` (default `Settings.from_env()`), over `hub` or a hub it opens and
    closes; `listen` is the (host, port) it will be served on, for the host allow-list."""
    settings = settings if settings is not None else Settings.from_env()
    owns_hub = hub is None
    opened = hub if hub is not None else Hub.open(settings)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mdm-prefetch")
    state = UiState(hub=opened, settings=settings, executor=executor, owns_hub=owns_hub)
    try:
        opened.inbox.backfill_due_times()
        opened.batches.backfill_signatures()  # reviews written before signature batches (story 3.3)
        app = Dash(
            __name__,
            title=TITLE,
            assets_folder=str(ASSETS),
            suppress_callback_exceptions=True,
            update_title=None,  # or every poll flips the tab's title to "Updating…"
            on_error=web.on_callback_error,
        )

        def serve_layout() -> Component:
            try:
                badges = state.badges()
            except Exception as error:  # noqa: BLE001 - the header still renders; pages say what failed
                logger.warning("badges failed type=%s", type(error).__name__)
                badges = layout.fallback_badges(settings)
            return layout.shell(settings, badges)

        app.index_string = INDEX_STRING
        app.layout = serve_layout
        app.validation_layout = html.Div(
            [
                layout.shell(settings),
                inbox.skeleton(),
                record.skeleton(),
                source.skeleton(),
                groups.skeleton(),
                batch.skeleton(),
            ]
        )
        app.server.extensions[EXTENSION] = state
        web.install(app, settings, listen)
        layout.register(app)
        register_routes(app)
        tray.register(app)
        keys.register(app)
        inbox.register(app)
        record.register(app)
        source.register(app)
        groups.register(app)
        batch.register(app)
        if worker is True or (worker is None and settings.tray_worker_on):
            state.worker = TrayWorker(opened.tray)
            state.worker.start()
    except BaseException:
        state.close()
        raise
    atexit.register(state.close)
    return app
