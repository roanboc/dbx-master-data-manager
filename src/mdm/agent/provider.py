"""Providers of suggestions: the deterministic stub (the local default) and a serving endpoint (owner: SERVICES, B.11).

The stub fills templates from a match explanation and never leaves the
process. The endpoint provider sends one chat request, in the chat-completions
shape every model the platform serves speaks, through the Databricks SDK's
`serving_endpoints.query`; the SDK is imported lazily, inside this module's
functions, never at module import. Any endpoint error falls back to the
stub's suggestion, so a suggestion is always given and always labelled.

A provider only ever sees a `MaskedPrompt`; the check that it carries no
personal value runs before the call (`mdm.agent.narrative`, decision 17).
"""

from __future__ import annotations

import importlib
import logging
import re
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from mdm.agent.prompt import MaskedPrompt
from mdm.config import Settings
from mdm.models.entity_model import EntityModel
from mdm.models.errors import ConfigError
from mdm.models.match import LEVEL_NULL, Band, Contribution, Counterfactual, Explanation
from mdm.models.safety import SAFE_TEXT_RE, safe_message

logger = logging.getLogger(__name__)

#: the purpose of the one template Release 1 has: a reading of one match explanation
CASE_NARRATIVE = "case_narrative"
#: a serving endpoint's name: letters, digits, dashes and underscores (it becomes part of a URL path)
ENDPOINT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}\Z")
#: the most tokens an endpoint may answer with, and the most characters of its answer kept
MAX_TOKENS = 400
MAX_ANSWER_CHARS = 2000
#: the SDK's per-request timeout and its whole retry budget, in seconds: a suggestion is not worth a long wait
HTTP_TIMEOUT_SECONDS = 30
RETRY_TIMEOUT_SECONDS = 60
#: what every endpoint request says before the prompt; carries no value
SYSTEM_TEXT = (
    "You advise data stewards who turn source records into golden records. The arithmetic decides; you "
    "only explain it, and nothing you write is committed. A value shown as one character followed by *** "
    "is masked, and a null value is masked or missing: never guess or reconstruct a personal value. "
    "Answer in plain language, in at most four sentences."
)

#: (endpoint name, chat-completions request body) -> chat-completions response document
Transport = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class Suggestion:
    text: str
    provider: str
    purpose: str
    labelled: bool = True  # shown as a machine suggestion, never as a decision


class Provider(Protocol):
    name: str

    def suggest(self, purpose: str, prompt: MaskedPrompt) -> Suggestion: ...


# ------------------------------------------------------------------------------------------------ the stub


class StubProvider:
    """Deterministic templates from the explanation; the local default."""

    name = "stub"

    def suggest(self, purpose: str, prompt: MaskedPrompt) -> Suggestion:
        """The template of `purpose` filled from the prompt's fields; never raises."""
        try:
            if purpose == CASE_NARRATIVE and isinstance(prompt.fields.get("explanation"), Mapping):
                text = narrate(prompt.fields)
            else:
                text = "No suggestion: the stub has no template for this purpose."
        except (KeyError, TypeError, ValueError):
            text = "No suggestion: the explanation could not be read."
        return Suggestion(text=text, provider=self.name, purpose=purpose)


_BAND_WORDS = {Band.AUTO: "automatic", Band.REVIEW: "review", Band.DISTINCT: "distinct"}
#: how a level reads after a comparison's name: "family name exact", "birth date in the same year"
_LEVEL_WORDS = {
    "exact": "exact",
    "else": "differs",
    "null": "missing",
    "phonetic": "sounding alike",
    "swap_or_digit": "with day and month swapped or one digit different",
    "same_year": "in the same year",
    "equal_unchecked": "equal without a valid checksum",
    "last7": "equal in the last seven digits",
    "local_part": "alike in the local part",
    "prefix": "sharing a prefix",
}
_THRESHOLD_LABEL = re.compile(r"^(jw|lev|tokens)(>=|<=)([0-9.]+)\Z")
_NAME_WORDS = {"id": "ID", "ref": "reference", "reg": "registration"}
_MINUS = "−"


def narrate(fields: Mapping[str, Any]) -> str:
    """The case narrative's stub text: the score and band, the strongest and weakest comparisons, what is
    missing, the hard rule that decided, and the smallest change that would move the band.

    "Scores 71 (review). Strongest: family name exact (+8.7). Weakest: phone differs (−1.4).
    A matching person reference would give 99 (automatic)."
    """
    explanation = Explanation.from_dict(fields["explanation"])
    sentences = [f"Scores {_score(explanation.score)} ({_BAND_WORDS[explanation.band]})."]
    if explanation.hard_rule:
        sentences.append(_hard_rule_sentence(explanation.hard_rule))
    else:
        weighed = [c for c in explanation.contributions if c.level != LEVEL_NULL]
        strongest = max((c for c in weighed if c.weight > 0), key=lambda c: c.weight, default=None)
        weakest = min((c for c in weighed if c.weight < 0), key=lambda c: c.weight, default=None)
        if strongest is not None:
            sentences.append(f"Strongest: {_contribution_words(strongest)}.")
        if weakest is not None:
            sentences.append(f"Weakest: {_contribution_words(weakest)}.")
    missing = [_name(c.comparison) for c in explanation.contributions if c.level == LEVEL_NULL]
    if missing:
        sentences.append(f"Missing on one side: {', '.join(missing)}.")
    bands = fields.get("bands")
    edges = (float(bands["upper"]), float(bands["lower"])) if isinstance(bands, Mapping) else None
    sentences.extend(_counterfactual_sentence(c, edges) for c in explanation.counterfactuals)
    blocked_by = fields.get("blocked_by")
    if isinstance(blocked_by, str) and blocked_by:
        kind, _, attribute = blocked_by.partition(":")
        sentences.append(
            f"Blocked: the {kind.replace('_', '-')} rule on {_name(attribute)} holds against a member "
            "of this golden record."
        )
    return " ".join(sentences)


def _score(score: float) -> str:
    """A whole-number score; a score short of certainty never reads 100."""
    shown = round(score)
    if shown >= 100 and score < 100:
        shown = 99
    return str(int(shown))


def _signed(weight: float) -> str:
    return f"{weight:+.1f}".replace("-", _MINUS)


def _name(comparison: str) -> str:
    """`person_ref` -> "person reference", `registered_id` -> "registered ID"."""
    return " ".join(_NAME_WORDS.get(part, part) for part in comparison.split("_") if part)


def _level_words(label: str) -> str:
    if label in _LEVEL_WORDS:
        return _LEVEL_WORDS[label]
    found = _THRESHOLD_LABEL.match(label)
    if found is None:
        return label
    measure, _relation, value = found.groups()
    if measure == "jw":
        return f"similar (Jaro-Winkler at least {value})"
    if measure == "lev":
        return f"within {value} edit{'' if value == '1' else 's'}"
    return f"sharing most words (token-set ratio at least {value})"


def _contribution_words(contribution: Contribution) -> str:
    return f"{_name(contribution.comparison)} {_level_words(contribution.label)} ({_signed(contribution.weight)})"


def _counterfactual_sentence(counterfactual: Counterfactual, edges: tuple[float, float] | None) -> str:
    """The smallest change that moves the band, read: `A matching person reference would give 99
    (automatic).`, or, when a hard rule and not the score would set the band, `A different person
    reference would make it distinct by a hard rule.`"""
    name = _name(counterfactual.comparison)
    if counterfactual.to_label == "exact":
        subject = f"A matching {name}"
    elif counterfactual.to_label == "else":
        subject = f"A different {name}"
    else:
        subject = f"{name[:1].upper()}{name[1:]} {_level_words(counterfactual.to_label)}"
    band = _BAND_WORDS[counterfactual.band]
    if edges is not None and _band_of(counterfactual.score, edges) is not counterfactual.band:
        return f"{subject} would make it {band} by a hard rule."
    return f"{subject} would give {_score(counterfactual.score)} ({band})."


def _band_of(score: float, edges: tuple[float, float]) -> Band:
    upper, lower = edges
    if score >= upper:
        return Band.AUTO
    return Band.REVIEW if score >= lower else Band.DISTINCT


def _hard_rule_sentence(rule: str) -> str:
    kind, _, attribute = rule.partition(":")
    if kind == "must_link":
        return f"Decided by the must-link rule: the {_name(attribute)} matches."
    if kind == "cannot_link":
        return f"Kept apart by the cannot-link rule: the {_name(attribute)} differs."
    return f"Decided by the rule {rule}."


# ------------------------------------------------------------------------------------------- the endpoint


def chat_body(prompt: MaskedPrompt, max_tokens: int = MAX_TOKENS) -> dict[str, Any]:
    """The chat-completions request of one prompt: the fixed system text and the prompt's text, nothing else."""
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_TEXT},
            {"role": "user", "content": prompt.text},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.0,
    }


def answer_text(response: Mapping[str, Any]) -> str:
    """The first choice's text of a chat-completions response; ValueError when there is none."""
    choices = response.get("choices")
    if not isinstance(choices, Sequence) or isinstance(choices, str) or not choices:
        raise ValueError("no choices")
    choice = choices[0]
    if not isinstance(choice, Mapping):
        raise ValueError("malformed choice")
    message = choice.get("message")
    content = message.get("content") if isinstance(message, Mapping) else None
    text = _content_text(content) if content is not None else _content_text(choice.get("text"))
    text = " ".join(text.split())
    if not text:
        raise ValueError("empty answer")
    if len(text) > MAX_ANSWER_CHARS:
        text = text[: MAX_ANSWER_CHARS - 1].rstrip() + "…"
    return text


def _content_text(content: Any) -> str:
    """A message's text, whether a string or a list of parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif (
                isinstance(part, Mapping) and part.get("type") == "text" and isinstance(part.get("text"), str)
            ):
                parts.append(part["text"])
        return "".join(parts)
    return ""


def default_workspace() -> Any:
    """A `databricks.sdk.WorkspaceClient` with short timeouts: the app's identity on the platform, the
    person's own Databricks sign-in on a laptop (the `databricks` extra)."""
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.core import Config

    return WorkspaceClient(
        config=Config(http_timeout_seconds=HTTP_TIMEOUT_SECONDS, retry_timeout_seconds=RETRY_TIMEOUT_SECONDS)
    )


def sdk_transport(workspace: Callable[[], Any]) -> Transport:
    """A transport over `workspace().serving_endpoints.query(name=…, messages=[…])`, the client made once."""
    lock = threading.Lock()
    made: list[Any] = []

    def client() -> Any:
        with lock:
            if not made:
                made.append(workspace())
            return made[0]

    def send(endpoint: str, body: Mapping[str, Any]) -> Mapping[str, Any]:
        from databricks.sdk.service.serving import ChatMessage

        response = client().serving_endpoints.query(
            name=endpoint,
            messages=[ChatMessage.from_dict(dict(message)) for message in body["messages"]],
            max_tokens=body.get("max_tokens"),
            temperature=body.get("temperature"),
        )
        return response.as_dict() if hasattr(response, "as_dict") else response

    return send


class ServingEndpointProvider:
    """w.serving_endpoints.query(name=…, messages=[…]); any error -> the stub's suggestion.

    `workspace` makes the SDK's workspace client (default: `default_workspace`); `transport`, when given,
    replaces the SDK altogether and is what the tests hand in.
    """

    name = "endpoint"

    def __init__(
        self,
        endpoint: str,
        workspace: Callable[[], Any] | None = None,
        *,
        transport: Transport | None = None,
        fallback: Provider | None = None,
        max_tokens: int = MAX_TOKENS,
    ) -> None:
        if not endpoint or not ENDPOINT_NAME_RE.match(endpoint):
            raise ConfigError("bad_setting", variable="MDM_AGENT_ENDPOINT")
        self.endpoint = endpoint
        self.max_tokens = max_tokens
        self._transport = transport or sdk_transport(workspace or default_workspace)
        self._fallback: Provider = fallback or StubProvider()

    def suggest(self, purpose: str, prompt: MaskedPrompt) -> Suggestion:
        try:
            text = answer_text(self._transport(self.endpoint, chat_body(prompt, self.max_tokens)))
        except Exception as exc:  # noqa: BLE001 - any endpoint failure falls back to the stub
            logger.warning(
                safe_message(
                    "ai_endpoint_failed",
                    endpoint=self.endpoint,
                    purpose=_token(purpose),
                    error=type(exc).__name__,
                )
            )
            return self._fallback.suggest(purpose, prompt)
        return Suggestion(text=text, provider=self.name, purpose=purpose)


# ------------------------------------------------------------------------------------------------ choosing


def sdk_available() -> bool:
    """Whether the Databricks SDK imports (the `databricks` extra is installed)."""
    try:
        importlib.import_module("databricks.sdk")
    except ImportError:
        return False
    return True


def choose_provider(
    settings: Settings,
    model: EntityModel,
    *,
    workspace: Callable[[], Any] | None = None,
    transport: Transport | None = None,
) -> Provider:
    """The stub unless model.ai_enabled, settings.agent_provider in ("auto", "endpoint"),
    settings.agent_endpoint is set, and the SDK imports.

    A `transport` stands in for the SDK (tests); a malformed endpoint name also gives the stub, with a
    warning, so a suggestion is always labelled with the provider that gave it.
    """
    if not model.ai_enabled or settings.agent_provider not in ("auto", "endpoint"):
        return StubProvider()
    if not settings.agent_endpoint:
        return StubProvider()
    if transport is None and not sdk_available():
        logger.warning(safe_message("ai_sdk_missing", entity=model.entity, extra="databricks"))
        return StubProvider()
    try:
        return ServingEndpointProvider(settings.agent_endpoint, workspace, transport=transport)
    except ConfigError:
        logger.warning(
            safe_message("ai_endpoint_invalid", entity=model.entity, variable="MDM_AGENT_ENDPOINT")
        )
        return StubProvider()


def _token(text: str) -> str:
    """`text` when a safe message may carry it, else "other"."""
    return text if isinstance(text, str) and SAFE_TEXT_RE.match(text) else "other"
