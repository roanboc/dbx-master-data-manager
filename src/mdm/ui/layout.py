"""The shell every page sits in: header, navigation, main content, and the stores the shell owns.

`shell(settings, badges)` builds the MantineProvider with the location, the stores (route, persona,
entity, shortcuts switch, key event, tray state, tray version, settled), the intervals (tray poll, clock
tick, counts poll), the notification container, the skip link, the AppShell (header, navbar, main with
the address note and the page) and the key help modal (B.8.6). Header, left to right: burger, the product
name, the entity select; then the tray button (quiet until it holds a decision), the breaches link, the
role as quiet text, the persona select (local store only, "Act as"), the engine as quiet text ("DuckDB ·
stub"), "?" and the colour-scheme switch. The navigation has one
group, Work, with the Inbox and the view rail; a role that sees no tasks gets a line saying how records
open instead.

Callbacks here (`register`): S1 and S2 as one (the scheme, seeded from `prefers-color-scheme` on a
first visit, kept per browser and applied; two callbacks in a chain would skip the second when the first
has nothing to change), the burger, S3 and S4 (the persona and entity selects kept in step with their session stores,
one clientside callback each, since two callbacks feeding each other would be a cycle Dash refuses), and
S6 (the role badge, the breaches link and the view rail, from one capped count read). The route (S5) is in
`app.py`, the tray's in `components/tray.py`, the keys' in `components/keys.py`.

Owner: SHELL (B.8.6).
"""

from __future__ import annotations

from urllib.parse import parse_qsl

import dash_mantine_components as dmc
from dash import ClientsideFunction, Input, Output, State, dcc, html, no_update
from dash.development.base_component import Component

from mdm import capacity
from mdm.config import Settings
from mdm.models.authority import PERSONA_LABELS, PERSONAS, ROLE_LABELS, Actor
from mdm.models.batch import BATCH_ID_RE, SIGNATURE_KEY_RE
from mdm.models.tasks import TASK_KINDS
from mdm.models.workbench import ALL_VIEWS, SAMPLES_VIEW, HubBadges
from mdm.ui import context, ids
from mdm.ui.components import keys, rail, tray
from mdm.ui.components.common import count_text
from mdm.ui.components.icons import icon
from mdm.ui.theme import THEME

PRODUCT = "Master Data Manager"
#: the entity select's value for every entity (the ENTITY store holds "" then)
ALL_ENTITIES = "all"
#: the persona a local workbench starts as when MDM_ROLE names none (solution design § Authority)
DEFAULT_PERSONA = "data_steward"
#: the navbar's class for a role that reads tasks, and for one that does not (styles.css shows one part)
NAV_READER = "mdm-nav mdm-nav-reader"
NAV_NONE = "mdm-nav mdm-nav-none"
#: a part hidden until it has something to say
HIDDEN = {"display": "none"}
NAV_NOTE = "Your role has no inbox. A record opens from its link, such as /record/ORG-000123."


def entity_label(entity: str) -> str:
    """ "Organisation" for `organisation`."""
    return entity.replace("_", " ").capitalize()


def role_text(actor: Actor) -> str:
    """ "Data steward", or "Data steward (persona)" for a persona ("Data steward 2 (persona)" for the second
    data steward)."""
    if actor.persona:
        code = actor.name.partition(":")[2]
        return f"{PERSONA_LABELS.get(code, ROLE_LABELS.get(actor.role, actor.role))} (persona)"
    return ROLE_LABELS.get(actor.role, actor.role)


def default_persona(settings: Settings) -> str:
    """The persona a local workbench acts as before the tab chooses one: MDM_ROLE, else the data steward."""
    role = (settings.role or "").strip()
    return role if role in PERSONAS else DEFAULT_PERSONA


def fallback_badges(settings: Settings) -> HubBadges:
    """What the header says when the hub cannot be read: the engine unnamed, no entity."""
    return HubBadges(engine="Store", assistant="stub", local=settings.local_mode, entities=())


def engine_text(badges: HubBadges) -> str:
    """ "DuckDB · stub"."""
    return f"{badges.engine} · {badges.assistant}"


def header(settings: Settings, badges: HubBadges) -> list[Component]:
    """The header's children: burger, name, entity select, tray, breaches, role badge, persona select
    (only when `badges.local`), engine badge ("DuckDB · stub"), help and the scheme switch; every control
    with an accessible name."""
    persona = default_persona(settings)
    entities = [{"value": ALL_ENTITIES, "label": "All entities"}] + [
        {"value": entity, "label": entity_label(entity)} for entity in badges.entities
    ]
    left = dmc.Group(
        [
            dmc.Burger(
                id=ids.BURGER, opened=False, hiddenFrom="sm", size="sm", **{"aria-label": "Navigation"}
            ),
            dmc.Anchor(PRODUCT, href="/", underline="never", className="mdm-brand"),
            dmc.Select(
                id=ids.ENTITY_SELECT,
                data=entities,
                value=ALL_ENTITIES,
                allowDeselect=False,
                size="sm",
                w=170,
                visibleFrom="sm",
                **{"aria-label": "Entity"},
            ),
        ],
        gap="sm",
        wrap="nowrap",
    )
    persona_part: list[Component] = []
    if badges.local:
        persona_part = [
            dmc.Select(
                id=ids.PERSONA_SELECT,
                data=[{"value": code, "label": label} for code, label in PERSONA_LABELS.items()],
                value=persona,
                allowDeselect=False,
                size="sm",
                # every persona in view at once: a list that scrolls would be a scroll region no key reaches
                # (axe: scrollable-region-focusable), since the arrows move through the options, not the list
                maxDropdownHeight=360,
                w=232,
                leftSection=html.Span("Act as", className="mdm-act-as"),
                leftSectionWidth=58,
                leftSectionPointerEvents="none",
                visibleFrom="md",
                **{"aria-label": "Act as"},
            )
        ]
    role = Actor(f"persona:{persona}", "person", PERSONAS[persona], persona=True) if badges.local else None
    right = dmc.Group(
        [
            html.Div(tray.popover(), id=ids.TRAY_WRAP),
            dmc.Anchor(
                id=ids.BREACHES,
                href=rail.href("breaching"),
                children="",
                style=HIDDEN,
                className="mdm-breaches",
            ),
            # the role in words where the persona select does not show it: between the small and the medium
            # breakpoints on a local store, and always on the platform (the navigation says it below small);
            # quiet text, not a pill
            dmc.Text(
                role_text(role) if role is not None else "",
                id=ids.ROLE_BADGE,
                span=True,
                size="sm",
                c="dimmed",
                className="mdm-role-badge",
                visibleFrom="sm",
                **({"hiddenFrom": "md"} if badges.local else {}),
            ),
            *persona_part,
            dmc.Text(
                [html.Span("Store and assistant: ", className="mdm-sr-only"), engine_text(badges)],
                id=ids.ENGINE_BADGE,
                span=True,
                size="sm",
                c="dimmed",
                visibleFrom="lg",
                className="mdm-engine-badge",
            ),
            dmc.ActionIcon(
                "?",
                id=ids.HELP_OPEN,
                variant="subtle",
                color="gray",
                size="lg",
                className="mdm-help-open",
                visibleFrom="sm",
                **{"aria-label": "Keyboard shortcuts"},
            ),
            html.Div(
                dmc.SegmentedControl(
                    id=ids.SCHEME_TOGGLE,
                    data=[{"value": "light", "label": "Light"}, {"value": "dark", "label": "Dark"}],
                    value=None,  # seeded once from the browser's preference (S1), then kept per browser
                    persistence=True,
                    persistence_type="local",
                    size="xs",
                    **{"aria-label": "Colour scheme"},
                ),
                className="mdm-scheme-wrap",
            ),
        ],
        gap="xs",
        wrap="nowrap",
    )
    return [dmc.Group([left, right], justify="space-between", wrap="nowrap", h="100%", px="md")]


def small_controls() -> Component:
    """What the header drops below the small breakpoint, in the navigation instead: the role, the key help
    and the colour scheme (kept in step with the header's switch by S1)."""
    return html.Div(
        [
            html.P(id=ids.NAV_ROLE, className="mdm-nav-role"),
            dmc.Button(
                "Keyboard shortcuts", id=ids.HELP_OPEN_NAV, variant="default", size="xs", fullWidth=True, mb=8
            ),
            dmc.SegmentedControl(
                id=ids.SCHEME_TOGGLE_NAV,
                data=[{"value": "light", "label": "Light"}, {"value": "dark", "label": "Dark"}],
                value=None,
                size="xs",
                fullWidth=True,
                **{"aria-label": "Colour scheme"},
            ),
        ],
        className="mdm-nav-small",
    )


def navbar() -> list[Component]:
    """The navigation: the group Work with the Inbox link (NAV_INBOX) and the view rail (NAV_VIEWS)
    below it, filled by S6; for a role that sees no tasks, one line on how records open; below the small
    breakpoint, the header's role, key help and colour scheme."""
    return [
        html.Div(
            [
                html.H2("Work", className="mdm-nav-heading"),
                dmc.NavLink(
                    id=ids.NAV_INBOX,
                    label="Inbox",
                    href="/",
                    leftSection=icon("tray"),
                    active=True,
                    variant="subtle",
                    className="mdm-nav-inbox",
                ),
                html.Div(id=ids.NAV_VIEWS, className="mdm-nav-views"),
            ],
            className="mdm-nav-work",
        ),
        html.P(NAV_NOTE, className="mdm-nav-note"),
        small_controls(),
    ]


def stores() -> list[Component]:
    """The stores and intervals the shell owns (B.8.4)."""
    return [
        dcc.Location(id=ids.URL, refresh="callback-nav"),
        dcc.Store(id=ids.ROUTE, storage_type="memory"),
        dcc.Store(id=ids.PERSONA, storage_type="session"),
        dcc.Store(id=ids.ENTITY, storage_type="session"),
        dcc.Store(id=ids.KEYS_ENABLED, storage_type="local", data=True),
        dcc.Store(id=ids.KEY_EVENT, storage_type="memory"),
        dcc.Store(id=ids.TRAY_STATE, storage_type="memory"),
        dcc.Store(id=ids.TRAY_VERSION, storage_type="memory", data=0),
        dcc.Store(id=ids.SETTLED, storage_type="memory"),
        dcc.Store(id=ids.TRAY_LIVE, storage_type="memory"),
        dcc.Interval(id=ids.TRAY_POLL, interval=capacity.TRAY_POLL_SECONDS * 1000, disabled=True),
        dcc.Interval(id=ids.CLOCK_TICK, interval=1000, disabled=True),
        dcc.Interval(id=ids.COUNTS_POLL, interval=capacity.COUNTS_REFRESH_SECONDS * 1000),
    ]


def shell(settings: Settings | None = None, badges: HubBadges | None = None) -> Component:
    """The whole page around the routed content, for `settings` and the hub's `badges` (without them, a
    neutral header, as the validation layout needs)."""
    settings = settings if settings is not None else Settings()
    badges = badges if badges is not None else fallback_badges(settings)
    return dmc.MantineProvider(
        id=ids.SHELL,
        theme=THEME,
        defaultColorScheme="auto",  # the browser's preference until the switch is seeded (S1)
        children=[
            *stores(),
            # bottom left: never over the decide pane's actions or the header's tray
            dmc.NotificationContainer(id=ids.NOTIFY, position="bottom-left", zIndex=400),
            html.A("Skip to the main content", href="#main", id=ids.SKIP_LINK, className="mdm-skip"),
            dmc.AppShell(
                id=ids.APP_SHELL,
                header={"height": 56},
                navbar={"width": 240, "breakpoint": "sm", "collapsed": {"mobile": True}},
                padding="md",
                children=[
                    dmc.AppShellHeader(header(settings, badges), id=ids.HEADER, className="mdm-header"),
                    dmc.AppShellNavbar(
                        navbar(),
                        id=ids.NAVBAR,
                        p="sm",
                        className=NAV_READER,
                        **{"aria-label": "Work"},
                    ),
                    dmc.AppShellMain(
                        [html.Div(id=ids.ADDRESS_NOTE), html.Div(id=ids.PAGE)],
                        id=ids.MAIN,
                        tabIndex=-1,
                        className="mdm-main",
                    ),
                ],
            ),
            keys.help_modal(),
        ],
    )


# ---------------------------------------------------------------------------------------------- header state


def _alike_path(pathname: str) -> bool:
    """Whether a path is the Alike reviews page or a batch's page (story 3.3)."""
    if pathname.rstrip("/") == rail.ALIKE_HREF:
        return True
    head, _, batch = pathname.strip("/").partition("/")
    return head == "batch" and bool(BATCH_ID_RE.match(batch))


def inbox_query(pathname: str | None, search: str | None) -> dict[str, str | None]:
    """The rail's view of the address: `view` and `kind` codes on the inbox; the Alike reviews mark
    (`rail.ALIKE`) on the Alike reviews page, a batch's page and the inbox filtered to a group or a batch;
    nothing elsewhere."""
    path = pathname or "/"
    if path != "/":
        return {"view": rail.ALIKE} if _alike_path(path) else {}
    pairs = dict(parse_qsl((search or "").lstrip("?")))
    group, batch = pairs.get("group"), pairs.get("batch")
    if (group and SIGNATURE_KEY_RE.match(group)) or (batch and BATCH_ID_RE.match(batch)):
        return {"view": rail.ALIKE}
    view = pairs.get("view")
    kind = pairs.get("kind")
    view = view if view in ALL_VIEWS else "mine"
    return {"view": view, "kind": kind if kind in TASK_KINDS and view != SAMPLES_VIEW else None}


def entity_filter(ctx: context.UiContext, entity: object) -> str | None:
    """The ENTITY store's value when it names a published entity; None for all."""
    return entity if isinstance(entity, str) and entity in ctx.badges.entities else None


def header_parts(
    ctx: context.UiContext, entity: object, pathname: str | None, search: str | None
) -> tuple[tuple[str, list | str, dict, Component | list, dict], int | None]:
    """S6 without Dash: `header_state`'s five parts, and how many of the actor's tray entries still move
    (`ViewCounts.tray_live`: staged, or a batch committing; None when the counts were not read). One capped
    count read (`inbox.counts`)."""
    role = role_text(ctx.actor)
    tray_style = {} if ctx.can("work_tasks") else HIDDEN
    if not ctx.can("view_tasks"):
        return (role, "", HIDDEN, [], tray_style), None
    counts, failure = context.guarded(
        ctx.hub.inbox.counts, actor=ctx.actor, entity=entity_filter(ctx, entity)
    )
    if counts is None:
        return (role, "", HIDDEN, [], tray_style), None
    breaching = counts.views.get("breaching", 0)
    link = [icon("alert"), f"{count_text(breaching)} breaching"] if breaching else ""
    rail_view = rail.render(counts, inbox_query(pathname, search))
    return (role, link, ({} if breaching else HIDDEN), rail_view, tray_style), counts.tray_live


def header_state(
    ctx: context.UiContext, entity: object, pathname: str | None, search: str | None
) -> tuple[str, list | str, dict, Component | list, dict]:
    """S6 without Dash: the role badge's text, the breaches link's children and style, the view rail, and
    the tray's style (hidden for a role that decides no task). One capped count read (`inbox.counts`); a
    role that sees no tasks gets no link and no rail."""
    return header_parts(ctx, entity, pathname, search)[0]


def register(app) -> None:
    """Registers S1 and S2 (one callback), S3, S4 and S6 on `app`, and the burger (the tray's are in components.tray, the keys' in
    components.keys, the route in app.py)."""
    app.clientside_callback(
        ClientsideFunction(namespace="mdm_shell", function_name="scheme"),
        Output(ids.SHELL, "forceColorScheme"),
        Output(ids.SCHEME_TOGGLE, "value"),
        Output(ids.SCHEME_TOGGLE_NAV, "value"),
        Input(ids.SCHEME_TOGGLE, "value"),
        Input(ids.SCHEME_TOGGLE_NAV, "value"),
    )
    app.clientside_callback(
        ClientsideFunction(namespace="mdm_shell", function_name="burger"),
        Output(ids.APP_SHELL, "navbar"),
        Input(ids.BURGER, "opened"),
        State(ids.APP_SHELL, "navbar"),
        prevent_initial_call=True,
    )
    app.clientside_callback(
        ClientsideFunction(namespace="mdm_shell", function_name="syncPersona"),
        Output(ids.PERSONA, "data"),
        Output(ids.PERSONA_SELECT, "value"),
        Input(ids.PERSONA_SELECT, "value"),
        Input(ids.PERSONA, "data"),
    )
    app.clientside_callback(
        ClientsideFunction(namespace="mdm_shell", function_name="syncEntity"),
        Output(ids.ENTITY, "data"),
        Output(ids.ENTITY_SELECT, "value"),
        Input(ids.ENTITY_SELECT, "value"),
        Input(ids.ENTITY, "data"),
    )

    # S6: the counts follow a settlement and the tray at once, not only the poll, so the rail, the header
    # and the health strip never disagree for long
    @app.callback(
        Output(ids.ROLE_BADGE, "children"),
        Output(ids.BREACHES, "children"),
        Output(ids.BREACHES, "style"),
        Output(ids.NAV_VIEWS, "children"),
        Output(ids.TRAY_WRAP, "style"),
        Output(ids.NAV_ROLE, "children"),
        Output(ids.TRAY_LIVE, "data"),
        Input(ids.PERSONA, "data"),
        Input(ids.ENTITY, "data"),
        Input(ids.COUNTS_POLL, "n_intervals"),
        Input(ids.URL, "pathname"),
        Input(ids.URL, "search"),
        Input(ids.SETTLED, "data"),
        Input(ids.TRAY_VERSION, "data"),
    )
    def refresh_header(persona, entity, _polls, pathname, search, _settled, _tray):
        request_ctx, _failure = context.guarded(context.current, persona)
        if request_ctx is None:
            return (no_update,) * 7
        (role, link, style, views, tray_style), live = header_parts(request_ctx, entity, pathname, search)
        return role, link, style, views, tray_style, f"Acting as {role}", no_update if live is None else live

    # S6b: a tray that is not polling wakes when the counts see more (or fewer) of the steward's entries
    # moving than the tab shows, so a batch confirmed by a second steward reaches its maker's tray within one
    # counts refresh; the tray's refresh then keeps its poll on while anything moves (story 3.3). It wakes
    # the poll rather than bumping TRAY_VERSION, which S6 takes as an input: that would be a cycle.
    app.clientside_callback(
        ClientsideFunction(namespace="mdm_shell", function_name="trayWake"),
        Output(ids.TRAY_POLL, "disabled", allow_duplicate=True),
        Input(ids.TRAY_LIVE, "data"),
        State(ids.TRAY_STATE, "data"),
        prevent_initial_call=True,
    )
