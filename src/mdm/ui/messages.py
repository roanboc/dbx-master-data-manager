"""One plain sentence for each error a steward can meet, built from its code and safe fields.

A sentence says what happened and what to do next, from the steward's side of the screen; it echoes an
ID only when it has the shape of a master ID or a source key, and never a value. The table of codes and
sentences is contract C.3 of the plan; any other `MdmError` reads "That did not work (<code>)." A tray
entry that settled `failed` carries an outcome code, not an error: `outcome_sentence` reads it the same way.

Owner: SHELL (B.8.1, C.3).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from mdm.models.authority import ROLE_LABELS
from mdm.models.errors import MdmError

#: a master ID ("ORG-000123") or a task or tray ID ("TSK-…", "TR-…")
ID_RE = re.compile(r"^[A-Z]{2,6}-[A-Za-z0-9]{3,24}\Z")
#: a source key ("crm:C000123")
SOURCE_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,30}:[A-Za-z0-9._\-]{1,64}\Z")
#: what an action is, in words, after "cannot": one entry per action of `models.authority.ACTIONS`
ACTION_WORDS: Mapping[str, str] = {
    "read": "read records",
    "reveal": "show personal values",
    "arrival": "commit arrivals",
    "run_arrival": "run arrival",
    "link": "link records",
    "detach": "detach records",
    "reinstate": "reinstate records",
    "merge": "merge records",
    "unmerge": "undo merges",
    "retire": "retire records",
    "load_model": "load entity models",
    "publish_model": "publish entity models",
    "estimate": "estimate match weights",
    "publish_rules": "publish rules",
    "profile": "profile sources",
    "match_test": "run the match test",
    "load_code_lists": "load code lists",
    "redact": "redact values",
    "view_tasks": "see the inbox",
    "work_tasks": "work on tasks",
    "not_a_match": "decline a match",
    "keep_apart": "keep records apart",
    "keep_orphan": "keep a record without sources",
    "approve_update": "approve held updates",
    "reject_update": "reject held updates",
    "flush_tray": "commit the tray",
}
#: `record_changed` when a decision is staged rather than flushed: the case on screen was out of date
STAGE_RECORD_CHANGED = (
    "The source record changed after you opened this task. The task has been refreshed; decide it again."
)
#: the sentence for an unexpected failure, naming only the exception's type
UNEXPECTED = (
    "Something went wrong ({kind}). The details are not shown, because they could hold a personal value."
)

_FIXED: Mapping[str, str] = {
    "unknown_task": "That task no longer exists. The list has been refreshed.",
    "task_closed": "That task has already been decided. Pick the next one.",
    "close_call": "Two candidates are close. Choose one with its number key, then press L.",
    "cannot_link": (
        "A cannot-link rule keeps this record apart from that golden record. Choose another candidate, "
        "or decide Not a match."
    ),
    "task_changed": "This task changed after you opened it. It has been refreshed; decide it again.",
    "case_loading": "The next task is still loading. Press the key again once it shows.",
    "no_forwarded_user": "The platform did not say who you are. Reload the page, or sign in again.",
    "candidate_not_offered": "That record is not one of this task's candidates.",
    "decision_not_offered": "This task does not offer that decision.",
    "not_held": "This record's update is no longer held. The task has been refreshed.",
    "reason_required": "Choose a reason to show personal values.",
    "not_yours": "You can undo only your own decisions.",
    "record_changed": (
        "The source record changed while your decision waited. The task is back in your queue; decide it again."
    ),
    "target_changed": (
        "The golden record you chose changed, or was merged or retired, while your decision waited. "
        "Decide the task again."
    ),
    "not_active": (
        "The golden record you chose was merged or retired while your decision waited. Decide the task again."
    ),
    "not_settled": "Your decision could not be committed. The task is back in your queue.",
    "internal": "Your decision could not be committed. The task is back in your queue.",
    "tray_entry_settled": "That decision has already been settled.",
    "unknown_entry": "That decision is no longer in the tray.",
    "bad_cursor": "That page of the inbox is no longer there. The list starts again from the top.",
    "unknown_view": "That view of the inbox does not exist. Choose one on the left.",
    "unknown_kind": "That kind of task does not exist. Choose one on the left.",
    "persona_refused": "Personas work only on a local store.",
    "bad_snooze": "Choose one of the offered times.",
    "bad_escalation": "Choose one of the offered reasons.",
    "unknown_role": "That role does not exist.",
    "conflict": "Someone else changed this record just now. Refresh and try again.",
    "stale_row": "Someone else changed this record just now. Refresh and try again.",
    "stale_link": "Someone else changed this record just now. Refresh and try again.",
    "no_published_model": "That entity has no published model yet.",
    "workbench_needs_loopback": (
        "The workbench listens on 127.0.0.1 only, because whoever can reach it acts as you "
        "(or as any persona on a local store)."
    ),
    "dev_needs_local_store": "Development mode works only on a local store.",
}
_UNKNOWN_RECORD = ("unknown_master_id", "unknown_source_record", "unknown_ref")


def echo_id(value: Any) -> str | None:
    """`value` when it has the shape of a master ID, task ID or source key; otherwise None."""
    if isinstance(value, (list, tuple)):
        value = value[0] if len(value) == 1 else None
    if isinstance(value, str) and (ID_RE.match(value) or SOURCE_KEY_RE.match(value)):
        return value
    return None


def role_words(role: Any) -> str:
    """ "data owner" for `data_owner`; "this role" for anything unknown."""
    label = ROLE_LABELS.get(role) if isinstance(role, str) else None
    return label.lower() if label else "this role"


def action_words(action: Any) -> str:
    """What `action` is, in words ("decide tasks"); "do that" for anything unknown."""
    return ACTION_WORDS.get(action, "do that") if isinstance(action, str) else "do that"


def _minutes_until(until: Any, now: datetime) -> int | None:
    if not isinstance(until, str):
        return None
    try:
        moment = datetime.fromisoformat(until)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max(int((moment - now).total_seconds() // 60) + 1, 1)


def _claimed(fields: Mapping[str, Any], now: datetime) -> str:
    minutes = _minutes_until(fields.get("until"), now)
    if minutes is None:
        return "Another steward is working on this task. Pick another task."
    return f"Another steward is working on this task for the next {minutes} min. Pick another task."


def _already_staged(fields: Mapping[str, Any], now: datetime) -> str:
    if fields.get("mine"):
        return "Your decision on this task is already in the tray. Undo it there to change it."
    return (
        "Another steward's decision on this record is waiting in the tray. "
        "It commits within a minute; pick another task."
    )


def _already_settled(fields: Mapping[str, Any], now: datetime) -> str:
    status = fields.get("status")
    if status == "committed":
        version = fields.get("version")
        commit = f"commit {version}" if isinstance(version, int) else "a commit"
        return f"Too late to undo: it committed as {commit}. Reversing it is not on screen yet."
    if status == "undone":
        return "It was already undone."
    return "It was not committed, so there is nothing to undo."


def _forbidden(fields: Mapping[str, Any], now: datetime) -> str:
    return f"Your role, {role_words(fields.get('role'))}, cannot {action_words(fields.get('action'))}."


def _unknown_record(fields: Mapping[str, Any], now: datetime) -> str:
    for name in ("master_id", "source", "ref", "keys"):
        shown = echo_id(fields.get(name))
        if shown:
            return f"No record has the ID {shown}."
    return "No record has that ID."


_BUILT: Mapping[str, Callable[[Mapping[str, Any], datetime], str]] = {
    "claimed_by_another": _claimed,
    "already_staged": _already_staged,
    "already_settled": _already_settled,
    "forbidden": _forbidden,
    **{code: _unknown_record for code in _UNKNOWN_RECORD},
}
#: every code with a sentence of its own; any other reads "That did not work (<code>)."
KNOWN_CODES = frozenset(_FIXED) | frozenset(_BUILT)


#: a refusal's notification title: what went wrong, in a few words; any other code reads "Not done"
_TITLES: Mapping[str, str] = {
    "close_call": "Choose a candidate first",
    "cannot_link": "A rule keeps them apart",
    "record_changed": "The record changed",
    "task_changed": "The task changed",
    "case_loading": "Still loading",
    "task_closed": "Already decided",
    "unknown_task": "Task gone",
    "claimed_by_another": "Another steward has it",
    "already_staged": "Already in the tray",
    "already_settled": "Too late to undo",
    "not_held": "No longer held",
    "target_changed": "The golden record changed",
    "candidate_not_offered": "Not a candidate",
    "decision_not_offered": "Not offered here",
    "forbidden": "Not for your role",
    "reason_required": "Choose a reason",
    "not_yours": "Not your decision",
    "bad_snooze": "Choose a time",
    "bad_escalation": "Choose a reason",
    "persona_refused": "Personas are local only",
    "no_forwarded_user": "Who are you?",
}


def title_for(code: str) -> str:
    """The title of a refusal's notification: "Choose a candidate first" for `close_call`."""
    return _TITLES.get(code, "Not done")


def sentence_for(code: str, fields: Mapping[str, Any] | None = None, *, now: datetime | None = None) -> str:
    """The sentence for `code` with its safe `fields` (`now` for a claim's remaining minutes)."""
    fields = fields or {}
    if code in _FIXED:
        return _FIXED[code]
    built = _BUILT.get(code)
    if built is not None:
        return built(fields, now or datetime.now(UTC))
    shown = code if isinstance(code, str) and re.fullmatch(r"[a-z0-9_]{1,60}", code) else "unknown"
    return f"That did not work ({shown})."


def sentence(error: MdmError, *, now: datetime | None = None) -> str:
    """The sentence for `error`: by its code, filled from its safe fields (`already_staged` reads
    differently for `mine` true and false; `already_settled` for committed and undone)."""
    return sentence_for(error.code, error.fields, now=now)


def outcome_sentence(outcome: str | None) -> str:
    """Why a staged decision settled `failed`, from its outcome code (`record_changed`, `internal`, …)."""
    return sentence_for(outcome or "internal")


def unexpected(error: BaseException) -> str:
    """The sentence for an error that is not an `MdmError`: its type only, never its message."""
    return UNEXPECTED.format(kind=type(error).__name__)
