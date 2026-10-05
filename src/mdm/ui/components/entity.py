"""How an entity and its groups of attributes look: the badge (the model's icon on a tint of its colour)
and the attributes sorted into the model's groups, with "Other" last for any it does not list.

Both read what the entity model declares (`icon`, `colour`, `groups`) through the hub's badges, so a new
entity brings its own look and nothing here names one.

Owner: SHELL (B.8.6).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

from dash import html
from dash.development.base_component import Component

from mdm.models.entity_model import COLOURS, AttributeGroup
from mdm.models.workbench import EntityLook
from mdm.ui.components.icons import glyph

#: the group an attribute falls in when the model's groups do not list it
OTHER = AttributeGroup(name="other", label="Other", icon="tag", attributes=())

T = TypeVar("T")


def hue(colour: str) -> str:
    """The colour's class suffix, or gray for one the workbench does not draw."""
    return colour if colour in COLOURS else "gray"


def badge(look: EntityLook, *, size: int = 28, label: str | None = None) -> Component:
    """The entity's icon on a rounded tint of its colour; decorative unless `label` names it."""
    extra = {"role": "img", "aria-label": label} if label else {"aria-hidden": "true"}
    return html.Span(
        glyph(look.icon, size=round(size * 0.6)),
        className=f"mdm-entity-badge mdm-hue-{hue(look.colour)}",
        style={"width": f"{size}px", "height": f"{size}px"},
        **extra,
    )


def mark(look: EntityLook) -> Component:
    """The entity's icon alone, in its colour, at text size: for a line that names the entity."""
    return html.Span(glyph(look.icon, size=14), className=f"mdm-entity-mark mdm-hue-{hue(look.colour)}")


def grouped(
    groups: Sequence[AttributeGroup], items: Sequence[T], name_of
) -> list[tuple[AttributeGroup, list[T]]]:
    """`items` in the model's groups, in the groups' order and then the items' own; the ones no group lists
    under OTHER, last. Empty groups are left out; with no groups declared, one OTHER group holds all."""
    if not groups:
        return [(OTHER, list(items))] if items else []
    by_group: dict[str, list[T]] = {group.name: [] for group in groups}
    rest: list[T] = []
    for item in items:
        name = name_of(item)
        group = next((g for g in groups if name in g.attributes), None)
        (by_group[group.name] if group is not None else rest).append(item)
    out = [(group, by_group[group.name]) for group in groups if by_group[group.name]]
    if rest:
        out.append((OTHER, rest))
    return out


def icon_of(groups: Sequence[AttributeGroup], attribute: str) -> str | None:
    """The icon of the group that lists `attribute`, or None."""
    return next((g.icon for g in groups if attribute in g.attributes), None)
