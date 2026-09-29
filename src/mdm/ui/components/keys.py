"""The key help overlay and the switch that turns single-key shortcuts off (WCAG 2.2 success criterion
2.1.4, decision 18); the listener itself is `assets/keys.js`.

The switch is kept per browser (`KEYS_ENABLED`, local storage) and mirrored on `<body data-mdm-keys>`,
which the listener reads. The switch and the store are kept in step by one clientside callback, since two
callbacks feeding each other would be a cycle Dash refuses.

Owner: SHELL (B.8.6).
"""

from __future__ import annotations

import dash_mantine_components as dmc
from dash import ClientsideFunction, Input, Output, html
from dash.development.base_component import Component

from mdm.ui import ids
from mdm.ui.components.common import kbd

#: (key, what it does), in the order the overlay lists them
KEY_HELP = (
    ("J", "Next task"),
    ("K", "Previous task"),
    ("↓, ↑", "Next or previous task, in the list"),
    ("1, 2, 3", "Choose a candidate, or a golden record in a blind review"),
    ("L", "Link to the chosen candidate; in a blind review, it belongs there (a pair: the same)"),
    ("N", "Not a match, or keep apart; in a blind review, none of these (a pair: not the same)"),
    ("A", "Approve the update, keep as it is, or keep the first decision"),
    ("R", "Reject the update"),
    ("C", "Claim"),
    ("S", "Snooze"),
    ("E", "Escalate"),
    ("U", "Undo"),
    ("G", "Alike reviews: open reviews grouped by signature"),
    ("F", "Decide pane full width"),
    (".", "Show or hide why"),
    ("Enter", "Open the candidate's record"),
    ("?", "This help"),
)
#: what the overlay says above the list
INTRODUCTION = (
    "On the inbox, single keys move and decide when no field, list or menu has focus. "
    "Moving never claims a task; your first decision does, and it waits in the tray before it commits."
)
SWITCH_LABEL = "Use single-key shortcuts"
SWITCH_DESCRIPTION = (
    "Turn them off if they get in the way of speech input or a screen reader. "
    "Every shortcut has a button that does the same."
)


def _keys(text: str) -> list:
    parts: list = []
    for index, key in enumerate(text.split(", ")):
        if index:
            parts.append(" ")
        parts.append(kbd(key))
    return parts


def help_modal() -> Component:
    """The overlay (HELP_MODAL) listing KEY_HELP, with the switch "Use single-key shortcuts"
    (KEYS_SWITCH), kept per browser."""
    rows = [html.Tr([html.Td(_keys(key)), html.Td(what)]) for key, what in KEY_HELP]
    return dmc.Modal(
        id=ids.HELP_MODAL,
        title="Keyboard shortcuts",
        opened=False,
        size="lg",
        closeButtonProps={"aria-label": "Close the shortcuts"},
        children=[
            html.P(INTRODUCTION, className="mdm-modal-text"),
            dmc.Switch(
                id=ids.KEYS_SWITCH,
                label=SWITCH_LABEL,
                description=SWITCH_DESCRIPTION,
                checked=True,
                mb="md",
            ),
            html.Table(
                [
                    html.Caption("Shortcuts on the inbox", className="mdm-sr-only"),
                    html.Thead(html.Tr([html.Th("Key", scope="col"), html.Th("What it does", scope="col")])),
                    html.Tbody(rows),
                ],
                className="mdm-table mdm-keys-table",
            ),
        ],
    )


def register(app) -> None:
    """Registers S11 (help open) and S12 (the switch and the store kept in step, with the body flag), both
    clientside, from `assets/keys.js` (namespace `mdm_shell`)."""
    app.clientside_callback(
        ClientsideFunction(namespace="mdm_shell", function_name="openHelp"),
        Output(ids.HELP_MODAL, "opened"),
        Input(ids.HELP_OPEN, "n_clicks"),
        Input(ids.HELP_OPEN_NAV, "n_clicks"),
        prevent_initial_call=True,
    )
    app.clientside_callback(
        ClientsideFunction(namespace="mdm_shell", function_name="keysSwitch"),
        Output(ids.KEYS_ENABLED, "data"),
        Output(ids.KEYS_SWITCH, "checked"),
        Input(ids.KEYS_SWITCH, "checked"),
        Input(ids.KEYS_ENABLED, "data"),
    )
