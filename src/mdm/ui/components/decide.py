"""The decide pane: a header (the title with its band; a muted line with the kind, the IDs, due and claim)
and the reason, a notice, the candidate choice, the compare table, one panel per candidate (the waterfall to scale and what would flip
it, then what changes), and a footer that stays on screen with the impact line and the actions with their
key hints and, as visible text, why any is unavailable (B.8.7).

The evidence reads top down in the order a steward weighs it, and the footer keeps the decision beside
it: at 1440 × 900 a two-candidate Organisation case shows without scrolling. It renders the `TaskCase`
the decision service built, masked by role; the only values in clear it ever shows are those of a reveal
(`revealed`), and only inside `DECIDE_COMPARE`. Every candidate's panel and impact line are rendered at
once and all but the chosen one are hidden, so choosing another (1, 2, 3 or a click) needs no request.
Nothing here is a dead button: an action the steward cannot take is disabled and its reason is written
out beside it, and what comes in a later story is said in one plain line. The pane stays calm: no box
around a section, one filled button (the decision that changes records), the work actions as quiet text
buttons, and colour only where it carries meaning (the band, a disagreement, a warning).

A quality sample is decided blind (story 3.2): the record and the golden records it might belong to, with
no score, band, waterfall, flip, preview, impact line, first decision or link to a record view, since each
would give its placement away. The steward chooses one (1, 2, 3, as in a close call, never by default) and
answers "Belongs to <ID>" or "Belongs to none of these", both outlined, so the pane nudges no answer; a
pair kept apart is answered "They are the same" or "They are not the same". A dispute is decided in the
open, with the same equal weight. While the quality breaker has paused automatic linking, the pane of a
task of that entity says so, except a blind one, which stays free of anything about the first decision's
kind.

A review of a batch's forced sample (story 3.3) says so at the top, and shows every decision at equal
weight, none filled, as blind review does, so the pane never nudges the measurement. A decision that
disagrees with the case's suggestion ("Not a match", or a link to another candidate) names the comparison
that misled, in "Which comparison misled?" under the decisions (`SPLIT_CHOICE`): the reviews that share
the record's value on it leave the batch when the decision commits. A review a batch holds says it is part
of the batch, and its decisions are disabled with why.

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

from mdm.models.batch import SPLIT_ALL
from mdm.models.canonical import utcnow
from mdm.models.tasks import WAITS_TEXT, waits_for_restore
from mdm.models.workbench import (
    DECISIONS,
    ESCALATION_REASONS,
    SNOOZE_HOURS,
    Action,
    Candidate,
    Mark,
    Revealed,
    TaskCase,
    TaskRow,
)
from mdm.ui import ids
from mdm.ui.components import breaker, compare, impact, waterfall
from mdm.ui.components.band import band_chip, score_text
from mdm.ui.components.common import duration, empty_state, kbd, notice
from mdm.ui.components.icons import icon
from mdm.ui.components.provenance import source_href

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
#: how each action's button looks: the decision that changes records is filled, the one primary; its
#: alternatives (not a match, keep apart, reject) are outlined ("default"); the work actions are quiet
VARIANTS = {
    "link": "filled",
    "approve_update": "filled",
    "keep_orphan": "filled",
}
#: the work actions (claim, snooze, escalate): quiet text buttons in grey, never a decision's weight
WORK = ("claim", "snooze", "escalate")
QUIET = {"variant": "subtle", "color": "gray"}
#: the shapes whose decisions are all outlined, none filled: a blind measurement and a dispute, where the
#: pane must not nudge the answer (`actions(quiet=True)`; the pane carries `data-quiet`, so choosing a
#: candidate in the browser keeps the link outlined too)
QUIET_SHAPES = ("blind", "blind_pair", "disputed", "disputed_pair")
#: the actions offered as menus rather than buttons
MENUS = ("snooze", "escalate")
#: the full-width toggle's labels (the page keeps it outside the pane, so it survives a new case)
FULL_WIDTH = "Full width"
SHOW_LIST = "Show the list"
#: a forced-sample review's "Which comparison misled?" (story 3.3): its disclosure, its label, the choice
#: that ends bulk for the whole batch, and what the choice does
SPLIT_SUMMARY = "Not a match, or another golden record? Name the comparison that misled"
SPLIT_LABEL = "Which comparison misled?"
SPLIT_EVERY = "Every alike review in this batch"
SPLIT_NOTE = (
    "The reviews whose record holds the same value on it leave the batch, to be decided one by one. Values "
    "stay hidden: the hub compares them and, when they are personal, logs each record that leaves."
)
OPEN_BATCH = "Open the batch"


def choice_keys(count: int) -> str:
    """ "1 or 2"; "1, 2 or 3"."""
    keys = [str(n) for n in range(1, max(count, 1) + 1)]
    return keys[0] if len(keys) == 1 else ", ".join(keys[:-1]) + " or " + keys[-1]


def link_label(target: str | None, count: int = 2) -> str:
    """ "Link to ORG-000123"; "Choose 1 or 2 to link" before a close call is chosen."""
    return f"Link to {target}" if target else f"Choose {choice_keys(count)} to link"


def blind_label(target: str | None, count: int = 1) -> str:
    """ "Belongs to ORG-000123"; "Choose 1, 2 or 3 first" before a blind review's choice."""
    return f"Belongs to {target}" if target else f"Choose {choice_keys(count)} first"


def choosing(case: TaskCase) -> str:
    """The decision whose button follows the choice on screen: a blind review's `blind_link` (a record's,
    not a pair's, which names its second golden record itself), else the candidates' `link`."""
    return "blind_link" if case.blind and case.shape == "blind" else "link"


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


def button_look(decision: str, *, variant: str | None = None, quiet: bool = False) -> dict:
    """The variant (and colour) of an action's button: a work action quiet and grey; with `quiet`, every
    decision outlined, so the pane nudges no answer; else `variant`, or the decision's own look."""
    if decision in WORK:
        return dict(QUIET)
    if quiet:
        return {"variant": "default"}
    return {"variant": variant or VARIANTS.get(decision, "default")}


def action_button(action: Action, *, variant: str | None = None, quiet: bool = False) -> Component:
    """One action's button: its label, its key hint, disabled with its reason when not enabled."""
    return dmc.Button(
        action.label,
        id=ids.action(action.decision),
        disabled=not action.enabled,
        size="xs",
        rightSection=_hint(action.key),
        className=f"mdm-action mdm-action-{action.decision}",
        **button_look(action.decision, variant=variant, quiet=quiet),
        **_aria(action),
    )


def chosen_choice(case: TaskCase, chosen: str | None = None) -> str | None:
    """The golden record a blind review chose: one of `case.choices`, never a default."""
    return chosen if chosen in {c.master_id for c in case.choices} else None


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
    rule blocks, whose link is then disabled with its reason); None in a close call not yet chosen. On a
    blind review, the golden record chosen, else None."""
    if case.blind:
        return chosen_choice(case, chosen)
    if needs_choice(case, chosen):
        return None
    return chosen_candidate(case, chosen) or visible_candidate(case, chosen)


def needs_choice(case: TaskCase, chosen: str | None = None) -> bool:
    """A close call with no candidate chosen yet, or a blind review of a record with golden records
    offered and none chosen: L then moves to the choice instead of deciding."""
    if case.blind:
        return case.shape == "blind" and bool(case.choices) and chosen_choice(case, chosen) is None
    return case.close_call and chosen_candidate(case, chosen) is None


def locked(case: TaskCase) -> bool:
    """Whether no decision can be taken now, whatever is chosen (the role, the steward's own first decision,
    a decision in the tray, another's claim): choosing in the browser then enables nothing."""
    offered = [a for a in case.actions if a.decision in DECISIONS]
    return bool(offered) and not any(a.enabled for a in offered)


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
                    size="xs",
                    rightSection=_hint(action.key),
                    **QUIET,
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
    """Each distinct reason an action is unavailable, in the order the actions come; for the link (a blind
    review's answer), only the reason of the candidate (the golden record) it names."""
    target = link_target(case, chosen)
    follows = choosing(case)
    found: list[str] = []
    for action in case.actions:
        if action.enabled or not action.why_not:
            continue
        if action.decision == follows and action.target not in (None, target):
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
                **QUIET,
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
                **QUIET,
                size="md",
                className="mdm-move",
                **{"aria-label": "Next task (J)", "aria-keyshortcuts": "J"},
            ),
            label="Next task (J)",
            withArrow=True,
        ),
    ]


def actions(case: TaskCase, chosen: str | None = None, *, quiet: bool = False) -> Component:
    """The action group: a button per `case.actions` (ACTION pattern IDs) with its key hint, the snooze
    and escalate menus, Previous and Next task, and ACTION_REASONS listing each distinct `why_not`,
    referenced by `aria-describedby` from every disabled button. The candidates' link actions are one
    button, "Link to <the chosen candidate>", whose label follows the choice; before a close call is
    chosen it reads "Choose 1 or 2 to link", quieter. A blind review's answers are one button the same
    way, "Belongs to <the golden record chosen>", reading "Choose 1, 2 or 3 first" before a choice.
    `quiet` outlines every decision, none filled (a measurement or a dispute, where the pane must not
    nudge the answer). Claim, Snooze and Escalate sit in one group after the decisions, so the row wraps
    between the decisions and the work, never leaving one of them alone on a line."""
    buttons: list[Component] = []
    work: list[Component] = []  # claim, snooze and escalate: they wrap as one group
    follows = choosing(case)
    links = {a.target: a for a in case.actions if a.decision == follows}
    linked = False
    for action in case.actions:
        if action.decision == follows:
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
            if follows == "blind_link":
                label = blind_label(shown, len(case.choices))
            else:
                label = link_label(shown, len(case.candidates))
            merged = Action(follows, label, action.key, enabled, why, target=shown)
            buttons.append(action_button(merged, variant="light" if waiting else None, quiet=quiet))
        elif action.decision == "snooze":
            work.append(snooze_menu(action))
        elif action.decision == "escalate":
            work.append(escalate_menu(action))
        elif action.decision == "claim":
            work.append(action_button(action, quiet=quiet))
        else:
            buttons.append(action_button(action, quiet=quiet))
    why = reasons(case, chosen)
    return html.Div(
        [
            html.H3("Decide", className="mdm-sr-only"),
            html.Div(
                [
                    # the decisions and the work wrap inside their own group, so Previous and Next keep
                    # their place at the end of the first line
                    html.Div(
                        [*buttons, html.Div(work, className="mdm-actions-work")],
                        className="mdm-actions-main",
                    ),
                    html.Span(_moving(), className="mdm-moving"),
                ],
                className="mdm-actions",
            ),
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
    if waits_for_restore(row.due_at):
        return WAITS_TEXT
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


def subject_text(row: TaskRow) -> str:
    """The subject's IDs as plain words, "crm:C001409"; "ORG-000211 and ORG-000388" for a pair."""
    return " and ".join(part.strip() for part in row.subject.split("·") if part.strip())


def meta_line(case: TaskCase, row: TaskRow, now: datetime) -> list:
    """The header's muted line: "Review · crm:C000123 · Due in 7 h 40 min · Not claimed", with
    "Escalated" and "Snoozed" as words when they are so; the IDs link to their records, except on a blind
    review, where a record view would show where the record is placed now."""
    subject = [subject_text(row)] if case.blind else subject_links(row)
    meta: list = [html.Span(row.kind_label, className="mdm-decide-kind"), " · ", *subject]
    due = due_line(row, now)
    if due:
        meta.extend([" · ", due])
    meta.extend([" · ", claim_line(case)])
    if row.escalated:
        meta.append(" · Escalated")
    if row.snoozed_until is not None:
        meta.append(" · Snoozed")
    return meta


def header(case: TaskCase, now: datetime) -> Component:
    """The title (masked) with its band; a muted line with the kind, the subject's IDs, due and claim;
    then the reason."""
    row = case.row
    if case.shape == "golden_pair" and any(c.blocked_by for c in case.candidates):
        row = replace(row, kept_apart=True)  # a rule keeps the pair apart, whatever the score says
    headline: list[Component] = [html.H2(row.title, className="mdm-decide-title")]
    # a dispute's band would be the first decision's, beside the waterfall of the record as it is now
    chip = None if case.blind or case.shape in ("disputed", "disputed_pair") else row_band_chip(row)
    if chip is not None:
        headline.append(chip)
    return html.Div(
        [
            html.Div(headline, className="mdm-decide-headline"),
            html.P(meta_line(case, row, now), className="mdm-decide-meta"),
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


def blind_choice(case: TaskCase, chosen: str | None = None) -> Component:
    """A blind review's choice (CANDIDATE_CHOICE, as in a close call): one option per golden record
    offered, "1 · ORG-000123" with no score or band, each carrying `data-candidate-index` so 1, 2 and 3
    choose it; never a default."""
    options = [
        dmc.Radio(
            label=f"{c.index} · {c.master_id}",
            value=c.master_id,
            size="md",  # a 24 px target (WCAG 2.2 success criterion 2.5.8)
            **{"data-candidate-index": str(c.index), "data-master-id": c.master_id},
        )
        for c in case.choices
    ]
    return dmc.RadioGroup(
        dmc.Group(options, gap="md", mt=2),
        id=ids.CANDIDATE_CHOICE,
        label="Choose the golden record it belongs to",
        value=chosen_choice(case, chosen),
        size="sm",
        className="mdm-candidate-choice mdm-blind-choice",
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
    the shape's own; none on a blind review."""
    if case.blind:
        return []
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
    """The golden record Enter opens when no candidate is shown: the preview's, else the first subject ID;
    none on a blind review, whose record views would show the placement."""
    if case.blind:
        return None
    if case.preview is not None and case.preview.master_id:
        return record_href(case.preview.master_id)
    first = case.row.subject.split("·")[0].strip()
    if first and ":" not in first:
        return record_href(first)
    return None


def batch_href(batch_id: str) -> str:
    """A batch's page: "/batch/BAT-…"."""
    return f"/batch/{quote(batch_id, safe='')}"


def sample_notice(case: TaskCase) -> Component | None:
    """A forced-sample review's line at the top of the pane: "Forced sample for batch BAT-…: review 3 of 9.
    Decide it on its own: the rest are linked together only if every sample agrees." and "Open the
    batch"."""
    sample = case.sample
    if sample is None:
        return None
    text = (
        f"Forced sample for batch {sample.batch_id}: review {sample.position} of {sample.size}. Decide it on "
        "its own: the rest are linked together only if every sample agrees. "
    )
    found = notice("info", [text, dmc.Anchor(OPEN_BATCH, href=batch_href(sample.batch_id), inherit=True)])
    found.className = f"{found.className} mdm-sample-notice"
    return found


def split_codes(case: TaskCase) -> tuple[str, ...]:
    """The codes "Which comparison misled?" offers: the pattern's comparisons in rule order, then `all`."""
    if case.sample is None:
        return ()
    return (*(mark.comparison for mark in case.sample.choices), SPLIT_ALL)


def chosen_split(case: TaskCase, split: str | None = None) -> str | None:
    """The comparison chosen on this case, when it is one the pane offers; never a default."""
    return split if split in split_codes(case) else None


def split_words(case: TaskCase, split: str | None) -> str | None:
    """A chosen code in words: "birth date", or "every alike review"."""
    if split == SPLIT_ALL:
        return "every alike review"
    for mark in case.sample.choices if case.sample is not None else ():
        if mark.comparison == split:
            return mark.label[:1].lower() + mark.label[1:] if mark.label[1:2].islower() else mark.label
    return None


def _mark_label(mark: Mark) -> list:
    """ "Birth date ≈ similar": the symbol beside its words, hidden from screen readers."""
    return [
        f"{mark.label} ",
        html.Span(mark.mark, className="mdm-symbol", **{"aria-hidden": "true"}),
        f" {mark.words}",
    ]


def split_choice(case: TaskCase, split: str | None = None) -> Component | None:
    """ "Which comparison misled?" (SPLIT_CHOICE): a closed disclosure under the decisions of an open
    forced-sample review, whose options are the pattern's comparisons in rule order and "Every alike review
    in this batch", with no default; open when a comparison is chosen already (kept across a redraw of the
    same case). None when the case is no sample review, or nothing can be decided on it now."""
    if case.sample is None or case.staged is not None or locked(case):
        return None
    options = [
        dmc.Radio(
            label=_mark_label(mark), value=mark.comparison, size="md", **{"data-comparison": mark.comparison}
        )
        for mark in case.sample.choices
    ]
    options.append(dmc.Radio(label=SPLIT_EVERY, value=SPLIT_ALL, size="md", **{"data-comparison": SPLIT_ALL}))
    chosen = chosen_split(case, split)
    return html.Details(
        [
            html.Summary(SPLIT_SUMMARY, className="mdm-split-summary"),
            dmc.RadioGroup(
                dmc.Group(options, gap="md", mt=4),
                id=ids.SPLIT_CHOICE,
                label=SPLIT_LABEL,
                value=chosen,
                size="sm",
                className="mdm-split-choice",
            ),
            html.P(SPLIT_NOTE, className="mdm-split-note"),
        ],
        open=chosen is not None,
        className="mdm-split",
    )


def _batch_line(case: TaskCase, now: datetime) -> Component:
    """A review a batch holds: in the tray with the batch, or committing with it; "Open the batch". Only the
    batch's maker and its second steward (`staged.mine`) are told that U undoes the whole batch: for anyone
    else the batch is another steward's, and U never reaches it."""
    staged = case.staged
    assert staged is not None and staged.batch_id is not None
    if staged.deadline <= now:
        text = f"Part of batch {staged.batch_id}, which is committing. "
    elif staged.mine:
        text = (
            f"Part of batch {staged.batch_id} in the tray: it commits at {staged.deadline:%H:%M:%S} UTC unless "
            "the batch is undone. U undoes the whole batch. "
        )
    else:
        # the decisions' reason ("…another steward's batch, BAT-…. Pick another task.") says what to do
        text = (
            f"Part of another steward's batch, {staged.batch_id}, in the tray: it commits at "
            f"{staged.deadline:%H:%M:%S} UTC unless its steward undoes it. "
        )
    return notice("warning", [text, dmc.Anchor(OPEN_BATCH, href=batch_href(staged.batch_id), inherit=True)])


def _sample_staged(case: TaskCase, split: str | None) -> str | None:
    """What a forced-sample decision in the tray will do: "Not a match, flagged on birth date: when it
    commits at 12:04:31 UTC, the reviews that share this record's value on it leave batch BAT-…. U undoes
    it, and nothing leaves."; None for a decision that names no comparison."""
    staged, sample = case.staged, case.sample
    if staged is None or sample is None or not staged.mine:
        return None
    at = f"{staged.deadline:%H:%M:%S} UTC"
    named = split_words(case, chosen_split(case, split))
    words = "Not a match" if staged.decision == "not_a_match" else "Linked to another golden record"
    if named == "every alike review":
        return (
            f"{words}, flagged on every alike review: when it commits at {at}, every alike review leaves batch "
            f"{sample.batch_id}. U undoes it, and nothing leaves."
        )
    if named is not None:
        return (
            f"{words}, flagged on {named}: when it commits at {at}, the reviews that share this record's value "
            f"on it leave batch {sample.batch_id}. U undoes it, and nothing leaves."
        )
    if staged.decision == "not_a_match":
        return (
            f"In the tray: {staged.label}. When it commits at {at}, the reviews that share this record's value "
            f"on the comparison named with it leave batch {sample.batch_id}. U undoes it, and nothing leaves."
        )
    return None


def staged_line(case: TaskCase, *, split: str | None = None, now: datetime | None = None) -> Component | None:
    """ "In the tray: Link crm:C000123 to ORG-000123. It commits at 12:04:31 UTC unless you undo it (U)."
    A fixed time: the live countdown is the tray's. A review a batch holds says so ("Part of batch BAT-… in
    the tray: …"); a forced-sample decision that disagrees says what leaves the batch when it commits."""
    staged = case.staged
    if staged is None:
        return None
    if staged.batch_id is not None:
        return _batch_line(case, now if now is not None else utcnow())
    sample = _sample_staged(case, split)
    if sample is not None:
        return notice("warning", sample)
    at = staged.deadline.strftime("%H:%M:%S")
    if staged.mine:
        text = f"In the tray: {staged.label}. It commits at {at} UTC unless you undo it (U)."
    else:
        text = f"Another steward's decision waits in the tray: {staged.label}. It commits at {at} UTC."
    return notice("warning", text)


def quiet_case(case: TaskCase) -> bool:
    """Whether every decision is outlined, none filled: a blind measurement, a dispute, or a review of a
    batch's forced sample, where the pane must not nudge the answer."""
    return case.shape in QUIET_SHAPES or case.sample is not None


def render(
    case: TaskCase,
    *,
    revealed: Revealed | None = None,
    chosen: str | None = None,
    split: str | None = None,
    now: datetime | None = None,
) -> Component:
    """The pane (DECIDE_PANE's children) for `case`; `revealed` renders the compare table in clear once;
    `chosen` is the candidate the steward chose on this task, and `split` the comparison named on a
    forced-sample review (both kept when the same task renders again)."""
    now = now if now is not None else utcnow()
    body: list = [header(case, now)]
    sample = sample_notice(case)
    if sample is not None:
        body.append(sample)
    staged = staged_line(case, split=split, now=now)
    if staged is not None:
        body.append(staged)
    # the breaker's notice on a record it could have linked; never on a blind review, which would learn
    # the first decision's kind from it, nor on a pair, an orphan or a dispute, which it does not touch
    if case.paused is not None and not case.blind and case.shape == "source":
        body.append(breaker.pane_notice(case.paused, open_=case.row.reason == breaker.WAITS_REASON))
    # a notice that is also the reason an action is unavailable is said once, beside the action
    if case.notice and case.notice not in reasons(case, chosen):
        body.append(notice("info", case.notice))
    if case.blind:
        if case.choices:
            body.append(blind_choice(case, chosen))
    elif case.close_call:
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
    # a blind review shows no candidate, preview or impact line, even if one came with the case
    if case.candidates and not case.blind:
        body.append(candidates(case, chosen))
    if (
        not case.blind
        and case.preview is not None
        and case.preview.rows
        and not any(c.preview is not None for c in case.candidates)
    ):
        verb = PREVIEW_VERBS.get(case.shape, "approve")
        body.append(
            html.Div(impact.changes(case.preview, verb=verb, open_=True), className="mdm-shape-preview")
        )
    footer_parts: list = [*impact_lines(case, chosen), actions(case, chosen, quiet=quiet_case(case))]
    naming = split_choice(case, split)
    if naming is not None:
        footer_parts.append(naming)
    footer = html.Div(footer_parts, className="mdm-decide-footer")
    extra = {"data-task-id": case.row.task_id}
    path = _record_path(case)
    if path:
        extra["data-open-record"] = path
    if needs_choice(case, chosen):
        extra["data-needs-choice"] = "yes"
    if quiet_case(case):
        extra["data-quiet"] = "yes"
    if case.sample is not None:
        # a forced-sample review: N, or L on another candidate than the default, names the comparison
        # first (`needsSplit` in inbox.js); a close call has no default, and its link names none
        extra["data-sample"] = "yes"
        extra["data-default"] = case.default_candidate or ""
    if case.blind:
        extra["data-blind"] = "yes"
        if not any(a.decision == "blind_link" for a in case.actions):
            extra["data-link"] = "none"  # nothing to place it in: L does nothing, N answers
    if locked(case):
        extra["data-locked"] = "yes"
    return html.Section(
        [html.Div(body, className="mdm-decide-body"), footer],
        className="mdm-decide",
        **{"aria-label": "Decide", **extra},
    )


def empty_pane(text: str | Sequence[Component | str] = NOTHING_SELECTED) -> Component:
    """The pane before a task is chosen, after it was decided, or on an empty view."""
    return html.Section(
        empty_state(text) if isinstance(text, str) else html.P(list(text), className="mdm-empty"),
        className="mdm-decide mdm-decide-empty",
        **{"aria-label": "Decide"},
    )
