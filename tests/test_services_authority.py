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
