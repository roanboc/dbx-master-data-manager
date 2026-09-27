"""Masked prompts: what a provider may see (owner: SERVICES, B.11)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from mdm.models.entity_model import EntityModel
from mdm.models.match import GoldenCandidate

_TODO = "SERVICES: B.11"


@dataclass(frozen=True, slots=True)
class MaskedPrompt:
    purpose: str
    text: str
    fields: Mapping[str, Any]


def build_prompt(
    purpose: str,
    model: EntityModel,
    candidate: GoldenCandidate,
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> MaskedPrompt:
    """The explanation and the masked values of both sides; no personal value."""
    raise NotImplementedError(_TODO)


def assert_masked(prompt: MaskedPrompt, model: EntityModel, originals: Iterable[Mapping[str, Any]]) -> None:
    """ValueError when any personal value of `originals` appears in the prompt's text or fields."""
    raise NotImplementedError(_TODO)
