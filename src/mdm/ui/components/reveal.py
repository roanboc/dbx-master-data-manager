"""The page-owned reveal modal: showing personal values asks for one of four reasons, and each value
shown is logged with it (decision 20). Each page that offers a reveal (`ids.REVEAL_PAGES`) builds its
own modal and registers its own confirm callback on its own IDs; `register_toggle` gives a page the open
and cancel callbacks, clientside.

The reasons are codes (`REVEAL_REASONS`), so no free text reaches the access log. Confirming with no
reason chosen reveals nothing and says `REASON_REQUIRED`.

Owner: SHELL (B.8.6).
"""

from __future__ import annotations

import dash_mantine_components as dmc
from dash import Input, Output, html
from dash.development.base_component import Component

from mdm.models.workbench import REVEAL_REASONS
from mdm.ui import ids
from mdm.ui.components.icons import icon

#: the reasons as a person reads them, per code of REVEAL_REASONS; "deciding_task" reads "Deciding about
#: this record" on the record and source pages
REASON_LABELS = {
    "deciding_task": "Deciding this task",
    "source_defect": "Checking a source defect",
    "subject_request": "Answering the person's request",
    "audit_check": "An audit check",
}
#: "deciding_task" on a page that shows a record rather than a task
RECORD_REASON_LABEL = "Deciding about this record"
#: what the modal says before a value is shown
EXPLANATION = (
    "The values will be shown until you leave this task or record. "
    "Your reason is logged with your name, one entry per value."
)
#: what a confirm without a reason says (the sentence of `reason_required`)
REASON_REQUIRED = "Choose a reason to show personal values."


def reason_label(code: str, page: str) -> str:
    """How the reason `code` reads on `page`."""
    if code == "deciding_task" and page != "decide":
        return RECORD_REASON_LABEL
    return REASON_LABELS[code]


def checked_reason(value: object) -> str | None:
    """The reason code the modal's radio group holds, or None when none (or anything else) is chosen."""
    return value if isinstance(value, str) and value in REVEAL_REASONS else None


def open_button(page: str) -> Component:
    """ "Show values" (`ids.reveal(REVEAL_OPEN, page)`): a quiet grey button with the eye, like the pane's
    work actions, since it changes no record."""
    return dmc.Button(
        "Show values",
        id=ids.reveal(ids.REVEAL_OPEN, page),
        variant="subtle",
        color="gray",
        size="xs",
        leftSection=icon("eye"),
        className="mdm-reveal-open",
    )


def modal(page: str) -> Component:
    """The modal "Show personal values" for `page`: the sentence on logging, the reasons with no default,
    "Show values" and "Cancel"."""
    reasons = [dmc.Radio(label=reason_label(code, page), value=code) for code in REVEAL_REASONS]
    return dmc.Modal(
        id=ids.reveal(ids.REVEAL_MODAL, page),
        title="Show personal values",
        opened=False,
        centered=True,
        closeButtonProps={"aria-label": "Close without showing values"},
        children=[
            html.P(EXPLANATION, className="mdm-modal-text"),
            dmc.RadioGroup(
                id=ids.reveal(ids.REVEAL_REASON, page),
                label="Why do you need to see them?",
                value=None,
                children=dmc.Stack(reasons, gap="xs", mt="xs"),
            ),
            dmc.Group(
                [
                    dmc.Button("Cancel", id=ids.reveal(ids.REVEAL_CANCEL, page), variant="default"),
                    dmc.Button("Show values", id=ids.reveal(ids.REVEAL_CONFIRM, page)),
                ],
                justify="flex-end",
                mt="md",
            ),
        ],
    )


def register_toggle(app, page: str) -> None:
    """Registers `page`'s open and cancel callbacks (clientside): "Show values" opens the modal, "Cancel"
    closes it. The page's own confirm callback closes it after a reveal (`allow_duplicate`)."""
    app.clientside_callback(
        """(opened, cancelled) => {
            const t = window.dash_clientside.callback_context.triggered_id;
            if (!t) { return window.dash_clientside.no_update; }
            return t.type === "reveal-open";
        }""",
        Output(ids.reveal(ids.REVEAL_MODAL, page), "opened"),
        Input(ids.reveal(ids.REVEAL_OPEN, page), "n_clicks"),
        Input(ids.reveal(ids.REVEAL_CANCEL, page), "n_clicks"),
        prevent_initial_call=True,
    )
