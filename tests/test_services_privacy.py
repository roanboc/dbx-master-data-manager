"""The vault, masking, reveals and redaction (owner: SERVICES, B.10)."""

from __future__ import annotations

import pytest

from mdm.models.authority import Actor
from mdm.models.errors import Forbidden
from mdm.models.records import SourceKey
from mdm.services.privacy import source_subject
from tests.test_services_fixtures import (
    arrive,
    crm_person_key,
    hr_key,
    land,
    master_of,
    person_payload,
    person_ref,
    row,
)

STEWARD = Actor("steward-one", "person", "data_steward")
OWNER = Actor("owner-one", "person", "data_owner")
ADMIN = Actor("admin-one", "person", "administrator")


def _one_person(hub) -> str:
    land(hub, [row("hr", hr_key(1), "person", person_payload(1, person_ref=person_ref(1001)), version=1)])
    land(hub, [row("crm", crm_person_key(1), "person", person_payload(1))])
    arrive(hub)
    return master_of(hub, "person", "hr", hr_key(1))


def test_the_record_view_is_masked_and_a_reveal_needs_a_role_and_a_reason(hub) -> None:
    master = _one_person(hub)
    payload = person_payload(1)
    view = hub.privacy.record_view("person", master, actor=STEWARD)
    assert view["values"]["given_name"] == payload["given_name"][0] + "***"
    assert view["values"]["birth_date"] is None and view["values"]["city"] == payload["city"]
    assert sorted(view["members"]) == sorted([f"hr:{hr_key(1)}", f"crm:{crm_person_key(1)}"])
    assert "value" not in view["provenance"]["given_name"]["winner"]
    with pytest.raises(Forbidden):
        hub.privacy.record_view("person", master, actor=STEWARD, reveal=["given_name"])  # no reason
    with pytest.raises(Forbidden):
        hub.privacy.record_view(
            "person", master, actor=Actor("r", "person", "consumer"), reveal=["given_name"], reason="x"
        )
    revealed = hub.privacy.record_view(
        "person", master, actor=STEWARD, reveal=["given_name"], reason="checking"
    )
    assert revealed["values"]["given_name"] == payload["given_name"]
    assert revealed["provenance"]["given_name"]["winner"]["value"] == payload["given_name"]
    log = hub.store.access_log(None, 10)
    assert [(r["action"], r["attribute"], r["reason"]) for r in log] == [("reveal", "given_name", "checking")]


def test_vault_references_in_versions_and_provenance(hub) -> None:
    master = _one_person(hub)
    [version] = hub.store.source_versions("person", SourceKey("hr", hr_key(1)), 10)
    assert set(version["payload"]["given_name"]) == {"$vault"}
    assert version["payload"]["city"] == person_payload(1)["city"]
    provenance = hub.store.provenance("person", [master])[master]
    assert set(provenance["email"]["winner"]["value"]) == {"$vault"}
    assert hub.vault.resolve(provenance["email"]["winner"]["value"]) == person_payload(1)["email"]


def test_redaction_needs_an_owner_a_different_administrator_and_the_typed_confirmation(hub) -> None:
    _one_person(hub)
    subjects = [source_subject(SourceKey("hr", hr_key(1)))]
    for owner, admin, confirmation, reason in [
        (STEWARD, ADMIN, "REDACT 1", "erasure request"),
        (OWNER, OWNER, "REDACT 1", "erasure request"),
        (OWNER, Actor("owner-one", "person", "administrator"), "REDACT 1", "erasure request"),
        (OWNER, ADMIN, "REDACT 2", "erasure request"),
        (OWNER, ADMIN, "REDACT 1", " "),
    ]:
        with pytest.raises(Forbidden):
            hub.vault.redact(
                subjects, owner=owner, administrator=admin, confirmation=confirmation, reason=reason
            )
    emptied = hub.vault.redact(
        subjects, owner=OWNER, administrator=ADMIN, confirmation="REDACT 1", reason="erasure request"
    )
    assert emptied and set(hub.vault.store.vault_get(emptied).values()) == {None}
    rows = hub.store._fetch_all(
        f"/*mdm:paged*/ SELECT actor, checker, reason FROM {hub.store.t('audit', 'redaction_log')} LIMIT 10"
    )
    assert rows == [(OWNER.name, ADMIN.name, "erasure request")]


def test_masked_equals_the_read_view(hub) -> None:
    master = _one_person(hub)
    model = hub.registry.published("person")
    golden = hub.store.golden("person", [master])[master]
    view = hub.store.masked_rows("person", [master])[master]
    masked = hub.privacy.masked(model, golden.values)
    for name in model.personal_attributes():
        assert masked.get(name) == view.get(name), name
    assert masked["city"] == view["city"]
