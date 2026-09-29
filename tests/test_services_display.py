"""How the workbench words what it shows: reasons for every code the hub writes, tray lines, due times, claimants,
display names and masked forms (owner: SERVICES, B.6.3)."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mdm.engine.cluster import REASONS as CLUSTER_REASONS
from mdm.models.authority import Actor
from mdm.models.entity_model import EntityModel
from mdm.models.records import SourceKey
from mdm.models.safety import SAFE_TEXT_RE
from mdm.models.tasks import Task
from mdm.services import display

SERVICES = Path(__file__).resolve().parents[1] / "src" / "mdm" / "services"
NOW = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
#: a task kind, then the reason code a plan or a task names after it
_TASK_REASON = re.compile(r'"(?:review|possible_duplicate|held|exception|orphan)",\s*"([a-z_]+)"')
_ORPHAN_REASON = re.compile(r'_orphan_task\([^)]*"([a-z_]+)"\)')
_HELD_REASON = re.compile(r'\bheld\(\s*"([a-z_]+)"')


def written_reasons() -> set[str]:
    """Every task reason code the services and the clustering write."""
    found = {code for code in CLUSTER_REASONS if code not in ("auto_band", "new_cluster")}
    for path in SERVICES.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        found.update(_TASK_REASON.findall(text))
        found.update(_ORPHAN_REASON.findall(text))
        found.update(_HELD_REASON.findall(text))
    return found - {"review", "held"}  # a kind followed by a kind is not a reason


def a_task(reason: str, kind: str = "review", **evidence) -> Task:
    return Task(
        "TSK-1", "TK-1", "person", kind, "open", SourceKey("crm", "C1"), ("PER-000001",), reason, {}, evidence,
        "ev-1", NOW, NOW,
    )  # fmt: skip


def test_every_reason_the_hub_writes_reads_in_words() -> None:
    written = written_reasons()
    assert {
        "review_band",
        "critical_update_held",
        "no_active_member",
        "last_member_moved",
        "quality_hold",
    } <= written
    assert written <= set(display.REASONS), sorted(written - set(display.REASONS))
    for code in written:
        chip = display.reason_chip(
            a_task(code, critical=["family_name"], end_dates=["left_on"], other=["PER-2"])
        )
        sentence = display.reason_sentence(a_task(code, critical=["family_name"], rules=["PER-V3"]))
        assert chip and sentence.endswith(".") and "{" not in chip + sentence
    assert display.reason_chip(a_task("critical_update_held", "held", critical=["family_name"])) == (
        "critical: family name"
    )
    assert display.reason_chip(a_task("review_band", hinge="registered_id")) == "hinges on registered ID"
    assert display.reason_sentence(a_task("review_band", hinge="postcode")).endswith(
        "It hinges on the postcode."
    )
    assert display.reason_chip(a_task("something_new")) == "something new"


def test_tray_lines_are_built_from_ids_and_source_keys() -> None:
    subject = {"source": "crm:C000123", "candidates": ["ORG-000123", "ORG-004410"]}
    assert display.tray_label("link", subject, "ORG-000123") == "Link crm:C000123 to ORG-000123"
    assert display.tray_label("not_a_match", subject, None) == "Not a match: crm:C000123"
    assert display.tray_label("approve_update", subject, None) == "Approve the update of crm:C000123"
    assert display.tray_label("reject_update", subject, None) == "Reject the update of crm:C000123"
    pair = {"master_ids": ["ORG-000123", "ORG-004410"]}
    assert display.tray_label("keep_apart", pair, None) == "Keep ORG-000123 and ORG-004410 apart"
    assert display.tray_label("keep_orphan", {"master_ids": ["ORG-000123"]}, None) == "Keep ORG-000123"
    for label in (display.tray_label(d, subject, "ORG-000123") for d in ("link", "not_a_match")):
        assert all(SAFE_TEXT_RE.match(word) for word in label.split())


def test_due_times_claimants_and_roles() -> None:
    assert display.due_text(NOW + timedelta(hours=3, minutes=5), NOW) == "due in 3 h"
    assert display.due_text(NOW + timedelta(minutes=25), NOW) == "due in 25 min"
    assert display.due_text(NOW - timedelta(days=2, hours=1), NOW) == "overdue by 2 d"
    assert display.due_text(None, NOW) == ""
    me = Actor("persona:data_steward", "person", "data_steward", persona=True)
    assert display.claimant_text("persona:data_steward", me) == "you"
    assert display.claimant_text("persona:coordinating_steward", me) == "Coordinating steward (persona)"
    assert display.claimant_text(None, me) is None
    assert display.role_label("data_owner") == "Data owner"
    assert display.kind_label("possible_duplicate") == "Possible duplicate"


def test_display_names_and_masked_forms(person_model: EntityModel, org_model: EntityModel) -> None:
    values = {"given_name": "Tamsin", "family_name": "Quorrel", "birth_date": "1990-04-01", "city": "Norvale"}
    personal = set(person_model.personal_attributes())
    assert display.display_name(person_model, values, personal) == "T*** Q***"
    assert display.display_name(person_model, values, set()) == "Tamsin Quorrel"
    assert display.display_name(person_model, {}, personal, "crm:C1") == "crm:C1"
    assert display.masked_form(person_model, "birth_date", "1990-04-01") == display.HIDDEN
    assert display.masked_form(person_model, "city", "Norvale") == "Norvale"
    assert (
        display.masked_form(person_model, "addresses", [{"kind": "home", "line1": "1 Ash Row"}]) == "hidden"
    )
    assert display.display_name(org_model, {"name": "Brindle Works Ltd"}, set()) == "Brindle Works Ltd"
    group = [{"kind": "registered", "line1": "121 Umber Lane", "city": "Varnmouth"}, {"kind": "trading"}]
    assert (
        display.value_text(org_model, "addresses", group) == "registered: 121 Umber Lane, Varnmouth; trading"
    )
    assert display.value_text(org_model, "closed_on", datetime(2026, 1, 5, tzinfo=UTC).date()) == "2026-01-05"
    assert display.attribute_label(None, "registered_id") == "Registered ID"


# ---------------------------------------------------------------------------------------------- signature batches


def test_a_signature_in_marks_and_words(person_model: EntityModel) -> None:
    signature = "given_name= · family_name≈ · birth_date≠ · email∅"
    marks = display.signature_marks(person_model, signature)
    assert [(m.comparison, m.label, m.mark, m.words) for m in marks] == [
        ("given_name", "Given name", "=", "the same"),
        ("family_name", "Family name", "≈", "similar"),
        ("birth_date", "Birth date", "≠", "different"),
        ("email", display.attribute_label(person_model, "email"), "∅", "missing"),
    ]
    words = display.signature_words(person_model, signature)
    assert words.startswith("given name the same · family name similar · birth date different · ")
    assert display.signature_comparisons(signature) == ("given_name", "family_name", "birth_date", "email")
    assert display.signature_words(person_model, "") == "no pattern"


def test_a_batchs_tray_line_strata_and_reasons() -> None:
    subject = {"batch_id": "BAT-" + "a" * 20, "decisions": 566, "kind": "link"}
    assert display.tray_label("batch_link", subject, None) == f"Link 566 alike reviews (BAT-{'a' * 20})"
    undo = {**subject, "kind": "compensate", "compensates": "BAT-" + "b" * 20, "decisions": 1}
    assert display.tray_label("batch_compensate", undo, None) == f"Undo batch BAT-{'b' * 20}: 1 link"
    assert display.stratum_label("crm/hr") == "crm and hr"
    assert display.item_reason_words("claimed") == "claimed by another steward"
    assert display.item_reason_words("split:birth_date") == "split off on birth date"
    assert display.item_reason_words("split:all") == "split off with every alike review"
    from mdm.models.batch import ITEM_REASONS

    assert set(ITEM_REASONS) <= set(display.ITEM_REASON_WORDS)  # every code has its words
    for code in ITEM_REASONS:
        assert SAFE_TEXT_RE.match(code)
