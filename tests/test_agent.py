"""The assistance plumbing: provider choice, masked prompts, the stub and the call log (B.11, decision 17).

No test reaches a network: the endpoint is a fake transport, or a fake SDK put
in `sys.modules`. Every name and value here is invented.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import sys
import types
from collections.abc import Mapping
from datetime import date
from typing import Any

import pytest

from mdm.agent import (
    MaskedPrompt,
    ServingEndpointProvider,
    StubProvider,
    Suggestion,
    assert_masked,
    build_prompt,
    case_narrative,
    choose_provider,
)
from mdm.agent import narrative as narrative_module
from mdm.agent.narrative import AI_CALL, prompt_sha256
from mdm.agent.prompt import masked_values
from mdm.agent.provider import (
    CASE_NARRATIVE,
    HTTP_TIMEOUT_SECONDS,
    MAX_ANSWER_CHARS,
    RETRY_TIMEOUT_SECONDS,
    SYSTEM_TEXT,
    answer_text,
    chat_body,
    narrate,
)
from mdm.config import Settings
from mdm.engine.score import compile_rules, explain
from mdm.models.authority import Actor
from mdm.models.entity_model import EntityModel
from mdm.models.errors import ConfigError, Forbidden
from mdm.models.match import Band, Contribution, Counterfactual, Explanation, GoldenCandidate, PairScore
from mdm.models.records import SourceKey
from tests.helpers import arrive, hr_key, land, master_of, person_payload, person_ref
from tests.helpers import row as landing_row

STEWARD = Actor(name="steward-one", kind="person", role="data_steward")
ENDPOINT = "mdm-assistant"
ANSWER = "The family name agrees exactly; the phone numbers differ, which weighs against."

#: the record tested: every personal attribute set, a group, and a key the model does not name
LEFT: dict[str, Any] = {
    "source_system": "crm",
    "given_name": "Oriel",
    "family_name": "Vantrasse",
    "birth_date": "1984-05-17",
    "email": "oriel.vantrasse@example.org",
    "phone": "+999 7700 900123",
    "postcode": "XA9 4QT",
    "city": "Fernwick Vale",
    "country": "XA",
    "person_ref": "PR45395787",
    "addresses": [
        {"kind": "home", "line1": "12 Orchard Row", "city": "Fernwick Vale", "postcode": "XA9 4QT"}
    ],
    "nickname": "Orry Vant",
}
#: the golden record's values, as the entity table returns them
RIGHT: dict[str, Any] = {
    "given_name": "Oriel",
    "family_name": "Vantrasse",
    "birth_date": date(1984, 5, 17),
    "email": "o.vantrasse@example.com",
    "phone": "+999 7700 900456",
    "postcode": "XA9 4QT",
    "city": "Fernwick Vale",
    "country": "XA",
    "person_ref": None,
    "left_on": date(2025, 7, 31),
}
#: every personal value above, as text; none may reach a prompt, a log row or a log line
PERSONAL = (
    "Oriel",
    "Vantrasse",
    "1984-05-17",
    "oriel.vantrasse@example.org",
    "o.vantrasse@example.com",
    "7700 900123",
    "7700 900456",
    "XA9 4QT",
    "PR45395787",
    "12 Orchard Row",
    "Orry Vant",
)


def _explanation(**changes: Any) -> Explanation:
    """Scores 71 (review): family name exact, given name phonetic, phone differs, email missing."""
    explanation = Explanation(
        prior=-9.97,
        contributions=(
            Contribution("given_name", 1, "phonetic", 3.0),
            Contribution("family_name", 0, "exact", 8.73),
            Contribution("birth_date", 2, "same_year", 1.0),
            Contribution("email", -1, "null", 0.0),
            Contribution("phone", 2, "else", -1.44),
            Contribution("postcode", 1, "prefix", 0.0),
            Contribution("person_ref", 2, "else", -0.03),
        ),
        hard_rule=None,
        weight=1.3,
        score=71.2,
        band=Band.REVIEW,
        counterfactuals=(Counterfactual("person_ref", 2, 0, "exact", 99.3, Band.AUTO, "up"),),
        signature="given_name≈ · family_name= · birth_date≈ · email∅ · phone≠ · postcode≈ · person_ref≠",
        rule_version=1,
    )
    return dataclasses.replace(explanation, **changes)


def _candidate(explanation: Explanation | None = None, blocked_by: str | None = None) -> GoldenCandidate:
    pair = PairScore(SourceKey("crm", "C000123"), SourceKey("hr", "H000045"), explanation or _explanation())
    return GoldenCandidate("PER-000012", pair, members_scored=2, blocked_by=blocked_by)


def _ai(model: EntityModel) -> EntityModel:
    return dataclasses.replace(model, ai_enabled=True)


def _prompt_haystack(prompt: MaskedPrompt) -> str:
    return (prompt.text + json.dumps(prompt.fields, default=str, ensure_ascii=False)).casefold()


class FakeTransport:
    """Records each request; answers with `answer`, or raises `error`."""

    def __init__(self, answer: Mapping[str, Any] | None = None, error: Exception | None = None) -> None:
        self.answer = answer if answer is not None else {"choices": [{"message": {"content": ANSWER}}]}
        self.error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, endpoint: str, body: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append((endpoint, json.loads(json.dumps(body))))
        if self.error is not None:
            raise self.error
        return self.answer


@pytest.fixture
def fake_sdk(monkeypatch: pytest.MonkeyPatch) -> types.SimpleNamespace:
    """A stand-in for the Databricks SDK in `sys.modules`: WorkspaceClient, Config and ChatMessage."""
    seen = types.SimpleNamespace(queries=[], clients=[], answer=ANSWER)

    class ChatMessage:
        def __init__(self, content: str | None = None, role: str | None = None) -> None:
            self.content, self.role = content, role

        @classmethod
        def from_dict(cls, doc: Mapping[str, Any]) -> ChatMessage:
            return cls(doc.get("content"), doc.get("role"))

    class Response:
        def as_dict(self) -> dict[str, Any]:
            return {"choices": [{"index": 0, "message": {"role": "assistant", "content": seen.answer}}]}

    class ServingEndpoints:
        def query(self, name: str, **kwargs: Any) -> Response:
            seen.queries.append((name, kwargs))
            return Response()

    class Config:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    class WorkspaceClient:
        def __init__(self, config: Config | None = None) -> None:
            self.config = config
            self.serving_endpoints = ServingEndpoints()
            seen.clients.append(self)

    modules = {
        "databricks": types.ModuleType("databricks"),
        "databricks.sdk": types.ModuleType("databricks.sdk"),
        "databricks.sdk.core": types.ModuleType("databricks.sdk.core"),
        "databricks.sdk.service": types.ModuleType("databricks.sdk.service"),
        "databricks.sdk.service.serving": types.ModuleType("databricks.sdk.service.serving"),
    }
    modules["databricks.sdk"].WorkspaceClient = WorkspaceClient  # type: ignore[attr-defined]
    modules["databricks.sdk.core"].Config = Config  # type: ignore[attr-defined]
    modules["databricks.sdk.service.serving"].ChatMessage = ChatMessage  # type: ignore[attr-defined]
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    return seen


@pytest.fixture
def no_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """The `databricks` extra is not installed."""
    monkeypatch.setitem(sys.modules, "databricks.sdk", None)


# ------------------------------------------------------------------------------------------ choosing


def test_the_stub_is_the_default(person_model: EntityModel) -> None:
    assert not person_model.ai_enabled
    assert isinstance(choose_provider(Settings(), person_model), StubProvider)
    configured = Settings(agent_endpoint=ENDPOINT, agent_provider="endpoint")
    assert isinstance(choose_provider(configured, person_model, transport=FakeTransport()), StubProvider)


def test_an_endpoint_needs_the_entity_the_setting_and_a_name(person_model: EntityModel) -> None:
    model = _ai(person_model)
    fake = FakeTransport()
    assert isinstance(choose_provider(Settings(), model, transport=fake), StubProvider)
    stub_setting = Settings(agent_endpoint=ENDPOINT, agent_provider="stub")
    assert isinstance(choose_provider(stub_setting, model, transport=fake), StubProvider)
    for wanted in ("auto", "endpoint"):
        chosen = choose_provider(
            Settings(agent_endpoint=ENDPOINT, agent_provider=wanted), model, transport=fake
        )
        assert isinstance(chosen, ServingEndpointProvider) and chosen.endpoint == ENDPOINT
    assert fake.calls == []  # choosing sends nothing


def test_without_the_sdk_the_stub_answers(person_model: EntityModel, no_sdk: None) -> None:
    for wanted in ("auto", "endpoint"):
        settings = Settings(agent_endpoint=ENDPOINT, agent_provider=wanted)
        assert isinstance(choose_provider(settings, _ai(person_model)), StubProvider)


def test_a_malformed_endpoint_name_gives_the_stub(
    person_model: EntityModel, caplog: pytest.LogCaptureFixture
) -> None:
    settings = Settings(agent_endpoint="../workspace/other?x=1", agent_provider="endpoint")
    with caplog.at_level(logging.WARNING, logger="mdm.agent"):
        chosen = choose_provider(settings, _ai(person_model), transport=FakeTransport())
    assert isinstance(chosen, StubProvider)
    assert "ai_endpoint_invalid" in caplog.text and "../workspace" not in caplog.text
    with pytest.raises(ConfigError):
        ServingEndpointProvider("", transport=FakeTransport())


def test_with_the_sdk_the_endpoint_is_queried_through_it(
    person_model: EntityModel, fake_sdk: types.SimpleNamespace
) -> None:
    settings = Settings(agent_endpoint=ENDPOINT)
    provider = choose_provider(settings, _ai(person_model))
    assert isinstance(provider, ServingEndpointProvider)
    assert fake_sdk.clients == []  # the client is made on the first call, not on choosing
    prompt = build_prompt(CASE_NARRATIVE, person_model, _candidate(), LEFT, RIGHT)
    first = provider.suggest(CASE_NARRATIVE, prompt)
    second = provider.suggest(CASE_NARRATIVE, prompt)
    assert first == second == Suggestion(ANSWER, "endpoint", CASE_NARRATIVE)
    assert len(fake_sdk.clients) == 1
    assert fake_sdk.clients[0].config.kwargs == {
        "http_timeout_seconds": HTTP_TIMEOUT_SECONDS,
        "retry_timeout_seconds": RETRY_TIMEOUT_SECONDS,
    }
    name, sent = fake_sdk.queries[0]
    assert name == ENDPOINT
    assert [(m.role, m.content) for m in sent["messages"]] == [("system", SYSTEM_TEXT), ("user", prompt.text)]
    assert sent["temperature"] == 0.0 and sent["max_tokens"] > 0


# ------------------------------------------------------------------------------------------ the prompt


def test_a_prompt_carries_identifiers_scores_and_masked_values_only(person_model: EntityModel) -> None:
    prompt = build_prompt(CASE_NARRATIVE, person_model, _candidate(), LEFT, RIGHT)
    haystack = _prompt_haystack(prompt)
    for value in PERSONAL:
        assert value.casefold() not in haystack, value
    assert prompt.purpose == CASE_NARRATIVE
    assert prompt.fields["master_id"] == "PER-000012"
    assert (prompt.fields["record"], prompt.fields["member"]) == ("crm:C000123", "hr:H000045")
    assert prompt.fields["explanation"] == _candidate().best.explanation.to_dict()
    record = prompt.fields["record_values"]
    assert record["given_name"] == "O***" and record["family_name"] == "V***"
    assert record["birth_date"] is None and record["addresses"] is None
    assert record["city"] == "Fernwick Vale" and "nickname" not in record and "source_system" not in record
    assert prompt.fields["golden_values"]["left_on"] == "2025-07-31"
    assert "71.2" in prompt.text and "PER-000012" in prompt.text
    assert_masked(prompt, person_model, (LEFT, RIGHT))
    assert build_prompt(CASE_NARRATIVE, person_model, _candidate(), LEFT, RIGHT) == prompt  # deterministic


def test_masked_values_follow_the_read_views(person_model: EntityModel) -> None:
    masked = masked_values(person_model, {"given_name": {"$vault": "V1"}, "email": "x", "country": "XA"})
    assert masked == {"given_name": None, "email": "x***", "country": "XA"}
    with pytest.raises(ValueError):
        build_prompt("Case narrative!", person_model, _candidate(), LEFT, RIGHT)


def test_the_check_finds_a_personal_value_anywhere_in_a_prompt(person_model: EntityModel) -> None:
    def leaked(text: str = "", **fields: Any) -> MaskedPrompt:
        return MaskedPrompt(CASE_NARRATIVE, text or "Purpose: case_narrative", fields)

    for prompt in (
        leaked("The record is VANTRASSE, born 1984-05-17"),
        leaked(note="oriel"),
        leaked(values={"line": "12 orchard row"}),
        leaked("Call +999 7700 900123"),
        leaked(sample=["XA9", "4QT"]),  # a value split over two leaves is still found as a run of words
    ):
        with pytest.raises(ValueError) as refused:
            assert_masked(prompt, person_model, (LEFT, RIGHT))
        assert "Vantrasse" not in str(refused.value)


def test_the_check_does_not_refuse_what_the_prompt_may_carry(person_model: EntityModel) -> None:
    left = {"given_name": "Ann", "family_name": "Q", "city": "Norvale", "addresses": [{"city": "Norvale"}]}
    prompt = MaskedPrompt(
        CASE_NARRATIVE,
        "We announce a match in Norvale for Q*** and A***.",
        {"given_name": {"$vault": "V-ann"}, "city": "Norvale"},
    )
    assert_masked(prompt, person_model, (left,))  # "Ann" is no word here; "Q" is one character


# ------------------------------------------------------------------------------------------ the stub


def test_the_stub_reads_the_explanation_in_plain_words(person_model: EntityModel) -> None:
    prompt = build_prompt(CASE_NARRATIVE, person_model, _candidate(), LEFT, RIGHT)
    suggestion = StubProvider().suggest(CASE_NARRATIVE, prompt)
    assert suggestion == Suggestion(
        "Scores 71 (review). Strongest: family name exact (+8.7). Weakest: phone differs (−1.4). "
        "Missing on one side: email. A matching person reference would give 99 (automatic).",
        "stub",
        CASE_NARRATIVE,
        labelled=True,
    )


def test_the_stub_names_hard_rules_and_blocks(person_model: EntityModel) -> None:
    linked = _explanation(hard_rule="must_link:person_ref", score=99.97, band=Band.AUTO, counterfactuals=())
    text = narrate(build_prompt(CASE_NARRATIVE, person_model, _candidate(linked), {}, {}).fields)
    assert text.startswith(
        "Scores 99 (automatic). Decided by the must-link rule: the person reference matches."
    )
    assert "Strongest" not in text
    down = Counterfactual("person_ref", 1, 2, "else", 87.7, Band.DISTINCT, "down")
    blocked = _explanation(counterfactuals=(down,))
    text = narrate(
        build_prompt(
            CASE_NARRATIVE, person_model, _candidate(blocked, "cannot_link:person_ref"), {}, {}
        ).fields
    )
    assert "A different person reference would make it distinct by a hard rule." in text
    assert text.endswith(
        "Blocked: the cannot-link rule on person reference holds against a member of this golden record."
    )


def test_the_stub_reads_an_explanation_the_engine_made(person_model: EntityModel) -> None:
    compiled = compile_rules(person_model.match, person_model)
    levels = (3, 1, 3, -1, 1, 2, 2)
    weight = compiled.prior_weight + sum(
        compiled.weight_tables[i][level] for i, level in enumerate(levels) if level >= 0
    )
    explanation = explain(compiled, levels, weight, None)
    prompt = build_prompt(CASE_NARRATIVE, person_model, _candidate(explanation), LEFT, RIGHT)
    text = StubProvider().suggest(CASE_NARRATIVE, prompt).text
    assert text.startswith(f"Scores {round(explanation.score)} (")
    assert "Weakest: " in text and "Missing on one side: email." in text
    assert len(explanation.counterfactuals) >= 1 and "would " in text
    for value in PERSONAL:
        assert value not in text


def test_the_stub_answers_any_purpose_and_any_prompt() -> None:
    other = StubProvider().suggest("rule_draft", MaskedPrompt("rule_draft", "text", {}))
    broken = StubProvider().suggest(CASE_NARRATIVE, MaskedPrompt(CASE_NARRATIVE, "text", {"explanation": {}}))
    assert other.provider == broken.provider == "stub" and other.labelled and broken.labelled
    assert other.text.startswith("No suggestion") and broken.text.startswith("No suggestion")


# ------------------------------------------------------------------------------------------ the endpoint


def test_the_endpoint_sends_the_masked_prompt_in_the_chat_shape(person_model: EntityModel) -> None:
    fake = FakeTransport()
    prompt = build_prompt(CASE_NARRATIVE, person_model, _candidate(), LEFT, RIGHT)
    suggestion = ServingEndpointProvider(ENDPOINT, transport=fake).suggest(CASE_NARRATIVE, prompt)
    assert suggestion == Suggestion(ANSWER, "endpoint", CASE_NARRATIVE, labelled=True)
    [(endpoint, body)] = fake.calls
    assert endpoint == ENDPOINT
    assert body == chat_body(prompt)
    assert body["messages"] == [
        {"role": "system", "content": SYSTEM_TEXT},
        {"role": "user", "content": prompt.text},
    ]
    sent = json.dumps(body, ensure_ascii=False).casefold()
    for value in PERSONAL:
        assert value.casefold() not in sent


@pytest.mark.parametrize(
    "fake",
    [
        FakeTransport(error=RuntimeError("endpoint said Oriel Vantrasse is unknown")),
        FakeTransport(error=TimeoutError()),
        FakeTransport(answer={"choices": []}),
        FakeTransport(answer={"choices": [{"message": {"content": "   "}}]}),
        FakeTransport(answer={"error": {"message": "quota"}}),
    ],
    ids=["error", "timeout", "no-choice", "empty", "error-document"],
)
def test_any_endpoint_failure_falls_back_to_the_stub(
    person_model: EntityModel, fake: FakeTransport, caplog: pytest.LogCaptureFixture
) -> None:
    prompt = build_prompt(CASE_NARRATIVE, person_model, _candidate(), LEFT, RIGHT)
    with caplog.at_level(logging.WARNING, logger="mdm.agent"):
        suggestion = ServingEndpointProvider(ENDPOINT, transport=fake).suggest(CASE_NARRATIVE, prompt)
    assert suggestion == StubProvider().suggest(CASE_NARRATIVE, prompt)
    assert "ai_endpoint_failed" in caplog.text
    for value in PERSONAL:
        assert value not in caplog.text


def test_an_answer_is_read_from_text_or_parts_and_kept_short() -> None:
    assert answer_text({"choices": [{"message": {"content": " Two\n lines. "}}]}) == "Two lines."
    parts = [{"type": "reasoning", "summary": "x"}, {"type": "text", "text": "From parts."}]
    assert answer_text({"choices": [{"message": {"content": parts}}]}) == "From parts."
    assert answer_text({"choices": [{"text": "A completion."}]}) == "A completion."
    long = answer_text({"choices": [{"message": {"content": "word " * 1000}}]})
    assert len(long) == MAX_ANSWER_CHARS and long.endswith("…")
    for bad in ({}, {"choices": "x"}, {"choices": [None]}, {"choices": [{"message": {"content": None}}]}):
        with pytest.raises(ValueError):
            answer_text(bad)


# ------------------------------------------------------------------------------------------ the narrative and its log


def _ai_calls(store: Any) -> list[dict[str, Any]]:
    return [row for row in store.access_log(None, 100) if row["action"] == AI_CALL]


def test_every_narrative_is_labelled_and_logged_without_the_prompt(
    store: Any, person_model: EntityModel
) -> None:
    prompt = build_prompt(CASE_NARRATIVE, person_model, _candidate(), LEFT, RIGHT)
    stub = case_narrative(store, StubProvider(), person_model, _candidate(), LEFT, RIGHT, STEWARD)
    fake = FakeTransport()
    endpoint = ServingEndpointProvider(ENDPOINT, transport=fake)
    answered = case_narrative(store, endpoint, _ai(person_model), _candidate(), LEFT, RIGHT, STEWARD)
    assert stub.labelled and stub.provider == "stub" and stub.text.startswith("Scores 71 (review).")
    assert answered == Suggestion(ANSWER, "endpoint", CASE_NARRATIVE)
    rows = _ai_calls(store)
    assert sorted(row["detail"]["provider"] for row in rows) == ["endpoint", "stub"]  # IDs are random
    for row in rows:
        assert row["detail"] == {
            "provider": row["detail"]["provider"],
            "purpose": CASE_NARRATIVE,
            "prompt_sha256": prompt_sha256(prompt.text),
        }
        assert (row["actor"], row["actor_role"]) == ("steward-one", "data_steward")
        assert (row["entity"], row["master_id"], row["attribute"]) == ("person", "PER-000012", None)
        assert row["reason"] == CASE_NARRATIVE
        stored = json.dumps(row, default=str, ensure_ascii=False)
        assert prompt.text not in stored
        for value in PERSONAL:
            assert value not in stored


def test_a_failing_endpoint_is_logged_as_where_the_prompt_went(store: Any, person_model: EntityModel) -> None:
    endpoint = ServingEndpointProvider(ENDPOINT, transport=FakeTransport(error=ConnectionError()))
    suggestion = case_narrative(store, endpoint, _ai(person_model), _candidate(), LEFT, RIGHT, STEWARD)
    assert suggestion.provider == "stub" and suggestion.labelled  # the stub answered
    assert [row["detail"]["provider"] for row in _ai_calls(store)] == ["endpoint"]  # the prompt went there


def test_no_prompt_leaves_the_process_unless_its_access_row_is_written(
    store: Any, person_model: EntityModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeTransport()
    endpoint = ServingEndpointProvider(ENDPOINT, transport=fake)

    def failing(*args: Any, **kwargs: Any) -> str:
        raise RuntimeError("the audit is unreachable")

    monkeypatch.setattr(store, "append_access", failing)
    with pytest.raises(RuntimeError):
        case_narrative(store, endpoint, _ai(person_model), _candidate(), LEFT, RIGHT, STEWARD)
    assert fake.calls == []


def test_a_prompt_the_check_refuses_never_reaches_the_endpoint(
    store: Any, person_model: EntityModel, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def leaking(purpose: str, *args: Any) -> MaskedPrompt:
        return MaskedPrompt(purpose, "Explain Vantrasse", {"explanation": _explanation().to_dict()})

    monkeypatch.setattr(narrative_module, "build_prompt", leaking)
    fake = FakeTransport()
    endpoint = ServingEndpointProvider(ENDPOINT, transport=fake)
    with caplog.at_level(logging.WARNING, logger="mdm.agent"):
        suggestion = case_narrative(store, endpoint, _ai(person_model), _candidate(), LEFT, RIGHT, STEWARD)
    assert fake.calls == []
    assert suggestion.provider == "stub" and suggestion.text.startswith("Scores 71 (review).")
    assert "ai_prompt_refused" in caplog.text and "Vantrasse" not in caplog.text
    assert [row["detail"]["provider"] for row in _ai_calls(store)] == ["stub"]


def test_a_narrative_needs_a_role_that_may_read(store: Any, person_model: EntityModel) -> None:
    with pytest.raises(Forbidden):
        case_narrative(
            store, StubProvider(), person_model, _candidate(), LEFT, RIGHT, Actor("x", "person", "visitor")
        )
    assert _ai_calls(store) == []


def test_the_narrative_of_a_match_test_on_the_hub(hub: Any) -> None:
    """End to end, as `mdm match --narrative` runs it: a golden record, a match test, the narrative."""
    payload = person_payload(3, person_ref=person_ref(3003))
    land(hub, [landing_row("hr", hr_key(3), "person", payload, version=1)])
    arrive(hub)
    master = master_of(hub, "person", "hr", hr_key(3))
    tested = {**person_payload(3, phone="0999 5599999"), "source_system": "crm"}
    [best, *_] = hub.matching.match_test("person", actor=hub.actor, payload=tested, top=3)
    assert best.master_id == master
    model = hub.registry.published("person")
    golden = hub.store.golden("person", [master])[master]
    suggestion = case_narrative(
        hub.store, choose_provider(hub.settings, model), model, best, tested, dict(golden.values), hub.actor
    )
    assert suggestion.provider == "stub" and suggestion.labelled
    assert suggestion.text.startswith("Scores ")
    [logged] = _ai_calls(hub.store)
    stored = json.dumps(logged, default=str, ensure_ascii=False) + suggestion.text
    for name in ("given_name", "family_name", "email", "birth_date"):
        assert str(payload[name]) not in stored
