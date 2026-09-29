"""The Alike reviews page (story 3.3, plan 5 B.2): the open reviews grouped by signature, largest first,
each group with its pattern in words, its capped count (which opens its reviews in the inbox), its label
history, its blind-review agreement and its batch, and the batches waiting for the steward as their second
steward. G on the inbox opens it (decision 18 keeps single keys on the inbox), and so does the rail's
"Alike reviews" link; no single key acts here, and every action is a button.

`/groups` opens it with the header's entity, as the inbox does. Its callbacks: G1 draws the list again when
the header's entity changes (the inbox's address bridge fills `GROUPS_ADDRESS`), when the tray settles
something (`GROUPS_SETTLED`) and after a draw (`GROUPS_VERSION`); G2 draws a forced sample from a group and
opens the batch's page, or says why it could not. The page carries each group's safe key (`SIG-…`), never
its signature, and offers no restore of withdrawn bulk decisions anywhere.

The pure functions behind the callbacks (`view`, `draw`) run in tests without Dash.

Owner: WORKBENCH (plan 5, B.2).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from dash import ALL, Input, Output, State, dcc, html, no_update, set_props
from dash import ctx as dash_ctx
from dash.development.base_component import Component

from mdm.models.batch import SIGNATURE_KEY_RE
from mdm.ui import context, ids
from mdm.ui.components import groups
from mdm.ui.components.common import notice, notify, page_title
from mdm.ui.context import UiContext

#: the page's own copies of the shell's address and settlements (the inbox's bridges fill them)
ADDRESS = ids.GROUPS_ADDRESS
SETTLED_HERE = ids.GROUPS_SETTLED
#: the draw's notification title
DRAWN = "Sample drawn"


@dataclass(frozen=True)
class Drawn:
    """What a draw did: the batch's page to open (None when refused), and the notification that says so."""

    path: str | None
    note: dict


def entity_of(ctx: UiContext, value: Any) -> str | None:
    """The header's entity when it names a published entity; None for all."""
    return value if isinstance(value, str) and value in ctx.badges.entities else None


def view(ctx: UiContext, entity: str | None) -> list[Component]:
    """GROUPS_LIST's children (G1 without Dash): the consumer's line for a role that decides no tasks; the
    groups, their batches and the batches to confirm otherwise, every draw disabled with its reason for a role
    that may not draw; a failure's sentence under the title."""
    if not ctx.can("view_tasks"):
        return groups.consumer(ctx.actor.role)
    found, failure = context.guarded(ctx.hub.batches.groups, actor=ctx.actor, entity=entity)
    if found is None:
        text = (failure or {}).get("message", "")
        level = "error" if (failure or {}).get("color") == "red" else "warning"
        return [page_title(groups.TITLE), notice(level, text)]
    return groups.render(
        found,
        can_draw=ctx.can("batch_link"),
        role=ctx.actor.role,
        sample_base=ctx.settings.forced_sample_base,
    )


def drawn_words(population: int, size: int) -> str:
    """ "Forced sample drawn: 9 of 612 reviews. Decide them in the inbox."."""
    noun = "review" if population == 1 else "reviews"
    return f"Forced sample drawn: {size:,} of {population:,} {noun}. Decide them in the inbox."


def draw(ctx: UiContext, group_key: Any, entity: Any) -> Drawn:
    """G2 without Dash: draws a forced sample from the group and names the batch's page to open, with the
    notification that gives the sample's size; a refusal's sentence and no page otherwise. Only a group's
    safe key and a published entity reach the service."""
    if not isinstance(group_key, str) or not SIGNATURE_KEY_RE.match(group_key):
        group_key = ""
    known = entity if isinstance(entity, str) and entity in ctx.badges.entities else ""
    found, failure = context.guarded(ctx.hub.batches.draw, group_key, actor=ctx.actor, entity=known)
    if found is None:
        return Drawn(None, failure or {})
    note = notify(drawn_words(found.population, found.sample_size), "teal", title=DRAWN)[0]
    return Drawn(f"/batch/{found.batch_id}", note)


def _stores(entity: str | None) -> list[Component]:
    return [
        dcc.Store(id=ids.GROUPS_VERSION, storage_type="memory", data=0),
        dcc.Store(id=ADDRESS, storage_type="memory", data={"entity": entity or ""}),
        dcc.Store(id=SETTLED_HERE, storage_type="memory", data=None),
    ]


def _page(children: list[Component], entity: str | None) -> Component:
    return html.Div(
        [html.Div(children, id=ids.GROUPS_LIST), *_stores(entity)],
        id=ids.GROUPS,
        className="mdm-groups",
    )


def skeleton() -> Component:
    """The page's components with empty children and no service call, for the validation layout."""
    return _page([], None)


def layout(ctx: UiContext, query: Mapping[str, str]) -> Component:
    """The Alike reviews page for the header's entity (`query["entity"]`, "" for all)."""
    entity = entity_of(ctx, query.get("entity"))
    return _page(view(ctx, entity), entity)


# ---------------------------------------------------------------------------------------------- callbacks


def _request(persona: Any) -> UiContext | None:
    found, _failure = context.guarded(context.current, persona)
    return found


def register(app) -> None:
    """Registers G1 (the list) and G2 (a draw) on `app`."""

    @app.callback(
        Output(ids.GROUPS_LIST, "children"),
        Input(ADDRESS, "data"),
        Input(SETTLED_HERE, "data"),
        Input(ids.GROUPS_VERSION, "data"),
        State(ids.PERSONA, "data"),
        prevent_initial_call=True,
    )
    def load_groups(address, _settled, _version, persona):
        request_ctx = _request(persona)
        if request_ctx is None:
            return no_update
        entity = address.get("entity") if isinstance(address, dict) else None
        return view(request_ctx, entity_of(request_ctx, entity))

    @app.callback(
        Output(ids.GROUPS_VERSION, "data", allow_duplicate=True),
        Input({"type": ids.GROUP_DRAW, "group": ALL, "entity": ALL}, "n_clicks"),
        State(ids.PERSONA, "data"),
        State(ids.GROUPS_VERSION, "data"),
        prevent_initial_call=True,
    )
    def draw_sample(_clicks, persona, version):
        trigger = dash_ctx.triggered_id
        if not isinstance(trigger, dict) or not dash_ctx.triggered or not dash_ctx.triggered[0].get("value"):
            return no_update  # a re-rendered button with no click must not act
        request_ctx = _request(persona)
        if request_ctx is None:
            return no_update
        done = draw(request_ctx, trigger.get("group"), trigger.get("entity"))
        if done.note:
            set_props(ids.NOTIFY, {"sendNotifications": [done.note]})
        if done.path is not None:
            set_props(ids.URL, {"pathname": done.path, "search": ""})
            return no_update  # the batch's page replaces this one
        return (version or 0) + 1  # the list is read again: the group may have changed meanwhile
