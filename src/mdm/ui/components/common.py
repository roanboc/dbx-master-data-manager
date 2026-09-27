"""Small shared pieces: page titles, notices, notification payloads, key hints, times and counts.

Every text here is built from what the caller passes (a sentence, a label, a count, a time); nothing is
formatted from a record's value. Times are relative ("4 min ago", "7 h 40 min") so they read the same
whatever the steward's time zone.

Owner: SHELL (B.8.6).
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from dash import html
from dash.development.base_component import Component

from mdm import capacity

#: notice levels and the live-region role each takes: an error interrupts, the rest wait their turn
NOTICE_ROLES = {"info": "status", "success": "status", "warning": "status", "error": "alert"}
#: how long a notification stays, per colour: a confirmation briefly, a refusal long enough to read, an
#: error until closed (WCAG 2.2 success criterion 2.2.1)
NOTIFY_CLOSE_MS: dict[str, int | bool] = {"teal": 4000, "gray": 4000, "yellow": 10000, "red": False}
#: the title each colour gets when the caller names none
NOTIFY_TITLES = {"teal": "Done", "gray": "Note", "yellow": "Not done", "red": "Something went wrong"}


def page_title(text: str, *, hidden: bool = False) -> Component:
    """The page's one `h1`; `hidden` keeps it for screen readers only (the inbox's "Inbox")."""
    return html.H1(text, className="mdm-sr-only" if hidden else "mdm-page-title")


def notice(level: str, text: str | Component | list) -> Component:
    """A notice in the page (`level` info | success | warning | error), text colour 4.5:1 in both schemes."""
    if level not in NOTICE_ROLES:
        raise ValueError("not a notice level")
    return html.Div(text, className=f"mdm-notice mdm-notice-{level}", role=NOTICE_ROLES[level])


def notify(
    message: str, color: str = "teal", *, title: str | None = None, key: str | None = None
) -> list[dict]:
    """The payload of `NotificationContainer.sendNotifications`: one notification shown with `message`
    (teal a confirmation, yellow a refusal, red a failure, gray a neutral note). With `key` (a code) its
    ID is stable, so the same refusal pressed again is not stacked on the first while that one shows."""
    return [
        {
            "id": f"n-{key}" if key else f"n-{secrets.token_hex(6)}",
            "action": "show",
            "title": title if title is not None else NOTIFY_TITLES.get(color, ""),
            "message": message,
            "color": color,
            "autoClose": NOTIFY_CLOSE_MS.get(color, 6000),
            "withBorder": True,
            # a failure interrupts; anything else waits its turn (Mantine's own role is "alert")
            "role": "alert" if color == "red" else "status",
            "closeButtonProps": {"aria-label": "Close the message"},
        }
    ]


def empty_state(text: str) -> Component:
    """What a list says when it has nothing to show."""
    return html.P(text, className="mdm-empty")


def kbd(key: str) -> Component:
    """A key hint ("L") beside the control it presses; the control carries `aria-keyshortcuts`."""
    return html.Kbd(key, className="mdm-kbd")


def _span(seconds: float) -> str:
    """A span of time, coarse: "40 min", "7 h", "3 d"."""
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{max(minutes, 1)} min"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} h"
    return f"{hours // 24} d"


def relative_time(moment: datetime, now: datetime) -> str:
    """ "just now", "4 min ago", "2 h ago", "3 d ago"; "in 4 min" ahead; the date beyond a week."""
    seconds = (now - moment).total_seconds()
    if abs(seconds) < 45:
        return "just now"
    if abs(seconds) >= 7 * 86400:
        return f"{moment.day} {moment.strftime('%b %Y')}"
    return f"{_span(seconds)} ago" if seconds > 0 else f"in {_span(-seconds)}"


def duration(delta: timedelta) -> str:
    """ "7 h 40 min" for time left; "breached 2 h ago" for a negative delta."""
    seconds = delta.total_seconds()
    if seconds < 0:
        return f"breached {_span(-seconds)} ago"
    minutes = int(seconds // 60)
    if minutes < 1:
        return "under 1 min"
    days, rest = divmod(minutes, 24 * 60)
    hours, minutes = divmod(rest, 60)
    if days:
        return f"{days} d {hours} h" if hours else f"{days} d"
    if hours:
        return f"{hours} h {minutes} min" if minutes else f"{hours} h"
    return f"{minutes} min"


def count_text(count: int) -> str:
    """A capped count: exact below `capacity.COUNT_CAP`, "999+" at the cap."""
    if count >= capacity.COUNT_CAP:
        return f"{capacity.COUNT_CAP - 1}+"
    return str(max(int(count), 0))


def countdown(seconds: float) -> str:
    """Time left in an undo window: "0:48", "1:05"; "0:00" once it has passed."""
    whole = max(int(seconds + 0.999), 0)
    return f"{whole // 60}:{whole % 60:02d}"
