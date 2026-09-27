"""The Mantine theme. System fonts only: nothing loads from a network.

The primary colour is indigo, as in the sibling applications, at shade 8 in both schemes, so white text
on a filled button reads at 5.7:1 and a link on the light background at 5.7:1 (WCAG 2.2 AA asks 4.5:1).
The workbench's own tokens (bands, agreement, provenance, the staged outline, focus) are in
`assets/styles.css`, for both schemes.

Owner: SHELL (B.8.5).
"""

from __future__ import annotations

from typing import Any

THEME: dict[str, Any] = {
    "primaryColor": "indigo",
    "primaryShade": {"light": 8, "dark": 8},
    "fontFamily": "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
    "fontFamilyMonospace": "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
    "defaultRadius": "md",
    "headings": {"fontWeight": "650"},
    # the focus ring shows on keyboard focus (:focus-visible); styles.css draws it at 3:1 in both schemes
    "focusRing": "auto",
}
#: the colour scheme a first visit starts in when the browser states no preference
DEFAULT_SCHEME = "light"
#: the two schemes the header's switch offers
SCHEMES = ("light", "dark")
