"""The case narrative: a labelled, plain-language reading of one match explanation (owner: SERVICES, B.11).

Stub example: "Scores 71 (review). Strongest: family name exact (+8.7).
Weakest: phone differs (−1.4). A matching person reference would give 99
(automatic)." Every call writes access_log(action="ai_call",
detail={provider, purpose, prompt_sha256}) before the prompt goes anywhere,
naming the provider it goes to, so no prompt leaves the process unrecorded.

The prompt is built masked and checked before the provider sees it; a prompt
the check refuses goes to the stub instead, so nothing leaves the process.
The suggestion is returned, never committed (principle P6).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from typing import Any

from mdm.agent.prompt import assert_masked, build_prompt
from mdm.agent.provider import CASE_NARRATIVE, Provider, StubProvider, Suggestion
from mdm.backend.store import SqlStore
from mdm.models.authority import ACTIONS, Actor
from mdm.models.entity_model import EntityModel
from mdm.models.errors import Forbidden
from mdm.models.match import GoldenCandidate
from mdm.models.safety import SAFE_TEXT_RE, safe_detail, safe_message

logger = logging.getLogger(__name__)

#: the access-log action of every assistant call
AI_CALL = "ai_call"


def case_narrative(
    store: SqlStore,
    provider: Provider,
    model: EntityModel,
    candidate: GoldenCandidate,
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    actor: Actor,
) -> Suggestion:
    """The narrative of one candidate: `left` is the record tested, `right` the golden record's values.

    Needs the "read" action. Writes one access_log row, before the call, naming the provider the prompt is
    sent to, the purpose and the SHA-256 of the prompt's text, never the prompt; when that row cannot be
    written, nothing is sent. The suggestion names the provider that answered (the stub when an
    endpoint failed).
    """
    if actor.role not in ACTIONS["read"]:
        role = actor.role if SAFE_TEXT_RE.match(actor.role) else "other"
        raise Forbidden("forbidden", action="read", role=role)
    prompt = build_prompt(CASE_NARRATIVE, model, candidate, left, right)
    answering: Provider = provider
    try:
        assert_masked(prompt, model, (left, right))
    except ValueError:
        logger.warning(safe_message("ai_prompt_refused", entity=model.entity, purpose=CASE_NARRATIVE))
        answering = StubProvider()
    name = getattr(answering, "name", "")
    store.append_access(
        actor,
        AI_CALL,
        model.entity,
        candidate.master_id,
        None,
        CASE_NARRATIVE,
        safe_detail(
            provider=name if isinstance(name, str) and SAFE_TEXT_RE.match(name) else "other",
            purpose=CASE_NARRATIVE,
            prompt_sha256=prompt_sha256(prompt.text),
        ),
    )
    return answering.suggest(CASE_NARRATIVE, prompt)


def prompt_sha256(text: str) -> str:
    """The hex SHA-256 of a prompt's text: what the access log keeps in place of the prompt."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
