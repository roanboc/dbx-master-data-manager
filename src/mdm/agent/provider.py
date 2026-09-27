"""Providers of suggestions: the deterministic stub (the local default) and a serving endpoint (owner: SERVICES, B.11).

The Databricks SDK is imported lazily, inside `ServingEndpointProvider`, never
at module import; any endpoint error falls back to the stub.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from mdm.agent.prompt import MaskedPrompt
from mdm.config import Settings
from mdm.models.entity_model import EntityModel

_TODO = "SERVICES: B.11"


@dataclass(frozen=True, slots=True)
class Suggestion:
    text: str
    provider: str
    purpose: str
    labelled: bool = True  # shown as a machine suggestion, never as a decision


class Provider(Protocol):
    name: str

    def suggest(self, purpose: str, prompt: MaskedPrompt) -> Suggestion: ...


class StubProvider:
    """Deterministic templates from the explanation; the local default."""

    name = "stub"

    def suggest(self, purpose: str, prompt: MaskedPrompt) -> Suggestion:
        raise NotImplementedError(_TODO)


class ServingEndpointProvider:
    """w.serving_endpoints.query(name=…, messages=[…]); any error -> the stub's suggestion."""

    name = "endpoint"

    def __init__(self, endpoint: str, workspace: Callable[[], Any] | None = None) -> None:
        raise NotImplementedError(_TODO)

    def suggest(self, purpose: str, prompt: MaskedPrompt) -> Suggestion:
        raise NotImplementedError(_TODO)


def choose_provider(settings: Settings, model: EntityModel) -> Provider:
    """The stub unless model.ai_enabled, settings.agent_provider in ("auto", "endpoint"),
    settings.agent_endpoint is set, and the SDK imports."""
    raise NotImplementedError(_TODO)
