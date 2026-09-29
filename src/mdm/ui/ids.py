"""Component IDs, each once, so callbacks and layouts cannot drift apart.

UPPER_SNAKE constants with kebab-case values. A pattern-matching ID is built only here, as a constant of
codes or by the helpers below, from IDs, codes and attribute names — never from a value a person could
read, so no ID carries a personal value into the page or a callback's request; each helper refuses
(ValueError) any text that `mdm.models.safety.safe` refuses. `assets/keys.js` and `assets/inbox.js` use the same literals: change
one, change both.
"""

from __future__ import annotations

from mdm.models.safety import safe

# shell
SHELL = "shell"  # MantineProvider (forceColorScheme)
APP_SHELL = "app-shell"  # AppShell, whose navbar the burger opens on a narrow screen
URL = "url"  # dcc.Location (refresh="callback-nav")
ROUTE = "route"  # dcc.Store(memory): {"path", "persona"} of the page on screen
PAGE = "page"  # main content container
MAIN = "main"  # skip-link target (AppShellMain id)
SKIP_LINK = "skip-link"
ADDRESS_NOTE = "address-note"  # unknown path notice
NOTIFY = "notify"  # dmc.NotificationContainer
HEADER = "header"
NAVBAR = "navbar"
BURGER = "burger"
NAV_INBOX = "nav-inbox"
NAV_VIEWS = "nav-views"  # the view rail under Inbox: plain links, shell-owned
NAV_ROLE = "nav-role"  # the role in words, in the navigation below the small breakpoint
PERSONA = "persona"  # dcc.Store(storage_type="session"): a role name
PERSONA_SELECT = "persona-select"
ENTITY = "entity"  # dcc.Store(storage_type="session"): an entity name or ""
ENTITY_SELECT = "entity-select"
ROLE_BADGE = "role-badge"
ENGINE_BADGE = "engine-badge"
BREACHES = "breaches"  # the header's breach count, a link to /?view=breaching
SCHEME_TOGGLE = "scheme-toggle"  # SegmentedControl, persistence local
SCHEME_TOGGLE_NAV = "scheme-toggle-nav"  # its copy in the navigation, below the small breakpoint
HELP_OPEN = "help-open"  # the "?" button
HELP_OPEN_NAV = "help-open-nav"  # its copy in the navigation, below the small breakpoint
HELP_MODAL = "help-modal"
KEYS_ENABLED = "keys-enabled"  # dcc.Store(storage_type="local"): true/false
KEYS_SWITCH = "keys-switch"
KEY_EVENT = "key-event"  # dcc.Store(memory), written by assets/keys.js: decision keys only
COUNTS_POLL = "counts-poll"  # dcc.Interval, COUNTS_REFRESH_SECONDS
TRAY_WRAP = "tray-wrap"  # holds the tray's popover; hidden for a role that decides no task
TRAY_BUTTON = "tray-button"
TRAY_POPOVER = "tray-popover"
TRAY_LIST = "tray-list"
TRAY_STATE = "tray-state"  # dcc.Store(memory): [{entry_id, task_id, label, deadline_ms, status, outcome}]
TRAY_VERSION = "tray-version"  # dcc.Store(memory): an int bumped after a stage or an undo
SETTLED = "settled"  # dcc.Store(memory): [{entry_id, task_id, status}] newly settled since the last refresh
TRAY_POLL = (
    "tray-poll"  # dcc.Interval, TRAY_POLL_SECONDS, disabled while nothing of this tab's steward is staged
)
#: how many of the steward's tray entries still move (staged, or a batch committing), from the counts every
#: COUNTS_REFRESH_SECONDS: a tray that is not polling wakes when it differs from what the tab shows (story
#: 3.3: a batch the second steward confirmed appears in its maker's tray)
TRAY_LIVE = "tray-live"  # dcc.Store(memory): an int
CLOCK_TICK = "clock-tick"  # dcc.Interval, 1 s (clientside countdown only)
TRAY_UNDO = "tray-undo"  # pattern {"type": TRAY_UNDO, "entry": entry_id}
TRAY_COUNTDOWN = "tray-countdown"  # pattern {"type": TRAY_COUNTDOWN, "entry": entry_id}

# reveal, page-owned: pattern {"type": …, "page": "decide" | "record" | "source"}
REVEAL_OPEN = "reveal-open"
REVEAL_MODAL = "reveal-modal"
REVEAL_REASON = "reveal-reason"  # RadioGroup of REVEAL_REASONS, no default
REVEAL_CONFIRM = "reveal-confirm"
REVEAL_CANCEL = "reveal-cancel"
#: the pages that own a reveal modal
REVEAL_PAGES = ("decide", "record", "source")

# inbox
INBOX = "inbox"  # page root, carries data-mdm-keys="on"
HEALTH_STRIP = "health-strip"
INBOX_QUERY = "inbox-query"  # dcc.Store(memory): {"view", "kind", "entity"}
INBOX_CURSOR = "inbox-cursor"  # dcc.Store(memory): {"stack": [cursor, …], "moved": "next" | "prev" | None}
PAGE_AFTER = "page-after"  # dcc.Store(memory): the next page's cursor
INBOX_GRID = "inbox-grid"  # dag.AgGrid
PAGE_PREV = "page-prev"
PAGE_NEXT = "page-next"
PAGE_LABEL = "page-label"
SELECTED_TASK = "selected-task"  # dcc.Store(memory): task ID — the one source of truth
SELECTED_CANDIDATE = "selected-candidate"  # dcc.Store(memory): master ID or None (none chosen)
NEXT_HINT = "next-hint"  # dcc.Store(memory): the task ID after the selected one, for prefetch
CASE_VERSION = "case-version"  # dcc.Store(memory): bumped only when the selected task's own state changes
CASE_STAMP = "case-stamp"  # dcc.Store(memory): task_id, event_id, task_version, shape, default (codes)
ACT_RESULT = "act-result"  # dcc.Store(memory): {"task_id", "advance", "n"} from the act callback
DECIDE_PANE = "decide-pane"
DECIDE_COMPARE = "decide-compare"  # re-rendered in clear after a reveal; never a store
WHY_SECTION = "why-section"  # the waterfalls and flip sentences ("." toggles)
CANDIDATE_CHOICE = "candidate-choice"  # a radio group: value = master ID; options carry data-candidate-index
CANDIDATE_PANEL = (
    "candidate-panel"  # pattern {"type": CANDIDATE_PANEL, "master_id": id}: waterfall, flip, preview
)
CANDIDATE_IMPACT = (
    "candidate-impact"  # pattern {"type": CANDIDATE_IMPACT, "master_id": id}: the footer's impact line
)
PANE_FULL = "pane-full"  # the pane's full-width button (F)
PANE_FULL_TIP = "pane-full-tip"  # its tooltip, which names what it does next
ACTION = "action"  # pattern {"type": ACTION, "decision": d}
ACTION_REASONS = "action-reasons"  # visible text: why unavailable actions are unavailable
SNOOZE_MENU = "snooze-menu"
SNOOZE_OPTION = "snooze-option"  # pattern {"type": SNOOZE_OPTION, "hours": h}
ESCALATE_MENU = "escalate-menu"
ESCALATE_OPTION = "escalate-option"  # pattern {"type": ESCALATE_OPTION, "reason": code}
PREV_TASK = "prev-task"
NEXT_TASK = "next-task"
#: the inbox's own copies of what the shell announces, typed by the shell's IDs: a callback whose output
#: is on the inbox never takes a shell input directly (off the inbox its output would be missing), so a
#: clientside bridge copies the shell's value into these through a wildcard output. Codes only.
INBOX_ADDRESS = {"type": INBOX_QUERY, "part": "address"}  # {"search", "entity", "path"} of the address
INBOX_SETTLED = {"type": SETTLED, "page": "inbox"}  # the shell's SETTLED, while the inbox is on screen
HEALTH_POLL = {"type": HEALTH_STRIP, "part": "poll"}  # the health strip's own counts poll
INBOX_ACT_REQUEST = {
    "type": KEY_EVENT,
    "page": "inbox",
}  # {"action", "n", "task"}: a key, button or menu item
INBOX_TRAY = {"type": TRAY_STATE, "page": "inbox"}  # the shell's TRAY_STATE, while the inbox is on screen
# the inbox filtered to a signature group or a batch's forced sample (story 3.3)
INBOX_FILTER = "inbox-filter"  # the line above the list that names the group or the batch, and links back
#: the decide pane's "Which comparison misled?" on a forced-sample review: a RadioGroup whose value is a
#: comparison's name or `all`, beside CANDIDATE_CHOICE
SPLIT_CHOICE = "split-choice"
#: the comparison named on the forced-sample review on screen, copied out of the pane in the browser (as
#: SELECTED_CANDIDATE is), so the act callback never names a State the pane may lack: {"task", "on"}, codes
SELECTED_SPLIT = "selected-split"

# alike reviews (story 3.3): the signature groups
GROUPS = "groups"  # page root
GROUPS_LIST = "groups-list"  # the page's regions, redrawn after a draw or a settlement
GROUPS_VERSION = "groups-version"  # dcc.Store(memory): an int bumped after a draw
GROUPS_DRAW_WHY = "groups-draw-why"  # why every draw is disabled for this role (aria-describedby)
GROUP_DRAW = "group-draw"  # pattern {"type": GROUP_DRAW, "group": "SIG-…", "entity": entity}
#: the page's own copies of the shell's address and settlements (the inbox's bridges fill them)
GROUPS_ADDRESS = {"type": INBOX_QUERY, "part": "groups"}  # {"search", "entity", "path"}
GROUPS_SETTLED = {"type": SETTLED, "page": "groups"}

# a batch of alike reviews (story 3.3)
BATCH = "batch"  # page root
BATCH_REF = "batch-ref"  # dcc.Store(memory): {"batch_id"}
BATCH_VIEW = "batch-view"  # the page's regions, redrawn only when the batch's stamp changes
BATCH_STAMP = "batch-stamp"  # dcc.Store(memory): the batch's status, sample outcomes, splits, chunks, stop
BATCH_POLL = (
    "batch-poll"  # dcc.Interval: every 2 s while staged or committing, 30 s while open, off once done
)
BATCH_VERSION = "batch-version"  # dcc.Store(memory): an int bumped after an action
BATCH_ROWS = "batch-rows"  # one page of every row's change
BATCH_ROWS_LABEL = "batch-rows-label"  # "Rows 51–100 of 566"
BATCH_ROWS_PREV = "batch-rows-prev"
BATCH_ROWS_NEXT = "batch-rows-next"
BATCH_ROWS_CURSOR = "batch-rows-cursor"  # dcc.Store(memory): {"stack": [position, …], "after": position}
BATCH_ACTION_REASONS = "batch-action-reasons"  # visible text: why unavailable actions are unavailable
BATCH_ACTION = "batch-action"  # pattern {"type": BATCH_ACTION, "action": code of PAGE_ACTIONS}
BATCH_SETTLED = {"type": SETTLED, "page": "batch"}  # the shell's SETTLED, while the batch is on screen

# record
RECORD = "record"
RECORD_REF = "record-ref"  # dcc.Store(memory): {"entity", "master_id"}
RECORD_HEADER = "record-header"
RECORD_TABS = "record-tabs"
GOLDEN_TABLE = "golden-table"
PROV_CHIP = "prov-chip"  # pattern {"type": PROV_CHIP, "attr": name}: opens the value's Why row
WHY_ROW = "why-row"  # pattern {"type": WHY_ROW, "attr": name}: the row under a value, shown when open
WHY_PANEL = "why-panel"  # pattern {"type": WHY_PANEL, "attr": name}: that row's explanation
#: the record the Why rows explain, as a pattern so the Why's callback asks for it only while the record
#: is on screen (a plain State would name a store the next page no longer has)
WHY_REF = {"type": RECORD_REF, "page": "record"}  # dcc.Store(memory): {"entity", "master_id", "personal"}
MEMBERS_TABLE = "members-table"
TIMELINE_LIST = "timeline-list"
TIMELINE_MORE = "timeline-more"
TIMELINE_CURSOR = "timeline-cursor"
RELATIONSHIPS_TABLE = "relationships-table"

# source record
SOURCE = "source"
SOURCE_REF = "source-ref"  # dcc.Store(memory): {"entity", "source"}
SOURCE_VALUES = "source-values"
SOURCE_VERSIONS = "source-versions"

#: the parts of the page-owned reveal modal
REVEAL_PARTS = (REVEAL_OPEN, REVEAL_MODAL, REVEAL_REASON, REVEAL_CONFIRM, REVEAL_CANCEL)


def reveal(part: str, page: str) -> dict:
    """A part of a page's reveal modal: `part` one of REVEAL_PARTS, `page` one of REVEAL_PAGES."""
    if part not in REVEAL_PARTS or page not in REVEAL_PAGES:
        raise ValueError("not a reveal part or page")
    return {"type": part, "page": page}


def action(decision: str) -> dict:
    """An action button of the decide pane: `decision` a code (DECISIONS, TASK_ACTIONS or "undo")."""
    return {"type": ACTION, "decision": safe(decision)}


def candidate_impact(master_id: str) -> dict:
    """A candidate's impact line in the pane's footer: `master_id` an ID."""
    return {"type": CANDIDATE_IMPACT, "master_id": safe(master_id)}


def why_row(attribute: str) -> dict:
    """The row under a golden value that holds its Why: `attribute` an attribute name."""
    return {"type": WHY_ROW, "attr": safe(attribute)}


def why_panel(attribute: str) -> dict:
    """The explanation inside that row: `attribute` an attribute name."""
    return {"type": WHY_PANEL, "attr": safe(attribute)}


def candidate_panel(master_id: str) -> dict:
    """A candidate's panel (waterfall, flip sentences, preview): `master_id` an ID."""
    return {"type": CANDIDATE_PANEL, "master_id": safe(master_id)}


def snooze_option(hours: int) -> dict:
    """A snooze menu item: `hours` one of SNOOZE_HOURS."""
    return {"type": SNOOZE_OPTION, "hours": int(hours)}


def escalate_option(reason: str) -> dict:
    """An escalate menu item: `reason` a code of ESCALATION_REASONS."""
    return {"type": ESCALATE_OPTION, "reason": safe(reason)}


def tray_undo(entry_id: str) -> dict:
    """The Undo button of one tray entry: `entry_id` an ID ("TR-…")."""
    return {"type": TRAY_UNDO, "entry": safe(entry_id)}


def tray_countdown(entry_id: str) -> dict:
    """The countdown of one tray entry: `entry_id` an ID ("TR-…")."""
    return {"type": TRAY_COUNTDOWN, "entry": safe(entry_id)}


def prov_chip(attribute: str) -> dict:
    """The provenance chip of one golden value: `attribute` an attribute name."""
    return {"type": PROV_CHIP, "attr": safe(attribute)}


def group_draw(group_key: str, entity: str) -> dict:
    """The "Draw a forced sample" button of one signature group: `group_key` a key ("SIG-…"), `entity` a
    code. A signature itself never passes: it is not safe text."""
    return {"type": GROUP_DRAW, "group": safe(group_key), "entity": safe(entity)}


def batch_action(code: str) -> dict:
    """An action button of the batch page: `code` one of `models.batch.PAGE_ACTIONS`."""
    return {"type": BATCH_ACTION, "action": safe(code)}
