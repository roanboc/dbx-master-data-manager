"""The decide pane: a one-line header (kind, band, title, IDs, due, claim) and the reason, a notice, the
candidate choice, the compare table, one panel per candidate (the waterfall to scale and what would flip
it, then what changes), and a footer that stays on screen with the impact line and the actions with their
key hints and, as visible text, why any is unavailable (B.8.7).

The evidence reads top down in the order a steward weighs it, and the footer keeps the decision beside
it: at 1440 × 900 a two-candidate Organisation case shows without scrolling. It renders the `TaskCase`
the decision service built, masked by role; the only values in clear it ever shows are those of a reveal
(`revealed`), and only inside `DECIDE_COMPARE`. Every candidate's panel and impact line are rendered at
once and all but the chosen one are hidden, so choosing another (1, 2, 3 or a click) needs no request.
Nothing here is a dead button: an action the steward cannot take is disabled and its reason is written
out beside it, and what comes in a later story is said in one plain line.

Owner: INBOX (B.8.7).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from urllib.parse import quote

import dash_mantine_components as dmc
from dash import html
from dash.development.base_component import Component

from mdm.models.canonical import utcnow
from mdm.models.workbench import (
    ESCALATION_REASONS,
    SNOOZE_HOURS,
    Action,
    Candidate,
    Revealed,
    TaskCase,
    TaskRow,
)
from mdm.ui import ids
from mdm.ui.components import compare, impact, waterfall
from mdm.ui.components.band import band_chip, score_text
from mdm.ui.components.common import duration, empty_state, kbd, notice
from mdm.ui.components.icons import icon
from mdm.ui.components.provenance import source_href

#: the size of the pane's text (the pane sits beside the list, so it stays small)
PANE_TEXT = {"fontSize": "0.9rem"}
SUBHEADING = {"fontSize": "0.9rem", "fontWeight": 650, "margin": "10px 0 4px"}
#: what the pane says before a task is chosen
NOTHING_SELECTED = "No task is selected. Choose one in the list, or press J for the next one."
#: what the pane says when the selected task has been decided or is gone
DECIDED = "This task has been decided. Press J for the next one."
#: the snooze menu's items, per hour count of SNOOZE_HOURS
SNOOZE_LABELS = {1: "For 1 hour", 4: "For 4 hours", 24: "Until tomorrow (24 hours)"}
#: the escalate menu's items, per code of ESCALATION_REASONS
ESCALATION_LABELS = {
    "second_opinion": "Needs a second opinion",
    "outside_my_data": "Outside my data",
    "source_defect": "A source defect",
    "policy_question": "A policy question",
}
#: the decision each shape's preview describes, as `impact.render`'s verb
PREVIEW_VERBS = {"held_update": "approve", "golden_pair": "keep_apart", "golden": "keep"}
#: how each action's button looks: the decision that changes records is filled, the rest quieter
VARIANTS = {
    "link": "filled",
    "approve_update": "filled",
    "keep_orphan": "filled",
    "not_a_match": "light",
    "keep_apart": "light",
    "reject_update": "light",
}
#: the actions offered as menus rather than buttons
MENUS = ("snooze", "escalate")
#: the full-width toggle's labels (the page keeps it outside the pane, so it survives a new case)
FULL_WIDTH = "Full width"
SHOW_LIST = "Show the list"


def choice_keys(count: int) -> str:
    """ "1 or 2"; "1, 2 or 3"."""
    keys = [str(n) for n in range(1, max(count, 1) + 1)]
    return keys[0] if len(keys) == 1 else ", ".join(keys[:-1]) + " or " + keys[-1]


def link_label(target: str | None, count: int = 2) -> str:
    """ "Link to ORG-000123"; "Choose 1 or 2 to link" before a close call is chosen."""
    return f"Link to {target}" if target else f"Choose {choice_keys(count)} to link"


def _hint(key: str | None) -> Component | None:
    """A key hint for a button's right side, hidden from screen readers (the button carries
    `aria-keyshortcuts`)."""
    if not key:
        return None
    return html.Span(kbd(key), **{"aria-hidden": "true"})


def _aria(action: Action) -> dict:
    extra: dict = {}
    if action.key:
        extra["aria-keyshortcuts"] = action.key
    if not action.enabled:
        extra["aria-describedby"] = ids.ACTION_REASONS
    return extra


def action_button(action: Action, *, variant: str | None = None) -> Component:
    """One action's button: its label, its key hint, disabled with its reason when not enabled."""
    return dmc.Button(
        action.label,
        id=ids.action(action.decision),
        disabled=not action.enabled,
        variant=variant or VARIANTS.get(action.decision, "default"),
        size="xs",
        rightSection=_hint(action.key),
        className=f"mdm-action mdm-action-{action.decision}",
        **_aria(action),
    )


def visible_candidate(case: TaskCase, chosen: str | None = None) -> str | None:
    """The candidate whose panel shows: the one chosen, else the default, else (a close call) the first."""
    names = [c.master_id for c in case.candidates]
    if chosen in names:
        return chosen
    if case.default_candidate in names:
        return case.default_candidate
    return names[0] if names else None


def chosen_candidate(case: TaskCase, chosen: str | None = None) -> str | None:
    """The candidate a link names now: the one chosen, else the default (None in a close call)."""
    names = [c.master_id for c in case.candidates]
    if chosen in names:
        return chosen
    return case.default_candidate if case.default_candidate in names else None


def link_target(case: TaskCase, chosen: str | None = None) -> str | None:
    """The candidate the link button names: the chosen or default one, else the one shown (a candidate a
    rule blocks, whose link is then disabled with its reason); None in a close call not yet chosen."""
    if needs_choice(case, chosen):
        return None
    return chosen_candidate(case, chosen) or visible_candidate(case, chosen)


def needs_choice(case: TaskCase, chosen: str | None = None) -> bool:
    """A close call with no candidate chosen yet: L then moves to the choice instead of linking."""
    return case.close_call and chosen_candidate(case, chosen) is None


def _menu(menu_id: str, action: Action, label: str, items: Sequence[Component]) -> Component:
    return dmc.Menu(
        id=menu_id,
        position="top-start",
        shadow="md",
        trapFocus=True,
        returnFocus=True,
        children=[
            dmc.MenuTarget(
                dmc.Button(
                    action.label,
                    disabled=not action.enabled,
                    variant="default",
                    size="xs",
                    rightSection=_hint(action.key),
                    className=f"mdm-action mdm-menu-target mdm-{action.decision}-target",
                    **_aria(action),
                )
            ),
            dmc.MenuDropdown([dmc.MenuLabel(label), *items]),
        ],
    )


def snooze_menu(action: Action) -> Component:
    """Snooze (S): 1 hour, 4 hours, until tomorrow."""
    items = [dmc.MenuItem(SNOOZE_LABELS[hours], id=ids.snooze_option(hours)) for hours in SNOOZE_HOURS]
    return _menu(ids.SNOOZE_MENU, action, "Hide it from My queue", items)


def escalate_menu(action: Action) -> Component:
    """Escalate (E): the four reasons."""
    items = [
        dmc.MenuItem(ESCALATION_LABELS[code], id=ids.escalate_option(code)) for code in ESCALATION_REASONS
    ]
    return _menu(ids.ESCALATE_MENU, action, "Why escalate?", items)


def reasons(case: TaskCase, chosen: str | None = None) -> list[str]:
    """Each distinct reason an action is unavailable, in the order the actions come; for the link, only
    the reason of the candidate it names."""
    target = link_target(case, chosen)
    found: list[str] = []
    for action in case.actions:
        if action.enabled or not action.why_not:
            continue
        if action.decision == "link" and action.target not in (None, target):
            continue
        found.append(action.why_not)
    return list(dict.fromkeys(found))


def _moving() -> list[Component]:
    """Previous task (K) and Next task (J), as icon buttons at the end of the action row."""
    return [
        dmc.Tooltip(
            dmc.ActionIcon(
                icon("chevron-left"),
                id=ids.PREV_TASK,
                variant="default",
                size="md",
                className="mdm-move",
                **{"aria-label": "Previous task (K)", "aria-keyshortcuts": "K"},
            ),
            label="Previous task (K)",
            withArrow=True,
        ),
        dmc.Tooltip(
            dmc.ActionIcon(
                icon("chevron"),
                id=ids.NEXT_TASK,
                variant="default",
                size="md",
                className="mdm-move",
                **{"aria-label": "Next task (J)", "aria-keyshortcuts": "J"},
            ),
            label="Next task (J)",
            withArrow=True,
        ),
    ]


def actions(case: TaskCase, chosen: str | None = None) -> Component:
    """The action group: a button per `case.actions` (ACTION pattern IDs) with its key hint, the snooze
    and escalate menus, Previous and Next task, and ACTION_REASONS listing each distinct `why_not`,
    referenced by `aria-describedby` from every disabled button. The candidates' link actions are one
    button, "Link to <the chosen candidate>", whose label follows the choice; before a close call is
    chosen it reads "Choose 1 or 2 to link", quieter."""
    buttons: list[Component] = []
    links = {a.target: a for a in case.actions if a.decision == "link"}
    linked = False
    for action in case.actions:
        if action.decision == "link":
            if linked:
                continue
            linked = True
            waiting = needs_choice(case, chosen)
            shown = None if waiting else link_target(case, chosen)
            named = links.get(shown)
            first_why = next((a.why_not for a in links.values() if a.why_not), None)
            if waiting:
                enabled = any(a.enabled for a in links.values())
                why = None if enabled else first_why
            else:
                enabled = named.enabled if named is not None else False
                why = named.why_not if named is not None else first_why
            label = link_label(shown, len(case.candidates))
            merged = Action("link", label, action.key, enabled, why, target=shown)
            buttons.append(action_button(merged, variant="light" if waiting else None))
        elif action.decision == "snooze":
            buttons.append(snooze_menu(action))
        elif action.decision == "escalate":
            buttons.append(escalate_menu(action))
        else:
            buttons.append(action_button(action))
    why = reasons(case, chosen)
    return html.Div(
        [
            html.H3("Decide", className="mdm-sr-only"),
            html.Div([*buttons, html.Span(_moving(), className="mdm-moving")], className="mdm-actions"),
            html.Div(
                [html.P(text, className="mdm-action-reason") for text in why],
                id=ids.ACTION_REASONS,
                className="mdm-action-reasons",
            ),
        ],
        className="mdm-decide-actions",
    )


# ---------------------------------------------------------------------------------------------- the header


def due_line(row: TaskRow, now: datetime) -> str | None:
    """ "Due in 7 h 40 min", "Breached 2 h ago", "In the tray"; None without a due time."""
    if row.staged is not None:
        return "In the tray"
    if row.due_at is None:
        return None
    text = duration(row.due_at - now)
    return text[:1].upper() + text[1:] if text.startswith("breached") else f"Due in {text}"


def claim_line(case: TaskCase) -> str:
    """ "Claimed by you", "Claimed by Data steward (persona)", "Not claimed"."""
    return f"Claimed by {case.claimed_by}" if case.claimed_by else "Not claimed"


def record_href(master_id: str) -> str:
    """The golden record view of a master ID, quoted."""
    return f"/record/{quote(master_id, safe='')}"


def subject_links(row: TaskRow) -> list[Component]:
    """The subject's IDs as links: a source key to its source record, a master ID to its golden record."""
    parts: list[Component] = []
    for index, ref in enumerate(part.strip() for part in row.subject.split("·")):
        if not ref:
            continue
        if index:
            parts.append(html.Span(" and "))
        href = source_href(ref) if ":" in ref else record_href(ref)
        parts.append(dmc.Anchor(ref, href=href or "/", className="mdm-subject-link"))
    return parts


def row_band_chip(row: TaskRow) -> Component | None:
    """The band chip a row carries: "79 review"; "95 · kept apart by a rule" when a cannot-link rule
    holds whatever the score says; none on a held update, which is approved or rejected, not scored."""
    if row.kind == "held" or (row.band is None and row.score is None):
        return None
    if row.kept_apart:
        text = (
            f"{score_text(row.score)} · kept apart by a rule"
            if row.score is not None
            else "kept apart by a rule"
        )
        return html.Span(text, className="mdm-band mdm-band-distinct")
    return band_chip(row.band, row.score)


def header(case: TaskCase, now: datetime) -> Component:
    """One line: kind, band, the title (masked), the subject's IDs, due and claim; then the reason."""
    row = case.row
    parts: list[Component | str] = [
        dmc.Badge(row.kind_label, variant="light", color="indigo", tt="none", className="mdm-kind-badge")
    ]
    if case.shape == "golden_pair" and any(c.blocked_by for c in case.candidates):
        row = replace(row, kept_apart=True)  # a rule keeps the pair apart, whatever the score says
    chip = row_band_chip(row)
    if chip is not None:
        parts.append(chip)
    if row.escalated:
        parts.append(dmc.Badge("Escalated", variant="light", color="orange", tt="none"))
    if row.snoozed_until is not None:
        parts.append(dmc.Badge("Snoozed", variant="light", color="gray", tt="none"))
    meta: list = [*subject_links(row)]
    due = due_line(row, now)
    if due:
        meta.extend([" · ", icon("clock"), " ", due])
    meta.extend([" · ", claim_line(case)])
    return html.Div(
        [
            html.Div(
                [
                    *parts,
                    html.H2(row.title, className="mdm-decide-title"),
                    html.Span(meta, className="mdm-decide-meta"),
                ],
                className="mdm-decide-headline",
            ),
            html.P(case.reason_text, className="mdm-decide-reason"),
        ],
        className="mdm-decide-header",
    )


# ---------------------------------------------------------------------------------------------- candidates


def _system(member: str) -> str:
    return member.partition(":")[0] or member


def separation(case: TaskCase) -> list[str]:
    """What tells the two closest candidates apart, and whether they look like one record twice: "What
    separates them: nothing in the score; candidate 1's best match comes from crm, candidate 2's from
    finance." and "These two records may be duplicates of each other." Built from comparison labels,
    weights, source systems and the values the table shows that are not personal."""
    if len(case.candidates) < 2:
        return []
    first, second = case.candidates[0], case.candidates[1]
    weights = {s.label.rpartition(" ")[0] or s.label: s.weight for s in first.steps if s.comparison}
    others = {s.label.rpartition(" ")[0] or s.label: s.weight for s in second.steps if s.comparison}
    differing = [name for name in weights if abs(weights[name] - others.get(name, 0.0)) >= 0.05]
    if differing:
        names = [n[:1].lower() + n[1:] if n[1:2].islower() else n for n in differing]
        what = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        lead = f"What separates them: the {what}"
    else:
        lead = "Nothing in the score separates them"
    systems = (_system(first.member), _system(second.member))
    if systems[0] != systems[1]:
        lead += f"; 1 matched through {systems[0]}, 2 through {systems[1]}"
    lines = [lead + "."]
    plain = [r for r in case.compare if not r.personal and any(r.agreement) and len(r.values) >= 3]
    shared = [r for r in plain if r.values[1] is not None and r.values[2] is not None]
    if shared and all(r.values[1] == r.values[2] for r in shared) and any(r.critical for r in shared):
        lines.append("These two records may be duplicates of each other.")
    return lines


def close_call_box(case: TaskCase, chosen: str | None = None) -> Component:
    """The close call: the scores, how to choose, what separates the candidates, and the choice."""
    count = len(case.candidates)
    scores = " and ".join(score_text(c.score) for c in case.candidates[:2])
    words: list[Component | str] = []
    if needs_choice(case, chosen):
        words.append(
            html.Strong(
                f"Close call ({scores}): choose {choice_keys(count)} before you link. ",
                className="mdm-close-lead",
            )
        )
    words.append(" ".join(separation(case)))
    lines: list[Component] = [html.P(words, className="mdm-close-line"), candidate_choice(case, chosen)]
    return html.Div(
        lines,
        className="mdm-notice mdm-notice-warning mdm-close-call",
        role="group",
        **{"aria-label": "Close call"},
    )


def candidate_choice(case: TaskCase, chosen: str | None = None) -> Component:
    """The radio group (CANDIDATE_CHOICE): one option per candidate, "1 · ORG-000123 · 79 review", each
    carrying `data-candidate-index`; its value the chosen or default candidate (none in a close call)."""
    options = [
        dmc.Radio(
            label=f"{c.index} · {c.master_id} · {score_text(c.score)} "
            + ("· kept apart by a rule" if c.blocked_by else waterfall.band_words(c.band)),
            value=c.master_id,
            size="md",  # a 24 px target (WCAG 2.2 success criterion 2.5.8)
            **{"data-candidate-index": str(c.index)},
        )
        for c in case.candidates
    ]
    label = "Choose the candidate to link" if case.shape == "source" else "Show the candidate"
    return dmc.RadioGroup(
        dmc.Group(options, gap="md", mt=2),
        id=ids.CANDIDATE_CHOICE,
        label=label,
        value=chosen_candidate(case, chosen),
        size="sm",
        className="mdm-candidate-choice",
    )


def flip(candidate: Candidate) -> Component:
    """What would flip it, in the steward's words, and the hard-rule or blocked sentence."""
    parts: list[Component] = [html.H4("What would flip it", className="mdm-flip-heading")]
    if candidate.flip:
        parts.append(html.Ul([html.Li(sentence) for sentence in candidate.flip], className="mdm-flip-list"))
    else:
        parts.append(html.P("No single comparison would move it to another band.", className="mdm-flip-none"))
    if candidate.hard_rule:
        parts.append(html.P(candidate.hard_rule, className="mdm-flip-rule"))
    if candidate.blocked_by:
        parts.append(html.P(candidate.blocked_by, className="mdm-flip-rule"))
    return html.Div(parts, className="mdm-flip")


def candidate_panel(case: TaskCase, candidate: Candidate, *, shown: bool) -> Component:
    """One candidate's panel (CANDIDATE_PANEL): its heading, whose ID links to the record, the why (the waterfall to scale and what
    would flip it, one disclosure "." toggles), and what changes if linked, collapsed."""
    parts: list = [
        html.Div(
            [
                html.H3(
                    [
                        f"Candidate {candidate.index}: ",
                        # underlined: inside a heading, colour alone would not tell the link apart
                        dmc.Anchor(
                            candidate.master_id,
                            href=record_href(candidate.master_id),
                            inherit=True,
                            underline="always",
                        ),
                    ],
                    className="mdm-decide-subheading",
                ),
                html.Span(
                    [
                        html.Span(candidate.title, className="mdm-candidate-title"),
                        f" · best match {candidate.member}",
                    ],
                    className="mdm-candidate-meta",
                ),
            ],
            className="mdm-candidate-head",
        ),
        html.Details(
            [
                html.Summary(waterfall.title(candidate), className="mdm-why-summary"),
                waterfall.render(candidate),
                flip(candidate),
            ],
            open=True,
            className="mdm-why",
        ),
    ]
    if candidate.preview is not None and impact.changed_rows(candidate.preview):
        parts.append(impact.changes(candidate.preview, verb="link"))
    return html.Div(
        parts,
        id=ids.candidate_panel(candidate.master_id),
        className="mdm-candidate-panel" if shown else "mdm-candidate-panel mdm-hidden",
        **{
            "data-candidate-index": str(candidate.index),
            "data-master-id": candidate.master_id,
            "data-blocked": "yes" if candidate.blocked_by else "no",
        },
    )


def candidates(case: TaskCase, chosen: str | None = None) -> Component:
    """Every candidate's panel inside WHY_SECTION, all but the visible one hidden."""
    visible = visible_candidate(case, chosen)
    return html.Div(
        [candidate_panel(case, c, shown=c.master_id == visible) for c in case.candidates],
        id=ids.WHY_SECTION,
        className="mdm-why-section",
    )


def impact_lines(case: TaskCase, chosen: str | None = None) -> list[Component]:
    """The footer's impact line: one per candidate (CANDIDATE_IMPACT), all but the visible one hidden; or
    the shape's own."""
    if case.candidates and any(c.preview is not None for c in case.candidates):
        visible = visible_candidate(case, chosen)
        return [
            html.Div(
                impact.line(c.preview, verb="link") if c.preview is not None else impact.none_line(),
                id=ids.candidate_impact(c.master_id),
                className="mdm-candidate-impact"
                if c.master_id == visible
                else "mdm-candidate-impact mdm-hidden",
            )
            for c in case.candidates
        ]
    if case.preview is not None:
        return [impact.line(case.preview, verb=PREVIEW_VERBS.get(case.shape, "approve"))]
    return []


def _record_path(case: TaskCase) -> str | None:
    """The golden record Enter opens when no candidate is shown: the preview's, else the first subject ID."""
    if case.preview is not None and case.preview.master_id:
        return record_href(case.preview.master_id)
    first = case.row.subject.split("·")[0].strip()
    if first and ":" not in first:
        return record_href(first)
    return None


def staged_line(case: TaskCase) -> Component | None:
    """ "In the tray: Link crm:C000123 to ORG-000123. It commits at 12:04:31 UTC unless you undo it (U)."
    A fixed time: the live countdown is the tray's."""
    staged = case.staged
    if staged is None:
        return None
    at = staged.deadline.strftime("%H:%M:%S")
    if staged.mine:
        text = f"In the tray: {staged.label}. It commits at {at} UTC unless you undo it (U)."
    else:
        text = f"Another steward's decision waits in the tray: {staged.label}. It commits at {at} UTC."
    return notice("warning", text)


def render(
    case: TaskCase,
    *,
    revealed: Revealed | None = None,
    chosen: str | None = None,
    now: datetime | None = None,
) -> Component:
    """The pane (DECIDE_PANE's children) for `case`; `revealed` renders the compare table in clear once;
    `chosen` is the candidate the steward chose on this task (kept when the same task renders again)."""
    now = now if now is not None else utcnow()
    body: list = [header(case, now)]
    staged = staged_line(case)
    if staged is not None:
        body.append(staged)
    if case.notice:
        body.append(notice("info", case.notice))
    if case.close_call:
        body.append(close_call_box(case, chosen))
    elif len(case.candidates) >= 2:
        body.append(candidate_choice(case, chosen))
    columns = revealed.columns if revealed is not None and revealed.columns else case.columns
    rows = revealed.compare if revealed is not None else case.compare
    body.append(
        html.Div(
            compare.render(
                columns,
                rows,
                revealable=bool(case.revealable) and revealed is None,
                revealed=revealed is not None,
            ),
            id=ids.DECIDE_COMPARE,
        )
    )
    if case.candidates:
        body.append(candidates(case, chosen))
    if (
        case.preview is not None
        and case.preview.rows
        and not any(c.preview is not None for c in case.candidates)
    ):
        verb = PREVIEW_VERBS.get(case.shape, "approve")
        body.append(
            html.Div(impact.changes(case.preview, verb=verb, open_=True), className="mdm-shape-preview")
        )
    footer = html.Div(
        [*impact_lines(case, chosen), actions(case, chosen)],
        className="mdm-decide-footer",
    )
    extra = {"data-task-id": case.row.task_id}
    path = _record_path(case)
    if path:
        extra["data-open-record"] = path
    if needs_choice(case, chosen):
        extra["data-needs-choice"] = "yes"
    return html.Section(
        [html.Div(body, className="mdm-decide-body"), footer],
        className="mdm-decide",
        style=PANE_TEXT,
        **{"aria-label": "Decide", **extra},
    )


def empty_pane(text: str | Sequence[Component | str] = NOTHING_SELECTED) -> Component:
    """The pane before a task is chosen, after it was decided, or on an empty view."""
    return html.Section(
        empty_state(text) if isinstance(text, str) else html.P(list(text), className="mdm-empty"),
        className="mdm-decide mdm-decide-empty",
        **{"aria-label": "Decide"},
    )
