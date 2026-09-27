"""The case narrative: a labelled, plain-language reading of one match explanation (owner: SERVICES, B.11).

Stub example: "Scores 71 (review). Strongest: family name exact (+8.7).
Weakest: phone differs (−1.4). A matching person reference would give 99
(automatic)." Every call writes access_log(action="ai_call",
detail={provider, purpose, prompt_sha256}).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mdm.agent.provider import Provider, Suggestion
from mdm.backend.store import SqlStore
from mdm.models.authority import Actor
from mdm.models.entity_model import EntityModel
from mdm.models.match import GoldenCandidate

_TODO = "SERVICES: B.11"


def case_narrative(
    store: SqlStore,
    provider: Provider,
    model: EntityModel,
    candidate: GoldenCandidate,
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    actor: Actor,
) -> Suggestion:
    raise NotImplementedError(_TODO)
