"""The icons bundled with the workbench, drawn as CSS masks over inline SVG: nothing loads from a
network, and each icon takes the colour of the text around it, so it reads in both schemes. Decorative
(hidden from screen readers) unless given a label.

An entity model names its icon and its groups' icons (`icon: building`); `glyph` draws a name the
workbench does not know as the generic entity, so a model never breaks a page. `glyph_css` gives every icon
a class, `mdm-glyph-<name>`, for the grid's cells, which are drawn in the browser.

The paths are drawn for this workbench on a 24-unit grid with a 2-unit stroke.

Owner: SHELL (B.8.6).
"""

from __future__ import annotations

from urllib.parse import quote

from dash import html
from dash.development.base_component import Component

#: the SVG body of each icon (stroked paths on a 24 × 24 grid)
_PATHS: dict[str, str] = {
    "person": '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7"/>',
    "spark": '<path d="M12 2l2.2 7.8L22 12l-7.8 2.2L12 22l-2.2-7.8L2 12l7.8-2.2z"/>',
    "tray": '<path d="M3 13l3-8h12l3 8v6H3z"/><path d="M3 13h5l1 3h6l1-3h5"/>',
    "undo": '<path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 010 12h-3"/>',
    "link": '<path d="M10 14a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.7l-1 1"/>'
    '<path d="M14 10a4 4 0 00-5.7 0l-3 3a4 4 0 005.7 5.7l1-1"/>',
    "not-equal": '<path d="M5 9h14M5 15h14M16 4L8 20"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "alert": '<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18v.5"/>',
    "eye": '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "chevron": '<path d="M9 5l7 7-7 7"/>',
    "chevron-left": '<path d="M15 5l-7 7 7 7"/>',
    "expand": '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13M3 6h.5M3 12h.5M3 18h.5"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 114 2c-1 .7-1.5 1.2-1.5 2.5M12 17v.5"/>',
    "moon": '<path d="M20 14.5A8 8 0 019.5 4 8 8 0 1020 14.5z"/>',
    # the entities, their groups of attributes and the generic glyph a model may fall back on
    "entity": '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5l8 4.5 8-4.5M12 12v9"/>',
    "building": '<path d="M5 21V4h10v17"/><path d="M15 9h4v12"/><path d="M3 21h18"/>'
    '<path d="M8.5 8h3M8.5 12h3M8.5 16h3"/>',
    "id-card": '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="11" r="2"/>'
    '<path d="M6 16c.6-1.3 1.7-2 3-2s2.4.7 3 2M15 10h3M15 14h3"/>',
    "mail": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>',
    "map-pin": '<path d="M12 21s-7-6.2-7-11.5a7 7 0 0114 0C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>',
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    "tag": '<path d="M3 12V3h9l9 9-9 9z"/><circle cx="7.5" cy="7.5" r="1.5"/>',
    "sun": '<circle cx="12" cy="12" r="4"/>'
    '<path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4'
    'M17.7 6.3l1.4-1.4"/>',
}
#: the icons the workbench uses
NAMES = tuple(_PATHS)


def svg(name: str) -> str:
    """The icon `name` as a whole SVG document (black strokes: the mask keeps their shape only)."""
    body = _PATHS[name]
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="black" '
        f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    )


def data_uri(name: str) -> str:
    """The icon `name` as a `data:` URI, for a CSS mask."""
    return "data:image/svg+xml;charset=utf-8," + quote(svg(name), safe="")


def known(name: str | None) -> str:
    """`name` when the workbench draws it, else the generic entity glyph."""
    return name if name in _PATHS else "entity"


def glyph(name: str | None, *, label: str | None = None, size: int = 16) -> Component:
    """The icon a model names (`known`), so an unknown name draws the generic glyph instead of failing."""
    return icon(known(name), label=label, size=size)


def glyph_css() -> str:
    """One rule per icon, `.mdm-glyph-<name>`, masking an `.mdm-icon` span drawn in the browser."""
    return "\n".join(
        f'.mdm-glyph-{name}{{-webkit-mask-image:url("{data_uri(name)}");mask-image:url("{data_uri(name)}")}}'
        for name in NAMES
    )


def icon(name: str, *, label: str | None = None, size: int = 16) -> Component:
    """The icon `name` (one of NAMES); hidden from screen readers unless `label` names it."""
    if name not in _PATHS:
        raise ValueError("not an icon of the workbench")
    mask = f'url("{data_uri(name)}")'
    style = {
        "width": f"{size}px",
        "height": f"{size}px",
        "maskImage": mask,
        "WebkitMaskImage": mask,
    }
    if label:
        return html.Span(
            className=f"mdm-icon mdm-icon-{name}", style=style, role="img", **{"aria-label": label}
        )
    return html.Span(className=f"mdm-icon mdm-icon-{name}", style=style, **{"aria-hidden": "true"})
