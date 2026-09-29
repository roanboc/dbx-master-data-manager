"""Authority: personas only in the local mode, roles, the automated authority (owner: SERVICES, B.10)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from mdm.config import Settings
from mdm.models.authority import AUTOMATED_MATCHER, Actor
from mdm.models.errors import Forbidden, PlatformRefused
from mdm.services.authority import AuthorityService, bootstrap_authority, require


def _service(env: dict[str, str], workspace=None) -> AuthorityService:
    return AuthorityService(Settings.from_env(env), store=None, registry=None, workspace=workspace)  # type: ignore[arg-type]


def _workspace(name: str):
    me = SimpleNamespace(user_name=name)
    return lambda: SimpleNamespace(current_user=SimpleNamespace(me=lambda: me))


def test_the_default_persona_is_the_data_owner_locally() -> None:
    actor = _service({}).resolve_actor()
    assert actor == Actor("persona:data_owner", "person", "data_owner", persona=True)
    assert _service({}).resolve_actor("data_steward").role == "data_steward"
    assert _service({"MDM_ROLE": "technical_steward"}).resolve_actor().role == "technical_steward"
    with pytest.raises(Forbidden):
        _service({}).resolve_actor("wizard")


@pytest.mark.parametrize(
    "env",
    [
        {"MDM_LAKEBASE_ENDPOINT": "projects/p/branches/b/endpoints/e"},
        {"DATABRICKS_APP_NAME": "mdm"},
        {"MDM_BACKEND": "postgres", "MDM_POSTGRES_DSN": "postgresql://localhost/x"},
    ],
)
def test_a_persona_is_refused_on_a_shared_store(env) -> None:
    with pytest.raises(PlatformRefused):
        _service(env).resolve_actor("data_owner")
    with pytest.raises(PlatformRefused):
        _service({**env, "MDM_ROLE": "data_owner"}).resolve_actor()


def test_a_test_postgres_may_take_personas_and_a_client_id_alone_refuses_nothing() -> None:
    env = {
        "MDM_BACKEND": "postgres",
        "MDM_POSTGRES_DSN": "postgresql://localhost/x",
        "MDM_ALLOW_PERSONAS": "1",
    }
    assert _service(env).resolve_actor("data_steward").persona
    assert _service({"DATABRICKS_CLIENT_ID": "an-id"}).resolve_actor().role == "data_owner"


def test_on_a_shared_store_a_person_without_a_mapping_is_a_consumer() -> None:
    env = {"DATABRICKS_APP_PORT": "8000"}
    actor = _service(env, workspace=_workspace("reader-one")).resolve_actor()
    assert actor == Actor("reader-one", "person", "consumer")

    def broken():
        raise RuntimeError("no workspace")

    assert _service(env, workspace=broken).resolve_actor().role == "consumer"


def test_roles_allow_actions() -> None:
    consumer = Actor("reader", "person", "consumer")
    with pytest.raises(Forbidden) as raised:
        require(consumer, "publish_model")
    assert str(raised.value) == "forbidden action=publish_model role=consumer"
    require(Actor("o", "person", "data_owner"), "publish_model")
    require(AUTOMATED_MATCHER, "arrival")
    with pytest.raises(Forbidden):
        require(Actor("someone", "person", "data_steward"), "arrival")  # only the matcher commits arrivals
    with pytest.raises(Forbidden):
        require(consumer, "no_such_action")


def test_the_automated_authority_names_rule_versions_and_clauses(hub) -> None:
    model = hub.registry.published("person")
    authority = hub.authority.automated_authority(model, ["hr.new=auto", "crm.update=auto", "hr.new=auto"])
    assert authority.kind == "rule_version"
    assert authority.ref == (
        "person: model v1, match v1, survivorship v1, validation v1; clauses crm.update=auto, hr.new=auto"
    )
    assert hub.authority.clause_held(model, "hr.new=auto")
    assert not hub.authority.clause_held(model, "crm.critical_update=auto")  # crm holds critical updates
    assert hub.authority.clause_held(model, "rule1:delete") and not hub.authority.clause_held(
        model, "rule1:x"
    )
    assert bootstrap_authority("person", "model", 1).kind == "bootstrap"


def test_the_workbench_actor_is_a_persona_locally_and_the_forwarded_user_in_an_app() -> None:
    """Decision 21: a persona only on a local store (the data steward by default, two tabs one actor); in a
    Databricks App the user the platform forwards, as a consumer, a persona never honoured; a laptop pointed at
    a shared store is the signed-in user as a consumer."""
    local = _service({})
    steward = Actor("persona:data_steward", "person", "data_steward", persona=True)
    assert local.actor_for_request(persona=None, forwarded_user=None) == steward
    assert local.actor_for_request(persona="data_steward", forwarded_user="someone") == steward
    assert local.actor_for_request(persona="data_owner", forwarded_user=None).role == "data_owner"
    assert (
        _service({"MDM_ROLE": "coordinating_steward"})
        .actor_for_request(persona=None, forwarded_user=None)
        .role
        == "coordinating_steward"
    )
    with pytest.raises(Forbidden) as unknown:
        local.actor_for_request(persona="wizard", forwarded_user=None)
    assert unknown.value.code == "unknown_role"
    app = _service({"DATABRICKS_APP_NAME": "mdm", "DATABRICKS_APP_PORT": "8000"})
    forwarded = app.actor_for_request(persona="data_owner", forwarded_user="reader-two")
    assert forwarded == Actor("reader-two", "person", "consumer")
    with pytest.raises(PlatformRefused) as nobody:
        app.actor_for_request(persona=None, forwarded_user=None)
    assert nobody.value.code == "no_forwarded_user"
    with pytest.raises(PlatformRefused):
        app.actor_for_request(persona=None, forwarded_user="  ")
    laptop = _service(
        {"MDM_LAKEBASE_ENDPOINT": "projects/p/branches/b/endpoints/e"}, workspace=_workspace("reader-three")
    )
    assert laptop.actor_for_request(persona="data_owner", forwarded_user="ignored") == Actor(
        "reader-three", "person", "consumer"
    )


def test_a_batch_chunk_names_its_batch_and_above_the_threshold_its_checker() -> None:
    """Story 3.3: a chunk of a batch its maker did not make, or of no batch, is refused; above the threshold the
    chunk names the recorded second steward, who is not the maker."""
    from mdm.models.authority import Authority
    from mdm.models.changes import new_change_set

    maker = Actor("persona:data_steward", "person", "data_steward", persona=True)
    checker = Actor("persona:coordinating_steward", "person", "coordinating_steward", persona=True)
    batch = SimpleNamespace(maker=maker.name, decisions=300, checker=checker.name)
    store = SimpleNamespace(batches=lambda ids: {i: batch for i in ids if i == "BAT-" + "e" * 20})
    service = AuthorityService(Settings(), store=store, registry=None)  # type: ignore[arg-type]

    def chunk(named: str | None, with_checker: Actor | None) -> object:
        return new_change_set(
            "person",
            "batch_link",
            maker,
            Authority("role", "data_steward"),
            (),
            planning_version=0,
            evidence={"batch_id": named} if named else {},
            checker=with_checker,
        )

    for named, expected in ((None, "batch_unknown"), ("BAT-" + "f" * 20, "batch_unknown")):
        with pytest.raises(Forbidden) as refused:
            service.check(chunk(named, checker))
        assert refused.value.code == expected
    with pytest.raises(Forbidden) as missing:
        service.check(chunk("BAT-" + "e" * 20, None))
    assert missing.value.code == "checker_required"
    with pytest.raises(Forbidden) as own:
        service.check(chunk("BAT-" + "e" * 20, maker))
    assert own.value.code == "checker_is_maker"
    other = Actor("persona:data_owner", "person", "coordinating_steward", persona=True)
    with pytest.raises(Forbidden) as unrecorded:
        service.check(chunk("BAT-" + "e" * 20, other))
    assert unrecorded.value.code == "checker_not_recorded"
    service.check(chunk("BAT-" + "e" * 20, checker))


def test_a_second_data_steward_persona_acts_locally_under_its_own_name() -> None:
    """Story 3.3: a batch's maker, its second steward and a blind reviewer of its links are three people on a
    local store, so the local mode offers a second data steward: its own name, the data steward's role, and
    refused on a shared store like every persona."""
    second = Actor("persona:data_steward_2", "person", "data_steward", persona=True)
    local = _service({})
    assert local.resolve_actor("data_steward_2") == second
    assert local.actor_for_request(persona="data_steward_2", forwarded_user=None) == second
    assert (
        _service({"MDM_ROLE": "data_steward_2"}).actor_for_request(persona=None, forwarded_user=None)
        == second
    )
    assert second.name != local.resolve_actor("data_steward").name
    for env in (
        {"MDM_LAKEBASE_ENDPOINT": "projects/p/branches/b/endpoints/e"},
        {"DATABRICKS_APP_NAME": "mdm"},
    ):
        with pytest.raises(PlatformRefused):
            _service(env).resolve_actor("data_steward_2")
    app = _service({"DATABRICKS_APP_NAME": "mdm", "DATABRICKS_APP_PORT": "8000"})
    assert app.actor_for_request(persona="data_steward_2", forwarded_user="reader-two").role == "consumer"
    with pytest.raises(Forbidden) as unknown:
        local.resolve_actor("data_steward_3")
    assert unknown.value.code == "unknown_role"
